import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .routes import chats, messages, send, realtime
from .auth import router as auth_router

def allowed_origins():
    raw = os.getenv("FRONTEND_ORIGIN", "*").strip()
    if not raw or raw == "*":
        return ["*"]
    return [o.strip() for o in raw.split(",") if o.strip()]

app = FastAPI(title="Luna Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/api/health")
def health():
    return {"ok": True, "origins": allowed_origins()}

# Rotas
app.include_router(auth_router,        prefix="/api/auth", tags=["auth"])
app.include_router(chats.router,       prefix="/api",      tags=["chats"])
app.include_router(messages.router,    prefix="/api",      tags=["messages"])
app.include_router(send.router,        prefix="/api",      tags=["send"])
app.include_router(realtime.router,    prefix="/api",      tags=["sse"])
