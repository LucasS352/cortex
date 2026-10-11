"""Create a separate server environment without printing credentials."""
import argparse
import secrets
from pathlib import Path
from dotenv import dotenv_values, set_key


def prepare(root, from_local=False):
    target = root / '.env.server'
    # Exclusive creation: never replace an already configured deployment.
    with target.open('x', encoding='utf-8') as handle:
        handle.write((root / '.env.server.example').read_text(encoding='utf-8'))
    if from_local:
        current = dotenv_values(root / '.env', interpolate=False)
        for key in ('MOVIDESK_TOKEN', 'GEMINI_API_KEY', 'GEMINI_MODEL',
                    'CORTEX_ADMIN_USERNAME', 'CORTEX_ADMIN_PASSWORD_HASH'):
            if current.get(key):
                set_key(target, key, current[key], quote_mode='always')
    for key in ('POSTGRES_PASSWORD', 'CORTEX_SESSION_SECRET', 'MOVIDESK_WEBHOOK_SECRET'):
        set_key(target, key, secrets.token_urlsafe(48), quote_mode='always')
    return target


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--from-local', action='store_true', help='Reutilizar somente chaves de API e login do .env local')
    args = parser.parse_args()
    try:
        target = prepare(Path(__file__).resolve().parent.parent, args.from_local)
    except FileExistsError:
        raise SystemExit('.env.server já existe. Nenhuma credencial foi alterada.')
    print('Criado .env.server separado. Confira CORTEX_PUBLIC_URL, login, agente Movidesk e IDs do piloto.')
