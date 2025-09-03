import io, re, httpx
from typing import Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Query, Body
from starlette.responses import StreamingResponse

from app.routes.deps import get_uazapi_ctx
from app.routes.ai import classify_by_rules

# cache de classificação
from app.services.lead_status import (
    getCachedLeadStatus,
    upsertLeadStatus,
    needsReclassify,
)

router = APIRouter()

def _uaz(ctx):
    base = f"https://{ctx['host']}"
    headers = {"token": ctx["token"]}
    return base, headers

def _pick(d: Dict[str, Any], path: str, default=None):
    cur = d
    for p in path.split("."):
        if not isinstance(cur, dict):
            return default
        cur = cur.get(p)
    return cur if cur is not None else default

@router.get("/proxy")
async def media_proxy(u: str = Query(...)):
    if not re.match(r"^https?://", u):
        raise HTTPException(400, "URL inválida")
    async with httpx.AsyncClient(timeout=30) as cli:
        r = await cli.get(u)
    if r.status_code >= 400:
        raise HTTPException(r.status_code, "Falha ao baixar mídia")
    ct = r.headers.get("content-type", "application/octet-stream")
    return StreamingResponse(io.BytesIO(r.content), media_type=ct)

@router.post("/resolve")
async def media_resolve(payload: Dict[str, Any] = Body(...), ctx=Depends(get_uazapi_ctx)):
    m = payload or {}
    mime = ( m.get("mimetype") or m.get("mime") or
             _pick(m,"message.imageMessage.mimetype") or
             _pick(m,"message.videoMessage.mimetype") or
             _pick(m,"message.documentMessage.mimetype") or
             _pick(m,"message.audioMessage.mimetype") or
             _pick(m,"message.stickerMessage.mimetype") or "" )
    url = ( m.get("mediaUrl") or m.get("url") or m.get("fileUrl") or m.get("downloadUrl") or
            m.get("image") or m.get("video") or
            _pick(m,"message.imageMessage.url") or _pick(m,"message.videoMessage.url") or
            _pick(m,"message.documentMessage.url") or _pick(m,"message.audioMessage.url") or
            _pick(m,"message.stickerMessage.url") or "" )
    data_url = ( m.get("dataUrl") or _pick(m,"message.imageMessage.dataUrl") or
                 _pick(m,"message.videoMessage.dataUrl") or _pick(m,"message.stickerMessage.dataUrl") or "" )
    if url or data_url:
        return {"url": url, "mime": mime, "dataUrl": data_url}

    media_id = ( m.get("mediaId") or _pick(m,"message.documentMessage.mediaKey") or
                 _pick(m,"message.imageMessage.mediaKey") or _pick(m,"message.videoMessage.mediaKey") or
                 _pick(m,"message.audioMessage.mediaKey") or _pick(m,"message.stickerMessage.mediaKey") or None )

    base, headers = _uaz(ctx)
    async with httpx.AsyncClient(timeout=30) as cli:
        candidates = []
        if media_id:
            candidates.append(("GET", f"{base}/media/resolve?id={media_id}", None))
        candidates.append(("POST", f"{base}/media/resolve", {"message": m}))
        for method, url2, body in candidates:
            r = await (cli.post(url2, headers=headers, json=body) if method == "POST" else cli.get(url2, headers=headers))
            if 200 <= r.status_code < 300:
                try:
                    j = r.json()
                except Exception:
                    continue
                u = j.get("url") or j.get("downloadUrl")
                mm = j.get("mime") or j.get("mimetype") or mime
                d = j.get("dataUrl") or ""
                if u or d:
                    return {"url": u, "mime": mm, "dataUrl": d}

    raise HTTPException(404, "Não foi possível resolver a mídia")

# ---------- Classificação com cache ----------
def _ts(m: Dict[str, Any]) -> int:
    return int(
        m.get("messageTimestamp")
        or m.get("timestamp")
        or m.get("t")
        or (m.get("message") or {}).get("messageTimestamp")
        or 0
    )

def _from_me(m: Dict[str, Any]) -> bool:
    return bool(
        m.get("fromMe")
        or m.get("fromme")
        or m.get("from_me")
        or (m.get("key") or {}).get("fromMe")
    )

@router.post("/stage/classify")
async def stage_classify(payload: Dict[str, Any] = Body(...)):
    # payload esperado: { chatid?: str, messages: [...] }
    chatid = str(payload.get("chatid") or "").strip()
    items = payload.get("messages") or []

    last = max(items, key=_ts) if items else None
    last_ts = _ts(last) if last else 0
    last_from_me = _from_me(last) if last else False

    # Se temos chatid e não há mensagem mais nova, devolve cache
    if chatid and not needsReclassify(chatid, last_ts, last_from_me):
        cached = getCachedLeadStatus(chatid)
        if cached:
            return {"stage": cached["stage"], "cached": True}

    # Caso contrário, classifica pelas regras existentes e atualiza cache
    stage = classify_by_rules(items)
    if chatid:
        upsertLeadStatus(chatid, stage=stage, last_msg_ts=last_ts, last_from_me=last_from_me)
    return {"stage": stage, "cached": False}
