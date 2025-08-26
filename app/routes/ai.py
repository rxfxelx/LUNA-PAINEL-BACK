# app/routes/ai.py
import os
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List, Optional
import httpx

router = APIRouter()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip()

# Estágios internos do CRM (não mudam)
CRM_STAGES = ["novo", "interessado", "em_negociacao", "fechou"]

# Rótulos exibidos (o que você quer ver)
DISPLAY_LABELS = [
    "Lead",
    "Lead Qualificado",
    "Lead Quente",
    "Prospectivo Cliente",
    "Cliente",
]

# Mapeamento: rótulo exibido -> estágio interno
DISPLAY_TO_INTERNAL = {
    "Lead": "novo",
    "Lead Qualificado": "interessado",
    "Lead Quente": "em_negociacao",
    "Prospectivo Cliente": "em_negociacao",  # estágio mais avançado dentro de negociação
    "Cliente": "fechou",
}

class Msg(BaseModel):
    role: str
    content: str

class ClassifyReq(BaseModel):
    history: Optional[List[Msg]] = None
    text: Optional[str] = None
    language: Optional[str] = "pt-BR"

class ClassifyResp(BaseModel):
    stage: str          # interno (novo/interessado/em_negociacao/fechou)
    confidence: float
    reason: str

# Prompt alinhado às suas regras (em PT-BR) + exigência de JSON
SYSTEM_PROMPT = (
    "Você é um classificador de estágio de lead para uma loja.\n"
    "Receberá como entrada um histórico de mensagens (cliente ↔ loja). Leia tudo e classifique o estágio atual do lead seguindo as regras:\n\n"
    "1) Lead – Enviou até 2 mensagens, sem perguntas ou interesse evidente.\n"
    "2) Lead Qualificado – Fez perguntas específicas sobre produtos/serviços.\n"
    "3) Lead Quente – Mostrou forte intenção de comprar, mas ainda sem pedir preço/condições finais.\n"
    "4) Prospectivo Cliente – Pediu preço, formas de pagamento, condições ou declarou intenção clara de fechar.\n"
    "5) Cliente – Confirmou que já comprou ou informou ser cliente.\n\n"
    "Responda **em JSON estrito** com este formato:\n"
    "{ \"label\": \"Lead|Lead Qualificado|Lead Quente|Prospectivo Cliente|Cliente\", \"confidence\": 0-1, \"reason\": \"breve justificativa\" }\n"
    "Não inclua comentários fora do JSON."
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

def _map_display_to_internal(label: str) -> str:
    lab = (label or "").strip()
    # normaliza variações comuns
    lab_norm = lab.lower()
    aliases = {
        "lead": "Lead",
        "lead qualificado": "Lead Qualificado",
        "lead quente": "Lead Quente",
        "prospectivo cliente": "Prospectivo Cliente",
        "cliente": "Cliente",
    }
    lab_canon = aliases.get(lab_norm, lab)
    return DISPLAY_TO_INTERNAL.get(lab_canon, "novo")

@router.post("/ai/classify", response_model=ClassifyResp)
async def classify(req: ClassifyReq):
    # Monta o texto do usuário (histórico compactado)
    msgs = [{"role": "system", "content": SYSTEM_PROMPT}]
    user_text = ""

    if req.history:
        lines = []
        for m in req.history:
            role = "Cliente" if m.role == "user" else ("Loja" if m.role == "assistant" else m.role)
            lines.append(f"{role}: {m.content}")
        user_text = "\n".join(lines)
    elif req.text:
        user_text = req.text
    else:
        raise HTTPException(status_code=400, detail="Forneça 'history' ou 'text'.")

    msgs.append({"role": "user", "content": user_text})

    raw = await _openai_chat(msgs)

    # Tenta parsear como JSON; se vier só o rótulo (string simples), tratamos também.
    import json, re
    label = ""
    confidence = 0.6
    reason = "Classificação automática."

    try:
        obj = json.loads(raw)
        label = str(obj.get("label", "")).strip()
        if "confidence" in obj:
            try:
                confidence = float(obj["confidence"])
            except Exception:
                confidence = 0.6
        if "reason" in obj:
            reason = str(obj["reason"] or reason).strip() or reason
    except Exception:
        # Pode ter vindo só texto tipo: "Lead Quente"
        t = raw.strip()
        # tenta extrair uma linha com um dos rótulos válidos
        pat = r"(Lead Qualificado|Lead Quente|Prospectivo Cliente|Cliente|Lead)"
        m = re.search(pat, t, flags=re.I)
        if not m:
            raise HTTPException(status_code=502, detail="IA não retornou classificação reconhecida.")
        label = m.group(1).title()

    # Se o label não estiver na lista, cai em Lead
    if label not in DISPLAY_LABELS:
        # tenta normalizar minúsculas
        lower_map = {x.lower(): x for x in DISPLAY_LABELS}
        label = lower_map.get(label.lower(), "Lead")

    stage_internal = _map_display_to_internal(label)

    # Confiança normalizada
    try:
        confidence = max(0.0, min(1.0, float(confidence)))
    except Exception:
        confidence = 0.6

    return ClassifyResp(stage=stage_internal, confidence=confidence, reason=f"{label} • {reason}")
