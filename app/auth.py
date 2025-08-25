import os
from datetime import datetime, timedelta
from typing import Optional, Any, Dict

import jwt  # PyJWT
from fastapi import APIRouter, HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, Field

JWT_SECRET: str = os.getenv("JWT_SECRET", "change-me")
JWT_ALGORITHM: str = os.getenv("JWT_ALGORITHM", "HS256")
JWT_EXPIRE_MINUTES: int = int(os.getenv("JWT_EXPIRE_MINUTES", "43200"))

router = APIRouter()
security = HTTPBearer(auto_error=True)

class LoginIn(BaseModel):
    token: str = Field(..., description="Instance token da UAZAPI")
    label: Optional[str] = None
    number_hint: Optional[str] = None

class LoginOut(BaseModel):
    jwt: str
    profile: Dict[str, Any]

def _jwt_encode(payload: dict) -> str:
    try:
        return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Falha ao assinar token: {e}")

def _jwt_decode(token: str) -> dict:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expirado")
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Token inválido: {e}")

@router.post("/login", response_model=LoginOut)
def login(body: LoginIn) -> LoginOut:
    instance_token = (body.token or "").strip()
    if not instance_token:
        raise HTTPException(status_code=400, detail="Informe o token da instância")

    now = datetime.utcnow()
    exp = now + timedelta(minutes=JWT_EXPIRE_MINUTES)

    payload = {
        "sub": "luna-user",
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
        "instance_token": instance_token,
        "label": (body.label or "").strip() or None,
        "number_hint": (body.number_hint or "").strip() or None,
    }

    token = _jwt_encode(payload)
    profile = {"label": payload.get("label"), "number_hint": payload.get("number_hint")}
    return LoginOut(jwt=token, profile=profile)

@router.get("/me")
def me(user=Depends(lambda creds=Depends(security): _jwt_decode(creds.credentials))):
    return user

def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)) -> dict:
    return _jwt_decode(credentials.credentials)
