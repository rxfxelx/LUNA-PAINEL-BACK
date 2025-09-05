# app/pay/getnet_client.py
from __future__ import annotations

import os
import uuid
from typing import Any, Dict, Optional

import httpx


class GetNetClient:
    """
    Client simplificado. Por padrão roda em MODO MOCK (não chama a GetNet)
    até você configurar os ENVs/endpoint da criação de checkout.

    ENVs:
      - GETNET_ENV=sandbox|prod (opcional, só pra log/decisão)
      - GETNET_MERCHANT_ID=... (opcional, futuro uso)
      - GETNET_CLIENT_ID=...   (opcional, futuro uso)
      - GETNET_CLIENT_SECRET=... (opcional, futuro uso)
      - GETNET_CHECKOUT_URL=https://<seu-endpoint-de-criar-checkout> (opcional)
      - PUBLIC_BASE_URL=https://seu-dominio.com.br  (usado no mock)

    Método principal:
      create_checkout(...) -> {"payment_url": str, "reference_id": str, "raw": dict}
    """

    def __init__(self) -> None:
        self.env = (os.getenv("GETNET_ENV") or "sandbox").strip()
        self.merchant_id = (os.getenv("GETNET_MERCHANT_ID") or "").strip()
        self.client_id = (os.getenv("GETNET_CLIENT_ID") or "").strip()
        self.client_secret = (os.getenv("GETNET_CLIENT_SECRET") or "").strip()
        self.checkout_url = (os.getenv("GETNET_CHECKOUT_URL") or "").strip()
        self.public_base = (os.getenv("PUBLIC_BASE_URL") or "").rstrip("/")

    async def create_checkout(
        self,
        *,
        amount_cents: int,
        customer_email: str,
        reference_id: Optional[str] = None,
        return_url: str,
        notify_url: str,
        description: str = "Assinatura Luna IA",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Se GETNET_CHECKOUT_URL estiver configurado, faz POST nele e espera
        um JSON com ao menos "payment_url".
        Caso contrário, retorna um link MOCK que redireciona o usuário
        para a sua própria /pagamentos/getnet/sucesso.
        """
        ref = reference_id or f"gt_{uuid.uuid4().hex}"

        # MODO "REAL" (você pluga aqui a chamada real da GetNet)
        if self.checkout_url:
            payload = {
                "amount_cents": int(amount_cents),
                "customer_email": customer_email,
                "reference_id": ref,
                "return_url": return_url,
                "notify_url": notify_url,
                "description": description,
                "metadata": metadata or {},
                # adicione aqui os campos exigidos pela GetNet
            }
            async with httpx.AsyncClient(timeout=30) as cli:
                r = await cli.post(self.checkout_url, json=payload)
                r.raise_for_status()
                data = r.json()
            url = data.get("payment_url") or data.get("redirect_url") or ""
            if not url:
                raise RuntimeError("Resposta do checkout sem payment_url/redirect_url")
            return {"payment_url": url, "reference_id": ref, "raw": data}

        # MODO MOCK (sem chamar gateway): simula aprovado e volta para sua rota de sucesso
        mock_url = f"{self.public_base or 'http://localhost:3000'}/pagamentos/getnet/sucesso?ref={ref}&mock=1"
        raw = {"mode": "mock", "env": self.env, "return_url": return_url, "notify_url": notify_url}
        return {"payment_url": mock_url, "reference_id": ref, "raw": raw}
