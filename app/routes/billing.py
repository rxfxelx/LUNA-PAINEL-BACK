# app/routes/billing.py
from __future__ import annotations

import os
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.auth import get_current_user
from app.services.billing import (
    make_billing_key,
    ensure_trial,
    get_status,
    mark_paid,
)

router = APIRouter()


# ------------------------ helpers ------------------------
def _billing_key_from_user(user: Dict[str, Any]) -> str:
    """
    Monta o billing_key a partir do JWT. Valida token/host.
    """
    token = (user.get("token") or user.get("instance_token") or "").strip()
    host = (user.get("host") or "").strip()
    iid  = user.get("instance_id")
    if not token or not host:
        raise HTTPException(status_code=401, detail="JWT inválido: sem token/host")
    try:
        return make_billing_key(token, host, iid)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Erro ao gerar billing_key: {e}")


def _safe_get_status(bkey: str) -> Dict[str, Any]:
    """
    Lê o status do billing sem deixar a rota explodir em 500.
    """
    try:
        return get_status(bkey)
    except Exception as e:
        # serviço de billing indisponível, devolve 503 para o front lidar
        raise HTTPException(status_code=503, detail=f"Billing indisponível: {e}")


# ------------------------ modelos ------------------------
class CheckoutLinkIn(BaseModel):
    return_url: Optional[str] = None  # URL para redirecionar após pagamento (opcional)


class WebhookIn(BaseModel):
    ref: str                         # billing_key enviado como reference/order_id
    status: str = "paid"             # 'paid', 'approved', 'refused', etc.
    days: int = 30                   # dias para estender o paid_until
    plan: Optional[str] = "mensal-34990"


# ------------------------ rotas ------------------------
@router.post("/register-trial")
async def register_trial(user=Depends(get_current_user)) -> Dict[str, Any]:
    """
    Garante o registro do tenant e inicia o trial se não existir.
    Idempotente: se já existir, apenas retorna o status.
    Nunca deve derrubar com 500 por regra.
    """
    bkey = _billing_key_from_user(user)

    # tenta ler status; se não existir, cria trial
    try:
        st = get_status(bkey)
    except Exception:
        st = {"exists": False, "active": False}

    if not st.get("exists"):
        try:
            ensure_trial(bkey)
        except Exception:
            # segue; reconsulta já lida com indisponibilidade
            pass

    st = _safe_get_status(bkey)
    return {"ok": True, "billing_key": bkey, "status": st}


@router.get("/status")
async def billing_status(user=Depends(get_current_user)) -> Dict[str, Any]:
    """
    Retorna o status atual do billing (active, days_left, trial_ends_at, paid_until, plan...).
    """
    bkey = _billing_key_from_user(user)
    st = _safe_get_status(bkey)
    return {"ok": True, "billing_key": bkey, "status": st}


@router.post("/checkout-link")
async def checkout_link(body: CheckoutLinkIn, user=Depends(get_current_user)) -> Dict[str, Any]:
    """
    Normalmente criaria a ordem na GetNet e retornaria a URL real.
    Aqui simulamos: GETNET_CHECKOUT_BASE?ref=<billing_key>&return_url=<ret>
    """
    bkey = _billing_key_from_user(user)
    base = os.getenv("GETNET_CHECKOUT_BASE", "https://pay.getnet.com.br/checkout")
    ret = body.return_url or os.getenv("PAY_RETURN_URL") or "https://lunahia.com.br/pagamentos/getnet/"
    url = f"{base}?ref={bkey}&return_url={ret}"
    return {"ok": True, "url": url, "ref": bkey}


@router.post("/webhook/getnet")
async def webhook_getnet(payload: WebhookIn) -> Dict[str, Any]:
    """
    Endpoint para receber o callback da GetNet (server-to-server).
    Atualiza o paid_until e o plan. (Validação de assinatura/HMAC deve ser feita aqui, se houver.)
    """
    if not payload.ref:
        raise HTTPException(status_code=400, detail="ref ausente")

    try:
        mark_paid(payload.ref, days=payload.days, plan=payload.plan, status=payload.status)
    except Exception as e:
        # não derruba com 500 – responde que não foi possível aplicar
        raise HTTPException(status_code=503, detail=f"Falha ao aplicar pagamento: {e}")

    return {"ok": True}
