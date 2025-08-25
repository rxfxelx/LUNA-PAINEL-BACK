import logging
import httpx
from fastapi import APIRouter, Depends, HTTPException
from app.auth import get_current_user

log = logging.getLogger("name_image")
router = APIRouter()

UAZAPI_URL = "https://hia-clientes.uazapi.com/chat/GetNameAndImageURL"

@router.post("/chat/GetNameAndImageURL")
async def get_name_and_image_url(payload: dict, user=Depends(get_current_user)):
    token = user.get("instance_token") or user.get("token")
    if not token:
        raise HTTPException(status_code=401, detail="Instance token não encontrado para o usuário.")

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.post(UAZAPI_URL, json=payload, headers={"token": token})
            log.info("GetNameAndImageURL req=%s status=%s", payload, r.status_code)
            if r.status_code != 200:
                raise HTTPException(status_code=r.status_code, detail=r.text)
            data = r.json()
            try:
                keys = list(data.keys()) if isinstance(data, dict) else str(type(data))
            except Exception:
                keys = "unknown"
            log.info("UAZAPI response keys=%s", keys)
            return data
    except HTTPException:
        raise
    except Exception as e:
        log.exception("Erro ao contatar UAZAPI")
        raise HTTPException(status_code=500, detail=str(e))
