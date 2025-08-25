# app/routes/messages.py
import httpx
from fastapi import APIRouter, Depends, HTTPException
from app.routes.deps import get_uazapi_ctx

router = APIRouter()

def _uaz(ctx):
    base = f"https://{ctx['host']}"
    headers = {"token": ctx["token"]}
    return base, headers

@router.post("/messages")
async def get_messages(body: dict, ctx=Depends(get_uazapi_ctx)):
    """
    Busca mensagens de um chat.
    Espera body com: { chatid, limit, sort, ... }
    Encaminha para UAZAPI (ajuste o caminho conforme sua API — aqui usamos /chat/messages).
    """
    chatid = (body.get("chatid") or "").strip()
    if not chatid:
        raise HTTPException(status_code=400, detail="chatid é obrigatório")

    base, headers = _uaz(ctx)
    url = f"{base}/chat/messages"
    async with httpx.AsyncClient(timeout=30) as cli:
        r = await cli.post(url, json=body, headers=headers)
    if r.status_code >= 400:
        raise HTTPException(status_code=r.status_code, detail=r.text)
    return r.json()
