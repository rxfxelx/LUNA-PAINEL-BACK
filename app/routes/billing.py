# app/routes/billing.py
from __future__ import annotations
from typing import Dict, Any, Optional
from fastapi import APIRouter, Depends, HTTPException, Body, Request
from pydantic import BaseModel
import os

from app.utils.jwt_handler import decode_jwt  # retorna dict com host/token/instance_id se houver
from app.services.billing import (
    make_billing_key,
    ensure_trial,
    get_status,
    mark_paid,
)

router = APIRouter()

class CheckoutLinkIn(BaseModel):
    return_url: Optional[str] = None
    # outros metadados opcionais

@router.post("/register-trial")
async def register_trial(user=Depends(decode_jwt)) -> Dict[str, Any]:
    billing_key = make_billing_key(user["token"], user["host"], user.get("instance_id"))
    rec = ensure_trial(billing_key)
    st = get_status(billing_key)
    return {"ok": True, "status": st}

@router.get("/status")
async def billing_status(user=Depends(decode_jwt)) -> Dict[str, Any]:
    billing_key = make_billing_key(user["token"], user["host"], user.get("instance_id"))
    st = get_status(billing_key)
    return {"ok": True, "status": st}

@router.post("/checkout-link")
async def checkout_link(body: CheckoutLinkIn, user=Depends(decode_jwt)) -> Dict[str, Any]:
    """
    Gera a URL de pagamento (placeholder). Normalmente você chama a API da GetNet aqui e devolve a URL.
    Usamos o billing_key como "reference" para receber no webhook.
    """
    billing_key = make_billing_key(user["token"], user["host"], user.get("instance_id"))
    base = os.getenv("GETNET_CHECKOUT_BASE", "https://pay.getnet.com.br/checkout")
    # Você vai criar a ordem via API da GetNet e receber a URL real; por enquanto simulamos:
    ret = body.return_url or os.getenv("PAY_RETURN_URL") or "https://lunahia.com.br/pagamentos/getnet/"
    url = f"{base}?ref={billing_key}&return_url={ret}"
    return {"ok": True, "url": url}

class WebhookIn(BaseModel):
    ref: str                   # billing_key que enviamos (ou order_id que mapeia para ele)
    status: str = "paid"       # 'paid', 'approved', 'refused' etc.
    days: int = 30
    plan: Optional[str] = "mensal-34990"

@router.post("/webhook/getnet")
async def webhook_getnet(payload: WebhookIn):
    """
    Endpoint para a GetNet chamar (server-to-server). Atualiza o paid_until.
    """
    if not payload.ref:
        raise HTTPException(400, "ref ausente")
    # Valida assinatura/secret aqui se necessário (cabeçalhos HMAC da GetNet)
    mark_paid(payload.ref, days=payload.days, plan=payload.plan, status=payload.status)
    return {"ok": True}
