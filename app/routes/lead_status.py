# app/routes/lead_status.py
from __future__ import annotations
from typing import Dict, Any, List, Optional
from fastapi import APIRouter, Query, Body, HTTPException, Request
import logging

# tenta importar a versão bulk do service (assíncrona)
try:
    from app.services.lead_status import (  # type: ignore
        getCachedLeadStatus,
        getCachedLeadStatusBulk,
    )
    _HAS_BULK = True
except Exception:
    # fallback assíncrono que chama o unitário em loop (com await!)
    from app.services.lead_status import getCachedLeadStatus  # type: ignore
    _HAS_BULK = False

    async def getCachedLeadStatusBulk(instance_id: str, chatids: List[str]):
        out = []
        for cid in chatids:
            rec = await getCachedLeadStatus(instance_id, cid)
            if rec:
                out.append(rec)
        return out

router = APIRouter()
log = logging.getLogger("uvicorn.error")

# -------- utils: extrai instance_id do JWT --------
def _b64url_to_bytes(s: str) -> bytes:
    import base64
    pad = "=" * ((4 - len(s) % 4) % 4)
    return base64.urlsafe_b64decode(s + pad)

def _get_instance_id_from_request(req: Request) -> str:
    inst = getattr(req.state, "instance_id", None)
    if inst:
        return str(inst)
    h = req.headers.get("x-instance-id")
    if h:
        return str(h)
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

# -------- endpoints --------
@router.get("/lead-status")
async def get_one(request: Request, chatid: str = Query(..., min_length=1)):
    instance_id = _get_instance_id_from_request(request)
    if not instance_id:
        raise HTTPException(401, "JWT sem instance_id")

    rec = await getCachedLeadStatus(instance_id, chatid)
    if not rec:
        return {"found": False}

    return {"found": True, **rec}

@router.post("/lead-status/bulk")
async def get_bulk(request: Request, payload: Dict[str, Any] = Body(...)):
    instance_id = _get_instance_id_from_request(request)
    if not instance_id:
        raise HTTPException(401, "JWT sem instance_id")

    chatids: List[str] = payload.get("chatids") or []
    if not isinstance(chatids, list) or not all(isinstance(c, str) and c.strip() for c in chatids):
        raise HTTPException(400, "chatids inválido")

    dedup = list(dict.fromkeys([c.strip() for c in chatids if c.strip()]))
    if len(dedup) > 2000:
        dedup = dedup[:2000]

    # LOG p/ depurar se o front está chamando
    log.info("lead-status/bulk req: instance=%s, qtd=%d", instance_id, len(dedup))

    items = await getCachedLeadStatusBulk(instance_id, dedup)  # <-- await (importante!)
    return {
        "items": items,
        "count": len(items),
        "requested": len(dedup),
        "bulk_accelerated": _HAS_BULK,
    }
