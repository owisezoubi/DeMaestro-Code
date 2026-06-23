"""FastAPI app entry point."""
import os
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

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


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1. Ensure schema exists (idempotent — safe to call on every boot)
    try:
        from app.database import Base, engine
        Base.metadata.create_all(bind=engine)
        print("[startup] Tables ready.", flush=True)
    except Exception as e:
        print(f"[startup] WARNING: Could not create tables: {e}", flush=True)

    # 2. Seed demo data if the database is empty (best-effort; never blocks boot)
    try:
        from app.seed import seed_demo_data
        from app.database import SessionLocal
        import inspect as _inspect
        db = SessionLocal()
        try:
            # Tolerate both signatures: seed_demo_data(db) and seed_demo_data()
            if _inspect.signature(seed_demo_data).parameters:
                seed_demo_data(db)
            else:
                seed_demo_data()
        finally:
            db.close()
    except ImportError:
        pass  # seed.py is optional
    except Exception as e:
        print(f"[startup] WARNING: Could not seed demo data: {e}", flush=True)

    yield
    # Shutdown — nothing to clean up for SQLite


app = FastAPI(
    title="App",
    description="Generated FastAPI app.",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_origin_regex=_localhost_regex,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Vercel serverless safety net ─────────────────────────────────────────────
# On serverless platforms, FastAPI's lifespan handler may not run before the
# first request arrives. This middleware ensures tables exist and demo data
# is seeded on the first request per warm container. Both operations are
# idempotent: create_all is a no-op when tables already exist, and seed
# checks for existing rows before inserting. Runs at most once per cold start.
_db_ready = False


@app.middleware("http")
async def _ensure_db_ready(request, call_next):
    global _db_ready
    if not _db_ready:
        try:
            from app.database import Base, engine, SessionLocal
            import app.models  # registers all model classes with Base  # noqa: F401
            # 1. Create tables if missing (idempotent).
            Base.metadata.create_all(bind=engine)
            print("[middleware] tables ensured", flush=True)
            # 2. Seed demo data only if the users table is empty.
            try:
                from app.seed import seed_demo_data
                from sqlalchemy import text as _text
                import inspect as _inspect
                with SessionLocal() as _check_db:
                    try:
                        user_count = _check_db.execute(
                            _text("SELECT COUNT(*) FROM users")
                        ).scalar()
                    except Exception:
                        user_count = 0
                if user_count == 0:
                    db = SessionLocal()
                    try:
                        if _inspect.signature(seed_demo_data).parameters:
                            seed_demo_data(db)
                        else:
                            seed_demo_data()
                        print("[middleware] users seeded", flush=True)
                    finally:
                        db.close()
                else:
                    print("[middleware] seed skipped — users table not empty", flush=True)
            except ImportError:
                pass  # seed.py is optional
            except Exception as _seed_exc:
                print(f"[middleware] seed failed (non-fatal): {_seed_exc}", flush=True)
            _db_ready = True
        except Exception as _e:
            # Don't block the request — the handler will surface the real error.
            print(f"[middleware] ensure_db_ready failed: {_e}", flush=True)
    return await call_next(request)


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


@app.get("/_health/db", tags=["meta"])
def health_db():
    """Diagnostic: DB connection + tables + row counts."""
    from app.database import engine, SessionLocal
    from sqlalchemy import text, inspect as _sqla_inspect
    import os as _os
    result: dict = {
        "db_url_prefix": "",
        "connect": False,
        "tables": [],
        "row_counts": {},
        "middleware_ran": _db_ready,
    }
    raw = _os.environ.get("DATABASE_URL", "<not set>")
    result["db_url_prefix"] = raw[:30] + "..." if len(raw) > 30 else raw
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
            result["connect"] = True
            inspector = _sqla_inspect(engine)
            tables = sorted(inspector.get_table_names())
            result["tables"] = tables
            with SessionLocal() as session:
                for t in tables:
                    try:
                        count = session.execute(
                            text(f'SELECT COUNT(*) FROM "{t}"')
                        ).scalar()
                        result["row_counts"][t] = int(count or 0)
                    except Exception:
                        result["row_counts"][t] = "error"
    except Exception as e:
        result["error"] = f"{type(e).__name__}: {e}"
    return result


# Auth scaffold (mandatory when auth is enabled)
try:
    from app.routes.auth_routes import router as auth_router
    app.include_router(auth_router, prefix="/api")
except ImportError:
    pass  # auth disabled — no routes file

# Project-specific route includes — generator appends here.
# The LLM should APPEND new include_router calls below this line,
# not replace this file.

# === ROUTE INCLUDES BELOW THIS LINE — LLM appends, never replaces ===
