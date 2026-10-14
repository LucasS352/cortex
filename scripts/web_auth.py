"""Small single-admin gate for the private Cortex deployment."""
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
_failures: dict[str, list[float]] = {}


def public_origin() -> str:
    value = os.getenv("CORTEX_PUBLIC_URL", "").rstrip("/")
    url = urlsplit(value)
    if (url.scheme not in {"http", "https"} or not url.hostname or url.username
            or url.password or url.path or url.query or url.fragment):
        raise ValueError("CORTEX_PUBLIC_URL deve ser uma origem HTTP/HTTPS, sem caminho")
    # Also validate malformed ports before serving any request.
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


def verify_password(password: str) -> bool:
    try:
        kind, n, r, p, salt, expected = os.environ["CORTEX_ADMIN_PASSWORD_HASH"].split("$")
        if kind != "scrypt" or (int(n), int(r), int(p)) != (16384, 8, 1):
            return False
        digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p))
        return hmac.compare_digest(digest, bytes.fromhex(expected))
    except (KeyError, ValueError, TypeError):
        return False


def _sign(payload: str) -> str:
    key = os.environ["CORTEX_SESSION_SECRET"].encode()
    return hmac.new(key, payload.encode(), hashlib.sha256).hexdigest()


def _issue() -> str:
    payload = f"{os.environ['CORTEX_ADMIN_USERNAME']}:{int(time.time()) + SESSION_SECONDS}:{secrets.token_hex(12)}"
    return base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=") + "." + _sign(payload)


def _valid(cookie: str | None) -> bool:
    if not cookie or len(cookie) > 512:
        return False
    try:
        encoded, signature = cookie.rsplit(".", 1)
        payload = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)).decode()
        user, expiry, _nonce = payload.split(":", 2)
        return (user == os.environ["CORTEX_ADMIN_USERNAME"] and int(expiry) > time.time()
                and hmac.compare_digest(signature, _sign(payload)))
    except (ValueError, KeyError, UnicodeError, TypeError):
        return False


def login_page(error: bool = False) -> HTMLResponse:
    from pathlib import Path
    message = '<p class="error" role="alert">Usuário ou senha inválidos.</p>' if error else ""
    page = (Path(__file__).parent / "static" / "login.html").read_text(encoding="utf-8")
    return HTMLResponse(page.replace("<!-- LOGIN_ERROR -->", message),
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
    if path == "/healthz":
        ready = await run_in_threadpool(_database_ready)
        return JSONResponse({"status": "ok" if ready else "unavailable"}, status_code=200 if ready else 503,
                            headers={"Cache-Control": "no-store"})
    # Only reviewed, bundled procedure images are public so Movidesk can render
    # them inside a ticket action. Admin assets and source manuals stay private.
    if request.method in {"GET", "HEAD"} and re.fullmatch(r"/procedure-images/[a-z0-9-]+\.png", path):
        response = await call_next(request)
        response.headers["Cache-Control"] = "public, max-age=86400"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response
    if not enabled():
        return await call_next(request)
    if path == "/login":
        if request.method == "GET":
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
        # Reserve before awaiting scrypt so simultaneous failures share the limit.
        attempts.append(now)
        form = parse_qs(data.decode("utf-8", errors="replace"))
        user = form.get("username", [""])[0]
        password = form.get("password", [""])[0]
        valid_user = hmac.compare_digest(user.encode(), os.environ["CORTEX_ADMIN_USERNAME"].encode())
        valid_pass = await run_in_threadpool(verify_password, password)
        if not (valid_user and valid_pass):
            return login_page(True)
        _failures.pop(ip, None)
        response = RedirectResponse("/admin", status_code=303)
        response.set_cookie(COOKIE, _issue(), max_age=SESSION_SECONDS, httponly=True,
                            secure=public_origin().startswith("https://"), samesite="lax", path="/")
        response.headers["Cache-Control"] = "no-store"
        return response
    authenticated = _valid(request.cookies.get(COOKIE))
    if not authenticated:
        if request.method == "GET" and "text/html" in request.headers.get("accept", ""):
            return RedirectResponse("/login", status_code=303)
        return JSONResponse({"error": "Autenticação necessária"}, status_code=401)
    request.state.cortex_authenticated = True
    request.state.cortex_user = os.environ["CORTEX_ADMIN_USERNAME"]
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        origin = request.headers.get("origin")
        expected = public_origin()
        if not origin or origin.rstrip("/") != expected:
            return JSONResponse({"error": "Origin inválida"}, status_code=403)
    if path == "/logout":
        response = RedirectResponse("/login", status_code=303)
        response.delete_cookie(COOKIE, path="/")
        return response
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    # The admin console embeds /treinamento in a same-origin iframe. DENY also
    # blocks that legitimate frame in production, where this middleware is
    # enabled. SAMEORIGIN keeps cross-site framing blocked while allowing the
    # authenticated Cortex shell to render its own training page.
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["Content-Security-Policy"] = "frame-ancestors 'self'"
    return response
