# app/routes/uazapi_instance.py
from __future__ import annotations
import time, hashlib
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.routes.deps import decode_jwt   # já existe no seu projeto
from app.db import upsert_status, get_uaz_row, save_uaz_row  # vamos criar helpers abaixo

router = APIRouter()

# --------- MODELOS ---------
class CreateInstanceBody(BaseModel):
    host: str                          # ex: https://hia-clientes.uazapi.com
    display_name: Optional[str] = None # nome interno
    webhook_url: Optional[str] = None  # opcional: onde Uazapi vai postar eventos

class InstanceResp(BaseModel):
    instance: str
    status: str

# --------- HELPERS ---------
def hdr(token: str) -> dict:
    return {"token": token, "Content-Type": "application/json"}

def base(host: str) -> str:
    return host.rstrip("/")

def tokhash(tok: str) -> str:
    return hashlib.sha256(tok.encode("utf-8")).hexdigest()

# --------- ROTAS ---------
@router.post("/uaz/instance", response_model=InstanceResp)
async def create_instance(body: CreateInstanceBody, user=Depends(decode_jwt)):
    """
    Cria uma instância nova na Uazapi e salva no banco (hash do token).
    Assumimos que o usuário já tem 'token' master da Uazapi no JWT ou em seu cadastro.
    """
    # Se o token da Uazapi vier do usuário autenticado:
    uaz_token = user.get("token")          # adapte para sua realidade
    tenant = user.get("tenant") or user.get("email") or "default"

    if not uaz_token:
        raise HTTPException(400, "Token Uazapi ausente no usuário")

    url = f"{base(body.host)}/instance/create"
    payload = {}
    if body.display_name: payload["name"] = body.display_name
    if body.webhook_url:  payload["webhook"] = body.webhook_url

    async with httpx.AsyncClient(timeout=30.0) as c:
        r = await c.post(url, headers=hdr(uaz_token), json=payload or None)
        if r.status_code >= 400:
            raise HTTPException(r.status_code, r.text)
        data = r.json()
        instance = data.get("instance") or data.get("id") or data.get("name")
        if not instance:
            raise HTTPException(502, "Resposta inesperada da Uazapi (sem instance)")

    # guarda no banco (token hash)
    save_uaz_row(tenant=tenant, host=body.host, instance=instance, token_hash=tokhash(uaz_token), status="CREATED")
    return InstanceResp(instance=instance, status="CREATED")

@router.get("/uaz/instance/qr")
async def get_qr(instance: str, host: str, user=Depends(decode_jwt)):
    """Retorna status e QR atual (quando existir) para renderizar no front."""
    uaz_token = user.get("token")
    if not uaz_token:
        raise HTTPException(400, "Token Uazapi ausente no usuário")

    url = f"{base(host)}/instance/qr"
    async with httpx.AsyncClient(timeout=20.0) as c:
        r = await c.get(url, headers=hdr(uaz_token), params={"instance": instance})
        if r.status_code >= 400:
            raise HTTPException(r.status_code, r.text)
        data = r.json()  # esperado: { status: "QRCODE"|"CONNECTED"|..., qr: "..." }
        # normaliza resposta:
        return {
            "status": data.get("status") or "UNKNOWN",
            "qr_data": data.get("qr") or data.get("qrcode") or None
        }

@router.get("/uaz/instance/status")
async def instance_status(instance: str, host: str, user=Depends(decode_jwt)):
    uaz_token = user.get("token")
    if not uaz_token:
        raise HTTPException(400, "Token Uazapi ausente no usuário")

    url = f"{base(host)}/instance/status"
    async with httpx.AsyncClient(timeout=20.0) as c:
        r = await c.get(url, headers=hdr(uaz_token), params={"instance": instance})
        if r.status_code >= 400:
            raise HTTPException(r.status_code, r.text)
        return r.json()

@router.post("/uaz/webhook")
async def uaz_webhook(req: Request):
    """
    Opcional: receba eventos da Uazapi p/ atualizar status sem polling.
    Ex.: { event: "onScan", instance, qr }, { event: "connected", instance }, etc.
    """
    body = await req.json()
    instance = body.get("instance")
    event = body.get("event")
    # Atualiza sua tabela/status
    if event == "connected":
        upsert_status(instance, chatid="*", stage="CONNECTED", notes=None)
    elif event == "onScan":
        upsert_status(instance, chatid="*", stage="QRCODE", notes="qr_available")
    elif event == "disconnected":
        upsert_status(instance, chatid="*", stage="DISCONNECTED", notes=None)
    return {"ok": True}
