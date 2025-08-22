from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from ..utils.jwt_handler import decode_jwt
import httpx

router = APIRouter()

class MsgFind(BaseModel):
    chatid: str
    limit: int = 50
    offset: int = 0
    sort: str | None = None

def base(host: str) -> str: return f"https://{host}"
def hdr(tok: str) -> dict:  return {"token": tok, "Content-Type": "application/json"}
def to_dict(m: BaseModel) -> dict: return m.model_dump() if hasattr(m,"model_dump") else m.dict()

@router.post("/messages")
async def find_messages(body: MsgFind, user=Depends(decode_jwt)):
    host, tok = user["host"], user["token"]
    url = f"{base(host)}/message/find"
    async with httpx.AsyncClient(timeout=30.0) as c:
        r = await c.post(url, headers=hdr(tok), json=to_dict(body))
        if r.status_code >= 400: raise HTTPException(r.status_code, r.text)
        data = r.json()
        items = data.get("messages") or data.get("items") or data.get("data") or data.get("result") \
                or (data if isinstance(data, list) else [])
        return {"items": items}

@router.get("/messages/{chatid}")
async def get_messages(chatid: str, user=Depends(decode_jwt)):
    host, tok = user["host"], user["token"]
    url = f"{base(host)}/message/find"
    payload = {"chatid": chatid, "limit": 100, "offset": 0, "sort": "-messageTimestamp"}
    async with httpx.AsyncClient(timeout=30.0) as c:
        r = await c.post(url, headers=hdr(tok), json=payload)
        if r.status_code >= 400: raise HTTPException(r.status_code, r.text)
        data = r.json()
        items = data.get("messages") or data.get("items") or data.get("data") or data.get("result") \
                or (data if isinstance(data, list) else [])
        return {"items": items}
