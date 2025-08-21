import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .routes import chats, messages, send
from .auth import router as auth_router

def get_allowed_origins():
    raw = os.getenv("FRONTEND_ORIGIN", "*").strip()
    if not raw or raw == "*":
        return ["*"]
    # permite lista separada por vírgula
    return [o.strip() for o in raw.split(",") if o.strip()]

app = FastAPI(title="Luna Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_allowed_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)

@app.get("/api/health")
def health():
    return {"ok": True, "name": "Luna Backend", "origins": get_allowed_origins()}

# Rotas
app.include_router(auth_router, prefix="/api/auth", tags=["auth"])
app.include_router(chats.router, prefix="/api", tags=["chats"])
app.include_router(messages.router, prefix="/api", tags=["messages"])
app.include_router(send.router, prefix="/api", tags=["send"])
