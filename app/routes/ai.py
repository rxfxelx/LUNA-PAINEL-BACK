# app/routes/ai.py
from __future__ import annotations

import asyncio
import re
from typing import Any, Dict, List, Optional

from app.services.lead_status import (  # type: ignore
    upsert_lead_status,
    should_reclassify,
    get_lead_status,
)

# ------------------------------------------------------------------
# Normalização de estágio
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
    # aceita epoch em segundos
    if len(str(abs(n))) == 10:
        n *= 1000
    return n

# ------------------------------------------------------------------
# Heurística simples (placeholder)
# ------------------------------------------------------------------
_LEAD_QUENTE_PAT = re.compile(
    r"\b(fechar|fechamos|contratar|contrato|comprar|pag(ar|amento)|boleto|pix|"
    r"cart[aã]o|assinar|onde assino|quando posso|vamos fechar|quero fechar|"
    r"manda a fatura|emite.*nota|nota fiscal|NF)\b",
    re.IGNORECASE,
)

_LEAD_PAT = re.compile(
    r"\b(interesse|tenho interesse|quero saber|mais informa[cç][oõ]es?|"
    r"como funciona|pre[cç]o|valor(es)?|quanto custa|planos?|pacotes?)\b",
    re.IGNORECASE,
)

def _extract_plain_text(m: Dict[str, Any]) -> str:
    mm = m.get("message") or {}
    return (
        m.get("text")
        or m.get("caption")
        or mm.get("text")
        or (mm.get("extendedTextMessage") or {}).get("text")
        or mm.get("conversation")
        or m.get("body")
        or ""
    ) or ""

def _heuristic_from_messages(messages: List[Dict[str, Any]]) -> str:
    """
    Regras simples em cima do texto das mensagens.
    Se encontrar termos "quentes", marca como lead_quente; senão, se achar interesse,
    marca como lead; fallback: contatos.
    """
    # Examina últimas ~60 mensagens (se vier muita coisa)
    msgs = messages[-60:] if len(messages) > 60 else messages
    found_lead_quente = False
    found_lead = False

    for m in msgs:
        txt = _extract_plain_text(m)
        if not txt:
            continue
        if _LEAD_QUENTE_PAT.search(txt):
            found_lead_quente = True
            break
        if _LEAD_PAT.search(txt):
            found_lead = True

    if found_lead_quente:
        return "lead_quente"
    if found_lead:
        return "lead"
    return "contatos"

async def _heuristic_stage(chatid: str, ctx: Dict[str, Any] | None = None, limit: int = 200) -> str:
    """
    Placeholder para quando não houver classificador externo plugado.
    Aqui você pode integrar sua IA/serviço real.
    """
    await asyncio.sleep(0)
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
    - Se já existir no banco e NÃO precisar reclassificar, devolve o salvo.
    - Caso precise (ou não exista), usa a heurística/IA e persiste se `persist=True`.
    """
    instance_id = str((ctx or {}).get("instance_id") or "")

    # tenta usar o salvo, se não precisar reclassificar
    try:
        cur = await get_lead_status(instance_id, chatid)
    except Exception:
        cur = None

    last_msg_ts = None  # opcional: pode vir no ctx e ser passado para should_reclassify
    need_reclass = True
    if cur and cur.get("stage"):
        try:
            need_reclass = await should_reclassify(
                instance_id, chatid,
                last_msg_ts=_last_ts_guard(last_msg_ts),
                last_from_me=None,
            )
        except Exception:
            # se o should_reclassify falhar, assume que NÃO precisa reclassificar
            need_reclass = False

        if not need_reclass:
            return {"stage": _normalize_stage(str(cur["stage"]))}

    # classifica (placeholder)
    stage = await _heuristic_stage(chatid, ctx=ctx, limit=limit)
    stage = _normalize_stage(stage)

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
# Compat: alguns módulos antigos importam esse nome
# ------------------------------------------------------------------
async def classify_stage(
    chatid: str,
    persist: bool = True,
    limit: int = 200,
    ctx: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    return await classify_chat(chatid=chatid, persist=persist, limit=limit, ctx=ctx)

# ------------------------------------------------------------------
# Usado por app/routes/media.py
# ------------------------------------------------------------------
async def classify_by_rules(
    messages: List[Dict[str, Any]] | None = None,
    chatid: Optional[str] = None,
    ctx: Dict[str, Any] | None = None,
    persist: bool = True,
) -> Dict[str, Any]:
    """
    Classifica em cima de uma lista de mensagens (regras locais).
    Se vier `chatid`, tenta persistir/atualizar no banco.
    """
    msgs = messages or []
    stage = _heuristic_from_messages(msgs)
    stage = _normalize_stage(stage)

    instance_id = str((ctx or {}).get("instance_id") or "")

    if persist and chatid:
        try:
            # tenta deduzir last_msg_ts básico
            last_ts = 0
            try:
                if msgs:
                    # pega o maior timestamp entre as mensagens
                    for m in msgs:
                        ts = (
                            m.get("messageTimestamp")
                            or m.get("timestamp")
                            or m.get("t")
                            or (m.get("message") or {}).get("messageTimestamp")
                            or 0
                        )
                        ts = _last_ts_guard(ts)
                        if ts > last_ts:
                            last_ts = ts
            except Exception:
                last_ts = 0

            await upsert_lead_status(
                instance_id,
                chatid,
                stage,
                last_msg_ts=last_ts,
                last_from_me=False,
            )
        except Exception:
            pass

    return {"stage": stage}
