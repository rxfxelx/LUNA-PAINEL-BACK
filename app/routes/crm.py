# app/routes/crm.py
import os, json, time
from typing import Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Body

# Proteção por auth igual às demais rotas
from app.auth import get_current_user  # noqa

router = APIRouter()

# Estágios do funil
STAGES: List[str] = [
    "novo",
    "sem_resposta",
    "interessado",
    "em_negociacao",
    "fechou",
    "descartado",
]

# Persistência simples em arquivo
DATA_DIR = os.getenv("CRM_DATA_DIR", "/app/data")
os.makedirs(DATA_DIR, exist_ok=True)
CRM_STORE = os.path.join(DATA_DIR, "crm.json")

# Estrutura:
# {
#   "<chatid>": {
#      "chatid": "...",
#      "stage": "interessado",
#      "notes": "",
#      "updated_at": 1710000000,
#      "meta": {... opcional ...}
#   },
#   ...
# }
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


_load_store()


@router.get("/views")
async def crm_views(user=Depends(get_current_user)):
    """
    Retorna contadores por estágio.
    """
    counts = {s: 0 for s in STAGES}
    for v in _store.values():
        st = v.get("stage", "novo")
        if st in counts:
            counts[st] += 1
    return {"counts": counts, "stages": STAGES}


@router.get("/list")
async def crm_list(
    stage: str = Query(..., description="Estágio do funil"),
    q: Optional[str] = Query(None, description="Filtro por chatid ou texto"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    user=Depends(get_current_user),
):
    """
    Lista itens por estágio, ordenados por updated_at desc.
    """
    if stage not in STAGES:
        raise HTTPException(400, f"Estágio inválido: {stage}")

    # filtra por estágio
    items = [v for v in _store.values() if v.get("stage") == stage]

    # busca simples
    if q:
        ql = q.lower()
        items = [v for v in items if ql in v.get("chatid", "").lower() or ql in (v.get("notes") or "").lower()]

    # ordena por mais recente
    items.sort(key=lambda x: x.get("updated_at", 0), reverse=True)

    return {
        "items": items[offset : offset + limit],
        "total": len(items),
        "stage": stage,
    }


@router.get("/item")
async def crm_item(chatid: str = Query(...), user=Depends(get_current_user)):
    """
    Retorna o registro CRM de um chat.
    """
    return _store.get(chatid) or {"chatid": chatid, "stage": "novo", "notes": "", "updated_at": 0}


@router.post("/status")
async def crm_set_status(
    payload: Dict = Body(..., example={"chatid": "55319...@s.whatsapp.net", "stage": "interessado", "notes": ""}),
    user=Depends(get_current_user),
):
    """
    Define/atualiza o estágio de um chat.
    """
    chatid = (payload.get("chatid") or "").strip()
    stage = (payload.get("stage") or "").strip()
    notes = payload.get("notes", "")
    meta = payload.get("meta") or {}

    if not chatid:
        raise HTTPException(400, "chatid é obrigatório")
    if stage not in STAGES:
        raise HTTPException(400, f"Estágio inválido: {stage}")

    rec = _store.get(chatid) or {"chatid": chatid}
    rec.update(
        {
            "stage": stage,
            "notes": notes,
            "meta": meta,
            "updated_at": _now(),
        }
    )
    _store[chatid] = rec
    _save_store()
    return {"ok": True, "item": rec}


@router.delete("/status")
async def crm_clear_status(
    chatid: str = Query(..., description="Chat a limpar do CRM"),
    user=Depends(get_current_user),
):
    """
    Remove o registro CRM do chat.
    """
    if chatid in _store:
        _store.pop(chatid, None)
        _save_store()
    return {"ok": True, "chatid": chatid}
