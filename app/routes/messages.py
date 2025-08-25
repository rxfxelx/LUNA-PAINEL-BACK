# app/routes/messages.py
import httpx
from fastapi import APIRouter, Depends, HTTPException
from app.routes.deps import get_uazapi_ctx

router = APIRouter()

def _uaz(ctx):
    base = f"https://{ctx['host']}"
    headers = {"token": ctx["token"]}
    return base, headers

def _normalize_items(resp_json):
    """Sempre devolve { items: [...] } para o front."""
    if isinstance(resp_json, dict):
        for key in ("items", "data", "results", "messages"):
            val = resp_json.get(key)
            if isinstance(val, list):
                return {"items": val}
        return {"items": []}
    if isinstance(resp_json, list):
        return {"items": resp_json}
    return {"items": []}

async def _try(cli, method, url, headers, json=None, params=None):
    if method == "GET":
        r = await cli.get(url, headers=headers, params=params)
    else:
        r = await cli.post(url, headers=headers, json=json)
    return r

@router.post("/messages")
async def get_messages(body: dict, ctx=Depends(get_uazapi_ctx)):
    """
    Busca mensagens de um chat com *fallback* de rotas da UAZAPI.
    Espera body com ao menos: { chatid, limit?, offset?, sort? }
    """
    chatid = (body.get("chatid") or "").strip()
    if not chatid:
        raise HTTPException(status_code=400, detail="chatid é obrigatório")

    limit  = body.get("limit", 100)
    offset = body.get("offset", 0)
    sort   = body.get("sort", "-messageTimestamp")

    base, headers = _uaz(ctx)

    # tentativas (ordem de preferência). Troque/adicione conforme sua UAZAPI
    attempts = [
        # 1) POST /chat/messages (algumas instâncias suportam)
        ("POST", f"{base}/chat/messages", {"chatid": chatid, "limit": limit, "offset": offset, "sort": sort}, None),
        # 2) POST /messages/find
        ("POST", f"{base}/messages/find", {"chatid": chatid, "limit": limit, "offset": offset, "sort": sort}, None),
        # 3) POST /chat/findMessages
        ("POST", f"{base}/chat/findMessages", {"chatid": chatid, "limit": limit, "offset": offset, "sort": sort}, None),
        # 4) GET /chat/messages?chatid=... (algumas usam GET)
        ("GET",  f"{base}/chat/messages", None, {"chatid": chatid, "limit": limit, "offset": offset, "sort": sort}),
        # 5) GET /messages?chatid=...
        ("GET",  f"{base}/messages", None, {"chatid": chatid, "limit": limit, "offset": offset, "sort": sort}),
    ]

    async with httpx.AsyncClient(timeout=30) as cli:
        last_text = ""
        last_status = 502
        for method, url, json_payload, params in attempts:
            r = await _try(cli, method, url, headers, json=json_payload, params=params)
            last_status = r.status_code
            last_text   = r.text

            # 2xx => ok
            if 200 <= r.status_code < 300:
                try:
                    data = r.json()
                except Exception:
                    raise HTTPException(status_code=502, detail="Resposta inválida da UAZAPI ao buscar mensagens")
                return _normalize_items(data)

            # erros que indicam rota errada → tenta próxima
            if r.status_code in (404, 405):
                continue

            # outros erros: devolve direto
            raise HTTPException(status_code=r.status_code, detail=last_text)

    # se todas as tentativas falharem (rotas não suportadas)
    raise HTTPException(status_code=last_status, detail=last_text or "Nenhuma rota de mensagens suportada pela UAZAPI")
