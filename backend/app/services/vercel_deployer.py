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


def _slugify(name: str, project_id: str | None = None, force_suffix: bool = False) -> str:
    """Vercel project name rules: lowercase, alphanumeric, hyphens, max 63 chars.

    Without force_suffix: returns a clean slug with no id suffix (preferred UX).
    With force_suffix + project_id: appends a 6-char id prefix so projects with
    the same display name don't collide on Vercel.
    """
    slug = re.sub(r"[^a-z0-9-]+", "-", name.lower()).strip("-")
    slug = slug or "demaestro-app"

    if force_suffix and project_id:
        suffix = re.sub(r"[^a-z0-9]", "", project_id.lower())[:6]
        if suffix:
            # Reserve room for "-" + suffix inside the 63-char limit.
            slug = slug[: 63 - len(suffix) - 1] + "-" + suffix

    return slug[:63]


def _ensure_project(token: str, slug: str) -> None:
    """Create the Vercel project if it doesn't exist.

    Raises VercelDeployError("409: ...") on name collision so the caller can
    retry with a suffixed slug.  Raises for all other non-2xx responses too.
    """
    r = requests.post(
        f"{VERCEL_API}/v9/projects",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": slug, "framework": None},
        timeout=REQUEST_TIMEOUT,
    )
    if r.status_code in (200, 201):
        log.info("vercel.project.created", slug=slug)
    elif r.status_code == 409:
        raise VercelDeployError(f"409: project name {slug!r} already exists")
    else:
        raise VercelDeployError(
            f"create_project {r.status_code}: {r.text[:200]}"
        )


def _set_env(token: str, slug: str, key: str, value: str) -> None:
    """Upsert an env var on the Vercel project (production + preview targets).

    Vercel's CREATE endpoint returns ENV_ALREADY_EXISTS for any conflict.
    Empirically observed status codes for that case: 400 (older API) and
    403 (current API). When we see that, we list the project's env vars,
    look up the matching id, and PATCH the value instead.
    """
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

    # Detect the "already exists" case across both code paths Vercel uses.
    body_lc = r.text.lower()
    is_already_exists = (
        r.status_code in (400, 403)
        and (
            "env_already_exists" in body_lc
            or "already exists" in body_lc
        )
    )

    if is_already_exists:
        # Look up the existing env var id(s) and PATCH each (the var may
        # exist in multiple scopes; PATCH the first match — its value is
        # shared across targets when the env was created with multi-target).
        ls = requests.get(
            f"{VERCEL_API}/v9/projects/{slug}/env",
            headers={"Authorization": f"Bearer {token}"},
            timeout=REQUEST_TIMEOUT,
        )
        if ls.status_code != 200:
            raise VercelDeployError(f"list_env {ls.status_code}: {ls.text[:200]}")
        envs = ls.json().get("envs", [])
        matches = [e for e in envs if e.get("key") == key]
        if not matches:
            # Sometimes the env API filters by user scope; treat as benign.
            log.warning("vercel.set_env.exists_but_no_match", key=key)
            return
        for m in matches:
            patch = requests.patch(
                f"{VERCEL_API}/v9/projects/{slug}/env/{m['id']}",
                headers={"Authorization": f"Bearer {token}"},
                json={"value": value},
                timeout=REQUEST_TIMEOUT,
            )
            if patch.status_code not in (200, 201):
                # Per-match patch failure is non-fatal — log and continue.
                log.warning(
                    "vercel.set_env.patch_failed",
                    key=key,
                    env_id=m.get("id"),
                    status=patch.status_code,
                    body=patch.text[:200],
                )
        log.info("vercel.set_env.updated", key=key, matches=len(matches))
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


def _per_project_database_url(project_slug: str) -> tuple[str, str]:
    """Return (per_project_db_url, db_name).

    Side effect: ensures the per-project database exists on Neon by
    issuing CREATE DATABASE against the admin URL. Idempotent — uses
    IF NOT EXISTS where the role allows, falls back to catching the
    'database already exists' error otherwise.
    """
    import re as _re
    from urllib.parse import urlparse, urlunparse
    import psycopg2

    admin_url = os.environ.get("DEMAESTRO_POSTGRES_URL", "").strip()
    if not admin_url:
        raise RuntimeError("DEMAESTRO_POSTGRES_URL not set")

    # Sanitise the slug into a valid Postgres database identifier.
    safe_slug = _re.sub(r"[^a-z0-9_]", "_", project_slug.lower())[:48]
    if not safe_slug:
        safe_slug = "app"
    db_name = f"app_{safe_slug}"

    parsed = urlparse(admin_url)
    # path is "/<dbname>"; replace it with the per-project name
    new_path = "/" + db_name
    per_project_url = urlunparse(parsed._replace(path=new_path))

    # Create the database if it does not exist. Postgres CREATE DATABASE
    # cannot run inside a transaction, so use autocommit.
    try:
        conn = psycopg2.connect(admin_url)
        conn.autocommit = True
        try:
            with conn.cursor() as cur:
                cur.execute(f'CREATE DATABASE "{db_name}"')
            log.info("vercel.neon.per_project_db.created", db=db_name)
        except psycopg2.errors.DuplicateDatabase:
            log.info("vercel.neon.per_project_db.exists", db=db_name)
        except Exception as _create_exc:
            # Some Neon plans throw a generic error on duplicate; treat
            # "already exists" substring as benign.
            msg = str(_create_exc).lower()
            if "already exists" in msg:
                log.info("vercel.neon.per_project_db.exists_caught", db=db_name)
            else:
                raise
        finally:
            conn.close()
    except Exception as _conn_exc:
        log.error(
            "vercel.neon.per_project_db.failed",
            error=str(_conn_exc)[:200],
            db=db_name,
        )
        raise

    return per_project_url, db_name


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


def _apply_pre_deploy_normalizers(files: dict) -> dict:
    """Apply algorithmic fixups to the files dict before every deploy.

    The debugger only runs during test cycles. Redeploys (and resumed
    projects in `ready` state) skip the debugger entirely, so any new
    auto-fixer added to debugger.py would never reach already-generated
    apps. This wrapper re-runs the SAFE, algorithmic-only debugger
    helpers on every deploy so existing projects benefit from new fixes
    just by clicking Redeploy.

    LLM-based debug passes (_agentic_holistic_fix, _fix_with_claude) are
    deliberately NOT invoked here — those are only safe inside the test
    pipeline where every change is validated by re-running tests.
    """
    out = dict(files)

    # ── 1. bcrypt pinning (Vercel runtime bug fix) ────────────────────────
    for req_path in ("api/requirements.txt", "backend/requirements.txt"):
        content = out.get(req_path)
        if content is None:
            continue
        lines = content.splitlines()
        has_passlib = any(l.strip().startswith("passlib") for l in lines)
        if not has_passlib:
            continue  # not an auth project

        has_bcrypt = any(l.strip().startswith("bcrypt") for l in lines)
        new_lines: list[str] = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("passlib") and "==1.7.4" not in stripped:
                line = "passlib[bcrypt]==1.7.4"
            if stripped.startswith("bcrypt") and "==4.0.1" not in stripped:
                line = "bcrypt==4.0.1"
            new_lines.append(line)

        if not has_bcrypt:
            inserted = False
            tmp: list[str] = []
            for line in new_lines:
                tmp.append(line)
                if not inserted and line.strip().startswith("passlib"):
                    tmp.append("bcrypt==4.0.1")
                    inserted = True
            new_lines = tmp
            if not inserted:
                new_lines.append("bcrypt==4.0.1")

        new_content = "\n".join(new_lines)
        if not new_content.endswith("\n"):
            new_content += "\n"
        if new_content != content:
            out[req_path] = new_content
            log.info("vercel.pre_deploy.bcrypt_pinned", file=req_path)

    # ── 2. Reuse selected debugger helpers (algorithmic-only) ─────────────
    # These are safe to run on already-generated code: each is idempotent
    # and only mutates files when a specific known bug pattern is present.
    try:
        from app.ai.claude.agents.debugger import (
            _fix_missing_post_auth_navigate,
            _strip_notfound_from_navbar,
            _normalize_accent_css_var,
            _normalize_user_fk_types,
        )
    except Exception as _imp_exc:
        log.warning("vercel.pre_deploy.helpers_import_failed", error=str(_imp_exc)[:200])
        return out

    helpers = (
        ("post_auth_navigate", _fix_missing_post_auth_navigate),
        ("strip_notfound_navbar", _strip_notfound_from_navbar),
        ("normalize_accent_css_var", _normalize_accent_css_var),
        ("normalize_user_fk_types", _normalize_user_fk_types),
    )
    for name, fn in helpers:
        try:
            patches = fn({}, out) or {}
        except Exception as _h_exc:
            log.warning(
                "vercel.pre_deploy.helper_failed",
                helper=name,
                error=str(_h_exc)[:200],
            )
            continue
        if patches:
            out.update(patches)
            log.info(
                "vercel.pre_deploy.helper_applied",
                helper=name,
                files=list(patches.keys()),
            )

    return out


def deploy(
    project_name: str,
    files: dict,
    wait_for_ready: bool = True,
    stable_id: str | None = None,
) -> dict:
    """Deploy files to Vercel. Returns {url, deployment_id, project_name, display_name, state}."""
    token = os.environ.get("DEMAESTRO_VERCEL_TOKEN")
    if not token or not os.environ.get("DEMAESTRO_POSTGRES_URL"):
        raise VercelDeployError(
            "DEMAESTRO_VERCEL_TOKEN or DEMAESTRO_POSTGRES_URL not set"
        )

    import secrets as _secrets

    # Try a clean slug (e.g. "sitnbite") — only fall back to the id-suffixed form
    # (e.g. "sitnbite-ab12cd") when a name collision is detected (HTTP 409).
    slug = _slugify(project_name)
    log.info("vercel.deploy.start", project=slug, display_name=project_name, files=len(files))

    # Apply Vercel-critical fixes (bcrypt pin, etc.) BEFORE pushing files.
    # Redeploy paths skip the debugger, so old projects need this safety net.
    files = _apply_pre_deploy_normalizers(files)

    try:
        _ensure_project(token, slug)
        log.info("vercel.slug.clean", slug=slug)
    except VercelDeployError as _exc:
        if "409" in str(_exc):
            # Name collision — another DeMaestro project owns that clean slug.
            # Retry with the stable-id suffix so this project gets its own name.
            slug = _slugify(project_name, project_id=stable_id, force_suffix=True)
            log.info("vercel.slug.collision_retry", slug=slug)
            try:
                _ensure_project(token, slug)
            except VercelDeployError as _exc2:
                if "409" in str(_exc2):
                    # 409 on suffixed slug = our own existing project; proceed.
                    log.info("vercel.project.exists", slug=slug)
                else:
                    raise
        else:
            raise

    # Per-project DB isolation: each Vercel deploy gets its own Neon database.
    per_project_db_url, per_project_db_name = _per_project_database_url(slug)
    log.info("vercel.deploy.using_per_project_db", db=per_project_db_name)

    disable_deployment_protection(token, slug)

    # auth.py reads JWT_SECRET (NOT JWT_SECRET_KEY). Set both names so
    # the deployed app picks up the real secret regardless of which env
    # var the scaffold reads. Falling back to the hardcoded dev secret
    # in production is a security issue we want to make impossible.
    jwt_secret_value = _secrets.token_hex(32)
    env_vars = {
        "DATABASE_URL": per_project_db_url,
        "JWT_SECRET": jwt_secret_value,
        "JWT_SECRET_KEY": jwt_secret_value,
        "CORS_ORIGINS": "*",
    }
    for k, v in env_vars.items():
        _set_env(token, slug, k, v)

    # Initialize Neon DB BEFORE deploying so tables + seed exist when the
    # first Vercel request arrives (no cold-start race).
    log.info("vercel.neon_init.start", project=slug)
    neon_result = initialize_neon_db(files, per_project_db_url)
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
    log.info(
        "vercel.deploy.isolated_db",
        project=project_name,
        db=per_project_db_name,
        url=final,
    )

    live_smoke: dict = {"ok": True, "failures": []}
    if wait_for_ready and final:
        # Full authed-flow smoke — register, login, probe every authed GET.
        try:
            live_smoke = _live_authed_smoke(final)
            if live_smoke["ok"]:
                log.info("vercel.post_deploy.live_smoke_ok")
            else:
                log.error(
                    "vercel.post_deploy.live_smoke_failed",
                    reason=live_smoke["reason"],
                    num_failures=len(live_smoke["failures"]),
                    sample=live_smoke["failures"][:3],
                )
        except Exception as _smoke_exc:
            log.warning("vercel.post_deploy.live_smoke_exception", error=str(_smoke_exc)[:200])

    if wait_for_ready and final:
        # Probe the deployed app: wake the function, verify DB, retry seed if needed.
        try:
            requests.get(f"{final}/_health/db", timeout=20)
            time.sleep(3)
            check = requests.get(f"{final}/_health/db", timeout=20)
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
                        retry = initialize_neon_db(files, per_project_db_url)
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
        "display_name": project_name,
        "state": state,
        # Patched files (after _apply_pre_deploy_normalizers ran).
        # The caller should persist these back to Firestore so the
        # ZIP download and file explorer reflect the same code that
        # was actually deployed.
        "patched_files": files,
        "live_smoke": live_smoke,
    }


def _live_authed_smoke(base_url: str) -> dict:
    """Walk the deployed app end-to-end: register, login, then probe
    every auth-required GET endpoint with the JWT.  Returns:
        {"ok": True, "failures": []}                   on success
        {"ok": False, "failures": [...], "reason": "..."} on failure
    """
    import secrets as _secrets
    import json as _json

    email = f"probe_{_secrets.token_hex(4)}@example.com"
    pwd = "ProbePass!2024"
    try:
        reg = requests.post(
            f"{base_url}/api/auth/register",
            json={"email": email, "password": pwd, "name": "Probe"},
            timeout=15,
        )
        if reg.status_code not in (200, 201):
            return {"ok": False, "reason": f"register {reg.status_code}: {reg.text[:200]}", "failures": []}
        try:
            token = reg.json().get("access_token")
        except Exception:
            token = None
        if not token:
            lg = requests.post(
                f"{base_url}/api/auth/login",
                json={"email": email, "password": pwd},
                timeout=15,
            )
            if lg.status_code != 200:
                return {"ok": False, "reason": f"login {lg.status_code}: {lg.text[:200]}", "failures": []}
            try:
                token = lg.json().get("access_token")
            except Exception:
                token = None
        if not token:
            return {"ok": False, "reason": "no JWT from register or login", "failures": []}

        ops = requests.get(f"{base_url}/openapi.json", timeout=15)
        if ops.status_code != 200:
            return {"ok": False, "reason": f"openapi {ops.status_code}", "failures": []}
        try:
            schema = ops.json()
        except Exception as je:
            return {"ok": False, "reason": f"openapi parse: {je}", "failures": []}

        failures: list[dict] = []
        for path, ops_obj in (schema.get("paths") or {}).items():
            for method_lc, op in (ops_obj or {}).items():
                if method_lc.upper() != "GET":
                    continue
                probe_path = re.sub(r"\{[^}]+\}", "1", path)
                url = f"{base_url}{probe_path}"
                try:
                    r = requests.get(
                        url,
                        headers={"Authorization": f"Bearer {token}"},
                        timeout=15,
                    )
                except Exception as exc:
                    failures.append({"path": path, "status": 0, "body": str(exc)[:300]})
                    continue
                if r.status_code >= 500:
                    failures.append({"path": path, "status": r.status_code, "body": r.text[:400]})

        if failures:
            return {"ok": False, "reason": f"{len(failures)} endpoint(s) returned 500", "failures": failures}
        return {"ok": True, "failures": []}
    except Exception as exc:
        return {"ok": False, "reason": f"smoke exception: {exc}", "failures": []}


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
        ("GET", "/_health/db"),
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
