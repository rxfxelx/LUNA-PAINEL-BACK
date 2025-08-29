# app/routes/chats.py
import asyncio
import httpx
from fastapi import APIRouter, Depends, HTTPException
from app.routes.deps import get_uazapi_ctx

router = APIRouter()

# =========================
# UAZ helpers
# =========================
def _uaz(ctx):
    base = f"https://{ctx['host']}"
    headers = {"token": ctx["token"]}
    return base, headers

def _normalize_items(resp_json):
    """
    Garante que sempre retornamos { items: [...] } ao front.
    - Se vier {items:[...]}: mantém
    - Se vier lista pura [...]: embrulha
    - Se vier outro objeto: tenta achar algo listável ou devolve vazio
    """
    if isinstance(resp_json, dict):
        if "items" in resp_json and isinstance(resp_json["items"], list):
            return {"items": resp_json["items"]}
        for key in ("data", "results", "chats"):
            val = resp_json.get(key)
            if isinstance(val, list):
                return {"items": val}
        return {"items": []}
    if isinstance(resp_json, list):
        return {"items": resp_json}
    return {"items": []}

def _pick_chatid(ch: dict) -> str:
    return (
        ch.get("wa_chatid")
        or ch.get("chatid")
        or ch.get("wa_fastid")
        or ch.get("wa_id")
        or ""
    )

# =========================
# Regras de classificação (mesmas do front)
# =========================
def _norm(s: str) -> str:
    import unicodedata
    s = str(s or "").lower().strip()
    s = "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")
    s = " ".join(s.split())
    return s

STAGE_RANK = {"contatos": 0, "lead": 1, "lead_quente": 2}
def _normalize_stage(s: str) -> str:
    k = _norm(s)
    if k.startswith("contato"):
        return "contatos"
    if "lead_quente" in k or "quente" in k:
        return "lead_quente"
    if k == "lead":
        return "lead"
    return "contatos"

def _max_stage(a: str, b: str) -> str:
    a = _normalize_stage(a)
    b = _normalize_stage(b)
    return b if STAGE_RANK.get(b, 0) > STAGE_RANK.get(a, 0) else a

HOT_HINTS = list(map(_norm, [
  "vou te passar para","vou te passar pro","vou passar voce para","vou passar você para",
  "vou passar para o setor","vou passar para o departamento","vou passar para o time",
  "vou passar seu contato","vou passar o seu contato","vou passar seu numero","vou passar o seu numero",
  "vou repassar seu contato","repassei seu contato","enviei seu contato","vou enviar seu contato","enviarei seu contato",
  "vou encaminhar","encaminhando seu contato","encaminhei seu contato","encaminhei seu numero","encaminhei seu número",
  "encaminhar seu contato","estou encaminhando","encaminharei",
  "vou te colocar em contato","vou colocar voce em contato","vou colocar você em contato","colocar voce em contato","colocar você em contato",
  "vou te conectar","vou te por em contato","te coloco em contato",
  "o time comercial vai te chamar","o time vai te chamar","nossa equipe vai entrar em contato","a equipe vai entrar em contato","o setor vai entrar em contato",
  "o atendente vai falar com voce","o atendente vai falar com você","um atendente vai te chamar","um consultor vai te chamar","o consultor vai te chamar",
  "o especialista vai te chamar","o responsavel vai te chamar","o responsável vai te chamar","o pessoal do comercial te chama","suporte vai te chamar",
  "vendas vai te chamar","pre-vendas vai te chamar","pré-vendas vai te chamar",
  "vou pedir para alguem te chamar","vou pedir para alguém te chamar","vou pedir pra alguem te chamar","vou pedir pra alguém te chamar",
  "vou pedir pro pessoal te chamar","vou pedir para o time te chamar","ja pedi para te chamarem","já pedi para te chamarem",
  "vou transferir","estou transferindo","transferencia para o setor","transferência para o setor",
  "transferi sua solicitacao","transferi sua solicitação","direcionei seu contato","direcionando seu contato","direcionar seu contato",
  "daqui a pouco te chamam","em breve vao entrar em contato","em breve vão entrar em contato","abrirei um chamado","vou abrir um chamado","abrir um ticket","abrirei um ticket",
]))
HOT_NEGATIVE_GUARDS = list(map(_norm, [
  "cardapio","cardápio","menu","catalogo","catálogo","ver menu","ver cardapio",
  "acesse o menu","acesse o cardapio","acesse o cardápio","acesse nosso catalogo","acesse nosso catálogo",
  "cardapio online","link do menu","nosso menu","veja o menu","veja o cardapio","veja o catálogo",
]))
LEAD_OK_PATTERNS = list(map(_norm, [
  "sim, pode continuar","sim pode continuar","pode continuar","ok, pode continuar","ok pode continuar",
  "pode seguir","sim, pode seguir","sim pode seguir","vamos continuar","podemos continuar",
  "pode prosseguir","ok vamos prosseguir","segue","segue por favor","pode mostrar","pode me mostrar",
  "pode enviar","pode mandar","pode continuar 👍","pode continuar sim","sim, pode continuar sim",
  "pode continuar por favor","pode continuar pf","pode continuar pff","pode cont","pode cnt","pode seg",
  "pode prosseg","pode proseguir",
]))
LEAD_NAME_PATTERNS = list(map(_norm, [
  "qual seu nome","qual o seu nome","me diga seu nome","me fala seu nome","como voce se chama","como você se chama",
  "quem fala","quem esta falando","quem está falando","quem e voce","quem é você","pode me dizer seu nome",
  "me passa seu nome","me informe seu nome","seu nome por favor","nome pfv","nome por favor","nome?","qual seu primeiro nome",
  "qual seu nome completo","nome do cliente","nome do titular","nome para cadastro","poderia me informar seu nome","me diga o seu nome",
  "informe seu nome","sobrenome","seu nome e sobrenome","como devo te chamar","como posso te chamar","qual e seu nome","qual é seu nome","qual seria seu nome",
  "ql seu nome","q seu nome","seu nm","seu nome sff","seu nome pf",
]))

def _classify_by_rules(msgs: list[dict]) -> str:
    stage = "contatos"
    for m in msgs or []:
        me = bool(m.get("fromMe") or m.get("fromme") or m.get("from_me"))
        text = _norm(
            m.get("text")
            or m.get("caption")
            or (m.get("message") or {}).get("text")
            or (m.get("message") or {}).get("conversation")
            or m.get("body")
            or ""
        )
        if not text or not me:
            continue
        has_menuish = any(g in text for g in HOT_NEGATIVE_GUARDS)
        if (not has_menuish) and any(h in text for h in HOT_HINTS):
            stage = "lead_quente"
            break
        if any(p in text for p in LEAD_OK_PATTERNS):
            stage = _max_stage(stage, "lead")
        if any(p in text for p in LEAD_NAME_PATTERNS):
            stage = _max_stage(stage, "lead")
    return stage

async def _fetch_last_messages(cli: httpx.AsyncClient, base: str, headers: dict, chatid: str, limit: int = 200) -> list[dict]:
    """
    Busca histórico recente de um chat na UAZAPI.
    """
    url = f"{base}/message/find"
    body = {"chatid": chatid, "limit": limit, "sort": "-messageTimestamp"}
    r = await cli.post(url, json=body, headers=headers)
    if r.status_code >= 400:
        return []
    try:
        data = r.json()
    except Exception:
        return []
    if isinstance(data, dict) and isinstance(data.get("items"), list):
        return data["items"]
    if isinstance(data, list):
        return data
    # tenta em 'data'/'results'
    for k in ("data", "results", "messages"):
        v = data.get(k) if isinstance(data, dict) else None
        if isinstance(v, list):
            return v
    return []

async def _classify_many(base: str, headers: dict, items: list[dict], limit_msgs: int = 120, concurrency: int = 8):
    sem = asyncio.Semaphore(concurrency)
    async with httpx.AsyncClient(timeout=30) as cli:
        async def one(ch: dict):
            chatid = _pick_chatid(ch)
            if not chatid:
                ch["ai_stage"] = "contatos"
                return
            async with sem:
                msgs = await _fetch_last_messages(cli, base, headers, chatid, limit_msgs)
            # classifica com as regras
            stage = _classify_by_rules(msgs)
            ch["ai_stage"] = stage

        await asyncio.gather(*(one(ch) for ch in items))

# =========================
# Rotas
# =========================
@router.post("/chats")
async def find_chats(body: dict | None = None, ctx=Depends(get_uazapi_ctx)):
    """
    Proxy para UAZAPI /chat/find + classificação backend.
    Sempre normalizamos a saída para { items: [...] } e
    devolvemos cada chat com campo extra: ai_stage.
    """
    base, headers = _uaz(ctx)
    url = f"{base}/chat/find"

    # body default se vier None
    if not body or not isinstance(body, dict):
        body = {"operator": "AND", "sort": "-wa_lastMsgTimestamp", "limit": 50, "offset": 0}

    async with httpx.AsyncClient(timeout=30) as cli:
        r = await cli.post(url, json=body, headers=headers)

    if r.status_code >= 400:
        raise HTTPException(status_code=r.status_code, detail=r.text)

    try:
        data = r.json()
    except Exception:
        raise HTTPException(status_code=502, detail="Resposta inválida da UAZAPI em /chat/find")

    wrapped = _normalize_items(data)
    items: list[dict] = wrapped.get("items", [])

    # Classificação em paralelo (rápido e não bloqueia demais)
    try:
        await _classify_many(base, headers, items, limit_msgs=120, concurrency=8)
    except Exception:
        # Em caso de qualquer falha na classificação, seguimos sem travar a lista.
        for ch in items:
            ch.setdefault("ai_stage", "contatos")

    return {"items": items}

@router.get("/labels")
async def get_labels(ctx=Depends(get_uazapi_ctx)):
    base, headers = _uaz(ctx)
    url = f"{base}/labels"
    async with httpx.AsyncClient(timeout=30) as cli:
        r = await cli.get(url, headers=headers)
    if r.status_code >= 400:
        raise HTTPException(status_code=r.status_code, detail=r.text)
    return r.json()

@router.get("/status")
async def instance_status(ctx=Depends(get_uazapi_ctx)):
    base, headers = _uaz(ctx)
    url = f"{base}/instance/status"
    async with httpx.AsyncClient(timeout=30) as cli:
        r = await cli.get(url, headers=headers)
    if r.status_code >= 400:
        raise HTTPException(status_code=r.status_code, detail=r.text)
    return r.json()
