from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import jwt, time, os

router = APIRouter()

JWT_SECRET = os.getenv("LUNA_JWT_SECRET", "changeme")
JWT_TTL = int(os.getenv("LUNA_JWT_TTL", "86400"))
SUBDOMAIN_DEFAULT = os.getenv("SUBDOMAIN_DEFAULT", "hia-clientes")

class LoginRequest(BaseModel):
    token: str
    label: str | None = None
    number_hint: str | None = None

@router.post("/login")
def login(data: LoginRequest):
    payload = {
        "subdomain": SUBDOMAIN_DEFAULT,
        "token": data.token,
        "label": data.label,
        "number_hint": data.number_hint,
        "exp": time.time() + JWT_TTL
    }
    encoded = jwt.encode(payload, JWT_SECRET, algorithm="HS256")
    return {"jwt": encoded}
