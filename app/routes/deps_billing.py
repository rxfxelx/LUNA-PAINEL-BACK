# app/routes/deps_billing.py
from __future__ import annotations

from fastapi import Depends, HTTPException
from app.auth import get_current_user
from app.db.models_billing import is_tenant_active_by_key, is_tenant_active_by_email


async def require_active_tenant(user=Depends(get_current_user)) -> dict:
    """
    Bloqueia rotas 'operacionais' se a assinatura não estiver ativa.
    Tenta identificar o tenant por:
      1) user['email'] (se você passar a incluir no JWT futuramente)
      2) user['instance_token'] ou user['token']  (atual fluxo)
    """
    email = (user or {}).get("email")
    key = (
        (user or {}).get("instance_id")
        or (user or {}).get("phone_number_id")
        or (user or {}).get("pnid")
        or (user or {}).get("instance_token")
        or (user or {}).get("token")
        or (user or {}).get("sub")
        or ""
    )
    ok = False
    if email:
        ok = await is_tenant_active_by_email(str(email))
    if not ok and key:
        ok = await is_tenant_active_by_key(str(key))

    if not ok:
        # 402 Payment Required se encaixa bem aqui
        raise HTTPException(
            status_code=402,
            detail="Assinatura inativa. Acesse /pagamentos/getnet para assinar/regularizar.",
        )

    return user
