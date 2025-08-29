# app/routes/media.py
import httpx
import unicodedata
from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel
from typing import Any, Dict, List, Optional
from urllib.parse import unquote

router = APIRouter()

@router.get("/proxy")
async def proxy_media(u: str = Query(..., description="URL absoluta da mídia (codificada com encodeURIComponent)")):
    """
    Proxy simples para evitar CORS/leak de origem.
    Use: /api/media/proxy?u=<encodeURIComponent(URL_ABSOLUTA)>
    """
    url = unquote(u).strip()
    if not (url.startswith("http://") or url.startswith("https://")):
        raise HTTPException(status_code=400, detail="URL inválida")

    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as cli:
            r = await cli.get(url)
            if r.status_code >= 400:
                raise HTTPException(status_code=r.status_code, detail="Falha ao buscar mídia upstream")
            # repassa o content-type se existir
            ct = r.headers.get("content-type", "application/octet-stream")
            return Response(content=r.content, media_type=ct)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Proxy error: {e}")

# ================== CLASSIFICAÇÃO (server-side) ==================

class Message(BaseModel):
    fromMe: Optional[bool] = None
    fromme: Optional[bool] = None
    from_me: Optional[bool] = None
    text: Optional[str] = None
    caption: Optional[str] = None
    body: Optional[str] = None
    message: Optional[Dict[str, Any]] = None

class ClassifyPayload(BaseModel):
    messages: List[Message]

def _norm(s: str) -> str:
    s = (s or "").lower()
    s = unicodedata.normalize("NFD", s)
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    s = " ".join(s.split())
    return s

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

def _extract_text(msg: Message) -> str:
    m = msg
    text = (
        m.text
        or m.caption
        or m.body
        or (m.message or {}).get("text")
        or (m.message or {}).get("conversation")
        or (((m.message or {}).get("extendedTextMessage") or {}).get("text"))
        or ""
    )
    return _norm(text)

def _is_from_me(msg: Message) -> bool:
    return bool(msg.fromMe or msg.fromme or msg.from_me)

def _classify(messages: List[Message]) -> str:
    stage = "contatos"
    for m in messages:
        if not _is_from_me(m):
            continue
        text = _extract_text(m)
        if not text:
            continue
        has_menuish = any(g in text for g in HOT_NEGATIVE_GUARDS)
        if (not has_menuish) and any(h in text for h in HOT_HINTS):
            return "lead_quente"
        if any(p in text for p in LEAD_OK_PATTERNS):
            stage = "lead" if stage != "lead_quente" else stage
        if any(p in text for p in LEAD_NAME_PATTERNS):
            stage = "lead" if stage != "lead_quente" else stage
    return stage

@router.post("/stage/classify")
async def classify_stage(payload: ClassifyPayload):
    """
    Classifica o estágio (contatos/lead/lead_quente) com as mesmas regras do front,
    porém no servidor — para ficar instantâneo no carregamento do chat.
    """
    try:
      stage = _classify(payload.messages or [])
      return {"stage": stage}
    except Exception as e:
      raise HTTPException(status_code=400, detail=f"classify error: {e}")
