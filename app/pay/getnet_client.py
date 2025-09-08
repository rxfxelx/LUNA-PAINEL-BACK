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
        """
        Initialise the GetNet client using environment variables.  The following
        variables can be configured at runtime:

        - **GETNET_ENV** – either ``sandbox`` or ``prod``.  Determines which base
          URL will be used.  Defaults to ``sandbox``.
        - **GETNET_SELLER_ID** – the seller identifier supplied by GetNet.  This
          value is required when creating payment orders.  It is optional for
          simple test harnesses but should be provided in production.
        - **GETNET_CLIENT_ID** – OAuth client ID provided by GetNet.  Required
          for token generation.
        - **GETNET_CLIENT_SECRET** – OAuth client secret provided by GetNet.
          Required for token generation.
        - **GETNET_CHECKOUT_URL** – absolute URL to a custom checkout endpoint.
          When set, calls to :meth:`create_checkout` will be forwarded to this
          URL instead of the built in Digital Platform endpoints.  This can be
          useful if you wish to proxy requests through your own middleware or if
          GetNet provisions a bespoke checkout service for your application.
        - **GETNET_CHECKOUT_ENDPOINT** – relative path to the Digital Platform
          endpoint responsible for creating a payment link or checkout.  When
          unset, the client defaults to ``/v1/payments/link`` which is
          compatible with GetNet's GetPay and recurring APIs.  You should
          consult the GetNet documentation for your specific product to ensure
          that the payload matches the expected contract.
        - **PUBLIC_BASE_URL** – base URL of your own application.  Used only for
          mock mode to build redirect URLs.

        Additional environment variables may be added in the future.
        """

        self.env = (os.getenv("GETNET_ENV") or "sandbox").strip().lower()
        self.seller_id = (os.getenv("GETNET_SELLER_ID") or "").strip()
        self.client_id = (os.getenv("GETNET_CLIENT_ID") or "").strip()
        self.client_secret = (os.getenv("GETNET_CLIENT_SECRET") or "").strip()
        # Optional override: if set, requests are proxied to this full URL rather
        # than built from the base URL and checkout endpoint.
        self.checkout_url = (os.getenv("GETNET_CHECKOUT_URL") or "").strip()
        # Optional override for the relative path used when constructing the
        # checkout request against the GetNet Digital Platform.  Should start
        # with '/'.  Defaults to '/v1/payments/link'.
        self.checkout_endpoint = (os.getenv("GETNET_CHECKOUT_ENDPOINT") or "/v1/payments/link").strip()
        # Base URL for the Digital Platform.  This is derived from GETNET_ENV
        # unless explicitly overridden via GETNET_BASE_URL (future support).
        if self.env == "prod":
            self.base_url = "https://api.getnet.com.br"
        else:
            # default to sandbox/homologação
            self.base_url = "https://api-homologacao.getnet.com.br"
        self.public_base = (os.getenv("PUBLIC_BASE_URL") or "").rstrip("/")

        # Precompute the basic auth header for token retrieval if client
        # credentials have been provided.  This avoids repeatedly encoding the
        # values for every request.
        if self.client_id and self.client_secret:
            import base64
            raw = f"{self.client_id}:{self.client_secret}".encode()
            self._basic_auth = base64.b64encode(raw).decode()
        else:
            self._basic_auth = ""

    async def _get_access_token(self) -> str:
        """
        Request an OAuth2 access token from GetNet.  The Digital Platform uses
        the client credentials flow with the ``oob`` scope.  See the GetNet
        documentation for additional details.  Raises an exception if the
        credentials are missing or if the request fails.

        Returns:
            str: bearer token that can be used in subsequent API calls.
        """
        if not (self.client_id and self.client_secret):
            raise RuntimeError("Missing GETNET_CLIENT_ID/GETNET_CLIENT_SECRET for token generation")
        # Build request headers.  We use HTTP Basic Auth by concatenating the
        # client ID and secret with a colon and base64 encoding the result.  See
        # the GetNet docs for an example of this process【955860423414041†L96-L116】.
        headers = {
            "authorization": f"Basic {self._basic_auth}",
            "content-type": "application/x-www-form-urlencoded",
        }
        # According to GetNet, the body must include grant_type=client_credentials
        # and scope=oob【955860423414041†L96-L116】.  Adjust these values if your
        # integration requires a different scope.
        data = {
            "scope": "oob",
            "grant_type": "client_credentials",
        }
        token_url = f"{self.base_url}/auth/oauth/v2/token"
        async with httpx.AsyncClient(timeout=30) as cli:
            resp = await cli.post(token_url, headers=headers, data=data)
            # Raise for non-2xx status codes; httpx includes the response text
            # which aids debugging.
            resp.raise_for_status()
            payload = resp.json()
        token = payload.get("access_token") or payload.get("accessToken")
        if not token:
            raise RuntimeError("Token missing from GetNet response")
        return token

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

        # MODO "REAL": integra-se à API da GetNet para gerar uma URL de pagamento.
        # Caso ``GETNET_CHECKOUT_URL`` esteja definido, ele será utilizado como
        # endpoint absoluto para a criação do checkout.  Essa opção permite ao
        # usuário apontar para um serviço próprio que encapsule as chamadas à
        # GetNet.  Se não estiver definido, construímos a URL com base no
        # ambiente (sandbox ou prod) e no ``checkout_endpoint``.  Uma vez
        # determinado o endpoint, obtemos um token OAuth2 e montamos o payload
        # exigido pela API da Digital Platform.  O payload mínimo consiste em
        # informar o valor, a moeda, o número de parcelas e o identificador de
        # referência.  Consulte a documentação de Recorrência/GetPay para
        # ajustar os campos necessários.  Nesta implementação enviamos
        # ``seller_id`` e ``reference`` bem como URLs de retorno e notificação.
        # Caso não seja possível extrair uma URL de pagamento da resposta, o
        # método lança uma exceção.
        try:
            if self.checkout_url:
                # Quando apontar para uma URL externa, assumimos que ela já
                # lida com autenticação.  Envie as informações fornecidas pelo
                # GetNet através do corpo da requisição e retorne qualquer
                # ``payment_url`` ou ``redirect_url`` recebido.
                payload = {
                    "amount_cents": int(amount_cents),
                    "customer_email": customer_email,
                    "reference_id": ref,
                    "return_url": return_url,
                    "notify_url": notify_url,
                    "description": description,
                    "metadata": metadata or {},
                }
                async with httpx.AsyncClient(timeout=30) as cli:
                    resp = await cli.post(self.checkout_url, json=payload)
                    resp.raise_for_status()
                    data = resp.json()
                payment_link = data.get("payment_url") or data.get("redirect_url")
                if not payment_link:
                    raise RuntimeError("Resposta do checkout sem payment_url/redirect_url")
                return {"payment_url": payment_link, "reference_id": ref, "raw": data}

            # Gera token de acesso para autenticar as chamadas seguintes.
            token = await self._get_access_token()
            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            }
            endpoint = f"{self.base_url}{self.checkout_endpoint}"
            # Constrói payload compatível com a API de criação de links de
            # pagamento/assinaturas.  O GetNet espera determinados campos que
            # variam conforme o tipo de integração.  Os campos abaixo são os
            # mínimos recomendados para criar um link de pagamento único.
            # Ajuste ou expanda conforme as necessidades do seu produto.
            payload = {
                "seller_id": self.seller_id or None,
                "reference": ref,
                "amount": {
                    "value": int(amount_cents),
                    "currency": "BRL",
                },
                # Um único item correspondente à assinatura ou serviço.  Este
                # formato é amplamente utilizado em APIs de pagamento.
                "items": [
                    {
                        "name": description,
                        "amount": int(amount_cents),
                        "quantity": 1,
                    }
                ],
                # Informações do comprador.  Se o e‑mail estiver vazio,
                # simplesmente omitimos o campo.  Campos adicionais (como
                # ``document`` ou ``phone``) podem ser acrescentados conforme
                # solicitado pela GetNet.
                "customer": {
                    "email": customer_email,
                },
                # URL para onde o comprador será redirecionado após concluir o
                # pagamento.  A API da GetNet normalmente utiliza o termo
                # ``order_id`` ou ``reference`` para identificar a transação.
                "url_redirect": return_url,
                # Endereço de notificação assíncrona (webhook) para status da
                # transação.  Será chamado pela GetNet sempre que o pagamento
                # mudar de status.
                "url_notification": notify_url,
                # Dados adicionais livres.  São serializados como string; por
                # padrão enviamos o objeto original, mas você pode remover ou
                # alterar conforme a documentação oficial.
                "metadata": metadata or {},
            }
            # Remove chaves com valores vazios (None) para evitar erros de
            # validação no lado da GetNet.
            def _prune(obj: Any) -> Any:
                if isinstance(obj, dict):
                    return {k: _prune(v) for k, v in obj.items() if v is not None}
                elif isinstance(obj, list):
                    return [_prune(x) for x in obj if x is not None]
                return obj
            payload = _prune(payload)
            async with httpx.AsyncClient(timeout=30) as cli:
                resp = await cli.post(endpoint, json=payload, headers=headers)
                resp.raise_for_status()
                data = resp.json()
            # Diversas versões da API retornam a URL de pagamento em campos
            # diferentes.  Os nomes mais comuns são ``payment_url``,
            # ``url_payment``, ``redirect_url`` e ``checkout_url``.  Passamos
            # por eles em ordem e usamos o primeiro disponível.
            payment_link = (
                data.get("payment_url")
                or data.get("url_payment")
                or data.get("redirect_url")
                or data.get("checkout_url")
            )
            if not payment_link:
                raise RuntimeError("Não foi possível obter a URL de pagamento da resposta da GetNet")
            return {"payment_url": payment_link, "reference_id": ref, "raw": data}
        except Exception:
            # Em caso de falha, propagamos a exceção para que a rota FastAPI
            # responda com erro apropriado.  Os consumidores da API podem
            # capturar e lidar com a exceção conforme necessário.
            raise

        # MODO MOCK: quando os dados de integração (cliente e secret) não
        # estiverem configurados, a API retornará sempre um link para o próprio
        # domínio.  Isso facilita o desenvolvimento front‑end sem depender de
        # chamadas reais ao gateway.  O link retorna imediatamente para
        # /pagamentos/getnet/sucesso com o parâmetro ``mock=1`` e a referência
        # gerada.
        mock_url = f"{self.public_base or 'http://localhost:3000'}/pagamentos/getnet/sucesso?ref={ref}&mock=1"
        raw = {"mode": "mock", "env": self.env, "return_url": return_url, "notify_url": notify_url}
        return {"payment_url": mock_url, "reference_id": ref, "raw": raw}
