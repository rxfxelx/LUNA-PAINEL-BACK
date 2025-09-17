# app/routes/pay_getnet.py
from __future__ import annotations

import os
import re
import uuid
from typing import Any, Dict, Optional, Literal

import httpx
from fastapi import APIRouter, Body, HTTPException, Request
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
GETNET_ENV = (os.getenv("GETNET_ENV") or "homologacao").lower()
BASE_URL = "https://api.getnet.com.br" if GETNET_ENV.startswith("prod") else "https://api-homologacao.getnet.com.br"
CLIENT_ID = os.getenv("GETNET_CLIENT_ID") or ""
CLIENT_SECRET = os.getenv("GETNET_CLIENT_SECRET") or ""
SELLER_ID = os.getenv("GETNET_SELLER_ID") or ""


@router.on_event("startup")
async def _startup() -> None:
    # garante tabelas
    await init_billing_schema()


# =========================
# Modelos p/ checkout link
# =========================
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


# =========================
# Modelos p/ pagamento direto
# =========================
class BillingAddress(BaseModel):
    street: Optional[str] = ""
    number: Optional[str] = ""
    complement: Optional[str] = ""
    district: Optional[str] = ""
    city: Optional[str] = ""
    state: Optional[str] = ""
    country: Optional[str] = ""
    postal_code: Optional[str] = ""


class CustomerIn(BaseModel):
    email: EmailStr
    name: str
    document_number: str
    phone_number: Optional[str] = ""
    first_name: Optional[str] = ""
    last_name: Optional[str] = ""
    billing_address: Optional[BillingAddress] = None


class CardIn(BaseModel):
    card_number: str
    cardholder_name: str
    expiration_month: str  # "MM"
    expiration_year: str   # "YYYY" (aceita 2 dígitos; normalizamos)
    security_code: str
    brand: str  # Visa | Mastercard | American Express | Elo | Hipercard | Diners


class PayDirectIn(BaseModel):
    type: Literal["credit", "debit"] = "credit"
    amount_cents: int = Field(default=PRICE_CENTS, ge=50)
    currency: str = Field(default="BRL", pattern="^[A-Z]{3}$")
    number_installments: int = Field(default=1, ge=1, le=12)
    cardholder_mobile: Optional[str] = None  # obrigatório p/ débito
    customer: CustomerIn
    card: CardIn
    order_id: Optional[str] = None
    product_type: str = Field(default="digital_content")  # service|digital_content|physical_goods etc.
    sales_tax: int = 0


def _digits(s: str) -> str:
    return re.sub(r"\D+", "", s or "")


def _pad2(v: str) -> str:
    v = (v or "").strip()
    return (("0" + v)[-2:])[:2] or "00"


def _to_yyyy(v: str) -> str:
    d = _digits(v)
    if len(d) >= 4:
        return d[:4]
    if len(d) == 2:
        return "20" + d
    if len(d) == 3:
        return "20" + d[-2:]
    return d or ""


def _normalize_brand(b: str) -> str:
    m = (b or "").strip().lower()
    if "master" in m:
        return "Mastercard"
    if "american" in m or "amex" in m:
        return "American Express"
    if "elo" in m:
        return "Elo"
    if "hiper" in m:
        return "Hipercard"
    if "diners" in m:
        return "Diners"
    return "Visa"


def _split_name(full: str) -> tuple[str, str]:
    parts = (full or "").strip().split()
    if not parts:
        return "", ""
    first = parts[0]
    last = " ".join(parts[1:]) if len(parts) > 1 else first
    return first, last


def _e164_br(phone: str) -> str:
    if not phone:
        return ""
    p = phone.strip()
    if p.startswith("+"):
        return re.sub(r"[^\d+]", "", p)
    d = _digits(p)
    if not d:
        return ""
    if d.startswith("55"):
        return "+" + d
    return "+55" + d


async def _oauth_token() -> str:
    if not CLIENT_ID or not CLIENT_SECRET:
        raise HTTPException(status_code=500, detail="Credenciais GetNet não configuradas.")
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"{BASE_URL}/auth/oauth/v2/token",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={"grant_type": "client_credentials", "scope": "oob"},
            auth=(CLIENT_ID, CLIENT_SECRET),
        )
        if resp.status_code != 200:
            raise HTTPException(status_code=400, detail=f"Erro OAuth: {resp.text}")
        return resp.json().get("access_token") or ""


async def _tokenize_card(access_token: str, card_number: str, customer_id: str) -> str:
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"{BASE_URL}/v1/tokens/card",
            headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
            json={"card_number": _digits(card_number), "customer_id": customer_id},
        )
        if resp.status_code != 200:
            raise HTTPException(status_code=400, detail=f"Erro tokenizar cartão: {resp.text}")
        data = resp.json()
        return data.get("number_token") or data.get("numberToken") or ""


def _resolve_return_url(ref: str) -> str:
    base = RETURN_BASE or (PUBLIC_BASE + "/pagamentos/getnet/sucesso")
    return f"{base}?ref={ref}"


def _extract_ref_and_status(payload: Dict[str, Any]) -> Dict[str, Optional[str]]:
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

    status = None
    if status_raw:
        if any(x in status_raw for x in ("paid", "approved", "success", "authorized", "confirmed")):
            status = "paid"
        elif any(x in status_raw for x in ("denied", "canceled", "cancelled", "refused", "failed", "error")):
            status = "failed"

    return {"ref": ref, "status": status}


# =================================
# ROTAS: Checkout (link) — mantém
# =================================
@router.post("/checkout", response_model=CheckoutOut)
async def create_checkout(body: CheckoutIn) -> CheckoutOut:
    ref = f"gt_{uuid.uuid4().hex}"
    tenant_key = body.tenant_key or str(body.email)

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

    try:
        await update_payment_status(ref, "pending", raw=ret.get("raw") or {})
    except Exception:
        pass

    return CheckoutOut(ref=ret["reference_id"], url=ret["payment_url"])


@router.get("/checkout-url", response_model=CheckoutOut)
async def get_checkout_url(
    email: Optional[EmailStr] = None,
    plan: str = "luna_base",
    amount_cents: int = PRICE_CENTS,
    tenant_key: Optional[str] = None,
) -> CheckoutOut:
    import uuid as _uuid
    actual_email = email or f"anon-{_uuid.uuid4().hex}@example.com"
    body = CheckoutIn(
        email=actual_email,
        plan=plan,
        amount_cents=amount_cents,
        tenant_key=tenant_key,
    )
    return await create_checkout(body)


# =================================
# NOVA ROTA: Pagamento direto
# - Tokeniza e paga no servidor
# - card NO NÍVEL RAIZ (conforme Getnet)
# =================================
@router.post("/pay-direct")
async def pay_direct(req: Request, body: PayDirectIn = Body(...)) -> Dict[str, Any]:
    if not SELLER_ID:
        raise HTTPException(status_code=500, detail="SELLER_ID não configurado.")

    # Normalizações
    full_name = body.customer.name.strip()
    first, last = (body.customer.first_name or "").strip(), (body.customer.last_name or "").strip()
    if not first or not last:
        first, last = _split_name(full_name)

    exp_year = _to_yyyy(body.card.expiration_year)
    exp_month = _pad2(body.card.expiration_month)
    brand = _normalize_brand(body.card.brand)
    cvv = _digits(body.card.security_code)
    if (brand == "American Express" and len(cvv) != 4) or (brand != "American Express" and len(cvv) != 3):
        raise HTTPException(status_code=400, detail="CVV inválido para a bandeira informada.")

    document_number = _digits(body.customer.document_number)
    document_type = "CNPJ" if len(document_number) > 11 else "CPF"
    phone_number = _e164_br(body.customer.phone_number or "")
    if body.type == "debit" and not (body.cardholder_mobile or phone_number):
        raise HTTPException(status_code=400, detail="cardholder_mobile é obrigatório para pagamentos no débito.")

    # 1) OAuth
    access_token = await _oauth_token()

    # 2) Tokenização
    number_token = await _tokenize_card(access_token, body.card.card_number, str(body.customer.email))
    if not number_token:
        raise HTTPException(status_code=400, detail="Falha ao tokenizar o cartão.")

    # 3) Monta payload final (conforme especificação)
    order_id = body.order_id or f"order_{uuid.uuid4().hex}"
    customer_dict: Dict[str, Any] = {
        "customer_id": str(body.customer.email),
        "first_name": first,
        "last_name": last,
        "name": full_name,
        "email": str(body.customer.email),
        "document_type": document_type,
        "document_number": document_number,
        "phone_number": phone_number or "",
    }
    if body.customer.billing_address:
        ba = body.customer.billing_address
        customer_dict["billing_address"] = {
            "street": ba.street or "",
            "number": ba.number or "",
            "complement": ba.complement or "",
            "district": ba.district or "",
            "city": ba.city or "",
            "state": ba.state or "",
            "country": ba.country or "",
            "postal_code": _digits(ba.postal_code or ""),
        }

    payload: Dict[str, Any] = {
        "seller_id": SELLER_ID,
        "amount": int(body.amount_cents),
        "currency": body.currency,
        "order": {
            "order_id": order_id,
            "sales_tax": int(body.sales_tax or 0),
            "product_type": body.product_type,
        },
        "customer": customer_dict,
        # IMPORTANTE: card no NÍVEL RAIZ, não dentro de credit/debit
        "card": {
            "number_token": number_token,
            "cardholder_name": body.card.cardholder_name.strip().upper(),
            "expiration_month": exp_month,
            "expiration_year": exp_year,
            "brand": brand,
            "security_code": cvv,
        },
    }

    # credit|debit
    endpoint = ""
    if body.type == "debit":
        endpoint = "/v1/payments/debit"
        payload["debit"] = {
            "cardholder_mobile": body.cardholder_mobile or phone_number or "",
            "soft_descriptor": "LunaAI",
            "authenticated": False,
            # REMOVIDO: dynamic_mcc (não obrigatório)
        }
    else:
        endpoint = "/v1/payments/credit"
        payload["credit"] = {
            "delayed": False,
            "authenticated": False,
            "pre_authorization": False,
            "save_card_data": False,
            "transaction_type": "FULL",
            "number_installments": int(body.number_installments or 1),
            "soft_descriptor": "LunaAI",
            # REMOVIDO: dynamic_mcc (não obrigatório)
        }

    # Dispositivo (opcional) — melhora antifraude quando disponível
    ip = (req.headers.get("x-forwarded-for") or req.client.host or "").split(",")[0].strip()
    ua = req.headers.get("user-agent") or ""
    if ip or ua:
        payload["device"] = {"ip_address": ip or "0.0.0.0", "user_agent": ua[:256]}

    # 4) Pagamento
    async with httpx.AsyncClient(timeout=40.0) as client:
        pay_resp = await client.post(
            f"{BASE_URL}{endpoint}",
            headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
            json=payload,
        )
        if pay_resp.status_code != 200:
            # repassa erro bruto da Getnet p/ facilitar debug
            raise HTTPException(status_code=400, detail=pay_resp.text)

        data = pay_resp.json()
        # status normalizado
        status_raw = str(
            data.get("status") or data.get("transaction_status") or (data.get("payment") or {}).get("status") or ""
        ).lower()
        normalized = "approved" if any(s in status_raw for s in ("approved", "authorized", "confirmed", "paid")) else status_raw

        return {"ok": True, "status": normalized, "raw": data}


# =================================
# Webhook e status — mantém
# =================================
@router.post("/webhook", response_model=WebhookOut)
async def webhook(payload: Dict[str, Any] = Body(...)) -> WebhookOut:
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
