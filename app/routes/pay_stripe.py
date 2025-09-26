"""
Stripe payment integration routes for Luna.

This module implements a minimal integration with Stripe Checkout for
recurring subscription payments.  Instead of interacting directly with
credit card data (as the previous GetNet integration did), Stripe
provides a hosted checkout page.  Your backend only needs to create a
Checkout Session and redirect the user to the ``session.url``.  When
payments succeed or fail, Stripe sends webhook events that we use to
update our own billing records.  For more details on how Stripe
Checkout works see the official documentation, which describes that
you create a checkout session server‑side, redirect the customer to
Stripe, and then fulfill the order via a webhook upon receiving the
``checkout.session.completed`` or ``invoice.paid`` event【436761317421081†L145-L152】.
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
# The following environment variables must be set in your deployment to enable
# Stripe payments:
#
#   STRIPE_SECRET_KEY      – your Stripe secret API key
#   STRIPE_PRICE_ID        – the price ID for your subscription plan
#   STRIPE_WEBHOOK_SECRET  – signing secret for webhook verification (optional in dev)
#   PUBLIC_BASE_URL        – base URL of your front‑end (used to compose return URLs)
#   STRIPE_RETURN_BASE     – override for success page (defaults to PUBLIC_BASE_URL/pagamentos/stripe/sucesso)
#   STRIPE_CANCEL_BASE     – override for cancel/error page (defaults to PUBLIC_BASE_URL/pagamentos/stripe/cancelado)
#   STRIPE_NOTIFY_URL      – override for webhook endpoint (defaults to PUBLIC_BASE_URL/api/pay/stripe/webhook)
#   LUNA_PRICE_CENTS       – price in cents used to record the amount locally
#
# All parameters other than STRIPE_SECRET_KEY and STRIPE_PRICE_ID are optional.
# If required variables are missing, the checkout route will return HTTP 500.

STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "").strip()
STRIPE_PRICE_ID = os.getenv("STRIPE_PRICE_ID", "").strip()
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "").strip()

stripe.api_key = STRIPE_SECRET_KEY or None

PUBLIC_BASE = (os.getenv("PUBLIC_BASE_URL") or "").rstrip("/")
RETURN_BASE = (os.getenv("STRIPE_RETURN_BASE") or f"{PUBLIC_BASE}/pagamentos/stripe/sucesso").rstrip("/")
CANCEL_BASE = (os.getenv("STRIPE_CANCEL_BASE") or f"{PUBLIC_BASE}/pagamentos/stripe/cancelado").rstrip("/")
NOTIFY_URL = (os.getenv("STRIPE_NOTIFY_URL") or f"{PUBLIC_BASE}/api/pay/stripe/webhook").rstrip("/")

# Default plan price (for storing in our own database).  This is only used
# locally; Stripe uses the price defined by STRIPE_PRICE_ID when creating
# the session.  We default to the same 34990 cents used by GetNet if
# LUNA_PRICE_CENTS is not defined.
DEFAULT_PRICE_CENTS = int(os.getenv("LUNA_PRICE_CENTS") or 34990)


# ---------------------------- Request/Response models -------------------------
class CheckoutIn(BaseModel):
    """
    Input payload for creating a checkout session.

    ``email`` – customer's email address.  Required by Stripe to prefill the
                checkout page and link the subscription.
    ``plan`` – identifier of the plan within our application.  This value is
               stored in the metadata sent to Stripe so it can be retrieved
               during webhook processing.
    ``amount_cents`` – price of the plan in cents.  This is used only to
                       record the pending payment locally; Stripe charges
                       according to ``STRIPE_PRICE_ID``.  Defaults to the
                       value configured via ``LUNA_PRICE_CENTS`` or 34990.
    ``tenant_key`` – optional tenant identifier.  If not provided, the
                    customer's email is used.
    """
    email: EmailStr
    plan: str = Field(default="luna_base")
    amount_cents: int = Field(default=DEFAULT_PRICE_CENTS)
    tenant_key: Optional[str] = Field(default=None)


class CheckoutOut(BaseModel):
    """
    Response returned after creating a checkout session.

    ``ref`` – unique reference for this payment in our system.  It is also
             stored as the ``client_reference_id`` and metadata in Stripe so
             that webhook events can be mapped back to our records.
    ``url`` – Stripe hosted URL to which the customer should be redirected.
    """
    ref: str
    url: str


# ---------------------------- Helper functions -------------------------------
def _build_success_url(ref: str) -> str:
    """Compose a success URL using the configured RETURN_BASE and ref."""
    return f"{RETURN_BASE}?ref={ref}"


def _build_cancel_url(ref: str) -> str:
    """Compose a cancel URL using the configured CANCEL_BASE and ref."""
    return f"{CANCEL_BASE}?ref={ref}"


# ---------------------------- Checkout endpoints -----------------------------
@router.post("/checkout", response_model=CheckoutOut)
async def create_checkout(body: CheckoutIn) -> CheckoutOut:
    """
    Initiate a new Stripe Checkout session for a subscription.

    The backend first records a pending payment in our database via
    ``create_pending_payment`` with status ``pending``.  It then calls
    ``stripe.checkout.Session.create`` to obtain a URL hosted by Stripe.
    The ``client_reference_id`` and ``metadata`` are used to store our
    internal reference so that we can correlate webhook events back to this
    record【193457525688558†L105-L112】.  Any error during this process will cause
    the request to fail with HTTP 503.
    """
    # Fail fast if Stripe is not properly configured
    if not STRIPE_SECRET_KEY or not STRIPE_PRICE_ID:
        raise HTTPException(status_code=500, detail="Stripe não configurado.")

    # Generate our own reference ID and tenant key
    ref = f"st_{uuid.uuid4().hex}"
    tenant_key = body.tenant_key or str(body.email)

    # Record a pending payment in our own database.  We perform this in a
    # try/except because we do not want to block the customer if our DB is
    # temporarily unavailable.  The raw field is populated later in the
    # webhook once we have full Stripe data.
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
        # In case of failure we still proceed; pending payments can be
        # reconciled later.
        pass

    # Attempt to create the Stripe Checkout Session
    try:
        session = stripe.checkout.Session.create(
            mode="subscription",
            line_items=[{"price": STRIPE_PRICE_ID, "quantity": 1}],
            success_url=_build_success_url(ref),
            cancel_url=_build_cancel_url(ref),
            client_reference_id=ref,
            customer_email=str(body.email),
            # Store metadata at both the session and subscription levels so
            # that we can retrieve it in webhook events.  Subscription
            # metadata is propagated to invoices and subscription objects.
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

    # Update status to pending and record minimal info about the session
    try:
        await update_payment_status(ref, "pending", raw={"session_id": session.id})
    except Exception:
        # Ignore errors – they can be reconciled later
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
    Convenience endpoint to generate a checkout session via GET.

    This mirrors the behaviour of the original GetNet integration: if the
    ``email`` parameter is omitted we generate an anonymous address.  All
    parameters are forwarded to ``create_checkout``.  Using a GET request
    allows simple redirection from a static payment page in the front‑end.
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
    Stripe webhook endpoint.

    Stripe sends various events to this endpoint.  We verify the signature
    using ``STRIPE_WEBHOOK_SECRET`` (if configured) and then handle relevant
    events.  The most important events are ``invoice.paid`` (indicating the
    first or subsequent subscription invoice has been successfully paid),
    ``invoice.payment_failed`` (payment failure) and ``customer.subscription.deleted``
    (subscription cancelled).  For paid invoices we mark the payment as paid
    and activate the tenant for one month.  For failed payments we mark the
    payment as failed.  When a subscription is deleted we also deactivate the
    tenant.  All other events are ignored.
    """
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")
    event: Any
    # Verify the signature if a secret is provided; allow unsigned payloads in dev
    if STRIPE_WEBHOOK_SECRET:
        try:
            event = stripe.Webhook.construct_event(
                payload, sig_header, STRIPE_WEBHOOK_SECRET
            )
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Webhook signature verification failed: {e}")
    else:
        try:
            # Fallback: parse the JSON without signature verification
            event = json.loads(payload)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Invalid payload: {e}")

    event_type = event.get("type")
    data_object = event.get("data", {}).get("object", {})

    # Handle invoice paid events
    if event_type == "invoice.paid":
        # The invoice contains the subscription id; we retrieve the subscription
        # to access the metadata with our reference id and tenant key.
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
                        # Extend the tenant by one month (30 days).  Stripe
                        # subscriptions are recurring monthly, so this keeps
                        # the tenant active until the next invoice is paid.
                        await ensure_tenant_active(
                            tenant_key=tenant_key,
                            email=email,
                            plan=plan,
                            months=1,
                        )
                    except Exception:
                        pass
            except Exception:
                pass

    # Handle invoice payment failures
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

    # Handle subscription cancellations
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

    # Return a simple acknowledgement.  Stripe expects a 2xx response to
    # consider the event delivered successfully.
    return {"ok": True}
