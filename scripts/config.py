"""
DeskCortex — Configuração Centralizada do Pipeline de Ingestão Movidesk
=========================================================================
Carrega variáveis de ambiente e define constantes para rate limiting,
parâmetros OData e detecção de janela horária (comercial vs turbo noturno).
"""

import os
from datetime import datetime
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Carregamento do .env (busca na raiz do projeto)
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

# ---------------------------------------------------------------------------
# Credenciais Movidesk
# ---------------------------------------------------------------------------
MOVIDESK_TOKEN: str = os.getenv("MOVIDESK_TOKEN", "")
MOVIDESK_BASE_URL: str = os.getenv("MOVIDESK_BASE_URL", "https://api.movidesk.com/public/v1")
MOVIDESK_WEBHOOK_SECRET: str = os.getenv("MOVIDESK_WEBHOOK_SECRET", "")

# ---------------------------------------------------------------------------
# Credenciais PostgreSQL
# ---------------------------------------------------------------------------
POSTGRES_HOST: str = os.getenv("POSTGRES_HOST", "127.0.0.1")
if POSTGRES_HOST == "localhost":
    POSTGRES_HOST = "127.0.0.1"
POSTGRES_PORT: int = int(os.getenv("POSTGRES_PORT", "5432"))
POSTGRES_DB: str = os.getenv("POSTGRES_DB", "deskcortex")
POSTGRES_USER: str = os.getenv("POSTGRES_USER", "deskcortex")
POSTGRES_PASSWORD: str = os.getenv("POSTGRES_PASSWORD", "")

POSTGRES_DSN: str = (
    f"postgresql://{quote(POSTGRES_USER, safe='')}:{quote(POSTGRES_PASSWORD, safe='')}"
    f"@{POSTGRES_HOST}:{POSTGRES_PORT}/{POSTGRES_DB}"
)

# ---------------------------------------------------------------------------
# Credenciais Redis
# ---------------------------------------------------------------------------
REDIS_HOST: str = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT: int = int(os.getenv("REDIS_PORT", "6379"))

# ---------------------------------------------------------------------------
# Gemini AI — Embeddings (gemini-embedding-2, 3072 dims, MRL)
# ---------------------------------------------------------------------------
GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
GEMINI_EMBEDDING_MODEL: str = os.getenv("GEMINI_EMBEDDING_MODEL", "models/gemini-embedding-2")
GEMINI_EMBEDDING_DIMENSIONS: int = int(os.getenv("GEMINI_EMBEDDING_DIMENSIONS", "3072"))
# Rate limit gratuito: 1500 req/min, 100 req/min por projeto
GEMINI_EMBED_RPM_LIMIT: int = 90  # Margem de segurança abaixo do limite

# ---------------------------------------------------------------------------
# Parâmetros OData para a API do Movidesk
# ---------------------------------------------------------------------------
ODATA_TOP: int = 100  # Máximo suportado por página

# Campos selecionados para listagem de tickets (baseado na doc oficial da API Movidesk)
ODATA_SELECT_FIELDS: str = ",".join([
    "id",
    "protocol",
    "type",
    "subject",
    "category",
    "urgency",
    "status",
    "baseStatus",
    "justification",
    "origin",
    "createdDate",
    "originEmailAccount",
    "serviceFirstLevelId",
    "serviceFirstLevel",
    "serviceSecondLevel",
    "serviceThirdLevel",
    "contactForm",
    "tags",
    "resolvedIn",
    "reopenedIn",
    "closedIn",
    "lastActionDate",
    "actionCount",
    "lastUpdate",
    "lifetimeWorkingTime",
    "stoppedTime",
    "stoppedTimeWorkingTime",
    "resolvedInFirstCall",
    "sequence",
    "slaAgreement",
    "slaAgreementRule",
    "slaSolutionTime",
    "slaResponseTime",
    "slaSolutionDate",
    "slaSolutionDateIsPaused",
    "slaResponseDate",
    "slaRealResponseDate",
    "jiraIssueKey",
    "redmineIssueId",
    "owner",
    "ownerTeam",
    "createdBy",
])

# Expands para incluir dados relacionados (actions, clients, customFields, parentTickets)
ODATA_EXPAND: str = (
    "actions($select=id,type,origin,description,status,justification,createdDate,createdBy,isDeleted),"
    "clients($expand=organization),"
    "customFieldValues($expand=items),"
    "parentTickets"
)

# ---------------------------------------------------------------------------
# Rate Limiting Adaptativo
# ---------------------------------------------------------------------------
@dataclass
class RateConfig:
    """Configuração de rate limiting baseada na janela horária."""
    max_concurrent: int     # Número máximo de workers async
    delay_seconds: float    # Delay entre requests (por worker)
    mode: str               # "comercial" ou "turbo"
    requests_per_minute: int  # Limite de requests/min


# Limites por janela horária
RATE_COMERCIAL = RateConfig(
    max_concurrent=1,
    delay_seconds=6.7,      # ~9 req/min (seguro abaixo de 10)
    mode="comercial",
    requests_per_minute=9,
)

RATE_TURBO = RateConfig(
    max_concurrent=8,
    delay_seconds=0.2,      # Intervalo mínimo entre requests por worker
    mode="turbo",
    requests_per_minute=0,  # 0 = ilimitado
)


def get_rate_config() -> RateConfig:
    """Retorna a configuração de rate limiting baseada no horário atual.

    - Comercial (07:00–18:59): 1 worker, ~9 req/min
    - Turbo Noturno (19:00–06:58): 8 workers concorrentes
    """
    hour = datetime.now().hour
    if 7 <= hour <= 18:
        return RATE_COMERCIAL
    return RATE_TURBO


def is_turbo_window() -> bool:
    """Verifica se estamos na janela noturna (turbo)."""
    hour = datetime.now().hour
    return hour >= 19 or hour < 7


# ---------------------------------------------------------------------------
# Retry / Backoff
# ---------------------------------------------------------------------------
MAX_RETRIES: int = 5
BACKOFF_BASE_SECONDS: float = 5.0   # 5s, 10s, 20s, 40s, 80s
BACKOFF_MAX_SECONDS: float = 120.0

# ---------------------------------------------------------------------------
# Diretórios de saída
# ---------------------------------------------------------------------------
OUTPUT_DIR = Path(__file__).parent / "output"
OUTPUT_DIR.mkdir(exist_ok=True)
