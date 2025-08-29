# app/routes/ai.py
from __future__ import annotations
from typing import Dict, List, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query

from app.routes.deps import get_uazapi_ctx
from app.core.stage_rules import classify_by_rules
from app.routes import crm as crm_module  # para persistir

router = APIRouter()

async def _fetch_last_messages(ctx: dict, chatid: str, limit: int = 200) -> List[dict]:
    base = f"https://{ctx['host']}"
    headers = {"token": ctx["token"]}
    url = f"{base}/message/find"
    body = {"chatid": chatid, "limit": limit, "sort": "-messageTimestamp"}
    async with httpx.AsyncClient(timeout=40) as cli:
        r = await cli.post(url, json=body, headers=headers)
    if r.status_code >= 400:
        raise HTTPException(r.status_code, f"UAZAPI /message/find falhou: {r.text}")
    try:
        j = r.json()
    except Exception:
        raise HTTPException(502, "Resposta inválida da UAZAPI em /message/find")
    items = j.get("items") if isinstance(j, dict) else j
    return items or []

@router.post("/stage/classify")
async def classify_chat(
    chatid: str,
    persist: bool = True,
    limit: int = 200,
    ctx=Depends(get_uazapi_ctx),
):
    """
    Classifica 1 chat pelas regras e (opcional) persiste no CRM.
    """
    msgs = await _fetch_last_messages(ctx, chatid, limit=limit)
    stage = classify_by_rules(msgs)
    if persist:
        crm_module.set_status_internal(chatid, stage)
    return {"chatid": chatid, "stage": stage, "count": len(msgs)}
