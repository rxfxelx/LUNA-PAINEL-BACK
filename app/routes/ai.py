# app/routes/ai.py
import os
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List, Optional
import httpx

router = APIRouter()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip()

# Classes finais (apenas 3)
STAGES = ["Lead", "Lead Quente", "Cliente"]

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
    "- Lead\n"
    "- Lead Quente\n"
    "- Cliente\n\n"
    "Regras de decisão (use com rigor):\n"
    "1) Lead — caso inicial/frio: poucos envios, sem engajamento real, sem perguntas; ou o cliente ainda não respondeu; ou apenas respostas automáticas/link (ex.: cardápio, catálogo, link) sem diálogo. Não infira intenção de compra.\n"
    "2) Lead Quente — há conversa em andamento com interesse claro: o cliente faz perguntas relevantes, compara opções, demonstra avaliar compra (mas ainda sem handoff/encaminhamento final). Pode pedir detalhes, disponibilidade, como funciona etc.\n"
    "3) Cliente — SOMENTE quando o atendente afirma explicitamente que vai transferir/encaminhar para outra pessoa/setor, que alguém entrará em contato, ou que foi repassado o contato; ou uma confirmação explícita de fechamento/contratação. Mensagens automáticas ou links sozinhos NÃO são 'Cliente'.\n\n"
    "Observações importantes:\n"
    "- Ignore mensagens automáticas de boas-vindas, links de cardápio/catálogo ou respostas padrão como sinal de 'Cliente'.\n"
    "- Seja conservador para marcar 'Cliente': procure termos como 'vou te passar para', 'vou encaminhar', 'o setor X vai te chamar', 'vou pedir para fulano falar com você', 'já fechei', 'contratei'.\n"
    "- Retorne SEMPRE JSON estrito e válido com 'stage' sendo exatamente uma das três opções, 'confidence' entre 0 e 1, e 'reason' curta.\n"
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
        "temperature": 0.2,
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
        # concatena o histórico em um único bloco
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
        # fallback conservador
        stage = "Lead"

    conf = obj.get("confidence", 0.5)
    try:
        conf = float(conf)
    except Exception:
        conf = 0.5
    conf = max(0.0, min(1.0, conf))

    reason = str(obj.get("reason", "")).strip() or "Classificação automática."

    return ClassifyResp(stage=stage, confidence=conf, reason=reason)
