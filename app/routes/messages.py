# app/routes/messages.py
import httpx
from fastapi import APIRouter, Depends, HTTPException
from app.routes.deps import get_uazapi_ctx

router = APIRouter()

def _uaz(ctx):
    base = f"https://{ctx['host']}"
    headers = {"token": ctx["token"]}
    return base, headers

def _normalize_items(data):
    """Garante { items: [...] } sem perder mídia."""
    if isinstance(data, dict):
        for key in ("items", "messages", "data", "results"):
            val = data.get(key)
            if isinstance(val, list):
                return {"items": val}
        return {"items": []}
    if isinstance(data, list):
        return {"items": data}
    return {"items": []}

async def _req(cli: httpx.AsyncClient, method: str, url: str, headers: dict, json=None, params=None):
    if method == "GET":
        return await cli.get(url, headers=headers, params=params)
    return await cli.post(url, headers=headers, json=json)

@router.post("/messages")
async def get_messages(body: dict, ctx=Depends(get_uazapi_ctx)):
    """
    Busca mensagens de um chat com fallback agressivo.
    body: { chatid | wa_chatid, limit?, offset?, sort? }
    """
    chatid = (body.get("chatid") or body.get("wa_chatid") or "").strip()
    if not chatid:
        raise HTTPException(status_code=400, detail="chatid (ou wa_chatid) é obrigatório")

    limit  = body.get("limit", 100)
    offset = body.get("offset", 0)
    sort   = body.get("sort", "-messageTimestamp")

    base, headers = _uaz(ctx)
    payload = {"chatid": chatid, "limit": limit, "offset": offset, "sort": sort}
    qparams = {"chatid": chatid, "limit": limit, "offset": offset, "sort": sort}

    # Tentativas ampliadas (POST e GET; snake e CamelCase)
    attempts: list[tuple[str, str, dict | None, dict | None]] = [
        # POST mais prováveis
        ("POST", f"{base}/chat/messages", payload, None),
        ("POST", f"{base}/messages/find", payload, None),
        ("POST", f"{base}/messages/search", payload, None),
        ("POST", f"{base}/message/find", payload, None),
        ("POST", f"{base}/chat/findMessages", payload, None),
        ("POST", f"{base}/chat/getMessages", payload, None),
        ("POST", f"{base}/chat/GetMessages", payload, None),
        ("POST", f"{base}/chat/GetChatMessages", payload, None),
        ("POST", f"{base}/chat/getChatMessages", payload, None),
        ("POST", f"{base}/chat/getHistory", payload, None),
        ("POST", f"{base}/chat/GetHistory", payload, None),
        ("POST", f"{base}/messages", payload, None),

        # GET equivalentes
        ("GET",  f"{base}/chat/messages", None, qparams),
        ("GET",  f"{base}/messages", None, qparams),
        ("GET",  f"{base}/chat/getMessages", None, qparams),
        ("GET",  f"{base}/chat/GetMessages", None, qparams),
        ("GET",  f"{base}/chat/getChatMessages", None, qparams),
        ("GET",  f"{base}/chat/GetChatMessages", None, qparams),
        ("GET",  f"{base}/chat/getHistory", None, qparams),
        ("GET",  f"{base}/chat/GetHistory", None, qparams),
    ]

    tried = []
    last_status = 502
    last_text = "Nenhuma rota UAZAPI funcionou"

    async with httpx.AsyncClient(timeout=30) as cli:
        for method, url, json_payload, params in attempts:
            tried.append({"method": method, "url": url})
            r = await _req(cli, method, url, headers, json=json_payload, params=params)
            last_status, last_text = r.status_code, r.text

            if 200 <= r.status_code < 300:
                try:
                    data = r.json()
                except Exception:
                    raise HTTPException(status_code=502, detail="Resposta inválida da UAZAPI ao buscar mensagens")
                return _normalize_items(data)

            # 404/405 → tenta próxima
            if r.status_code in (404, 405):
                continue

            # outros erros: devolve direto
            raise HTTPException(status_code=r.status_code, detail={"upstream": last_text, "tried": tried})

    # nenhuma rota bateu: devolve 404 com diagnóstico
    raise HTTPException(status_code=404, detail={"message": "Not Found em todas rotas de mensagens", "tried": tried, "upstream": last_text})
