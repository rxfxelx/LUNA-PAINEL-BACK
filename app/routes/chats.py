from __future__ import annotations

import asyncio
import json
import os
import re
import time
from typing import Any, Dict, List, Optional, Tuple

import httpx
from fastapi import APIRouter, Depends, HTTPException, Body, Query, Request
from fastapi.responses import StreamingResponse, Response, JSONResponse

from app.routes.deps import get_uazapi_ctx
from app.routes import ai as ai_routes
from app.routes import crm as crm_module
# trocado: usa o wrapper que não derruba a rota com 500 inesperado
from app.routes.deps_billing import require_active_tenant_soft  # bloqueia se inativa (modo tolerante)

# DB helpers de lead status
from app.services.lead_status import (  # type: ignore
    get_lead_status,
    upsert_lead_status,
    should_reclassify,
)

router = APIRouter()

# ---------------- util: CORS helpers ---------------- #
def _load_allowed_origins() -> list[str]:
    """
    Lê FRONTEND_ORIGINS (CSV) e normaliza espaços.
    Ex.: FRONTEND_ORIGINS="https://lunahia.com.br, https://www.lunahia.com.br, http://localhost:3000"
    """
    raw = os.getenv("FRONTEND_ORIGINS", "")
    if not raw.strip():
        return []
    return [o.strip() for o in raw.split(",") if o.strip()]

def _origin_matches_regex(origin: str) -> bool:
    pattern = os.getenv("FRONTEND_ORIGIN_REGEX", "").strip()
    if not pattern:
        return False
    try:
        return re.match(pattern, origin) is not None
    except re.error:
        return False

def _resolve_cors_origin(request: Request) -> Optional[str]:
    """
    Retorna a origem (string) que deve ser refletida no Access-Control-Allow-Origin
    OU None se não deve permitir.
    Se CORS_ALLOW_CREDENTIALS=true, jamais retorna "*" (precisa ser origem específica).
    """
    origin = request.headers.get("origin")
    if not origin:
        return None

    allow_list = _load_allowed_origins()
    allow_re = _origin_matches_regex(origin)
    if origin in allow_list or allow_re:
        # Se permitir credenciais, refletimos a origem específica.
        if os.getenv("CORS_ALLOW_CREDENTIALS", "false").lower() == "true":
            return origin
        # Sem credenciais, poderíamos retornar "*" — porém, para consistência e para
        # proxies mais rígidos, preferimos refletir a origem aprovada.
        return origin

    return None

def _cors_preflight_response(request: Request) -> Response:
    """
    Responde ao preflight com os cabeçalhos CORS adequados.
    - Permite POST e OPTIONS.
    - Ecoa os headers solicitados pelo navegador em Access-Control-Allow-Headers.
    """
    allowed_origin = _resolve_cors_origin(request)
    if not allowed_origin:
        # Origem não autorizada: responde 403 para deixar claro.
        return Response(status_code=403)

    # Quais headers o navegador quer usar na requisição real:
    acrh = request.headers.get("access-control-request-headers", "authorization, content-type, x-instance-id")

    headers = {
        "Access-Control-Allow-Origin": allowed_origin,
        "Access-Control-Allow-Methods": "POST, OPTIONS",
        "Access-Control-Allow-Headers": acrh,
        "Access-Control-Max-Age": "86400",
        "Vary": "Origin, Access-Control-Request-Headers, Access-Control-Request-Method",
    }
    if os.getenv("CORS_ALLOW_CREDENTIALS", "false").lower() == "true":
        headers["Access-Control-Allow-Credentials"] = "true"

    return Response(status_code=204, headers=headers)

def _attach_cors_headers_to_response(request: Request, resp: Response) -> Response:
    """
    Garante que a resposta real (POST /chats/stream) também carregue os headers CORS.
    Útil quando há proxies/CDN que podem interferir no middleware.
    """
    allowed_origin = _resolve_cors_origin(request)
    if allowed_origin:
        resp.headers.setdefault("Access-Control-Allow-Origin", allowed_origin)
        resp.headers.setdefault("Vary", "Origin")
        if os.getenv("CORS_ALLOW_CREDENTIALS", "false").lower() == "true":
            resp.headers.setdefault("Access-Control-Allow-Credentials", "true")
    return resp

# ---------------- util: extrai instance_id do JWT/headers ---------------- #
def _b64url_to_bytes(s: str) -> bytes:
    import base64
    pad = "=" * ((4 - len(s) % 4) % 4)
    return base64.urlsafe_b64decode(s + pad)

def _get_instance_id_from_request(req: Request) -> str:
    # 1) se um middleware já setou
    inst = getattr(req.state, "instance_id", None)
    if inst:
        return str(inst)

    # 2) header auxiliar
    h = req.headers.get("x-instance-id")
    if h:
        return str(h)

    # 3) decodifica JWT sem verificar assinatura
    auth = req.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        token = auth.split(" ", 1)[1].strip()
        parts = token.split(".")
        if len(parts) >= 2:
            try:
                payload = json.loads(_b64url_to_bytes(parts[1]).decode("utf-8"))
                return str(
                    payload.get("instance_id")
                    or payload.get("phone_number_id")
                    or payload.get("pnid")
                    or ""
                )
            except Exception:
                pass
    return ""

# ---------------- cache simples p/ classificação (protege IA) ---------------- #
_CLASSIFY_CACHE: dict[str, tuple[float, str]] = {}  # chatid -> (ts_epoch, stage)
_CLASSIFY_TTL = 300  # 5 minutos

def _uaz(ctx: Dict[str, Any]) -> tuple[str, Dict[str, str]]:
    base = f"https://{ctx['host']}"
    headers = {"token": ctx["token"]}
    return base, headers

def _normalize_items(resp_json: Any) -> Dict[str, List[Dict[str, Any]]]:
    if isinstance(resp_json, dict):
        if isinstance(resp_json.get("items"), list):
            return {"items": resp_json["items"]}
        for key in ("data", "results", "chats"):
            val = resp_json.get(key)
            if isinstance(val, list):
                return {"items": val}
        return {"items": []}
    if isinstance(resp_json, list):
        return {"items": resp_json}
    return {"items": []}

def _pick_chatid(item: Dict[str, Any]) -> str:
    return (
        item.get("wa_chatid")
        or item.get("chatid")
        or item.get("wa_fastid")
        or item.get("id")
        or ""
    )

def _last_msg_ts_of(item: Dict[str, Any]) -> int:
    """Retorna ts em milissegundos (aceita segundos)."""
    ts = (
        item.get("wa_lastMsgTimestamp")
        or item.get("messageTimestamp")
        or item.get("updatedAt")
        or 0
    )
    try:
        n = int(ts)
    except Exception:
        return 0
    s = str(abs(n))
    if len(s) == 10:  # epoch s
        n *= 1000
    return n

async def _maybe_classify_and_persist(
    instance_id: str,
    ctx: Dict[str, Any],
    chatid: str,
    last_msg_ts: Optional[int] = None,
) -> Optional[str]:
    """
    Estratégia:
    - Se tiver no banco -> usa e retorna
    - Se tiver e should_reclassify(...) == False -> não mexe
    - Se não tiver ou precisar reclassificar -> IA e salva
    """
    if not instance_id:
        return None

    # 1) banco
    try:
        rec = await get_lead_status(instance_id, chatid)
    except Exception:
        rec = None

    if rec and rec.get("stage"):
        try:
            need = await should_reclassify(
                instance_id,
                chatid,
                last_msg_ts=last_msg_ts,
                last_from_me=None,
            )
        except Exception:
            need = False

        if not need:
            return str(rec["stage"])

    # 2) cache curto
    now = time.time()
    hit = _CLASSIFY_CACHE.get(chatid)
    if hit and (now - hit[0]) <= _CLASSIFY_TTL:
        stage_cached = hit[1]
        try:
            await upsert_lead_status(
                instance_id,
                chatid,
                stage_cached,
                last_msg_ts=int(last_msg_ts or 0),
                last_from_me=False,
            )
        except Exception:
            pass
        return stage_cached

    # 3) IA
    try:
        res = await asyncio.wait_for(
            ai_routes.classify_chat(
                chatid=chatid,
                persist=False,
                limit=200,
                ctx=ctx,
            ),
            timeout=3.5,
        )
        stage = (res or {}).get("stage")
        if stage:
            _CLASSIFY_CACHE[chatid] = (now, stage)
            try:
                await upsert_lead_status(
                    instance_id,
                    chatid,
                    stage,
                    last_msg_ts=int(last_msg_ts or 0),
                    last_from_me=False,
                )
            except Exception:
                pass
            return stage
    except Exception:
        return None

    return None

# ------------------ Resposta única (paginada) ------------------ #
@router.post("/chats")
async def find_chats(
    request: Request,
    body: dict | None = Body(None),
    classify: bool = Query(
        True,
        description=(
            "Se True, usa banco quando houver; "
            "classifica com IA apenas quando não houver registro ou quando precisar reclassificar."
        ),
    ),
    page_size: int = Query(100, ge=1, le=500),
    max_total: int = Query(5000, ge=1, le=20000),
    _user=Depends(require_active_tenant_soft),   # <<< guard tolerante
    ctx=Depends(get_uazapi_ctx),
):
    instance_id = _get_instance_id_from_request(request)
    base, headers = _uaz(ctx)
    url = f"{base}/chat/find"

    items: list[dict] = []
    offset = 0

    async with httpx.AsyncClient(timeout=30) as cli:
        while len(items) < max_total:
            payload = body if body else {"operator": "AND", "sort": "-wa_lastMsgTimestamp"}
            payload = {**payload, "limit": page_size, "offset": offset}

            r = await cli.post(url, json=payload, headers=headers)
            if r.status_code >= 400:
                raise HTTPException(status_code=r.status_code, detail=r.text)

            try:
                data = r.json()
            except Exception:
                raise HTTPException(502, "Resposta inválida da UAZAPI em /chat/find")

            chunk = _normalize_items(data)["items"]
            if not chunk:
                break

            items.extend(chunk)
            if len(chunk) < page_size:
                break
            offset += page_size

    items = items[:max_total]

    if classify and items:
        async def worker(item: dict):
            chatid = _pick_chatid(item)
            if not chatid:
                return
            last_ts = _last_msg_ts_of(item)
            st = await _maybe_classify_and_persist(instance_id, ctx, chatid, last_msg_ts=last_ts)
            if st:
                item["_stage"] = st
                item["stage"] = st
                try:
                    crm_module.set_status_internal(chatid, st)
                except Exception:
                    pass
            item["_last_ts"] = last_ts

        await asyncio.gather(*(worker(it) for it in items))
    else:
        for it in items:
            it["_last_ts"] = _last_msg_ts_of(it)

    items.sort(key=lambda x: int(x.get("_last_ts") or 0), reverse=True)

    return {"items": items}

# ------------------ Stream NDJSON ------------------ #
@router.post("/chats/stream")
async def stream_chats(
    request: Request,
    body: dict | None = Body(None),
    page_size: int = Query(100, ge=1, le=500),
    max_total: int = Query(5000, ge=1, le=20000),
    _user=Depends(require_active_tenant_soft),   # <<< guard tolerante
    ctx=Depends(get_uazapi_ctx),
):
    instance_id = _get_instance_id_from_request(request)
    base, headers = _uaz(ctx)
    url = f"{base}/chat/find"

    async def gen():
        try:
            count = 0
            offset = 0

            async with httpx.AsyncClient(timeout=30) as cli:

                async def process_item(item: dict) -> Tuple[int, str]:
                    chatid = _pick_chatid(item)
                    last_ts = _last_msg_ts_of(item)
                    if chatid:
                        st = await _maybe_classify_and_persist(instance_id, ctx, chatid, last_msg_ts=last_ts)
                        if st:
                            item["_stage"] = st
                            item["stage"] = st
                            try:
                                crm_module.set_status_internal(chatid, st)
                            except Exception:
                                pass
                    item["_last_ts"] = last_ts
                    return last_ts, json.dumps(item, ensure_ascii=False) + "\\n"

                while count < max_total:
                    payload = body if body else {"operator": "AND", "sort": "-wa_lastMsgTimestamp"}
                    payload = {**payload, "limit": page_size, "offset": offset}

                    r = await cli.post(url, json=payload, headers=headers)
                    if r.status_code >= 400:
                        yield json.dumps({"error": r.text}) + "\\n"
                        return

                    try:
                        data = r.json()
                    except Exception:
                        yield json.dumps({"error": "Resposta inválida da UAZAPI em /chat/find"}) + "\\n"
                        return

                    chunk = _normalize_items(data)["items"]
                    if not chunk:
                        break

                    # processa em paralelo e mantém ordem por last_ts desc
                    coros = [process_item(it) for it in chunk]
                    results: list[Tuple[int, str]] = []
                    for fut in asyncio.as_completed(coros):
                        try:
                            results.append(await fut)
                        except Exception as e:
                            results.append((0, json.dumps({"error": f"process_item: {e}"}) + "\\n"))

                    for _ts, line in sorted(results, key=lambda x: x[0], reverse=True):
                        yield line
                        count += 1
                        if count >= max_total:
                            break

                    if len(chunk) < page_size or count >= max_total:
                        break
                    offset += page_size
        except Exception as e:
            # failsafe: nunca transforma em 500; retorna NDJSON de erro
            yield json.dumps({"error": f"stream-failed: {e.__class__.__name__}: {e}"}) + "\\n"

    # Resposta do stream com cabeçalhos CORS aplicados
    resp = StreamingResponse(gen(), media_type="application/x-ndjson")
    return _attach_cors_headers_to_response(request, resp)

# ------------------ CORS Preflight explícito ------------------ #
@router.options("/chats/stream", include_in_schema=False)
async def options_chats_stream(request: Request) -> Response:
    """
    Responde ao preflight CORS de /chats/stream com 204 e cabeçalhos adequados.
    Evita 400 em proxies/ambientes que não deixam o CORSMiddleware interceptar.
    """
    return _cors_preflight_response(request)
