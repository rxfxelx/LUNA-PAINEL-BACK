# app/routes/chats.py
from __future__ import annotations
import asyncio, json
import httpx
from fastapi import APIRouter, Depends, HTTPException, Body, Query
from fastapi.responses import StreamingResponse

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

async def _classify_one(ctx: dict, chatid: str) -> str | None:
    try:
        res = await ai_routes.classify_chat(chatid=chatid, persist=True, limit=200, ctx=ctx)
        return res["stage"]
    except Exception:
        return None

# -------- resposta única (paginando) --------
@router.post("/chats")
async def find_chats(
    body: dict | None = Body(None),
    classify: bool = Query(True, description="Classifica cada chat antes de devolver"),
    page_size: int = Query(100, ge=1, le=500),
    max_total: int = Query(5000, ge=1, le=20000),
    ctx=Depends(get_uazapi_ctx),
):
    base, headers = _uaz(ctx)
    url = f"{base}/chat/find"

    items: list[dict] = []
    offset = 0
    while len(items) < max_total:
        payload = body if body else {"operator": "AND", "sort": "-wa_lastMsgTimestamp"}
        payload = {**payload, "limit": page_size, "offset": offset}
        async with httpx.AsyncClient(timeout=30) as cli:
            r = await cli.post(url, json=payload, headers=headers)
        if r.status_code >= 400:
            raise HTTPException(status_code=r.status_code, detail=r.text)
        try:
            data = r.json()
        except Exception:
            raise HTTPException(502, "Resposta inválida da UAZAPI em /chat/find")

        chunk = _normalize_items(data)["items"]
        if not chunk:
            break
        items.extend(chunk)
        if len(chunk) < page_size:
            break
        offset += page_size

    items = items[:max_total]

    if classify and items:
        sem = asyncio.Semaphore(8)
        async def worker(item):
            chatid = item.get("wa_chatid") or item.get("chatid") or item.get("wa_fastid") or item.get("id")
            if not chatid: 
                return
            async with sem:
                st = await _classify_one(ctx, chatid)
                if st:
                    item["_stage"] = st
                    crm_module.set_status_internal(chatid, st)
        await asyncio.gather(*(worker(it) for it in items))

    return {"items": items}

# -------- stream ao vivo (NDJSON + paginação) --------
@router.post("/chats/stream")
async def stream_chats(
    body: dict | None = Body(None),
    page_size: int = Query(100, ge=1, le=500),
    max_total: int = Query(5000, ge=1, le=20000),
    ctx=Depends(get_uazapi_ctx),
):
    base, headers = _uaz(ctx)
    url = f"{base}/chat/find"

    async def gen():
        count = 0
        offset = 0
        sem = asyncio.Semaphore(8)

        async def enrich_and_dump(item):
            chatid = item.get("wa_chatid") or item.get("chatid") or item.get("wa_fastid") or item.get("id")
            if chatid:
                async with sem:
                    st = await _classify_one(ctx, chatid)
                    if st:
                        item["_stage"] = st
                        crm_module.set_status_internal(chatid, st)
            yield json.dumps(item, ensure_ascii=False) + "\n"

        while count < max_total:
            payload = body if body else {"operator": "AND", "sort": "-wa_lastMsgTimestamp"}
            payload = {**payload, "limit": page_size, "offset": offset}
            async with httpx.AsyncClient(timeout=30) as cli:
                r = await cli.post(url, json=payload, headers=headers)
            if r.status_code >= 400:
                yield json.dumps({"error": r.text}) + "\n"
                return
            try:
                data = r.json()
            except Exception:
                yield json.dumps({"error": "Resposta inválida da UAZAPI"}) + "\n"
                return

            chunk = _normalize_items(data)["items"]
            if not chunk:
                break

            tasks = [enrich_and_dump(it) for it in chunk]
            for coro in asyncio.as_completed(tasks):
                async for line in coro:
                    yield line
                    count += 1
                    if count >= max_total:
                        break

            if len(chunk) < page_size or count >= max_total:
                break
            offset += page_size

    return StreamingResponse(gen(), media_type="application/x-ndjson")
