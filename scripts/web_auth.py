"""Small single-admin gate for the private Cortex deployment."""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import secrets
import time
from urllib.parse import parse_qs

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

COOKIE = "cortex_session"
SESSION_SECONDS = 8 * 60 * 60
_failures: dict[str, list[float]] = {}


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
    if not os.getenv("CORTEX_PUBLIC_URL", "").startswith("https://"):
        raise RuntimeError("CORTEX_PUBLIC_URL precisa ser HTTPS")


def hash_password(password: str) -> str:
    if len(password) < 12:
        raise ValueError("A senha precisa de pelo menos 12 caracteres")
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
    except (ValueError, KeyError, UnicodeError):
        return False


def login_page(error: bool = False) -> HTMLResponse:
    message = '<p class="error">Usuário ou senha inválidos.</p>' if error else ""
    page = f'''<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Entrar · Cortex</title><style>
    :root{{color-scheme:dark}}*{{box-sizing:border-box}}body{{margin:0;min-height:100vh;display:grid;place-items:center;background:#0d1930;color:#e8f3ff;font:16px system-ui,sans-serif}}main{{width:min(92vw,420px);background:#14243c;border:1px solid #335170;border-radius:18px;padding:32px;box-shadow:0 18px 65px #0006}}h1{{margin:0 0 8px}}p{{color:#a9c0d5}}label{{display:block;margin:18px 0 6px}}input{{width:100%;padding:12px;border:1px solid #54708d;border-radius:8px;background:#0b1a30;color:white;font:inherit}}button{{width:100%;margin-top:24px;padding:12px;border:0;border-radius:8px;background:#1a77db;color:white;font:inherit;font-weight:700;cursor:pointer}}.error{{color:#ffaaa9}}
    </style></head><body><main><h1>CORTEX</h1><p>Acesso administrativo</p>{message}<form method="post" action="/login"><label for="user">Usuário</label><input id="user" name="username" autocomplete="username" required autofocus><label for="pass">Senha</label><input id="pass" type="password" name="password" autocomplete="current-password" required><button>Entrar</button></form></main></body></html>'''
    return HTMLResponse(page, headers={"Cache-Control": "no-store", "X-Frame-Options": "DENY"})


async def gate(request: Request, call_next):
    if not enabled():
        return await call_next(request)
    path = request.url.path
    if path == "/healthz":
        try:
            import psycopg
            from config import POSTGRES_DSN
            with psycopg.connect(POSTGRES_DSN, connect_timeout=2) as db:
                ready = db.execute("SELECT to_regclass('public.ticket_knowledge_nodes') IS NOT NULL").fetchone()[0]
        except Exception:
            ready = False
        return JSONResponse({"status": "ok" if ready else "unavailable"}, status_code=200 if ready else 503)
    if path == "/login":
        if request.method == "GET":
            return login_page()
        if request.method != "POST":
            return JSONResponse({"error": "Method not allowed"}, status_code=405)
        origin = request.headers.get("origin")
        if not origin or origin.rstrip("/") != os.environ["CORTEX_PUBLIC_URL"].rstrip("/"):
            return JSONResponse({"error": "Origin inválida"}, status_code=403)
        ip = request.client.host if request.client else "unknown"
        now = time.time()
        attempts = [stamp for stamp in _failures.get(ip, []) if now - stamp < 900]
        _failures[ip] = attempts
        if len(attempts) >= 8:
            return HTMLResponse("Muitas tentativas. Tente mais tarde.", status_code=429)
        data = await request.body()
        if len(data) > 4096:
            return HTMLResponse("Formulário muito grande", status_code=413)
        form = parse_qs(data.decode("utf-8", errors="replace"))
        user = form.get("username", [""])[0]
        password = form.get("password", [""])[0]
        valid_user = hmac.compare_digest(user, os.environ["CORTEX_ADMIN_USERNAME"])
        valid_pass = verify_password(password)
        if not (valid_user and valid_pass):
            attempts.append(now)
            return login_page(True)
        _failures.pop(ip, None)
        response = RedirectResponse("/admin", status_code=303)
        response.set_cookie(COOKIE, _issue(), max_age=SESSION_SECONDS, httponly=True, secure=True, samesite="lax", path="/")
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
        expected = os.environ["CORTEX_PUBLIC_URL"].rstrip("/")
        if not origin or origin.rstrip("/") != expected:
            return JSONResponse({"error": "Origin inválida"}, status_code=403)
    if path == "/logout":
        response = RedirectResponse("/login", status_code=303)
        response.delete_cookie(COOKIE, path="/")
        return response
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    return response
