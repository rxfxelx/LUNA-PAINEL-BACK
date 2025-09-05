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
    media,
    lead_status,
)
from .auth import router as auth_router  # /api/auth/*
from .pg import init_schema


# ---- CORS -------------------------------------------------------------
def allowed_origins() -> list[str]:
    """
    Origem permitida = (ENV FRONTEND_ORIGIN, separado por vírgula) + allowlist fixa.
    """
    # allowlist fixa (seu domínio + o domínio atual da Vercel)
    allowlist = {
        "https://www.lunahia.com.br",
        "https://luna-painel-front-git-main-iahelsenservice-7497s-projects.vercel.app",
    }

    raw = (os.getenv("FRONTEND_ORIGIN") or "").strip()
    if raw:
        for o in raw.split(","):
            o = o.strip()
            if o:
                allowlist.add(o)

    # Railway/localhost (úteis em testes)
    if os.getenv("ALLOW_LOCALHOST", "1") == "1":
        allowlist.update({
            "http://localhost:3000",
            "http://127.0.0.1:3000",
            "http://localhost:5173",
            "http://127.0.0.1:5173",
        })

    return sorted(allowlist)


app = FastAPI(title="Luna Backend", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*", "Authorization"],
)


# ---- Startup -----------------------------------------------------------
@app.on_event("startup")
async def _startup():
    logger = logging.getLogger("uvicorn.error")
    logger.info("Inicializando Luna Backend.")

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


# ---- Rotas -------------------------------------------------------------
# Auth
app.include_router(auth_router,        prefix="/api/auth",   tags=["auth"])

# Core
app.include_router(chats.router,       prefix="/api",        tags=["chats"])
app.include_router(messages.router,    prefix="/api",        tags=["messages"])
app.include_router(send.router,        prefix="/api",        tags=["send"])
app.include_router(realtime.router,    prefix="/api",        tags=["realtime"])
app.include_router(meta.router,        prefix="/api",        tags=["meta"])
app.include_router(name_image.router,  prefix="/api",        tags=["name-image"])
app.include_router(crm.router,         prefix="/api",        tags=["crm"])
# (não incluir ai.router: app/routes/ai.py não define APIRouter)
app.include_router(media.router,       prefix="/api/media",  tags=["media"])
app.include_router(lead_status.router, prefix="/api",        tags=["lead-status"])
