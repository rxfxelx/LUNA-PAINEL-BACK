# app/routes/ai.py
import os
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List, Optional
import httpx

router = APIRouter()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip()

STAGES = ["contatos", "lead", "lead_quente"]

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
    "Você classifica o estágio de um contato em uma conversa de WhatsApp com base no histórico.\n"
    "RETORNE STRICT JSON com as chaves: {\"stage\":\"contatos|lead|lead_quente\",\"confidence\":0-1,\"reason\":\"...\"}.\n\n"
    "Definições (USE APENAS UMA):\n"
    "• contatos  – conversa inicial ou sem sinais claros de interesse (poucas mensagens curtas, saudações, links genéricos, cardápio etc.).\n"
    "• lead      – há interesse/engajamento: perguntas específicas, dúvidas, comparações, tentativa de entender oferta/benefícios.\n"
    "• lead_quente – SOMENTE quando a conversa indica transferência para humano/setor/consultor, ou agendamento explícito de atendimento, ou confirmação de que alguém entrará em contato (ex: \"vou te passar para o setor X\", \"alguém vai falar com você\", \"vou encaminhar seu contato\", \"o time comercial vai te chamar\").\n\n"
    "Atenção:\n"
    "- Mensagens automáticas com links (cardápio, site, catálogo) NÃO significam lead_quente.\n"
    "- Prefira 'contatos' se houver pouca informação objetiva.\n"
    "- Prefira 'lead' quando houver perguntas/demonstração de interesse.\n"
    "- Use 'lead_quente' apenas quando HÁ frase de encaminhamento/transferência humano-setor.\n"
)

async def _openai_chat(messages):
    if not OPENAI_API_KEY:
        raise HTTPException(status_code=503, detail="OPENAI_API_KEY ausente no backend")
    url = "https://api.openai.com/v1/chat/completions"
    headers = {"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": OPENAI_MODEL or "gpt-4o-mini",
        "messages": messages,
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
    }
    async with httpx.AsyncClient(timeout=40) as cli:
        r = await cli.post(url, headers=headers, json=payload)
        if r.status_code >= 400:
            raise HTTPException(status_code=r.status_code, detail=r.text)
        data = r.json()
        try:
            return data["choices"][0]["message"]["content"]
        except Exception:
            raise HTTPException(status_code=502, detail="Resposta inesperada do provedor de IA.")

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
        msgs.append({"role": "user", "content": "\n".join(lines)})
    else:
        msgs.append({"role": "user", "content": req.text})

    raw = await _openai_chat(msgs)

    import json, re
    try:
        obj = json.loads(raw)
    except Exception:
        m = re.search(r"\{.*\}", raw, flags=re.S)
        if not m:
            raise HTTPException(status_code=502, detail="IA não retornou JSON válido.")
        obj = json.loads(m.group(0))

    stage = str(obj.get("stage", "")).strip().lower()
    if stage not in STAGES:
        stage = "contatos"

    try:
        conf = float(obj.get("confidence", 0.5))
    except Exception:
        conf = 0.5
    conf = max(0.0, min(1.0, conf))

    reason = (obj.get("reason") or "Classificação automática").strip()

    return ClassifyResp(stage=stage, confidence=conf, reason=reason)
