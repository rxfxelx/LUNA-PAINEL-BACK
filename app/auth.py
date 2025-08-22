import os, time, jwt
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter()

JWT_SECRET  = os.getenv("LUNA_JWT_SECRET", "changeme")
JWT_TTL     = int(os.getenv("LUNA_JWT_TTL", "86400"))
UAZAPI_HOST = os.getenv("UAZAPI_HOST", "hia-clientes.uazapi.com")  # FIXO

class LoginRequest(BaseModel):
    token: str
    label: str | None = None
    number_hint: str | None = None

@router.post("/login")
def login(data: LoginRequest):
    if not data.token:
        raise HTTPException(400, "Instance token obrigatório")
    payload = {
        "host": UAZAPI_HOST,
        "token": data.token,
        "label": data.label,
        "number_hint": data.number_hint,
        "exp": int(time.time()) + JWT_TTL,
    }
    encoded = jwt.encode(payload, JWT_SECRET, algorithm="HS256")
    return {
        "jwt": encoded,
        "profile": {"label": data.label, "host": UAZAPI_HOST, "number_hint": data.number_hint}
    }
