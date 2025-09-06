from __future__ import annotations
from typing import Dict, Any
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, EmailStr
from datetime import datetime, timedelta, timezone
import jwt, os, logging

from app.services.users import (
    create_user,
    get_user_by_email,
    verify_password,
    touch_last_login,
)

router = APIRouter()
log = logging.getLogger("uvicorn.error")

# >>> Variáveis de ambiente padronizadas (suporta suas chaves atuais)
JWT_SECRET = os.getenv("LUNA_JWT_SECRET") or os.getenv("JWT_SECRET") or "change-me"
JWT_ALG = os.getenv("JWT_ALGORITHM", "HS256")

# Se LUNA_JWT_TTL existir, tratamos como SEGUNDOS (como você usa 2592000=30d)
# Caso contrário, caímos no USER_JWT_TTL_MIN (em minutos) e convertemos para segundos.
if os.getenv("LUNA_JWT_TTL"):
    JWT_TTL_SECONDS = int(os.getenv("LUNA_JWT_TTL", "2592000"))
else:
    JWT_TTL_SECONDS = int(os.getenv("USER_JWT_TTL_MIN", "43200")) * 60  # 30 dias


def _issue_user_jwt(user: Dict[str, Any]) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "iss": "luna-backend",
        "sub": f"user:{user['id']}",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=JWT_TTL_SECONDS)).timestamp()),
        "email": user["email"],
        "role": "user",
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALG)


class RegisterIn(BaseModel):
    email: EmailStr
    password: str


class LoginIn(BaseModel):
    email: EmailStr
    password: str


@router.post("/register")
def register(body: RegisterIn):
    try:
        user = create_user(body.email, body.password)
    except ValueError as e:
        raise HTTPException(400, str(e))
    token = _issue_user_jwt({"id": user["id"], "email": user["email"]})
    return {"jwt": token, "profile": {"email": user["email"]}}


@router.post("/login")
def login(body: LoginIn):
    try:
        u = get_user_by_email(body.email)
        if not u or not verify_password(body.password, u["password_hash"]):
            raise HTTPException(401, "Credenciais inválidas")

        touch_last_login(u["id"])
        token = _issue_user_jwt({"id": u["id"], "email": u["email"]})
        return {"jwt": token, "profile": {"email": u["email"]}}
    except HTTPException:
        raise
    except Exception:
        log.exception("Erro no login")
        raise HTTPException(500, "Erro interno")
