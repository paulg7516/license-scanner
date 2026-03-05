"""
License Scanner - Token Health Portal
Xolv Technology Solutions

Run with:  streamlit run app.py
"""

import streamlit as st
import base64
import json
import os
import signal
import subprocess
import time as _time
from datetime import datetime, timedelta
from pathlib import Path

import config
import auth
import storage
import ai_summary


# ── User Action Tracking ──────────────────────────────────────────
ACTIONS_FILE = Path(__file__).parent / "user_actions.json"

def load_user_actions():
    """Load persisted user action statuses."""
    if ACTIONS_FILE.exists():
        try:
            return json.loads(ACTIONS_FILE.read_text())
        except (json.JSONDecodeError, IOError):
            return {}
    return {}

def save_user_actions(actions):
    """Persist user action statuses."""
    ACTIONS_FILE.write_text(json.dumps(actions, indent=2))

def action_key(org, platform, email):
    """Build unique key for a user action entry."""
    return f"{org}::{platform}::{email}"

def get_status(actions, key):
    """Get status for a user (default: pending)."""
    return actions.get(key, {}).get("status", "pending")

STATUS_STYLES = {
    "pending": {"label": "Pending", "bg": "#78350F", "color": "#FCD34D", "border": "#92400E"},
    "deactivated": {"label": "Deactivated", "bg": "#064E3B", "color": "#6EE7B7", "border": "#065F46"},
    "kept": {"label": "Kept", "bg": "#1E3A5F", "color": "#93C5FD", "border": "#1E40AF"},
}

def status_pill_html(status):
    """Render a colored status pill."""
    s = STATUS_STYLES.get(status, STATUS_STYLES["pending"])
    return f'<span style="background:{s["bg"]};color:{s["color"]};border:1px solid {s["border"]};padding:1px 8px;border-radius:4px;font-size:0.68rem;font-weight:600;white-space:nowrap;">{s["label"]}</span>'


CONFIDENCE_MAP = {
    "No Login Recorded":                      {"level": "high",   "score": 95, "label": "Safe to deactivate"},
    "Inactive 60+ Days":                      {"level": "medium", "score": 75, "label": "Review recommended"},
    "On Escalation Policy - Inactive 60+ Days": {"level": "low",  "score": 50, "label": "May still be needed"},
}
CONFIDENCE_SORT_ORDER = {"high": 0, "medium": 1, "low": 2}

def confidence_score(inactivity_type, last_active):
    """Return confidence dict based on inactivity type and last_active."""
    base = CONFIDENCE_MAP.get(inactivity_type, {"level": "medium", "score": 75, "label": "Review recommended"})
    score = base["score"]
    if not last_active:
        score = min(score + 5, 99)
    return {"level": base["level"], "score": score, "label": base["label"]}

def confidence_pill_html(conf):
    """Render a colored confidence pill styled as an AI recommendation."""
    icon = {"high": "&#10003;", "medium": "&#9679;", "low": "&#9888;"}.get(conf["level"], "&#9679;")
    return (
        f'<span class="conf-pill conf-{conf["level"]}" title="{conf["label"]}">'
        f'<span class="conf-icon">{icon}</span>'
        f'{conf["score"]}%'
        f'</span>'
    )


# ── User Registry (auto-populated on login) ──────────────────────
USER_REGISTRY_FILE = Path(__file__).parent / "user_registry.json"

def load_user_registry():
    if USER_REGISTRY_FILE.exists():
        try:
            return json.loads(USER_REGISTRY_FILE.read_text())
        except (json.JSONDecodeError, IOError):
            return {}
    return {}

def register_user(email, name=""):
    """Add user to registry on login. Key is email, value is display info."""
    reg = load_user_registry()
    if email not in reg:
        reg[email] = {"name": name, "first_seen": datetime.utcnow().isoformat()}
    reg[email]["last_seen"] = datetime.utcnow().isoformat()
    if name:
        reg[email]["name"] = name
    USER_REGISTRY_FILE.write_text(json.dumps(reg, indent=2))

def get_known_users():
    """Return list of known user emails for dropdowns."""
    reg = load_user_registry()
    return sorted(reg.keys())


# ── Rotation Metadata (created_on, rotated_by per system) ────────
ROTATION_META_FILE = Path(__file__).parent / "rotation_metadata.json"

def load_rotation_metadata():
    if ROTATION_META_FILE.exists():
        try:
            return json.loads(ROTATION_META_FILE.read_text())
        except (json.JSONDecodeError, IOError):
            return {}
    return {}

def save_rotation_entry(system_key, rotated_by, created_on, logged_by):
    """Save rotation metadata for a system."""
    meta = load_rotation_metadata()
    meta[system_key] = {
        "rotated_by": rotated_by,
        "created_on": created_on,
        "logged_by": logged_by,
        "logged_on": datetime.utcnow().isoformat(),
    }
    ROTATION_META_FILE.write_text(json.dumps(meta, indent=2))

def get_rotation_entry(system_key):
    """Get rotation metadata for a system, or None."""
    return load_rotation_metadata().get(system_key)


# ── Audit Log ────────────────────────────────────────────────────
AUDIT_LOG_FILE = Path(__file__).parent / "audit_log.json"

def _load_audit_log():
    if AUDIT_LOG_FILE.exists():
        try:
            return json.loads(AUDIT_LOG_FILE.read_text())
        except (json.JSONDecodeError, IOError):
            return []
    return []

def log_event(system_key, action, user_email, details=None):
    """Append an event to the audit log."""
    logs = _load_audit_log()
    logs.insert(0, {
        "system_key": system_key,
        "action": action,
        "user_email": user_email,
        "timestamp": datetime.utcnow().isoformat(),
        "details": details or {},
    })
    # Keep last 500 entries
    AUDIT_LOG_FILE.write_text(json.dumps(logs[:500], indent=2))

def get_audit_log(system_key=None, limit=100):
    """Read audit log, optionally filtered by system_key."""
    logs = _load_audit_log()
    if system_key:
        logs = [e for e in logs if e.get("system_key") == system_key]
    return logs[:limit]


def get_logo_base64(variant="white"):
    assets = Path(__file__).parent / "assets"
    filename = "logo_white.png" if variant == "white" else "logo_black.png"
    filepath = assets / filename
    if filepath.exists():
        return base64.b64encode(filepath.read_bytes()).decode()
    return ""


st.set_page_config(
    page_title="License Scanner Console",
    page_icon="🔑",
    layout="wide",
    initial_sidebar_state="collapsed",
)

LOGO_WHITE = get_logo_base64("white")

if "page" not in st.session_state:
    st.session_state.page = "overview"

# ------------------------------------------------------------------
# Check auth BEFORE building CSS so container can vary by state
# ------------------------------------------------------------------
IS_LOGGED_IN = auth.is_authenticated()

if IS_LOGGED_IN:
    CONTAINER_CSS = """
    .main .block-container {{
        padding: 0 1.5rem 4rem 1.5rem;
        max-width: 1005px !important;
        background: #151C2C;
        border: 1.5px solid #2A3548;
        border-radius: 16px;
        margin: 2rem auto;
        box-shadow: 0 10px 40px rgba(0,0,0,0.5);
        position: relative;
        overflow: hidden;
    }}
    .main .block-container::before {{
        content: '';
        position: absolute;
        top: 0; left: 0; right: 0;
        height: 3px;
        background: linear-gradient(90deg, #028090, #A927B2, #F25239);
        z-index: 10;
    }}
    """
else:
    CONTAINER_CSS = """
    .stApp > header {{ display: none !important; }}
    /* Glowing orbs */
    .login-orb {{
        position: fixed;
        border-radius: 50%;
        filter: blur(100px);
        opacity: 0.15;
        z-index: 0;
        pointer-events: none;
        animation: orb-float 12s ease-in-out infinite;
    }}
    .login-orb-teal {{
        width: 500px; height: 500px;
        background: radial-gradient(circle, #028090, transparent 70%);
        top: -10%; right: -5%;
    }}
    .login-orb-purple {{
        width: 400px; height: 400px;
        background: radial-gradient(circle, #A927B2, transparent 70%);
        bottom: -10%; left: -5%;
        animation-delay: -6s;
    }}
    @keyframes orb-float {{
        0%, 100% {{ transform: translate(0, 0) scale(1); }}
        33% {{ transform: translate(30px, -20px) scale(1.05); }}
        66% {{ transform: translate(-20px, 15px) scale(0.95); }}
    }}
    .main .block-container {{
        padding-top: 3rem !important;
        position: relative;
        z-index: 1;
    }}
    /* Hide column borders/gaps for login layout */
    .main [data-testid="stHorizontalBlock"] {{
        gap: 0 !important;
    }}
    /* Widget overrides inside login card */
    .main .block-container .stTextInput > div > div {{
        background: #0F172A !important;
        border: 1.5px solid #334155 !important;
        border-radius: 8px !important;
    }}
    .main .block-container .stTextInput > div > div:focus-within {{
        border-color: #028090 !important;
        box-shadow: 0 0 0 3px rgba(2,128,144,0.12) !important;
    }}
    .main .block-container .stTextInput input {{
        color: #E2E8F0 !important; font-size: 0.88rem !important;
    }}
    .main .block-container button[data-testid="stBaseButton-primary"] {{
        border-radius: 8px !important;
        background: #028090 !important;
        border: none !important; font-weight: 600 !important;
        padding: 0.65rem 1.5rem !important;
        transition: all 150ms ease !important;
    }}
    .main .block-container button[data-testid="stBaseButton-primary"]:hover {{
        background: #03939F !important;
        box-shadow: 0 4px 12px rgba(2,128,144,0.3) !important;
    }}
    """

st.markdown(f"""
<style>
    #MainMenu, header, footer {{ visibility: hidden; }}
    .stDeployButton {{ display: none; }}
    section[data-testid="stSidebar"] {{ display: none !important; }}
    [data-testid="collapsedControl"] {{ display: none !important; }}

    :root {{
        --bg: #0B0F19;
        --bg-r: #111827;
        --bg-c: #151C2C;
        --bg-ch: #1A2236;
        --bg-in: #0F1525;
        --bdr: #1E293B;
        --bdr2: #253044;
        --t1: #F1F5F9;
        --t2: #94A3B8;
        --t3: #64748B;
        --t4: #475569;
        --teal: #028090;
        --teal-h: #03939F;
        --teal-g: rgba(2,128,144,0.15);
        --orange: #F25239;
        --purple: #A927B2;
        --ok: #10B981; --ok-bg: rgba(16,185,129,0.1); --ok-b: rgba(16,185,129,0.2);
        --bad: #EF4444; --bad-bg: rgba(239,68,68,0.1); --bad-b: rgba(239,68,68,0.2);
        --warn: #F59E0B; --warn-bg: rgba(245,158,11,0.1); --warn-b: rgba(245,158,11,0.25);
        --r: 10px; --rs: 6px; --rl: 14px;
        --ease: 160ms ease;
    }}

    .stApp {{
        font-family: 'Segoe UI', -apple-system, BlinkMacSystemFont, sans-serif !important;
        background: #000000 !important;
        color: var(--t1) !important;
    }}

    /* Container card - conditional on auth state */
    {CONTAINER_CSS}

    h1, h2, h3, h4 {{ font-family: 'Segoe UI', sans-serif !important; color: var(--t1) !important; }}

    /* ══════════════════════════════════════════════════
       FULL-WIDTH TOP HEADER
    ══════════════════════════════════════════════════ */
    .hdr-wrap {{
        background: #111827;
        border-bottom: 1.5px solid #2A3548;
        margin: 0 0 1.75rem 0;
        padding: 0 1rem;
        position: relative;
        border-radius: 12px 12px 0 0;
    }}
    .hdr-wrap::after {{
        content: '';
        position: absolute;
        bottom: 0; left: 0; right: 0;
        height: 2px;
        background: linear-gradient(90deg, var(--teal), var(--purple), var(--orange));
        opacity: 0.6;
    }}
    .hdr {{
        padding: 1.35rem 0;
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 1rem;
    }}
    .hdr-logo img {{
        height: 48px;
        display: block;
    }}
    .hdr-right {{
        display: flex;
        align-items: center;
        gap: 1rem;
    }}
    .hdr-name {{
        font-size: 0.88rem;
        font-weight: 600;
        color: var(--t1);
        line-height: 1.2;
        text-align: right;
    }}
    .hdr-email {{
        font-size: 0.76rem;
        color: var(--t3);
        text-align: right;
    }}

    /* ══════════════════════════════════════════════════
       PAGE HEADER
    ══════════════════════════════════════════════════ */
    .phdr {{
        background: #111827;
        border: 1.5px solid #2A3548;
        border-radius: 12px;
        padding: 1.5rem 1.75rem;
        margin-bottom: 1.25rem;
        position: relative;
        overflow: hidden;
        box-shadow: 0 2px 8px rgba(0,0,0,0.3);
    }}
    .phdr::before {{
        content: '';
        position: absolute;
        top: 0; left: 0; right: 0;
        height: 3px;
        background: linear-gradient(90deg, var(--teal), var(--purple), var(--orange));
    }}
    .phdr-ey {{
        font-size: 0.66rem;
        font-weight: 700;
        color: var(--teal);
        text-transform: uppercase;
        letter-spacing: 0.1em;
        margin-bottom: 0.35rem;
    }}
    .phdr-t {{
        font-size: 1.35rem;
        font-weight: 700;
        color: var(--t1);
        letter-spacing: -0.025em;
        margin-bottom: 0.2rem;
        line-height: 1.3;
    }}
    .phdr-d {{
        font-size: 0.88rem;
        color: var(--t2);
        line-height: 1.55;
    }}

    /* ══════════════════════════════════════════════════
       CARDS
    ══════════════════════════════════════════════════ */
    .card {{
        background: #111827;
        border: 1.5px solid #2A3548;
        border-radius: 12px;
        padding: 1.2rem 1.4rem;
        margin-bottom: 0.75rem;
        transition: all var(--ease);
        box-shadow: 0 2px 8px rgba(0,0,0,0.25);
    }}
    .card:hover {{ background: #1A2236; border-color: #3D4F66; }}
    .sysh {{ display: flex; justify-content: space-between; align-items: flex-start; gap: 1rem; flex-wrap: wrap; }}
    .sysn {{ font-weight: 600; font-size: 0.96rem; color: var(--t1); margin-bottom: 0.1rem; }}
    .sysd {{ font-size: 0.84rem; color: var(--t3); }}
    .pill {{ display: inline-flex; align-items: center; gap: 5px; padding: 2px 10px 2px 7px; border-radius: 100px; font-size: 0.72rem; font-weight: 600; white-space: nowrap; }}
    .pdot {{ width: 6px; height: 6px; border-radius: 50%; }}
    .pill-ok {{ background: var(--ok-bg); color: var(--ok); border: 1px solid var(--ok-b); }}
    .pill-ok .pdot {{ background: var(--ok); }}
    .pill-no {{ background: var(--bad-bg); color: var(--bad); border: 1px solid var(--bad-b); }}
    .pill-no .pdot {{ background: var(--bad); }}
    .mets {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 0.6rem; margin-top: 0.85rem; padding-top: 0.85rem; border-top: 1px solid var(--bdr); }}
    .ml {{ font-size: 0.66rem; font-weight: 700; color: var(--t4); text-transform: uppercase; letter-spacing: 0.08em; margin-bottom: 0.15rem; }}
    .mv {{ font-size: 0.88rem; font-weight: 500; color: var(--t1); }}
    .sl {{ font-size: 0.68rem; font-weight: 700; color: var(--t4); text-transform: uppercase; letter-spacing: 0.1em; margin: 1.5rem 0 0.5rem; }}

    /* Trash / remove integration button - in column next to card */
    div[data-testid="stHorizontalBlock"]:has(.card) div[data-testid="stColumn"]:last-child button[data-testid="stBaseButton-secondary"] {{
        color: #EF4444 !important;
        border: 1.5px solid rgba(239,68,68,0.3) !important;
        background: transparent !important;
        font-size: 0.82rem !important;
        font-weight: 600 !important;
        padding: 0.2rem 0.55rem !important;
        min-height: 30px !important;
        min-width: 30px !important;
        border-radius: 8px !important;
        line-height: 1 !important;
    }}
    div[data-testid="stHorizontalBlock"]:has(.card) div[data-testid="stColumn"]:last-child button[data-testid="stBaseButton-secondary"]:hover {{
        background: rgba(239,68,68,0.1) !important;
        border-color: rgba(239,68,68,0.5) !important;
    }}

    /* ══════════════════════════════════════════════════
       FORMS
    ══════════════════════════════════════════════════ */
    .stTextInput label p {{ font-family: 'Segoe UI', sans-serif !important; font-size: 0.85rem !important; font-weight: 500 !important; color: var(--t2) !important; }}
    .stTextInput > div > div > input {{ font-family: 'Segoe UI', sans-serif !important; font-size: 0.88rem !important; border: 1.5px solid var(--bdr) !important; border-radius: var(--rs) !important; padding: 0.55rem 0.75rem !important; background: var(--bg-in) !important; color: var(--t1) !important; transition: border-color var(--ease), box-shadow var(--ease); }}
    .stTextInput > div > div > input:focus {{ border-color: var(--teal) !important; box-shadow: 0 0 0 3px var(--teal-g) !important; }}
    .stSelectbox > div > div {{ border-radius: var(--rs) !important; border: 1.5px solid var(--bdr) !important; background: var(--bg-in) !important; }}
    .stSelectbox > div > div > div {{ color: #F1F5F9 !important; font-weight: 500 !important; font-size: 0.9rem !important; }}
    .stSelectbox label p {{ font-family: 'Segoe UI', sans-serif !important; font-size: 0.85rem !important; font-weight: 500 !important; color: var(--t2) !important; }}
    .stCheckbox label p {{ font-family: 'Segoe UI', sans-serif !important; font-size: 0.85rem !important; color: var(--t2) !important; }}

    /* Details/summary styling for platform user tables */
    details summary::-webkit-details-marker {{ display: none; }}
    details summary {{ list-style: none; }}
    details[open] .details-chevron {{ transform: rotate(90deg); }}
    details summary:hover {{ background: rgba(2,128,144,0.06) !important; }}

    /* ══════════════════════════════════════════════════
       BUTTONS
    ══════════════════════════════════════════════════ */
    button[data-testid="stBaseButton-primary"] {{ background: var(--teal) !important; color: #fff !important; border: none !important; border-radius: var(--rs) !important; font-family: 'Segoe UI', sans-serif !important; font-weight: 600 !important; font-size: 0.88rem !important; padding: 0.55rem 1.5rem !important; transition: all var(--ease); box-shadow: 0 2px 8px rgba(2,128,144,0.25); }}
    button[data-testid="stBaseButton-primary"]:hover {{ background: var(--teal-h) !important; box-shadow: 0 4px 16px rgba(2,128,144,0.35) !important; }}
    button[data-testid="stBaseButton-primary"]:disabled {{ background: var(--bdr) !important; color: var(--t4) !important; box-shadow: none !important; }}

    /* Tab colors - Scan Overview (green), Rotate Tokens (teal), Audit Log (purple), AI Insights (orange) */
    div[data-testid="column"]:nth-child(1) button[data-testid="stBaseButton-primary"],
    div[data-testid="stColumn"]:nth-child(1) button[data-testid="stBaseButton-primary"] {{
        background: #10B981 !important; box-shadow: 0 2px 8px rgba(16,185,129,0.25) !important;
    }}
    div[data-testid="column"]:nth-child(1) button[data-testid="stBaseButton-primary"]:hover,
    div[data-testid="stColumn"]:nth-child(1) button[data-testid="stBaseButton-primary"]:hover {{
        background: #059669 !important; box-shadow: 0 4px 16px rgba(16,185,129,0.35) !important;
    }}
    div[data-testid="column"]:nth-child(3) button[data-testid="stBaseButton-primary"],
    div[data-testid="stColumn"]:nth-child(3) button[data-testid="stBaseButton-primary"] {{
        background: #A927B2 !important; box-shadow: 0 2px 8px rgba(169,39,178,0.25) !important;
    }}
    div[data-testid="column"]:nth-child(3) button[data-testid="stBaseButton-primary"]:hover,
    div[data-testid="stColumn"]:nth-child(3) button[data-testid="stBaseButton-primary"]:hover {{
        background: #9320A1 !important; box-shadow: 0 4px 16px rgba(169,39,178,0.35) !important;
    }}
    div[data-testid="column"]:nth-child(4) button[data-testid="stBaseButton-primary"],
    div[data-testid="stColumn"]:nth-child(4) button[data-testid="stBaseButton-primary"],
    div[data-testid="column"]:nth-child(4) button[data-testid="stBaseButton-secondary"],
    div[data-testid="stColumn"]:nth-child(4) button[data-testid="stBaseButton-secondary"] {{
        padding-left: 2rem !important;
    }}
    div[data-testid="column"]:nth-child(4) button[data-testid="stBaseButton-primary"]::before,
    div[data-testid="stColumn"]:nth-child(4) button[data-testid="stBaseButton-primary"]::before,
    div[data-testid="column"]:nth-child(4) button[data-testid="stBaseButton-secondary"]::before,
    div[data-testid="stColumn"]:nth-child(4) button[data-testid="stBaseButton-secondary"]::before {{
        content: '';
        display: inline-block;
        width: 16px; height: 16px;
        margin-right: 6px;
        vertical-align: middle;
        background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 120 120'%3E%3Cdefs%3E%3ClinearGradient id='a' x1='0' y1='1' x2='1' y2='0'%3E%3Cstop offset='0%25' stop-color='%2300C9DB'/%3E%3Cstop offset='100%25' stop-color='%235B6CF7'/%3E%3C/linearGradient%3E%3ClinearGradient id='b' x1='0' y1='1' x2='1' y2='0'%3E%3Cstop offset='0%25' stop-color='%235B6CF7'/%3E%3Cstop offset='100%25' stop-color='%238B5CF6'/%3E%3C/linearGradient%3E%3C/defs%3E%3Cpath d='M45 10C48 30 62 48 80 52C62 56 48 70 45 92C42 70 28 56 8 52C28 48 42 30 45 10Z' fill='url(%23a)'/%3E%3Cpath d='M80 2C82 14 90 22 102 25C90 28 82 36 80 50C78 36 70 28 58 25C70 22 78 14 80 2Z' fill='url(%23b)'/%3E%3Cpath d='M85 72C86 80 90 84 98 86C90 88 86 92 85 100C84 92 80 88 72 86C80 84 84 80 85 72Z' fill='%237C3AED'/%3E%3C/svg%3E");
        background-size: contain;
        background-repeat: no-repeat;
    }}
    div[data-testid="column"]:nth-child(4) button[data-testid="stBaseButton-primary"],
    div[data-testid="stColumn"]:nth-child(4) button[data-testid="stBaseButton-primary"] {{
        background: #F59E0B !important; box-shadow: 0 2px 8px rgba(245,158,11,0.25) !important;
    }}
    div[data-testid="column"]:nth-child(4) button[data-testid="stBaseButton-primary"]:hover,
    div[data-testid="stColumn"]:nth-child(4) button[data-testid="stBaseButton-primary"]:hover {{
        background: #D97706 !important; box-shadow: 0 4px 16px rgba(245,158,11,0.35) !important;
    }}

    button[data-testid="stBaseButton-secondary"] {{ font-family: 'Segoe UI', sans-serif !important; border-radius: var(--rs) !important; font-size: 0.84rem !important; background: var(--bg-c) !important; color: var(--t2) !important; border: 1px solid var(--bdr) !important; }}
    button[data-testid="stBaseButton-secondary"]:hover {{ background: var(--bg-ch) !important; color: var(--t1) !important; }}

    /* AI Generate button - gradient */
    button[kind="primary"][data-testid="stBaseButton-primary"].st-key-btn_ai_gen,
    .st-key-btn_ai_gen button,
    .st-key-btn_ai_gen button[data-testid="stBaseButton-primary"] {{
        background: linear-gradient(135deg, #00C9DB, #5B6CF7, #8B5CF6) !important;
        color: #fff !important;
        font-weight: 600 !important;
        border: none !important;
        box-shadow: 0 2px 12px rgba(91,108,247,0.3) !important;
    }}
    .st-key-btn_ai_gen button:hover,
    .st-key-btn_ai_gen button[data-testid="stBaseButton-primary"]:hover {{
        box-shadow: 0 4px 20px rgba(91,108,247,0.45) !important;
        filter: brightness(1.1) !important;
    }}

    /* Sign out - red text (5th column) */
    div[data-testid="column"]:last-child button[data-testid="stBaseButton-secondary"],
    div[data-testid="stColumn"]:last-child button[data-testid="stBaseButton-secondary"],
    div[data-testid="column"]:nth-child(5) button[data-testid="stBaseButton-secondary"],
    div[data-testid="stColumn"]:nth-child(5) button[data-testid="stBaseButton-secondary"] {{
        color: #EF4444 !important;
        border-color: rgba(239,68,68,0.3) !important;
    }}
    div[data-testid="column"]:last-child button[data-testid="stBaseButton-secondary"]:hover,
    div[data-testid="stColumn"]:last-child button[data-testid="stBaseButton-secondary"]:hover,
    div[data-testid="column"]:nth-child(5) button[data-testid="stBaseButton-secondary"]:hover,
    div[data-testid="stColumn"]:nth-child(5) button[data-testid="stBaseButton-secondary"]:hover {{
        background: rgba(239,68,68,0.1) !important;
        border-color: rgba(239,68,68,0.5) !important;
        color: #F87171 !important;
    }}

    /* -- Login card -- */
    .lcard {{
        background: #1E293B;
        border: 1.5px solid #334155;
        border-radius: 16px;
        padding: 2.5rem 2rem 1.75rem;
        text-align: center;
        position: relative;
        overflow: hidden;
        box-shadow: 0 20px 60px rgba(0,0,0,0.4);
        margin-bottom: 0.75rem;
    }}

    /* ══════════════════════════════════════════════════
       AUDIT
    ══════════════════════════════════════════════════ */
    .ar {{ background: #111827; border: 1.5px solid #2A3548; border-radius: 12px; padding: 0.85rem 1.15rem; margin-bottom: 0.4rem; display: grid; grid-template-columns: 10px 1fr auto; gap: 0.9rem; align-items: start; transition: all var(--ease); box-shadow: 0 1px 4px rgba(0,0,0,0.2); }}
    .ar:hover {{ background: #1A2236; border-color: #3D4F66; }}
    .adot {{ width: 7px; height: 7px; border-radius: 50%; background: var(--teal); box-shadow: 0 0 0 3px var(--teal-g); margin-top: 6px; }}
    .at {{ font-size: 0.88rem; font-weight: 600; color: var(--t1); margin-bottom: 0.15rem; }}
    .a-source {{ display: inline-block; background: rgba(2,128,144,0.1); border: 1px solid rgba(2,128,144,0.2); color: #5EEAD4; font-size: 0.66rem; font-weight: 600; padding: 1px 8px; border-radius: 100px; letter-spacing: 0.03em; margin-bottom: 0.2rem; }}
    .a-detail {{ font-size: 0.78rem; color: var(--t3); margin-bottom: 0.15rem; }}
    .as {{ font-size: 0.74rem; color: var(--t4); }}
    .ats {{ font-size: 0.72rem; color: var(--t4); white-space: nowrap; text-align: right; line-height: 1.5; }}

    .empty {{ text-align: center; padding: 2.5rem 2rem; background: #111827; border: 1.5px dashed #2A3548; border-radius: 12px; }}
    .empty-t {{ font-weight: 600; font-size: 0.94rem; color: var(--t2); margin-bottom: 0.2rem; }}
    .empty-d {{ font-size: 0.86rem; color: var(--t4); }}

    .dev-bx {{ background: var(--warn-bg); border: 1px solid var(--warn-b); border-radius: var(--r); padding: 0.8rem 1rem; margin-top: 1.25rem; }}
    .dev-lbl {{ font-size: 0.66rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.08em; color: var(--warn); margin-bottom: 0.1rem; }}
    .dev-txt {{ font-size: 0.82rem; color: #FBBF24; }}

    .lcard-bar {{
        position: absolute; top: 0; left: 0; right: 0; height: 3px;
        background: linear-gradient(90deg, #028090, #A927B2, #F25239);
    }}
    .lcard-logo {{ margin-bottom: 1.75rem; }}
    .lcard-logo img {{ height: 46px; }}
    .lcard-title {{
        font-size: 1.35rem; font-weight: 700; color: #F1F5F9;
        margin-bottom: 0.35rem; letter-spacing: -0.02em;
    }}
    .lcard-title span {{
        color: #F1F5F9;
    }}
    .lcard-sub {{
        font-size: 0.84rem; color: #94A3B8; line-height: 1.6;
        margin-bottom: 1.5rem;
    }}
    /* Feature pills */
    .lcard-pills {{
        display: flex; gap: 0.45rem; justify-content: center;
        flex-wrap: wrap; margin-bottom: 1.75rem;
    }}
    .lcard-pill {{
        display: inline-flex; align-items: center; gap: 6px;
        background: rgba(255,255,255,0.04); border: 1px solid rgba(148,163,184,0.1);
        border-radius: 100px; padding: 0.3rem 0.75rem;
    }}
    .lcard-pill-dot {{ width: 5px; height: 5px; border-radius: 50%; flex-shrink: 0; }}
    .lcard-pill-text {{ font-size: 0.72rem; color: #94A3B8; font-weight: 500; }}
    /* AI pill gradient text */
    .lcard-pill-ai .lcard-pill-text {{
        background: linear-gradient(90deg, #00C9DB, #5B6CF7, #8B5CF6);
        -webkit-background-clip: text; -webkit-text-fill-color: transparent;
        background-clip: text; font-weight: 600;
    }}
    /* Divider */
    .lcard-div {{
        height: 1px; background: linear-gradient(90deg, transparent, #334155, transparent);
        margin: 0 0 1.75rem;
    }}
    /* SSO button */
    .lcard-sso {{
        display: inline-flex; align-items: center; justify-content: center; gap: 10px;
        background: #FFFFFF; color: #1A1A1A;
        border: 1.5px solid #E2E8F0; border-radius: 8px;
        padding: 0.7rem 2rem;
        font-family: 'Segoe UI', system-ui, sans-serif;
        font-size: 0.88rem; font-weight: 600;
        text-decoration: none;
        box-shadow: 0 1px 4px rgba(0,0,0,0.08);
        transition: all 200ms ease;
    }}
    .lcard-sso:hover {{
        background: #F8FAFC; border-color: #CBD5E1;
        box-shadow: 0 4px 12px rgba(0,0,0,0.12);
        transform: translateY(-1px);
    }}
    .lcard-or {{
        display: flex; align-items: center; gap: 0.75rem;
        margin: 1.25rem 0 0;
    }}
    .lcard-or-line {{ flex: 1; height: 1px; background: #283548; }}
    .lcard-or-text {{
        font-size: 0.65rem; color: #64748B; text-transform: uppercase;
        letter-spacing: 0.08em; font-weight: 600;
    }}
    /* Dev badge + footer */
    .lcard-dev {{
        background: rgba(245,158,11,0.06); border: 1px solid rgba(245,158,11,0.18);
        border-radius: 8px; padding: 0.5rem 0.85rem; margin-bottom: 0.6rem;
        text-align: left;
    }}
    .lcard-dev span {{
        font-size: 0.58rem; font-weight: 700; text-transform: uppercase;
        letter-spacing: 0.08em; color: #D97706;
    }}
    .lcard-footer {{
        text-align: center; margin-top: 1.5rem;
        font-size: 0.64rem; color: #475569;
    }}

    .stAlert {{ border-radius: var(--rs) !important; }}

    /* Lighter placeholder text */
    input::placeholder {{ color: #64748B !important; opacity: 1 !important; }}

    /* Platform summary chips */
    .plat-chips {{ display: flex; flex-wrap: wrap; gap: 0.4rem; margin: 0.4rem 0 0.6rem 0; }}
    .plat-chip {{ display: inline-flex; align-items: center; gap: 6px; background: #111827; border: 1px solid #2A3548; border-radius: 8px; padding: 0.35rem 0.75rem; font-size: 0.84rem; }}
    .plat-chip img {{ width: 16px; height: 16px; border-radius: 2px; }}
    .plat-chip .pc-name {{ color: #F1F5F9; font-weight: 500; }}
    .plat-chip .pc-count {{ color: #94A3B8; }}
    .plat-chip .pc-cost {{ color: #10B981; font-weight: 600; }}

    /* Clickable platform chip buttons - match chip visual style */
    .chip-filter-row {{ display: flex; flex-wrap: wrap; gap: 0.35rem; margin: 0.2rem 0 0.5rem 0; }}

    /* Table row hover */
    .scan-tbl tr:hover td {{ background: #1A2236 !important; }}

    /* ── Interactive table rows (compact widgets inside expander) ── */

    /* ══════════════════════════════════════════════════
       TABLE ROW ALIGNMENT - NUCLEAR RESET
       Strip ALL wrapper padding so align-items works
       on actual visual content, not padded wrappers.
    ══════════════════════════════════════════════════ */
    /* Global horizontal blocks: center children */
    div[data-testid="stHorizontalBlock"] {{
        align-items: center !important;
    }}

    /* Kill ALL vertical spacing in column internals inside expanders */
    div[data-testid="stExpander"] div[data-testid="stColumn"] > div[data-testid="stVerticalBlockBorderWrapper"] {{
        padding: 0 !important;
        margin: 0 !important;
    }}
    div[data-testid="stExpander"] div[data-testid="stColumn"] > div > div[data-testid="stVerticalBlock"] {{
        gap: 0 !important;
        padding: 0 !important;
    }}
    div[data-testid="stExpander"] div[data-testid="stColumn"] div[data-testid="element-container"] {{
        margin: 0 !important;
        padding: 0 !important;
    }}

    /* Checkbox - zero everything */
    div[data-testid="stExpander"] .stCheckbox {{
        margin: 0 !important;
        padding: 0 !important;
    }}
    div[data-testid="stExpander"] .stCheckbox > div {{
        margin: 0 !important;
        padding: 0 !important;
    }}
    div[data-testid="stExpander"] .stCheckbox label {{
        padding: 0 !important;
        min-height: unset !important;
        gap: 0 !important;
        margin: 0 !important;
    }}
    div[data-testid="stExpander"] .stCheckbox label p {{ display: none !important; }}

    /* Selectbox - zero wrapper, keep control visible */
    div[data-testid="stExpander"] .stSelectbox {{
        margin: 0 !important;
        padding: 0 !important;
    }}
    div[data-testid="stExpander"] .stSelectbox > div {{
        margin: 0 !important;
        padding: 0 !important;
    }}
    div[data-testid="stExpander"] .stSelectbox label {{
        display: none !important;
    }}
    div[data-testid="stExpander"] .stSelectbox > div > div {{
        min-height: 34px !important;
        padding: 0 10px !important;
        font-size: 0.82rem !important;
        border-radius: 6px !important;
        display: flex !important;
        align-items: center !important;
    }}
    div[data-testid="stExpander"] .stSelectbox > div > div > div {{
        font-size: 0.82rem !important;
        overflow: visible !important;
        white-space: nowrap !important;
        text-align: center !important;
        width: 100% !important;
    }}
    div[data-testid="stExpander"] .stSelectbox svg {{ width: 14px !important; height: 14px !important; }}

    /* Buttons - zero wrapper */
    div[data-testid="stExpander"] .stElementToolbar {{ display: none !important; }}
    div[data-testid="stExpander"] button[data-testid="stBaseButton-secondary"] {{
        padding: 0.3rem 0.5rem !important;
        font-size: 0.72rem !important;
        min-height: 34px !important;
        line-height: 1.3 !important;
        white-space: nowrap !important;
        margin: 0 !important;
    }}
    div[data-testid="stExpander"] button[data-testid="stBaseButton-primary"] {{
        padding: 0.3rem 0.5rem !important;
        font-size: 0.72rem !important;
        min-height: 34px !important;
        line-height: 1.3 !important;
        white-space: nowrap !important;
        margin: 0 !important;
    }}

    /* Teams logo on notify buttons (8th column in data rows) */
    div[data-testid="stExpander"] div[data-testid="stHorizontalBlock"] > div:nth-child(8) button {{
        background-image: url('https://cdn.jsdelivr.net/gh/homarr-labs/dashboard-icons/png/microsoft-teams.png') !important;
        background-size: 18px 18px !important;
        background-repeat: no-repeat !important;
        background-position: center !important;
        color: transparent !important;
        font-size: 0 !important;
        min-height: 34px !important;
        padding: 0.3rem !important;
        border: 1px solid #2A3548 !important;
        border-radius: 6px !important;
    }}
    div[data-testid="stExpander"] div[data-testid="stHorizontalBlock"] > div:nth-child(8) button:hover {{
        border-color: #505AC9 !important;
        background-color: rgba(80,90,201,0.1) !important;
    }}

    /* HTML tcell - nudged up to match widget visual center */
    .tcell {{ font-size: 0.84rem; color: #94A3B8; line-height: 1.35; min-height: 34px; padding: 0; margin-top: -11px; word-break: break-word; display: flex; align-items: center; }}
    .tcell-name {{ color: #F1F5F9; font-weight: 500; }}
    .tcell-email {{ font-size: 0.8rem; }}
    .tcell-cost {{ color: #10B981; font-weight: 600; text-align: left; justify-content: flex-start; }}
    .tcell-plat {{ gap: 5px; }}
    .tcell-plat img {{ width: 14px; height: 14px; border-radius: 2px; }}
    .tcell-dim {{ opacity: 0.4; }}

    /* ── AI Shared Identity (matches AI Insights palette) ── */

    /* AI Score pills */
    .conf-pill {{ display:inline-flex;align-items:center;gap:4px;padding:3px 10px;border-radius:20px;font-size:0.72rem;font-weight:700;white-space:nowrap;letter-spacing:0.01em;cursor:default;position:relative;overflow:hidden;transition:transform 0.15s ease,box-shadow 0.15s ease; }}
    .conf-pill:hover {{ transform:translateY(-1px); }}
    .conf-pill::after {{ content:"";position:absolute;top:0;left:-100%;width:50%;height:100%;background:linear-gradient(90deg,transparent,rgba(255,255,255,0.06),transparent);animation:conf-shimmer 4s ease-in-out infinite; }}
    @keyframes conf-shimmer {{ 0%{{left:-100%}} 40%{{left:150%}} 100%{{left:150%}} }}
    .conf-icon {{ font-size:0.6rem;opacity:0.85; }}
    .conf-high {{ background:rgba(16,185,129,0.12);color:#6EE7B7;border:1px solid rgba(16,185,129,0.25); }}
    .conf-high:hover {{ box-shadow:0 2px 8px rgba(16,185,129,0.18); }}
    .conf-medium {{ background:rgba(245,158,11,0.10);color:#F59E0B;border:1px solid rgba(245,158,11,0.22); }}
    .conf-medium:hover {{ box-shadow:0 2px 8px rgba(245,158,11,0.14); }}
    .conf-low {{ background:rgba(248,113,113,0.10);color:#FCA5A5;border:1px solid rgba(248,113,113,0.22); }}
    .conf-low:hover {{ box-shadow:0 2px 8px rgba(248,113,113,0.14); }}

    /* AI Recommendation banner - matches AI Insights headline card */
    .conf-banner {{ position:relative;padding:0.85rem 1.1rem 0.65rem;background:linear-gradient(135deg,#1a1425,#151020);border:1.5px solid #3D2E5C;border-radius:12px;margin:0.1rem 0 0.25rem 0;overflow:hidden;box-shadow:0 2px 12px rgba(169,39,178,0.08); }}
    .conf-banner::before {{ content:"";position:absolute;top:0;left:0;right:0;height:3px;background:linear-gradient(90deg,#A927B2,#F59E0B,#A927B2); }}
    .conf-banner-top {{ display:flex;align-items:center;gap:0.4rem;margin-bottom:0.45rem; }}
    .conf-banner-ai {{ font-size:0.65rem;font-weight:700;color:#F59E0B;text-transform:uppercase;letter-spacing:0.1em; }}
    .conf-banner-sparkle {{ line-height:1;display:flex;align-items:center; }}
    .conf-banner-sub {{ font-size:0.65rem;color:#64748B;margin-left:auto; }}
    .conf-banner-counts {{ display:flex;gap:0.5rem;align-items:stretch; }}
    .conf-banner-item {{ flex:1;display:flex;flex-direction:column;align-items:center;gap:0.15rem;background:rgba(255,255,255,0.025);padding:0.35rem 0.5rem;border-radius:8px;border:1px solid rgba(255,255,255,0.04);transition:border-color 0.15s ease,background 0.15s ease; }}
    .conf-banner-item:hover {{ border-color:rgba(255,255,255,0.08);background:rgba(255,255,255,0.04); }}
    .conf-banner-ct {{ font-size:1.05rem;font-weight:800;line-height:1; }}
    .conf-banner-label {{ font-size:0.6rem;font-weight:600;letter-spacing:0.03em;opacity:0.85; }}

    /* AI Score column - visually distinct cell */
    .ai-col {{ background:linear-gradient(135deg,#1f1528,#1a1225);border:1px solid rgba(169,39,178,0.25);border-radius:6px;padding:5px 8px;margin:-11px -8px 0;min-height:34px;display:flex;align-items:center;justify-content:center;width:calc(100% + 16px); }}
    .ai-col-hdr {{ background:linear-gradient(135deg,#251830,#1f1428);border:1px solid rgba(169,39,178,0.3);border-radius:6px;padding:0 8px;margin:-11px -8px 0;min-height:20px;display:flex;align-items:center;justify-content:center;width:calc(100% + 16px); }}
    .ai-score-hdr {{ display:inline-flex;align-items:center;gap:4px; }}
    .ai-score-hdr-label {{ font-size:0.7rem;font-weight:700;color:#F59E0B;text-transform:uppercase;letter-spacing:0.06em; }}

    /* Org header card (above expander) */
    .org-card {{ background: #111827; border: 1.5px solid #2A3548; border-radius: 12px 12px 0 0; padding: 0.9rem 1.15rem; display: flex; align-items: center; justify-content: space-between; gap: 1rem; margin-bottom: -1rem; }}
    .org-card-standalone {{ border-radius: 12px !important; margin-bottom: 0.75rem !important; }}
    .org-card-left {{ display: flex; align-items: center; gap: 0.65rem; }}
    .org-card-left img {{ height: 28px; border-radius: 4px; vertical-align: middle; }}
    .org-card-name {{ font-weight: 700; font-size: 1.1rem; color: #F1F5F9; }}
    .org-card-sub {{ font-size: 0.84rem; color: #94A3B8; margin-top: 0.05rem; }}
    .org-card-right {{ display: flex; align-items: center; gap: 0.85rem; text-align: right; }}
    .org-card-cost {{ font-size: 1rem; font-weight: 700; color: #10B981; }}
    .org-card-cost-yr {{ font-size: 0.92rem; font-weight: 600; color: rgba(16,185,129,0.7); }}
    .org-card-cost-label {{ font-size: 0.72rem; color: #64748B; }}

    /* Seamless expander under org card - org cards are only in scan overview */
    div[data-testid="stExpander"] > details {{ border-radius: 0 0 12px 12px !important; }}
    div[data-testid="stExpander"] > details > summary {{ padding: 0.55rem 1.15rem !important; font-size: 0.78rem !important; color: #64748B !important; }}

    /* Action bar styling */
    .action-bar {{ background: #0F1525; border: 1px solid #1E293B; border-radius: 8px; padding: 0.5rem 0.65rem; margin: 0.35rem 0; display: flex; align-items: center; gap: 0.5rem; }}
    .action-bar-label {{ font-size: 0.66rem; font-weight: 700; color: #64748B; text-transform: uppercase; letter-spacing: 0.06em; white-space: nowrap; }}

    /* ── Token Health Components ── */
    .th-strip {{ background: #111827; border: 1.5px solid #2A3548; border-radius: 12px; padding: 0.75rem 1.1rem; display: flex; align-items: center; justify-content: space-between; margin-bottom: 0.75rem; position: relative; overflow: hidden; }}
    .th-strip::before {{ content: ''; position: absolute; top: 0; left: 0; right: 0; height: 2px; background: linear-gradient(90deg, #10B981 60%, #F59E0B 80%, #EF4444 100%); opacity: 0.7; }}
    .th-strip-left {{ display: flex; align-items: center; gap: 0.85rem; }}
    .th-strip-info h3 {{ font-size: 0.88rem; font-weight: 700; color: #F1F5F9; margin: 0; }}
    .th-strip-info p {{ font-size: 0.72rem; color: #94A3B8; margin: 0.05rem 0 0 0; }}
    .th-strip-right {{ display: flex; align-items: center; gap: 1.2rem; }}
    .th-stat {{ text-align: center; }}
    .th-stat-num {{ font-family: 'Segoe UI', monospace; font-size: 1.15rem; font-weight: 700; line-height: 1; }}
    .th-stat-num.thg {{ color: #10B981; }}
    .th-stat-num.tha {{ color: #F59E0B; }}
    .th-stat-num.thr {{ color: #EF4444; }}
    .th-stat-label {{ font-size: 0.58rem; font-weight: 600; color: #64748B; text-transform: uppercase; letter-spacing: 0.06em; margin-top: 0.15rem; }}
    .th-div {{ width: 1px; height: 28px; background: #2A3548; }}
    .th-badge {{ display: inline-flex; align-items: center; gap: 5px; padding: 3px 10px 3px 7px; border-radius: 100px; font-size: 0.62rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.05em; }}
    .th-badge-ok {{ background: rgba(16,185,129,0.08); border: 1px solid rgba(16,185,129,0.2); color: #10B981; }}
    .th-badge-warn {{ background: rgba(245,158,11,0.08); border: 1px solid rgba(245,158,11,0.2); color: #F59E0B; }}
    .th-badge-crit {{ background: rgba(239,68,68,0.08); border: 1px solid rgba(239,68,68,0.2); color: #EF4444; }}
    .th-badge-dot {{ width: 5px; height: 5px; border-radius: 50%; }}
    .th-badge-ok .th-badge-dot {{ background: #10B981; box-shadow: 0 0 0 2px rgba(16,185,129,0.2); }}
    .th-badge-warn .th-badge-dot {{ background: #F59E0B; box-shadow: 0 0 0 2px rgba(245,158,11,0.2); }}
    .th-badge-crit .th-badge-dot {{ background: #EF4444; box-shadow: 0 0 0 2px rgba(239,68,68,0.2); }}

    /* Token alert banners */
    .th-alert {{ border-radius: 10px; padding: 0.55rem 1rem; display: flex; align-items: center; justify-content: space-between; margin-bottom: 0.4rem; }}
    .th-alert-red {{ background: rgba(239,68,68,0.06); border: 1px solid rgba(239,68,68,0.2); }}
    .th-alert-amber {{ background: rgba(245,158,11,0.06); border: 1px solid rgba(245,158,11,0.2); }}
    .th-alert-left {{ display: flex; align-items: center; gap: 0.5rem; }}
    .th-alert-text {{ font-size: 0.76rem; color: #CBD5E1; }}
    .th-alert-text strong {{ color: #F1F5F9; font-weight: 600; }}
    .th-alert-btn {{ font-size: 0.68rem; font-weight: 600; padding: 0.3rem 0.75rem; border-radius: 6px; cursor: pointer; white-space: nowrap; border: 1px solid; text-decoration: none; }}
    .th-alert-btn-red {{ background: rgba(239,68,68,0.12); border-color: rgba(239,68,68,0.3); color: #EF4444; }}
    .th-alert-btn-amber {{ background: rgba(245,158,11,0.12); border-color: rgba(245,158,11,0.3); color: #F59E0B; }}

    /* Token cards grid */
    .th-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(210px, 1fr)); gap: 0.6rem; margin-top: 0.6rem; }}
    .th-card {{ background: #111827; border: 1.5px solid #2A3548; border-radius: 12px; padding: 0.85rem 1rem; position: relative; overflow: hidden; transition: all 0.2s ease; }}
    .th-card:hover {{ background: #151f32; border-color: #3a4a60; }}
    .th-card::before {{ content: ''; position: absolute; top: 10px; bottom: 10px; left: 0; width: 3px; border-radius: 0 3px 3px 0; }}
    .th-card-ok::before {{ background: #10B981; box-shadow: 0 0 8px rgba(16,185,129,0.3); }}
    .th-card-warn::before {{ background: #F59E0B; box-shadow: 0 0 8px rgba(245,158,11,0.3); }}
    .th-card-crit::before {{ background: #EF4444; box-shadow: 0 0 8px rgba(239,68,68,0.3); }}
    .th-card-hdr {{ display: flex; align-items: center; justify-content: space-between; margin-bottom: 0.55rem; }}
    .th-card-plat {{ display: flex; align-items: center; gap: 7px; }}
    .th-card-plat img {{ width: 18px; height: 18px; border-radius: 3px; }}
    .th-card-plat span {{ font-size: 0.86rem; font-weight: 600; color: #F1F5F9; }}
    .th-card-dot {{ width: 7px; height: 7px; border-radius: 50%; }}
    .th-card-dot-ok {{ background: #10B981; box-shadow: 0 0 0 3px rgba(16,185,129,0.15); }}
    .th-card-dot-warn {{ background: #F59E0B; box-shadow: 0 0 0 3px rgba(245,158,11,0.15); }}
    .th-card-dot-crit {{ background: #EF4444; box-shadow: 0 0 0 3px rgba(239,68,68,0.15); }}
    .th-card-row {{ display: flex; justify-content: space-between; align-items: center; padding: 0.18rem 0; }}
    .th-card-lbl {{ font-size: 0.68rem; color: #64748B; font-weight: 500; }}
    .th-card-val {{ font-family: 'Segoe UI', monospace; font-size: 0.7rem; font-weight: 500; color: #CBD5E1; }}
    .th-card-val-ok {{ color: #10B981; }}
    .th-card-val-warn {{ color: #F59E0B; }}
    .th-card-val-crit {{ color: #EF4444; }}
    .th-bar-wrap {{ margin-top: 0.45rem; }}
    .th-bar-top {{ display: flex; justify-content: space-between; margin-bottom: 0.2rem; }}
    .th-bar-bg {{ height: 3px; background: #2A3548; border-radius: 3px; overflow: hidden; }}
    .th-bar-fill {{ height: 100%; border-radius: 3px; }}
    .th-bar-fill-ok {{ background: linear-gradient(90deg, #10B981, #34d399); }}
    .th-bar-fill-warn {{ background: linear-gradient(90deg, #F59E0B, #fbbf24); }}
    .th-bar-fill-crit {{ background: linear-gradient(90deg, #EF4444, #f87171); }}
</style>
""", unsafe_allow_html=True)


# ------------------------------------------------------------------
# Header + Nav
# ------------------------------------------------------------------
def show_header(user):
    logo = f'<img src="data:image/png;base64,{LOGO_WHITE}" alt="Xolv" />' if LOGO_WHITE else '<span style="color:#fff;font-weight:700;font-size:1.1rem">XOLV</span>'
    st.markdown(f"""
    <div class="hdr-wrap">
        <div class="hdr">
            <div class="hdr-logo">{logo}</div>
            <div class="hdr-right">
                <div>
                    <div class="hdr-name">{user['name']}</div>
                    <div class="hdr-email">{user['email']}</div>
                </div>
            </div>
        </div>
    </div>
    """, unsafe_allow_html=True)


def show_nav():
    active = st.session_state.page
    c1, c2, c3, c4, c5 = st.columns([2, 2, 1.5, 1.5, 1])
    with c1:
        if st.button("Scan Overview", use_container_width=True, type="primary" if active == "overview" else "secondary"):
            st.session_state.page = "overview"
            st.rerun()
    with c2:
        if st.button("Token Health", use_container_width=True, type="primary" if active == "tokens" else "secondary"):
            st.session_state.page = "tokens"
            st.rerun()
    with c3:
        if st.button("Audit Log", use_container_width=True, type="primary" if active == "audit" else "secondary"):
            st.session_state.page = "audit"
            st.rerun()
    with c4:
        if st.button("AI Insights", use_container_width=True, type="primary" if active == "insights" else "secondary"):
            st.session_state.page = "insights"
            st.rerun()
    with c5:
        if st.button("Sign out", use_container_width=True, key="signout_btn"):
            auth.logout()
            st.rerun()


# ------------------------------------------------------------------
# Token Health Helper
# ------------------------------------------------------------------
ROTATION_POLICY_DAYS = 90

def get_all_token_health():
    """Get health status for all configured systems."""
    results = []
    for sys_key, sys_info in config.SYSTEMS.items():
        meta = storage.get_token_metadata(sys_key)
        rot = get_rotation_entry(sys_key)
        is_removed = rot.get("removed", False) if rot else False
        entry = {"key": sys_key, "name": sys_info["name"], "meta": meta}
        if (not meta and not rot) or is_removed:
            entry["status"] = "missing"
            entry["days_left"] = 0
            entry["pct_elapsed"] = 100
            entry["last_rotated"] = None
            entry["expires"] = None
        else:
            # Use created_on from rotation metadata if available, else fall back to updated_at
            if rot and rot.get("created_on"):
                rotated_dt = datetime.fromisoformat(rot["created_on"])
            elif meta:
                rotated_dt = datetime.fromisoformat(meta["updated_at"])
            else:
                rotated_dt = datetime.utcnow()
            now_dt = datetime.now(rotated_dt.tzinfo) if rotated_dt.tzinfo else datetime.utcnow()
            days_since = (now_dt - rotated_dt).days
            days_left = ROTATION_POLICY_DAYS - days_since
            pct = min(100, int(days_since / ROTATION_POLICY_DAYS * 100))
            entry["days_left"] = days_left
            entry["pct_elapsed"] = pct
            entry["last_rotated"] = rotated_dt.strftime("%b %d, %Y")
            entry["expires"] = (rotated_dt + timedelta(days=ROTATION_POLICY_DAYS)).strftime("%b %d, %Y")
            entry["rotated_by"] = rot.get("rotated_by", "Unknown") if rot else meta.get("updated_by", "Unknown")
            if days_left <= 0:
                entry["status"] = "expired"
            elif days_left <= 14:
                entry["status"] = "expiring"
            else:
                entry["status"] = "healthy"
        results.append(entry)
    return results

def render_compact_health_strip():
    """Render a subtle one-line token health indicator for the scan overview page."""
    try:
        tokens = get_all_token_health()
    except Exception:
        return

    if not tokens:
        return

    healthy = sum(1 for t in tokens if t["status"] == "healthy")
    expiring = sum(1 for t in tokens if t["status"] == "expiring")
    expired = sum(1 for t in tokens if t["status"] in ("expired", "missing"))
    total = len(tokens)

    # Build compact dots: green/amber/red per token
    dots = ""
    for t in tokens:
        if t["status"] == "healthy":
            dots += '<span style="width:6px;height:6px;border-radius:50%;background:#10B981;display:inline-block;"></span>'
        elif t["status"] == "expiring":
            dots += '<span style="width:6px;height:6px;border-radius:50%;background:#F59E0B;display:inline-block;"></span>'
        else:
            dots += '<span style="width:6px;height:6px;border-radius:50%;background:#EF4444;display:inline-block;"></span>'

    # Only show alert text if something needs attention
    alert = ""
    if expired > 0:
        bad = [t for t in tokens if t["status"] in ("expired", "missing")]
        alert = '<span style="color:#EF4444;font-weight:600;">' + bad[0]["name"] + ' needs attention</span>'
    elif expiring > 0:
        warn = [t for t in tokens if t["status"] == "expiring"]
        alert = '<span style="color:#F59E0B;">' + warn[0]["name"] + ' rotation due in ' + str(warn[0]["days_left"]) + 'd</span>'

    separator = '<span style="color:#2A3548;margin:0 6px;">|</span>' if alert else ""

    st.markdown(
        '<div style="display:flex;align-items:center;gap:5px;padding:0 0.75rem;min-height:38px;font-size:0.72rem;color:#64748B;">'
        + '<span>' + str(healthy) + '/' + str(total) + ' integrations healthy</span>'
        + '<span style="display:inline-flex;gap:3px;margin:0 4px;">' + dots + '</span>'
        + separator + alert
        + '</div>',
        unsafe_allow_html=True
    )


# ------------------------------------------------------------------
# AI Insights
# ------------------------------------------------------------------
def _ai_icon(size=16, _color=None):
    """AI sparkle icon - three 4-pointed stars with cyan-to-purple gradient."""
    return (
        f'<svg width="{size}" height="{size}" viewBox="0 0 120 120" fill="none" '
        f'style="display:inline-block;vertical-align:middle;">'
        f'<defs>'
        f'<linearGradient id="ai_g1" x1="0" y1="1" x2="1" y2="0">'
        f'<stop offset="0%" stop-color="#00C9DB"/>'
        f'<stop offset="100%" stop-color="#5B6CF7"/>'
        f'</linearGradient>'
        f'<linearGradient id="ai_g2" x1="0" y1="1" x2="1" y2="0">'
        f'<stop offset="0%" stop-color="#5B6CF7"/>'
        f'<stop offset="100%" stop-color="#8B5CF6"/>'
        f'</linearGradient>'
        f'</defs>'
        f'<path d="M42 8C42 8 52 38 52 50C52 62 42 92 42 92C42 92 32 62 32 50C32 38 42 8 42 8Z'
        f'M42 8C42 8 12 28 0 38C0 38 12 48 42 8Z'
        f'" fill="url(#ai_g1)" opacity="0"/>'
        f'<path d="M45 10 C48 30, 62 48, 80 52 C62 56, 48 70, 45 92 C42 70, 28 56, 8 52 C28 48, 42 30, 45 10Z" fill="url(#ai_g1)"/>'
        f'<path d="M80 2 C82 14, 90 22, 102 25 C90 28, 82 36, 80 50 C78 36, 70 28, 58 25 C70 22, 78 14, 80 2Z" fill="url(#ai_g2)"/>'
        f'<path d="M85 72 C86 80, 90 84, 98 86 C90 88, 86 92, 85 100 C84 92, 80 88, 72 86 C80 84, 84 80, 85 72Z" fill="#7C3AED"/>'
        f'</svg>'
    )

def _ai_sparkle(size=16, color="#F59E0B"):
    """Single sparkle for minor decorative use."""
    return _ai_icon(size)

def _platform_logo(text: str, size: int = 18) -> str:
    """Return an inline SVG logo based on platform name detected in text."""
    t = text.lower()
    if "pagerduty" in t or "pager" in t:
        return f'<svg width="{size}" height="{size}" viewBox="0 0 24 24" style="display:inline-block;vertical-align:middle;"><path d="M16.965 1.18C15.085.164 13.769 0 10.683 0H3.73v14.55h6.926c2.743 0 4.8-.164 6.678-1.394 1.989-1.31 3.14-3.487 3.14-5.99-.001-2.72-1.37-4.95-3.509-5.986zM11.32 10.578H8.186V3.857h2.987c2.825 0 4.42 1.068 4.42 3.244 0 2.393-1.595 3.477-4.273 3.477zM3.73 17.96h4.457V24H3.73z" fill="#06AC38"/></svg>'
    elif "atlassian" in t or "jira" in t or "confluence" in t:
        return f'<svg width="{size}" height="{size}" viewBox="0 0 256 256" style="display:inline-block;vertical-align:middle;"><defs><linearGradient id="atl1" x1="99%" y1="35%" x2="46%" y2="60%"><stop offset="0%" stop-color="#0052CC"/><stop offset="100%" stop-color="#2684FF"/></linearGradient></defs><path d="M103.5 152.2c-3.9-4-10.1-3.7-13.6.7L34.6 229.6c3.3 5.1 9 8.4 15.4 8.4h78.9c6 0 11.5-3.5 14-9-14.3-28.8-30-59.7-39.4-76.8z" fill="url(#atl1)"/><path d="M122.4 27.4c-27 47.5-36 72.5-14.2 108.5l50.7 92.3c3.3 5 9 8 15.2 8H253c6.4 0 12-3.4 15.4-8.4L134.1 27.5c-3.2-5.5-8.5-5.6-11.7-.1z" fill="#2684FF"/></svg>'
    elif "gitlab" in t:
        return f'<svg width="{size}" height="{size}" viewBox="0 0 24 24" style="display:inline-block;vertical-align:middle;"><path d="M23.955 13.587l-1.342-4.135-2.664-8.189a.455.455 0 00-.867 0L16.418 9.45H7.582L4.918 1.263a.455.455 0 00-.867 0L1.386 9.452.044 13.587a.924.924 0 00.331 1.023L12 23.054l11.625-8.443a.92.92 0 00.33-1.024z" fill="#E24329"/><path d="M12 23.054L16.418 9.45H7.582L12 23.054z" fill="#FC6D26"/><path d="M12 23.054l-4.418-13.6H1.386L12 23.054z" fill="#FCA326"/><path d="M1.386 9.452L.044 13.587a.924.924 0 00.331 1.023L12 23.054 1.386 9.452z" fill="#E24329"/><path d="M12 23.054l4.418-13.6h6.196L12 23.054z" fill="#FCA326"/><path d="M22.614 9.452l1.341 4.135a.924.924 0 01-.33 1.023L12 23.054l10.614-13.602z" fill="#E24329"/></svg>'
    return ""

def show_insights():
    sparkle = _ai_sparkle()
    ai_icon = _ai_icon

    # -- Page header (same bg as other tabs, with gradient accent) --
    st.markdown(f"""<div class="phdr" style="position:relative;overflow:hidden;">
        <div style="position:absolute;top:0;left:0;right:0;height:3px;background:linear-gradient(90deg,#A927B2,#F59E0B,#F97316,#F59E0B,#A927B2);"></div>
        <div style="position:absolute;top:-40px;right:-20px;width:180px;height:180px;background:radial-gradient(circle,rgba(245,158,11,0.08) 0%,transparent 70%);pointer-events:none;"></div>
        <div style="position:absolute;bottom:-40px;left:-20px;width:150px;height:150px;background:radial-gradient(circle,rgba(169,39,178,0.06) 0%,transparent 70%);pointer-events:none;"></div>
        <div style="display:flex;align-items:center;gap:8px;margin-bottom:0.5rem;">
            {_ai_icon(18, '#F59E0B')}
            <span style="font-size:0.68rem;font-weight:700;color:#F59E0B;text-transform:uppercase;letter-spacing:0.12em;">AI-Powered Analysis</span>
        </div>
        <div style="font-size:1.5rem;font-weight:800;color:#F1F5F9;letter-spacing:-0.02em;margin-bottom:0.35rem;">Executive Summary</div>
        <div style="font-size:0.82rem;color:#94A3B8;line-height:1.5;">Intelligent analysis of your scan data to surface what matters most.</div>
    </div>""", unsafe_allow_html=True)

    # Load scan data
    scan_data = load_scan_results()
    if not scan_data:
        st.markdown('<div class="empty"><div style="font-size:1.1rem;font-weight:600;color:var(--t2);margin-bottom:0.4rem;">No Scan Data</div><div style="color:var(--t3);font-size:0.85rem;">Run a scan first to generate AI insights.</div></div>', unsafe_allow_html=True)
        return

    # -- Scan context bar --
    run_date = scan_data.get("run_date", "")
    try:
        dt = datetime.fromisoformat(run_date)
        date_str = dt.strftime("%b %d, %Y at %I:%M %p")
    except (ValueError, TypeError):
        date_str = run_date or "Unknown"

    st.markdown(f"""<div style="background:#111827;border:1.5px solid #2A3548;border-radius:10px;padding:0.75rem 1.1rem;margin-bottom:1rem;display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:0.5rem;">
        <div style="display:flex;align-items:center;gap:8px;">
            <div style="width:8px;height:8px;border-radius:50%;background:#10B981;box-shadow:0 0 6px rgba(16,185,129,0.5);"></div>
            <span style="font-size:0.75rem;color:#94A3B8;">Scan from <strong style="color:#F1F5F9;">{date_str}</strong></span>
        </div>
        <div style="display:flex;gap:1.25rem;">
            <span style="font-size:0.78rem;color:#F59E0B;font-weight:600;">{scan_data.get('total_inactive_users', 0)} inactive</span>
            <span style="font-size:0.78rem;color:#10B981;font-weight:600;">${scan_data.get('total_monthly_savings', 0):,.0f}/mo savings</span>
            <span style="font-size:0.78rem;color:#A927B2;font-weight:600;">{scan_data.get('total_platforms_scanned', 0)} platforms</span>
        </div>
    </div>""", unsafe_allow_html=True)

    # -- Generate button --
    generate = st.button("Generate AI Analysis", use_container_width=False, type="primary", key="btn_ai_gen")

    # Check cache or generate
    result = None
    if generate:
        with st.spinner("Claude is analyzing your scan data..."):
            result = ai_summary.generate_summary(force=True)
    else:
        result = ai_summary.generate_summary(force=False)

    if result and "error" in result:
        st.markdown(f"""<div style="background:rgba(239,68,68,0.08);border:1.5px solid rgba(239,68,68,0.2);border-radius:10px;padding:1rem 1.25rem;margin-top:0.75rem;">
            <div style="color:#EF4444;font-weight:600;font-size:0.85rem;">Error</div>
            <div style="color:#F1F5F9;font-size:0.82rem;margin-top:0.3rem;">{result['error']}</div>
        </div>""", unsafe_allow_html=True)
        return

    if result and "summary" in result:
        s = result["summary"]
        generated_at = result.get("generated_at", "")
        try:
            gen_dt = datetime.fromisoformat(generated_at)
            gen_str = gen_dt.strftime("%b %d, %Y at %I:%M %p")
        except (ValueError, TypeError):
            gen_str = generated_at

        # -- Headline card --
        headline = s.get("headline", "")
        if headline:
            st.markdown(f"""<div style="background:linear-gradient(135deg,#1a1425,#151020);border:1.5px solid #3D2E5C;border-radius:12px;padding:1.5rem 1.75rem;margin-top:0.75rem;position:relative;overflow:hidden;box-shadow:0 2px 16px rgba(169,39,178,0.1);">
                <div style="position:absolute;top:0;left:0;right:0;height:3px;background:linear-gradient(90deg,#A927B2,#F59E0B,#A927B2);"></div>
                <div style="display:flex;align-items:center;gap:8px;margin-bottom:0.6rem;">
                    {_ai_icon(14, '#F59E0B')}
                    <span style="font-size:0.65rem;font-weight:700;color:#F59E0B;text-transform:uppercase;letter-spacing:0.1em;">AI Headline</span>
                    <span style="margin-left:auto;font-size:0.65rem;color:#64748B;">Generated {gen_str}</span>
                </div>
                <div style="font-size:1.1rem;font-weight:600;color:#F1F5F9;line-height:1.5;">{headline}</div>
            </div>""", unsafe_allow_html=True)

        st.markdown("<div style='height:0.5rem'></div>", unsafe_allow_html=True)

        # -- Three-column cards: Findings, Opportunities, Actions --
        col1, col2, col3 = st.columns(3)

        # Findings
        findings = s.get("findings", [])
        findings_html = "".join(
            f'<div style="display:flex;gap:8px;align-items:flex-start;margin-bottom:0.65rem;">'
            f'<div style="min-width:6px;width:6px;height:6px;border-radius:50%;background:#3B82F6;margin-top:6px;box-shadow:0 0 4px rgba(59,130,246,0.4);"></div>'
            f'<div style="font-size:0.8rem;color:#CBD5E1;line-height:1.5;">{f}</div></div>'
            for f in findings
        )
        with col1:
            st.markdown(f"""<div style="background:#111827;border:1.5px solid #1E293B;border-radius:12px;padding:1.25rem;height:100%;position:relative;overflow:hidden;">
                <div style="position:absolute;top:0;left:0;right:0;height:2px;background:linear-gradient(90deg,#3B82F6,transparent);"></div>
                <div style="display:flex;align-items:center;gap:6px;margin-bottom:1rem;">
                    <div style="width:28px;height:28px;border-radius:8px;background:rgba(59,130,246,0.12);display:flex;align-items:center;justify-content:center;">
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none"><path d="M12 22C6.477 22 2 17.523 2 12S6.477 2 12 2s10 4.477 10 10-4.477 10-10 10zm-1-11v6h2v-6h-2zm0-4v2h2V7h-2z" fill="#3B82F6"/></svg>
                    </div>
                    <span style="font-size:0.72rem;font-weight:700;color:#3B82F6;text-transform:uppercase;letter-spacing:0.08em;">Key Findings</span>
                </div>
                {findings_html}
            </div>""", unsafe_allow_html=True)

        # Opportunities
        opportunities = s.get("opportunities", [])
        opps_html = ""
        for opp in opportunities:
            area = opp.get('area', '')
            logo = _platform_logo(area)
            opps_html += f"""<div style="background:rgba(16,185,129,0.05);border:1px solid rgba(16,185,129,0.15);border-radius:8px;padding:0.7rem 0.85rem;margin-bottom:0.5rem;">
                <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:0.25rem;">
                    <span style="display:flex;align-items:center;gap:6px;font-size:0.78rem;font-weight:600;color:#10B981;">{logo}{area}</span>
                    <span style="font-size:0.75rem;font-weight:700;color:#F59E0B;background:rgba(245,158,11,0.1);padding:2px 8px;border-radius:4px;">{opp.get('savings', '')}</span>
                </div>
                <div style="font-size:0.76rem;color:#94A3B8;line-height:1.45;">{opp.get('detail', '')}</div>
            </div>"""
        with col2:
            st.markdown(f"""<div style="background:#111827;border:1.5px solid #1E293B;border-radius:12px;padding:1.25rem;height:100%;position:relative;overflow:hidden;">
                <div style="position:absolute;top:0;left:0;right:0;height:2px;background:linear-gradient(90deg,#10B981,transparent);"></div>
                <div style="display:flex;align-items:center;gap:6px;margin-bottom:1rem;">
                    <div style="width:28px;height:28px;border-radius:8px;background:rgba(16,185,129,0.12);display:flex;align-items:center;justify-content:center;">
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none"><path d="M12 8V4l8 8-8 8v-4H4V8h8z" fill="#10B981"/></svg>
                    </div>
                    <span style="font-size:0.72rem;font-weight:700;color:#10B981;text-transform:uppercase;letter-spacing:0.08em;">Opportunities</span>
                </div>
                {opps_html}
            </div>""", unsafe_allow_html=True)

        # Actions
        actions = s.get("actions", [])
        actions_html = ""
        for i, a in enumerate(actions, 1):
            actions_html += f"""<div style="display:flex;gap:10px;align-items:flex-start;margin-bottom:0.65rem;">
                <div style="min-width:22px;height:22px;border-radius:6px;background:rgba(245,158,11,0.12);display:flex;align-items:center;justify-content:center;font-size:0.7rem;font-weight:700;color:#F59E0B;">{i}</div>
                <div style="font-size:0.8rem;color:#CBD5E1;line-height:1.5;">{a}</div>
            </div>"""
        with col3:
            st.markdown(f"""<div style="background:#111827;border:1.5px solid #1E293B;border-radius:12px;padding:1.25rem;height:100%;position:relative;overflow:hidden;">
                <div style="position:absolute;top:0;left:0;right:0;height:2px;background:linear-gradient(90deg,#F59E0B,transparent);"></div>
                <div style="display:flex;align-items:center;gap:6px;margin-bottom:1rem;">
                    <div style="width:28px;height:28px;border-radius:8px;background:rgba(245,158,11,0.12);display:flex;align-items:center;justify-content:center;">
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none"><path d="M13 3v2h3.59L7.76 13.83l1.41 1.41L18 6.41V10h2V3h-7z" fill="#F59E0B"/><path d="M18 18H6V6h5V4H6a2 2 0 00-2 2v12a2 2 0 002 2h12a2 2 0 002-2v-5h-2v5z" fill="#F59E0B"/></svg>
                    </div>
                    <span style="font-size:0.72rem;font-weight:700;color:#F59E0B;text-transform:uppercase;letter-spacing:0.08em;">Recommended Actions</span>
                </div>
                {actions_html}
            </div>""", unsafe_allow_html=True)

        # -- Bottom line --
        bottom_line = s.get("bottom_line", "")
        if bottom_line:
            st.markdown(f"""<div style="background:linear-gradient(135deg,rgba(16,185,129,0.06),rgba(245,158,11,0.06));border:1.5px solid rgba(16,185,129,0.2);border-radius:10px;padding:1rem 1.25rem;margin-top:0.75rem;display:flex;align-items:center;gap:10px;">
                <div style="min-width:32px;height:32px;border-radius:50%;background:rgba(16,185,129,0.12);display:flex;align-items:center;justify-content:center;">
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none"><path d="M12 2L14.09 8.26L20 9.27L15.55 13.97L16.91 20L12 16.9L7.09 20L8.45 13.97L4 9.27L9.91 8.26L12 2Z" fill="#10B981"/></svg>
                </div>
                <div style="font-size:0.88rem;color:#F1F5F9;font-weight:500;line-height:1.5;">{bottom_line}</div>
            </div>""", unsafe_allow_html=True)

    else:
        # No summary yet - show empty state
        st.markdown(f"""<div style="text-align:center;padding:3rem 2rem;background:#111827;border:1.5px dashed #2A3548;border-radius:14px;margin-top:0.75rem;">
            <div style="margin-bottom:0.75rem;">{_ai_icon(40, '#F59E0B')}</div>
            <div style="font-size:1.1rem;font-weight:600;color:#F1F5F9;margin-bottom:0.4rem;">No Summary Generated Yet</div>
            <div style="color:#64748B;font-size:0.85rem;max-width:400px;margin:0 auto;">Click <strong style="color:#F59E0B;">Generate AI Analysis</strong> above to create an intelligent analysis of your scan results.</div>
        </div>""", unsafe_allow_html=True)


# ------------------------------------------------------------------
# Dashboard
# ------------------------------------------------------------------
def show_dashboard():
    user = auth.get_current_user()
    register_user(user.get("email", ""), user.get("name", ""))
    show_header(user)
    show_nav()
    st.markdown("<div style='height:0.75rem'></div>", unsafe_allow_html=True)
    if st.session_state.page == "overview":
        show_scan_overview()
    elif st.session_state.page == "tokens":
        show_tokens(user)
    elif st.session_state.page == "insights":
        show_insights()
    else:
        show_audit()


# ------------------------------------------------------------------
# Scan Overview
# ------------------------------------------------------------------
def load_scan_results():
    """Load the latest scan results from JSON file."""
    scan_file = Path(__file__).parent / "scan_results.json"
    if scan_file.exists():
        try:
            return json.loads(scan_file.read_text())
        except (json.JSONDecodeError, KeyError):
            return None
    return None


def load_scan_history():
    """Load scan history for trend chart."""
    history_file = Path(__file__).parent / "scan_history.json"
    if history_file.exists():
        try:
            return json.loads(history_file.read_text())
        except (json.JSONDecodeError, IOError):
            return []
    return []


def load_scan_progress():
    """Load scan progress from JSON file."""
    progress_file = Path(__file__).parent / "scan_progress.json"
    if progress_file.exists():
        try:
            data = json.loads(progress_file.read_text())
            # Auto-expire stale progress (if stuck for > 10 minutes)
            if data.get("status") == "running" and data.get("updated_at"):
                try:
                    updated = datetime.fromisoformat(data["updated_at"].rstrip("Z"))
                    if (datetime.utcnow() - updated).total_seconds() > 600:
                        return None  # Stale, ignore
                except ValueError:
                    pass
            return data
        except (json.JSONDecodeError, IOError):
            return None
    return None


def start_scan():
    """Launch the silent scan runner as a background subprocess, save PID."""
    runner = Path("/Users/paulgerios/Project2/new_system_watchdog4/system_watchdog/run_scan_silent.py")
    if not runner.exists():
        st.error(f"Scan runner not found at {runner}")
        return
    log_file = Path(__file__).parent / "scan_log.txt"
    log = open(log_file, "w")
    proc = subprocess.Popen(
        ["python3", "-u", str(runner)],
        cwd=str(runner.parent),
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    # Save PID so we can kill it later
    pid_file = Path(__file__).parent / "scan_pid.txt"
    pid_file.write_text(str(proc.pid))


def stop_scan():
    """Kill the running scan subprocess and clean up."""
    pid_file = Path(__file__).parent / "scan_pid.txt"
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text().strip())
            os.kill(pid, signal.SIGTERM)
        except (ProcessLookupError, ValueError):
            pass  # Process already exited
        try:
            pid_file.unlink(missing_ok=True)
        except Exception:
            pass
    # Mark as cancelled in progress file
    progress_file = Path(__file__).parent / "scan_progress.json"
    try:
        if progress_file.exists():
            data = json.loads(progress_file.read_text())
            data["status"] = "cancelled"
            progress_file.write_text(json.dumps(data, indent=2))
        else:
            progress_file.write_text(json.dumps({"status": "cancelled"}))
    except Exception:
        pass


def show_scan_overview():
    progress = load_scan_progress()
    is_scanning = progress and progress.get("status") == "running"

    # ── Header with Run/Stop button ───────────────────────────────
    st.markdown("""<div class="phdr"><div class="phdr-ey">License Optimization</div>
        <div class="phdr-t">Scan Overview</div>
        <div class="phdr-d">Cost savings identified from inactive user licenses across all scanned platforms.</div></div>""", unsafe_allow_html=True)

    # ── Health strip + Run/Stop on same row ─────────────────────
    hcol1, hcol2 = st.columns([5.5, 1])
    with hcol1:
        render_compact_health_strip()
    with hcol2:
        if is_scanning:
            if st.button("⏹ Stop Scan", use_container_width=True):
                stop_scan()
                try:
                    log_event("console", "scan_stopped", auth.get_current_user().get("email", ""), {})
                except Exception:
                    pass
                st.rerun()
        else:
            if st.button("▶ Run Scan", type="primary", use_container_width=True):
                start_scan()
                try:
                    log_event("console", "scan_started", auth.get_current_user().get("email", ""), {})
                except Exception:
                    pass
                _time.sleep(0.5)
                st.rerun()

    # ── Progress bar (during scan) ────────────────────────────────
    if is_scanning:
        pct = progress.get("percent", 0)
        completed = progress.get("completed", 0)
        total = progress.get("total", 0)
        current = progress.get("current_platform", "")

        st.markdown(f"""
        <div style="margin:0.5rem 0 0.75rem 0;">
            <div style="position:relative;height:6px;background:#111827;border:1px solid #2A3548;border-radius:3px;overflow:hidden;">
                <div style="
                    position:absolute;top:0;left:0;bottom:0;
                    width:{max(pct, 2)}%;
                    background:linear-gradient(90deg,#028090,#03A5A5,#028090);
                    border-radius:3px;
                    transition:width 0.8s ease;
                "></div>
            </div>
            <div style="display:flex;justify-content:space-between;align-items:center;margin-top:0.35rem;">
                <span style="font-size:0.66rem;color:#94A3B8;">Scanning {current}...</span>
                <span style="font-size:0.62rem;font-weight:600;color:#028090;">{pct}% &nbsp;·&nbsp; {completed}/{total} platforms</span>
            </div>
        </div>
        """, unsafe_allow_html=True)

    # ── Status banners (complete / error / cancelled) ─────────────
    if progress and progress.get("status") == "complete":
        st.markdown("""
        <div style="background:rgba(16,185,129,0.08);border:1px solid rgba(16,185,129,0.2);border-radius:8px;padding:0.6rem 1rem;margin:0.5rem 0 0.75rem 0;display:flex;align-items:center;gap:8px;">
            <span style="color:#10B981;font-weight:700;">✓</span>
            <span style="font-size:0.82rem;color:#10B981;font-weight:600;">Scan complete - results updated</span>
        </div>
        """, unsafe_allow_html=True)
        progress_file = Path(__file__).parent / "scan_progress.json"
        try:
            progress_file.unlink(missing_ok=True)
        except Exception:
            pass

    if progress and progress.get("status") == "error":
        err_msg = progress.get("error", "Unknown error")
        st.markdown(f"""
        <div style="background:rgba(239,68,68,0.08);border:1px solid rgba(239,68,68,0.2);border-radius:8px;padding:0.6rem 1rem;margin:0.5rem 0 0.75rem 0;display:flex;align-items:center;gap:8px;">
            <span style="color:#EF4444;font-weight:700;">✗</span>
            <span style="font-size:0.82rem;color:#EF4444;font-weight:600;">Scan failed: {err_msg}</span>
        </div>
        """, unsafe_allow_html=True)
        progress_file = Path(__file__).parent / "scan_progress.json"
        try:
            progress_file.unlink(missing_ok=True)
        except Exception:
            pass

    if progress and progress.get("status") == "cancelled":
        completed = progress.get("completed", 0)
        total = progress.get("total", 0)
        st.markdown(f"""
        <div style="background:rgba(245,158,11,0.08);border:1px solid rgba(245,158,11,0.2);border-radius:8px;padding:0.6rem 1rem;margin:0.5rem 0 0.75rem 0;display:flex;align-items:center;gap:8px;">
            <span style="color:#F59E0B;font-weight:700;">⚠</span>
            <span style="font-size:0.82rem;color:#F59E0B;font-weight:600;">Scan stopped - results have not updated</span>
        </div>
        """, unsafe_allow_html=True)
        progress_file = Path(__file__).parent / "scan_progress.json"
        try:
            progress_file.unlink(missing_ok=True)
        except Exception:
            pass

    # ── Load data (always show last results) ──────────────────────
    data = load_scan_results()

    if not data:
        st.markdown("""<div class="empty">
            <div class="empty-t">No scan data available</div>
            <div class="empty-d">Click Run Scan above or run System Watchdog to generate scan results.</div>
        </div>""", unsafe_allow_html=True)
        # Still poll if scanning
        if is_scanning:
            _time.sleep(2.5)
            st.rerun()
        return

    # ── Headline metrics ──────────────────────────────────────────
    monthly = data.get("total_monthly_savings", 0)
    annual = data.get("total_annual_savings", 0)
    inactive = data.get("total_inactive_users", 0)
    platforms = data.get("total_platforms_scanned", 0)
    systems = data.get("total_systems", 0)
    run_date = data.get("run_date", "")
    threshold = data.get("inactivity_days", 90)

    if run_date:
        try:
            run_dt = datetime.fromisoformat(run_date)
            run_label = run_dt.strftime("%b %d, %Y at %H:%M UTC")
        except ValueError:
            run_label = run_date
    else:
        run_label = "Unknown"

    # ── Load user action statuses ─────────────────────────────────
    actions = load_user_actions()
    orgs = data.get("orgs", {})

    # Calculate realized vs pending savings
    realized_monthly = 0
    pending_monthly = 0
    kept_monthly = 0
    count_pending = 0
    count_deactivated = 0
    count_kept = 0

    for org_name, org_data in orgs.items():
        for p in org_data.get("platforms", []):
            for u in p.get("users", []):
                key = action_key(org_name, p.get("name", ""), u.get("email", ""))
                status = get_status(actions, key)
                cost = u.get("cost", 0)
                if status == "deactivated":
                    realized_monthly += cost
                    count_deactivated += 1
                elif status == "kept":
                    kept_monthly += cost
                    count_kept += 1
                else:
                    pending_monthly += cost
                    count_pending += 1

    # Big savings banner - split into realized / pending
    if is_scanning:
        st.markdown("""
        <div style="background:#111827;border:1.5px solid #2A3548;border-radius:12px;padding:1.5rem 1.75rem;margin-bottom:0.75rem;position:relative;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,0.3);">
            <div style="position:absolute;top:0;left:0;right:0;height:3px;background:linear-gradient(90deg,#10B981,#028090,#10B981);"></div>
            <div style="font-size:0.66rem;font-weight:700;color:#10B981;text-transform:uppercase;letter-spacing:0.1em;margin-bottom:0.75rem;">License Optimization</div>
            <div style="display:flex;align-items:center;gap:10px;">
                <div style="width:10px;height:10px;border-radius:50%;background:#028090;animation:pulse 1.5s ease-in-out infinite;"></div>
                <div style="font-size:1.1rem;font-weight:600;color:#94A3B8;">Scan in progress - results will update soon</div>
            </div>
            <style>@keyframes pulse{0%,100%{opacity:1}50%{opacity:0.3}}</style>
        </div>
        """, unsafe_allow_html=True)
    else:
        st.markdown(f"""
        <div style="background:#111827;border:1.5px solid #2A3548;border-radius:12px;padding:1.5rem 1.75rem;margin-bottom:0.75rem;position:relative;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,0.3);">
            <div style="position:absolute;top:0;left:0;right:0;height:3px;background:linear-gradient(90deg,#10B981,#028090,#10B981);"></div>
            <div style="display:flex;gap:2.5rem;flex-wrap:wrap;">
                <div>
                    <div style="font-size:0.66rem;font-weight:700;color:#10B981;text-transform:uppercase;letter-spacing:0.1em;margin-bottom:0.5rem;">Savings Realized</div>
                    <div style="font-size:2rem;font-weight:800;color:#10B981;letter-spacing:-0.03em;line-height:1;">${realized_monthly:,.2f}</div>
                    <div style="font-size:0.76rem;color:#64748B;margin-top:0.2rem;">per month</div>
                </div>
                <div style="width:1px;background:#2A3548;align-self:stretch;"></div>
                <div>
                    <div style="font-size:0.66rem;font-weight:700;color:#F59E0B;text-transform:uppercase;letter-spacing:0.1em;margin-bottom:0.5rem;">Pending Action</div>
                    <div style="font-size:2rem;font-weight:800;color:#F59E0B;letter-spacing:-0.03em;line-height:1;">${pending_monthly:,.2f}</div>
                    <div style="font-size:0.76rem;color:#64748B;margin-top:0.2rem;">per month</div>
                </div>
                <div style="width:1px;background:#2A3548;align-self:stretch;"></div>
                <div>
                    <div style="font-size:0.66rem;font-weight:700;color:#64748B;text-transform:uppercase;letter-spacing:0.1em;margin-bottom:0.5rem;">Total Identified</div>
                    <div style="font-size:2rem;font-weight:800;color:#94A3B8;letter-spacing:-0.03em;line-height:1;">${monthly:,.2f}</div>
                    <div style="font-size:0.76rem;color:#64748B;margin-top:0.2rem;">per month · ${annual:,.2f}/yr</div>
                </div>
            </div>
        </div>
        """, unsafe_allow_html=True)

    # Summary stats row
    st.markdown(f"""
    <div style="display:grid;grid-template-columns:1fr 1fr 1fr 1fr;gap:0.6rem;margin-bottom:0.75rem;">
        <div style="background:#111827;border:1.5px solid #2A3548;border-radius:10px;padding:0.85rem 1rem;text-align:center;">
            <div style="font-size:1.4rem;font-weight:700;color:#F59E0B;line-height:1;">{count_pending}</div>
            <div style="font-size:0.7rem;color:#64748B;margin-top:0.2rem;">Pending</div>
        </div>
        <div style="background:#111827;border:1.5px solid #2A3548;border-radius:10px;padding:0.85rem 1rem;text-align:center;">
            <div style="font-size:1.4rem;font-weight:700;color:#10B981;line-height:1;">{count_deactivated}</div>
            <div style="font-size:0.7rem;color:#64748B;margin-top:0.2rem;">Deactivated</div>
        </div>
        <div style="background:#111827;border:1.5px solid #2A3548;border-radius:10px;padding:0.85rem 1rem;text-align:center;">
            <div style="font-size:1.4rem;font-weight:700;color:#93C5FD;line-height:1;">{count_kept}</div>
            <div style="font-size:0.7rem;color:#64748B;margin-top:0.2rem;">Kept</div>
        </div>
        <div style="background:#111827;border:1.5px solid #2A3548;border-radius:10px;padding:0.85rem 1rem;text-align:center;">
            <div style="font-size:1.4rem;font-weight:700;color:#F1F5F9;line-height:1;">{platforms}</div>
            <div style="font-size:0.7rem;color:#64748B;margin-top:0.2rem;">Instances</div>
        </div>
    </div>
    """, unsafe_allow_html=True)


    # ── Orphan scan detection (only after a completed scan) ──────
    if progress and progress.get("status") == "complete":
        try:
            tokens_health = get_all_token_health()
            removed_systems = {t["name"] for t in tokens_health if t["status"] == "missing"}
            if removed_systems and orgs:
                scanned_platforms = set()
                for org_data in orgs.values():
                    for p in org_data.get("platforms", []):
                        scanned_platforms.add(p.get("name", ""))
                for removed in removed_systems:
                    base = removed.split("[")[0].split("(")[0].strip()
                    for scanned in scanned_platforms:
                        if base.lower() in scanned.lower():
                            st.markdown(
                                '<div style="background:rgba(245,158,11,0.06);border:1px solid rgba(245,158,11,0.2);border-radius:8px;padding:0.5rem 0.85rem;margin-bottom:0.5rem;display:flex;align-items:center;gap:8px;">'
                                + '<span style="font-size:0.82rem;">&#9888;</span>'
                                + '<span style="font-size:0.76rem;color:#F59E0B;">'
                                + '<strong>' + removed + '</strong> was removed from console but the scanner is still scanning it. '
                                + 'Update files configuration to stop scanning, or re-add the token under Token Health.'
                                + '</span></div>',
                                unsafe_allow_html=True
                            )
                            break
        except Exception:
            pass

    # ── Per-org breakdown ─────────────────────────────────────────
    if not orgs:
        pass
    else:
        st.markdown('<div class="sl">Breakdown by Organization</div>', unsafe_allow_html=True)

    # Org logo mapping (base64-encoded PNGs)
    ORG_LOGOS = {
        "Xolv": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAABMAAAAUCAYAAABvVQZ0AAABCGlDQ1BJQ0MgUHJvZmlsZQAAeJxjYGA8wQAELAYMDLl5JUVB7k4KEZFRCuwPGBiBEAwSk4sLGHADoKpv1yBqL+viUYcLcKakFicD6Q9ArFIEtBxopAiQLZIOYWuA2EkQtg2IXV5SUAJkB4DYRSFBzkB2CpCtkY7ETkJiJxcUgdT3ANk2uTmlyQh3M/Ck5oUGA2kOIJZhKGYIYnBncAL5H6IkfxEDg8VXBgbmCQixpJkMDNtbGRgkbiHEVBYwMPC3MDBsO48QQ4RJQWJRIliIBYiZ0tIYGD4tZ2DgjWRgEL7AwMAVDQsIHG5TALvNnSEfCNMZchhSgSKeDHkMyQx6QJYRgwGDIYMZAKbWPz9HbOBQAAADJElEQVR42pVTS4scVRg991ZVT9dM5tG0iZqZaIiBEHcGhQHB1r/Q0L1QkUAgIRvd+AAfGYNEEGHQjYyIWYp0g4qvTZT4ABUds5COxhFmEIVkMnZPd7qru6ru/eq4mCpTjlHGDz4Kqs49dc75vqvwP6pRazj1Zl3eu2vx7r1F84VfuDw2Xljnvqm200/672gAIKlIOvneTkRQAcAylr0knH19EM75G8FsMoru0Kvdfb9uDnad1ACglKJSSvJNbh3O6vnKZ069WZeP5n9+IRrffeSPoW+D0V61ObxN/dbdf/TguXNXFQDEcXxSa11VSo1IFkh+53necyQdpZTUag2n2axLtfrGvbvt5Jflazq5+ZrDA6bkjsyVl+uXHnzyfGXBVSRVGIb7C4XCj1rrYqbEWlv1PO/d86T7gILMHVsszarJr8pm4tBMz5q5cMqb6ZkL5XhzvnSglNSatUQD0L7vrxhjainPCIBorZf6/f6ejSaooBhNuw9dvWXi0O/Fnrk8kzirU8Pwh5u6j5z4/oS5eGeNCoo6zcctFosfWmvPAvABWK31Ht/3l+p1JQmpw4J+q510Lm1MO85GSeu18c0n3v7g+MVKZcE9fVol1ye1NU1NclJEVkkmJCOSjKLoKAA4ALynXzoyceZNlh9/5WMAqCwsuPkh/TXNrYfqJ0lyDIBKW1zXfXU4HN7+LKnlxacuxMP28fbE8FEA6nMg+delJOkCgIgscqsikrTWfgIAy8v0drzh2fK2Wq2CiHybJ4zj+DEAaLRahVT1zshWVlbGcmSWpBWRURiGh1Oc3glZZvO1HJGQNCQpIt80Go3syqn/InIAIAzDao4o4fUyaX6n8j++EZEmqYIgmBORdl6NtfZrEbmSvotFxAZBcE9ewPacMnufpiri9HCn0+lMW2sfzg9DRFokx/5hNyOy1j6Ts2PShc2uGETk/fR7SJLGmMUb2g2CYD5VYjMiY8zZFOyRVIPB4NZcBHGKuf9vdnu9XllEfknlZ1P7aX19fVdmIwNHUVTPxZCIyFq32y2lGAVrbSO/BiISxXF83/aAc3E0ttldyrB/ApXB7exKRTgWAAAAAElFTkSuQmCC",
        "Catalight": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAABYAAAAUCAYAAACJfM0wAAABCGlDQ1BJQ0MgUHJvZmlsZQAAeJxjYGA8wQAELAYMDLl5JUVB7k4KEZFRCuwPGBiBEAwSk4sLGHADoKpv1yBqL+viUYcLcKakFicD6Q9ArFIEtBxopAiQLZIOYWuA2EkQtg2IXV5SUAJkB4DYRSFBzkB2CpCtkY7ETkJiJxcUgdT3ANk2uTmlyQh3M/Ck5oUGA2kOIJZhKGYIYnBncAL5H6IkfxEDg8VXBgbmCQixpJkMDNtbGRgkbiHEVBYwMPC3MDBsO48QQ4RJQWJRIliIBYiZ0tIYGD4tZ2DgjWRgEL7AwMAVDQsIHG5TALvNnSEfCNMZchhSgSKeDHkMyQx6QJYRgwGDIYMZAKbWPz9HbOBQAAAFI0lEQVR42oWVTYzdZRXGn+ec937MvdNO58OhlU5bmLQFWgHHAg02tFQT0KoBm0qEhUkNohBWJsaNNnftQuNGNG6aqO0CA42aGA0frcqHQSVCSYHq0Jlhvqdz752Z/733//7fc1wUlJ1nc1bnyZPzJL+H+D9z6tQpaTQads+XH9td7at/y4HbzWzQzUqi2gZ5xd3OPverH5374EKAhhEADh8+HDC0e2spiHxUNOY9OX/u9Hv3fuWJJ0uVvkcspUUC68lSBBwiKgD6KGEElmZbVxe/+dff/7INOK8JHz95gAj7UBQzJhQxN4agHmOLtU0PEjzmZq9pCDnhWeEGOoxCFREUMR+mhC0EP8Yiu+/Tt2xbCwBQWAoS8gt/OveLyY86/uxXn7gvOb9oKb5KSrTUW1GtZAL2KHSAmvJiC8lp8ySEKKX6k0aj8bACwNgnPjUGikxffH3hw79idF9dGH7u5u+Kagb3JQJLKeZdAVfoqZW8IA3LRh+leYuAQrBnfP+dswEAWXgWkK/c/8iTm72u9RdfxBJHcJeL10huuKcmHE2Hb4hyPhW9cilUc/deCyY7BHgbouNOtGFYcNGvBQDeV+FSSgNfz5Nv13Uvha0rWfI0ZYWf1RD2m3smQDelYglmrsn/fDXvrW0uFwfovAyp7jTYKl1qQjYdtjccPvH4VjOcAtwtj1OhVipS3qtCZK8IZmPR+U2pVLnNzNquxqD6j+ef/enCBzFcOHr80c+lVGxAZAuDdM3RE0pFFPyGw8yKeNFTnLKiN+WpN+VFvAT49aI6aI4IACGJo1sp/psDgGR0ACCvbXcXd6fu3H/gMTreSSlfYez902LndQODi9aUUid1iC50YkMcLZfutpsmDi205yfjyJ6DB8WLCkN5mMIaDEMiWgcxKATFgQyO7mg9vnX+t2eWE8LlCqQjZorCO0k5V1CGo3CPuFrKOp9Zxcj9hAyzXNvhRqH7ECmA2wCN03rD/rseoHlTJa2v5YJd459EicXNLrpztR7yrqZ37/hX+w8ifmO923t5bqA2sdZfjv05tqpgwCiRyj0AClC2kTIG+s8CwTecvjtZWHPabaFamuho2UG88uhry0eHMvtShD3eN9fNInBMpLPw3uYweebmyu8Gq4NH2I3jMM8oOu7wOiALz5/98dO649bb3xYP94AYUwbm1YrcObn6xkMXN75bSTZRCKwAm7lKluAx0YdGOz5+9/vdg1N9fKW5qTJaAm5wsEzq7pTiQ1dOfD7TK2/+Ldtx4O6/aOHbtVwd277YvHh0svO9pppG2IxCZglvA94C2IGjtc7oTm66dW7jjksj+kLsqx9SUe91sxMXfv7UPI4coQLgrl03VSXvtJ4b5unvvNr8fldcSMwYfLkHy5R6VSntHNaNiCaQDkgkwcAtS/no+RsHf1ieufJMqb+28u8TX4hoNEwAeMm1Nj000P/s0zMTkbbX3RaSpWZytnueFjeQz+fiyz3EuRyYd/f1AqmZgFlx7Pv76ZcuzQwNDmnOQTQaBoACALUMWKt4p9tXOW5k1yA9E+lFpjVXX1nXopmCXl4TaxXirZzWhkhmYLeA4+z260++X7P1eoz+IRmvYbOc7LpWXtZUQOFvqvlUcJ+D9eaT2Oq351+44AAUwA+uu/do2dnSUFkP0AwioRoL3bZu7OJ/PUEAOHTs4UEJlZP93TRdeMHCmaDORIkuIfeSFCFayMsaJRVliamkLgSgFXN1ga2VSx+vafnMH595ahEA/wOJYNwNyNe6WAAAAABJRU5ErkJggg==",
    }

    LOGO_OVERRIDES = {"Atlassian": "atlassian", "PagerDuty": "pagerduty", "GitHub": "github", "GitLab": "gitlab"}
    def logo_url(name):
        icon = LOGO_OVERRIDES.get(name, name.lower())
        return f"https://cdn.jsdelivr.net/gh/homarr-labs/dashboard-icons/png/{icon}.png"

    for org_name, org_data in orgs.items():
        org_count = org_data.get("count", 0)
        org_monthly = org_data.get("monthly", 0)
        org_annual = org_data.get("annual", 0)
        org_platforms = org_data.get("platforms", [])
        org_key = org_name.replace(" ", "_")

        # ── Org header card with logo (always visible) ───────────
        org_logo_src = ORG_LOGOS.get(org_name, "")
        org_logo_html = f'<img src="{org_logo_src}" />' if org_logo_src else ""

        if org_count > 0:
            plat_with_inactive = len([p for p in org_platforms if p.get("inactive_users", 0) > 0])
            sub = f"{org_count} inactive users across {plat_with_inactive} platforms"
            right_html = f"""
                <div style="text-align:right;">
                    <span class="org-card-cost">${org_monthly:,.2f}</span><span class="org-card-cost-label">/mo</span>
                    &nbsp;&nbsp;
                    <span class="org-card-cost-yr">${org_annual:,.2f}</span><span class="org-card-cost-label">/yr</span>
                </div>"""
        else:
            sub = "✓ No inactive users"
            right_html = '<span style="color:#10B981;font-weight:600;font-size:0.85rem;">All active</span>'

        if org_count == 0:
            st.markdown(f"""
            <div class="org-card org-card-standalone" style="border-radius:12px;">
                <div class="org-card-left">
                    {org_logo_html}
                    <div>
                        <div class="org-card-name">{org_name}</div>
                        <div class="org-card-sub">{sub}</div>
                    </div>
                </div>
                <div class="org-card-right">{right_html}</div>
            </div>
            """, unsafe_allow_html=True)
            continue

        st.markdown(f"""
        <div class="org-card">
            <div class="org-card-left">
                {org_logo_html}
                <div>
                    <div class="org-card-name">{org_name}</div>
                    <div class="org-card-sub">{sub}</div>
                </div>
            </div>
            <div class="org-card-right">{right_html}</div>
        </div>
        """, unsafe_allow_html=True)

        # ── Collapsible body (collapsed by default) ──────────────
        with st.expander("Details", expanded=False):

            # ── Platform filter chips ────────────────────────────
            if f"pf_{org_key}" not in st.session_state:
                st.session_state[f"pf_{org_key}"] = "All"

            active_pf = st.session_state.get(f"pf_{org_key}", "All")

            # Visual platform chips (with logos, counts, cost)
            chips_html = ""
            plat_names_list = []
            for p in org_platforms:
                p_name = p.get("name", "Unknown")
                plat_names_list.append(p_name)
                p_inactive = p.get("inactive_users", 0)
                p_monthly = p.get("monthly", 0)
                p_logo = logo_url(p_name)
                is_active = active_pf == p_name
                border_color = "#028090" if is_active else "#2A3548"
                bg = "rgba(2,128,144,0.1)" if is_active else "#111827"
                if p_inactive > 0:
                    chips_html += f'<div class="plat-chip" style="border-color:{border_color};background:{bg};cursor:default;"><img src="{p_logo}" onerror="this.style.display=\'none\'" /><span class="pc-name">{p_name}</span><span class="pc-count">{p_inactive} users</span><span class="pc-cost">${p_monthly:,.0f}/mo</span></div>'
                else:
                    chips_html += f'<div class="plat-chip" style="border-color:{border_color};background:{bg};cursor:default;"><img src="{p_logo}" onerror="this.style.display=\'none\'" /><span class="pc-name">{p_name}</span><span style="color:#10B981;font-size:0.74rem;">✓ Active</span></div>'
            if chips_html:
                st.markdown(f'<div class="plat-chips">{chips_html}</div>', unsafe_allow_html=True)

            # ── Flatten users ────────────────────────────────────
            all_users = []
            for p in org_platforms:
                p_name = p.get("name", "Unknown")
                for u in p.get("users", []):
                    akey = action_key(org_name, p_name, u.get("email", ""))
                    u_status = get_status(actions, akey)
                    u_conf = confidence_score(u.get("inactivity_type", ""), u.get("last_active", ""))
                    all_users.append((u, p_name, u_status, akey, u_conf))

            # Sort by confidence (highest first)
            all_users.sort(key=lambda x: (CONFIDENCE_SORT_ORDER.get(x[4]["level"], 1), -x[4]["score"]))

            if not all_users:
                st.markdown('<div style="color:#64748B;font-size:0.85rem;padding:0.5rem 0;">No inactive users found.</div>', unsafe_allow_html=True)
            else:
                # Filter row: platform + status + search
                fc1, fc2, fc3 = st.columns([2, 2, 3])
                with fc1:
                    plat_options = ["All Platforms"] + plat_names_list
                    pf_idx = 0
                    if active_pf != "All" and active_pf in plat_names_list:
                        pf_idx = plat_names_list.index(active_pf) + 1
                    plat_filter_val = st.selectbox("Platform", plat_options, index=pf_idx, key=f"pf_sel_{org_key}", label_visibility="collapsed")
                    new_pf = "All" if plat_filter_val == "All Platforms" else plat_filter_val
                    if new_pf != active_pf:
                        st.session_state[f"pf_{org_key}"] = new_pf
                        st.rerun()
                with fc2:
                    status_filter = st.selectbox("Status", ["All Statuses", "Pending", "Deactivated", "Kept"], key=f"sf_{org_key}", label_visibility="collapsed")
                with fc3:
                    prev_search = st.session_state.get(f"search_prev_{org_key}", "")
                    search_query = st.text_input("Search", placeholder="Search by name or email...", key=f"search_{org_key}", label_visibility="collapsed")
                    # Reset pagination when search changes
                    if search_query != prev_search:
                        st.session_state[f"search_prev_{org_key}"] = search_query
                        st.session_state[f"pg_{org_key}"] = 0

                # Apply filters
                plat_filter = st.session_state.get(f"pf_{org_key}", "All")
                search_q = search_query.strip().lower() if search_query else ""
                filtered = []
                for u, p_name, u_status, akey, u_conf in all_users:
                    if plat_filter != "All" and p_name != plat_filter:
                        continue
                    if status_filter != "All Statuses" and u_status != status_filter.lower():
                        continue
                    if search_q:
                        u_name_l = u.get("name", "").lower()
                        u_email_l = u.get("email", "").lower()
                        if search_q not in u_name_l and search_q not in u_email_l:
                            continue
                    filtered.append((u, p_name, u_status, akey, u_conf))

                if not filtered:
                    st.markdown('<div style="text-align:center;padding:1.5rem;color:#64748B;font-size:0.85rem;">No users match filters.</div>', unsafe_allow_html=True)
                else:
                    # ── Confidence summary banner ──────────────────
                    conf_high_ct = sum(1 for *_, c in filtered if c["level"] == "high")
                    conf_med_ct = sum(1 for *_, c in filtered if c["level"] == "medium")
                    conf_low_ct = sum(1 for *_, c in filtered if c["level"] == "low")
                    ai_ico = _ai_icon(14, '#F59E0B')
                    st.markdown(f'''<div class="conf-banner">
                        <div class="conf-banner-top">
                            <span class="conf-banner-sparkle">{ai_ico}</span>
                            <span class="conf-banner-ai">AI Recommendation</span>
                            <span class="conf-banner-sub">Login activity &middot; Inactivity duration &middot; Escalation policy</span>
                        </div>
                        <div class="conf-banner-counts">
                            <div class="conf-banner-item">
                                <span class="conf-banner-ct" style="color:#6EE7B7;">{conf_high_ct}</span>
                                <span class="conf-banner-label" style="color:#6EE7B7;">Safe to deactivate</span>
                            </div>
                            <div class="conf-banner-item">
                                <span class="conf-banner-ct" style="color:#F59E0B;">{conf_med_ct}</span>
                                <span class="conf-banner-label" style="color:#F59E0B;">Review recommended</span>
                            </div>
                            <div class="conf-banner-item">
                                <span class="conf-banner-ct" style="color:#FCA5A5;">{conf_low_ct}</span>
                                <span class="conf-banner-label" style="color:#FCA5A5;">Needs investigation</span>
                            </div>
                        </div>
                    </div>''', unsafe_allow_html=True)

                    st.markdown('<div style="height:0.3rem;"></div>', unsafe_allow_html=True)

                    # ── Interactive table with widgets per row ────
                    pending_count = sum(1 for u, p, s, k, c in filtered if s == "pending")
                    ROWS_PER_PAGE = 15

                    # Column proportions - shared by header + rows
                    COL_W = [0.25, 1.0, 1.4, 2.1, 1.2, 0.55, 0.9, 1.2, 0.7]

                    # ── Table header (st.columns - matches row layout) ─
                    hdr_labels = ["", "Platform", "Name", "Email", "Last Active", "Cost", None, "Status", "Action"]
                    hdr_style = 'font-size:0.7rem;font-weight:700;color:#64748B;text-transform:uppercase;letter-spacing:0.06em;padding:0;margin-top:-11px;'
                    hdr_cols = st.columns(COL_W)
                    for i, lbl in enumerate(hdr_labels):
                        with hdr_cols[i]:
                            if lbl is None:
                                st.markdown(f'<div class="ai-col-hdr"><span class="ai-score-hdr">{_ai_icon(11)} <span class="ai-score-hdr-label">AI Score</span></span></div>', unsafe_allow_html=True)
                            else:
                                st.markdown(f'<div style="{hdr_style}">{lbl}</div>', unsafe_allow_html=True)
                    st.markdown('<div style="border-bottom:2px solid #2A3548;margin:-0.4rem 0 0.15rem 0;"></div>', unsafe_allow_html=True)

                    # ── Select-all + pagination controls ─────────
                    pg_key = f"pg_{org_key}"
                    if pg_key not in st.session_state:
                        st.session_state[pg_key] = 0
                    total_pages = max(1, -(-len(filtered) // ROWS_PER_PAGE))  # ceil division
                    current_page = min(st.session_state[pg_key], total_pages - 1)
                    page_start = current_page * ROWS_PER_PAGE
                    page_end = min(page_start + ROWS_PER_PAGE, len(filtered))
                    page_slice = filtered[page_start:page_end]

                    sa_col, info_col, pg_col = st.columns([0.25, 5, 4])
                    with sa_col:
                        select_all = st.checkbox("All", key=f"sa_{org_key}", label_visibility="collapsed")
                    with info_col:
                        st.markdown(f'<div style="font-size:0.72rem;color:#64748B;margin-top:-5px;line-height:1;">{len(filtered)} users - Showing {page_start+1}-{page_end}</div>', unsafe_allow_html=True)
                    with pg_col:
                        pc1, pc2, pc3 = st.columns([1, 2, 1])
                        with pc1:
                            if st.button("‹ Prev", key=f"pprev_{org_key}", disabled=(current_page == 0), use_container_width=True):
                                st.session_state[pg_key] = current_page - 1
                                st.rerun()
                        with pc2:
                            st.markdown(f'<div style="text-align:center;font-size:0.72rem;color:#94A3B8;margin-top:-11px;">Page {current_page+1} of {total_pages}</div>', unsafe_allow_html=True)
                        with pc3:
                            if st.button("Next ›", key=f"pnext_{org_key}", disabled=(current_page >= total_pages - 1), use_container_width=True):
                                st.session_state[pg_key] = current_page + 1
                                st.rerun()

                    st.markdown('<div style="border-bottom:1px solid #1E293B;margin:0 0 0.15rem 0;"></div>', unsafe_allow_html=True)

                    # ── Render page rows ─────────────────────────
                    status_options = ["Pending", "Deactivated", "Kept"]
                    changed_keys = []

                    for row_i, (u, p_name, u_status, akey, u_conf) in enumerate(page_slice):
                        global_i = page_start + row_i
                        u_name = u.get("name", "Unknown")
                        u_email = u.get("email", "")
                        u_last = u.get("last_active", "")
                        u_cost = u.get("cost", 0)
                        u_inactivity = u.get("inactivity_type", "")

                        # Format last active
                        if u_last:
                            try:
                                dt = datetime.fromisoformat(str(u_last).rstrip("Z"))
                                days_ago = (datetime.utcnow() - dt).days
                                last_label = f'{dt.strftime("%b %d")} ({days_ago}d)'
                            except (ValueError, TypeError):
                                last_label = str(u_last).replace("-", "-")
                        elif u_inactivity:
                            last_label = u_inactivity.replace("-", "-")
                        else:
                            last_label = "Unknown"

                        cost_label = f'${u_cost:,.0f}' if u_cost > 0 else '-'
                        p_logo = logo_url(p_name)
                        dim_cls = "tcell-dim" if u_status == "deactivated" else ""

                        # Row columns (same proportions as header)
                        rc = st.columns(COL_W)

                        with rc[0]:
                            st.checkbox("sel", key=f"cb_{org_key}_{global_i}", value=select_all, label_visibility="collapsed")

                        with rc[1]:
                            st.markdown(f'<div class="tcell tcell-plat {dim_cls}"><img src="{p_logo}" onerror="this.style.display=\'none\'" /><span>{p_name}</span></div>', unsafe_allow_html=True)

                        with rc[2]:
                            st.markdown(f'<div class="tcell tcell-name {dim_cls}">{u_name}</div>', unsafe_allow_html=True)

                        with rc[3]:
                            st.markdown(f'<div class="tcell tcell-email {dim_cls}">{u_email}</div>', unsafe_allow_html=True)

                        with rc[4]:
                            st.markdown(f'<div class="tcell {dim_cls}">{last_label}</div>', unsafe_allow_html=True)

                        with rc[5]:
                            st.markdown(f'<div class="tcell tcell-cost {dim_cls}">{cost_label}</div>', unsafe_allow_html=True)

                        with rc[6]:
                            st.markdown(f'<div class="ai-col {dim_cls}">{confidence_pill_html(u_conf)}</div>', unsafe_allow_html=True)

                        with rc[7]:
                            current_idx = status_options.index(u_status.capitalize()) if u_status.capitalize() in status_options else 0
                            new_val = st.selectbox("s", status_options, index=current_idx, key=f"rs_{org_key}_{global_i}", label_visibility="collapsed")
                            if new_val.lower() != u_status:
                                changed_keys.append((akey, new_val.lower(), u.get("cost", 0)))

                        with rc[8]:
                            if st.button("Notify", key=f"snd_{org_key}_{global_i}", use_container_width=True):
                                st.toast(f"Notification sent to {u_name} asking about {p_name} access")

                        # Row divider
                        st.markdown('<div style="border-bottom:1px solid #1E293B;margin:0 0 0.1rem 0;"></div>', unsafe_allow_html=True)

                    # ── Apply any status changes from selectboxes ─
                    if changed_keys:
                        for akey, new_status, cost in changed_keys:
                            actions[akey] = {
                                "status": new_status,
                                "reason": "",
                                "changed_at": datetime.utcnow().isoformat(),
                                "cost_at_action": cost,
                            }
                            try:
                                parts = akey.split("::")
                                log_event("console", "status_changed", auth.get_current_user().get("email", ""), {
                                    "user": parts[2] if len(parts) > 2 else akey,
                                    "platform": parts[1] if len(parts) > 1 else "",
                                    "new_status": new_status,
                                })
                            except Exception:
                                pass
                        save_user_actions(actions)
                        st.rerun()

                    # ── Bulk action bar (for selected users) ─────
                    selected_indices = [i for i in range(len(filtered)) if st.session_state.get(f"cb_{org_key}_{i}", False)]
                    selected_count = len(selected_indices)

                    if selected_count > 0:
                        st.markdown(f"""
                        <div style="background:rgba(2,128,144,0.06);border:1px solid rgba(2,128,144,0.2);border-radius:8px;padding:0.45rem 0.8rem;margin:0.15rem 0 0.2rem 0;">
                            <span style="font-size:0.74rem;color:#028090;font-weight:600;">{selected_count} user(s) selected</span>
                        </div>
                        """, unsafe_allow_html=True)

                        ba1, ba2, ba3, ba4, ba5 = st.columns([2, 1.5, 1.5, 1.5, 2.5])
                        with ba1:
                            bulk_status = st.selectbox("Set to", ["Pending", "Deactivated", "Kept"], key=f"bst_{org_key}", label_visibility="collapsed")
                        with ba2:
                            if st.button("Apply", key=f"bapply_{org_key}", use_container_width=True, type="primary"):
                                for idx in selected_indices:
                                    u, p_name, u_status, akey, _conf = filtered[idx]
                                    actions[akey] = {
                                        "status": bulk_status.lower(),
                                        "reason": "Bulk action",
                                        "changed_at": datetime.utcnow().isoformat(),
                                        "cost_at_action": u.get("cost", 0),
                                    }
                                save_user_actions(actions)
                                try:
                                    log_event("console", "bulk_status_changed", auth.get_current_user().get("email", ""), {
                                        "count": len(selected_indices),
                                        "new_status": bulk_status.lower(),
                                    })
                                except Exception:
                                    pass
                                st.rerun()
                        with ba3:
                            notify_clicked = st.button(f"Notify ({selected_count})", key=f"bnotify_{org_key}", use_container_width=True)
                        if notify_clicked:
                            for idx in selected_indices:
                                u, p_name, u_status, akey, _conf = filtered[idx]
                                st.toast(f"Teams notification sent to {u.get('name', '?')} ({p_name})")
                            try:
                                log_event("console", "teams_notification_sent", auth.get_current_user().get("email", ""), {
                                    "count": selected_count,
                                })
                            except Exception:
                                pass
                            with ba4:
                                st.markdown(f'<div style="font-size:0.72rem;color:#10B981;margin-top:-11px;">✓ Sent {selected_count} notification(s)</div>', unsafe_allow_html=True)

                    # Footer stats
                    st.markdown(f'<div style="font-size:0.74rem;color:#475569;padding:0.15rem 0;">Showing {len(filtered)} of {len(all_users)} users · {pending_count} pending</div>', unsafe_allow_html=True)

    # ── Scan History Trend Chart ──────────────────────────────────
    history = load_scan_history()
    if len(history) >= 2:
        show_trend_chart(history)

    # Footer with scan timestamp
    st.markdown(f'<div style="text-align:center;margin-top:1rem;font-size:0.72rem;color:#475569;">Last scan: {run_label}</div>', unsafe_allow_html=True)

    # ── Auto-poll: runs AFTER all content renders (no flash) ──────
    if is_scanning:
        _time.sleep(2.5)
        st.rerun()


def show_trend_chart(history):
    """Render the scan history trend chart with time filter."""
    try:
        import plotly.graph_objects as go
    except ImportError:
        st.markdown('<div style="font-size:0.8rem;color:#64748B;padding:0.5rem 0;">Install plotly for trend charts: pip install plotly</div>', unsafe_allow_html=True)
        return

    st.markdown('<div class="sl">Scan History</div>', unsafe_allow_html=True)

    # ── Time filter ───────────────────────────────────────────────
    filter_options = ["Last 7 days", "Last 30 days", "Last quarter", "Last year", "All time"]
    fcol1, fcol2 = st.columns([3, 1])
    with fcol2:
        time_filter = st.selectbox("Period", filter_options, index=4, label_visibility="collapsed", key="trend_filter")

    # ── Filter history by time ────────────────────────────────────
    now = datetime.utcnow()
    if time_filter == "Last 7 days":
        cutoff = now - timedelta(days=7)
    elif time_filter == "Last 30 days":
        cutoff = now - timedelta(days=30)
    elif time_filter == "Last quarter":
        cutoff = now - timedelta(days=90)
    elif time_filter == "Last year":
        cutoff = now - timedelta(days=365)
    else:
        cutoff = None

    filtered = []
    for entry in history:
        try:
            dt = datetime.fromisoformat(entry["date"].rstrip("Z"))
            if cutoff is None or dt >= cutoff:
                entry["_dt"] = dt
                filtered.append(entry)
        except (ValueError, KeyError):
            continue

    if len(filtered) < 2:
        st.markdown('<div style="text-align:center;font-size:0.8rem;color:#64748B;padding:1rem 0;">Not enough data points for selected period. Run more scans to see trends.</div>', unsafe_allow_html=True)
        return

    # ── Prepare data ──────────────────────────────────────────────
    dates = [e["_dt"] for e in filtered]
    savings = [e.get("total_monthly_savings", 0) for e in filtered]
    users = [e.get("total_inactive_users", 0) for e in filtered]

    # Date labels
    if (dates[-1] - dates[0]).days > 60:
        date_labels = [d.strftime("%b %d") for d in dates]
    else:
        date_labels = [d.strftime("%b %d %H:%M") for d in dates]

    # ── Build plotly chart ────────────────────────────────────────
    fig = go.Figure()

    # Monthly savings line
    fig.add_trace(go.Scatter(
        x=date_labels, y=savings,
        name="Monthly Savings",
        line=dict(color="#10B981", width=2.5),
        fill="tozeroy",
        fillcolor="rgba(16,185,129,0.08)",
        hovertemplate="$%{y:,.2f}/mo<extra></extra>",
    ))

    # Inactive users line (secondary y-axis)
    fig.add_trace(go.Scatter(
        x=date_labels, y=users,
        name="Inactive Users",
        line=dict(color="#028090", width=2, dash="dot"),
        yaxis="y2",
        hovertemplate="%{y} users<extra></extra>",
    ))

    fig.update_layout(
        height=260,
        margin=dict(l=0, r=0, t=10, b=0),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family="Segoe UI, sans-serif", size=11, color="#94A3B8"),
        legend=dict(
            orientation="h", yanchor="top", y=1.15, xanchor="left", x=0,
            font=dict(size=11, color="#94A3B8"),
            bgcolor="rgba(0,0,0,0)",
        ),
        hovermode="x unified",
        yaxis=dict(
            title=None,
            gridcolor="rgba(42,53,72,0.5)",
            tickprefix="$", tickformat=",",
            zeroline=False,
            side="left",
        ),
        yaxis2=dict(
            title=None,
            overlaying="y", side="right",
            gridcolor="rgba(0,0,0,0)",
            zeroline=False,
        ),
        xaxis=dict(
            gridcolor="rgba(42,53,72,0.3)",
            showgrid=False,
        ),
    )

    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

    # ── Summary stats below chart ─────────────────────────────────
    first_savings = filtered[0].get("total_monthly_savings", 0)
    last_savings = filtered[-1].get("total_monthly_savings", 0)
    delta = last_savings - first_savings
    delta_pct = (delta / first_savings * 100) if first_savings > 0 else 0
    first_users = filtered[0].get("total_inactive_users", 0)
    last_users = filtered[-1].get("total_inactive_users", 0)
    user_delta = last_users - first_users

    # Direction indicators
    if delta > 0:
        savings_arrow = "↑"
        savings_color = "#EF4444"  # Red = savings going up means more waste
    elif delta < 0:
        savings_arrow = "↓"
        savings_color = "#10B981"  # Green = savings going down means optimization working
    else:
        savings_arrow = "→"
        savings_color = "#94A3B8"

    if user_delta > 0:
        user_arrow = "↑"
        user_color = "#EF4444"
    elif user_delta < 0:
        user_arrow = "↓"
        user_color = "#10B981"
    else:
        user_arrow = "→"
        user_color = "#94A3B8"

    st.markdown(f"""
    <div style="display:flex;gap:1.5rem;justify-content:center;margin:-0.5rem 0 0.75rem 0;">
        <div style="font-size:0.72rem;color:#94A3B8;">
            Savings trend: <span style="color:{savings_color};font-weight:600;">{savings_arrow} ${abs(delta):,.2f}/mo ({abs(delta_pct):.1f}%)</span>
        </div>
        <div style="font-size:0.72rem;color:#94A3B8;">
            Users trend: <span style="color:{user_color};font-weight:600;">{user_arrow} {abs(user_delta)} users</span>
        </div>
        <div style="font-size:0.72rem;color:#64748B;">
            {len(filtered)} scans in period
        </div>
    </div>
    """, unsafe_allow_html=True)


# ------------------------------------------------------------------
# Tokens
# ------------------------------------------------------------------
def show_tokens(user):
    st.markdown("""<div class="phdr"><div class="phdr-ey">Credential Management</div>
        <div class="phdr-t">Token Health & Rotation</div>
        <div class="phdr-d">Monitor integration health, rotate API credentials, and maintain scan continuity across all platforms.</div></div>""", unsafe_allow_html=True)

    # ── Token Health Dashboard ────────────────────────────────────
    try:
        tokens = get_all_token_health()
    except Exception:
        tokens = []

    if tokens:
        healthy = sum(1 for t in tokens if t["status"] == "healthy")
        expiring = sum(1 for t in tokens if t["status"] == "expiring")
        expired = sum(1 for t in tokens if t["status"] in ("expired", "missing"))
        total = len(tokens)

        # Health score: 100 = all healthy, -20 per expiring, -40 per expired
        score = max(0, int(100 - (expiring * 20) - (expired * 40)))

        if expired > 0:
            badge_cls, badge_txt = "th-badge-crit", "Action Required"
        elif expiring > 0:
            badge_cls, badge_txt = "th-badge-warn", "Needs Attention"
        else:
            badge_cls, badge_txt = "th-badge-ok", "All Healthy"

        # Score ring color
        if score >= 70:
            ring_color = "#10B981"
        elif score >= 40:
            ring_color = "#F59E0B"
        else:
            ring_color = "#EF4444"

        # SVG ring math: circumference = 2 * pi * 22 = 138.23, offset = circ * (1 - score/100)
        circ = 138.23
        offset = circ * (1 - score / 100)

        st.markdown(f"""
        <div class="th-strip">
            <div class="th-strip-left">
                <div style="position:relative;width:52px;height:52px;">
                    <svg viewBox="0 0 48 48" style="transform:rotate(-90deg);width:52px;height:52px;">
                        <circle cx="24" cy="24" r="22" fill="none" stroke="#2A3548" stroke-width="4.5" />
                        <circle cx="24" cy="24" r="22" fill="none" stroke="{ring_color}" stroke-width="4.5" stroke-linecap="round" stroke-dasharray="{circ}" stroke-dashoffset="{offset}" />
                    </svg>
                    <span style="position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);font-family:'Segoe UI',monospace;font-size:0.92rem;font-weight:700;color:#F1F5F9;">{score}</span>
                </div>
                <div class="th-strip-info">
                    <h3>Integration Health</h3>
                    <p>{healthy} of {total} platform tokens are healthy{' - ' + str(expired) + ' overdue for rotation' if expired else ''}</p>
                </div>
            </div>
            <div class="th-strip-right">
                <div class="th-stat"><div class="th-stat-num thg">{healthy}</div><div class="th-stat-label">Healthy</div></div>
                <div class="th-div"></div>
                <div class="th-stat"><div class="th-stat-num tha">{expiring}</div><div class="th-stat-label">Due Soon</div></div>
                <div class="th-div"></div>
                <div class="th-stat"><div class="th-stat-num thr">{expired}</div><div class="th-stat-label">Overdue</div></div>
                <div class="th-div"></div>
                <span class="th-badge {badge_cls}"><span class="th-badge-dot"></span>{badge_txt}</span>
            </div>
        </div>
        """, unsafe_allow_html=True)

        # ── Action banners for urgent items ──
        for t in tokens:
            if t["status"] == "expired":
                st.markdown(f"""<div class="th-alert th-alert-red"><div class="th-alert-left"><span>🔴</span><span class="th-alert-text"><strong>{t['name']} rotation overdue</strong> - Token has exceeded the {ROTATION_POLICY_DAYS}-day rotation policy. Schedule rotation to maintain compliance.</span></div><span class="th-alert-text" style="font-size:0.66rem;color:#64748B;">Overdue by {abs(t['days_left'])}d</span></div>""", unsafe_allow_html=True)
            elif t["status"] == "missing":
                st.markdown(f"""<div class="th-alert th-alert-red"><div class="th-alert-left"><span>🔴</span><span class="th-alert-text"><strong>{t['name']} token not configured</strong> - Set up credentials to enable scanning.</span></div></div>""", unsafe_allow_html=True)
        for t in tokens:
            if t["status"] == "expiring":
                st.markdown(f"""<div class="th-alert th-alert-amber"><div class="th-alert-left"><span>🟡</span><span class="th-alert-text"><strong>{t['name']} rotation due in {t['days_left']}d</strong> - Approaching the {ROTATION_POLICY_DAYS}-day rotation policy deadline.</span></div></div>""", unsafe_allow_html=True)

        # ── Token Cards Grid ──
        st.markdown('<div class="sl" style="margin-top:0.75rem;">Platform Tokens</div>', unsafe_allow_html=True)

        LOGO_OVERRIDES = {"Atlassian": "atlassian", "PagerDuty": "pagerduty", "GitHub": "github", "GitLab": "gitlab"}

        # Render cards in rows of 4 using st.columns
        for row_start in range(0, len(tokens), 4):
            row_tokens = tokens[row_start:row_start + 4]
            cols = st.columns(4)
            for ci, t in enumerate(row_tokens):
                # Derive icon from system name (e.g. "PagerDuty [Xolv]" -> "pagerduty")
                base_name = t["name"].split("[")[0].split("(")[0].strip()
                icon_name = LOGO_OVERRIDES.get(base_name, base_name.lower())
                icon_url = f"https://cdn.jsdelivr.net/gh/homarr-labs/dashboard-icons/png/{icon_name}.png"

                if t["status"] == "healthy":
                    cls, dot, val_cls, status_label = "th-card-ok", "th-card-dot-ok", "th-card-val-ok", "Healthy"
                    bar_cls, bar_color = "th-bar-fill-ok", "#10B981"
                elif t["status"] == "expiring":
                    cls, dot, val_cls = "th-card-warn", "th-card-dot-warn", "th-card-val-warn"
                    status_label = "Due in " + str(t["days_left"]) + "d"
                    bar_cls, bar_color = "th-bar-fill-warn", "#F59E0B"
                elif t["status"] == "expired":
                    cls, dot, val_cls = "th-card-crit", "th-card-dot-crit", "th-card-val-crit"
                    status_label = "Overdue by " + str(abs(t["days_left"])) + "d"
                    bar_cls, bar_color = "th-bar-fill-crit", "#EF4444"
                else:
                    cls, dot, val_cls, status_label = "th-card-crit", "th-card-dot-crit", "th-card-val-crit", "Not Configured"
                    bar_cls, bar_color = "th-bar-fill-crit", "#EF4444"

                rotated_label = t.get("last_rotated") or "-"
                expires_label = t.get("expires") or "-"
                pct = t.get("pct_elapsed", 100)
                pct_label = str(pct) + "% elapsed" if t["status"] != "expired" else "Overdue"
                updated_by = t.get("rotated_by", "-")
                exp_val_cls = val_cls if t["status"] != "healthy" else ""

                with cols[ci]:
                    st.markdown(
                        '<div class="th-card ' + cls + '">'
                        + '<div class="th-card-hdr"><div class="th-card-plat"><img src="' + icon_url + '" onerror="this.style.display=&apos;none&apos;" /><span>' + t["name"] + '</span></div><div class="th-card-dot ' + dot + '"></div></div>'
                        + '<div class="th-card-row"><span class="th-card-lbl">Status</span><span class="th-card-val ' + val_cls + '">' + status_label + '</span></div>'
                        + '<div class="th-card-row"><span class="th-card-lbl">Last Rotated</span><span class="th-card-val">' + rotated_label + '</span></div>'
                        + '<div class="th-card-row"><span class="th-card-lbl">Rotation Due</span><span class="th-card-val ' + exp_val_cls + '">' + expires_label + '</span></div>'
                        + '<div class="th-card-row"><span class="th-card-lbl">Rotated By</span><span class="th-card-val">' + updated_by + '</span></div>'
                        + '<div class="th-bar-wrap"><div class="th-bar-top"><span class="th-card-lbl">Token lifetime</span><span class="th-card-lbl" style="color:' + bar_color + '">' + pct_label + '</span></div>'
                        + '<div class="th-bar-bg"><div class="th-bar-fill ' + bar_cls + '" style="width:' + str(pct) + '%"></div></div></div>'
                        + '</div>',
                        unsafe_allow_html=True
                    )

        st.markdown("<div style='height:1.25rem'></div>", unsafe_allow_html=True)

    # ── Rotate Credentials Form ───────────────────────────────────
    st.markdown('<div class="sl" style="margin-top:0.25rem;">Rotate Credentials</div>', unsafe_allow_html=True)

    accessible = {k: s for k, s in config.SYSTEMS.items() if auth.can_manage_system(k)}
    if not accessible:
        st.info("No systems available.")
        return

    st.markdown('<div class="sl">System</div>', unsafe_allow_html=True)
    sk = st.selectbox("System", list(accessible.keys()), format_func=lambda k: accessible[k]["name"], label_visibility="collapsed")
    system = accessible[sk]
    meta = storage.get_token_metadata(sk)
    rot = get_rotation_entry(sk)
    # Determine active status: has rotation metadata or legacy storage metadata, and not explicitly removed
    is_removed = rot.get("removed", False) if rot else False
    is_active = bool((meta or rot) and not is_removed)
    pc, pl = ("pill-ok", "Active") if is_active else ("pill-no", "Not Configured")

    # Build countdown pill HTML if rotation tracked
    timer_html = ""
    if is_active:
        if rot and rot.get("created_on"):
            rotated_dt = datetime.fromisoformat(rot["created_on"])
        elif meta:
            rotated_dt = datetime.fromisoformat(meta["updated_at"])
        else:
            rotated_dt = datetime.utcnow()
        now_dt = datetime.now(rotated_dt.tzinfo) if rotated_dt.tzinfo else datetime.utcnow()
        days_since = (now_dt - rotated_dt).days
        days_left = ROTATION_POLICY_DAYS - days_since
        if days_left <= 0:
            timer_color = "#EF4444"
            timer_bg = "rgba(239,68,68,0.1)"
            timer_border = "rgba(239,68,68,0.25)"
            timer_text = f"Overdue by {abs(days_left)}d"
        elif days_left <= 10:
            timer_color = "#EF4444"
            timer_bg = "rgba(239,68,68,0.1)"
            timer_border = "rgba(239,68,68,0.25)"
            timer_text = f"{days_left}d remaining"
        elif days_left <= 30:
            timer_color = "#F59E0B"
            timer_bg = "rgba(245,158,11,0.1)"
            timer_border = "rgba(245,158,11,0.25)"
            timer_text = f"{days_left}d remaining"
        else:
            timer_color = "#10B981"
            timer_bg = "rgba(16,185,129,0.1)"
            timer_border = "rgba(16,185,129,0.25)"
            timer_text = f"{days_left}d remaining"
        timer_html = f'<span style="display:inline-flex;align-items:center;gap:5px;background:{timer_bg};border:1px solid {timer_border};border-radius:100px;padding:2px 10px 2px 7px;font-size:0.58rem;font-weight:700;color:{timer_color};letter-spacing:0.03em;margin-left:8px;vertical-align:middle;"><span style="width:5px;height:5px;border-radius:50%;background:{timer_color};"></span>{timer_text}</span>'

    # Build card metadata
    rotated_by_display = rot.get("rotated_by", "-") if rot else (meta.get("updated_by", "Unknown") if meta else "-")
    if rot and rot.get("created_on"):
        created_dt = datetime.fromisoformat(rot["created_on"])
        rotated_date_display = created_dt.strftime("%b %d, %Y at %H:%M UTC")
    elif meta:
        rotated_date_display = datetime.fromisoformat(meta["updated_at"]).strftime("%b %d, %Y at %H:%M UTC")
    else:
        rotated_date_display = "-"

    card = f'<div class="card"><div class="sysh"><div><div class="sysn">{system["name"]} {timer_html}</div><div class="sysd">{system["description"]}</div></div><div style="display:flex;align-items:center;gap:8px;"><div class="pill {pc}"><span class="pdot"></span>{pl}</div></div></div>'
    if is_active:
        card += f'<div class="mets"><div><div class="ml">Rotated by</div><div class="mv">{rotated_by_display}</div></div><div><div class="ml">Token created</div><div class="mv">{rotated_date_display}</div></div><div><div class="ml">Rotation policy</div><div class="mv">{ROTATION_POLICY_DAYS}-day cycle</div></div></div>'
    card += "</div>"

    if is_active:
        del_key = f"del_state_{sk}"
        card_col, trash_col = st.columns([18, 1.5])
        with card_col:
            st.markdown(card, unsafe_allow_html=True)
        with trash_col:
            st.markdown('<div style="height:1.1rem"></div>', unsafe_allow_html=True)
            if st.button("✕", key=f"trash_{sk}", help="Remove rotation tracking"):
                st.session_state[del_key] = not st.session_state.get(del_key, False)

        if st.session_state.get(del_key, False):
            warn_col, _ = st.columns([20, 1])
            with warn_col:
                st.markdown(
                    '<div style="background:rgba(239,68,68,0.04);border:1px solid rgba(239,68,68,0.15);border-radius:12px;padding:0.55rem 0.85rem;margin:-0.3rem 0 0.4rem 0;">'
                    + '<span style="font-size:0.74rem;color:#F59E0B;">&#9888; This will remove rotation tracking for ' + system["name"] + ' from the console. It does not revoke the API token or stop active scans - you must update the system configuration files separately.</span>'
                    + '</div>',
                    unsafe_allow_html=True
                )
            btn_col1, btn_col2, btn_col3 = st.columns([8.5, 3, 8.5])
            with btn_col2:
                st.markdown("""<style>
                div[data-testid="stHorizontalBlock"]:has(.confirm-del-marker) button[data-testid="stBaseButton-secondary"] {
                    color: #EF4444 !important;
                    border: 1.5px solid rgba(239,68,68,0.3) !important;
                    background: transparent !important;
                    white-space: nowrap !important;
                }
                div[data-testid="stHorizontalBlock"]:has(.confirm-del-marker) button[data-testid="stBaseButton-secondary"]:hover {
                    background: rgba(239,68,68,0.1) !important;
                    border-color: rgba(239,68,68,0.5) !important;
                }
                </style><div class="confirm-del-marker" style="display:none"></div>""", unsafe_allow_html=True)
                if st.button("Confirm removal", key=f"del_confirm_{sk}", use_container_width=True):
                    try:
                        rmeta = load_rotation_metadata()
                        if sk in rmeta:
                            rmeta[sk]["removed"] = True
                        else:
                            rmeta[sk] = {"removed": True}
                        ROTATION_META_FILE.write_text(json.dumps(rmeta, indent=2))
                        try:
                            log_event(sk, "integration_removed", user["email"], {"system_name": system["name"]})
                        except Exception:
                            pass
                        st.session_state[del_key] = False
                        st.success(f"**{system['name']}** removed from console.")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Failed to remove: {e}")
    else:
        st.markdown(card, unsafe_allow_html=True)

    # ── Log Rotation Form ──────────────────────────────────────────
    st.markdown('<div class="sl">Log Rotation</div>', unsafe_allow_html=True)

    known_users = get_known_users()
    current_email = user.get("email", "")
    # Build user list: current user first, then others
    user_options = [current_email] + [u for u in known_users if u != current_email] if current_email else known_users

    rc1, rc2 = st.columns(2)
    with rc1:
        rotated_by = st.selectbox("Rotated by", user_options, key=f"rot_by_{sk}", help="Who performed the rotation in the platform")
    with rc2:
        created_on = st.date_input("Token created on", value=datetime.utcnow().date(), key=f"rot_date_{sk}", help="When the token was created in the platform")

    # Disable Save if values match last saved state
    last_save_key = f"last_save_{sk}"
    last_save = st.session_state.get(last_save_key, {})
    form_unchanged = (last_save.get("rotated_by") == rotated_by and last_save.get("created_on") == str(created_on))

    st.markdown("<div style='height:0.3rem'></div>", unsafe_allow_html=True)
    if st.button("Save", type="primary", use_container_width=True, key=f"save_rot_{sk}", disabled=form_unchanged):
        save_rotation_entry(sk, rotated_by, datetime.combine(created_on, datetime.min.time()).isoformat(), current_email)
        try:
            log_event(sk, "rotation_logged", current_email, {
                "system_name": system["name"],
                "rotated_by": rotated_by,
                "created_on": str(created_on),
            })
        except Exception:
            pass
        st.session_state[last_save_key] = {"rotated_by": rotated_by, "created_on": str(created_on)}
        st.success(f"Rotation for **{system['name']}** saved.")
        st.rerun()


# ------------------------------------------------------------------
# Audit
# ------------------------------------------------------------------
def show_audit():
    st.markdown("""<div class="phdr"><div class="phdr-ey">Activity History</div>
        <div class="phdr-t">Audit Log</div>
        <div class="phdr-d">A record of all console activity - scans, status changes, token rotations, and notifications.</div></div>""", unsafe_allow_html=True)

    sf = st.selectbox("Filter", ["All Events"] + ["console"] + list(config.SYSTEMS.keys()), format_func=lambda k: "Console" if k == "console" else (config.SYSTEMS[k]["name"] if k in config.SYSTEMS else k))
    logs = get_audit_log(system_key=None if sf == "All Events" else sf, limit=500)

    if not logs:
        st.markdown('<div class="empty"><div class="empty-t">No activity recorded yet</div><div class="empty-d">Actions will appear here automatically.</div></div>', unsafe_allow_html=True)
        return

    # Pagination
    AUDIT_PER_PAGE = 15
    total_pages = max(1, -(-len(logs) // AUDIT_PER_PAGE))  # ceil division
    if "audit_page" not in st.session_state:
        st.session_state.audit_page = 0
    # Reset page if filter changes
    if st.session_state.get("_audit_filter") != sf:
        st.session_state.audit_page = 0
        st.session_state["_audit_filter"] = sf
    page = st.session_state.audit_page
    page_logs = logs[page * AUDIT_PER_PAGE : (page + 1) * AUDIT_PER_PAGE]

    st.markdown(f'<div style="font-size:0.74rem;color:#64748B;margin-bottom:0.4rem;">{len(logs)} event(s) - page {page + 1} of {total_pages}</div>', unsafe_allow_html=True)

    for e in page_logs:
        sn = config.SYSTEMS.get(e["system_key"], {}).get("name", e["system_key"])
        ts_dt = datetime.fromisoformat(e["timestamp"])
        ts_date = ts_dt.strftime("%b %d, %Y")
        ts_time = ts_dt.strftime("%H:%M UTC")
        act = e["action"].replace("_", " ").title()
        details = e.get("details", {})
        source = sn if e["system_key"] != "console" else "Console"

        # Build detail line based on event type
        detail_html = ""
        if e["action"] == "status_changed" and details:
            detail_html = f'<div class="a-detail">{details.get("user","")} - {details.get("new_status","").title()}</div>'
        elif e["action"] == "bulk_status_changed" and details:
            detail_html = f'<div class="a-detail">{details.get("count","")} user(s) set to {details.get("new_status","").title()}</div>'
        elif e["action"] == "rotation_logged" and details:
            detail_html = f'<div class="a-detail">Rotated by {details.get("rotated_by","")} - created {details.get("created_on","")}</div>'
        elif e["action"] == "teams_notification_sent" and details:
            detail_html = f'<div class="a-detail">Sent to {details.get("count","")} user(s)</div>'
        elif e["action"] == "integration_removed" and details:
            detail_html = f'<div class="a-detail">{details.get("system_name","")}</div>'

        st.markdown(
            f'<div class="ar"><div class="adot"></div>'
            f'<div><div class="at">{act}</div>'
            f'<span class="a-source">{source}</span>'
            f'{detail_html}'
            f'<div class="as">{e["user_email"]}</div></div>'
            f'<div class="ats">{ts_date}<br>{ts_time}</div></div>',
            unsafe_allow_html=True
        )

    # Page controls
    if total_pages > 1:
        pc1, pc2, pc3 = st.columns([1, 2, 1])
        with pc1:
            if st.button("← Previous", disabled=page == 0, key="audit_prev", use_container_width=True):
                st.session_state.audit_page = page - 1
                st.rerun()
        with pc3:
            if st.button("Next →", disabled=page >= total_pages - 1, key="audit_next", use_container_width=True):
                st.session_state.audit_page = page + 1
                st.rerun()


# ------------------------------------------------------------------
# Login - polished card (container styled by CONTAINER_CSS above)
# ------------------------------------------------------------------
def show_login_screen_with_dev():
    """Login page - centered card layout."""
    logo = f'<img src="data:image/png;base64,{LOGO_WHITE}" alt="Xolv" style="height:46px;" />' if LOGO_WHITE else '<span style="font-size:1.5rem;font-weight:800;color:#F1F5F9;letter-spacing:0.05em;">XOLV</span>'

    # AI sparkle - simple inline SVG (no <defs> to avoid Streamlit parse issues)
    ai_star = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" style="display:inline-block;vertical-align:middle;"><path d="M12 2L14 9L21 9L15.5 13.5L17.5 21L12 16.5L6.5 21L8.5 13.5L3 9L10 9Z" fill="url(#aig)"/><defs><linearGradient id="aig" x1="0" y1="0" x2="1" y2="1"><stop offset="0%" stop-color="#00C9DB"/><stop offset="100%" stop-color="#8B5CF6"/></linearGradient></defs></svg>'

    # Check if Azure AD is properly configured
    azure_configured = bool(
        config.AZURE_CLIENT_ID and config.AZURE_TENANT_ID and config.AZURE_CLIENT_SECRET
        and "your" not in config.AZURE_TENANT_ID.lower()
        and "YOUR" not in config.AZURE_CLIENT_ID
        and len(config.AZURE_CLIENT_ID) > 10
    )

    # Handle OAuth callback
    query_params = st.query_params
    if "code" in query_params and azure_configured:
        user = auth.complete_login(dict(query_params))
        if user:
            st.session_state["user"] = user
            st.query_params.clear()
            st.rerun()

    ms_logo = '<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 21 21"><rect x="1" y="1" width="9" height="9" fill="#f25022"/><rect x="11" y="1" width="9" height="9" fill="#7fba00"/><rect x="1" y="11" width="9" height="9" fill="#00a4ef"/><rect x="11" y="11" width="9" height="9" fill="#ffb900"/></svg>'
    if azure_configured:
        login_url = auth.get_login_url()
        disabled_css = ""
    else:
        login_url = "#"
        disabled_css = "opacity:0.45;pointer-events:none;"

    # Glowing background orbs
    st.markdown("""
        <div class="login-orb login-orb-teal"></div>
        <div class="login-orb login-orb-purple"></div>
    """, unsafe_allow_html=True)

    # Use st.columns to center content (CSS can't override layout="wide" inline styles)
    _left, center, _right = st.columns([1, 1.2, 1])

    with center:
        # ── Full card as single HTML block ────────────────────────
        st.markdown(f"""
            <div class="lcard">
            <div class="lcard-bar"></div>
            <div class="lcard-logo">{logo}</div>
            <div class="lcard-title">License Scanner <span>Console</span></div>
            <div class="lcard-sub">Manage licenses, rotate credentials, and optimize SaaS spend.</div>
            <div class="lcard-pills">
                <div class="lcard-pill">
                    <span class="lcard-pill-dot" style="background:#10B981;"></span>
                    <span class="lcard-pill-text">Scan &amp; Detect</span>
                </div>
                <div class="lcard-pill">
                    <span class="lcard-pill-dot" style="background:#028090;"></span>
                    <span class="lcard-pill-text">Token Health</span>
                </div>
                <div class="lcard-pill lcard-pill-ai">
                    {ai_star}
                    <span class="lcard-pill-text">AI Insights</span>
                </div>
                <div class="lcard-pill">
                    <span class="lcard-pill-dot" style="background:#F59E0B;"></span>
                    <span class="lcard-pill-text">Audit Trail</span>
                </div>
            </div>
            <div class="lcard-div"></div>
            </div>
        """, unsafe_allow_html=True)

        # ── Access code + Sign in (Streamlit widgets) ─────────────
        access_code = st.text_input("Access code", placeholder="Enter your access code", label_visibility="collapsed", type="password")
        if st.button("Sign in", use_container_width=True, type="primary"):
            if access_code and config.ACCESS_CODE and access_code == config.ACCESS_CODE:
                st.session_state["user"] = {"name": "User", "email": "user@company.com", "groups": []}
                st.rerun()
            elif not access_code:
                st.error("Please enter an access code.")
            else:
                st.error("Invalid access code.")

        # ── "or" divider + Microsoft SSO ──────────────────────────
        st.markdown(f"""
            <div class="lcard-or">
                <div class="lcard-or-line"></div>
                <span class="lcard-or-text">or</span>
                <div class="lcard-or-line"></div>
            </div>
            <div style="text-align:center; margin-top:0.5rem;">
                <a href="{login_url}" class="lcard-sso" style="{disabled_css}">
                    {ms_logo} Sign in with Microsoft
                </a>
            </div>
        """, unsafe_allow_html=True)

    st.markdown('<div class="lcard-footer">Protected by Xolv Technology Solutions</div>', unsafe_allow_html=True)

# ------------------------------------------------------------------
# Entry - uses IS_LOGGED_IN checked before CSS was built
# ------------------------------------------------------------------
if IS_LOGGED_IN:
    show_dashboard()
else:
    show_login_screen_with_dev()
