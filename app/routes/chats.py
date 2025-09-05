# app/routes/chats.py
from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Body, Query
from fastapi.responses import StreamingResponse

from app.routes.deps import get_uazapi_ctx
from app.routes import ai as ai_routes
from app.routes import crm as crm_module

# DB helpers de lead status
from app.services.lead_status import (  # type: ignore
    get_lead_status,
    upsert_lead_status,
    should_reclassify,
)

router = APIRouter()

# ---------------- cache simples p/ classificação (protege IA) ---------------- #
_CLASSIFY_CACHE: dict[str, tuple[float, str]] = {}  # chatid -> (ts_epoch, stage)
_CLASSIFY_TTL = 300  # 5 minutos


def _uaz(ctx: Dict[str, Any]) -> tuple[str, Dict[str, str]]:
    base = f"https://{ctx['host']}"
    headers = {"token": ctx["token"]}
    return base, headers


def _get_instance_id(ctx: Dict[str, Any]) -> str:
    # tenta várias claims comuns que vêm no ctx
    return str(
        ctx.get("instance_id")
        or ctx.get("phone_number_id")
        or ctx.get("pnid")
        or ctx.get("sub")
        or ""
    )


def _normalize_items(resp_json: Any) -> Dict[str, List[Dict[str, Any]]]:
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


def _pick_chatid(item: Dict[str, Any]) -> str:
    return (
        item.get("wa_chatid")
        or item.get("chatid")
        or item.get("wa_fastid")
        or item.get("id")
        or ""
    )


def _last_msg_ts_of(item: Dict[str, Any]) -> int:
    # aceita várias chaves possíveis; retorna em epoch ms se possível
    ts = (
        item.get("wa_lastMsgTimestamp")
        or item.get("messageTimestamp")
        or item.get("updatedAt")
        or 0
    )
    try:
        n = int(ts)
    except Exception:
        return 0
    # alguns backends podem devolver segundos
    if len(str(n)) == 10:
        n = n * 1000
    return n


async def _maybe_classify_and_persist(
    ctx: Dict[str, Any],
    chatid: str,
    last_msg_ts: Optional[int] = None,
) -> Optional[str]:
    """
    Estratégia:
    - Se tiver no banco -> usa e retorna
    - Se tiver e should_reclassify(...) == False -> não mexe
    - Se não tiver ou precisar reclassificar -> IA e salva
    """
    instance_id = _get_instance_id(ctx)
    if not instance_id:
        return None

    # 1) tenta banco
    try:
        rec = await get_lead_status(instance_id, chatid)
    except Exception:
        rec = None

    if rec and rec.get("stage"):
        # decide se precisa reclassificar
        try:
            need = await should_reclassify(
                instance_id,
                chatid,
                last_msg_ts=last_msg_ts,
                last_from_me=None,
            )
        except Exception:
            need = False

        if not need:
            return str(rec["stage"])

    # 2) cache curto (evita bombar IA)
    now = time.time()
    hit = _CLASSIFY_CACHE.get(chatid)
    if hit and (now - hit[0]) <= _CLASSIFY_TTL:
        stage_cached = hit[1]
        # garante persistência caso não tenha sido salvo
        try:
            await upsert_lead_status(
                instance_id,
                chatid,
                stage_cached,
                last_msg_ts=int(last_msg_ts or 0),
                last_from_me=False,
            )
        except Exception:
            pass
        return stage_cached

    # 3) classifica com IA (timeout curto)
    try:
        res = await asyncio.wait_for(
            ai_routes.classify_chat(
                chatid=chatid,
                persist=False,  # persistiremos nós
                limit=200,
                ctx=ctx,
            ),
            timeout=3.5,
        )
        stage = (res or {}).get("stage")
        if stage:
            _CLASSIFY_CACHE[chatid] = (now, stage)
            try:
                await upsert_lead_status(
                    instance_id,
                    chatid,
                    stage,
                    last_msg_ts=int(last_msg_ts or 0),
                    last_from_me=False,
                )
            except Exception:
                pass
            return stage
    except Exception:
        return None

    return None


# ------------------ Resposta única (paginada) ------------------ #
@router.post("/chats")
async def find_chats(
    body: dict | None = Body(None),
    classify: bool = Query(
        True,
        description=(
            "Se True, usa banco quando houver; "
            "classifica com IA apenas quando não houver registro ou quando precisar reclassificar."
        ),
    ),
    page_size: int = Query(100, ge=1, le=500),
    max_total: int = Query(5000, ge=1, le=20000),
    ctx=Depends(get_uazapi_ctx),
):
    base, headers = _uaz(ctx)
    url = f"{base}/chat/find"

    items: list[dict] = []
    offset = 0

    async with httpx.AsyncClient(timeout=30) as cli:
        while len(items) < max_total:
            payload = body if body else {"operator": "AND", "sort": "-wa_lastMsgTimestamp"}
            payload = {**payload, "limit": page_size, "offset": offset}

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
        sem = asyncio.Semaphore(16)

        async def worker(item: dict):
            chatid = _pick_chatid(item)
            if not chatid:
                return
            last_ts = _last_msg_ts_of(item)
            st = await _maybe_classify_and_persist(ctx, chatid, last_msg_ts=last_ts)
            if st:
                item["_stage"] = st
                item["stage"] = st
                try:
                    crm_module.set_status_internal(chatid, st)
                except Exception:
                    pass

        await asyncio.gather(*(worker(it) for it in items))

    return {"items": items}


# ------------------ Stream NDJSON ------------------ #
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
        sem = asyncio.Semaphore(16)

        async with httpx.AsyncClient(timeout=30) as cli:

            async def process_item(item: dict) -> str:
                chatid = _pick_chatid(item)
                if chatid:
                    last_ts = _last_msg_ts_of(item)
                    st = await _maybe_classify_and_persist(ctx, chatid, last_msg_ts=last_ts)
                    if st:
                        item["_stage"] = st
                        item["stage"] = st
                        try:
                            crm_module.set_status_internal(chatid, st)
                        except Exception:
                            pass
                return json.dumps(item, ensure_ascii=False) + "\n"

            while count < max_total:
                payload = body if body else {"operator": "AND", "sort": "-wa_lastMsgTimestamp"}
                payload = {**payload, "limit": page_size, "offset": offset}

                r = await cli.post(url, json=payload, headers=headers)
                if r.status_code >= 400:
                    yield json.dumps({"error": r.text}) + "\n"
                    return

                try:
                    data = r.json()
                except Exception:
                    yield json.dumps({"error": "Resposta inválida da UAZAPI em /chat/find"}) + "\n"
                    return

                chunk = _normalize_items(data)["items"]
                if not chunk:
                    break

                coros = [process_item(it) for it in chunk]
                for fut in asyncio.as_completed(coros):
                    try:
                        line = await fut
                    except Exception as e:
                        line = json.dumps({"error": f"process_item: {e}"}) + "\n"
                    yield line
                    count += 1
                    if count >= max_total:
                        break

                if len(chunk) < page_size or count >= max_total:
                    break
                offset += page_size

    return StreamingResponse(gen(), media_type="application/x-ndjson")
