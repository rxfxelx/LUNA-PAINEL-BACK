# app/routes/lead_status.py
from __future__ import annotations
from typing import Dict, Any, List, Optional
from fastapi import APIRouter, Query, Body, HTTPException, Request

try:
    from app.services.lead_status import getCachedLeadStatus, get_many_lead_status  # async
    _HAS_BULK = True
except Exception:  # pragma: no cover
    from app.services.lead_status import getCachedLeadStatus  # async
    _HAS_BULK = False

router = APIRouter()

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

    if _HAS_BULK:
        items = await get_many_lead_status(instance_id, dedup)  # ✅ bulk real
    else:
        # fallback assíncrono (N chamadas)
        items = []
        for cid in dedup:
            rec = await getCachedLeadStatus(instance_id, cid)
            if rec:
                items.append(rec)

    return {"items": items, "count": len(items), "requested": len(dedup), "bulk_accelerated": _HAS_BULK}
