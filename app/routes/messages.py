from fastapi import APIRouter, Depends
from ..utils.jwt_handler import decode_jwt
import httpx, os

router = APIRouter()

def get_headers(token: str):
    return {"Authorization": f"Bearer {token}"}

@router.get("/messages/{chatid}")
async def get_messages(chatid: str, user=Depends(decode_jwt)):
    sub = user["subdomain"]
    token = user["token"]
    url = f"https://{sub}.uazapi.com/api/v1/messages/{chatid}"
    async with httpx.AsyncClient() as client:
        r = await client.get(url, headers=get_headers(token))
        return r.json()
