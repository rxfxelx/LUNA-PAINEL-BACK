from fastapi import APIRouter, Depends, HTTPException
from ..utils.jwt_handler import decode_jwt
import httpx, os

router = APIRouter()

BASE_URL = os.getenv("UAZAPI_BASEURL", "https://docs.uazapi.com")

def get_headers(token: str):
    return {"Authorization": f"Bearer {token}"}

@router.get("/chats")
async def list_chats(user=Depends(decode_jwt)):
    sub = user["subdomain"]
    token = user["token"]
    url = f"https://{sub}.uazapi.com/api/v1/chats"
    async with httpx.AsyncClient() as client:
        r = await client.get(url, headers=get_headers(token))
        return r.json()
