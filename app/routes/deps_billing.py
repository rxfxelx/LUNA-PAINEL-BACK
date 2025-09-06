from __future__ import annotations

import os
from typing import Optional, Dict, Any

from fastapi import Depends, HTTPException
from app.auth import get_current_user
from app.services.billing import make_billing_key, get_status, ensure_trial


# ---------------------- helpers bypass ----------------------
def _env_list(name: str) -> list[str]:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return []
    return [x.strip() for x in raw.split(",") if x.strip()]

def _is_admin_bypass(user: Dict[str, Any]) -> bool:
    emails = set(x.lower() for x in _env_list("ADMIN_BYPASS_EMAILS"))
    hosts  = set(_env_list("ADMIN_BYPASS_HOSTS"))
    toks   = set(_env_list("ADMIN_BYPASS_TOKENS"))

    email = (user.get("email") or user.get("user_email") or "").lower().strip()
    host  = (user.get("host") or "").strip()
    token = (user.get("token") or user.get("instance_token") or "").strip()

    return (
        (email and email in emails) or
        (host and host in hosts) or
        (token and token in toks)
    )


async def require_active_tenant(user=Depends(get_current_user)) -> Dict[str, Any]:
    """
    Gate de billing para rotas operacionais.
    """
    # Admin bypass primeiro (não depende de billing)
    if _is_admin_bypass(user):
        return {
            "billing_key": None,
            "status": {"active": True, "plan": "admin", "admin_bypass": True},
            "user": user,
        }

    # Bypass global opcional (emergência)
    if os.getenv("DISABLE_BILLING", "0") == "1":
        return {
            "billing_key": "disabled",
            "status": {"active": True, "bypassed": True},
            "user": user,
        }

    token = (user.get("token") or user.get("instance_token") or "").strip()
    host = (user.get("host") or "").strip()
    iid: Optional[str] = user.get("instance_id")

    if not token or not host:
        raise HTTPException(status_code=401, detail="JWT inválido: sem token/host")

    # Cria a chave de cobrança do tenant
    try:
        bkey = make_billing_key(token, host, iid)
    except Exception as e:
        raise HTTPException(
            status_code=402,
            detail={"message": f"Erro ao gerar billing_key: {e}", "require_payment": True},
        )

    # Lê status atual
    try:
        st = get_status(bkey)
    except Exception as e:
        raise HTTPException(
            status_code=402,
            detail={"message": f"Billing indisponível (get_status): {e}", "require_payment": True},
        )

    # Se ainda não existir, tenta abrir trial e reconsulta
    if not st.get("exists"):
        try:
            ensure_trial(bkey)
        except Exception:
            pass
        try:
            st = get_status(bkey)
        except Exception as e:
            raise HTTPException(
                status_code=402,
                detail={"message": f"Billing indisponível (re-get_status): {e}", "require_payment": True},
            )

    if st.get("active"):
        return {"billing_key": bkey, "status": st, "user": user}

    # 402 Payment Required
    raise HTTPException(
        status_code=402,
        detail={
            "message": (
                "Sua avaliação terminou ou a assinatura está inativa. "
                "Acesse a aba Pagamentos para continuar."
            ),
            "require_payment": True,
            "days_left": int(st.get("days_left") or 0),
            "trial_ends_at": st.get("trial_ends_at"),
            "paid_until": st.get("paid_until"),
            "plan": st.get("plan"),
        },
    )


# -------------------------------------------------------------------
# Versão 'soft': não derruba por falhas inesperadas (exceto 401/402/403)
# -------------------------------------------------------------------
async def require_active_tenant_soft(
    user=Depends(get_current_user),
) -> Optional[Dict[str, Any]]:
    try:
        return await require_active_tenant(user)
    except HTTPException as e:
        if e.status_code in (401, 402, 403):
            raise
        return None
    except Exception:
        return None
