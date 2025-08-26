# app/routes/ai.py
import os
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List, Optional
import httpx

router = APIRouter()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip()

# Estágios finais (labels exatas em PT-BR)
STAGES = [
    "Lead",
    "Lead Qualificado",
    "Lead Quente",
    "Prospectivo Cliente",
    "Cliente",
]

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
    "Receberá como entrada o histórico de mensagens (cliente vs atendente). "
    "Classifique o estágio atual do lead EXCLUSIVAMENTE em UMA das opções abaixo (responda em JSON estrito):\n\n"
    "1) Lead – Enviou até 2 mensagens, sem perguntas ou interesse evidente.\n"
    "2) Lead Qualificado – Fez perguntas específicas sobre produtos/serviços.\n"
    "3) Lead Quente – Mostrou forte intenção de comprar, mas ainda sem pedir preço/condições finais.\n"
    "4) Prospectivo Cliente – Pediu preço, formas de pagamento, condições, ou sinalizou claramente que vai fechar.\n"
    "5) Cliente – SOMENTE quando o ATENDENTE (não o cliente) disser explicitamente que irá: "
    "   transferir/encaminhar a conversa para outro setor, colocar em contato com alguém, "
    "   passar o número para outra equipe ou concluir o atendimento com handoff claro.\n\n"
    "Regras IMPORTANTES:\n"
    "- NÃO marque 'Cliente' por mensagens automáticas, catálogos, cardápios, menus, links do tipo 'veja o cardápio', 'acesse o catálogo', respostas automáticas ou saudações.\n"
    "- Se houver dúvidas entre 'Lead Quente' e 'Prospectivo Cliente', prefira 'Prospectivo Cliente' apenas quando houver pedido de preço/condições ou intenção explícita de fechar.\n"
    "- Seja conservador: só use 'Cliente' quando houver evidência textual CLARA de handoff pelo atendente (ex.: 'vou te colocar em contato com...', 'vou te transferir para...', 'vou passar seu número para o setor X').\n"
    "- Se não encaixar em 'Cliente', escolha o estágio mais alto coerente com as mensagens.\n\n"
    "Formato de resposta: JSON estrito (sem texto extra) no modelo:\n"
    "{ \"stage\": \"Lead|Lead Qualificado|Lead Quente|Prospectivo Cliente|Cliente\", "
    "\"confidence\": 0-1, \"reason\": \"breve justificativa\" }\n"
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
    async with httpx.AsyncClient(timeout=40) as cli:
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
    # Monta mensagens para o modelo
    msgs = [{"role": "system", "content": SYSTEM_PROMPT}]
    user_text = ""

    if req.history:
        lines = []
        for m in req.history:
            # normaliza os papéis para o prompt
            role = "Cliente" if m.role.lower() == "user" else ("Atendente" if m.role.lower() == "assistant" else m.role)
            content = (m.content or "").strip()
            if not content:
                continue
            lines.append(f"{role}: {content}")
        user_text = "\n".join(lines)
    elif req.text:
        user_text = req.text.strip()
    else:
        raise HTTPException(status_code=400, detail="Forneça 'history' ou 'text'.")

    msgs.append({"role": "user", "content": user_text})
    raw = await _openai_chat(msgs)

    # Parse JSON estrito (ou fallback)
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
        stage = "Lead"

    conf = obj.get("confidence", 0.5)
    try:
        conf = float(conf)
    except Exception:
        conf = 0.5
    conf = max(0.0, min(1.0, conf))

    reason = str(obj.get("reason", "")).strip() or "Classificação automática."

    return ClassifyResp(stage=stage, confidence=conf, reason=reason)
