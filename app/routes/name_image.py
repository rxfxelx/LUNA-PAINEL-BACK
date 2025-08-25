# app/routes/name_image.py
import httpx
from fastapi import APIRouter, Depends, HTTPException, Body
from app.routes.deps import get_uazapi_ctx  # mesmo deps usado nas outras rotas

router = APIRouter()

def _uaz(ctx):
    base = f"https://{ctx['host']}"
    headers = {"token": ctx["token"]}
    return base, headers

def _normalize(resp: dict) -> dict:
    """
    Normaliza a resposta da UAZAPI para sempre conter:
      { "name": str|None, "image": str|None, "imagePreview": str|None }
    (sem quebrar se vierem chaves diferentes)
    """
    if not isinstance(resp, dict):
        return {"name": None, "image": None, "imagePreview": None}

    name = resp.get("name") or resp.get("wa_name") or resp.get("fullName") or resp.get("displayName")
    image = resp.get("image") or resp.get("photo") or resp.get("profileImage")
    image_preview = resp.get("imagePreview") or resp.get("photoPreview") or resp.get("preview")

    return {
        "name": name,
        "image": image,
        "imagePreview": image_preview
    }

async def _try(cli: httpx.AsyncClient, method: str, url: str, headers: dict, json=None, params=None):
    if method == "GET":
        return await cli.get(url, headers=headers, params=params)
    return await cli.post(url, headers=headers, json=json)

@router.post("/name-image")
async def get_name_and_image(
    payload: dict = Body(..., example={"number": "5531999999999@s.whatsapp.net", "preview": True}),
    ctx=Depends(get_uazapi_ctx),
):
    """
    Proxy para o endpoint de nome/foto da UAZAPI, com fallbacks de rota:
      - /chat/GetNameAndImageURL
      - /chat/GetNameAndImageUrl
      - /chat/getNameAndImageURL
      - /chat/getNameAndImageUrl
      - /chat/GetNameAndImage      (algumas instâncias)
      - /chat/getNameAndImage
    Body esperado:
      { "number": "<wa_chatid ou número>", "preview": true|false }
    """
    number = (payload.get("number") or "").strip()
    preview = bool(payload.get("preview", True))
    if not number:
        raise HTTPException(status_code=400, detail="number é obrigatório")

    base, headers = _uaz(ctx)
    body = {"number": number, "preview": preview}
    params = {"number": number, "preview": "true" if preview else "false"}

    attempts = [
        ("POST", f"{base}/chat/GetNameAndImageURL", body, None),
        ("POST", f"{base}/chat/GetNameAndImageUrl", body, None),
        ("POST", f"{base}/chat/getNameAndImageURL", body, None),
        ("POST", f"{base}/chat/getNameAndImageUrl", body, None),
        ("POST", f"{base}/chat/GetNameAndImage", body, None),
        ("POST", f"{base}/chat/getNameAndImage", body, None),

        ("GET",  f"{base}/chat/GetNameAndImageURL", None, params),
        ("GET",  f"{base}/chat/GetNameAndImageUrl", None, params),
        ("GET",  f"{base}/chat/getNameAndImageURL", None, params),
        ("GET",  f"{base}/chat/getNameAndImageUrl", None, params),
        ("GET",  f"{base}/chat/GetNameAndImage", None, params),
        ("GET",  f"{base}/chat/getNameAndImage", None, params),
    ]

    last_status = 502
    last_text = "Upstream error"

    async with httpx.AsyncClient(timeout=25) as cli:
        for method, url, json_payload, query in attempts:
            r = await _try(cli, method, url, headers, json=json_payload, params=query)
            last_status, last_text = r.status_code, r.text

            if 200 <= r.status_code < 300:
                try:
                    data = r.json()
                except Exception:
                    raise HTTPException(status_code=502, detail="Resposta inválida da UAZAPI em name-image")
                return _normalize(data)

            if r.status_code in (404, 405):
                continue
            raise HTTPException(status_code=r.status_code, detail=last_text)

    raise HTTPException(status_code=last_status, detail=last_text)
