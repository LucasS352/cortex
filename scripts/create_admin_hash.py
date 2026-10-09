"""Generate deployment secrets without storing a clear-text password."""
from getpass import getpass
import secrets

from web_auth import hash_password


if __name__ == "__main__":
    password = getpass("Senha do administrador (mínimo 12 caracteres): ")
    confirmation = getpass("Repita a senha: ")
    if password != confirmation:
        raise SystemExit("As senhas não coincidem.")
    try:
        password_hash = hash_password(password)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    print("CORTEX_ADMIN_PASSWORD_HASH=" + password_hash)
    print("CORTEX_SESSION_SECRET=" + secrets.token_urlsafe(48))
    print("MOVIDESK_WEBHOOK_SECRET=" + secrets.token_urlsafe(48))
