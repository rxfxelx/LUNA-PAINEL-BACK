# app/routes/chats.py
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from ..utils.jwt_handler import decode_jwt
import httpx

router = APIRouter()

class ChatFind(BaseModel):
    operator: str = "AND"
    sort: str = "-wa_lastMsgTimestamp"
    limit: int = 50
    offset: int = 0
    wa_isGroup: bool | None = None
    wa_label: str | None = None
    wa_contactName: str | None = None
    name: str | None = None

def uaz_base(subdomain: str) -> str:
    return f"https://{subdomain}.uazapi.com"

def uaz_headers(token: str) -> dict:
    # UAZAPI espera header 'token'
    return {"token": token, "Content-Type": "application/json"}

def model_to_dict(model: BaseModel) -> dict:
    # compat Pydantic v1/v2
    return model.model_dump() if hasattr(model, "model_dump") else model.dict()

@router.post("/chats")
async def find_chats(body: ChatFind, user=Depends(decode_jwt)):
    sub = user["subdomain"]
    tok = user["token"]
    url = f"{uaz_base(sub)}/chat/find"
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(url, headers=uaz_headers(tok), json=model_to_dict(body))
        if r.status_code >= 400:
            raise HTTPException(r.status_code, r.text)
        return r.json()

# (Opcional) fallback GET para front antigo
@router.get("/chats")
async def list_chats(user=Depends(decode_jwt)):
    sub = user["subdomain"]
    tok = user["token"]
    url = f"{uaz_base(sub)}/chat/find"
    payload = {"operator":"AND","sort":"-wa_lastMsgTimestamp","limit":50,"offset":0}
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(url, headers=uaz_headers(tok), json=payload)
        if r.status_code >= 400:
            raise HTTPException(r.status_code, r.text)
        return r.json()
