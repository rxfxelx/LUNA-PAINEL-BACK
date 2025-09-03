# app/main.py
import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# rotas internas
from .routes import chats, messages, send, realtime, meta, name_image, crm, ai, media, lead_status
from .auth import router as auth_router  # /api/auth/login, /api/auth/check, /api/auth/debug
from .pg import init_schema

def allowed_origins():
    raw = (os.getenv("FRONTEND_ORIGIN") or "*").strip()
    if not raw or raw == "*":
        return ["*"]
    return [o.strip() for o in raw.split(",") if o.strip()]

app = FastAPI(title="Luna Backend", version="1.0.0")
...
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
async def _startup_log():
    import logging
    logger = logging.getLo
...

# Auth
app.include_router(auth_router,     prefix="/api/auth", tags=["auth"])

# Core
app.include_router(chats.router,     prefix="/api",      tags=["chats"])
app.include_router(messages.router,  prefix="/api",      tags=["messages"])
app.include_router(send.router,      prefix="/api",      tags=["send"])
app.include_router(realtime.router,  prefix="/api",      tags=["sse"])
app.include_router(meta.router,      prefix="/api",      tags=["meta"])
app.include_router(name_image.router,prefix="/api",      tags=["name-image"])

# CRM
app.include_router(crm.router,       prefix="/api/crm",  tags=["crm"])

# IA
app.include_router(ai.router,        prefix="/api",      tags=["ai"])

# MEDIA
app.include_router(media.router,      prefix="/api/media", tags=["media"])

# Lead status cache
app.include_router(lead_status.router, prefix="/api",       tags=["lead-status"])

@app.on_event("startup")
async def _init_lead_status_schema():
    try:
        init_schema()
    except Exception:
        pass
