from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from ..utils.jwt_handler import decode_jwt
import httpx

router = APIRouter()

class ChatFind(BaseModel):
    operator: str | None = "AND"
    sort: str | None = "-wa_lastMsgTimestamp"
    limit: int | None = 50
    offset: int | None = 0
    wa_isGroup: bool | None = None
    wa_label: str | None = None
    wa_contactName: str | None = None
    name: str | None = None

def base(host: str) -> str: return f"https://{host}"
def hdr(tok: str) -> dict:
    return {
        "token": tok,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

def to_dict(m: BaseModel | dict | None) -> dict:
    if m is None: return {}
    if isinstance(m, dict): return {k:v for k,v in m.items() if v is not None}
    return {k:v for k,v in (m.model_dump() if hasattr(m,"model_dump") else m.dict()) .items() if v is not None}

async def _uaz_find(host: str, tok: str, payload: dict):
    url = f"{base(host)}/chat/find"
    async with httpx.AsyncClient(timeout=40.0) as c:
        r = await c.post(url, headers=hdr(tok), json=payload)
        if r.status_code >= 400:
            raise HTTPException(r.status_code, r.text)
        return r.json()

def _pick_items(data):
    if isinstance(data, list): return data
    if isinstance(data, dict):
        for k in ("chats","items","data","result","rows"):
            v = data.get(k)
            if isinstance(v, list): return v
    return []

@router.post("/chats")
async def find_chats(body: ChatFind | None, user=Depends(decode_jwt)):
    host, tok = user["host"], user["token"]

    # Sequência de tentativas (algumas instâncias exigem corpo vazio)
    variants = [
        {},  # 1) vazio
        {"sort": "-wa_lastMsgTimestamp", "limit": 100, "offset": 0},  # 2) mínimo
        to_dict(body or ChatFind()),  # 3) payload padrão do nosso app
    ]

    last_data = None
    for payload in variants:
        data = await _uaz_find(host, tok, payload)
        items = _pick_items(data)
        if items:  # achou algo
            return {"items": items}
        last_data = data

    # nada encontrado: devolve vazio mas indica o último "shape" no header de erro
    # (mantemos 200 para não quebrar o front)
    return {"items": []}

@router.get("/chats/count")
async def chats_count(user=Depends(decode_jwt)):
    host, tok = user["host"], user["token"]
    url = f"{base(host)}/chat/count"
    async with httpx.AsyncClient(timeout=20.0) as c:
        r = await c.get(url, headers={"token": tok, "Accept": "application/json"})
        if r.status_code >= 400: raise HTTPException(r.status_code, r.text)
        return r.json()

# Endpoint de diagnóstico: retorna a resposta CRUA da UAZAPI para 3 variantes
@router.get("/chats/debug")
async def chats_debug(user=Depends(decode_jwt)):
    host, tok = user["host"], user["token"]
    variants = [
        ("empty", {}),
        ("minimal", {"sort": "-wa_lastMsgTimestamp", "limit": 100, "offset": 0}),
        ("default", {"operator":"AND","sort":"-wa_lastMsgTimestamp","limit":50,"offset":0}),
    ]
    out = {}
    for name, payload in variants:
        try:
            data = await _uaz_find(host, tok, payload)
            out[name] = {
                "payload": payload,
                "items_len": len(_pick_items(data)),
                "raw": data,
            }
        except HTTPException as e:
            out[name] = {"payload": payload, "error": e.detail}
    return out
