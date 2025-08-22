# app/routes/name_image.py
import httpx
from fastapi import APIRouter, Depends, HTTPException
# Use a mesma dependência de autenticação já usada nas outras rotas
from app.auth import get_current_user

router = APIRouter()

UAZAPI_URL = "https://hia-clientes.uazapi.com/chat/GetNameAndImageURL"

@router.post("/chat/GetNameAndImageURL")
async def get_name_and_image_url(payload: dict, user=Depends(get_current_user)):
    """
    Proxy para UAZAPI /chat/GetNameAndImageURL
    Exemplo de payload:
      { "number": "553199999999@s.whatsapp.net", "preview": true }
    Usa o token da instância do usuário autenticado (via JWT) no header `token`.
    """
    token = user.get("instance_token") or user.get("token")
    if not token:
        raise HTTPException(status_code=401, detail="Instance token não encontrado para o usuário.")

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(UAZAPI_URL, json=payload, headers={"token": token})
            r.raise_for_status()
            return r.json()
    except httpx.HTTPStatusError as e:
        # Devolve o status real da UAZAPI
        raise HTTPException(status_code=e.response.status_code, detail=e.response.text)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
