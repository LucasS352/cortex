"""Build a private env file ready for Portainer's environment import."""
from pathlib import Path

from dotenv import dotenv_values


ROOT = Path(__file__).resolve().parent.parent
SERVER_ENV = ROOT / ".env.server"
ACCESS_ENV = ROOT / ".env.server.access"
OUTPUT_ENV = ROOT / ".env.server.portainer"


def write_portainer_env(path: Path, values: dict[str, str]) -> None:
    """Write values exactly as Portainer expects them in its env-file importer."""
    lines = ["# Importar em Stacks > cortex > Edit stack settings > Environment variables.\n"]
    for key, value in values.items():
        if any(character in value for character in ("\0", "\r", "\n")):
            raise ValueError(f"A variável {key} contém uma quebra de linha inválida.")
        lines.append(f"{key}={value}\n")
    path.write_text("".join(lines), encoding="utf-8")


def main() -> None:
    server = dotenv_values(SERVER_ENV, interpolate=False)
    access = dotenv_values(ACCESS_ENV, interpolate=False)
    values = {
        "CORTEX_PUBLIC_URL": access.get("CORTEX_PUBLIC_URL") or "http://179.236.68.24",
        "CORTEX_IMAGE": server.get("CORTEX_IMAGE") or "ghcr.io/lucass352/cortex:latest",
        "CORTEX_ADMIN_USERNAME": access.get("CORTEX_ADMIN_USERNAME") or "alternariva",
        "CORTEX_ADMIN_PASSWORD_HASH": access.get("CORTEX_ADMIN_PASSWORD_HASH") or "",
        "CORTEX_SESSION_SECRET": access.get("CORTEX_SESSION_SECRET") or "",
        # The existing Portainer volume was initialized by the old Compose fallback.
        "POSTGRES_PASSWORD": "cortex_secret",
        "MOVIDESK_TOKEN": server.get("MOVIDESK_TOKEN") or "",
        "MOVIDESK_WEBHOOK_SECRET": server.get("MOVIDESK_WEBHOOK_SECRET") or "",
        "GEMINI_API_KEY": server.get("GEMINI_API_KEY") or "",
        "GEMINI_MODEL": server.get("GEMINI_MODEL") or "gemini-2.5-flash",
        "CORTEX_AUTONOTE_MODE": server.get("CORTEX_AUTONOTE_MODE") or "dry_run",
        "MOVIDESK_BOT_PERSON_ID": server.get("MOVIDESK_BOT_PERSON_ID") or "",
        "MOVIDESK_TICKET_UI_BASE": server.get("MOVIDESK_TICKET_UI_BASE") or "https://helpdesk.alternativasistemas.com.br/Ticket/Edit",
        "CORTEX_PILOT_ORGANIZATION_IDS": server.get("CORTEX_PILOT_ORGANIZATION_IDS") or "",
        "CORTEX_PILOT_TICKET_IDS": server.get("CORTEX_PILOT_TICKET_IDS") or "",
        "CORTEX_MOVIDESK_INTERVAL_SECONDS": server.get("CORTEX_MOVIDESK_INTERVAL_SECONDS") or "7",
        "CORTEX_RUNTIME_PILOT_CONTROL": "true",
    }
    required = ("CORTEX_ADMIN_PASSWORD_HASH", "CORTEX_SESSION_SECRET", "MOVIDESK_WEBHOOK_SECRET")
    missing = [key for key in required if not values[key]]
    if missing:
        raise SystemExit("Variáveis privadas ausentes: " + ", ".join(missing))

    # Portainer preserves quote characters when importing environment variables.
    # Write raw values so quotes do not become part of usernames, URLs or hashes.
    write_portainer_env(OUTPUT_ENV, values)
    print(f"Arquivo privado criado: {OUTPUT_ENV.name} ({len(values)} variáveis).")


if __name__ == "__main__":
    main()
