# app/routes/chats.py
import httpx
from fastapi import APIRouter, Depends, HTTPException
from app.routes.deps import get_uazapi_ctx

router = APIRouter()

def _uaz(ctx):
    base = f"https://{ctx['host']}"
    headers = {"token": ctx["token"]}
    return base, headers

def _normalize_items(resp_json):
    """
    Sempre retorna { items: [...] } ao front.
    """
    if isinstance(resp_json, dict):
        if "items" in resp_json and isinstance(resp_json["items"], list):
            return {"items": resp_json["items"]}
        for key in ("data", "results", "chats"):
            val = resp_json.get(key)
            if isinstance(val, list):
                return {"items": val}
        return {"items": []}
    if isinstance(resp_json, list):
        return {"items": resp_json}
    return {"items": []}

@router.post("/chats")
async def find_chats(body: dict | None = None, ctx=Depends(get_uazapi_ctx)):
    """
    Proxy para UAZAPI /chat/find.
    Se o front não mandar body, usamos um default seguro.
    """
    base, headers = _uaz(ctx)
    url = f"{base}/chat/find"

    if not body or not isinstance(body, dict):
        body = {"operator": "AND", "sort": "-wa_lastMsgTimestamp", "limit": 50, "offset": 0}

    async with httpx.AsyncClient(timeout=30) as cli:
        r = await cli.post(url, json=body, headers=headers)

    if r.status_code >= 400:
        raise HTTPException(status_code=r.status_code, detail=r.text)

    try:
        data = r.json()
    except Exception:
        raise HTTPException(status_code=502, detail="Resposta inválida da UAZAPI em /chat/find")

    return _normalize_items(data)

@router.get("/labels")
async def get_labels(ctx=Depends(get_uazapi_ctx)):
    base, headers = _uaz(ctx)
    url = f"{base}/labels"
    async with httpx.AsyncClient(timeout=30) as cli:
        r = await cli.get(url, headers=headers)
    if r.status_code >= 400:
        raise HTTPException(status_code=r.status_code, detail=r.text)
    return r.json()

@router.get("/status")
async def instance_status(ctx=Depends(get_uazapi_ctx)):
    base, headers = _uaz(ctx)
    url = f"{base}/instance/status"
    async with httpx.AsyncClient(timeout=30) as cli:
        r = await cli.get(url, headers=headers)
    if r.status_code >= 400:
        raise HTTPException(status_code=r.status_code, detail=r.text)
    return r.json()
