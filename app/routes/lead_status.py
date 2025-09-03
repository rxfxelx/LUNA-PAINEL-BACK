# app/routes/lead_status.py
from __future__ import annotations
from typing import Dict, Any, List
from fastapi import APIRouter, Query, Body, HTTPException
from app.services.lead_status import getCachedLeadStatus

router = APIRouter()

@router.get("/lead-status")
async def get_one(chatid: str = Query(...)):
    rec = getCachedLeadStatus(chatid)
    if not rec:
        return {"found": False}
    return {"found": True, **rec}

@router.post("/lead-status/bulk")
async def get_bulk(payload: Dict[str, Any] = Body(...)):
    chatids: List[str] = payload.get("chatids") or []
    if not isinstance(chatids, list) or not all(isinstance(c, str) for c in chatids):
        raise HTTPException(400, "chatids inválido")
    out = []
    for cid in chatids:
        rec = getCachedLeadStatus(cid)
        if rec:
            out.append(rec)
    return {"items": out}
