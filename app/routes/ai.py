# app/routes/ai.py
import os
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List, Optional
import httpx

router = APIRouter()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip()

if not OPENAI_API_KEY:
    # sem chave, a rota existe mas retornará 503
    pass

STAGES = ["novo", "sem_resposta", "interessado", "em_negociacao", "fechou", "descartado"]

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
    "Você é um assistente que classifica a jornada do lead em conversas de WhatsApp.\n"
    "Categorias possíveis (em português, devolver apenas a chave):\n"
    "- novo\n"
    "- sem_resposta\n"
    "- interessado\n"
    "- em_negociacao\n"
    "- fechou\n"
    "- descartado\n\n"
    "Regras:\n"
    "1) Analise o tom e o conteúdo das mensagens (histórico inteiro, se fornecido).\n"
    "2) Seja conservador: só marque 'fechou' se o cliente explicitamente confirmou a compra/fechamento.\n"
    "3) Resposta deve ser STRICT JSON no formato:\n"
    "{ \"stage\": \"<uma_das_chaves>\", \"confidence\": <0-1>, \"reason\": \"breve justificativa\" }\n"
)

async def _openai_chat(messages):
    if not OPENAI_API_KEY:
        raise HTTPException(status_code=503, detail="OPENAI_API_KEY ausente no backend")

    # chamada minimalista via HTTP para /v1/chat/completions (OpenAI)
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
    # Monta mensagens para o chat
    msgs = [{"role": "system", "content": SYSTEM_PROMPT}]
    user_text = ""

    if req.history:
        # concatena o histórico num bloco único
        lines = []
        for m in req.history:
            role = "Cliente" if m.role == "user" else ("Atendente" if m.role == "assistant" else m.role)
            lines.append(f"{role}: {m.content}")
        user_text = "\n".join(lines)
    elif req.text:
        user_text = req.text
    else:
        raise HTTPException(status_code=400, detail="Forneça 'history' ou 'text'.")

    msgs.append({"role": "user", "content": user_text})

    raw = await _openai_chat(msgs)

    # Tenta parsear o JSON estrito
    import json
    try:
        obj = json.loads(raw)
    except Exception:
        # fallback: tenta achar um objeto JSON dentro
        import re
        m = re.search(r"\{.*\}", raw, flags=re.S)
        if not m:
            raise HTTPException(status_code=502, detail="IA não retornou JSON válido.")
        obj = json.loads(m.group(0))

    stage = str(obj.get("stage", "")).strip()
    if stage not in STAGES:
        stage = "novo"

    conf = obj.get("confidence", 0.5)
    try:
        conf = float(conf)
    except Exception:
        conf = 0.5
    conf = max(0.0, min(1.0, conf))

    reason = str(obj.get("reason", "")).strip() or "Classificação automática."

    return ClassifyResp(stage=stage, confidence=conf, reason=reason)
