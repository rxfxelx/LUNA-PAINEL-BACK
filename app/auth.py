# app/auth.py
import os
from datetime import datetime, timedelta
from typing import Optional, Dict, Any

import jwt  # PyJWT
from fastapi import APIRouter, HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel

router = APIRouter()
security = HTTPBearer(auto_error=True)

# --------- ENV ROBUSTA ---------
def _get_env_str(*keys: str, default: str = "") -> str:
    for k in keys:
        v = os.getenv(k)
        if v is not None and str(v).strip() != "":
            return str(v).strip()
    return default

def _get_exp_minutes() -> int:
    vmin = os.getenv("JWT_EXPIRE_MINUTES")
    if vmin and vmin.isdigit():
        return int(vmin)
    vttl = os.getenv("LUNA_JWT_TTL")
    if vttl and vttl.isdigit():
        secs = int(vttl)
        return max(1, secs // 60)
    return 43200  # 30 dias

JWT_SECRET          = _get_env_str("JWT_SECRET", "LUNA_JWT_SECRET", default="change-me")
JWT_ALGORITHM       = _get_env_str("JWT_ALGORITHM", default="HS256")
JWT_EXPIRE_MINUTES  = _get_exp_minutes()
DEFAULT_UAZAPI_HOST = _get_env_str("UAZAPI_HOST")

# --------- MODELOS ---------
class LoginIn(BaseModel):
    token: str                         # token da instância UAZAPI
    host: Optional[str] = None         # opcional; se não vier, usa UAZAPI_HOST
    label: Optional[str] = None
    number_hint: Optional[str] = None

class LoginOut(BaseModel):
    jwt: str
    profile: Dict[str, Any]

# --------- JWT HELPERS ---------
def _jwt_encode(payload: dict) -> str:
    try:
        return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Falha ao assinar JWT: {e}")

def _jwt_decode(token: str) -> dict:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="JWT expirado")
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"JWT inválido/expirado: {e}")

def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)) -> dict:
    tok = credentials.credentials or ""
    if not tok:
        raise HTTPException(status_code=401, detail="Sem credenciais")
    return _jwt_decode(tok)

# --------- ROTAS ---------
@router.post("/login", response_model=LoginOut)
def login(body: LoginIn) -> LoginOut:
    instance_token = (body.token or "").strip()
    if not instance_token:
        raise HTTPException(status_code=400, detail="Informe o token da instância")

    host = (body.host or DEFAULT_UAZAPI_HOST or "").strip()
    if not host:
        raise HTTPException(status_code=400, detail="Host da UAZAPI ausente (defina UAZAPI_HOST ou envie 'host' no login)")

    now = datetime.utcnow()
    exp = now + timedelta(minutes=JWT_EXPIRE_MINUTES)
    payload = {
        "sub": "luna-user",
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
        # claims usados pelas rotas:
        "token": instance_token,          # <<< usado diretamente
        "instance_token": instance_token, # <<< mantido por compat
        "host": host,                     # <<< usado diretamente (ou por compat)
        # perfil opcional
        "label": (body.label or None),
        "number_hint": (body.number_hint or None),
    }
    tok = _jwt_encode(payload)
    return LoginOut(jwt=tok, profile={"label": payload.get("label"), "number_hint": payload.get("number_hint")})

@router.get("/check")
def check(user=Depends(get_current_user)):
    return {"ok": True, "user": {"label": user.get("label"), "number_hint": user.get("number_hint"), "host": user.get("host")}}

@router.get("/debug")
def debug():
    tail = JWT_SECRET[-5:] if len(JWT_SECRET) >= 5 else "***"
    return {
        "alg": JWT_ALGORITHM,
        "exp_minutes": JWT_EXPIRE_MINUTES,
        "secret_len": len(JWT_SECRET),
        "secret_tail": tail,
        "default_host": DEFAULT_UAZAPI_HOST,
    }
