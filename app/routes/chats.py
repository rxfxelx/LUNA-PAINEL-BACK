# app/routes/chats.py
from __future__ import annotations
import asyncio
import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Body

from app.routes.deps import get_uazapi_ctx
from app.routes import ai as ai_routes
from app.routes import crm as crm_module

router = APIRouter()

def _uaz(ctx):
    base = f"https://{ctx['host']}"
    headers = {"token": ctx["token"]}
    return base, headers

def _normalize_items(resp_json):
    if isinstance(resp_json, dict):
        if isinstance(resp_json.get("items"), list):
            return {"items": resp_json["items"]}
        for key in ("data", "results", "chats"):
            val = resp_json.get(key)
            if isinstance(val, list):
                return {"items": val}
        return {"items": []}
    if isinstance(resp_json, list):
        return {"items": resp_json}
    return {"items": []}

async def _classify_one(ctx: dict, chatid: str) -> tuple[str, str | None]:
    try:
        res = await ai_routes.classify_chat(chatid=chatid, persist=True, limit=200, ctx=ctx)  # reusa função
        return chatid, res["stage"]
    except Exception:
        return chatid, None

@router.post("/chats")
async def find_chats(
    body: dict | None = Body(None),
    classify: bool = Query(True, description="Se True, classifica cada chat durante o carregamento"),
    ctx=Depends(get_uazapi_ctx),
):
    """
    Proxy para UAZAPI /chat/find com **classificação opcional imediata**.
    Retorna { items: [...] } e, quando 'classify' for True, anexa '_stage' em cada item.
    """
    base, headers = _uaz(ctx)
    url = f"{base}/chat/find"

    if not body or not isinstance(body, dict):
        body = {"operator": "AND", "sort": "-wa_lastMsgTimestamp", "limit": 100, "offset": 0}

    async with httpx.AsyncClient(timeout=30) as cli:
        r = await cli.post(url, json=body, headers=headers)

    if r.status_code >= 400:
        raise HTTPException(status_code=r.status_code, detail=r.text)

    try:
        data = r.json()
    except Exception:
        raise HTTPException(status_code=502, detail="Resposta inválida da UAZAPI em /chat/find")

    out = _normalize_items(data)
    items = out["items"]

    # ===== Classificar já durante o retorno =====
    if classify and items:
        # pega ids normalizados
        chatids = []
        for c in items:
            chatid = c.get("wa_chatid") or c.get("chatid") or c.get("wa_fastid") or c.get("id") or ""
            if chatid:
                chatids.append(chatid)

        # paralelismo controlado
        sem = asyncio.Semaphore(8)
        async def worker(cid: str):
            async with sem:
                _cid, stage = await _classify_one(ctx, cid)
                return _cid, stage

        results = await asyncio.gather(*(worker(cid) for cid in chatids), return_exceptions=False)
        stage_by_id = {cid: st for cid, st in results if st}

        # anexa no payload e garante persistência no CRM
        for it in items:
            cid = it.get("wa_chatid") or it.get("chatid") or it.get("wa_fastid") or it.get("id")
            st = stage_by_id.get(cid)
            if st:
                it["_stage"] = st
                crm_module.set_status_internal(cid, st)

    return {"items": items}

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
