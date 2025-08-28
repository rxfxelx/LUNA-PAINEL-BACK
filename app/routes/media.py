# app/routes/media.py
import httpx
from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel
from urllib.parse import unquote

router = APIRouter()

class ProxyBody(BaseModel):
    url: str

async def _fetch(url: str) -> Response:
    url = url.strip()
    if not (url.startswith("http://") or url.startswith("https://")):
        raise HTTPException(status_code=400, detail="URL inválida")
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as cli:
            r = await cli.get(url)
            if r.status_code >= 400:
                raise HTTPException(status_code=r.status_code, detail="Falha ao buscar mídia upstream")
            ct = r.headers.get("content-type", "application/octet-stream")
            return Response(content=r.content, media_type=ct)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Proxy error: {e}")

@router.post("/proxy")
async def proxy_media_post(body: ProxyBody):
    """Compatível com o frontend atual: POST /api/media/proxy {url}"""
    return await _fetch(body.url)

@router.get("/proxy")
async def proxy_media_get(u: str = Query(..., description="URL absoluta da mídia (encodeURIComponent)")):
    """Opcional: GET /api/media/proxy?u=<encodeURIComponent(absoluteURL)>"""
    return await _fetch(unquote(u))
