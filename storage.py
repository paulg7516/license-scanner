"""
Secure Storage - encrypted token persistence and audit logging.

Tokens are encrypted with Fernet (AES-128-CBC) before being stored in SQLite.
Every read/write is logged to the audit_log table.
"""

import sqlite3
import json
from datetime import datetime, timezone
from cryptography.fernet import Fernet
import config


def _get_cipher():
    """Return a Fernet cipher using the configured encryption key."""
    if not config.ENCRYPTION_KEY:
        raise RuntimeError(
            "ENCRYPTION_KEY is not set. Generate one with:\n"
            '  python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'
        )
    return Fernet(config.ENCRYPTION_KEY.encode())


def _get_db():
    """Return a SQLite connection (creates tables on first use)."""
    conn = sqlite3.connect(config.DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tokens (
            system_key   TEXT PRIMARY KEY,
            encrypted    BLOB NOT NULL,
            updated_by   TEXT NOT NULL,
            updated_at   TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS audit_log (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp  TEXT NOT NULL,
            user_email TEXT NOT NULL,
            system_key TEXT NOT NULL,
            action     TEXT NOT NULL,
            details    TEXT
        )
    """)
    conn.commit()
    return conn


# ------------------------------------------------------------------
# Token CRUD
# ------------------------------------------------------------------

def save_token(system_key: str, fields: dict, user_email: str) -> None:
    """Encrypt and save token fields for a system."""
    cipher = _get_cipher()
    encrypted = cipher.encrypt(json.dumps(fields).encode())
    now = datetime.now(timezone.utc).isoformat()

    db = _get_db()
    db.execute(
        """
        INSERT INTO tokens (system_key, encrypted, updated_by, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(system_key) DO UPDATE SET
            encrypted  = excluded.encrypted,
            updated_by = excluded.updated_by,
            updated_at = excluded.updated_at
        """,
        (system_key, encrypted, user_email, now),
    )
    _log(db, user_email, system_key, "TOKEN_ROTATED", "Token updated successfully")
    db.commit()
    db.close()


def get_token_metadata(system_key: str) -> dict | None:
    """Get non-secret metadata (who updated, when). Never returns the actual token."""
    db = _get_db()
    row = db.execute(
        "SELECT updated_by, updated_at FROM tokens WHERE system_key = ?",
        (system_key,),
    ).fetchone()
    db.close()
    if row:
        return {"updated_by": row["updated_by"], "updated_at": row["updated_at"]}
    return None


def get_decrypted_token(system_key: str) -> dict | None:
    """Decrypt and return token fields. Use sparingly (e.g., for validation)."""
    cipher = _get_cipher()
    db = _get_db()
    row = db.execute(
        "SELECT encrypted FROM tokens WHERE system_key = ?", (system_key,)
    ).fetchone()
    db.close()
    if row:
        return json.loads(cipher.decrypt(row["encrypted"]).decode())
    return None


# ------------------------------------------------------------------
# Audit Log
# ------------------------------------------------------------------

def _log(db, user_email: str, system_key: str, action: str, details: str = ""):
    """Write an entry to the audit log."""
    db.execute(
        "INSERT INTO audit_log (timestamp, user_email, system_key, action, details) VALUES (?, ?, ?, ?, ?)",
        (datetime.now(timezone.utc).isoformat(), user_email, system_key, action, details),
    )


def get_audit_log(system_key: str = None, limit: int = 50) -> list[dict]:
    """Retrieve recent audit log entries, optionally filtered by system."""
    db = _get_db()
    if system_key:
        rows = db.execute(
            "SELECT * FROM audit_log WHERE system_key = ? ORDER BY id DESC LIMIT ?",
            (system_key, limit),
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    db.close()
    return [dict(r) for r in rows]
