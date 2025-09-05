# app/routes/ai.py
from __future__ import annotations

import asyncio
import re
from typing import Any, Dict, List, Optional

import httpx  # <— precisamos buscar mensagens na UAZAPI

from app.services.lead_status import (  # type: ignore
    upsert_lead_status,
    should_reclassify,
    get_lead_status,
)

# =========================
# Normalização / utilitários
# =========================
def _normalize_stage(s: str) -> str:
    s = (s or "").strip().lower()
    if s.startswith("contato"):
        return "contatos"
    if "lead_quente" in s or "quente" in s:
        return "lead_quente"
    if s == "lead":
        return "lead"
    return "contatos"


def _to_ms(ts: Optional[int | str]) -> int:
    try:
        n = int(ts or 0)
    except Exception:
        return 0
    if len(str(abs(n))) == 10:  # epoch s
        n *= 1000
    return n


def _is_from_me(m: Dict[str, Any]) -> bool:
    return bool(
        m.get("fromMe")
        or m.get("fromme")
        or m.get("from_me")
        or (isinstance(m.get("key"), dict) and m["key"].get("fromMe"))
        or (
            isinstance(m.get("message"), dict)
            and isinstance(m["message"].get("key"), dict)
            and m["message"]["key"].get("fromMe")
        )
        or (isinstance(m.get("sender"), dict) and m["sender"].get("fromMe"))
        or (isinstance(m.get("id"), str) and m["id"].startswith("true_"))
        or m.get("user") == "me"
    )


def _text_of(m: Dict[str, Any]) -> str:
    mm = m.get("message") or {}
    for path in (
        ("text",),
        ("caption",),
        ("body",),
        ("message", "text"),
        ("message", "conversation"),
        ("message", "extendedTextMessage", "text"),
        ("message", "imageMessage", "caption"),
        ("message", "videoMessage", "caption"),
        ("message", "documentMessage", "caption"),
    ):
        cur: Any = m
        ok = True
        for k in path:
            if not isinstance(cur, dict) or k not in cur:
                ok = False
                break
            cur = cur[k]
        if ok and isinstance(cur, str) and cur.strip():
            return cur.strip()
    return ""


def _ts_of(m: Dict[str, Any]) -> int:
    return _to_ms(
        m.get("messageTimestamp")
        or m.get("timestamp")
        or m.get("t")
        or (m.get("message") or {}).get("messageTimestamp")
        or 0
    )


# =========================
# Regras de classificação
# =========================
WINDOW_DAYS = 21

HOT_STRONG = {
    "fechar", "fechamos", "fechamento",
    "pix", "comprovante", "paguei", "pagar", "pagamento", "boleto",
    "nota fiscal", "nf-e", "nfe",
    "contrato", "assinar", "assinatura",
    "endereço", "localização", "location", "mandar localização",
    "horário", "agendar", "agendamento", "marcar", "marcamos",
    "entrega hoje", "hoje ainda", "agora", "sim pode ser", "pode ser sim",
}

HOT_PRICE = {"preço", "valor", "quanto", "orçamento", "cotação"}

ONLY_GREETINGS = {
    "oi", "olá", "ola", "bom dia", "boa tarde", "boa noite",
    "ok", "blz", "beleza", "certo", "show", "👍", "👋",
}

MONEY_RE = re.compile(r"(?:\br\$|\b\d{1,3}(?:\.\d{3})*(?:,\d{2})?\b|\b\d+[km]\b)", re.I)


def _days_between_ms(a_ms: int, b_ms: int) -> float:
    return abs(a_ms - b_ms) / 86_400_000.0


def _stage_from_messages(messages: List[Dict[str, Any]]) -> tuple[str, int]:
    """
    Retorna (stage, last_msg_ts_ms) baseado nas regras.
    """
    if not messages:
        return "contatos", 0

    msgs = messages[-40:]
    now_ms = 0
    for m in msgs:
        now_ms = max(now_ms, _ts_of(m))

    score = 0
    any_real_text = False
    only_greetings = True

    for m in msgs:
        txt_raw = _text_of(m)
        txt = txt_raw.lower()
        ts = _ts_of(m)
        if txt_raw.strip():
            any_real_text = True

        in_window = (_days_between_ms(ts, now_ms) <= WINDOW_DAYS) if now_ms else True
        from_client = not _is_from_me(m)

        if txt and any(w == txt for w in ONLY_GREETINGS):
            pass
        else:
            if txt.strip():
                only_greetings = False

        if in_window and from_client:
            if any(k in txt for k in HOT_STRONG):
                score += 3
            if any(k in txt for k in HOT_PRICE) or MONEY_RE.search(txt):
                score += 2
            if m.get("message", {}).get("listResponseMessage") or m.get("message", {}).get("buttonsResponseMessage"):
                score += 1

    if score >= 3:
        return "lead_quente", now_ms
    if any_real_text and not only_greetings:
        return "lead", now_ms
    return "contatos", now_ms


# =========================
# API pública (usada pelas rotas)
# =========================
async def classify_chat(
    chatid: str,
    persist: bool = True,
    limit: int = 200,
    ctx: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """
    Retorna {"stage": "..."} usando:
    - Banco (se existir e NÃO precisar reclassificar);
    - Caso contrário, busca mensagens na UAZAPI, aplica regra e persiste (se persist=True).
    """
    ctx = ctx or {}
    instance_id = str(
        ctx.get("instance_id")
        or ctx.get("phone_number_id")
        or ctx.get("pnid")
        or ctx.get("sub")
        or ""
    )

    # 1) tenta banco
    try:
        cur = await get_lead_status(instance_id, chatid)
    except Exception:
        cur = None

    # por padrão, assume que deve reclassificar quando não temos last_msg_ts novo
    need_reclass = True
    if cur and cur.get("stage"):
        try:
            need_reclass = await should_reclassify(
                instance_id,
                chatid,
                last_msg_ts=None,
                last_from_me=None,
            )
        except Exception:
            need_reclass = False

        if not need_reclass:
            return {"stage": _normalize_stage(str(cur["stage"]))}

    # 2) busca mensagens na UAZAPI e aplica regras
    base = f"https://{ctx['host']}"
    headers = {"token": ctx["token"]}
    payload = {"chatid": chatid, "limit": int(limit or 200), "offset": 0, "sort": "-messageTimestamp"}

    async with httpx.AsyncClient(timeout=20) as cli:
        r = await cli.post(f"{base}/message/find", json=payload, headers=headers)
        r.raise_for_status()
        data = r.json()

    items: List[Dict[str, Any]] = []
    if isinstance(data, dict):
        if isinstance(data.get("items"), list):
            items = data["items"]
        else:
            for k in ("data", "results", "messages"):
                v = data.get(k)
                if isinstance(v, list):
                    items = v
                    break
    elif isinstance(data, list):
        items = data

    stage, last_ts = _stage_from_messages(items)
    stage = _normalize_stage(stage)

    if persist:
        try:
            await upsert_lead_status(
                instance_id,
                chatid,
                stage,
                last_msg_ts=int(last_ts or 0),
                last_from_me=False,
            )
        except Exception:
            pass

    return {"stage": stage}


# Compat: alguns módulos importam esse nome
async def classify_stage(
    chatid: str,
    persist: bool = True,
    limit: int = 200,
    ctx: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    return await classify_chat(chatid=chatid, persist=persist, limit=limit, ctx=ctx)


# Usado por app/routes/media.py (quando já temos as mensagens)
async def classify_by_rules(
    messages: List[Dict[str, Any]] | None = None,
    chatid: Optional[str] = None,
    ctx: Dict[str, Any] | None = None,
    persist: bool = True,
) -> Dict[str, Any]:
    msgs = messages or []
    stage, last_ts = _stage_from_messages(msgs)
    stage = _normalize_stage(stage)

    if persist and chatid:
        instance_id = str(
            (ctx or {}).get("instance_id")
            or (ctx or {}).get("phone_number_id")
            or (ctx or {}).get("pnid")
            or (ctx or {}).get("sub")
            or ""
        )
        try:
            await upsert_lead_status(
                instance_id,
                chatid,
                stage,
                last_msg_ts=int(last_ts or 0),
                last_from_me=False,
            )
        except Exception:
            pass

    return {"stage": stage}
