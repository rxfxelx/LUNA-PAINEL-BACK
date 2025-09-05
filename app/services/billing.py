# app/services/billing.py
from __future__ import annotations
from datetime import datetime, timedelta, timezone
import hashlib, hmac, os
from typing import Optional, Dict, Any

from app.pg import get_conn

TRIAL_DAYS = int(os.getenv("TRIAL_DAYS") or 7)
_SALT = (os.getenv("BILLING_SALT") or "luna").encode()

def _utcnow() -> datetime:
    return datetime.now(timezone.utc)

def make_billing_key(token: str, host: str, instance_id: Optional[str]) -> str:
    """
    Preferimos instance_id (UUID). Se não houver, usamos hash estável de host+token.
    """
    if instance_id:
        return f"iid:{instance_id}"
    raw = f"{host}|{token}".encode()
    digest = hmac.new(_SALT, raw, hashlib.sha256).hexdigest()
    return f"ht:{digest}"

def ensure_trial(billing_key: str) -> Dict[str, Any]:
    """
    Garante que exista um registro e que o trial esteja iniciado.
    """
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT id, trial_started_at, trial_ends_at, paid_until, plan, last_payment_status
            FROM billing_accounts
            WHERE billing_key=%s
        """, (billing_key,))
        row = cur.fetchone()

        if not row:
            trial_ends = _utcnow() + timedelta(days=TRIAL_DAYS)
            cur.execute("""
                INSERT INTO billing_accounts (billing_key, created_at, trial_started_at, trial_ends_at)
                VALUES (%s, NOW(), NOW(), %s)
                RETURNING id, trial_started_at, trial_ends_at, paid_until, plan, last_payment_status
            """, (billing_key, trial_ends))
            row = cur.fetchone()
            conn.commit()

        return {
            "trial_started_at": row[1],
            "trial_ends_at": row[2],
            "paid_until": row[3],
            "plan": row[4],
            "last_payment_status": row[5],
        }

def get_status(billing_key: str) -> Dict[str, Any]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT trial_started_at, trial_ends_at, paid_until, plan, last_payment_status
            FROM billing_accounts WHERE billing_key=%s
        """, (billing_key,))
        row = cur.fetchone()

    if not row:
        # Sem registro: nada ainda (front pode chamar /register-trial para começar)
        return {
            "exists": False,
            "active": False,
            "trial_started_at": None,
            "trial_ends_at": None,
            "paid_until": None,
            "days_left": 0,
            "plan": None,
            "require_payment": False,
        }

    trial_started, trial_ends, paid_until, plan, last_status = row
    now = _utcnow()

    active = False
    days_left = 0
    if paid_until and paid_until > now:
        active = True
        days_left = max(0, (paid_until - now).days)
    elif trial_ends and trial_ends > now:
        active = True
        days_left = max(0, (trial_ends - now).days)

    return {
        "exists": True,
        "active": active,
        "trial_started_at": trial_started,
        "trial_ends_at": trial_ends,
        "paid_until": paid_until,
        "days_left": days_left,
        "plan": plan,
        "last_payment_status": last_status,
        "require_payment": not active and bool(trial_started),
    }

def mark_paid(billing_key: str, days: int = 30, plan: Optional[str] = None, status: str = "paid") -> None:
    now = _utcnow()
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT paid_until FROM billing_accounts WHERE billing_key=%s", (billing_key,))
        row = cur.fetchone()
        base = row[0] if row and row[0] and row[0] > now else now
        new_paid = base + timedelta(days=max(1, days))
        cur.execute("""
            UPDATE billing_accounts
               SET paid_until=%s,
                   plan=COALESCE(%s, plan),
                   last_payment_status=%s
             WHERE billing_key=%s
        """, (new_paid, plan, status, billing_key))
        conn.commit()
