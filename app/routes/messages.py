# app/routes/messages.py
from __future__ import annotations
from typing import Dict, Any, List
import inspect
import httpx
from fastapi import APIRouter, Depends, HTTPException, Body

from app.routes.deps import get_uazapi_ctx

# --- tenta usar a regra oficial do módulo ai.py; se não existir, usa fallback local
try:
    from app.routes.ai import classify_stage as _ai_classify_stage  # type: ignore
except Exception:
    _ai_classify_stage = None  # sem dependência dura

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


# -------- fallback simples (caso não exista app.routes.ai.classify_stage) ----------
def _fallback_classify_stage(items: List[Dict[str, Any]]) -> str:
    """
    Heurística bem simples:
    - Se há alguma mensagem recente com palavras-quentes => lead_quente
    - Se existe qualquer mensagem => lead
    - Senão => contatos
    """
    HOT = ("fechar", "fechamos", "pix", "pagar", "preço", "valor", "contrato", "assinar")
    text_fields = (
        "text",
        "caption",
        "body",
        ("message", "text"),
        ("message", "conversation"),
        ("message", "extendedTextMessage", "text"),
    )

    def _get_text(m: Dict[str, Any]) -> str:
        for f in text_fields:
            if isinstance(f, str):
                v = m.get(f)
            else:
                v = m
                for k in f:
                    if not isinstance(v, dict):
                        v = None
                        break
                    v = v.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip().lower()
        return ""

    hot = any(any(tok in _get_text(m) for tok in HOT) for m in items)
    if hot:
        return "lead_quente"
    if items:
        return "lead"
    return "contatos"


async def _classify_stage(items: List[Dict[str, Any]]) -> str:
    """
    Invoca a regra do módulo ai.py se disponível (sincrona ou assíncrona).
    Caso contrário, usa heurística local.
    """
    if _ai_classify_stage:
        try:
            res = _ai_classify_stage(items)
            if inspect.isawaitable(res):
                res = await res  # type: ignore[func-returns-value]
            if isinstance(res, dict):
                # alguns retornos podem vir como {"stage": "..."}
                stage = res.get("stage")
                if isinstance(stage, str):
                    return stage
            if isinstance(res, str) and res:
                return res
        except Exception:
            pass
    return _fallback_classify_stage(items)


@router.post("/messages")
async def find_messages(body: dict | None = Body(None), ctx=Depends(get_uazapi_ctx)):
    """
    Proxy para UAZAPI /message/find.
    Além de devolver normalizado, já calcula e retorna `stage`.
    """
    if not body or not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Body inválido")

    chatid = body.get("chatid")
    if not chatid or not isinstance(chatid, str) or not chatid.strip():
        raise HTTPException(status_code=400, detail="chatid é obrigatório")

    base, headers = _uaz(ctx)
    url = f"{base}/message/find"

    payload = {
        "chatid": chatid.strip(),
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
    items: List[Dict[str, Any]] = wrapped["items"]

    # Classificação instantânea no backend (com fallback se ai.classify_stage não existir)
    stage = await _classify_stage(items)

    return {"items": items, "stage": stage}
