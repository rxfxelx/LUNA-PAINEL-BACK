# app/main.py
import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# rotas internas
from .routes import chats, messages, send, realtime, meta, name_image, crm, ai
from .auth import router as auth_router  # contém /api/auth/login, /api/auth/check, /api/auth/debug

def allowed_origins():
    raw = (os.getenv("FRONTEND_ORIGIN") or "*").strip()
    if not raw or raw == "*":
        return ["*"]
    # permite lista separada por vírgula
    return [o.strip() for o in raw.split(",") if o.strip()]

app = FastAPI(title="Luna Backend", version="1.0.0")

# CORS — libera Authorization e preflight
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],  # inclui Authorization, Content-Type etc
)

@app.on_event("startup")
async def _startup_log():
    import logging
    logger = logging.getLogger("uvicorn")
    logger.info(f"[Luna] CORS allow_origins = {allowed_origins()}")
    logger.info(f"[Luna] UAZAPI_HOST = {os.getenv('UAZAPI_HOST')}")
    # NÃO loga o segredo; o /api/auth/debug já mostra apenas o tail

@app.get("/")
def root():
    return {"ok": True, "service": "luna-backend", "version": "1.0.0"}

@app.get("/api/health")
def health():
    return {"ok": True, "origins": allowed_origins()}

# Routers
app.include_router(auth_router,       prefix="/api/auth", tags=["auth"])
app.include_router(chats.router,      prefix="/api",      tags=["chats"])
app.include_router(messages.router,   prefix="/api",      tags=["messages"])
app.include_router(send.router,       prefix="/api",      tags=["send"])
app.include_router(realtime.router,   prefix="/api",      tags=["sse"])
app.include_router(meta.router,       prefix="/api",      tags=["meta"])
app.include_router(name_image.router, prefix="/api",      tags=["name-image"])

# CRM
app.include_router(crm.router,        prefix="/api/crm",  tags=["crm"])

# IA (classificação automática de estágio)
app.include_router(ai.router,         prefix="/api",      tags=["ai"])
