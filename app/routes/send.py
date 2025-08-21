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
    buttons: list[str]  # até 3

class SendList(BaseModel):
    number: str
    header: str
    body: str
    button_text: str
    sections: list[dict]  # conforme doc da UAZAPI

def uaz_base(subdomain: str) -> str:
    return f"https://{subdomain}.uazapi.com"

def uaz_headers(token: str) -> dict:
    return {"token": token, "Content-Type": "application/json"}

def to_dict(m: BaseModel) -> dict:
    return m.model_dump() if hasattr(m, "model_dump") else m.dict()

@router.post("/send-text")
async def send_text(body: SendText, user=Depends(decode_jwt)):
    sub = user["subdomain"]; tok = user["token"]
    url = f"{uaz_base(sub)}/send/text"
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(url, headers=uaz_headers(tok), json=to_dict(body))
        if r.status_code >= 400:
            raise HTTPException(r.status_code, r.text)
        return r.json()

@router.post("/send-media")
async def send_media(body: SendMedia, user=Depends(decode_jwt)):
    sub = user["subdomain"]; tok = user["token"]
    url = f"{uaz_base(sub)}/send/media"
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(url, headers=uaz_headers(tok), json=to_dict(body))
        if r.status_code >= 400:
            raise HTTPException(r.status_code, r.text)
        return r.json()

@router.post("/send-buttons")
async def send_buttons(body: SendButtons, user=Depends(decode_jwt)):
    sub = user["subdomain"]; tok = user["token"]
    url = f"{uaz_base(sub)}/send/buttons"
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(url, headers=uaz_headers(tok), json=to_dict(body))
        if r.status_code >= 400:
            raise HTTPException(r.status_code, r.text)
        return r.json()

@router.post("/send-list")
async def send_list(body: SendList, user=Depends(decode_jwt)):
    sub = user["subdomain"]; tok = user["token"]
    url = f"{uaz_base(sub)}/send/list"
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(url, headers=uaz_headers(tok), json=to_dict(body))
        if r.status_code >= 400:
            raise HTTPException(r.status_code, r.text)
        return r.json()
