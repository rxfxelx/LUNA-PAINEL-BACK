# app/routes/messages.py
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.core.stage import classify_and_cache

router = APIRouter()

# ====== MODELOS DE I/O (mantenha compatível com o que já existia) ======
class MessagesQuery(BaseModel):
    chatid: str
    limit: Optional[int] = 200
    offset: Optional[int] = 0
    sort: Optional[str] = "-messageTimestamp"

# ATENÇÃO:
# Troque esta função para usar a sua camada real de dados.
# Aqui está um placeholder que você já deve ter no seu projeto.
async def _fetch_messages_from_store(chatid: str, limit: int, offset: int, sort: str) -> List[Dict[str, Any]]:
    """
    Implemente de acordo com seu storage (Mongo/SQL/etc).
    Deve retornar a MESMA estrutura que o endpoint já retornava.
    """
    raise HTTPException(status_code=500, detail="Data layer não implementada aqui")

@router.post("/messages")
async def list_messages(payload: MessagesQuery):
    """
    Retorna mensagens e JÁ inclui 'stage' calculado no servidor,
    para que a classificação fique instantânea no front.
    """
    try:
        items = await _fetch_messages_from_store(
            chatid=payload.chatid,
            limit=payload.limit or 200,
            offset=payload.offset or 0,
            sort=payload.sort or "-messageTimestamp",
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao buscar mensagens: {e}")

    # ---- CLASSIFICA AQUI, ANTES DE RESPONDER ----
    try:
        stage, last_key = classify_and_cache(payload.chatid, items or [])
    except Exception as e:
        # mesmo que falhe a classificação, não bloqueia retorno das mensagens
        stage, last_key = "contatos", "error"

    return {
        "items": items or [],
        "stage": stage,          # <<< novo
        "stage_last_key": last_key,
    }
