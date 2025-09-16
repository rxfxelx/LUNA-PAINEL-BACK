# app/routes/pay_getnet.py
from __future__ import annotations

import os
import uuid
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel, EmailStr, Field

from app.pay.getnet_client import GetNetClient
from app.models_billing import (
    init_billing_schema,
    create_pending_payment,
    update_payment_status,
    get_payment_by_ref,
    ensure_tenant_active,
)

router = APIRouter()

PRICE_CENTS = int(os.getenv("LUNA_PRICE_CENTS") or 34990)
PUBLIC_BASE = (os.getenv("PUBLIC_BASE_URL") or "").rstrip("/")
RETURN_BASE = (os.getenv("GETNET_RETURN_BASE") or f"{PUBLIC_BASE}/pagamentos/getnet/sucesso").rstrip("/")
NOTIFY_URL = (os.getenv("GETNET_NOTIFY_URL") or f"{PUBLIC_BASE}/api/pay/getnet/webhook").rstrip("/")


@router.on_event("startup")
async def _startup() -> None:
    # garante tabelas
    await init_billing_schema()


# --------- MODELOS ----------
class CheckoutIn(BaseModel):
    email: EmailStr
    plan: str = Field(default="luna_base")
    amount_cents: int = Field(default=PRICE_CENTS)  # permite testar valores diferentes
    tenant_key: Optional[str] = Field(
        default=None,
        description="Identificador do tenant (ex: email, token da instância, etc.). Se ausente, usa o email.",
    )


class CheckoutOut(BaseModel):
    ref: str
    url: str


class WebhookOut(BaseModel):
    ok: bool


# --------- HELPERS ----------
def _resolve_return_url(ref: str) -> str:
    base = RETURN_BASE or (PUBLIC_BASE + "/pagamentos/getnet/sucesso")
    return f"{base}?ref={ref}"


def _extract_ref_and_status(payload: Dict[str, Any]) -> Dict[str, Optional[str]]:
    """
    Tenta deduzir os campos do webhook para não travar o desenvolvimento
    enquanto a integração exata não estiver definida.
    """
    keys_ref = ("reference_id", "referenceId", "ref", "order_id", "orderId", "payment_reference")
    keys_status = ("status", "payment_status", "current_status", "transaction_status")

    ref = None
    for k in keys_ref:
        if k in payload and payload[k]:
            ref = str(payload[k])
            break

    status_raw = None
    for k in keys_status:
        if k in payload and payload[k]:
            status_raw = str(payload[k]).lower()
            break

    # normaliza status
    status = None
    if status_raw:
        if any(x in status_raw for x in ("paid", "approved", "success", "authorized")):
            status = "paid"
        elif any(x in status_raw for x in ("denied", "canceled", "cancelled", "refused", "failed", "error")):
            status = "failed"

    return {"ref": ref, "status": status}


# --------- ROTAS ----------
@router.post("/checkout", response_model=CheckoutOut)
async def create_checkout(body: CheckoutIn) -> CheckoutOut:
    """
    Cria registro 'pending' e retorna a URL para pagamento (mock ou real).
    """
    ref = f"gt_{uuid.uuid4().hex}"
    tenant_key = body.tenant_key or str(body.email)

    # grava pagamento pendente
    await create_pending_payment(
        reference_id=ref,
        tenant_key=tenant_key,
        email=str(body.email),
        plan=body.plan,
        amount_cents=int(body.amount_cents),
        raw=None,
    )

    client = GetNetClient()
    ret = await client.create_checkout(
        amount_cents=int(body.amount_cents),
        customer_email=str(body.email),
        reference_id=ref,
        return_url=_resolve_return_url(ref),
        notify_url=NOTIFY_URL,
        description=f"Assinatura {body.plan}",
        metadata={"tenant_key": tenant_key, "plan": body.plan},
    )

    # opcional: registrar resposta crua do gateway (não muda status)
    try:
        await update_payment_status(ref, "pending", raw=ret.get("raw") or {})
    except Exception:
        pass

    return CheckoutOut(ref=ret["reference_id"], url=ret["payment_url"])


# ---------------------------------------------------------------------------
# Compatibilidade: rota GET /checkout-url
#
# Versões antigas do front‑end utilizam um endpoint GET com nome
# ``checkout-url`` que aceita o plano via query string.  Esta função
# reencaminha a chamada para ``create_checkout`` construindo os parâmetros
# necessários.  Se o e‑mail não for informado, retornamos erro 400.  Esta
# rota facilita a migração gradual do front‑end sem quebrar a interface.
@router.get("/checkout-url", response_model=CheckoutOut)
async def get_checkout_url(
    email: Optional[EmailStr] = None,
    plan: str = "luna_base",
    amount_cents: int = PRICE_CENTS,
    tenant_key: Optional[str] = None,
) -> CheckoutOut:
    """
    Cria um checkout GetNet via requisição GET.

    Esta rota existe por motivos de compatibilidade com versões mais antigas
    do front‑end que esperavam obter a URL de pagamento através de uma
    requisição GET.  Os parâmetros são passados via querystring.  Se o
    parâmetro **email** não for fornecido, utiliza‑se um e‑mail fictício para
    satisfazer a API de pagamento.  Para integrações oficiais, recomenda‑se
    utilizar a rota POST ``/checkout`` e enviar o e‑mail real do cliente.

    Parâmetros:
        * **email** – endereço de e‑mail do cliente.  Opcional.
        * **plan** – identificador do plano (opcional, "luna_base" por padrão).
        * **amount_cents** – valor em centavos.  Utiliza PRICE_CENTS por padrão.
        * **tenant_key** – identificador do tenant para usos multi‑instância.

    A implementação cria um registro de pagamento pendente, gera a URL de
    pagamento através de :class:`GetNetClient` e retorna as mesmas
    informações disponíveis em ``create_checkout``.
    """
    # Se o e‑mail não for informado, utilizamos um placeholder.  O endereço
    # resulta em um identificador único para evitar colisões de pagamentos.
    import uuid
    actual_email = email or f"anon-{uuid.uuid4().hex}@example.com"
    body = CheckoutIn(
        email=actual_email,
        plan=plan,
        amount_cents=amount_cents,
        tenant_key=tenant_key,
    )
    return await create_checkout(body)


@router.post("/webhook", response_model=WebhookOut)
async def webhook(payload: Dict[str, Any] = Body(...)) -> WebhookOut:
    """
    Webhook do gateway. Atualiza status do pagamento e ativa tenant quando 'paid'.
    """
    info = _extract_ref_and_status(payload or {})
    ref = info["ref"]
    status = info["status"]
    if not ref:
        raise HTTPException(status_code=400, detail="Webhook sem referência (reference_id).")

    row = await get_payment_by_ref(ref)
    if not row:
        # idempotência: se ainda não temos, cria como pendente
        await create_pending_payment(
            reference_id=ref,
            tenant_key=str(payload.get("tenant_key") or payload.get("metadata", {}).get("tenant_key") or ""),
            email=str(payload.get("email") or payload.get("payer_email") or ""),
            plan=str(payload.get("plan") or payload.get("metadata", {}).get("plan") or "luna_base"),
            amount_cents=int(payload.get("amount_cents") or 0),
            raw=payload,
        )
        row = await get_payment_by_ref(ref)

    # Atualiza status
    if status == "paid":
        await update_payment_status(ref, "paid", raw=payload)
        # ativa tenant (1 mês)
        tenant_key = row["tenant_key"]
        email = row["email"]
        plan = row["plan"]
        await ensure_tenant_active(tenant_key=tenant_key, email=email, plan=plan, months=1)
    elif status == "failed":
        await update_payment_status(ref, "failed", raw=payload)
    else:
        # mantém pending, só registra raw
        await update_payment_status(ref, row["status"], raw=payload)

    return WebhookOut(ok=True)


@router.get("/status")
async def status(ref: str):
    row = await get_payment_by_ref(ref)
    if not row:
        raise HTTPException(status_code=404, detail="Pagamento não encontrado")
    return {
        "reference_id": row["reference_id"],
        "email": row["email"],
        "tenant_key": row["tenant_key"],
        "plan": row["plan"],
        "amount_cents": row["amount_cents"],
        "status": row["status"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }

