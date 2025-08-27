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
    "Você é um classificador de estágio de lead para conversas de WhatsApp.\n"
    "Classifique APENAS em uma das chaves:\n"
    "- contatos\n"
    "- lead\n"
    "- lead_quente\n\n"
    "Definições (importante):\n"
    "• contatos: a loja enviou mensagem mas o cliente ainda não engajou, OU só respondeu 1 vez sem demonstrar interesse claro.\n"
    "• lead: o cliente demonstrou interesse real (fez perguntas, pediu informações, aceitou ver catálogo/mostrar algo, etc.).\n"
    "• lead_quente: quando o ATENDENTE sinaliza que vai TRANSFERIR/ENCAMINHAR para outra pessoa/setor/time comercial, ou colocar o cliente em contato com alguém. Exemplos: "
    "\"vou te passar para o comercial\", \"vou encaminhar seu contato\", \"alguém do time vai te chamar\", \"vou te transferir\", \"o time/comercial vai entrar em contato\".\n"
    "⚠️ Não marque 'lead_quente' por causa de mensagens automáticas com links (ex.: cardápio, catálogo) sem o atendente dizer explicitamente que transferirá/encaminhará.\n"
    "Regras:\n"
    "1) Analise todo o histórico disponível, mantendo foco no sentido prático.\n"
    "2) Só marque 'lead_quente' se EXISTIR mensagem do ATENDENTE com intenção explícita de transferência/encaminhamento/colocar em contato.\n"
    "3) Seja conservador: se houver dúvida entre 'lead' e 'lead_quente', prefira 'lead'.\n"
    "4) Responda STRICT JSON:\n"
    "{ \"stage\": \"contatos|lead|lead_quente\", \"confidence\": 0-1, \"reason\": \"breve justificativa\" }\n"
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
    msgs = [{"role": "system", "content": SYSTEM_PROMPT}]
    user_text = ""

    if req.history:
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

    conf = obj.get("confidence", 0.6)
    try:
        conf = float(conf)
    except Exception:
        conf = 0.6
    conf = max(0.0, min(1.0, conf))

    reason = str(obj.get("reason", "")).strip() or "Classificação automática."

    return ClassifyResp(stage=stage, confidence=conf, reason=reason)
