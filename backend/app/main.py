"""FastAPI application factory.

Run locally with:
    uvicorn app.main:app --reload
"""

from contextlib import asynccontextmanager
import logging

import sentry_sdk
import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.auth import firebase_admin as fb
from app.config import settings
from app.routes import auth as auth_routes
from app.routes import projects as projects_routes
from app.routes import requirements as requirements_routes
from app.routes import generation as generation_routes


# ---------- Logging ----------
logging.basicConfig(level=settings.log_level)
structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(
        getattr(logging, settings.log_level.upper(), logging.INFO)
    ),
)
log = structlog.get_logger()


# ---------- Sentry ----------
if settings.sentry_dsn:
    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        traces_sample_rate=0.1,
        environment=settings.env,
    )


# ---------- Lifespan ----------
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    log.info("startup", env=settings.env, mock_ai=settings.mock_ai)
    fb.init_firebase()
    yield
    # Shutdown
    log.info("shutdown")


# ---------- App ----------
app = FastAPI(
    title="DeMaestro Backend",
    description="Autonomous Multi-Agent System for Full-Stack Web App Synthesis",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_origin_regex=r"https://.*\.vercel\.app",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------- Routes ----------
@app.get("/health", tags=["meta"])
async def health():
    """Liveness probe."""
    return {"status": "ok", "env": settings.env}


app.include_router(auth_routes.router, prefix="/auth", tags=["auth"])
app.include_router(projects_routes.router, prefix="/projects", tags=["projects"])
app.include_router(
    requirements_routes.router, prefix="/projects", tags=["requirements"]
)
app.include_router(generation_routes.router, prefix="/projects", tags=["generation"])
