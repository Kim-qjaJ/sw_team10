from fastapi import FastAPI
from app.routers.chat import router as chat_router

app = FastAPI(
    title="Busan Mate API",
    version="0.1.0",
    description="Busan Mate prototype backend",
)

app.include_router(chat_router, prefix="/api/v1")


@app.get("/")
def root():
    return {"service": "Busan Mate", "status": "running"}


@app.get("/health")
def health():
    return {"status": "ok"}
