"""
Stripe payment integration routes for Luna.

This module implements a minimal integration with Stripe Checkout for
recurring subscription payments. Instead of interacting directly with
credit card data (as the previous GetNet integration did), Stripe
provides a hosted checkout page. Your backend only needs to create a
Checkout Session and redirect the user to the ``session.url``. When
payments succeed or fail, Stripe sends webhook events that we use to
update our own billing records.

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
from typing import Any, Dict, Optional

import stripe  # type: ignore
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field

from app.models_billing import (
    create_pending_payment,
    update_payment_status,
    ensure_tenant_active,
    set_tenant_inactive,
)

# Initialise the router
router = APIRouter()

# -----------------------------------------------------------------------------
# Stripe configuration
#
# Env vars esperadas:
#   STRIPE_SECRET_KEY      – secret key da Stripe (obrigatório)
#   STRIPE_PRICE_ID        – price (recorrente) do plano no Stripe (obrigatório)
#   STRIPE_WEBHOOK_SECRET  – secret para validar a assinatura do webhook (opcional em dev)
#   PUBLIC_BASE_URL        – base pública do front (para compor success/cancel)
#   STRIPE_RETURN_BASE     – (opcional) override do success
#   STRIPE_CANCEL_BASE     – (opcional) override do cancel
#   STRIPE_NOTIFY_URL      – (opcional) URL pública do webhook (apenas informativa; não usada no código)
#   LUNA_PRICE_CENTS       – valor em centavos usado no registro local (não afeta Stripe)
#
# Observação: o endpoint público REAL do webhook é o do backend:
#   .../api/pay/stripe/webhook
# Configure isso no Dashboard da Stripe apontando para o domínio público do backend.

STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "").strip()
STRIPE_PRICE_ID = os.getenv("STRIPE_PRICE_ID", "").strip()
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "").strip()

stripe.api_key = STRIPE_SECRET_KEY or None

PUBLIC_BASE = (os.getenv("PUBLIC_BASE_URL") or "").rstrip("/")
# Retornos apontam para a RAIZ do site (páginas estáticas /pagamentos foram removidas).
RETURN_BASE = (os.getenv("STRIPE_RETURN_BASE") or f"{PUBLIC_BASE}/").rstrip("/")
CANCEL_BASE = (os.getenv("STRIPE_CANCEL_BASE") or f"{PUBLIC_BASE}/").rstrip("/")
# Apenas referência para você configurar na Stripe; não é usado diretamente abaixo.
NOTIFY_URL = (os.getenv("STRIPE_NOTIFY_URL") or f"{PUBLIC_BASE}/api/pay/stripe/webhook").rstrip("/")

DEFAULT_PRICE_CENTS = int(os.getenv("LUNA_PRICE_CENTS") or 34990)


# ---------------------------- Request/Response models -------------------------
class CheckoutIn(BaseModel):
    """
    Input payload for creating a checkout session.

    email – e‑mail do cliente (prefill e vínculo da assinatura).
    plan – identificador interno do plano (vai nas metadatas).
    amount_cents – valor usado apenas no registro local (Stripe cobra pelo PRICE).
    tenant_key – chave do tenant na nossa base; se não vier, usamos o e‑mail.
    """
    email: EmailStr
    plan: str = Field(default="luna_base")
    amount_cents: int = Field(default=DEFAULT_PRICE_CENTS)
    tenant_key: Optional[str] = Field(default=None)


class CheckoutOut(BaseModel):
    """
    Response for checkout creation.

    ref – referência interna do pagamento.
    url – URL hosted do Stripe para redirecionar o cliente.
    """
    ref: str
    url: str


# ---------------------------- Helper functions -------------------------------
def _build_success_url(ref: str) -> str:
    return f"{RETURN_BASE}?ref={ref}"


def _build_cancel_url(ref: str) -> str:
    return f"{CANCEL_BASE}?ref={ref}"


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
            # `payload` é bytes; a SDK aceita bytes aqui
            event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Webhook signature verification failed: {e}")
    else:
        try:
            # Em dev, quando não há secret, precisamos decodificar bytes -> str
            event = json.loads(payload.decode("utf-8"))
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Invalid payload: {e}")

    event_type = event.get("type")
    data_object = event.get("data", {}).get("object", {})  # invoice/subscription/etc.

    # ---------------- invoice.paid ----------------
    if event_type == "invoice.paid":
        subscription_id = data_object.get("subscription")
        if subscription_id:
            try:
                sub = stripe.Subscription.retrieve(subscription_id)
                meta = sub.metadata or {}
                ref = meta.get("reference_id")
                tenant_key = meta.get("tenant_key") or ref
                plan = meta.get("plan", "luna_base")
                email = meta.get("email")
                if ref:
                    try:
                        await update_payment_status(ref, "paid", raw=event)
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
                meta = sub.metadata or {}
                ref = meta.get("reference_id")
                if ref:
                    try:
                        await update_payment_status(ref, "failed", raw=event)
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
                await update_payment_status(ref, "failed", raw=event)
            except Exception:
                pass
        if tenant_key:
            try:
                await set_tenant_inactive(tenant_key)
            except Exception:
                pass

    # Stripe espera apenas um 2xx para considerar entregue
    return {"ok": True}
