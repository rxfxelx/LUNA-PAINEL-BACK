# app/routes/lead_status.py
from __future__ import annotations
from typing import Dict, Any, List
from fastapi import APIRouter, Query, Body, HTTPException
from app.services.lead_status import getCachedLeadStatus

router = APIRouter()

@router.get("/lead-status")
async def get_one(chatid: str = Query(...)):
    """
    Retorna um único registro de cache.
    Resposta:
      { found: false }  ou
      { found: true, chatid, stage, last_msg_ts, updated_at, last_from_me }
    """
    rec = getCachedLeadStatus(chatid)
    if not rec:
        return {"found": False}
    return {"found": True, **rec}

@router.post("/lead-status/bulk")
async def get_bulk(payload: Dict[str, Any] = Body(...)):
    """
    Busca em lote. Aceita { ids: string[] } ou { chatids: string[] }.
    Resposta no formato que o front espera:
      { items: { "<chatid>": { stage, last_msg_ts } } }
    """
    ids: List[str] = payload.get("ids") or payload.get("chatids") or []
    if not isinstance(ids, list) or not all(isinstance(c, str) and c for c in ids):
        raise HTTPException(status_code=400, detail="ids/chatids inválido")

    items: Dict[str, Dict[str, Any]] = {}
    for cid in ids:
        rec = getCachedLeadStatus(cid)
        if rec:
            items[rec["chatid"]] = {
                "stage": rec.get("stage"),
                "last_msg_ts": int(rec.get("last_msg_ts") or 0),
            }

    return {"items": items}
