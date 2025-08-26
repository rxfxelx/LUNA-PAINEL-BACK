# app/routes/crm.py
from fastapi import APIRouter, Depends, HTTPException, Query, Body
from app.routes.deps import get_uazapi_ctx
from app.db import inst_key, upsert_status, get_status, list_by_stage, counts_by_stage
import httpx

router = APIRouter()

# Estágios padrão
STAGES = ["novo", "sem_resposta", "interessado", "em_negociacao", "fechou", "descartado"]

@router.get("/crm/stages")
def crm_stages():
    return {"stages": STAGES}

@router.get("/crm/views")
def crm_views(ctx=Depends(get_uazapi_ctx)):
    ik = inst_key(ctx["token"])
    counts = counts_by_stage(ik)
    return {"counts": counts, "stages": STAGES}

@router.get("/crm/list")
async def crm_list(
    stage: str = Query(..., description="Estágio"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    ctx=Depends(get_uazapi_ctx)
):
    if stage not in STAGES:
        raise HTTPException(status_code=400, detail="stage inválido")

    ik = inst_key(ctx["token"])
    rows = list_by_stage(ik, stage, limit=limit, offset=offset)

    # Junta informações do chat pela UAZAPI (quando possível)
    base = f"https://{ctx['host']}"
    headers = {"token": ctx["token"]}
    items = []
    async with httpx.AsyncClient(timeout=20) as cli:
        for r in rows:
            wa_chatid = r["chatid"]
            # tentamos buscar um pedaço do chat para renderizar igual à lista geral
            try:
                resp = await cli.post(f"{base}/chat/find", headers=headers, json={
                    "operator": "AND", "limit": 1, "offset": 0, "wa_chatid": wa_chatid
                })
                chat = None
                if resp.status_code == 200:
                    data = resp.json()
                    arr = data if isinstance(data, list) else data.get("items") or []
                    if isinstance(arr, list) and arr:
                        chat = arr[0]
                items.append({"chat": chat, "crm": r})
            except Exception:
                items.append({"chat": None, "crm": r})

    return {"items": items, "stage": stage, "limit": limit, "offset": offset}

@router.post("/crm/status")
def crm_set_status(
    payload: dict = Body(..., example={"chatid": "5531...@s.whatsapp.net", "stage": "interessado", "notes": ""}),
    ctx=Depends(get_uazapi_ctx)
):
    chatid = (payload.get("chatid") or "").strip()
    stage = (payload.get("stage") or "").strip()
    notes = payload.get("notes")
    if not chatid:
        raise HTTPException(status_code=400, detail="chatid é obrigatório")
    if stage not in STAGES:
        raise HTTPException(status_code=400, detail="stage inválido")

    ik = inst_key(ctx["token"])
    upsert_status(ik, chatid, stage, notes)
    cur = get_status(ik, chatid)
    return {"ok": True, "chatid": chatid, "status": cur}
