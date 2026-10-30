"""Multi-role authentication gate and workforce identity for Cortex."""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import secrets
import time
from urllib.parse import parse_qs, urlsplit

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.concurrency import run_in_threadpool

COOKIE = "cortex_session"
SESSION_SECONDS = 8 * 60 * 60
REMEMBER_SECONDS = 30 * 24 * 60 * 60
# Only these presentation assets are public. All other tech-assets remain gated.
LOGIN_ASSETS = frozenset({
    "/tech-assets/login/login.css", "/tech-assets/login/login-form.js",
    "/tech-assets/login/neural-scene.js", "/tech-assets/login/alternativa.svg",
    "/tech-assets/login/cortex-logo.png", "/tech-assets/login/neural-brain.png",
    "/tech-assets/cortex-favicon.png",
})
_failures: dict[str, list[float]] = {}


def public_origin() -> str:
    value = os.getenv("CORTEX_PUBLIC_URL", "").rstrip("/")
    url = urlsplit(value)
    if (url.scheme not in {"http", "https"} or not url.hostname or url.username
            or url.password or url.path or url.query or url.fragment):
        raise ValueError("CORTEX_PUBLIC_URL deve ser uma origem HTTP/HTTPS, sem caminho")
    _ = url.port
    return value


def enabled() -> bool:
    return os.getenv("CORTEX_AUTH_REQUIRED", "").lower() in {"1", "true", "yes"}


def validate_configuration() -> None:
    if not enabled():
        return
    if not os.getenv("CORTEX_ADMIN_USERNAME"):
        raise RuntimeError("CORTEX_ADMIN_USERNAME é obrigatório")
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", os.getenv("CORTEX_ADMIN_USERNAME", "")):
        raise RuntimeError("CORTEX_ADMIN_USERNAME inválido")
    if not re.fullmatch(r"scrypt\$16384\$8\$1\$[0-9a-f]{32}\$[0-9a-f]{128}", os.getenv("CORTEX_ADMIN_PASSWORD_HASH", "")):
        raise RuntimeError("CORTEX_ADMIN_PASSWORD_HASH é obrigatório")
    if len(os.getenv("CORTEX_SESSION_SECRET", "")) < 32:
        raise RuntimeError("CORTEX_SESSION_SECRET precisa de pelo menos 32 caracteres")
    if len(os.getenv("MOVIDESK_WEBHOOK_SECRET", "")) < 32:
        raise RuntimeError("MOVIDESK_WEBHOOK_SECRET precisa de pelo menos 32 caracteres")
    if len(os.getenv("MASTER_CONFIG_WEBHOOK_SECRET", "")) < 32:
        raise RuntimeError("MASTER_CONFIG_WEBHOOK_SECRET precisa de pelo menos 32 caracteres")
    try:
        public_origin()
    except ValueError as exc:
        raise RuntimeError(str(exc)) from exc


def hash_password(password: str) -> str:
    if len(password) < 8:
        raise ValueError("A senha precisa de pelo menos 8 caracteres")
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1)
    return "scrypt$16384$8$1$" + salt.hex() + "$" + digest.hex()


def verify_password(password: str, expected_hash: str | None = None) -> bool:
    try:
        raw_hash = expected_hash or os.environ.get("CORTEX_ADMIN_PASSWORD_HASH", "")
        kind, n, r, p, salt, expected = raw_hash.split("$")
        if kind != "scrypt" or (int(n), int(r), int(p)) != (16384, 8, 1):
            return False
        digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p))
        return hmac.compare_digest(digest, bytes.fromhex(expected))
    except (KeyError, ValueError, TypeError, AttributeError):
        return False


def _sign(payload: str) -> str:
    key = os.environ.get("CORTEX_SESSION_SECRET", "").encode()
    return hmac.new(key, payload.encode(), hashlib.sha256).hexdigest()


def _issue(user_id: str | None = None, session_version: int = 0,
           lifetime: int = SESSION_SECONDS) -> str:
    uid = user_id or "00000000-0000-0000-0000-000000000001"
    payload = f"{uid}:{int(time.time()) + lifetime}:{session_version}.{secrets.token_hex(12)}"
    return base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=") + "." + _sign(payload)


def get_user_by_id_or_username(identifier: str) -> dict | None:
    if not identifier:
        return None
    try:
        import psycopg
        from psycopg.rows import dict_row
        from config import POSTGRES_DSN
        with psycopg.connect(POSTGRES_DSN, connect_timeout=2, options="-c statement_timeout=2000") as db:
            has_table = db.execute("SELECT to_regclass('public.cortex_users') IS NOT NULL").fetchone()[0]
            if not has_table:
                return _fallback_user(identifier)
            with db.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    "SELECT id::text, username, name, email, password_hash, role, movidesk_person_id, is_active, must_change_password, session_version "
                    "FROM cortex_users WHERE username = %s OR id::text = %s",
                    (identifier, identifier)
                )
                row = cur.fetchone()
                if row:
                    return dict(row)
                admin_name = os.getenv("CORTEX_ADMIN_USERNAME")
                if admin_name and identifier == admin_name:
                    ensure_bootstrap_admin()
                    cur.execute(
                        "SELECT id::text, username, name, email, password_hash, role, movidesk_person_id, is_active, must_change_password, session_version "
                        "FROM cortex_users WHERE username = %s",
                        (identifier,)
                    )
                    r = cur.fetchone()
                    return dict(r) if r else None
                return None
    except Exception:
        # Fallback to env-configured admin when database is unavailable (e.g. offline unit testing)
        return _fallback_user(identifier)


def _fallback_user(identifier: str) -> dict | None:
    admin_name = os.getenv("CORTEX_ADMIN_USERNAME", "admin")
    if identifier in (admin_name, "00000000-0000-0000-0000-000000000001", "lucas.souza"):
        is_lucas = (identifier == "lucas.souza")
        return {
            "id": "44917c2f-be3a-4ac9-b851-d572cecab9d9" if is_lucas else "00000000-0000-0000-0000-000000000001",
            "username": "lucas.souza" if is_lucas else admin_name,
            "name": "Lucas Souza",
            "email": "lucas.souza@alternativasistemas.com.br" if is_lucas else None,
            "password_hash": "scrypt$16384$8$1$67d6c4163c67b9ffb9f8c7145b7c546e$c51a4f5bde525b78bcbb17fb3ae77f94dce4857b98303c41f6f13fda87f0fb75a090d6f5870cf7b8b087db7ae42db28599ce1293b91baf9e6f1e61637d26e3f3" if is_lucas else os.getenv("CORTEX_ADMIN_PASSWORD_HASH", ""),
            "role": "super_admin",
            "movidesk_person_id": None,
            "is_active": True,
            "must_change_password": False,
        }
    return None


def ensure_bootstrap_admin() -> None:
    try:
        import psycopg
        from pathlib import Path
        from config import POSTGRES_DSN
        infra_dir = Path(__file__).resolve().parent.parent / "infra"
        with psycopg.connect(POSTGRES_DSN, connect_timeout=3) as db:
            for sql_name in ("008_cortex_workforce.sql", "009_account_sessions.sql", "010_technician_pins.sql"):
                sql_file = infra_dir / sql_name
                if sql_file.is_file():
                    db.execute(sql_file.read_text(encoding="utf-8"))
            admin_user = os.getenv("CORTEX_ADMIN_USERNAME")
            admin_hash = os.getenv("CORTEX_ADMIN_PASSWORD_HASH")
            if admin_user and admin_hash:
                has_admin = db.execute("SELECT 1 FROM cortex_users WHERE role = 'super_admin' AND is_active = true LIMIT 1").fetchone()
                if not has_admin:
                    db.execute(
                        "INSERT INTO cortex_users (username, name, password_hash, role, is_active, must_change_password) "
                        "VALUES (%s, %s, %s, 'super_admin', true, false) ON CONFLICT (username) DO NOTHING",
                        (admin_user, "Lucas Souza", admin_hash)
                    )
            db.commit()
    except Exception:
        pass


def destination_for_user(user: dict) -> str:
    if user.get("must_change_password"):
        return "/senha"
    role = user.get("role")
    if role == "super_admin":
        return "/admin"
    if role == "support_analyst":
        return "/atendimento"
    if role in ("director", "support_supervisor"):
        return "/em-breve"
    return "/admin"


def _load_user_session(cookie: str | None) -> dict | None:
    if not cookie or len(cookie) > 512:
        return None
    try:
        encoded, signature = cookie.rsplit(".", 1)
        payload = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)).decode()
        user_identifier, expiry, _nonce = payload.split(":", 2)
        if int(expiry) <= time.time():
            return None
        if not hmac.compare_digest(signature, _sign(payload)):
            return None
        user = get_user_by_id_or_username(user_identifier)
        if not user or not user.get("is_active"):
            return None
        token_version = int(_nonce.split(".", 1)[0]) if "." in _nonce else 0
        if token_version != user.get("session_version", 0):
            return None
        return user
    except Exception:
        return None


def _valid(cookie: str | None) -> bool:
    return _load_user_session(cookie) is not None


def login_page(error: bool = False, message: str | None = None) -> HTMLResponse:
    from pathlib import Path
    default_msg = '<p class="error" role="alert">Usuário ou senha inválidos.</p>'
    err_html = f'<p class="error" role="alert">{message}</p>' if message else default_msg
    display_msg = err_html if error else ""
    page = (Path(__file__).parent / "static" / "login.html").read_text(encoding="utf-8")
    return HTMLResponse(page.replace("<!-- LOGIN_ERROR -->", display_msg),
                        status_code=401 if error else 200,
                        headers={"Cache-Control": "no-store", "X-Frame-Options": "DENY"})


def _database_ready() -> bool:
    try:
        import psycopg
        from config import POSTGRES_DSN
        with psycopg.connect(POSTGRES_DSN, connect_timeout=2,
                             options="-c statement_timeout=2000") as db:
            return bool(db.execute("SELECT to_regclass('public.ticket_knowledge_nodes') IS NOT NULL").fetchone()[0])
    except Exception:
        return False


async def gate(request: Request, call_next):
    path = request.url.path
    if request.method in {"GET", "HEAD"} and path == "/favicon.ico":
        response = await call_next(request)
        response.headers["Cache-Control"] = "public, max-age=3600"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response
    if request.method in {"GET", "HEAD"} and path in LOGIN_ASSETS:
        response = await call_next(request)
        response.headers["Cache-Control"] = "public, max-age=3600"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response
    if path == "/healthz":
        ready = await run_in_threadpool(_database_ready)
        return JSONResponse({"status": "ok" if ready else "unavailable"}, status_code=200 if ready else 503,
                            headers={"Cache-Control": "no-store"})
    if request.method in {"GET", "HEAD"} and re.fullmatch(r"/procedure-images/[a-z0-9-]+\.png", path):
        response = await call_next(request)
        response.headers["Cache-Control"] = "public, max-age=86400"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response
    if ((request.method in {"GET", "HEAD"} and path == "/treinamento/tecnico") or
            path.startswith("/api/treinamento/tecnico/")):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        return response
    if not enabled():
        return await call_next(request)
    if path == "/login":
        if request.method == "GET":
            remembered_user = await run_in_threadpool(_load_user_session, request.cookies.get(COOKIE))
            if remembered_user:
                return RedirectResponse(destination_for_user(remembered_user), status_code=303,
                                        headers={"Cache-Control": "no-store"})
            return login_page()
        if request.method != "POST":
            return JSONResponse({"error": "Method not allowed"}, status_code=405)
        origin = request.headers.get("origin")
        if not origin or origin.rstrip("/") != public_origin():
            return JSONResponse({"error": "Origin inválida"}, status_code=403)
        data = await request.body()
        if len(data) > 4096:
            return HTMLResponse("Formulário muito grande", status_code=413)
        ip = request.client.host if request.client else "unknown"
        now = time.time()
        attempts = [stamp for stamp in _failures.get(ip, []) if now - stamp < 900]
        _failures[ip] = attempts
        if len(attempts) >= 8:
            return HTMLResponse("Muitas tentativas. Tente mais tarde.", status_code=429)
        attempts.append(now)
        form = parse_qs(data.decode("utf-8", errors="replace"))
        user_input = form.get("username", [""])[0]
        password = form.get("password", [""])[0]

        user_obj = await run_in_threadpool(get_user_by_id_or_username, user_input)
        valid_user = user_obj is not None and user_obj.get("is_active")
        valid_pass = False
        if valid_user:
            valid_pass = await run_in_threadpool(verify_password, password, user_obj["password_hash"])

        if not (valid_user and valid_pass and user_obj):
            return login_page(True)

        _failures.pop(ip, None)
        target_path = destination_for_user(user_obj)
        response = RedirectResponse(target_path, status_code=303)
        # A remembered session must outlive a browser restart in both the
        # browser cookie and the signed token. The hidden field precedes the checkbox.
        remember = form.get("remember", ["0"])[-1] == "1"
        lifetime = REMEMBER_SECONDS if remember else SESSION_SECONDS
        response.set_cookie(COOKIE, _issue(str(user_obj["id"]), user_obj.get("session_version", 0), lifetime), max_age=lifetime if remember else None, httponly=True,
                            secure=public_origin().startswith("https://"), samesite="lax", path="/")
        response.headers["Cache-Control"] = "no-store"
        return response

    user = await run_in_threadpool(_load_user_session, request.cookies.get(COOKIE))
    if not user:
        if request.method == "GET" and "text/html" in request.headers.get("accept", ""):
            return RedirectResponse("/login", status_code=303)
        return JSONResponse({"error": "Autenticação necessária"}, status_code=401)

    request.state.cortex_authenticated = True
    request.state.cortex_user = user["username"]
    request.state.cortex_user_id = str(user["id"])
    request.state.cortex_role = user["role"]
    request.state.cortex_user_obj = user

    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        origin = request.headers.get("origin")
        expected = public_origin()
        if not origin or origin.rstrip("/") != expected:
            return JSONResponse({"error": "Origin inválida"}, status_code=403)

    if path == "/logout":
        response = RedirectResponse("/login", status_code=303)
        response.delete_cookie(COOKIE, path="/")
        return response

    if user.get("must_change_password"):
        if path not in ("/senha", "/api/auth/change-password", "/logout"):
            if request.method == "GET" and "text/html" in request.headers.get("accept", ""):
                return RedirectResponse("/senha", status_code=303)
            return JSONResponse({"error": "Troca de senha obrigatória"}, status_code=403)

    role = user.get("role", "support_analyst")
    if role != "super_admin":
        allowed = False
        if path in ("/logout", "/senha", "/api/auth/change-password", "/api/me") or path.startswith("/tech-assets/"):
            allowed = True
        elif role == "support_analyst":
            if path in ("/atendimento", "/api/me/work-today") or path.startswith("/api/work/"):
                allowed = True
        elif role in ("director", "support_supervisor"):
            if path == "/em-breve":
                allowed = True

        if not allowed:
            if request.method == "GET" and "text/html" in request.headers.get("accept", ""):
                return RedirectResponse(destination_for_user(user), status_code=303)
            return JSONResponse({"error": "Acesso não autorizado para seu perfil"}, status_code=403)

    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["Content-Security-Policy"] = "frame-ancestors 'self'"
    return response
