# app/routes/ai.py
from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional

from app.services.lead_status import (  # type: ignore
    upsert_lead_status,
    should_reclassify,
    get_lead_status,
)

# ------------------------------------------------------------------
# Utilidades
# ------------------------------------------------------------------

def _normalize_stage(s: str) -> str:
    s = (s or "").strip().lower()
    if s.startswith("contato"):
        return "contatos"
    if "lead_quente" in s or "quente" in s:
        return "lead_quente"
    if s == "lead":
        return "lead"
    return "contatos"

def _last_ts_guard(ts: Optional[int]) -> int:
    try:
        n = int(ts or 0)
    except Exception:
        return 0
    if len(str(n)) == 10:
        n *= 1000
    return n

# ------------------------------------------------------------------
# Heurística/placeholder de classificação
# ------------------------------------------------------------------
async def _heuristic_stage(chatid: str, ctx: Dict[str, Any] | None = None, limit: int = 200) -> str:
    """
    Placeholder simples: se não houver um classificador externo plugado,
    devolve 'contatos'. Aqui você pode plugar sua IA real se quiser.
    """
    # >>> plugar IA real aqui, se existir <<<
    # e.g.: chamar um endpoint interno, OpenAI, etc.
    await asyncio.sleep(0)  # só p/ manter assinatura assíncrona
    return "contatos"

# ------------------------------------------------------------------
# API pública usada pelos outros módulos
# ------------------------------------------------------------------
async def classify_chat(
    chatid: str,
    persist: bool = True,
    limit: int = 200,
    ctx: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """
    Retorna {"stage": "..."}.
    - Se já existir no banco e não precisar reclassificar, devolve o salvo.
    - Caso precise (ou não exista), usa a heurística/IA e persiste se `persist=True`.
    """
    instance_id = str((ctx or {}).get("instance_id") or "")

    # tenta usar o salvo, se não precisar reclassificar
    try:
        cur = await get_lead_status(instance_id, chatid)
    except Exception:
        cur = None

    last_msg_ts = None  # se quiser, passe por ctx e use aqui

    need_reclass = True
    if cur and cur.get("stage"):
        try:
            need_reclass = await should_reclassify(
                instance_id, chatid,
                last_msg_ts=_last_ts_guard(last_msg_ts),
                last_from_me=None,
            )
        except Exception:
            need_reclass = False

        if not need_reclass:
            return {"stage": _normalize_stage(str(cur["stage"]))}

    # classifica
    stage = await _heuristic_stage(chatid, ctx=ctx, limit=limit)
    stage = _normalize_stage(stage)

    # persiste se pedido
    if persist:
        try:
            await upsert_lead_status(
                instance_id,
                chatid,
                stage,
                last_msg_ts=_last_ts_guard(last_msg_ts),
                last_from_me=False,
            )
        except Exception:
            pass

    return {"stage": stage}

# ------------------------------------------------------------------
# Compatibilidade com versões antigas
# ------------------------------------------------------------------
async def classify_stage(
    chatid: str,
    persist: bool = True,
    limit: int = 200,
    ctx: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """
    Alias de compatibilidade. Antigo nome importado por messages.py.
    """
    return await classify_chat(chatid=chatid, persist=persist, limit=limit, ctx=ctx)
