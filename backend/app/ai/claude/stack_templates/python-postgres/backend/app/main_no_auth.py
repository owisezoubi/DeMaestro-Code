"""FastAPI application — public / no-auth variant.

Used when auth is disabled (auth_required = false in structured requirements).
No auth_router. No JWT. No session. Every route is publicly accessible.
"""
import os

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi import Request

log = structlog.get_logger()

_cors_origins = [
    o.strip()
    for o in os.environ.get("CORS_ORIGINS", "").split(",")
    if o.strip()
]
_allow_localhost = os.environ.get("ALLOW_LOCALHOST_CORS", "true").lower() == "true"
_localhost_regex = (
    r"https?://(localhost|127\.0\.0\.1)(:\d+)?" if _allow_localhost else None
)

app = FastAPI(
    title="{{app_name}}",
    description="Public app — no authentication required.",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_origin_regex=_localhost_regex,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    origin = request.headers.get("origin", "")
    headers: dict[str, str] = {}
    if origin:
        headers["access-control-allow-origin"] = origin
        headers["access-control-allow-credentials"] = "true"
        headers["vary"] = "Origin"
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error", "message": str(exc)},
        headers=headers,
    )


@app.get("/health", tags=["meta"])
def health():
    """Liveness probe."""
    return {"status": "ok"}


# LLM-generated route includes are appended after this point by the generator.
