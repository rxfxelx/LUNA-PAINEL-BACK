# app/routes/messages.py
from __future__ import annotations
from typing import Dict, Any
import httpx
from fastapi import APIRouter, Depends, HTTPException, Body

from app.routes.deps import get_uazapi_ctx
from app.routes.ai import classify_stage  # regra igual à usada no front

router = APIRouter()

def _uaz(ctx):
    base = f"https://{ctx['host']}"
    headers = {"token": ctx["token"]}
    return base, headers

def _normalize_items(resp_json):
    if isinstance(resp_json, dict):
        if isinstance(resp_json.get("items"), list):
            return {"items": resp_json["items"]}
        for key in ("data", "results", "messages"):
            val = resp_json.get(key)
            if isinstance(val, list):
                return {"items": val}
        return {"items": []}
    if isinstance(resp_json, list):
        return {"items": resp_json}
    return {"items": []}

@router.post("/messages")
async def find_messages(body: dict | None = None, ctx=Depends(get_uazapi_ctx)):
    """
    Proxy para UAZAPI /message/find.
    Além de devolver normalizado, já calcula e retorna `stage`.
    """
    if not body or not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Body inválido")

    chatid = body.get("chatid")
    if not chatid:
        raise HTTPException(status_code=400, detail="chatid é obrigatório")

    base, headers = _uaz(ctx)
    url = f"{base}/message/find"

    payload = {
        "chatid": chatid,
        "limit": int(body.get("limit") or 200),
        "offset": int(body.get("offset") or 0),
        "sort": body.get("sort") or "-messageTimestamp",
    }

    async with httpx.AsyncClient(timeout=30) as cli:
        r = await cli.post(url, json=payload, headers=headers)

    if r.status_code >= 400:
        raise HTTPException(status_code=r.status_code, detail=r.text)

    try:
        data = r.json()
    except Exception:
        raise HTTPException(status_code=502, detail="Resposta inválida da UAZAPI em /message/find")

    wrapped = _normalize_items(data)
    items = wrapped["items"]

    # Classificação instantânea no backend
    stage = classify_stage(items)

    return {"items": items, "stage": stage}
