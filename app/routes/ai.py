import unicodedata
from typing import Dict, Any, List, Optional
from fastapi import APIRouter, Body

router = APIRouter()

def _norm(s: Optional[str]) -> str:
    s = (s or "").lower()
    s = unicodedata.normalize("NFD", s)
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    return " ".join(s.split())

def _pick(d: Dict[str, Any], path: str, default=None):
    cur = d
    for p in path.split("."):
        if not isinstance(cur, dict):
            return default
        cur = cur.get(p)
    return cur if cur is not None else default

HOT_HINTS = [ "vou te passar para","vou te passar pro","vou passar voce para","vou passar você para",
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
]
HOT_NEGATIVE_GUARDS = [ "cardapio","cardápio","menu","catalogo","catálogo","ver menu","ver cardapio",
  "acesse o menu","acesse o cardapio","acesse o cardápio","acesse nosso catalogo","acesse nosso catálogo",
  "cardapio online","link do menu","nosso menu","veja o menu","veja o cardapio","veja o catálogo",
]
LEAD_OK_PATTERNS = [ "sim, pode continuar","sim pode continuar","pode continuar","ok, pode continuar","ok pode continuar",
  "pode seguir","sim, pode seguir","sim pode seguir","vamos continuar","podemos continuar",
  "pode prosseguir","ok vamos prosseguir","segue","segue por favor","pode mostrar","pode me mostrar",
  "pode enviar","pode mandar","pode continuar 👍","pode continuar sim","sim, pode continuar sim",
  "pode continuar por favor","pode continuar pf","pode continuar pff","pode cont","pode cnt","pode seg",
  "pode prosseg","pode proseguir",
]
LEAD_NAME_PATTERNS = [ "qual seu nome","qual o seu nome","me diga seu nome","me fala seu nome","como voce se chama","como você se chama",
  "quem fala","quem esta falando","quem está falando","quem e voce","quem é você","pode me dizer seu nome",
  "me passa seu nome","me informe seu nome","seu nome por favor","nome pfv","nome por favor","nome?","qual seu primeiro nome",
  "qual seu nome completo","nome do cliente","nome do titular","nome para cadastro","poderia me informar seu nome","me diga o seu nome",
  "informe seu nome","sobrenome","seu nome e sobrenome","como devo te chamar","como posso te chamar","qual e seu nome","qual é seu nome","qual seria seu nome",
  "ql seu nome","q seu nome","seu nm","seu nome sff","seu nome pf",
]

def classify_by_rules(items: List[Dict[str, Any]]) -> str:
    stage = "contatos"
    for m in items or []:
        me = m.get("fromMe") or m.get("fromme") or m.get("from_me")
        if not me:
            continue
        text = ( m.get("text") or m.get("caption") or
                 _pick(m,"message.text") or _pick(m,"message.conversation") or
                 _pick(m,"message.extendedTextMessage.text") or
                 m.get("body") or "" )
        t = _norm(text)
        if not t:
            continue
        if not any(g in t for g in HOT_NEGATIVE_GUARDS) and any(h in t for h in HOT_HINTS):
            return "lead_quente"
        if any(p in t for p in LEAD_OK_PATTERNS) or any(p in t for p in LEAD_NAME_PATTERNS):
            stage = "lead"
    return stage

@router.post("/ai/classify")
def api_classify(payload: Dict[str, Any] = Body(...)):
    items = payload.get("messages") or []
    stage = classify_by_rules(items)
    return {"stage": stage}
