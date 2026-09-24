import logging
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from core.config import settings
from routers import health, nodes, slack

logging.basicConfig(level=logging.INFO)

app = FastAPI(
    title="Neurix Enterprise API",
    description="Slack ingest + ask, web node list and personal graph (Week 2).",
    version="0.2.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.FRONTEND_URL],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(nodes.router)
app.include_router(slack.router)


@app.get("/")
async def root():
    return {"message": "Neurix Enterprise API", "version": "0.2.0"}


if __name__ == "__main__":
    import uvicorn

    # Cloud Run injects $PORT at runtime; settings.PORT falls back to 8080
    # for local dev only.
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.environ.get("PORT", settings.PORT)))
