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
    billing,  # <- novo
)
from .auth import router as auth_router  # /api/auth/*
from .pg import init_schema


# ----------------------------- CORS ------------------------------------ #
def _env_list(var: str) -> list[str]:
    raw = (os.getenv(var) or "").strip()
    if not raw:
        return []
    return [v.strip() for v in raw.split(",") if v.strip()]


def allowed_origins() -> list[str]:
    """
    Lista de origens explícitas (sem regex). Use FRONTEND_ORIGIN para acrescentar.
    """
    allowlist: set[str] = {
        # Domínio oficial
        "https://www.lunahia.com.br",
        "https://lunahia.com.br",
        # Deploy Vercel atual
        "https://luna-painel-front-git-main-iahelsenservice-7497s-projects.vercel.app",
    }

    # Extras via env (separar por vírgula)
    for o in _env_list("FRONTEND_ORIGIN"):
        allowlist.add(o)

    # Dev local (habilite via ALLOW_LOCALHOST=1)
    if os.getenv("ALLOW_LOCALHOST", "1") == "1":
        allowlist.update(
            {
                "http://localhost:3000",
                "http://127.0.0.1:3000",
                "http://localhost:5173",
                "http://127.0.0.1:5173",
            }
        )

    return sorted(allowlist)


def allowed_origin_regex() -> str | None:
    """
    Regex opcional para subdomínios, via FRONTEND_ORIGIN_REGEX.
    Ex.: https://([a-z0-9-]+\.)*lunahia\.com\.br$
    """
    rx = (os.getenv("FRONTEND_ORIGIN_REGEX") or "").strip()
    return rx or None


app = FastAPI(title="Luna Backend", version="1.0.0")

# CORS — aceita lista e/ou regex (FastAPI permite um único regex)
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins(),
    allow_origin_regex=allowed_origin_regex(),
    allow_credentials=True,
    allow_methods=["*"],            # libera todos os métodos (inclui OPTIONS p/ preflight)
    allow_headers=["*"],            # inclui Authorization
    expose_headers=["*"],           # opcional: expõe todos
    max_age=86400,                  # cacheia preflight por 1 dia
)


# --------------------------- Startup ----------------------------------- #
@app.on_event("startup")
async def _startup():
    logger = logging.getLogger("uvicorn.error")
    logger.info("Inicializando Luna Backend.")

    # Log de CORS (útil para depuração)
    logger.info("CORS allow_origins: %s", allowed_origins())
    logger.info("CORS allow_origin_regex: %s", allowed_origin_regex())

    db_url = os.getenv("DATABASE_URL") or ""
    if not db_url:
        logger.error(
            "DATABASE_URL não definido! Defina a variável de ambiente para conectar ao Postgres."
        )
    else:
        safe_db = db_url.split("@")[-1]
        logger.info("DATABASE_URL detectado (host/db: %s)", safe_db)

    try:
        init_schema()
        logger.info("Schemas verificados/criados com sucesso (lead_status/billing).")
    except Exception:
        logger.exception("Falha ao inicializar schema do banco.")


# ---------------------------- Rotas ------------------------------------ #
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
app.include_router(billing.router,     prefix="/api/billing",tags=["billing"])  # <- novo

# Healthcheck simples (útil no Railway)
@app.get("/healthz")
async def healthz():
    return {"ok": True}
