from __future__ import annotations

import hmac
import math
import re
import shutil
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import __version__
from .ai import EvaluationFailed, EvaluationUnavailable, evaluate_resume
from .auth import csrf_matches, new_csrf_token, verify_password
from .config import Settings, load_settings
from .database import APPLICATION_STATUSES, Database, utc_now
from .uploads import UploadValidationError, resolve_stored_resume, store_resume


ROOT = Path(__file__).resolve().parent
TEMPLATES = Jinja2Templates(directory=str(ROOT / "templates"))
EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")

ROLES = {
    "Campus Cafeteria Data Collector": {
        "number": "01",
        "type": "Part-time / Contract",
        "compensation": "$18-$28 / hour",
        "location": "On campus",
        "tasks": [
            "Visit assigned cafeterias and collect or verify menus, prices, hours, calories, and photos.",
            "Enter clean data into internal tools or shared spreadsheets.",
            "Flag missing, outdated, or inconsistent information.",
            "Follow ethical collection and review rules - no fake reviews or discount bias.",
        ],
        "skills": (
            "Detail, reliability, basic spreadsheet or mobile data-entry skills, campus travel, "
            "and deadline discipline. Bonus: field research, Airtable, Notion, SQL, OCR, "
            "nutrition knowledge, or photography."
        ),
        "accent": "orange",
    },
    "AI Search & Recommendation Engineer": {
        "number": "02",
        "type": "Full-time",
        "compensation": "$100k-$160k + equity",
        "location": "Remote / Hybrid",
        "tasks": [
            "Build intelligent search and personalized meal recommendations.",
            "Work on keyword, semantic, vector, and hybrid search, ranking, and LLM/RAG.",
            "Build data pipelines and evaluate models with offline metrics and A/B tests.",
            "Detect spam, fake, or discount-biased reviews.",
        ],
        "skills": (
            "2+ years in search, recommendation, or ML engineering; Python, SQL, "
            "PyTorch/TensorFlow/scikit-learn; vector databases; recommender algorithms; "
            "embeddings, APIs, and product sense."
        ),
        "accent": "green",
    },
}

STATUS_LABELS = {
    "new": "New",
    "reviewing": "Reviewing",
    "interview": "Interview",
    "offer": "Offer",
    "rejected": "Rejected",
    "archived": "Archived",
}


class FixedWindowLimiter:
    def __init__(self, limit: int, window_seconds: int):
        self.limit = limit
        self.window_seconds = window_seconds
        self._events: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        cutoff = now - self.window_seconds
        with self._lock:
            recent = [event for event in self._events.get(key, []) if event >= cutoff]
            if len(recent) >= self.limit:
                self._events[key] = recent
                return False
            recent.append(now)
            self._events[key] = recent
            return True


def _client_key(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _csrf_token(request: Request) -> str:
    token = request.session.get("csrf")
    if not token:
        token = new_csrf_token()
        request.session["csrf"] = token
    return str(token)


def _is_admin(request: Request, settings: Settings) -> bool:
    return request.session.get("admin_user") == settings.admin.username


def _set_flash(request: Request, kind: str, message: str) -> None:
    request.session["flash"] = {"kind": kind, "message": message}


def _pop_flash(request: Request) -> dict[str, str] | None:
    value = request.session.pop("flash", None)
    return value if isinstance(value, dict) else None


def _human_bytes(value: int) -> str:
    if value < 1024:
        return f"{value} B"
    if value < 1024 * 1024:
        return f"{value / 1024:.1f} KB"
    return f"{value / (1024 * 1024):.1f} MB"


def _display_time(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value)
        return parsed.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    except (TypeError, ValueError):
        return value


TEMPLATES.env.filters["human_bytes"] = _human_bytes
TEMPLATES.env.filters["display_time"] = _display_time


def _public_context(
    request: Request,
    settings: Settings,
    *,
    form: dict[str, str] | None = None,
    errors: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "request": request,
        "settings": settings,
        "roles": ROLES,
        "csrf_token": _csrf_token(request),
        "form": form or {},
        "errors": errors or [],
    }


def _admin_context(request: Request, settings: Settings, **extra: Any) -> dict[str, Any]:
    return {
        "request": request,
        "settings": settings,
        "csrf_token": _csrf_token(request),
        "admin_user": settings.admin.username,
        "flash": _pop_flash(request),
        "status_labels": STATUS_LABELS,
        "statuses": APPLICATION_STATUSES,
        "roles": ROLES,
        **extra,
    }


def _error_page(request: Request, status_code: int, title: str, message: str) -> HTMLResponse:
    return TEMPLATES.TemplateResponse(
        request=request,
        name="errors/error.html",
        context={"title": title, "message": message, "status_code": status_code},
        status_code=status_code,
    )


def create_app(config_path: str | Path | None = None) -> FastAPI:
    settings = load_settings(config_path)
    settings.storage.resume_dir.mkdir(parents=True, exist_ok=True)
    database = Database(settings.storage.database_path)
    database.initialize()

    app = FastAPI(
        title=settings.app.name,
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.settings = settings
    app.state.database = database
    app.state.application_limiter = FixedWindowLimiter(
        settings.security.application_limit_per_hour, 60 * 60
    )
    app.state.login_limiter = FixedWindowLimiter(
        settings.security.login_limit_per_15_minutes, 15 * 60
    )

    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.app.session_secret,
        session_cookie="bite_hunt_session",
        max_age=8 * 60 * 60,
        same_site="lax",
        https_only=settings.app.cookie_secure,
    )
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(settings.app.trusted_hosts))
    app.mount("/static", StaticFiles(directory=str(ROOT / "static")), name="static")

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault(
            "Permissions-Policy", "camera=(), microphone=(), geolocation=()"
        )
        if request.url.path.startswith("/admin"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException):
        if request.url.path == "/health":
            return JSONResponse({"status": "error"}, status_code=exc.status_code)
        if exc.status_code == 404:
            return _error_page(request, 404, "Page not found", "The page you requested does not exist.")
        if exc.status_code == 413:
            return _error_page(request, 413, "File too large", "The uploaded file exceeds the allowed size.")
        return _error_page(request, exc.status_code, "Request error", str(exc.detail))

    @app.get("/", response_class=HTMLResponse)
    async def home(request: Request):
        return TEMPLATES.TemplateResponse(
            request=request,
            name="public/index.html",
            context=_public_context(request, settings),
        )

    @app.post("/apply", response_class=HTMLResponse)
    async def apply(
        request: Request,
        full_name: str = Form(...),
        email: str = Form(...),
        phone: str = Form(""),
        position: str = Form(...),
        cover_note: str = Form(""),
        consent: str = Form(""),
        csrf_token: str = Form(...),
        website: str = Form(""),
        resume: UploadFile = File(...),
    ):
        form = {
            "full_name": full_name.strip(),
            "email": email.strip(),
            "phone": phone.strip(),
            "position": position.strip(),
            "cover_note": cover_note.strip(),
        }
        errors: list[str] = []
        if not csrf_matches(request.session.get("csrf"), csrf_token):
            await resume.close()
            return _error_page(request, 403, "Form expired", "Please refresh the page and submit again.")
        if website:
            await resume.close()
            return RedirectResponse("/application/success?ref=BH-RECEIVED", status_code=303)
        if not app.state.application_limiter.allow(_client_key(request)):
            await resume.close()
            errors.append("Too many applications were submitted from this address. Please try again later.")
        if not 2 <= len(form["full_name"]) <= 100:
            errors.append("Enter your full name (2-100 characters).")
        if len(form["email"]) > 254 or not EMAIL_PATTERN.fullmatch(form["email"]):
            errors.append("Enter a valid email address.")
        if len(form["phone"]) > 40:
            errors.append("Phone number must be 40 characters or fewer.")
        if form["position"] not in ROLES:
            errors.append("Choose one of the listed roles.")
        if len(form["cover_note"]) > 2_000:
            errors.append("Your note must be 2,000 characters or fewer.")
        if consent != "yes":
            errors.append("Confirm that Bite Hunt may store your application for recruitment.")
        if errors:
            await resume.close()
            return TEMPLATES.TemplateResponse(
                request=request,
                name="public/index.html",
                context=_public_context(request, settings, form=form, errors=errors),
                status_code=422,
            )

        application_id = str(uuid.uuid4())
        reference_code = f"BH-{datetime.now(timezone.utc):%Y%m%d}-{uuid.uuid4().hex[:6].upper()}"
        try:
            stored = await store_resume(
                resume,
                application_id=application_id,
                resume_root=settings.storage.resume_dir,
                allowed_extensions=settings.storage.allowed_extensions,
                max_bytes=settings.storage.max_upload_mb * 1024 * 1024,
            )
        except UploadValidationError as exc:
            return TEMPLATES.TemplateResponse(
                request=request,
                name="public/index.html",
                context=_public_context(request, settings, form=form, errors=[str(exc)]),
                status_code=422,
            )

        timestamp = utc_now()
        try:
            database.create_application(
                {
                    "id": application_id,
                    "reference_code": reference_code,
                    "full_name": form["full_name"],
                    "email": form["email"].lower(),
                    "phone": form["phone"],
                    "position": form["position"],
                    "cover_note": form["cover_note"],
                    "consent_given": 1,
                    "resume_original_name": stored.original_name,
                    "resume_stored_path": stored.relative_path,
                    "resume_media_type": stored.media_type,
                    "resume_size": stored.size,
                    "resume_sha256": stored.sha256,
                    "created_at": timestamp,
                    "updated_at": timestamp,
                }
            )
        except Exception:
            shutil.rmtree(stored.absolute_path.parent, ignore_errors=True)
            raise
        return RedirectResponse(f"/application/success?ref={reference_code}", status_code=303)

    @app.get("/application/success", response_class=HTMLResponse)
    async def application_success(request: Request, ref: str = ""):
        reference = ref if re.fullmatch(r"BH-[A-Z0-9-]{6,30}", ref) else "BH-RECEIVED"
        return TEMPLATES.TemplateResponse(
            request=request,
            name="public/success.html",
            context={"request": request, "reference": reference},
        )

    @app.get("/admin/login", response_class=HTMLResponse)
    async def admin_login(request: Request):
        if _is_admin(request, settings):
            return RedirectResponse("/admin", status_code=303)
        return TEMPLATES.TemplateResponse(
            request=request,
            name="admin/login.html",
            context={
                "request": request,
                "csrf_token": _csrf_token(request),
                "error": "",
            },
        )

    @app.post("/admin/login", response_class=HTMLResponse)
    async def admin_login_post(
        request: Request,
        username: str = Form(...),
        password: str = Form(...),
        csrf_token: str = Form(...),
    ):
        if not csrf_matches(request.session.get("csrf"), csrf_token):
            return _error_page(request, 403, "Form expired", "Please refresh the page and try again.")
        error = "The username or password is incorrect."
        attempt_allowed = app.state.login_limiter.allow(_client_key(request))
        if not attempt_allowed:
            error = "Too many login attempts. Please wait 15 minutes and try again."
        else:
            password_ok = verify_password(password, settings.admin.password_hash)
            if hmac.compare_digest(username, settings.admin.username) and password_ok:
                request.session.clear()
                request.session["admin_user"] = settings.admin.username
                request.session["csrf"] = new_csrf_token()
                return RedirectResponse("/admin", status_code=303)
        return TEMPLATES.TemplateResponse(
            request=request,
            name="admin/login.html",
            context={"request": request, "csrf_token": _csrf_token(request), "error": error},
            status_code=401,
        )

    @app.post("/admin/logout")
    async def admin_logout(request: Request, csrf_token: str = Form(...)):
        if not csrf_matches(request.session.get("csrf"), csrf_token):
            return _error_page(request, 403, "Form expired", "Please refresh the page and try again.")
        request.session.clear()
        return RedirectResponse("/admin/login", status_code=303)

    @app.get("/admin", response_class=HTMLResponse)
    async def admin_dashboard(
        request: Request,
        q: str = "",
        status: str = "",
        position: str = "",
        page: int = 1,
    ):
        if not _is_admin(request, settings):
            return RedirectResponse("/admin/login", status_code=303)
        q = q.strip()[:200]
        status = status if status in APPLICATION_STATUSES else ""
        position = position if position in ROLES else ""
        page = max(1, page)
        applications, total = database.list_applications(
            query=q, status=status, position=position, page=page, page_size=20
        )
        page_count = max(1, math.ceil(total / 20))
        if page > page_count:
            page = page_count
            applications, total = database.list_applications(
                query=q, status=status, position=position, page=page, page_size=20
            )
        return TEMPLATES.TemplateResponse(
            request=request,
            name="admin/dashboard.html",
            context=_admin_context(
                request,
                settings,
                applications=applications,
                counts=database.status_counts(),
                q=q,
                selected_status=status,
                selected_position=position,
                page=page,
                page_count=page_count,
                total=total,
            ),
        )

    @app.get("/admin/applications/{application_id}", response_class=HTMLResponse)
    async def admin_application(request: Request, application_id: str):
        if not _is_admin(request, settings):
            return RedirectResponse("/admin/login", status_code=303)
        application = database.get_application(application_id)
        if not application:
            return _error_page(request, 404, "Application not found", "This application does not exist.")
        evaluations = database.list_ai_evaluations(application_id)
        return TEMPLATES.TemplateResponse(
            request=request,
            name="admin/detail.html",
            context=_admin_context(
                request,
                settings,
                application=application,
                evaluations=evaluations,
                ai_enabled=settings.ai.enabled,
            ),
        )

    @app.post("/admin/applications/{application_id}/update")
    async def admin_application_update(
        request: Request,
        application_id: str,
        status: str = Form(...),
        admin_notes: str = Form(""),
        csrf_token: str = Form(...),
    ):
        if not _is_admin(request, settings):
            return RedirectResponse("/admin/login", status_code=303)
        if not csrf_matches(request.session.get("csrf"), csrf_token):
            return _error_page(request, 403, "Form expired", "Please refresh the page and try again.")
        if status not in APPLICATION_STATUSES:
            return _error_page(request, 400, "Unknown status", "Choose a valid application status.")
        if len(admin_notes) > 10_000:
            return _error_page(request, 400, "Notes too long", "Notes must be 10,000 characters or fewer.")
        if not database.update_application(application_id, status, admin_notes.strip()):
            return _error_page(request, 404, "Application not found", "This application does not exist.")
        _set_flash(request, "success", "Application updated.")
        return RedirectResponse(f"/admin/applications/{application_id}", status_code=303)

    @app.get("/admin/applications/{application_id}/resume")
    async def admin_resume(request: Request, application_id: str):
        if not _is_admin(request, settings):
            return RedirectResponse("/admin/login", status_code=303)
        application = database.get_application(application_id)
        if not application:
            return _error_page(request, 404, "Application not found", "This application does not exist.")
        try:
            path = resolve_stored_resume(
                settings.storage.resume_dir, application["resume_stored_path"]
            )
        except FileNotFoundError:
            return _error_page(request, 404, "Resume missing", "The stored resume file could not be found.")
        return FileResponse(
            path,
            media_type=application["resume_media_type"],
            filename=application["resume_original_name"],
            headers={"Cache-Control": "no-store"},
        )

    @app.post("/admin/applications/{application_id}/evaluate")
    async def admin_evaluate(
        request: Request,
        application_id: str,
        csrf_token: str = Form(...),
    ):
        if not _is_admin(request, settings):
            return RedirectResponse("/admin/login", status_code=303)
        if not csrf_matches(request.session.get("csrf"), csrf_token):
            return _error_page(request, 403, "Form expired", "Please refresh the page and try again.")
        application = database.get_application(application_id)
        if not application:
            return _error_page(request, 404, "Application not found", "This application does not exist.")
        try:
            path = resolve_stored_resume(
                settings.storage.resume_dir, application["resume_stored_path"]
            )
            result = await evaluate_resume(settings.ai, application, path)
            database.add_ai_evaluation(
                application_id, settings.ai.provider, settings.ai.model, result
            )
            _set_flash(request, "success", "AI evaluation completed. Review it as decision support only.")
        except EvaluationUnavailable as exc:
            _set_flash(request, "warning", str(exc))
        except EvaluationFailed as exc:
            _set_flash(request, "error", str(exc))
        except FileNotFoundError:
            _set_flash(request, "error", "The stored resume file could not be found.")
        return RedirectResponse(f"/admin/applications/{application_id}", status_code=303)

    @app.get("/health")
    async def health():
        try:
            with database.connect() as connection:
                connection.execute("SELECT 1").fetchone()
        except Exception:
            return JSONResponse({"status": "unhealthy"}, status_code=503)
        return {"status": "ok", "version": __version__}

    @app.get("/robots.txt", response_class=PlainTextResponse)
    async def robots():
        return "User-agent: *\nDisallow: /admin\n"

    return app
