# app/routes/name_image.py
import httpx
from fastapi import APIRouter, HTTPException

router = APIRouter()

UAZAPI_URL = "https://hia-clientes.uazapi.com/chat/GetNameAndImageURL"

@router.post("/chat/GetNameAndImageURL")
async def get_name_and_image_url(payload: dict):
    """
    Encaminha o payload para a UAZAPI e retorna o resultado.
    Exemplo de payload esperado:
    {
        "number": "553134282376@s.whatsapp.net",
        "preview": true
    }
    """
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(UAZAPI_URL, json=payload)
            response.raise_for_status()
            return response.json()
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
