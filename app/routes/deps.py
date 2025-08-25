# app/routes/deps.py
import os
from fastapi import Depends, HTTPException
from app.auth import get_current_user

def get_uazapi_ctx(user=Depends(get_current_user)) -> dict:
    """
    Extrai dados para falar com a UAZAPI de forma robusta.
    - token da instância: user['token'] OU user['instance_token']
    - host: user['host'] OU env UAZAPI_HOST
    """
    token = (user.get("token") or user.get("instance_token") or "").strip()
    host  = (user.get("host")  or os.getenv("UAZAPI_HOST") or "").strip()

    if not token:
        raise HTTPException(status_code=401, detail="JWT sem token de instância")
    if not host:
        raise HTTPException(status_code=401, detail="JWT sem host e UAZAPI_HOST não definido")

    return {"token": token, "host": host}
