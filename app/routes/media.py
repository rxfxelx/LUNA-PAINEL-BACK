import io, re, httpx
from typing import Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Query, Body
from starlette.responses import StreamingResponse

from app.routes.deps import get_uazapi_ctx
from app.routes.ai import classify_by_rules

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

@router.post("/stage/classify")
async def stage_classify(payload: Dict[str, Any] = Body(...)):
    items = payload.get("messages") or []
    return {"stage": classify_by_rules(items)}
