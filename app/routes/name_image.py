# app/routes/name_image.py
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
import httpx
import os

# >>> Ajuste este import para o seu projeto <<<
# Precisa de uma função que recupere o usuário da JWT e traga o token da instância.
# Nos exemplos anteriores chamamos de get_current_user e ela retorna um dict
# com a chave "instance_token". Se o seu nome for diferente, ajuste aqui.
from app.auth import get_current_user  # noqa: F401  (ajuste o caminho se necessário)

router = APIRouter()

UAZAPI_BASE_URL = os.getenv("UAZAPI_BASE_URL", "https://hia-clientes.uazapi.com")


class NameImageIn(BaseModel):
    number: str = Field(..., description="Ex.: 553199999999@s.whatsapp.net")
    preview: bool = Field(True, description="Se true, retorna imagePreview quando existir")


@router.post("/chat/name-image")
async def post_name_image(
    payload: NameImageIn,
    user: dict = Depends(get_current_user),
):
    """
    Proxy para a UAZAPI: /chat/GetNameAndImageURL
    Espera { number: '<wa_chatid OU numero@s.whatsapp.net>', preview: true }
    Usa o token da instância do usuário autenticado (JWT) no header `token`.
    """

    instance_token = user.get("instance_token") or user.get("token")
    if not instance_token:
        raise HTTPException(status_code=401, detail="Instance token ausente no usuário autenticado.")

    url = f"{UAZAPI_BASE_URL}/chat/GetNameAndImageURL"

    # Monta o corpo como a UAZAPI espera
    body = {"number": payload.number, "preview": bool(payload.preview)}

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.post(url, json=body, headers={"token": instance_token})
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Erro ao contatar UAZAPI: {e!s}")

    # Repassa status de erro da UAZAPI se houver
    if r.status_code != 200:
        raise HTTPException(status_code=r.status_code, detail=r.text)

    # Resposta JSON da UAZAPI, ex.:
    # { "id": "...", "image": "...", "imagePreview": "...", "wa_name": "...", ... }
    return r.json()
