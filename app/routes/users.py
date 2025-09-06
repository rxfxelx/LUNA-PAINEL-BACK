# app/routes/users.py
from __future__ import annotations
from typing import Dict, Any
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, EmailStr
from datetime import datetime, timedelta
import jwt, os

from app.services.users import create_user, get_user_by_email, verify_password, touch_last_login

router = APIRouter()

JWT_SECRET = os.getenv("JWT_SECRET", "change-me")
JWT_ALG    = os.getenv("JWT_ALGORITHM", "HS256")
USER_JWT_TTL_MIN = int(os.getenv("USER_JWT_TTL_MIN", "43200"))  # 30 dias

def _issue_user_jwt(user: Dict[str, Any]) -> str:
    now = datetime.utcnow()
    payload = {
        "iss": "luna-backend",
        "sub": f"user:{user['id']}",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=USER_JWT_TTL_MIN)).timestamp()),
        "email": user["email"],
        "role": "user",
        # OBS: ESTE JWT NÃO CONTÉM token/host da instância (isso continua em /api/auth/login)
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
    u = get_user_by_email(body.email)
    if not u or not verify_password(body.password, u["password_hash"]):
        raise HTTPException(401, "Credenciais inválidas")
    touch_last_login(u["id"])
    token = _issue_user_jwt({"id": u["id"], "email": u["email"]})
    return {"jwt": token, "profile": {"email": u["email"]}}
