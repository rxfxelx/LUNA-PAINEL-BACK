# app/routes/lead_status.py
from __future__ import annotations
from typing import Dict, Any, List
from fastapi import APIRouter, Query, Body, HTTPException, Request

from app.services.lead_status import getCachedLeadStatus

router = APIRouter()

# --- util: extrai instance_id do JWT sem dependências externas
def _b64url_to_bytes(s: str) -> bytes:
    import base64
    pad = "=" * ((4 - len(s) % 4) % 4)
    return base64.urlsafe_b64decode(s + pad)

def _get_instance_id_from_request(req: Request) -> str:
    # 1) se algum middleware já setou (opcional)
    inst = getattr(req.state, "instance_id", None)
    if inst:
        return str(inst)

    # 2) header extra opcional
    h = req.headers.get("x-instance-id")
    if h:
        return str(h)

    # 3) decodifica o JWT (sem verificar) e pega claim
    auth = req.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        token = auth.split(" ", 1)[1].strip()
        parts = token.split(".")
        if len(parts) >= 2:
            try:
                import json
                payload = json.loads(_b64url_to_bytes(parts[1]).decode("utf-8"))
                return str(
                    payload.get("instance_id")
                    or payload.get("phone_number_id")
                    or payload.get("pnid")
                    or payload.get("sub")
                    or ""
                )
            except Exception:
                pass
    return ""


@router.get("/lead-status")
async def get_one(request: Request, chatid: str = Query(...)):
    instance_id = _get_instance_id_from_request(request)
    if not instance_id:
        raise HTTPException(401, "JWT sem instance_id")
    rec = getCachedLeadStatus(instance_id, chatid)
    if not rec:
        return {"found": False}
    # retorna sempre escopado
    return {"found": True, **rec}

@router.post("/lead-status/bulk")
async def get_bulk(request: Request, payload: Dict[str, Any] = Body(...)):
    instance_id = _get_instance_id_from_request(request)
    if not instance_id:
        raise HTTPException(401, "JWT sem instance_id")

    chatids: List[str] = payload.get("chatids") or []
    if not isinstance(chatids, list) or not all(isinstance(c, str) for c in chatids):
        raise HTTPException(400, "chatids inválido")

    out = []
    for cid in chatids:
        rec = getCachedLeadStatus(instance_id, cid)
        if rec:
            out.append(rec)
    return {"items": out}
