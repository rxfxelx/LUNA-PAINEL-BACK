# app/routes/messages.py
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from ..utils.jwt_handler import decode_jwt
import httpx

router = APIRouter()

class MsgFind(BaseModel):
    chatid: str
    limit: int = 50
    offset: int = 0
    sort: str | None = None  # ex: "-messageTimestamp"

def uaz_base(subdomain: str) -> str:
    return f"https://{subdomain}.uazapi.com"

def uaz_headers(token: str) -> dict:
    return {"token": token, "Content-Type": "application/json"}

def model_to_dict(model: BaseModel) -> dict:
    return model.model_dump() if hasattr(model, "model_dump") else model.dict()

@router.post("/messages")
async def find_messages(body: MsgFind, user=Depends(decode_jwt)):
    sub = user["subdomain"]
    tok = user["token"]
    url = f"{uaz_base(sub)}/message/find"
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(url, headers=uaz_headers(tok), json=model_to_dict(body))
        if r.status_code >= 400:
            raise HTTPException(r.status_code, r.text)
        return r.json()

# (Opcional) fallback GET para front antigo
@router.get("/messages/{chatid}")
async def get_messages(chatid: str, user=Depends(decode_jwt)):
    sub = user["subdomain"]
    tok = user["token"]
    url = f"{uaz_base(sub)}/message/find"
    payload = {"chatid": chatid, "limit": 100, "offset": 0, "sort": "-messageTimestamp"}
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(url, headers=uaz_headers(tok), json=payload)
        if r.status_code >= 400:
            raise HTTPException(r.status_code, r.text)
        return r.json()
