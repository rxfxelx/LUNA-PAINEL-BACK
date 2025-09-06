# app/routes/deps_billing.py
from __future__ import annotations

from fastapi import Depends, HTTPException
from app.auth import get_current_user
from app.services.billing import make_billing_key, get_status, ensure_trial


async def require_active_tenant(user=Depends(get_current_user)) -> dict:
    """
    Bloqueia rotas 'operacionais' se a assinatura não estiver ativa.
    Identifica o tenant a partir do JWT (token/host/instance_id), gera o billing_key
    e verifica o status (trial ou pago). Se não existir registro, inicia o trial.
    """
    token = (user.get("token") or user.get("instance_token") or "").strip()
    host = (user.get("host") or "").strip()
    iid = user.get("instance_id")

    if not token or not host:
        raise HTTPException(status_code=401, detail="JWT inválido: sem token/host")

    bkey = make_billing_key(token, host, iid)

    # lê status; se não existir registro ainda, inicia o trial e re-lê
    st = get_status(bkey)
    if not st.get("exists"):
        ensure_trial(bkey)
        st = get_status(bkey)

    if st.get("active"):
        # Pode retornar infos úteis para quem depender
        return {"billing_key": bkey, "status": st, "user": user}

    # 402 Payment Required comunica claramente ao front que precisa pagar
    raise HTTPException(
        status_code=402,
        detail={
            "message": "Sua avaliação terminou ou a assinatura está inativa. "
                       "Acesse a aba Pagamentos para continuar.",
            "require_payment": True,
            "days_left": st.get("days_left", 0),
            "trial_ends_at": st.get("trial_ends_at"),
            "paid_until": st.get("paid_until"),
            "plan": st.get("plan"),
        },
    )
