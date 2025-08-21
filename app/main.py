from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from .routes import chats, messages, send
from .auth import router as auth_router

app = FastAPI(title="Luna Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # ajuste para FRONTEND_ORIGIN em produção
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Rotas
app.include_router(auth_router, prefix="/api/auth", tags=["auth"])
app.include_router(chats.router, prefix="/api", tags=["chats"])
app.include_router(messages.router, prefix="/api", tags=["messages"])
app.include_router(send.router, prefix="/api", tags=["send"])
