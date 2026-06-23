"""Vercel API integration. Deploys generated apps and returns the live URL.
Configured entirely via env vars — no end-user input required."""

import base64
import os
import re
import time

import requests
import structlog
from dotenv import load_dotenv

# DeMaestro uses pydantic-settings which reads .env into a typed
# Settings object but does NOT populate os.environ. This module
# reads from os.environ directly, so we manually load .env here.
# Idempotent -- safe to call multiple times.
load_dotenv()

log = structlog.get_logger("vercel_deployer")

VERCEL_API = "https://api.vercel.com"
REQUEST_TIMEOUT = 30
BUILD_POLL_TIMEOUT = 300


class VercelDeployError(Exception):
    """Raised on any Vercel deployment failure."""


def is_configured() -> bool:
    """True iff both Vercel token and Postgres URL are set."""
    token = os.environ.get("DEMAESTRO_VERCEL_TOKEN", "").strip()
    pg = os.environ.get("DEMAESTRO_POSTGRES_URL", "").strip()
    if not token:
        log.info(
            "vercel_deployer.not_configured",
            missing="DEMAESTRO_VERCEL_TOKEN",
            hint="add to backend/.env then restart",
        )
        return False
    if not pg:
        log.info(
            "vercel_deployer.not_configured",
            missing="DEMAESTRO_POSTGRES_URL",
            hint="add to backend/.env then restart",
        )
        return False
    return True


def is_auto_deploy_enabled() -> bool:
    """True iff is_configured AND DEMAESTRO_AUTO_DEPLOY != false."""
    if not is_configured():
        return False
    flag = os.environ.get("DEMAESTRO_AUTO_DEPLOY", "true").strip().lower()
    return flag not in ("false", "0", "no", "off")


def _slugify(name: str) -> str:
    """Vercel project name rules: lowercase, alphanumeric, hyphens, max 63 chars."""
    slug = re.sub(r"[^a-z0-9-]+", "-", name.lower()).strip("-")
    return (slug or "demaestro-app")[:63]


def _ensure_project(token: str, slug: str) -> None:
    """Create the Vercel project if it doesn't exist; skip 409 Conflict."""
    r = requests.post(
        f"{VERCEL_API}/v9/projects",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": slug, "framework": None},
        timeout=REQUEST_TIMEOUT,
    )
    if r.status_code in (200, 201):
        log.info("vercel.project.created", slug=slug)
    elif r.status_code == 409:
        log.info("vercel.project.exists", slug=slug)
    else:
        raise VercelDeployError(
            f"create_project {r.status_code}: {r.text[:200]}"
        )


def _set_env(token: str, slug: str, key: str, value: str) -> None:
    """Upsert an env var on the Vercel project (production + preview targets)."""
    r = requests.post(
        f"{VERCEL_API}/v9/projects/{slug}/env",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "key": key,
            "value": value,
            "type": "encrypted",
            "target": ["production", "preview"],
        },
        timeout=REQUEST_TIMEOUT,
    )
    if r.status_code in (200, 201):
        return
    if r.status_code == 400 and "already exists" in r.text.lower():
        # Look up the existing env var id and PATCH it.
        ls = requests.get(
            f"{VERCEL_API}/v9/projects/{slug}/env",
            headers={"Authorization": f"Bearer {token}"},
            timeout=REQUEST_TIMEOUT,
        )
        if ls.status_code != 200:
            raise VercelDeployError(f"list_env {ls.status_code}: {ls.text[:200]}")
        envs = ls.json().get("envs", [])
        match = next((e for e in envs if e.get("key") == key), None)
        if not match:
            raise VercelDeployError(f"env '{key}' exists but id not findable")
        patch = requests.patch(
            f"{VERCEL_API}/v9/projects/{slug}/env/{match['id']}",
            headers={"Authorization": f"Bearer {token}"},
            json={"value": value},
            timeout=REQUEST_TIMEOUT,
        )
        if patch.status_code not in (200, 201):
            raise VercelDeployError(
                f"patch_env {key} {patch.status_code}: {patch.text[:200]}"
            )
        return
    raise VercelDeployError(f"set_env {key} {r.status_code}: {r.text[:200]}")


def _create_deployment(token: str, slug: str, files: dict) -> dict:
    """Push files to Vercel and return the deployment JSON."""
    payload_files = []
    for path, content in files.items():
        if not path or path.endswith("package-lock.json"):
            continue
        if isinstance(content, bytes):
            data = content
        elif isinstance(content, str):
            data = content.encode("utf-8")
        else:
            data = str(content).encode("utf-8")
        payload_files.append({
            "file": path,
            "data": base64.b64encode(data).decode("ascii"),
            "encoding": "base64",
        })
    body = {
        "name": slug,
        "files": payload_files,
        "target": "production",
        "projectSettings": {
            "framework": None,
            "buildCommand": "cd frontend && npm install && npm run build",
            "outputDirectory": "frontend/dist",
            "installCommand": "echo skip-root-install",
        },
    }
    r = requests.post(
        f"{VERCEL_API}/v13/deployments",
        headers={"Authorization": f"Bearer {token}"},
        json=body,
        timeout=120,
    )
    if r.status_code not in (200, 201):
        raise VercelDeployError(
            f"create_deployment {r.status_code}: {r.text[:400]}"
        )
    return r.json()


def initialize_neon_db(files: dict, database_url: str) -> dict:
    """Connect to Neon, create tables and seed using the generated app's models.
    Runs BEFORE Vercel deploy so the DB is ready on the first request.
    Returns {"tables_created": [...], "users_seeded": int, "errors": [...]}.
    """
    import importlib
    import inspect as _ins
    import shutil
    import sys
    import tempfile
    from pathlib import Path

    result: dict = {"tables_created": [], "users_seeded": 0, "errors": []}
    tmp = Path(tempfile.mkdtemp(prefix="demaestro_neon_init_"))

    # Track which modules we add so we can remove them all on exit.
    pre_existing_mods = set(sys.modules)

    try:
        # Write backend .py files to temp dir.
        for path, content in files.items():
            if not path.startswith("backend/") or not path.endswith(".py"):
                continue
            dest = tmp / path
            dest.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(content, str):
                dest.write_text(content, encoding="utf-8")
            else:
                dest.write_bytes(content)

        backend_dir = str(tmp / "backend")
        if not (tmp / "backend").exists():
            result["errors"].append("no backend/ dir in files")
            return result

        sys.path.insert(0, backend_dir)
        try:
            import os as _os
            _os.environ["DATABASE_URL"] = database_url

            # Clear any stale app.* modules from a previous init call.
            for mod_name in list(sys.modules):
                if mod_name == "app" or mod_name.startswith("app."):
                    sys.modules.pop(mod_name, None)

            db_module = importlib.import_module("app.database")
            try:
                importlib.import_module("app.models")
            except Exception as me:
                result["errors"].append(f"models import failed: {me}")
            try:
                importlib.import_module("app.auth_models")
            except Exception:
                pass

            Base = db_module.Base
            engine = db_module.engine
            Base.metadata.create_all(bind=engine)

            from sqlalchemy import inspect as sa_inspect
            result["tables_created"] = sorted(sa_inspect(engine).get_table_names())
            log.info(
                "neon_init.tables_created",
                count=len(result["tables_created"]),
                tables=result["tables_created"],
            )

            try:
                seed_module = importlib.import_module("app.seed")
                seed_fn = getattr(seed_module, "seed_demo_data", None)
                if seed_fn:
                    SessionLocal = db_module.SessionLocal
                    session = SessionLocal()
                    try:
                        if _ins.signature(seed_fn).parameters:
                            seed_fn(session)
                        else:
                            seed_fn()
                    finally:
                        session.close()

                    from sqlalchemy import text
                    check = SessionLocal()
                    try:
                        count = check.execute(text("SELECT COUNT(*) FROM users")).scalar() or 0
                        result["users_seeded"] = int(count)
                    finally:
                        check.close()
                    log.info("neon_init.seeded", users=result["users_seeded"])
            except ImportError:
                result["errors"].append("seed.py not present")
            except Exception as seed_exc:
                result["errors"].append(f"seed failed: {seed_exc}")
                log.warning("neon_init.seed_failed", error=str(seed_exc))
        finally:
            if backend_dir in sys.path:
                sys.path.remove(backend_dir)
            # Clean up all modules we imported so DeMaestro's own app.* namespace is safe.
            for mod_name in list(sys.modules):
                if mod_name not in pre_existing_mods:
                    sys.modules.pop(mod_name, None)
    except Exception as e:
        result["errors"].append(f"init failed: {e}")
        log.error("neon_init.failed", error=str(e))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    return result


def disable_deployment_protection(token: str, project_slug: str) -> None:
    """Disable Vercel Deployment Protection so public users can access the URL."""
    try:
        r = requests.patch(
            f"{VERCEL_API}/v9/projects/{project_slug}",
            headers={"Authorization": f"Bearer {token}"},
            json={"ssoProtection": None, "passwordProtection": None},
            timeout=REQUEST_TIMEOUT,
        )
        if r.status_code in (200, 201):
            log.info("vercel.protection_disabled", slug=project_slug)
        else:
            log.warning(
                "vercel.protection_disable_failed",
                status=r.status_code,
                body=r.text[:200],
            )
    except Exception as e:
        log.warning("vercel.protection_disable_exception", error=str(e))


def _get_production_url(token: str, project_slug: str) -> str | None:
    """Return the stable production alias URL for the project, or None."""
    try:
        r = requests.get(
            f"{VERCEL_API}/v9/projects/{project_slug}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=REQUEST_TIMEOUT,
        )
        if r.status_code == 200:
            data = r.json()
            targets = data.get("targets") or {}
            prod = targets.get("production") or {}
            alias = prod.get("alias") or []
            if alias:
                return f"https://{alias[0]}"
            return f"https://{project_slug}.vercel.app"
    except Exception:
        pass
    return None


def _poll_ready(token: str, deployment_id: str) -> dict:
    """Poll until the deployment reaches READY (or ERROR/CANCELED).
    On ERROR/CANCELED, attaches build logs to the exception as `.build_logs`.
    """
    deadline = time.time() + BUILD_POLL_TIMEOUT
    while time.time() < deadline:
        r = requests.get(
            f"{VERCEL_API}/v13/deployments/{deployment_id}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=REQUEST_TIMEOUT,
        )
        if r.status_code != 200:
            raise VercelDeployError(f"poll {r.status_code}: {r.text[:200]}")
        data = r.json()
        state = data.get("readyState") or data.get("state")
        if state == "READY":
            return data
        if state in ("ERROR", "CANCELED"):
            build_logs = fetch_vercel_runtime_logs(token, deployment_id)
            err = VercelDeployError(f"deployment {state.lower()}")
            err.build_logs = build_logs  # type: ignore[attr-defined]
            raise err
        time.sleep(3)
    raise VercelDeployError(f"timeout after {BUILD_POLL_TIMEOUT}s")


def deploy(
    project_name: str,
    files: dict,
    wait_for_ready: bool = True,
) -> dict:
    """Deploy files to Vercel. Returns {url, deployment_id, project_name, state}."""
    token = os.environ.get("DEMAESTRO_VERCEL_TOKEN")
    pg_url = os.environ.get("DEMAESTRO_POSTGRES_URL")
    if not token or not pg_url:
        raise VercelDeployError(
            "DEMAESTRO_VERCEL_TOKEN or DEMAESTRO_POSTGRES_URL not set"
        )

    import secrets as _secrets

    slug = _slugify(project_name)
    log.info("vercel.deploy.start", project=slug, files=len(files))

    _ensure_project(token, slug)
    disable_deployment_protection(token, slug)

    env_vars = {
        "DATABASE_URL": pg_url,
        "JWT_SECRET_KEY": _secrets.token_hex(32),
        "CORS_ORIGINS": "*",
    }
    for k, v in env_vars.items():
        _set_env(token, slug, k, v)

    # Initialize Neon DB BEFORE deploying so tables + seed exist when the
    # first Vercel request arrives (no cold-start race).
    log.info("vercel.neon_init.start", project=slug)
    neon_result = initialize_neon_db(files, pg_url)
    log.info(
        "vercel.neon_init.done",
        project=slug,
        tables=neon_result["tables_created"],
        users_seeded=neon_result["users_seeded"],
        errors=neon_result["errors"],
    )
    if neon_result["errors"]:
        log.warning(
            "vercel.neon_init.had_errors",
            project=slug,
            errors=neon_result["errors"],
            hint="middleware will attempt recovery on first request",
        )

    deployment = _create_deployment(token, slug, files)
    did = deployment["id"]
    url = deployment.get("url", "")
    if url and not url.startswith("http"):
        url = f"https://{url}"

    if wait_for_ready:
        ready = _poll_ready(token, did)
        # Use stable production alias URL instead of the per-deploy hash URL.
        prod_url = _get_production_url(token, slug)
        final = prod_url or ready.get("url", url)
        if final and not final.startswith("http"):
            final = f"https://{final}"
        state = ready.get("readyState", "READY")
        try:
            _set_env(token, slug, "CORS_ORIGINS", final)
        except Exception as exc:
            log.warning("vercel.cors_tighten_failed", error=str(exc))
    else:
        final = url
        state = "BUILDING"

    log.info("vercel.deploy.done", project=slug, url=final, state=state)

    if wait_for_ready and final:
        # Probe the deployed app: wake the function, verify DB, retry seed if needed.
        try:
            requests.get(f"{final}/api/_health/db", timeout=20)
            time.sleep(3)
            check = requests.get(f"{final}/api/_health/db", timeout=20)
            if check.status_code == 200:
                data = check.json()
                if not data.get("connect"):
                    log.error(
                        "vercel.post_deploy.db_unreachable",
                        url=final,
                        error=data.get("error"),
                        hint="check DATABASE_URL in Vercel env vars",
                    )
                else:
                    row_counts = data.get("row_counts", {})
                    users_count = row_counts.get("users", 0)
                    total_rows = sum(
                        v for v in row_counts.values() if isinstance(v, int)
                    )
                    log.info(
                        "vercel.post_deploy.db_ready",
                        url=final,
                        tables=data.get("tables", []),
                        total_rows=total_rows,
                        users=users_count,
                    )

                    # If users table is empty, re-run init (idempotent).
                    if not isinstance(users_count, int) or users_count < 1:
                        log.warning(
                            "vercel.post_deploy.no_seed_data",
                            url=final,
                            hint="re-running initialize_neon_db",
                        )
                        retry = initialize_neon_db(files, pg_url)
                        log.info(
                            "vercel.post_deploy.reinit_done",
                            users_seeded=retry["users_seeded"],
                            errors=retry["errors"],
                        )

                    # Smoke-test the login path with demo credentials.
                    try:
                        login_resp = requests.post(
                            f"{final}/api/auth/login",
                            json={"email": "demo@example.com", "password": "demo1234"},
                            timeout=15,
                        )
                        log.info(
                            "vercel.post_deploy.login_smoke",
                            status=login_resp.status_code,
                            ok=login_resp.status_code == 200,
                        )
                    except Exception as le:
                        log.warning("vercel.post_deploy.login_smoke_failed", error=str(le))
        except Exception as e:
            log.warning("vercel.post_deploy.health_skipped", error=str(e))

    return {
        "url": final,
        "deployment_id": did,
        "project_name": slug,
        "state": state,
    }


def fetch_vercel_runtime_logs(
    token: str, deployment_id: str, limit: int = 20,
) -> list[dict]:
    """Pull recent runtime log entries for a deployment.
    Returns list of {ts, type, text} dicts (stdout/stderr/error only).
    """
    try:
        r = requests.get(
            f"{VERCEL_API}/v3/deployments/{deployment_id}/events",
            headers={"Authorization": f"Bearer {token}"},
            params={"limit": limit, "follow": "0"},
            timeout=REQUEST_TIMEOUT,
        )
        if r.status_code != 200:
            return []
        events = r.json()
        if not isinstance(events, list):
            events = events.get("events", [])
        return [
            {
                "ts": e.get("created"),
                "type": e.get("type"),
                "text": (e.get("payload") or {}).get("text", "")[:1000],
            }
            for e in events
            if e.get("type") in ("stdout", "stderr", "error")
        ]
    except Exception:
        return []


def post_deploy_smoke(
    base_url: str,
    contract_paths: list[dict] | None = None,
) -> dict:
    """Probe the deployed app for runtime errors.

    Returns:
      {
        "healthy": bool,
        "endpoint_errors": [{"method", "path", "status", "body_preview"}],
      }
    """
    results: dict = {"healthy": True, "endpoint_errors": []}

    probes = [
        ("GET", "/health"),
        ("GET", "/api/_health/db"),
        ("GET", "/openapi.json"),
    ]
    if contract_paths:
        for p in (contract_paths or [])[:15]:
            probes.append((p["method"], p["path"]))

    for method, path in probes:
        probe_path = re.sub(r"\{[^}]+\}", "1", path)
        url = f"{base_url}{probe_path}"
        try:
            if method == "GET":
                r = requests.get(url, timeout=15)
            else:
                r = requests.request(method, url, json={}, timeout=15)
            if r.status_code >= 500:
                results["healthy"] = False
                results["endpoint_errors"].append({
                    "method": method,
                    "path": path,
                    "status": r.status_code,
                    "body_preview": r.text[:500],
                })
        except Exception as e:
            results["healthy"] = False
            results["endpoint_errors"].append({
                "method": method,
                "path": path,
                "status": 0,
                "body_preview": f"exception: {e}",
            })
    return results
