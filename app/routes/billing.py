# app/routes/billing.py
from __future__ import annotations

import os
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.auth import get_current_user  # usa o auth padrão do projeto
from app.services.billing import (
    make_billing_key,
    ensure_trial,
    get_status,
    mark_paid,
)

router = APIRouter()


class CheckoutLinkIn(BaseModel):
    return_url: Optional[str] = None  # URL para redirecionar após pagamento (opcional)


@router.post("/register-trial")
async def register_trial(user=Depends(get_current_user)) -> Dict[str, Any]:
    """
    Garante o registro do trial (idempotente) e retorna o status de billing.
    """
    billing_key = make_billing_key(user.get("token"), user.get("host"), user.get("instance_id"))
    ensure_trial(billing_key)
    st = get_status(billing_key)
    return {"ok": True, "status": st}


@router.get("/status")
async def billing_status(user=Depends(get_current_user)) -> Dict[str, Any]:
    """
    Retorna o status atual (active, trial_until, paid_until, plan, status).
    """
    billing_key = make_billing_key(user.get("token"), user.get("host"), user.get("instance_id"))
    st = get_status(billing_key)
    return {"ok": True, "status": st}


@router.post("/checkout-link")
async def checkout_link(body: CheckoutLinkIn, user=Depends(get_current_user)) -> Dict[str, Any]:
    """
    Aqui normalmente você cria a ordem na GetNet e devolve a URL real.
    Por enquanto simulamos com GETNET_CHECKOUT_BASE + ref + return_url.
    """
    billing_key = make_billing_key(user.get("token"), user.get("host"), user.get("instance_id"))
    base = os.getenv("GETNET_CHECKOUT_BASE", "https://pay.getnet.com.br/checkout")
    ret = body.return_url or os.getenv("PAY_RETURN_URL") or "https://lunahia.com.br/pagamentos/getnet/"
    url = f"{base}?ref={billing_key}&return_url={ret}"
    return {"ok": True, "url": url, "ref": billing_key}


class WebhookIn(BaseModel):
    ref: str                        # billing_key enviado como reference/order_id
    status: str = "paid"            # 'paid', 'approved', 'refused', etc.
    days: int = 30                  # dias para estender o paid_until
    plan: Optional[str] = "mensal-34990"


@router.post("/webhook/getnet")
async def webhook_getnet(payload: WebhookIn) -> Dict[str, Any]:
    """
    Endpoint para a GetNet chamar (server-to-server). Atualiza o paid_until.
    OBS: valide a assinatura/HMAC da GetNet aqui, se aplicável.
    """
    if not payload.ref:
        raise HTTPException(status_code=400, detail="ref ausente")
    mark_paid(payload.ref, days=payload.days, plan=payload.plan, status=payload.status)
    return {"ok": True}
