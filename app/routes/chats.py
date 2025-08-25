# app/routes/chats.py
import httpx
from fastapi import APIRouter, Depends, HTTPException
from app.routes.deps import get_uazapi_ctx

router = APIRouter()

def _uaz(ctx):  # helper
    base = f"https://{ctx['host']}"
    headers = {"token": ctx["token"]}
    return base, headers

@router.post("/chats")
async def find_chats(body: dict, ctx=Depends(get_uazapi_ctx)):
    """
    Proxy para UAZAPI /chat/find
    body: { operator, sort, limit, offset, ... }
    """
    base, headers = _uaz(ctx)
    url = f"{base}/chat/find"
    async with httpx.AsyncClient(timeout=30) as cli:
        r = await cli.post(url, json=body, headers=headers)
    if r.status_code >= 400:
        raise HTTPException(status_code=r.status_code, detail=r.text)
    return r.json()

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
