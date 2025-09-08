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
from app.pay.getnet_client import GetNetClient  # import here to avoid circular import

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

    # Preferimos billing por instância quando token e host existem.  Isso
    # mantém compatibilidade com a cobrança antiga baseada na UAZAPI.
    if token and host:
        try:
            return make_billing_key(token, host, iid)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Erro ao gerar billing_key: {e}")

    # Caso não exista token/host, tratamos como JWT de usuário.  Tentamos
    # extrair o id numérico do claim 'sub' (formato 'user:<id>').
    sub = str(user.get("sub") or "")
    if sub.startswith("user:"):
        uid = sub.split(":", 1)[1]
        if uid:
            return f"uid:{uid}"

    # Como fallback, usamos o e‑mail.  Quando há e‑mail disponível, geramos um
    # digest HMAC‐SHA256 com o BILLING_SALT para não armazenar o endereço
    # original como chave.  Isso garante unicidade e evita vazamento de PII.
    email = (user.get("email") or user.get("user_email") or "").strip().lower()
    if email:
        import hashlib
        import hmac
        import os
        salt = (os.getenv("BILLING_SALT") or "luna").encode()
        digest = hmac.new(salt, email.encode(), hashlib.sha256).hexdigest()
        return f"ue:{digest}"

    # Se não conseguimos extrair nenhuma informação, negamos a solicitação.
    raise HTTPException(status_code=401, detail="JWT inválido: sem token/host/email/sub")


def _safe_get_status(bkey: str) -> Dict[str, Any]:
    """
    Lê o status do billing sem deixar a rota explodir em 500.
    """
    try:
        return get_status(bkey)
    except Exception as e:
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


@router.post("/checkout-link")
async def checkout_link(body: CheckoutLinkIn, user=Depends(get_current_user)) -> Dict[str, Any]:
    """
    Gera a URL de checkout para o cliente atual.

    Este endpoint suporta dois modos de operação:

    1. **Modo legado**: se a variável de ambiente ``GETNET_CHECKOUT_BASE`` estiver
       definida, a URL de checkout será construída concatenando o valor dessa
       base com o ``billing_key`` do usuário e um retorno opcional.  Esse
       comportamento preserva a funcionalidade existente sem requerer
       autenticação adicional.
    2. **Integração real GetNet**: quando ``GETNET_CHECKOUT_BASE`` não está
       definida, utiliza‐se a classe :class:`GetNetClient` para requisitar um
       link de pagamento via API da GetNet.  A transação é identificada pelo
       ``billing_key`` gerado a partir do token e host do usuário.  A URL de
       retorno e a de notificação podem ser personalizadas através das
       variáveis ``GETNET_RETURN_BASE``, ``PAY_RETURN_URL`` e
       ``GETNET_NOTIFY_URL``.

    Admin (bypass): usuários marcados como administradores não necessitam de
    checkout; nesse caso a função retorna ``about:blank``.
    """
    # Se for usuário com bypass, não há necessidade de cobrança.
    if _is_admin_bypass(user):
        return {
            "ok": True,
            "url": "about:blank",
            "ref": None,
            "admin_bypass": True,
        }

    # Calcula a chave de cobrança (billing_key) a partir do JWT.
    bkey = _billing_key_from_user(user)

    # Caso o base legado esteja definido, mantém comportamento anterior.
    checkout_base = os.getenv("GETNET_CHECKOUT_BASE")
    if checkout_base:
        # URL de retorno: prioriza valor enviado pelo corpo; senão lê PAY_RETURN_URL
        # ou cai para PUBLIC_BASE_URL/pagamentos/getnet.
        ret = body.return_url or os.getenv("PAY_RETURN_URL")
        if not ret:
            pb = (os.getenv("PUBLIC_BASE_URL") or "").rstrip("/")
            ret = f"{pb}/pagamentos/getnet/"
        url = f"{checkout_base}?ref={bkey}&return_url={ret}"
        return {"ok": True, "url": url, "ref": bkey}

    # Modo API Real: utiliza GetNetClient para obter link.
    try:
        client = GetNetClient()
        # Extrai e‑mail do usuário do JWT (pode estar em 'email' ou 'user_email').
        email = (user.get("email") or user.get("user_email") or "").strip()
        if not email:
            raise HTTPException(status_code=400, detail="E‑mail ausente no token do usuário")
        # Valor em centavos do plano.  Lê da env LUNA_PRICE_CENTS ou usa 34990.
        amount_cents = int(os.getenv("LUNA_PRICE_CENTS") or 34990)
        # Nome do plano usado na descrição.  Permite customização via LUNA_PLAN_NAME.
        plan_name = os.getenv("LUNA_PLAN_NAME", "Luna AI")
        description = f"Assinatura {plan_name}"
        # Base pública da aplicação para compor URLs de retorno/notificação.
        public_base = (os.getenv("PUBLIC_BASE_URL") or "").rstrip("/")
        # Define URL de retorno.  Prioriza body.return_url, depois env GETNET_RETURN_BASE.
        return_base = (os.getenv("GETNET_RETURN_BASE") or f"{public_base}/pagamentos/getnet/sucesso").rstrip("/")
        return_url = body.return_url or f"{return_base}?ref={bkey}"
        # Define URL de notificação (webhook).  Usa GETNET_NOTIFY_URL ou
        # cai para PUBLIC_BASE_URL/api/pay/getnet/webhook.
        notify_url = (os.getenv("GETNET_NOTIFY_URL") or f"{public_base}/api/pay/getnet/webhook").rstrip("/")
        # Chama a GetNet para gerar a URL de pagamento.
        ret = await client.create_checkout(
            amount_cents=amount_cents,
            customer_email=email,
            reference_id=bkey,
            return_url=return_url,
            notify_url=notify_url,
            description=description,
            metadata={"billing_key": bkey},
        )
        return {"ok": True, "url": ret["payment_url"], "ref": ret["reference_id"]}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Falha ao iniciar checkout: {e}")


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
        raise HTTPException(status_code=503, detail=f"Falha ao aplicar pagamento: {e}")

    return {"ok": True}
