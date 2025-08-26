# app/routes/ai.py
import os, json, re
from fastapi import APIRouter, HTTPException, Body, Depends
from pydantic import BaseModel
from typing import List, Optional
import httpx

from app.routes.deps import get_uazapi_ctx
from app.routes import crm as crm_routes

router = APIRouter()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_MODEL   = os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip()

# Estágios/labels finais (sem "sem_resposta" e "descartado")
LABELS = [
    "Lead",
    "Lead Qualificado",
    "Lead Quente",
    "Prospectivo Cliente",
    "Cliente",
]

# Mapa para chaves internas do CRM
LABEL2KEY = {
    "lead": "lead",
    "lead qualificado": "lead_qualificado",
    "lead quente": "lead_quente",
    "prospectivo cliente": "prospectivo_cliente",
    "cliente": "cliente",
}

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
    "Receberá o histórico de mensagens entre um cliente e a loja.\n"
    "Classifique o estágio atual do lead de acordo com as regras:\n"
    "1. Lead – Enviou até 2 mensagens, sem perguntas ou interesse evidente.\n"
    "2. Lead Qualificado – Fez perguntas específicas sobre produtos/serviços.\n"
    "3. Lead Quente – Mostrou forte intenção de compra, mas sem pedir preço/condições finais.\n"
    "4. Prospectivo Cliente – Pediu preço, formas de pagamento, condições ou demonstrou intenção clara de fechar.\n"
    "5. Cliente – Confirmou que já comprou ou informou ser cliente.\n\n"
    "Responda em JSON ESTRITO com a estrutura:\n"
    "{ \"label\": \"Lead | Lead Qualificado | Lead Quente | Prospectivo Cliente | Cliente\","
    "  \"confidence\": 0-1, \"reason\": \"breve justificativa\" }\n"
)

def _ensure_key():
    if not OPENAI_API_KEY:
        raise HTTPException(status_code=503, detail="OPENAI_API_KEY ausente no backend")

async def _openai_chat(messages):
    _ensure_key()
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

def _parse_json_or_text(raw: str) -> dict:
    try:
        return json.loads(raw)
    except Exception:
        m = re.search(r"\{.*\}", raw, flags=re.S)
        if not m:
            raise HTTPException(status_code=502, detail="IA não retornou JSON válido.")
        return json.loads(m.group(0))

def _label_to_key(label: str) -> str:
    lk = (label or "").strip().lower()
    return LABEL2KEY.get(lk, "lead")

def _items_to_history(items: list) -> List[dict]:
    hist = []
    for m in items[:200]:
        role = "assistant" if (m.get("fromMe") or m.get("fromme") or m.get("from_me")) else "user"
        text = (
            m.get("text")
            or m.get("caption")
            or (m.get("message") or {}).get("text")
            or (m.get("message") or {}).get("conversation")
            or m.get("body")
            or ""
        )
        text = str(text).strip()
        if text:
            hist.append({"role": role, "content": text})
    return hist

@router.post("/ai/classify", response_model=ClassifyResp)
async def classify(req: ClassifyReq):
    msgs = [{"role": "system", "content": SYSTEM_PROMPT}]
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
    obj = _parse_json_or_text(raw)

    label = str(obj.get("label", "")).strip()
    conf  = obj.get("confidence", 0.6)
    reason = str(obj.get("reason", "")).strip() or "Classificação automática."

    try:
        conf = float(conf)
    except Exception:
        conf = 0.6
    conf = max(0.0, min(1.0, conf))

    stage = _label_to_key(label)
    return ClassifyResp(stage=stage, confidence=conf, reason=reason)

# -------- util p/ buscar msgs direto na UAZAPI ----------
async def _uaz_messages(chatid: str, limit: int, ctx) -> list:
    base = f"https://{ctx['host']}"
    headers = {"token": ctx["token"]}
    payload = {"chatid": chatid, "limit": limit, "sort": "-messageTimestamp"}
    async with httpx.AsyncClient(timeout=40) as cli:
        r = await cli.post(f"{base}/message/list", headers=headers, json=payload)
        if r.status_code >= 400:
            raise HTTPException(status_code=r.status_code, detail=r.text)
        j = r.json()
        return j.get("items") or j.get("data") or []

@router.post("/ai/classify-chat", response_model=ClassifyResp)
async def classify_chat(
    chatid: str = Body(..., embed=True),
    apply: bool = Body(True, embed=True),
    ctx = Depends(get_uazapi_ctx)
):
    if not chatid:
        raise HTTPException(400, "chatid é obrigatório")
    items = await _uaz_messages(chatid, 200, ctx)
    hist = _items_to_history(items)
    if not hist:
        return ClassifyResp(stage="lead", confidence=0.3, reason="Sem histórico.")

    msgs = [{"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": "\n".join(
                [("Cliente: " + h["content"]) if h["role"] == "user" else ("Atendente: " + h["content"]) for h in hist]
            )}]
    raw = await _openai_chat(msgs)
    obj = _parse_json_or_text(raw)

    label = str(obj.get("label", "")).strip()
    conf  = obj.get("confidence", 0.6)
    reason = str(obj.get("reason", "")).strip() or "Classificação automática."
    try:
        conf = float(conf)
    except Exception:
        conf = 0.6
    conf = max(0.0, min(1.0, conf))
    stage = _label_to_key(label)

    if apply:
        crm_routes.set_status_internal(chatid=chatid, stage=stage, notes=f"[IA] {reason}")

    return ClassifyResp(stage=stage, confidence=conf, reason=reason)

class BatchReq(BaseModel):
    chatids: List[str]
    apply: Optional[bool] = True

@router.post("/ai/classify-many")
async def classify_many(req: BatchReq, ctx = Depends(get_uazapi_ctx)):
    out = []
    for cid in (req.chatids or [])[:200]:
        try:
            items = await _uaz_messages(cid, 200, ctx)
            hist = _items_to_history(items)
            if not hist:
                out.append({"chatid": cid, "stage": "lead", "confidence": 0.3, "reason": "Sem histórico"})
                continue

            msgs = [{"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": "\n".join(
                        [("Cliente: " + h["content"]) if h["role"] == "user" else ("Atendente: " + h["content"]) for h in hist]
                    )}]
            raw = await _openai_chat(msgs)
            obj = _parse_json_or_text(raw)

            label = str(obj.get("label", "")).strip()
            conf  = obj.get("confidence", 0.6)
            reason = str(obj.get("reason", "")).strip() or "Classificação automática."
            try:
                conf = float(conf)
            except Exception:
                conf = 0.6
            conf = max(0.0, min(1.0, conf))
            stage = _label_to_key(label)

            if req.apply:
                crm_routes.set_status_internal(chatid=cid, stage=stage, notes=f"[IA] {reason}")

            out.append({"chatid": cid, "stage": stage, "confidence": conf, "reason": reason})
        except Exception as e:
            out.append({"chatid": cid, "error": str(e)})
    return {"items": out}
