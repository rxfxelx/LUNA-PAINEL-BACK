# app/routes/messages.py
from __future__ import annotations
import httpx
from fastapi import APIRouter, Depends, HTTPException
from app.routes.deps import get_uazapi_ctx
from app.core.classify import classify_stage

router = APIRouter()

def _uaz(ctx):
    base = f"https://{ctx['host']}"
    headers = {"token": ctx["token"]}
    return base, headers

def _normalize_items(resp_json):
    if isinstance(resp_json, dict):
        if isinstance(resp_json.get("items"), list):
            return {"items": resp_json["items"]}
        for key in ("data", "results", "messages"):
            val = resp_json.get(key)
            if isinstance(val, list):
                return {"items": val}
        return {"items": []}
    if isinstance(resp_json, list):
        return {"items": resp_json}
    return {"items": []}

@router.post("/messages")
async def find_messages(body: dict | None = None, ctx=Depends(get_uazapi_ctx)):
    """
    Proxy para UAZAPI /message/find (ou equivalente).
    Além de devolver as mensagens normalizadas, já calcula e retorna `stage`
    usando as mesmas regras do front, para ficar instantâneo no carregamento.
    """
    if not body or not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Body inválido")

    chatid = body.get("chatid")
    if not chatid:
        raise HTTPException(status_code=400, detail="chatid é obrigatório")

    base, headers = _uaz(ctx)
    url = f"{base}/message/find"

    # defaults seguros (mantém compat com seu front)
    payload = {
        "chatid": chatid,
        "limit": int(body.get("limit") or 200),
        "offset": int(body.get("offset") or 0),
        "sort": body.get("sort") or "-messageTimestamp",
    }

    async with httpx.AsyncClient(timeout=30) as cli:
        r = await cli.post(url, json=payload, headers=headers)

    if r.status_code >= 400:
        raise HTTPException(status_code=r.status_code, detail=r.text)

    try:
        data = r.json()
    except Exception:
        raise HTTPException(status_code=502, detail="Resposta inválida da UAZAPI em /message/find")

    wrapped = _normalize_items(data)
    items = wrapped["items"]

    # === Classificação instantânea no backend ===
    stage = classify_stage(items)

    # Devolve no mesmo payload para o front já usar sem esperar nada extra
    return {"items": items, "stage": stage}
