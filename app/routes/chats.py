# app/routes/chats.py
from __future__ import annotations
import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from app.routes.deps import get_uazapi_ctx

router = APIRouter()

def _uaz(ctx):
    base = f"https://{ctx['host']}"
    headers = {"token": ctx["token"]}
    return base, headers

def _normalize_items(resp_json):
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
    Proxy simples p/ /chat/find (usa body do front).
    """
    base, headers = _uaz(ctx)
    url = f"{base}/chat/find"
    if not isinstance(body, dict):
        body = {}
    # defaults melhores
    body.setdefault("operator", "AND")
    body.setdefault("sort", "-wa_lastMsgTimestamp")
    body.setdefault("limit", 200)   # <- antes 50/100
    body.setdefault("offset", 0)

    async with httpx.AsyncClient(timeout=60) as cli:
        r = await cli.post(url, json=body, headers=headers)
    if r.status_code >= 400:
        raise HTTPException(status_code=r.status_code, detail=r.text)

    try:
        data = r.json()
    except Exception:
        raise HTTPException(status_code=502, detail="Resposta inválida da UAZAPI em /chat/find")

    return _normalize_items(data)

@router.get("/chats/all")
async def get_all_chats(
    ctx=Depends(get_uazapi_ctx),
    limit_per_page: int = Query(500, ge=1, le=1000),
    max_total: int = Query(5000, ge=1, le=20000),
    sort: str = Query("-wa_lastMsgTimestamp"),
):
    """
    Busca **todas** as páginas de /chat/find e concatena (até max_total).
    Útil p/ evitar ficar só nos 100.
    """
    base, headers = _uaz(ctx)
    url = f"{base}/chat/find"
    offset = 0
    out = []

    async with httpx.AsyncClient(timeout=90) as cli:
        while len(out) < max_total:
            payload = {
                "operator": "AND",
                "sort": sort,
                "limit": limit_per_page,
                "offset": offset,
            }
            r = await cli.post(url, json=payload, headers=headers)
            if r.status_code >= 400:
                raise HTTPException(status_code=r.status_code, detail=r.text)

            try:
                data = r.json()
            except Exception:
                raise HTTPException(status_code=502, detail="Resposta inválida da UAZAPI em /chat/find")

            items = _normalize_items(data)["items"]
            if not items:
                break
            out.extend(items)
            offset += limit_per_page

            if len(items) < limit_per_page:
                break  # última página

    if len(out) > max_total:
        out = out[:max_total]
    return {"items": out}

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
