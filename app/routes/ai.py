# app/routes/ai.py
import os, json, re, time
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, HTTPException, Body, Depends
import httpx

from pydantic import BaseModel

router = APIRouter()

# ===== Config =====
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
# cooldown simples para não spammar a IA por chat
_last_call: Dict[str, float] = {}

# Mapa do seu funil -> tabs existentes no app
FUNIL_TO_STAGE = {
    "lead": "novo",
    "lead_qualificado": "interessado",
    "lead_quente": "em_negociacao",
    "prospecto": "em_negociacao",
    "cliente": "fechou",
    "descartado": "descartado",
}

# Texto mostrado para o modelo
SYSTEM_PROMPT = (
    "Você é um analista de pré-vendas. Classifique o estágio do contato no funil "
    "com base no histórico de mensagens do WhatsApp. Responda **apenas** em JSON, "
    "sem comentários nem texto solto.\n\n"
    "Categorias possíveis (escolha **uma**):\n"
    "- lead            → primeiro contato, sem qualificação\n"
    "- lead_qualificado→ demonstrou interesse claro, informou dados mínimos\n"
    "- lead_quente      → forte intenção de compra, querendo proposta/fechamento\n"
    "- prospecto        → em negociação (trocando condições, dúvidas complexas)\n"
    "- cliente          → já fechou/aceitou proposta\n"
    "- descartado       → sem interesse, spam ou número errado\n\n"
    "Retorne no formato:\n"
    '{\"stage\":\"lead|lead_qualificado|lead_quente|prospecto|cliente|descartado\",'
    ' \"confidence\":0-1, \"reason\":\"breve justificativa\"}\n"
)

class Msg(BaseModel):
    fromMe: Optional[bool] = None
    text: Optional[str] = None
    caption: Optional[str] = None
    message: Optional[Dict[str, Any]] = None
    body: Optional[str] = None
    pushName: Optional[str] = None
    senderName: Optional[str] = None
    messageTimestamp: Optional[Any] = None
    timestamp: Optional[Any] = None

class ClassifyPayload(BaseModel):
    chatid: str
    transcript: Optional[List[Msg]] = None  # opcional; se vier vazio, a IA só terá contexto mínimo
    apply: bool = True                       # se True, aplica no CRM
    max_messages: int = 30

def _extract_text(m: Msg) -> str:
    return (
        m.text or
        (m.message or {}).get("text") or
        (m.message or {}).get("conversation") or
        m.caption or
        m.body or
        ""
    )

def _role(m: Msg) -> str:
    # padroniza para o chat.completions
    return "assistant" if (m.fromMe or False) else "user"

def _to_openai_messages(transcript: List[Msg]) -> List[Dict[str, str]]:
    out = []
    for m in transcript:
        txt = _extract_text(m).strip()
        if not txt:
            continue
        out.append({"role": _role(m), "content": txt[:4000]})
    # se por acaso veio vazio, ancora com um contexto mínimo
    if not out:
        out = [{"role":"user","content":"Sem histórico relevante. Classifique pelo mínimo possível."}]
    return out[-60:]  # sanidade
        

def _parse_json(s: str) -> Dict[str, Any]:
    # tenta achar um bloco JSON válido
    try:
        return json.loads(s)
    except Exception:
        pass
    m = re.search(r"\{.*\}", s, re.S)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            pass
    return {}

async def _post_crm_status(chatid: str, stage: str, notes: str = ""):
    # chama o próprio backend (loopback) para gravar o estágio
    # respeita CORS e auth do mesmo cliente
    # OBS: o front sempre envia Authorization para este endpoint,
    # então aqui apenas expomos a rota: quem grava é o endpoint /api/crm/status
    async with httpx.AsyncClient(timeout=20) as cli:
        # NÃO temos o token aqui (server-side). Esse endpoint será
        # chamado pelo frontend (com Bearer). Então este helper é
        # usado somente quando a requisição trouxer o header (forward).
        # Se não houver header, não aplicamos.
        pass  # deixamos sem uso server-side.


@router.post("/ai/classify")
async def classify(payload: ClassifyPayload):
    if not payload.chatid:
        raise HTTPException(400, detail="chatid é obrigatório")
    if not OPENAI_API_KEY:
        raise HTTPException(501, detail="OPENAI_API_KEY ausente no backend")

    # antispam simples por chat
    now = time.time()
    if now - _last_call.get(payload.chatid, 0) < 5:
        raise HTTPException(429, detail="Tente novamente em alguns segundos")
    _last_call[payload.chatid] = now

    # monta mensagens para o modelo
    transcript = payload.transcript or []
    oa_messages = [{"role":"system", "content": SYSTEM_PROMPT}]
    oa_messages += _to_openai_messages(transcript)

    # chamada ao OpenAI
    try:
        from openai import OpenAI
        client = OpenAI(api_key=OPENAI_API_KEY)
        resp = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=oa_messages,
            temperature=0.2,
            max_tokens=200
        )
        content = (resp.choices[0].message.content or "").strip()
    except Exception as e:
        raise HTTPException(502, detail=f"Falha na IA: {e}")

    data = _parse_json(content)
    stage_ia = str(data.get("stage", "")).strip().lower()
    reason = str(data.get("reason", "")).strip()
    conf = data.get("confidence", None)

    # normaliza saída da IA -> estágio do seu CRM
    mapped = FUNIL_TO_STAGE.get(stage_ia)
    if not mapped:
        # fallback simples
        mapped = "novo"

    return {
        "ok": True,
        "stage_ai": stage_ia,
        "stage_mapped": mapped,
        "confidence": conf,
        "reason": reason,
        "raw": content,
    }
