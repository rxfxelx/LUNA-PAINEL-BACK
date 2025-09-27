"""
Stripe payment integration routes for Luna.

Flow (resumo):
1) Criar sessão via /checkout (ou /checkout-url) e redirecionar para `session.url`.
2) Stripe chama /webhook com eventos (ex.: invoice.paid, invoice.payment_failed).
3) No paid: marcamos o pagamento como "paid" e deixamos o tenant ativo por 1 mês.
4) No failed/cancel: atualizamos o pagamento para "failed" e desativamos quando necessário.
"""

from __future__ import annotations

import json
import os
import uuid
from typing import Any, Dict, Optional, Tuple

import stripe  # type: ignore
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field

from app.models_billing import (
    create_pending_payment,
    update_payment_status,
    ensure_tenant_active,
    set_tenant_inactive,
)

router = APIRouter()

# -----------------------------------------------------------------------------
# Stripe configuration
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "").strip()
STRIPE_PRICE_ID = os.getenv("STRIPE_PRICE_ID", "").strip()
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "").strip()

stripe.api_key = STRIPE_SECRET_KEY or None

PUBLIC_BASE = (os.getenv("PUBLIC_BASE_URL") or "").rstrip("/")
# Retornos apontam para a RAIZ do site (páginas estáticas /pagamentos foram removidas).
RETURN_BASE = (os.getenv("STRIPE_RETURN_BASE") or f"{PUBLIC_BASE}/").rstrip("/")
CANCEL_BASE = (os.getenv("STRIPE_CANCEL_BASE") or f"{PUBLIC_BASE}/").rstrip("/")
# Apenas referência para configuração no dashboard (não usado diretamente abaixo).
NOTIFY_URL = (os.getenv("STRIPE_NOTIFY_URL") or f"{PUBLIC_BASE}/api/pay/stripe/webhook").rstrip("/")

DEFAULT_PRICE_CENTS = int(os.getenv("LUNA_PRICE_CENTS") or 34990)


# ---------------------------- Request/Response models -------------------------
class CheckoutIn(BaseModel):
    """
    Input payload for creating a checkout session.
    """
    email: EmailStr
    plan: str = Field(default="luna_base")
    amount_cents: int = Field(default=DEFAULT_PRICE_CENTS)
    tenant_key: Optional[str] = Field(default=None)


class CheckoutOut(BaseModel):
    """
    Response for checkout creation.
    """
    ref: str
    url: str


# ---------------------------- Helper functions -------------------------------
def _build_success_url(ref: str) -> str:
    base = RETURN_BASE or ""
    return f"{base}?ref={ref}" if base else f"?ref={ref}"


def _build_cancel_url(ref: str) -> str:
    base = CANCEL_BASE or ""
    return f"{base}?ref={ref}" if base else f"?ref={ref}"


def _extract_event_parts(event: Any) -> Tuple[str, Dict[str, Any]]:
    """
    Extrai (event_type, data_object) de um dict ou stripe.Event.
    Garante que sempre retornaremos estruturas nativas (dict).
    """
    # Caso 1: payload já em dict (sem validação de assinatura)
    if isinstance(event, dict):
        etype = event.get("type", "")
        data_obj = (event.get("data") or {}).get("object") or {}
        return etype, data_obj if isinstance(data_obj, dict) else {}

    # Caso 2: stripe.Event / StripeObject
    try:
        etype = getattr(event, "type", "") or event["type"]
    except Exception:
        etype = ""

    try:
        data = getattr(event, "data", None) or event["data"]
        obj = getattr(data, "object", None) or data["object"]
        # StripeObject -> dict
        if hasattr(obj, "to_dict"):
            obj = obj.to_dict()  # type: ignore
        return etype, obj if isinstance(obj, dict) else {}
    except Exception:
        return etype, {}

# ---------------------------- Checkout endpoints -----------------------------
@router.post("/checkout", response_model=CheckoutOut)
async def create_checkout(body: CheckoutIn) -> CheckoutOut:
    """
    Cria uma sessão de Stripe Checkout (modo assinatura) e retorna a URL.
    """
    if not STRIPE_SECRET_KEY or not STRIPE_PRICE_ID:
        raise HTTPException(status_code=500, detail="Stripe não configurado.")

    # Referência interna
    ref = f"st_{uuid.uuid4().hex}"
    tenant_key = body.tenant_key or str(body.email)

    # 1) cria/atualiza registro local "pending" (não bloqueia o fluxo)
    try:
        await create_pending_payment(
            reference_id=ref,
            tenant_key=tenant_key,
            email=str(body.email),
            plan=body.plan,
            amount_cents=int(body.amount_cents),
            raw=None,
        )
    except Exception:
        # Em caso de falha local, seguimos; o webhook poderá reconciliar.
        pass

    # 2) gera sessão de checkout no Stripe
    try:
        session = stripe.checkout.Session.create(
            mode="subscription",
            line_items=[{"price": STRIPE_PRICE_ID, "quantity": 1}],
            success_url=_build_success_url(ref),
            cancel_url=_build_cancel_url(ref),
            client_reference_id=ref,
            customer_email=str(body.email),
            # Metadata na sessão e na assinatura para correlacionar no webhook
            metadata={
                "reference_id": ref,
                "tenant_key": tenant_key,
                "plan": body.plan,
                "email": str(body.email),
            },
            subscription_data={
                "metadata": {
                    "reference_id": ref,
                    "tenant_key": tenant_key,
                    "plan": body.plan,
                    "email": str(body.email),
                }
            },
        )
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Falha ao criar sessão de pagamento: {e}")

    # 3) persiste "pending" com o session_id
    try:
        await update_payment_status(ref, "pending", raw={"session_id": session.id})
    except Exception:
        pass

    return CheckoutOut(ref=ref, url=session.url)


@router.get("/checkout-url", response_model=CheckoutOut)
async def get_checkout_url(
    email: Optional[EmailStr] = None,
    plan: str = "luna_base",
    amount_cents: int = DEFAULT_PRICE_CENTS,
    tenant_key: Optional[str] = None,
) -> CheckoutOut:
    """
    Versão GET para facilitar redirecionamento a partir do front.
    Se `email` não for enviado, criamos um anônimo (apenas para teste).
    """
    import uuid as _uuid
    actual_email: EmailStr = email or EmailStr(f"anon-{_uuid.uuid4().hex}@example.com")  # type: ignore
    body = CheckoutIn(
        email=actual_email,
        plan=plan,
        amount_cents=amount_cents,
        tenant_key=tenant_key,
    )
    return await create_checkout(body)


# ---------------------------- Webhook handler -------------------------------
@router.post("/webhook")
async def stripe_webhook(request: Request) -> Dict[str, Any]:
    """
    Webhook da Stripe.

    - invoice.paid: marca pagamento como "paid" e ativa o tenant por 1 mês.
    - invoice.payment_failed: marca pagamento como "failed".
    - customer.subscription.deleted: marca "failed" e desativa o tenant.
    """
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")
    event: Any

    # Com secret -> valida assinatura; sem secret (homolog/dev) -> parse JSON simples
    if STRIPE_WEBHOOK_SECRET:
        try:
            event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Webhook signature verification failed: {e}")
    else:
        try:
            event = json.loads(payload.decode("utf-8"))
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Invalid payload: {e}")

    event_type, data_object = _extract_event_parts(event)

    # ---------------- invoice.paid ----------------
    if event_type == "invoice.paid":
        subscription_id = data_object.get("subscription")
        if subscription_id:
            try:
                sub = stripe.Subscription.retrieve(subscription_id)
                meta = getattr(sub, "metadata", None) or {}
                # StripeObject → dict
                if hasattr(meta, "to_dict"):
                    meta = meta.to_dict()  # type: ignore
                ref = meta.get("reference_id")
                tenant_key = meta.get("tenant_key") or ref
                plan = meta.get("plan", "luna_base")
                email = meta.get("email")
                if ref:
                    try:
                        await update_payment_status(ref, "paid", raw={"type": event_type, "object": data_object})
                    except Exception:
                        pass
                    try:
                        # Ativa por 1 mês — o ciclo seguinte será cobrado pela Stripe
                        await ensure_tenant_active(
                            tenant_key=tenant_key,
                            email=email,
                            plan=plan,
                            months=1,
                        )
                    except Exception:
                        pass
            except Exception:
                # Não quebra o webhook; Stripe só precisa de 2xx
                pass

    # ---------------- invoice.payment_failed ----------------
    elif event_type == "invoice.payment_failed":
        subscription_id = data_object.get("subscription")
        if subscription_id:
            try:
                sub = stripe.Subscription.retrieve(subscription_id)
                meta = getattr(sub, "metadata", None) or {}
                if hasattr(meta, "to_dict"):
                    meta = meta.to_dict()  # type: ignore
                ref = meta.get("reference_id")
                if ref:
                    try:
                        await update_payment_status(ref, "failed", raw={"type": event_type, "object": data_object})
                    except Exception:
                        pass
            except Exception:
                pass

    # ---------------- customer.subscription.deleted ----------------
    elif event_type == "customer.subscription.deleted":
        meta = data_object.get("metadata", {}) or {}
        ref = meta.get("reference_id")
        tenant_key = meta.get("tenant_key")
        if ref:
            try:
                await update_payment_status(ref, "failed", raw={"type": event_type, "object": data_object})
            except Exception:
                pass
        if tenant_key:
            try:
                await set_tenant_inactive(tenant_key)
            except Exception:
                pass

    # Stripe espera apenas um 2xx para considerar entregue
    return {"ok": True}
