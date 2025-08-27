# app/routes/media.py
import httpx
from fastapi import APIRouter, HTTPException, Query, Response
from urllib.parse import unquote

router = APIRouter()

@router.get("/proxy")
async def proxy_media(u: str = Query(..., description="URL absoluta da mídia (codificada com encodeURIComponent)")):
    """
    Proxy simples para evitar CORS/leak de origem.
    Use: /api/proxy?u=<encodeURIComponent(URL_ABSOLUTA)>
    """
    url = unquote(u).strip()
    if not (url.startswith("http://") or url.startswith("https://")):
        raise HTTPException(status_code=400, detail="URL inválida")

    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as cli:
            r = await cli.get(url)
            if r.status_code >= 400:
                raise HTTPException(status_code=r.status_code, detail="Falha ao buscar mídia upstream")
            # repassa o content-type se existir
            ct = r.headers.get("content-type", "application/octet-stream")
            return Response(content=r.content, media_type=ct)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Proxy error: {e}")
