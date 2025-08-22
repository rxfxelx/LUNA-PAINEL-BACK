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

def base(host: str) -> str: return f"https://{host}"
def hdr(tok: str) -> dict:  return {"token": tok, "Content-Type": "application/json"}
def to_dict(m: BaseModel) -> dict: return m.model_dump() if hasattr(m, "model_dump") else m.dict()

@router.post("/chats")
async def find_chats(body: ChatFind, user=Depends(decode_jwt)):
    host, tok = user["host"], user["token"]
    url = f"{base(host)}/chat/find"
    async with httpx.AsyncClient(timeout=30.0) as c:
        r = await c.post(url, headers=hdr(tok), json=to_dict(body))
        if r.status_code >= 400: raise HTTPException(r.status_code, r.text)
        data = r.json()
        items = data.get("chats") or data.get("items") or data.get("data") or data.get("result") \
                or (data if isinstance(data, list) else [])
        return {"items": items}

@router.get("/chats/count")
async def chats_count(user=Depends(decode_jwt)):
    host, tok = user["host"], user["token"]
    url = f"{base(host)}/chat/count"
    async with httpx.AsyncClient(timeout=15.0) as c:
        r = await c.get(url, headers={"token": tok})
        if r.status_code >= 400: raise HTTPException(r.status_code, r.text)
        return r.json()
