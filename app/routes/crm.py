# app/routes/crm.py
import os, json, time, re
from typing import Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Body
import httpx

from app.auth import get_current_user  # proteção
from app.routes.deps import get_uazapi_ctx  # host/token da UAZAPI

router = APIRouter()

STAGES: List[str] = [
    "novo",
    "sem_resposta",
    "interessado",
    "em_negociacao",
    "fechou",
    "descartado",
]

DATA_DIR = os.getenv("CRM_DATA_DIR", "/app/data")
os.makedirs(DATA_DIR, exist_ok=True)
CRM_STORE = os.path.join(DATA_DIR, "crm.json")

_store: Dict[str, Dict] = {}


def _load_store() -> None:
    global _store
    try:
        if os.path.exists(CRM_STORE):
            with open(CRM_STORE, "r", encoding="utf-8") as f:
                _store = json.load(f)
        else:
            _store = {}
    except Exception:
        _store = {}


def _save_store() -> None:
    tmp = CRM_STORE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(_store, f, ensure_ascii=False)
    os.replace(tmp, CRM_STORE)


def _now() -> int:
    return int(time.time())


def _normalize_chatid(chatid: Optional[str] = None, number: Optional[str] = None) -> str:
    """
    Aceita:
      - chatid/wa_chatid (já com @s.whatsapp.net)
      - number (55319...) e converte para @s.whatsapp.net
    """
    if chatid and "@s.whatsapp.net" in chatid:
        return chatid.strip()

    raw = (chatid or number or "").strip()
    if not raw:
        return ""

    # só dígitos => é number
    if re.fullmatch(r"\d{10,15}", raw):
        return f"{raw}@s.whatsapp.net"

    # já veio @g.us (grupo) ou outro sufixo? mantém
    if "@" in raw:
        return raw

    return ""


_load_store()


@router.get("/views")
async def crm_views(user=Depends(get_current_user)):
    counts = {s: 0 for s in STAGES}
    for v in _store.values():
        st = v.get("stage", "novo")
        if st in counts:
            counts[st] += 1
    return {"counts": counts, "stages": STAGES}


@router.get("/list")
async def crm_list(
    stage: str = Query(...),
    q: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    user=Depends(get_current_user),
):
    if stage not in STAGES:
        raise HTTPException(400, f"Estágio inválido: {stage}")

    items = [v for v in _store.values() if v.get("stage") == stage]

    if q:
        ql = q.lower()
        items = [v for v in items if ql in v.get("chatid", "").lower() or ql in (v.get("notes") or "").lower()]

    items.sort(key=lambda x: x.get("updated_at", 0), reverse=True)

    return {
        "items": items[offset : offset + limit],
        "total": len(items),
        "stage": stage,
    }


@router.get("/item")
async def crm_item(chatid: str = Query(...), user=Depends(get_current_user)):
    return _store.get(chatid) or {"chatid": chatid, "stage": "novo", "notes": "", "updated_at": 0}


@router.post("/status")
async def crm_set_status(
    payload: Dict = Body(..., example={
        "chatid": "55319...@s.whatsapp.net",
        "stage": "interessado",
        "notes": "",
        # OU: "wa_chatid": "...", OU: "number": "55319..."
    }),
    user=Depends(get_current_user),
):
    stage = (payload.get("stage") or "").strip()
    if stage not in STAGES:
        raise HTTPException(400, f"Estágio inválido: {stage}")

    chatid = _normalize_chatid(
        chatid=payload.get("chatid") or payload.get("wa_chatid"),
        number=payload.get("number"),
    )
    if not chatid:
        raise HTTPException(400, "chatid/wa_chatid/number é obrigatório")

    notes = payload.get("notes", "")
    meta = payload.get("meta") or {}

    rec = _store.get(chatid) or {"chatid": chatid}
    rec.update({"stage": stage, "notes": notes, "meta": meta, "updated_at": _now()})
    _store[chatid] = rec
    _save_store()
    return {"ok": True, "item": rec}


@router.delete("/status")
async def crm_clear_status(chatid: str = Query(...), user=Depends(get_current_user)):
    if chatid in _store:
        _store.pop(chatid, None)
        _save_store()
    return {"ok": True, "chatid": chatid}


@router.post("/sync")
async def crm_sync_from_uazapi(
    limit: int = Body(300, embed=True),
    ctx=Depends(get_uazapi_ctx),
    user=Depends(get_current_user),
):
    """
    Sincroniza automaticamente com a lista de chats da UAZAPI.
    Cria registros que não existirem no estágio 'novo'.
    """
    base = f"https://{ctx['host']}"
    headers = {"token": ctx["token"]}

    created = 0
    async with httpx.AsyncClient(timeout=25) as cli:
        # tenta múltiplas variações de endpoint
        attempts = [
            ("POST", f"{base}/chat/list", {"limit": limit, "offset": 0}),
            ("GET",  f"{base}/chat/list?limit={limit}&offset=0", None),
        ]
        data = None
        for method, url, body in attempts:
            r = await (cli.post(url, headers=headers, json=body) if method == "POST" else cli.get(url, headers=headers))
            if 200 <= r.status_code < 300:
                try:
                    j = r.json()
                except Exception:
                    continue
                data = j.get("items") or j.get("data") or j
                if isinstance(data, list):
                    break

        if not isinstance(data, list):
            raise HTTPException(502, "Falha ao obter lista de chats da UAZAPI para sincronização")

        for c in data:
            wa_chatid = c.get("wa_chatid") or c.get("chatid") or c.get("wa_fastid") or c.get("id")
            chatid = _normalize_chatid(chatid=wa_chatid)
            if not chatid:
                continue

            if chatid not in _store:
                _store[chatid] = {
                    "chatid": chatid,
                    "stage": "novo",
                    "notes": "",
                    "meta": {},
                    "updated_at": _now(),
                }
                created += 1

    if created:
        _save_store()
    return {"ok": True, "created": created, "total": len(_store)}
