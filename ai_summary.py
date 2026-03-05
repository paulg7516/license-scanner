"""
AI Summary — generates executive summaries from scan data using Claude API.
"""

import json
from pathlib import Path
from datetime import datetime
import anthropic
from config import ANTHROPIC_API_KEY

SUMMARY_FILE = Path(__file__).parent / "ai_summary.json"
SCAN_FILE = Path(__file__).parent / "scan_results.json"

SYSTEM_PROMPT = """You are a concise business analyst. Given SaaS license scan data, produce a JSON object with this exact structure:

{
  "headline": "One punchy sentence summarizing the scan (e.g. 'Your organization has $55,943 in recoverable annual SaaS spend across 113 inactive licenses.')",
  "findings": [
    "Finding 1 — plain text, one sentence, include numbers",
    "Finding 2",
    "Finding 3"
  ],
  "opportunities": [
    {"area": "Short label (e.g. Atlassian Cleanup)", "detail": "One sentence with dollar amounts", "savings": "$X,XXX/mo"},
    {"area": "Label", "detail": "Detail", "savings": "$X,XXX/mo"}
  ],
  "actions": [
    "Action 1 — concrete, specific, one sentence",
    "Action 2",
    "Action 3"
  ],
  "bottom_line": "One sentence strategic recommendation or next milestone — do NOT repeat the savings figure from the headline."
}

Rules:
- Return ONLY valid JSON, no markdown, no code fences, no extra text.
- Use plain text only — no markdown bold/italic/headers.
- Keep each string short (under 30 words).
- Use real dollar amounts from the data provided.
- 3 findings, 2-3 opportunities, 3 actions."""


def _build_prompt(scan_data: dict) -> str:
    """Build a prompt from scan results."""
    orgs_summary = []
    for org_name, org in scan_data.get("orgs", {}).items():
        platforms = []
        for p in org.get("platforms", []):
            platforms.append(
                f"  - {p['name']}: {p['inactive_users']} inactive users, ${p['monthly']:,.2f}/mo"
            )
        orgs_summary.append(
            f"{org_name}: {org['count']} inactive, ${org['monthly']:,.2f}/mo\n"
            + "\n".join(platforms)
        )

    return f"""Scan date: {scan_data.get('run_date', 'Unknown')}
Inactivity threshold: {scan_data.get('inactivity_days', 60)} days
Total inactive users: {scan_data.get('total_inactive_users', 0)}
Total platforms scanned: {scan_data.get('total_platforms_scanned', 0)}
Monthly savings potential: ${scan_data.get('total_monthly_savings', 0):,.2f}
Annual savings potential: ${scan_data.get('total_annual_savings', 0):,.2f}

Breakdown by organization:
{chr(10).join(orgs_summary)}"""


def _load_cached() -> dict | None:
    """Load cached summary if it exists and is fresh."""
    if not SUMMARY_FILE.exists():
        return None
    try:
        cached = json.loads(SUMMARY_FILE.read_text())
        # Check if scan data is newer than cached summary
        if SCAN_FILE.exists():
            scan_data = json.loads(SCAN_FILE.read_text())
            scan_date = scan_data.get("run_date", "")
            if cached.get("scan_date") != scan_date:
                return None  # Stale cache
        return cached
    except (json.JSONDecodeError, IOError):
        return None


def _save_cache(summary: dict, scan_date: str):
    """Cache the generated summary."""
    SUMMARY_FILE.write_text(json.dumps({
        "summary": summary,
        "scan_date": scan_date,
        "generated_at": datetime.now().isoformat(),
    }, indent=2))


def generate_summary(force: bool = False) -> dict:
    """Generate an executive summary. Returns cached version if fresh.

    Returns dict with keys: summary (dict), scan_date, generated_at, error (if any).
    """
    if not force:
        cached = _load_cached()
        if cached:
            return cached

    # Load scan data
    if not SCAN_FILE.exists():
        return {"error": "No scan results found. Run a scan first."}

    try:
        scan_data = json.loads(SCAN_FILE.read_text())
    except (json.JSONDecodeError, IOError):
        return {"error": "Could not read scan results."}

    if not ANTHROPIC_API_KEY:
        return {"error": "ANTHROPIC_API_KEY not configured. Add it to your .env file."}

    # Call Claude
    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        message = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": _build_prompt(scan_data)}],
        )
        raw = message.content[0].text.strip()
        # Strip code fences if present
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
            if raw.endswith("```"):
                raw = raw[:-3].strip()
        summary = json.loads(raw)
    except json.JSONDecodeError:
        return {"error": "AI returned invalid format. Try generating again."}
    except Exception as e:
        return {"error": f"AI generation failed: {str(e)}"}

    scan_date = scan_data.get("run_date", "")
    _save_cache(summary, scan_date)

    return {
        "summary": summary,
        "scan_date": scan_date,
        "generated_at": datetime.now().isoformat(),
    }
