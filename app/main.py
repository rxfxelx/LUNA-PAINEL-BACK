# app/main.py
import os
import logging
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Rotas internas
from .routes import (
    chats,
    messages,
    send,
    realtime,
    meta,
    name_image,
    crm,
    ai,
    media,
    lead_status,
)
from .auth import router as auth_router  # /api/auth/*
from .pg import init_schema


def allowed_origins():
    raw = (os.getenv("FRONTEND_ORIGIN") or "*").strip()
    if not raw or raw == "*":
        return ["*"]
    return [o.strip() for o in raw.split(",") if o.strip()]


app = FastAPI(title="Luna Backend", version="1.0.0")  # <- atributo que o Uvicorn procura

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def _startup():
    logger = logging.getLogger("uvicorn.error")
    logger.info("Inicializando Luna Backend...")

    db_url = os.getenv("DATABASE_URL") or ""
    if not db_url:
        logger.error("DATABASE_URL não definido! Defina a variável de ambiente para conectar ao Postgres.")
    else:
        safe_db = db_url.split("@")[-1]
        logger.info("DATABASE_URL detectado (host/db: %s)", safe_db)

    try:
        init_schema()
        logger.info("Schema 'lead_status' verificado/criado com sucesso.")
    except Exception:
        logger.exception("Falha ao inicializar schema do banco.")


# Auth
app.include_router(auth_router,           prefix="/api/auth",   tags=["auth"])

# Core
app.include_router(chats.router,          prefix="/api",        tags=["chats"])
app.include_router(messages.router,       prefix="/api",        tags=["messages"])
app.include_router(send.router,           prefix="/api",        tags=["send"])
app.include_router(realtime.router,       prefix="/api",        tags=["sse"])
app.include_router(meta.router,           prefix="/api",        tags=["meta"])
app.include_router(name_image.router,     prefix="/api",        tags=["name-image"])

# CRM
app.include_router(crm.router,            prefix="/api/crm",    tags=["crm"])

# IA
app.include_router(ai.router,             prefix="/api",        tags=["ai"])

# MEDIA  (o router já tem prefixo /api/media dentro do arquivo; NÃO duplicar aqui)
app.include_router(media.router)

# Lead status cache (router sem prefixo interno; aqui aplicamos /api)
app.include_router(lead_status.router,    prefix="/api",        tags=["lead-status"])
