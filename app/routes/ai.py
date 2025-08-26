# app/routes/ai.py
import os
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List, Optional
import httpx
import json
import re

router = APIRouter()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip()

# estágios internos do CRM (não mudamos no backend)
INTERNAL_STAGES = {"novo","sem_resposta","interessado","em_negociacao","fechou","descartado"}

# mapeamento das labels de negócio -> estágio interno do CRM
LABEL_TO_STAGE = {
    "lead":                "novo",
    "lead qualificado":    "sem_resposta",   # reaproveitado como "qualificado"
    "lead quente":         "em_negociacao",
    "prospectivo cliente": "interessado",
    "cliente":             "fechou",
}

# também aceitamos variações de escrita/capitalização
def map_label_to_stage(label: str) -> str:
    if not label:
        return "novo"
    key = label.strip().lower()
    # normalizações comuns
    key = key.replace("prospectivo  cliente", "prospectivo cliente").replace("  ", " ").strip()
    return LABEL_TO_STAGE.get(key, "novo")

class Msg(BaseModel):
    role: str
    content: str

class ClassifyReq(BaseModel):
    history: Optional[List[Msg]] = None
    text: Optional[str] = None
    language: Optional[str] = "pt-BR"

class ClassifyResp(BaseModel):
    stage: str          # estágio interno do CRM
    confidence: float   # 0..1
    reason: str

SYSTEM_PROMPT = (
    "Você é um classificador de estágio de lead para uma loja.\n\n"
    "Você receberá o histórico de mensagens (cliente e atendente). Leia tudo e classifique APENAS em uma das categorias abaixo,\n"
    "seguindo as regras. Responda em JSON ESTRITO no formato:\n"
    "{ \"label\": \"<uma_das_5_labels>\", \"confidence\": <0-1>, \"reason\": \"breve justificativa\" }\n\n"
    "As 5 labels possíveis (escreva exatamente assim):\n"
    "- Lead\n"
    "- Lead Qualificado\n"
    "- Lead Quente\n"
    "- Prospectivo Cliente\n"
    "- Cliente\n\n"
    "Regras de decisão:\n"
    "1) Lead – Enviou até 2 mensagens, sem perguntas ou interesse evidente.\n"
    "2) Lead Qualificado – Fez perguntas específicas sobre produtos/serviços.\n"
    "3) Lead Quente – Mostrou forte intenção de comprar, mas ainda sem pedir preço/condições finais.\n"
    "4) Prospectivo Cliente – Pediu preço, formas de pagamento, condições ou indicou intenção clara de fechar.\n"
    "5) Cliente – Confirmou que já comprou ou declarou ser cliente.\n\n"
    "IMPORTANTE:\n"
    "- Seja conservador ao marcar 'Cliente' (exigir confirmação explícita).\n"
    "- Use 'Lead Quente' quando houver intenção clara, mas sem negociação final.\n"
    "- O campo 'confidence' deve ser um número entre 0 e 1.\n"
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

def _parse_json_or_text(raw: str) -> dict:
  """
  Tenta parsear JSON estrito. Se vier texto, tenta extrair um objeto JSON.
  Se ainda assim não houver JSON, aceita a linha como label pura.
  """
  if not raw:
      return {}

  # tentativa direta JSON
  try:
      return json.loads(raw)
  except Exception:
      pass

  # tentar achar um { ... } dentro do texto
  m = re.search(r"\{.*\}", raw, flags=re.S)
  if m:
      try:
          return json.loads(m.group(0))
      except Exception:
          pass

  # fallback: pode ter vindo apenas a label (ex.: "Lead Qualificado")
  txt = raw.strip().splitlines()[0].strip().strip('"').strip("'")
  if txt:
      return {"label": txt}

  return {}

@router.post("/ai/classify", response_model=ClassifyResp)
async def classify(req: ClassifyReq):
  # Monta prompt
  msgs = [{"role": "system", "content": SYSTEM_PROMPT}]

  if req.history:
      # concatena o histórico num bloco único
      lines = []
      for m in req.history:
          role = "Cliente" if m.role == "user" else ("Atendente" if m.role == "assistant" else m.role)
          content = (m.content or "").strip()
          if content:
              lines.append(f"{role}: {content}")
      user_text = "\n".join(lines).strip()
  elif req.text:
      user_text = req.text.strip()
  else:
      raise HTTPException(status_code=400, detail="Forneça 'history' ou 'text'.")

  if not user_text:
      # nada para analisar
      return ClassifyResp(stage="novo", confidence=0.3, reason="Sem conteúdo suficiente.")

  msgs.append({"role": "user", "content": user_text})

  raw = await _openai_chat(msgs)
  obj = _parse_json_or_text(raw)

  # normaliza
  label = str(obj.get("label", "")).strip()
  conf  = obj.get("confidence", 0.6)
  reason = str(obj.get("reason", "")).strip() or "Classificação automática."

  try:
      conf = float(conf)
  except Exception:
      conf = 0.6
  conf = max(0.0, min(1.0, conf))

  stage = map_label_to_stage(label)
  if stage not in INTERNAL_STAGES:
      stage = "novo"

  return ClassifyResp(stage=stage, confidence=conf, reason=reason)
