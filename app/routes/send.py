from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from ..utils.jwt_handler import decode_jwt
import httpx

router = APIRouter()

class SendText(BaseModel):
    number: str
    text: str

class SendMedia(BaseModel):
    number: str
    url: str
    caption: str | None = None

class SendButtons(BaseModel):
    number: str
    text: str
    buttons: list[str]

class SendList(BaseModel):
    number: str
    header: str
    body: str
    button_text: str
    sections: list[dict]

def base(host: str) -> str: return f"https://{host}"
def hdr(tok: str) -> dict:  return {"token": tok, "Content-Type": "application/json"}
def to_dict(m: BaseModel) -> dict: return m.model_dump() if hasattr(m,"model_dump") else m.dict()

@router.post("/send-text")
async def send_text(body: SendText, user=Depends(decode_jwt)):
    host, tok = user["host"], user["token"]
    url = f"{base(host)}/send/text"
    async with httpx.AsyncClient(timeout=30.0) as c:
        r = await c.post(url, headers=hdr(tok), json=to_dict(body))
        if r.status_code >= 400: raise HTTPException(r.status_code, r.text)
        return r.json()

@router.post("/send-media")
async def send_media(body: SendMedia, user=Depends(decode_jwt)):
    host, tok = user["host"], user["token"]
    url = f"{base(host)}/send/media"
    async with httpx.AsyncClient(timeout=30.0) as c:
        r = await c.post(url, headers=hdr(tok), json=to_dict(body))
        if r.status_code >= 400: raise HTTPException(r.status_code, r.text)
        return r.json()

@router.post("/send-buttons")
async def send_buttons(body: SendButtons, user=Depends(decode_jwt)):
    host, tok = user["host"], user["token"]
    url = f"{base(host)}/send/buttons"
    async with httpx.AsyncClient(timeout=30.0) as c:
        r = await c.post(url, headers=hdr(tok), json=to_dict(body))
        if r.status_code >= 400: raise HTTPException(r.status_code, r.text)
        return r.json()

@router.post("/send-list")
async def send_list(body: SendList, user=Depends(decode_jwt)):
    host, tok = user["host"], user["token"]
    url = f"{base(host)}/send/list"
    async with httpx.AsyncClient(timeout=30.0) as c:
        r = await c.post(url, headers=hdr(tok), json=to_dict(body))
        if r.status_code >= 400: raise HTTPException(r.status_code, r.text)
        return r.json()
