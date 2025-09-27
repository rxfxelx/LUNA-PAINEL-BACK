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
    # mark_paid  # (não utilizado; mantido comentado para evitar linter/ImportError)
)
# A integração legada GetNet foi removida; cobrança agora ocorre apenas via Stripe.

router = APIRouter()


# ------------------------ helpers ------------------------
def _env_list(name: str) -> list[str]:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return []
    return [x.strip() for x in raw.split(",") if x.strip()]


def _is_admin_bypass(user: Dict[str, Any]) -> bool:
    """
    Bypass para contas administradoras. Ativa caso QUALQUER uma das listas case:
      - ADMIN_BYPASS_EMAILS  (com base em user['email'] ou user['user_email'])
      - ADMIN_BYPASS_HOSTS   (com base em user['host'])
      - ADMIN_BYPASS_TOKENS  (com base em user['token'] ou user['instance_token'])
    """
    emails = set(x.lower() for x in _env_list("ADMIN_BYPASS_EMAILS"))
    hosts = set(_env_list("ADMIN_BYPASS_HOSTS"))
    toks = set(_env_list("ADMIN_BYPASS_TOKENS"))

    email = (user.get("email") or user.get("user_email") or "").lower().strip()
    host = (user.get("host") or "").strip()
    token = (user.get("token") or user.get("instance_token") or "").strip()

    if email and email in emails:
        return True
    if host and host in hosts:
        return True
    if token and token in toks:
        return True
    return False


def _billing_key_from_user(user: Dict[str, Any]) -> str:
    """
    Monta um ``billing_key`` a partir do JWT do usuário.  Este helper suporta
    tanto tokens de instância (quando ``token`` e ``host`` estão presentes no
    payload) quanto tokens de usuário (quando ``sub`` inicia com ``user:`` ou
    quando existe um ``email`` válido).  Para tokens de instância o
    comportamento original é preservado, utilizando :func:`make_billing_key`.

    Para tokens de usuário, o ``billing_key`` segue um dos formatos abaixo:

    * ``uid:<id>`` – quando o claim ``sub`` possui o prefixo ``user:`` e
      conseguirmos extrair o identificador numérico.
    * ``ue:<hash>`` – quando apenas o e‑mail está disponível.  O hash é
      calculado com HMAC-SHA256 a partir do e‑mail e do ``BILLING_SALT`` para
      evitar expor diretamente o endereço.

    Caso nenhum dado suficiente esteja presente no JWT, a função aborta com
    ``HTTP 401``.
    """
    token = (user.get("token") or user.get("instance_token") or "").strip()
    host = (user.get("host") or "").strip()
    iid = user.get("instance_id")

    # Preferimos billing por instância quando token e host existem (compat UAZAPI).
    if token and host:
        try:
            return make_billing_key(token, host, iid)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Erro ao gerar billing_key: {e}")

    # JWT de usuário: tenta 'sub' no formato 'user:<id>'.
    sub = str(user.get("sub") or "")
    if sub.startswith("user:"):
        uid = sub.split(":", 1)[1]
        if uid:
            return f"uid:{uid}"

    # Fallback: usa e-mail com HMAC-SHA256 (BILLING_SALT) para não expor PII.
    email = (user.get("email") or user.get("user_email") or "").strip().lower()
    if email:
        import hashlib
        import hmac

        salt = (os.getenv("BILLING_SALT") or "luna").encode()
        digest = hmac.new(salt, email.encode(), hashlib.sha256).hexdigest()
        return f"ue:{digest}"

    # Sem dados suficientes.
    raise HTTPException(status_code=401, detail="JWT inválido: sem token/host/email/sub")


def _safe_get_status(bkey: str) -> Dict[str, Any]:
    """Lê o status do billing sem deixar a rota explodir em 500."""
    try:
        return get_status(bkey)
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Billing indisponível: {e}")


# ------------------------ modelos ------------------------
class CheckoutLinkIn(BaseModel):
    return_url: Optional[str] = None  # (sem uso direto aqui; mantido para compat.)


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
    Admin (bypass): sempre ativo sem tocar no billing.
    """
    if _is_admin_bypass(user):
        return {
            "ok": True,
            "billing_key": None,
            "status": {"active": True, "plan": "admin", "admin_bypass": True},
        }

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
            # mantém idempotência mesmo se o serviço falhar; status é lido abaixo
            pass

    st = _safe_get_status(bkey)
    return {"ok": True, "billing_key": bkey, "status": st}


@router.get("/status")
async def billing_status(user=Depends(get_current_user)) -> Dict[str, Any]:
    """
    Retorna o status atual do billing (active, days_left, trial_ends_at, paid_until, plan...).
    Admin (bypass): sempre ativo.
    """
    if _is_admin_bypass(user):
        return {
            "ok": True,
            "billing_key": None,
            "status": {"active": True, "plan": "admin", "admin_bypass": True},
        }

    bkey = _billing_key_from_user(user)
    st = _safe_get_status(bkey)
    return {"ok": True, "billing_key": bkey, "status": st}

# Observação: cobranca via Stripe → ver routes/pay_stripe.py
