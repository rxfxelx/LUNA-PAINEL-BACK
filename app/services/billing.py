from __future__ import annotations

import hashlib
import hmac
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from app.pg import get_pool  # usa o pool do psycopg_pool

# Número de dias do período de testes gratuito.  Ajustado para 14 dias por padrão.
TRIAL_DAYS = int(os.getenv("TRIAL_DAYS") or 14)
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
    Garante que exista um registro e que o trial esteja iniciado (idempotente).
    Retorna um snapshot básico do registro.
    """
    trial_ends = _utcnow() + timedelta(days=TRIAL_DAYS)

    with get_pool().connection() as conn:
        with conn.cursor() as cur:
            # UPSERT idempotente
            cur.execute(
                """
                INSERT INTO billing_accounts
                    (billing_key, created_at, trial_started_at, trial_ends_at)
                VALUES
                    (%s, NOW(), NOW(), %s)
                ON CONFLICT (billing_key) DO NOTHING
                """,
                (billing_key, trial_ends),
            )

            # Lê o registro atualizado
            cur.execute(
                """
                SELECT trial_started_at, trial_ends_at, paid_until, plan, last_payment_status
                  FROM billing_accounts
                 WHERE billing_key = %s
                """,
                (billing_key,),
            )
            row = cur.fetchone()

    if not row:
        return {
            "trial_started_at": None,
            "trial_ends_at": None,
            "paid_until": None,
            "plan": None,
            "last_payment_status": None,
        }

    # dict_row permite acesso por chave; mantém compat se vier tupla
    def _get(r, k, i):
        try:
            return r[k]
        except Exception:
            return r[i]

    return {
        "trial_started_at": _get(row, "trial_started_at", 0),
        "trial_ends_at": _get(row, "trial_ends_at", 1),
        "paid_until": _get(row, "paid_until", 2),
        "plan": _get(row, "plan", 3),
        "last_payment_status": _get(row, "last_payment_status", 4),
    }


def get_status(billing_key: str) -> Dict[str, Any]:
    """
    Retorna o status atual de billing, incluindo flags de ativo, dias restantes e se requer pagamento.
    """
    with get_pool().connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT trial_started_at, trial_ends_at, paid_until, plan, last_payment_status
                  FROM billing_accounts
                 WHERE billing_key = %s
                """,
                (billing_key,),
            )
            row = cur.fetchone()

    if not row:
        return {
            "exists": False,
            "active": False,
            "trial_started_at": None,
            "trial_ends_at": None,
            "paid_until": None,
            "days_left": 0,
            "plan": None,
            "last_payment_status": None,
            "require_payment": False,
        }

    def _get(r, k, i):
        try:
            return r[k]
        except Exception:
            return r[i]

    trial_started = _get(row, "trial_started_at", 0)
    trial_ends    = _get(row, "trial_ends_at", 1)
    paid_until    = _get(row, "paid_until", 2)
    plan          = _get(row, "plan", 3)
    last_status   = _get(row, "last_payment_status", 4)

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
        "require_payment": (not active) and bool(trial_started),
    }


def mark_paid(
    billing_key: str,
    days: int = 30,
    plan: Optional[str] = None,
    status: str = "paid",
) -> None:
    """
    Avança/define paid_until por N dias a partir do maior entre agora e o paid_until atual.
    Atualiza também plan e last_payment_status.
    """
    now = _utcnow()
    add_days = max(1, int(days))

    with get_pool().connection() as conn:
        with conn.cursor() as cur:
            # garante existência do registro (idempotente)
            cur.execute(
                """
                INSERT INTO billing_accounts (billing_key, created_at, trial_started_at, trial_ends_at)
                VALUES (%s, NOW(), NOW(), %s)
                ON CONFLICT (billing_key) DO NOTHING
                """,
                (billing_key, now + timedelta(days=TRIAL_DAYS)),
            )

            cur.execute(
                "SELECT paid_until FROM billing_accounts WHERE billing_key = %s",
                (billing_key,),
            )
            row = cur.fetchone()
            paid_atual = None
            if row is not None:
                try:
                    paid_atual = row["paid_until"]
                except Exception:
                    paid_atual = row[0]

            base = paid_atual if (paid_atual and paid_atual > now) else now
            new_paid = base + timedelta(days=add_days)

            cur.execute(
                """
                UPDATE billing_accounts
                   SET paid_until = %s,
                       plan = COALESCE(%s, plan),
                       last_payment_status = %s
                 WHERE billing_key = %s
                """,
                (new_paid, plan, status, billing_key),
            )
