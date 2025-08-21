import os, time, jwt
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter()

JWT_SECRET = os.getenv("LUNA_JWT_SECRET", "changeme")
JWT_TTL    = int(os.getenv("LUNA_JWT_TTL", "86400"))  # segundos
SUBDOMAIN_DEFAULT = os.getenv("SUBDOMAIN_DEFAULT", "hia-clientes")  # fixo no back

class LoginRequest(BaseModel):
    token: str                  # instance token (vem do front, por usuário)
    label: str | None = None
    number_hint: str | None = None

@router.post("/login")
def login(data: LoginRequest):
    if not data.token:
        raise HTTPException(400, "Instance token obrigatório")

    payload = {
        "subdomain": SUBDOMAIN_DEFAULT,
        "token": data.token,
        "label": data.label,
        "number_hint": data.number_hint,
        "exp": int(time.time()) + JWT_TTL,
    }
    encoded = jwt.encode(payload, JWT_SECRET, algorithm="HS256")
    return {
        "jwt": encoded,
        "profile": {
            "label": data.label,
            "subdomain": SUBDOMAIN_DEFAULT,
            "number_hint": data.number_hint,
        }
    }
