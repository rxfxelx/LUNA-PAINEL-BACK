from fastapi import APIRouter, Depends
from pydantic import BaseModel
from ..utils.jwt_handler import decode_jwt
import httpx

router = APIRouter()

class SendTextRequest(BaseModel):
    chatid: str
    text: str

class SendMediaRequest(BaseModel):
    chatid: str
    url: str
    caption: str | None = None

def get_headers(token: str):
    return {"Authorization": f"Bearer {token}"}

@router.post("/send-text")
async def send_text(data: SendTextRequest, user=Depends(decode_jwt)):
    url = f"https://{user['subdomain']}.uazapi.com/api/v1/send-message"
    async with httpx.AsyncClient() as client:
        r = await client.post(url, headers=get_headers(user["token"]), json=data.dict())
        return r.json()

@router.post("/send-media")
async def send_media(data: SendMediaRequest, user=Depends(decode_jwt)):
    url = f"https://{user['subdomain']}.uazapi.com/api/v1/send-media"
    async with httpx.AsyncClient() as client:
        r = await client.post(url, headers=get_headers(user["token"]), json=data.dict())
        return r.json()
