import logging
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from core.config import settings, validate_production_config
from routers import health, nodes, slack, team

logging.basicConfig(level=logging.INFO)

# Fail fast rather than starting half-configured: a missing required setting
# in production must stop the app here, with a clear message naming exactly
# what's missing, not surface later as a confusing runtime error. No-op
# outside MODE=production.
validate_production_config()

app = FastAPI(
    title="Neurix Enterprise API",
    description="Slack ingest, ask, team sharing and insights; web node list, personal and team graphs.",
    version="0.3.0",
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
app.include_router(team.router)


@app.get("/")
async def root():
    return {"message": "Neurix Enterprise API", "version": "0.3.0"}


if __name__ == "__main__":
    import uvicorn

    # Cloud Run injects $PORT at runtime; settings.PORT falls back to 8080
    # for local dev only.
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.environ.get("PORT", settings.PORT)))
