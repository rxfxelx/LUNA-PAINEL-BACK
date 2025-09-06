# app/routes/deps_billing.py
from __future__ import annotations

import os
from typing import Optional, Dict, Any

from fastapi import Depends, HTTPException
from app.auth import get_current_user
from app.services.billing import make_billing_key, get_status, ensure_trial


async def require_active_tenant(user=Depends(get_current_user)) -> Dict[str, Any]:
    """
    Gate de billing para rotas operacionais.

    - Identifica o tenant pelo JWT (token/host/instance_id) e monta o billing_key.
    - Se não existir registro, tenta iniciar trial e reconsulta.
    - Retorna 402 (Payment Required) quando a assinatura não está ativa.
    - Se DISABLE_BILLING=1, libera o acesso (útil para hotfix/diagnóstico).

    Retorno (quando ativo):
      { "billing_key": <str>, "status": <dict>, "user": <dict> }
    """
    # Bypass opcional (p/ emergência/diagnóstico)
    if os.getenv("DISABLE_BILLING", "0") == "1":
        return {
            "billing_key": "disabled",
            "status": {"active": True, "bypassed": True},
            "user": user,
        }

    token = (user.get("token") or user.get("instance_token") or "").strip()
    host = (user.get("host") or "").strip()
    iid: Optional[str] = user.get("instance_id")

    if not token or not host:
        raise HTTPException(status_code=401, detail="JWT inválido: sem token/host")

    # Cria a chave de cobrança do tenant
    try:
        bkey = make_billing_key(token, host, iid)
    except Exception as e:
        # Qualquer erro aqui não deve virar 500 para o cliente
        raise HTTPException(
            status_code=402,
            detail={"message": f"Erro ao gerar billing_key: {e}", "require_payment": True},
        )

    # Lê status atual
    try:
        st = get_status(bkey)
    except Exception as e:
        raise HTTPException(
            status_code=402,
            detail={"message": f"Billing indisponível (get_status): {e}", "require_payment": True},
        )

    # Se ainda não existir, tenta abrir trial e reconsulta
    if not st.get("exists"):
        try:
            ensure_trial(bkey)
        except Exception:
            # segue mesmo se falhar; reconsulta abaixo para devolver estado atual
            pass
        try:
            st = get_status(bkey)
        except Exception as e:
            raise HTTPException(
                status_code=402,
                detail={"message": f"Billing indisponível (re-get_status): {e}", "require_payment": True},
            )

    if st.get("active"):
        return {"billing_key": bkey, "status": st, "user": user}

    # 402 Payment Required comunica claramente ao front que precisa pagar
    raise HTTPException(
        status_code=402,
        detail={
            "message": (
                "Sua avaliação terminou ou a assinatura está inativa. "
                "Acesse a aba Pagamentos para continuar."
            ),
            "require_payment": True,
            "days_left": int(st.get("days_left") or 0),
            "trial_ends_at": st.get("trial_ends_at"),
            "paid_until": st.get("paid_until"),
            "plan": st.get("plan"),
        },
    )


# -----------------------------------------------------------------------------
# Wrapper "soft" opcional:
# - Mantém 401/402/403 como estão;
# - Qualquer outra falha inesperada NÃO derruba a rota (retorna None).
#   Útil para endpoints que podem funcionar mesmo se o billing oscilar.
# -----------------------------------------------------------------------------
async def require_active_tenant_soft(
    user=Depends(get_current_user),
) -> Optional[Dict[str, Any]]:
    try:
        return await require_active_tenant(user)  # reaproveita o guard estrito
    except HTTPException as e:
        if e.status_code in (401, 402, 403):
            # Erros esperados continuam bloqueando a rota
            raise
        # Falha inesperada de infra/billing: não converter em 500
        return None
    except Exception:
        # Failsafe final
        return None
