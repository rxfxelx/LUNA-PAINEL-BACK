from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from ..utils.jwt_handler import decode_jwt
import httpx

router = APIRouter()

@router.get("/sse")
async def sse(events: str = "chats,messages,messages_update", user=Depends(decode_jwt)):
    """
    Proxy SSE -> UAZAPI
    Uso no front: new EventSource(`${BACK}/api/sse?events=messages`, { withCredentials:false })
    (O JWT vai por header; aqui o back injeta o token da instância na query para a UAZAPI)
    """
    sub = user["subdomain"]; tok = user["token"]
    base = f"https://{sub}.uazapi.com/sse?token={tok}&events={events}"

    async def generator():
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream("GET", base) as r:
                if r.status_code >= 400:
                    text = await r.aread()
                    raise HTTPException(r.status_code, text.decode())
                async for line in r.aiter_lines():
                    if line is None:
                        continue
                    yield (line + "\n")

    return StreamingResponse(generator(), media_type="text/event-stream")
