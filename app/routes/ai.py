# app/routes/ai.py
import os
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List, Optional
import httpx

router = APIRouter()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip()

# Classes finais (apenas 3): Contatos, Lead, Lead Quente
STAGES = ["Contatos", "Lead", "Lead Quente"]

class Msg(BaseModel):
    role: str
    content: str

class ClassifyReq(BaseModel):
    history: Optional[List[Msg]] = None
    text: Optional[str] = None
    language: Optional[str] = "pt-BR"

class ClassifyResp(BaseModel):
    stage: str
    confidence: float
    reason: str

SYSTEM_PROMPT = (
    "Você é um classificador de estágio de lead para uma loja.\n\n"
    "Receberá um histórico de mensagens (cliente/atendente) e deve classificar o estágio atual do lead em UMA das categorias abaixo, "
    "devolvendo JSON estrito no formato: {\"stage\":\"<uma_das_opções>\", \"confidence\":0-1, \"reason\":\"breve justificativa\"}.\n\n"
    "Categorias possíveis (retorne EXATAMENTE uma delas):\n"
    "- Contatos\n"
    "- Lead\n"
    "- Lead Quente\n\n"
    "Regras de decisão (use com rigor):\n"
    "1) Contatos — início/frio: poucas mensagens, sem engajamento real; cliente não respondeu; ou apenas mensagem automática/link (ex.: cardápio, catálogo, link) sem diálogo. NÃO infira intenção de compra por links automáticos.\n"
    "2) Lead — há conversa/engajamento com interesse claro: cliente faz perguntas relevantes, compara, busca detalhes (mas ainda sem handoff/encaminhamento final).\n"
    "3) Lead Quente — SOMENTE quando o atendente afirma explicitamente que vai transferir/encaminhar o atendimento, que outra pessoa/setor vai entrar em contato, ou que o contato foi repassado; OU quando há confirmação explícita de fechamento/contratação/pagamento.\n"
    "- Exemplos típicos de gatilhos para 'Lead Quente': 'vou te passar para...', 'vou encaminhar', 'vou transferir', 'o setor X vai te chamar', "
    "'vou pedir para fulano falar com você', 'nossa equipe comercial vai entrar em contato', 'já fechei/contratei/paguei'.\n"
    "- Ignore mensagens de boas-vindas automáticas e links como 'acesse nosso cardápio/catálogos' como sinal de fechamento.\n"
    "- Seja conservador para marcar 'Lead Quente'.\n\n"
    "Retorne SEMPRE JSON estrito e válido com 'stage' sendo exatamente uma das três opções acima, 'confidence' entre 0 e 1, e 'reason' curta.\n"
)

async def _openai_chat(messages):
    if not OPENAI_API_KEY:
        raise HTTPException(status_code=503, detail="OPENAI_API_KEY ausente no backend")

    url = "https://api.openai.com/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": OPENAI_MODEL or "gpt-4o-mini",
        "messages": messages,
        "temperature": 0.1,
        "response_format": {"type": "json_object"},
    }
    async with httpx.AsyncClient(timeout=30) as cli:
        r = await cli.post(url, headers=headers, json=payload)
        if r.status_code >= 400:
            raise HTTPException(status_code=r.status_code, detail=r.text)
        data = r.json()
        try:
            content = data["choices"][0]["message"]["content"]
        except Exception:
            raise HTTPException(status_code=502, detail="Resposta inesperada do provedor de IA.")
        return content

@router.post("/ai/classify", response_model=ClassifyResp)
async def classify(req: ClassifyReq):
    if not (req.history or req.text):
        raise HTTPException(status_code=400, detail="Forneça 'history' ou 'text'.")

    msgs = [{"role": "system", "content": SYSTEM_PROMPT}]
    if req.history:
        lines = []
        for m in req.history:
            role = "Cliente" if m.role == "user" else ("Atendente" if m.role == "assistant" else m.role)
            lines.append(f"{role}: {m.content}")
        user_text = "\n".join(lines)
    else:
        user_text = req.text or ""

    msgs.append({"role": "user", "content": user_text})

    raw = await _openai_chat(msgs)

    import json, re
    try:
        obj = json.loads(raw)
    except Exception:
        m = re.search(r"\{.*\}", raw, flags=re.S)
        if not m:
            raise HTTPException(status_code=502, detail="IA não retornou JSON válido.")
        obj = json.loads(m.group(0))

    stage = str(obj.get("stage", "")).strip()
    if stage not in STAGES:
        stage = "Contatos"

    conf = obj.get("confidence", 0.5)
    try:
        conf = float(conf)
    except Exception:
        conf = 0.5
    conf = max(0.0, min(1.0, conf))

    reason = str(obj.get("reason", "")).strip() or "Classificação automática."

    return ClassifyResp(stage=stage, confidence=conf, reason=reason)
