"""
DeskCortex — Simulador Interativo de Chamados da Alternativa Sistemas
=====================================================================
Interface Web para simular chamados reais do ERP Master e validar o motor
de busca híbrida, hierarquia de confiança e visualização de prints/GIFs originais.

Uso:
    python simulator_app.py
    Acesse no navegador: http://localhost:8000
"""

import base64
import html
import json
import os
import re
import sys
import time
import urllib.request
from pathlib import Path

os.environ["PYTHONIOENCODING"] = "utf-8"
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import psycopg
from psycopg.rows import dict_row
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response, FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel
import uvicorn

sys.path.insert(0, str(Path(__file__).parent))
from config import POSTGRES_DSN, GEMINI_API_KEY, GEMINI_EMBEDDING_MODEL, MOVIDESK_TOKEN, MOVIDESK_BASE_URL, PROJECT_ROOT
from chat_session import session_store, ChatSession, Turn, MediaRef, InvestigationState, EvidenceType, ResolutionStatus
from triage_engine import TriageEngine, BASE_HYPOTHESES
from reranker import Reranker
from vision_analyzer import VisionAnalyzer, VisionResult
from romario_dialogue import RomarioDialogueEngine
from skills.registry import get_global_registry
from skills.base_skill import Capability
"""
DeskCortex — Simulador Interativo de Chamados da Alternativa Sistemas
=====================================================================
Interface Web para simular chamados reais do ERP Master e validar o motor
de busca híbrida, hierarquia de confiança e visualização de prints/GIFs originais.

Uso:
    python simulator_app.py
    Acesse no navegador: http://localhost:8000
"""

import base64
import html
import json
import os
import re
import sys
import time
import urllib.request
from pathlib import Path

os.environ["PYTHONIOENCODING"] = "utf-8"
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import psycopg
from psycopg.rows import dict_row
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response, FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel
import uvicorn

sys.path.insert(0, str(Path(__file__).parent))
from config import POSTGRES_DSN, GEMINI_API_KEY, GEMINI_EMBEDDING_MODEL, MOVIDESK_TOKEN, MOVIDESK_BASE_URL, PROJECT_ROOT
from chat_session import session_store, ChatSession, Turn, MediaRef, InvestigationState, EvidenceType, ResolutionStatus
from triage_engine import TriageEngine, BASE_HYPOTHESES
from reranker import Reranker
from vision_analyzer import VisionAnalyzer, VisionResult
from romario_dialogue import RomarioDialogueEngine
from skills.registry import get_global_registry
from skills.base_skill import Capability
from triage_synthesizer import TriageSynthesizer
from tech_desk import router as tech_desk_router
from tech_evidence import revision as evidence_revision
from master_telemetry import router as master_telemetry_router
from technician_workbench import router as technician_workbench_router
from admin_console import router as admin_console_router, get_state as get_agent_admin_state
from knowledge_training_api import router as knowledge_training_router
import web_auth
import ticket_sync_control
import agent_operations
import knowledge_curation
import triage_router
from ticket_store import ticket_store
from fastapi.exceptions import RequestValidationError
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(_app):
    web_auth.validate_configuration()
    web_auth.ensure_bootstrap_admin()
    ticket_sync_control.start_scheduler()
    try:
        yield
    finally:
        ticket_sync_control.stop_scheduler()


app = FastAPI(title="DeskCortex — Simulador de Atendimento", lifespan=lifespan,
              docs_url=None, redoc_url=None, openapi_url=None)


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return FileResponse(Path(__file__).parent / "static" / "favicon.ico", media_type="image/x-icon")


@app.exception_handler(RequestValidationError)
async def validation_error_without_payload(_request: Request, exc: RequestValidationError):
    # Pydantic includes the original request in its default 422 response. Master
    # config uploads can contain credentials, so never echo rejected input.
    return JSONResponse(status_code=422, content={"detail": [
        {"loc": list(error.get("loc", ())), "type": error.get("type", "value_error"),
         "msg": "Valor inválido."} for error in exc.errors()
    ]})
_trusted_hosts = ["localhost", "127.0.0.1", "[::1]", "testserver"]
_trusted_hosts += [h.strip() for h in os.getenv("CORTEX_ALLOWED_HOSTS", "").split(",") if h.strip()]
if os.getenv("CORTEX_PUBLIC_URL"):
    from urllib.parse import urlsplit
    _public_host = urlsplit(web_auth.public_origin()).hostname
    if _public_host:
        _trusted_hosts.append(_public_host)
app.include_router(tech_desk_router)
app.include_router(master_telemetry_router)
app.include_router(technician_workbench_router)
app.include_router(admin_console_router)
from account_settings import router as account_settings_router
app.include_router(account_settings_router)
app.include_router(knowledge_training_router)
app.mount("/tech-assets", StaticFiles(directory=str(Path(__file__).parent / "static")), name="tech-assets")


@app.get("/treinamento", response_class=HTMLResponse, include_in_schema=False)
def get_training_page():
    return HTMLResponse((Path(__file__).parent / "static" / "treinamento.html").read_text(encoding="utf-8"))


@app.get("/treinamento/tecnico", response_class=HTMLResponse, include_in_schema=False)
def get_technician_training_page():
    return HTMLResponse((Path(__file__).parent / "static" / "treinamento_tecnico.html").read_text(encoding="utf-8"))

REVIEWED_REPLY_MEDIA = Path(__file__).parent / "static" / "reply-media"
TRAINING_MEDIA_DIR = Path(__file__).parent / "output" / "training_media"
TRAINING_MEDIA_DIR.mkdir(parents=True, exist_ok=True)


@app.get("/procedure-images/{name}", include_in_schema=False)
def reviewed_procedure_image(name: str):
    if not re.fullmatch(r"[a-z0-9-]+\.png", name):
        return JSONResponse({"error": "Imagem inválida"}, status_code=404)
    path = REVIEWED_REPLY_MEDIA / name
    if not path.is_file():
        return JSONResponse({"error": "Imagem não encontrada"}, status_code=404)
    return FileResponse(path, media_type="image/png")


@app.get("/training-media/{name}", include_in_schema=False)
def training_proposal_image(name: str):
    if not re.fullmatch(r"[0-9a-f]{32}\.png", name):
        return JSONResponse({"error": "Imagem inválida"}, status_code=404)
    path = TRAINING_MEDIA_DIR / name
    if not path.is_file():
        return JSONResponse({"error": "Imagem não encontrada"}, status_code=404)
    return FileResponse(path, media_type="image/png")


@app.middleware("http")
async def internal_simulator_only(request: Request, call_next):
    async def next_route(authenticated_request):
        if authenticated_request.url.path == '/api/chat' or authenticated_request.url.path.startswith('/api/chat/'):
            return JSONResponse(status_code=410, content={"error": "Chat desativado. Abra um chamado pelo formulário."})
        return await call_next(authenticated_request)
    return await web_auth.gate(request, next_route)

app.add_middleware(TrustedHostMiddleware, allowed_hosts=_trusted_hosts)

ATTACHMENTS_DIR = Path(__file__).parent / "output" / "attachments"
ATTACHMENTS_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/attachments", StaticFiles(directory=str(ATTACHMENTS_DIR)), name="attachments")

CURATION_MEDIA_DIR = Path(__file__).parent / "output" / "curation_media"
CURATION_MEDIA_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/curation_media", StaticFiles(directory=str(CURATION_MEDIA_DIR)), name="curation_media")

INSTRUCOES_DIR = Path(__file__).resolve().parent.parent / "Instrucoes_master"
app.mount("/manual_media", StaticFiles(directory=str(INSTRUCOES_DIR)), name="manual_media")

triage_engine = TriageEngine()
reranker = Reranker(top_k=5)
vision_analyzer = VisionAnalyzer(GEMINI_API_KEY)


@app.get("/", include_in_schema=False)
def root_redirect(request: Request):
    user = getattr(request.state, "cortex_user_obj", {})
    return RedirectResponse(web_auth.destination_for_user(user), status_code=303)


@app.get("/ambientes", response_class=HTMLResponse)
def get_ambientes_page():
    html_path = Path(__file__).parent / "static" / "master-environments.html"
    return HTMLResponse(html_path.read_text(encoding="utf-8"))


@app.get("/assistente")
@app.get("/clientes-contexto")
def get_workbench_page():
    return RedirectResponse(url="/admin", status_code=307)


@app.get("/api/skills")
def list_skills_endpoint():
    """Retorna o catálogo de Skills e suas Capabilities ativas no DeskCortex."""
    registry = get_global_registry()
    return {
        "skills": registry.list_all(),
        "capabilities": registry.list_capabilities()
    }


class SearchRequest(BaseModel):
    subject: str = ""
    problem: str = ""

    module: str = "TODOS"
    top_k: int = 5


class FeedbackRequest(BaseModel):
    node_id: int
    feedback_type: str  # APPROVED, EDITED, REJECTED
    notes: str = ""


QUERY_EMBED_CACHE = {}
EMBEDDING_API_ACTIVE = True
GEMINI_API_ACTIVE = True

def get_query_embedding(text: str) -> list[float] | None:
    """Gera embedding da query com Gemini (RETRIEVAL_QUERY) com cache e fallback instantâneo em 429."""
    global EMBEDDING_API_ACTIVE
    if not EMBEDDING_API_ACTIVE or not GEMINI_API_KEY:
        return None

    cache_key = text.strip()
    if cache_key in QUERY_EMBED_CACHE:
        return QUERY_EMBED_CACHE[cache_key]

    url = f"https://generativelanguage.googleapis.com/v1beta/{GEMINI_EMBEDDING_MODEL}:embedContent?key={GEMINI_API_KEY}"
    body = json.dumps({
        "model": GEMINI_EMBEDDING_MODEL,
        "content": {"parts": [{"text": text[:8000]}]},
        "taskType": "RETRIEVAL_QUERY"
    }).encode("utf-8")

    try:
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=1.5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            vals = data["embedding"]["values"]
            QUERY_EMBED_CACHE[cache_key] = vals
            return vals
    except urllib.error.HTTPError as e:
        if e.code == 429:
            print("[DeskCortex] Gemini Embedding quota/credits limit (429). Ativando busca FTS nativa.")
            EMBEDDING_API_ACTIVE = False
        else:
            print(f"[DeskCortex] Erro HTTP ao gerar embedding da query: {e}")
    except Exception as e:
        print(f"[DeskCortex] Erro ao gerar embedding da query: {e}")
    return None


def lexical_search(conn, query_text: str, module: str = "TODOS", top_k: int = 5) -> list[dict]:
    from knowledge_access import search
    return search(conn, query_text, module, top_k)


def hybrid_search(conn, query_text: str, embedding: list[float] | None, module: str = "TODOS", top_k: int = 5) -> list[dict]:
    from knowledge_access import search
    return search(conn, query_text, module, top_k, embedding)


def _legacy_lexical_search(conn, query_text: str, module: str = "TODOS", top_k: int = 5) -> list[dict]:
    """
    Busca Lexical Pura em ticket_knowledge_nodes com PostgreSQL FTS.
    Utilizada quando embeddings estão indisponíveis ou em fallback instantâneo.
    Aplica os mesmos pesos determinísticos de autoridade, manuais oficiais e recência.
    """
    is_howto = bool(
        re.search(r'^(como\s+|passo\s+a\s+passo|onde\s+|tutorial|procedimento|qual\s+o\s+caminho|forma\s+de)', query_text, re.I) or 
        re.search(r'\b(como\s+fa[çc]o|como\s+fazer|como\s+cadastrar|como\s+enviar|como\s+configurar|como\s+emitir|como\s+exportar|como\s+importar)\b', query_text, re.I)
    )

    clean_phrase = ""
    m = re.search(r'Rejei[çc][ãa]o\s*[:\-]?\s*([^\[\r\n]+)', query_text, re.I)
    if m:
        clean_phrase = re.split(r'(?:Nota\(s\)|;|\||\n|->)', m.group(1))[0].strip()
    else:
        m_err = re.search(r'(?:Erro|Exception|Rejeicao):\s*([^\[\r\n]+)', query_text, re.I)
        if m_err:
            clean_phrase = re.split(r'(?:Nota\(s\)|;|\||\n|->)', m_err.group(1))[0].strip()

    sanitized_ts = re.sub(r'[-–—_]+', ' ', query_text)
    sanitized_ts = re.sub(r'[<>\(\)\{\}\[\]\*\+\\\/]+', ' ', sanitized_ts)
    sanitized_ts = re.sub(r'\s+', ' ', sanitized_ts).strip() or query_text

    COMMON_STOPWORDS = {
        "como", "faco", "faço", "para", "onde", "qual", "quando", "quem", "forma",
        "fazer", "posso", "consigo", "ajuda", "duvida", "dúvida", "sobre", "este",
        "esta", "estou", "estamos", "favor", "verificar", "olá", "ola", "bom", "dia",
        "tarde", "noite", "por", "que", "porque", "porquê", "nfe", "nf", "erro",
        "error", "ocorreram", "seguintes"
    }

    if not clean_phrase:
        substantivos = [w for w in re.findall(r'\w+', sanitized_ts) if len(w) >= 3 and w.lower() not in COMMON_STOPWORDS]
        if substantivos:
            clean_phrase = " ".join(substantivos[:3])

    phrase_pattern = f"%{clean_phrase[:40]}%" if len(clean_phrase) >= 4 else "%"

    # Termo operacional central (ex: inutilizar, cancelamento, etc.)
    core_action = ""
    for w in re.findall(r'\w+', sanitized_ts):
        w_low = w.lower()
        if w_low.startswith("inutiliz"):
            core_action = "inutiliz"
            break
        elif w_low in ("cancelar", "cancelamento"):
            core_action = "cancel"
            break
        elif w_low in ("correcao", "correção", "cce"):
            core_action = "correção"
            break

    MODULE_ALIASES = {
        "Estoque": ["Estoque", "Produtos", "Produto", "Produtos_Estoque"],
        "Fiscal": ["Fiscal", "Nota Fiscal", "Nota_Fiscal", "NF-e", "NFC-e", "SPED"],
        "Nota_Fiscal": ["Fiscal", "Nota Fiscal", "Nota_Fiscal", "NF-e", "NFC-e", "SPED"],
        "Vendas": ["Vendas", "Faturamento", "Pedido"],
        "Financeiro": ["Financeiro", "Boleto", "Cobrança"],
        "Ecommerce": ["Ecommerce", "Marketplace", "Sincronizador", "Integração", "AnyMarket"],
        "Marketplace": ["Marketplace", "Sincronizador", "Integração", "AnyMarket", "Ecommerce"],
        "Banco": ["Banco", "Firebird", "SQL", "Backup"]
    }
    module_aliases = MODULE_ALIASES.get(module, [module])

    error_codes = re.findall(r'\b(10060|10061|10054|204|481|783|539|694|629|388|1824|20419)\b', query_text)
    if not error_codes:
        error_codes = re.findall(r'\b\d{3,5}\b', query_text)

    words = [w for w in re.findall(r'\w+', clean_phrase or sanitized_ts) if len(w) >= 3 and w.lower() not in COMMON_STOPWORDS]
    clauses = []
    if error_codes:
        for c in error_codes[:2]:
            clauses.append(f"'{c}'")
    if len(words) >= 2:
        clauses.append("(" + " & ".join(f"'{w}'" for w in words[:3]) + ")")
    elif words:
        clauses.append(f"'{words[0]}'")

    flexible_ts = " | ".join(clauses) if clauses else (f"'{words[0]}'" if words else "'master'")

    sql = f"""
        WITH lexical AS (
            SELECT
                id,
                LEAST(1.0, GREATEST(
                    ts_rank(
                        setweight(to_tsvector('portuguese', unaccent(COALESCE(title,''))), 'A') ||
                        setweight(to_tsvector('portuguese', unaccent(COALESCE(problem,'') || ' ' || COALESCE(solution,''))), 'D'),
                        to_tsquery('portuguese', unaccent(%s))
                    ) * 3.5,
                    ts_rank(
                        setweight(to_tsvector('portuguese', unaccent(COALESCE(title,''))), 'A') ||
                        setweight(to_tsvector('portuguese', unaccent(COALESCE(problem,'') || ' ' || COALESCE(solution,''))), 'D'),
                        websearch_to_tsquery('portuguese', unaccent(%s))
                    ) * 4.0
                )) AS fts_score
            FROM ticket_knowledge_nodes
            WHERE to_tsvector('portuguese', unaccent(COALESCE(title,'') || ' ' || COALESCE(problem,'') || ' ' || COALESCE(solution,''))) @@ to_tsquery('portuguese', unaccent(%s))
               OR to_tsvector('portuguese', unaccent(COALESCE(title,'') || ' ' || COALESCE(problem,'') || ' ' || COALESCE(solution,''))) @@ websearch_to_tsquery('portuguese', unaccent(%s))
               OR title ILIKE %s
               OR (%s != '' AND title ILIKE %s)
        )
        SELECT
            kn.id,
            kn.source_type,
            kn.source_id,
            kn.source_external_id,
            kn.title,
            kn.problem,
            kn.diagnosis,
            kn.solution,
            kn.result,
            kn.tier,
            kn.confidence,
            kn.source_authority,
            kn.final_score,
            kn.modules,
            kn.detected_error_codes,
            kn.validation_evidence,
            kn.metadata,
            kn.created_at_source,
            kn.media_path,
            kn.media_type,
            kn.manual_category,
            kn.shortcut_keys,
            kn.screen_path,
            kn.media_metadata,
            TO_CHAR(kn.created_at_source, 'DD/MM/YYYY') as formatted_date,
            ROUND(CAST(
                COALESCE(l.fts_score, 0.4)
                * (1.0 + (kn.source_authority / 100.0) * 0.05)
                * CASE
                    WHEN kn.source_type = 'MANUAL' THEN 1.60
                    WHEN %s = TRUE AND kn.source_type = 'OFFICIAL_KB' THEN 1.45
                    WHEN kn.source_type = 'OFFICIAL_KB' THEN 1.15
                    ELSE 1.00
                  END
                * CASE
                    WHEN %s != '' AND kn.title ILIKE %s THEN 2.20
                    WHEN %s != '' AND (kn.title ILIKE %s OR kn.problem ILIKE %s) THEN 1.35
                    ELSE 1.00
                  END
                * CASE 
                    WHEN %s != 'TODOS' AND (
                        (kn.modules IS NOT NULL AND kn.modules && %s::text[]) 
                        OR kn.title ILIKE %s 
                        OR kn.content_for_embedding ILIKE %s
                    ) THEN 1.08 
                    ELSE 1.00 
                  END
            AS NUMERIC), 4) AS combined_score,
            0.0 as semantic_sim,
            ROUND(CAST(COALESCE(l.fts_score, 0) AS NUMERIC), 3) as lexical_score
        FROM ticket_knowledge_nodes kn
        JOIN lexical l ON l.id = kn.id
        ORDER BY (CASE WHEN %s != '' AND kn.title ILIKE %s THEN 1 ELSE 2 END), combined_score DESC
        LIMIT {top_k};
    """
    mod_pattern = f"%{module}%" if module != "TODOS" else "%"
    core_pattern = f"%{core_action}%" if core_action else ""
    params = (
        flexible_ts, sanitized_ts, flexible_ts, sanitized_ts, phrase_pattern,
        core_action, core_pattern,
        is_howto,
        core_action, core_pattern,
        clean_phrase, phrase_pattern, phrase_pattern,
        module, module_aliases, mod_pattern, mod_pattern,
        core_action, core_pattern
    )
    return conn.execute(sql, params).fetchall()


def _legacy_hybrid_search(conn, query_text: str, embedding: list[float] | None, module: str = "TODOS", top_k: int = 5) -> list[dict]:
    """
    Busca Híbrida Balanceada em ticket_knowledge_nodes:
    Relevância = Semântica Cosseno (70%) + FTS Lexical Português (30%)
    Se o embedding for None, faz fallback transparente para busca lexical FTS.
    """
    if not embedding:
        return lexical_search(conn, query_text, module=module, top_k=top_k)

    vec_str = "[" + ",".join(str(float(v)) for v in embedding) + "]"

    # 1. Detecção de Intenção: Dúvida de Processo/Treinamento (HOW-TO) vs Resolução de Erro/Bug
    is_howto = bool(
        re.search(r'^(como\s+|passo\s+a\s+passo|onde\s+|tutorial|procedimento|qual\s+o\s+caminho|forma\s+de)', query_text, re.I) or 
        re.search(r'\b(como\s+fa[çc]o|como\s+fazer|como\s+cadastrar|como\s+enviar|como\s+configurar|como\s+emitir|como\s+exportar|como\s+importar)\b', query_text, re.I)
    )

    # 2. Extração de frase de erro específica para boost exato
    clean_phrase = ""
    m = re.search(r'Rejei[çc][ãa]o:\s*([^\[\r\n]+)', query_text, re.I)
    if m:
        clean_phrase = re.split(r'(?:Nota\(s\)|;|\||\n|->)', m.group(1))[0].strip()
    else:
        m_err = re.search(r'(?:Erro|Exception|Rejeicao):\s*([^\[\r\n]+)', query_text, re.I)
        if m_err:
            clean_phrase = re.split(r'(?:Nota\(s\)|;|\||\n|->)', m_err.group(1))[0].strip()
        elif not is_howto and len(query_text.strip()) >= 8:
            clean_phrase = query_text.strip()[:60]

    phrase_pattern = f"%{clean_phrase[:40]}%" if len(clean_phrase) >= 6 else "%"

    # Mapeamento inteligente de módulos para sinônimos do ERP Master
    MODULE_ALIASES = {
        "Estoque": ["Estoque", "Produtos", "Produto", "Produtos_Estoque"],
        "Fiscal": ["Fiscal", "Nota Fiscal", "Nota_Fiscal", "NF-e", "NFC-e", "SPED"],
        "Nota_Fiscal": ["Fiscal", "Nota Fiscal", "Nota_Fiscal", "NF-e", "NFC-e", "SPED"],
        "Vendas": ["Vendas", "Faturamento", "Pedido"],
        "Financeiro": ["Financeiro", "Boleto", "Cobrança"],
        "Ecommerce": ["Ecommerce", "Marketplace", "Sincronizador", "Integração", "AnyMarket"],
        "Marketplace": ["Marketplace", "Sincronizador", "Integração", "AnyMarket", "Ecommerce"],
        "Banco": ["Banco", "Firebird", "SQL", "Backup"]
    }
    module_aliases = MODULE_ALIASES.get(module, [module])
    sanitized_ts = re.sub(r'[-–—_]+', ' ', query_text)
    sanitized_ts = re.sub(r'[<>\(\)\{\}\[\]\*\+\\\/]+', ' ', sanitized_ts)
    sanitized_ts = re.sub(r'\s+', ' ', sanitized_ts).strip() or query_text

    error_codes = re.findall(r'\b(10060|10061|10054|204|481|783|539|694|629|388|1824|20419)\b', query_text)
    if not error_codes:
        error_codes = re.findall(r'\b\d{3,5}\b', query_text)

    words = [w for w in re.findall(r'\w+', clean_phrase or sanitized_ts) if len(w) >= 3 and w.lower() not in ("nfe", "nf", "erro", "error", "ocorreram", "seguintes")]
    clauses = []
    if error_codes:
        for c in error_codes[:2]:
            clauses.append(f"'{c}'")
    if len(words) >= 2:
        clauses.append("(" + " & ".join(f"'{w}'" for w in words[:3]) + ")")
    elif words:
        clauses.append(f"'{words[0]}'")

    flexible_ts = " | ".join(clauses) if clauses else (f"'{words[0]}'" if words else "'master'")
    
    sql = f"""
        WITH semantic AS (
            SELECT
                id,
                1 - (embedding <=> '{vec_str}'::vector) AS semantic_score
            FROM ticket_knowledge_nodes
            WHERE embedding_generated = TRUE
        ),
        lexical AS (
            SELECT
                id,
                LEAST(1.0, GREATEST(
                    ts_rank(
                        setweight(to_tsvector('portuguese', unaccent(COALESCE(title,''))), 'A') ||
                        setweight(to_tsvector('portuguese', unaccent(COALESCE(problem,'') || ' ' || COALESCE(solution,''))), 'D'),
                        to_tsquery('portuguese', unaccent(%s))
                    ) * 3.5,
                    ts_rank(
                        setweight(to_tsvector('portuguese', unaccent(COALESCE(title,''))), 'A') ||
                        setweight(to_tsvector('portuguese', unaccent(COALESCE(problem,'') || ' ' || COALESCE(solution,''))), 'D'),
                        websearch_to_tsquery('portuguese', unaccent(%s))
                    ) * 4.0
                )) AS fts_score
            FROM ticket_knowledge_nodes
            WHERE to_tsvector('portuguese', unaccent(COALESCE(title,'') || ' ' || COALESCE(problem,'') || ' ' || COALESCE(solution,''))) @@ to_tsquery('portuguese', unaccent(%s))
               OR to_tsvector('portuguese', unaccent(COALESCE(title,'') || ' ' || COALESCE(problem,'') || ' ' || COALESCE(solution,''))) @@ websearch_to_tsquery('portuguese', unaccent(%s))
               OR title ILIKE %s
        )
        SELECT
            kn.id,
            kn.source_type,
            kn.source_id,
            kn.source_external_id,
            kn.title,
            kn.problem,
            kn.diagnosis,
            kn.solution,
            kn.result,
            kn.tier,
            kn.confidence,
            kn.source_authority,
            kn.final_score,
            kn.modules,
            kn.detected_error_codes,
            kn.validation_evidence,
            kn.metadata,
            kn.created_at_source,
            kn.media_path,
            kn.media_type,
            kn.manual_category,
            kn.shortcut_keys,
            kn.screen_path,
            kn.media_metadata,
            TO_CHAR(kn.created_at_source, 'DD/MM/YYYY') as formatted_date,
            ROUND(CAST(
                ((COALESCE(s.semantic_score, 0) * 0.70) + (COALESCE(l.fts_score, 0) * 0.30))
                * (1.0 + (kn.source_authority / 100.0) * 0.05)
                * CASE
                    -- Manual Interno (MANUAL) possui autoridade maxima absoluta (100)
                    WHEN kn.source_type = 'MANUAL' THEN 1.60
                    -- Se a pergunta for HOW-TO (ex: "como cadastrar produto", "como enviar ao site"),
                    -- a Base de Conhecimento Oficial ganha autoridade absoluta (+45 por cento boost)
                    WHEN %s = TRUE AND kn.source_type = 'OFFICIAL_KB' THEN 1.45
                    WHEN kn.source_type = 'OFFICIAL_KB' THEN 1.15
                    ELSE 1.00
                  END
                * CASE
                    -- Recencia temporal para chamados de erro (bugs mudam rapido).
                    -- Mas nao penaliza artigos perenes da KB em duvidas conceituais de processo!
                    WHEN %s = FALSE AND kn.created_at_source >= '2026-01-01' THEN 1.25
                    WHEN %s = FALSE AND kn.created_at_source >= '2025-01-01' THEN 1.15
                    WHEN kn.created_at_source >= '2024-01-01' THEN 1.05
                    ELSE 1.00
                  END
                * CASE
                    WHEN %s != '' AND (kn.title ILIKE %s OR kn.problem ILIKE %s) THEN 1.35
                    ELSE 1.00
                  END
                * CASE 
                    WHEN %s != 'TODOS' AND (
                        (kn.modules IS NOT NULL AND kn.modules && %s::text[]) 
                        OR kn.title ILIKE %s 
                        OR kn.content_for_embedding ILIKE %s
                    ) THEN 1.08 
                    ELSE 1.00 
                  END
            AS NUMERIC), 4) AS combined_score,
            ROUND(CAST(COALESCE(s.semantic_score, 0) AS NUMERIC), 3) as semantic_sim,
            ROUND(CAST(COALESCE(l.fts_score, 0) AS NUMERIC), 3) as lexical_score
        FROM ticket_knowledge_nodes kn
        JOIN semantic s ON s.id = kn.id
        LEFT JOIN lexical l ON l.id = kn.id
        WHERE kn.embedding_generated = TRUE
        ORDER BY combined_score DESC
        LIMIT {top_k};

    """
    mod_pattern = f"%{module}%" if module != "TODOS" else "%"
    params = (
        flexible_ts, sanitized_ts, flexible_ts, sanitized_ts, f"%{clean_phrase[:40]}%",
        is_howto,
        is_howto, is_howto,
        clean_phrase, phrase_pattern, phrase_pattern,
        module, module_aliases, mod_pattern, mod_pattern
    )
    return conn.execute(sql, params).fetchall()










@app.get("/api/ticket/{external_id}")
def get_ticket_details(external_id: int):
    with psycopg.connect(POSTGRES_DSN, row_factory=dict_row) as conn:
        row = conn.execute("""
            SELECT payload
            FROM raw_tickets
            WHERE external_id = %s;
        """, (external_id,)).fetchone()
        
        p = row["payload"] if row else None
        actions = p.get("actions", []) if p else []
        has_rich_html = any(bool(a.get("htmlDescription")) for a in actions)
        
        # Se o payload armazenado não tem htmlDescription (dump legado), enriquece sob demanda da API!
        if not has_rich_html:
            url = f"{MOVIDESK_BASE_URL}/tickets?token={MOVIDESK_TOKEN}&id={external_id}&$expand=actions($expand=attachments),clients,owner"
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "DeskCortex/1.0"})
                with urllib.request.urlopen(req, timeout=8) as resp:
                    p = json.loads(resp.read().decode("utf-8"))
                    conn.execute("""
                        UPDATE raw_tickets
                        SET payload = %s::jsonb
                        WHERE external_id = %s;
                    """, (json.dumps(p), external_id))
                    conn.commit()
            except Exception as e:
                print(f"Erro ao enriquecer chamado #{external_id}: {e}")

        if not p:
            return JSONResponse(status_code=404, content={"error": "Chamado não encontrado"})

        for act in p.get("actions", []):
            html = act.get("htmlDescription")
            if html:
                act["htmlDescription"] = re.sub(
                    r'https?://[^"\']*movidesk-files/([A-Fa-f0-9]{32})[^"\']*',
                    r'/api/image/\1',
                    html
                )
        return p




@app.get("/api/image/{file_hash}")
def get_image_proxy(file_hash: str):
    """Proxy e cache de imagens do Movidesk para contornar bloqueio 403 da AWS S3."""
    match = re.search(r'([A-Fa-f0-9]{32})', file_hash)
    clean_hash = match.group(1) if match else re.sub(r'[^A-Za-z0-9]', '', file_hash)
    
    cache_dir = ATTACHMENTS_DIR / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    local_file = cache_dir / f"{clean_hash}.png"
    
    if local_file.exists() and local_file.stat().st_size > 0:
        return FileResponse(str(local_file), media_type="image/png")
        
    url = f"https://api.movidesk.com/public/v1/storage/download?token={MOVIDESK_TOKEN}&id={clean_hash}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=12) as resp:
            content = resp.read()
            local_file.write_bytes(content)
            media_type = "image/gif" if content.startswith(b"GIF") else "image/png"
            return Response(content=content, media_type=media_type)
    except Exception as e:
        print(f"Erro ao baixar imagem {clean_hash}: {e}")
        return JSONResponse(status_code=404, content={"error": "Imagem não encontrada"})


@app.get("/api/kb/{source_id}")
def get_kb_details(source_id: str):
    raw_id = source_id.replace("kb-", "")
    with psycopg.connect(POSTGRES_DSN, row_factory=dict_row) as conn:
        row = conn.execute("""
            SELECT id, title, summary, categories, content_html, content_text, created_at_source
            FROM raw_kb_articles
            WHERE id::text = %s;
        """, (raw_id,)).fetchone()
        if not row:
            return JSONResponse(status_code=404, content={"error": "Artigo não encontrado"})
        
        data = dict(row)
        if data.get("content_html"):
            # Substitui links do S3 pelo proxy seguro que não dá 403
            data["content_html"] = re.sub(
                r'https?://[^"\']*movidesk-files/([A-Fa-f0-9]{32})[^"\']*',
                r'/api/image/\1',
                data["content_html"]
            )
        return data


@app.get("/api/manual/{node_id}")
def get_manual_details(node_id: int):
    with psycopg.connect(POSTGRES_DSN, row_factory=dict_row) as conn:
        row = conn.execute("""
            SELECT id, source_type, source_id, title, problem, diagnosis, solution, result,
                   modules, detected_error_codes, validation_evidence, metadata,
                   media_path, media_type, manual_category, shortcut_keys, screen_path, media_metadata,
                   content_for_embedding
            FROM ticket_knowledge_nodes
            WHERE id = %s;
        """, (node_id,)).fetchone()
        if not row:
            return JSONResponse(status_code=404, content={"error": "Manual não encontrado"})
        
        doc = dict(row)
        if isinstance(doc.get("media_metadata"), str):
            try:
                doc["media_metadata"] = json.loads(doc["media_metadata"])
            except:
                pass
        if isinstance(doc.get("metadata"), str):
            try:
                doc["metadata"] = json.loads(doc["metadata"])
            except:
                pass
        return doc


@app.post("/api/search")
def search_endpoint(req: SearchRequest):
    combined_query = f"{req.subject} {req.problem}".strip()
    if not combined_query:
        return {"results": []}

    embedding = get_query_embedding(combined_query)
    with psycopg.connect(POSTGRES_DSN, row_factory=dict_row) as conn:
        results = hybrid_search(conn, combined_query, embedding, req.module, req.top_k)

    # The local admin console can pause individual knowledge lanes. Keep this
    # filter in the retrieval path so the setting affects real suggestions.
    allowed_sources = set(get_agent_admin_state().get("source_types", []))
    results = [r for r in results if (r.get("source_type") or "PROBABLE_TICKET") in allowed_sources]

    # Serializar resultados
    clean_results = []
    for r in results:
        r_dict = dict(r)
        if isinstance(r_dict.get("metadata"), str):
            try:
                r_dict["metadata"] = json.loads(r_dict["metadata"])
            except:
                pass
        
        # Reescrever paths de anexos para usar o proxy se for hash do Movidesk
        meta = r_dict.get("metadata")
        if isinstance(meta, dict) and meta.get("attachments"):
            for att in meta["attachments"]:
                rel = att.get("relative_path", "")
                m = re.search(r'([A-Fa-f0-9]{32})', rel)
                if m and ("movidesk-files" in rel or not rel.startswith("attachments/")):
                    att["relative_path"] = f"api/image/{m.group(1)}"
                    
        clean_results.append(r_dict)

    from knowledge_access import assessment
    return {"results": clean_results, "evidence_assessment": assessment(clean_results),
            "score_notice": "Pontuação de busca; não representa probabilidade de resposta correta."}


class DraftRequest(BaseModel):
    ticket_id: int | None = None
    subject: str = ""
    problem: str = ""
    top_results: list = []


def _generate_draft(req: DraftRequest):
    """Use the same evidence policy as Movidesk notes and the technician."""
    controls = get_agent_admin_state()
    if not controls.get("draft_generation_enabled", True):
        return JSONResponse(status_code=423, content={"error": "Geração de pré-respostas pausada pelo painel administrativo."})
    from technician_workbench import connection, build_reply
    from knowledge_access import approved_procedures, applicability, quality
    import mapped_replies
    query = f"{req.problem}\n{req.subject}"
    allowed = set(controls.get("source_types", []))
    sources = []
    if 'OFFICIAL_KB' in allowed:
        try:
            with connection() as conn:
                sources = approved_procedures(conn, query)
        except psycopg.Error:
            return JSONResponse(status_code=503, content={"error": "Não foi possível conferir a versão aprovada da fonte."})
    reviewed = mapped_replies.match(query) if 'OFFICIAL_KB' in allowed else None
    if not sources and reviewed:
        sources = [reviewed]
    if not sources:
        # Browser-supplied rows cannot certify their own approval/version.
        # Historical snippets remain leads; operational steps come from the server.
        for supplied in req.top_results:
            if supplied.get('source_type') not in allowed:
                continue
            item = dict(supplied)
            item.update(quality(item))
            item.update(applicability(item, query))
            item['evidence_status'] = 'historical_reference_requires_review'
            if item.get('source_type') == 'REVIEWED_PROCEDURE':
                item['source_type'] = 'PROBABLE_TICKET'
            sources.append(item)
    from support_policy import decide, investigation_reply
    decision = decide(query, sources)
    if not decision['can_propose_procedure']:
        return {'draft': investigation_reply(query, decision), 'needs_human': True, 'decision': decision}
    reply, meta = build_reply({'subject': req.subject}, '', [], [], sources, req.problem,
                              audience='customer_draft', decision_query=query)
    if not meta.get('used'):
        return JSONResponse(status_code=502, content={"error": meta.get('error') or "O provedor não concluiu a geração."})
    return {'draft': reply, 'needs_human': True, 'decision': meta.get('decision'), 'model': meta}


@app.post("/api/draft")
def draft_response(req: DraftRequest):
    controls = get_agent_admin_state()
    if not controls['draft_generation_enabled']:
        return JSONResponse(status_code=423, content={'error': 'Geração de pré-respostas pausada pelo painel administrativo.'})
    if req.ticket_id is not None:
        if req.ticket_id <= 0:
            return JSONResponse(status_code=422, content={'error': 'Número de chamado inválido.'})
        with psycopg.connect(POSTGRES_DSN, row_factory=dict_row) as conn:
            ticket = conn.execute('''SELECT subject FROM refined_tickets
                WHERE external_id=%s LIMIT 1''', (req.ticket_id,)).fetchone()
        if not ticket:
            return JSONResponse(status_code=404, content={'error': 'Chamado não encontrado na base local.'})
        if (req.subject or '').strip().casefold() != (ticket['subject'] or '').strip().casefold():
            return JSONResponse(status_code=422, content={'error': 'O assunto informado não corresponde ao chamado vinculado. Recarregue os dados do chamado.'})
    knowledge_curation.ensure_schema()
    requested_ids = [int(r['id']) for r in req.top_results if isinstance(r, dict) and str(r.get('id','')).isdigit()][:10]
    with psycopg.connect(POSTGRES_DSN, row_factory=dict_row) as conn:
        found = {r['id']:r for r in conn.execute('''SELECT id,source_type,source_external_id,title,problem,solution,
            content_for_embedding,tier,version_reference,modules,manual_category,created_at_source
            FROM ticket_knowledge_nodes WHERE id=ANY(%s)''',(requested_ids,)).fetchall()}
        edits = knowledge_curation.overlays(conn, found.keys())
    sources = []
    for source_id in requested_ids:
        row = found.get(source_id)
        if not row or row['source_type'] not in controls['source_types']: continue
        edit = edits.get(source_id)
        if edit and edit['status']=='excluded': continue
        source = knowledge_curation.apply_overlay(row, edit)
        if not source.get('solution') and source.get('content_for_embedding'):
            source['solution'] = source['content_for_embedding']
        sources.append(source)
    if not sources:
        return JSONResponse(status_code=400, content={'error': 'Nenhuma fonte permitida disponível para esta resposta.'})
    req.top_results = sources
    from knowledge_access import quality
    decision = {
        'ticket_id': req.ticket_id,
        'router': triage_router.route(req.subject, req.problem),
        'sources': [
            {'id': r['id'], 'title': r.get('title'), 'source_type': r.get('source_type'),
             'source_external_id': r.get('source_external_id'),
             'created_at_source': r['created_at_source'].isoformat() if r.get('created_at_source') else None,
             'version_reference': r.get('version_reference'),
             'curation_status': r.get('curation_status'),
             'answer_use': quality(r)['answer_use']}
            for r in sources[:3]
        ],
        'notice': 'Fontes e assuntos sugeridos exigem conferência humana; nenhuma ação foi enviada ao Movidesk.',
    }
    run_id = agent_operations.begin(req.subject, req.problem,
        [{'id': r.get('id'), 'title': r.get('title'), 'source_type': r.get('source_type')} for r in sources[:3]],
        controls, ticket_id=req.ticket_id, decision=decision)
    try:
        result = _generate_draft(req)
        if isinstance(result, dict) and result.get('draft'):
            agent_operations.finish(run_id, draft=result['draft'])
            return {**result, 'run_id': run_id, 'decision': decision}
        agent_operations.finish(run_id, error='Falha na geração. Confira a resposta da requisição e as configurações do provedor.')
        return result
    except Exception:
        agent_operations.finish(run_id, error='Falha interna na geração da resposta.')
        raise


@app.post("/api/feedback")
def feedback_endpoint(fb: FeedbackRequest):
    with psycopg.connect(POSTGRES_DSN, row_factory=dict_row) as conn:
        conn.execute("""
            UPDATE ticket_knowledge_nodes
            SET metadata = metadata || %s::jsonb,
                updated_at = NOW()
            WHERE id = %s;
        """, (json.dumps({"last_human_feedback": fb.feedback_type, "feedback_at": time.strftime("%Y-%m-%d %H:%M:%S")}), fb.node_id))
        conn.commit()
    return {"status": "ok", "node_id": fb.node_id}


def build_diagnostic_thought(state, user_msg: str, reply: str, skills_used: list[str]) -> dict:
    """
    Constrói a trajetória de raciocínio e diagnóstico interno da IA (Chain of Thought)
    para transparência total e inspeção visual no chat do cliente.
    """
    active_hyps = []
    if hasattr(state, "hypotheses") and state.hypotheses:
        for h in sorted(state.hypotheses, key=lambda x: x.confidence, reverse=True)[:5]:
            active_hyps.append({
                "cause": h.cause,
                "confidence_pct": round(h.confidence * 100)
            })

    # Determinar a razão da decisão tomada pela IA
    if state.confirmed_cause:
        decision_reason = (
            f"Causa raiz '{state.confirmed_cause}' confirmada por evidência técnica fatual (Trava T1 satisfeita). "
            f"Prescrevendo o procedimento de resolução oficial no ERP Master."
        )
    elif any(k in user_msg.lower() for k in ["481", "783", "539", "204"]):
        decision_reason = "Código de rejeição SEFAZ identificado. Acionada prescrição da resolução fiscal correspondente no Master."
    elif state.intent == "erro_nota_fiscal":
        decision_reason = (
            "Cliente relatou erro/rejeição na emissão de nota fiscal sem especificar o código retornado pela SEFAZ. "
            "Triagem fiscal acionada para solicitar o código de rejeição ou captura de tela (Ctrl+V) antes de prescrever a solução."
        )
    elif state.intent == "pedido_nao_integrado":
        decision_reason = (
            "Cliente relatou pedido não integrado. Triagem de e-commerce acionada para identificar canal de venda "
            "e verificar se o pedido chegou à AnyMarket ou se gerou ocorrência no Master."
        )
    elif any(k in user_msg.lower() for k in ["como", "faco", "faço", "onde", "cadastr"]) and not any(k in user_msg.lower() for k in ["erro", "falha", "rejei"]):
        decision_reason = "Dúvida de operação/procedimento detectada (How-To). Acionado catálogo de tutoriais oficiais e vídeo do YouTube da Alternativa Sistemas."
    elif state.missing_information:
        missing_str = ", ".join(state.missing_information)
        decision_reason = f"Informações técnicas pendentes ({missing_str}). Solicitando dados para eliminar hipóteses e fechar diagnóstico."
    else:
        decision_reason = "Analisando contexto do cliente para determinar a causa raiz com segurança e empatia."

    return {
        "intent": state.intent or "consulta_geral",
        "module": state.module or "Geral",
        "integration": state.integration or "Direto",
        "confirmed_cause": state.confirmed_cause,
        "missing_information": state.missing_information,
        "hypotheses": active_hyps,
        "decision_reason": decision_reason,
        "skills_used": skills_used
    }


# ---------------------------------------------------------------------------
# MODO CHAT INVESTIGATIVO MULTI-TURN (FASE 2)
# ---------------------------------------------------------------------------

class ChatMessageRequest(BaseModel):
    session_id: str = ""
    message: str


@app.post("/api/chat")
def chat_endpoint(req: ChatMessageRequest):
    """
    Modo Chat Investigativo Multi-Turn com Arquitetura de Skills (Fase 3).
    Atua como Analista de Suporte Sênior do ERP Master (Romário):
      1. Mantém estado central de investigação (InvestigationState)
      2. Executa triagem adaptativa sem perguntas redundantes
      3. Dispara capacidades especializadas via SkillRegistry:
         - Domínio Fiscal (se houver rejeição/chave SEFAZ)
         - Domínio E-commerce (se houver erro de pedido/integração)
         - Raciocínio & Trava T1 (InvestigationSkill)
         - Conhecimento Aterrado / Grounded RAG (GroundedRAGSkill)
         - Apresentação Humanizada / Persona Romário (DialogueSkill)
      4. Retorna skills_used para exibição dinâmica no chat
    """
    user_msg = req.message.strip()
    if not user_msg:
        return JSONResponse(status_code=400, content={"error": "Mensagem vazia."})

    session = session_store.get_or_create(req.session_id)
    session.add_user_turn(user_msg)
    state = session.state

    # 1. Processar triagem adaptativa
    if len(session.turns) == 1:
        triage = triage_engine.analyze_first_message(session, user_msg)
    else:
        triage = triage_engine.process_user_response(session, user_msg)

    # 2. Orquestração de Capacidades Especializadas via SkillRegistry
    registry = get_global_registry()
    skills_used = []

    # A. Domínio Fiscal Especializado
    rej_match = (
        re.search(r'(?:rejei[cç][aã]o|cstat)\s*[:\-]?\s*(\d{3,5})', user_msg, re.I)
        or re.search(r'(?:->|\b)(\d{3,5})\s*[-:]\s*rejei[cç][aã]o', user_msg, re.I)
        or re.search(r'\b(481|783|539|204|629|694|388|1824|10060)\b', user_msg)
    )
    rejection_code = None
    if rej_match:
        rejection_code = rej_match.group(1) if (rej_match.lastindex and rej_match.lastindex >= 1 and rej_match.group(1)) else (rej_match.group(0) if not rej_match.lastindex else None)
    if not rejection_code and any(w in user_msg.lower() for w in ["10060", "timed out", "timeout", "tempo esgotado"]):
        rejection_code = "10060"
    if not rejection_code and "valor do produto difere" in user_msg.lower():
        rejection_code = "629"

    # Se ainda não encontrou na mensagem atual, buscar no histórico de evidências
    if not rejection_code and hasattr(state, "evidence"):
        for ev in state.evidence:
            m_ev = (
                re.search(r'(?:rejei[cç][aã]o|cstat)\s*[:\-]?\s*(\d{3,5})', ev.content, re.I)
                or re.search(r'(\d{3,5})\s*[-:]\s*rejei[cç][aã]o', ev.content, re.I)
                or re.search(r'\b(481|783|539|204|629|694|388|1824|10060)\b', ev.content)
            )
            if m_ev:
                rejection_code = m_ev.group(1) if (m_ev.lastindex and m_ev.lastindex >= 1 and m_ev.group(1)) else (m_ev.group(0) if not m_ev.lastindex else None)
                break

    if rejection_code:
        state.intent = "erro_nota_fiscal"
        state.module = "Fiscal"
        registry.execute_single_capability(
            Capability.DIAGNOSE_FISCAL_REJECTION,
            state,
            context={"rejection_code": rejection_code}
        )
        skills_used.append("domain.fiscal.diagnose_rejection")

    # B. Domínio E-commerce Especializado
    if any(k in user_msg.lower() for k in ["anymarket", "pedido", "integracao", "integrador", "marketplace", "de-para", "shopee", "mercado livre", "vtex"]):
        registry.execute_single_capability(
            Capability.DIAGNOSE_ECOMMERCE_ORDER,
            state,
            context={"ecommerce_error": user_msg}
        )
        skills_used.append("domain.ecommerce.diagnose_order")

    # C. Raciocínio Investigativo & Trava T1 (InvestigationSkill)
    registry.execute_single_capability(
        Capability.EVALUATE_HYPOTHESES,
        state,
        context={"user_message": user_msg, "rejection_code": rejection_code}
    )
    skills_used.append("reasoning.evaluate_hypotheses")

    # D. Conhecimento Aterrado / Grounded RAG (GroundedRAGSkill)
    query_parts = []
    if state.module and state.module not in ("TODOS", "consulta_geral"):
        query_parts.append(state.module)
    if state.integration and state.integration.lower() not in user_msg.lower():
        query_parts.append(state.integration)

    # Se a mensagem atual do usuário for resposta curta, enriquecer com sintomas anteriores
    context_tokens = []
    if hasattr(state, "symptoms"):
        for s in state.symptoms:
            if len(s.strip()) > 5 and s.lower() not in user_msg.lower():
                context_tokens.append(s[:100])
    if hasattr(state, "evidence"):
        for ev in state.evidence:
            if ev.source in ("usuario", "sistema") and any(k in ev.content.lower() for k in ["rejeicao", "rejeição", "erro", "timeout", "falha", "difere", "629", "481"]):
                context_tokens.append(ev.content[:100])

    if context_tokens and len(user_msg.split()) < 10:
        query_parts.extend(context_tokens[:2])

    query_parts.append(user_msg)
    clean_rag_query = " ".join(query_parts).strip()

    rag_skill_res = registry.execute_single_capability(
        Capability.RETRIEVE_KNOWLEDGE,
        state,
        context={"query_text": clean_rag_query, "module": state.module or "TODOS", "user_message": user_msg}
    )
    skills_used.append("knowledge.retrieve")
    rag_data = rag_skill_res.data if rag_skill_res else {}
    top_ranked = rag_data.get("top_ranked", [])
    media_refs_dict = rag_data.get("media_refs", [])
    shortcuts_list = rag_data.get("shortcuts", [])
    context_text = rag_data.get("context_text", "")

    media_refs = [
        MediaRef(
            path=m["path"],
            media_type=m["type"],
            title=m.get("title", ""),
            module=m.get("module", ""),
            screen=m.get("screen", "")
        )
        for m in media_refs_dict
    ]

    # D.1 Tutoriais Oficiais em Vídeo e Animações (GIFs/Prints)
    try:
        from tutorial_catalog import find_matching_tutorial
        from romario_dialogue import RomarioDialogueEngine
        if not RomarioDialogueEngine.is_error_report(user_msg) and not (bool(state.intent) and "erro" in state.intent):
            matched_tut = find_matching_tutorial(user_msg)
            if matched_tut:
                if matched_tut.media_path:
                    if not any(m.path == matched_tut.media_path for m in media_refs):
                        media_refs.insert(0, MediaRef(
                            path=matched_tut.media_path,
                            media_type=matched_tut.media_type,
                            title=matched_tut.media_title or matched_tut.topic,
                            module="Estoque",
                            screen=matched_tut.screen
                        ))
                if matched_tut.shortcuts:
                    for sc in matched_tut.shortcuts:
                        if sc not in shortcuts_list:
                            shortcuts_list.append(sc)
                if "knowledge.tutorials_media" not in skills_used:
                    skills_used.append("knowledge.tutorials_media")
    except Exception:
        pass

    # E. Apresentação Humanizada (DialogueSkill)
    dial_skill_res = registry.execute_single_capability(
        Capability.HUMANIZE_RESPONSE,
        state,
        context={
            "session": session,
            "user_message": user_msg,
            "triage_next_question": triage.next_question if triage.needs_more_info else None,
            "media_refs": media_refs,
            "shortcuts": shortcuts_list,
            "top_candidates": [c.to_generation_dict() if hasattr(c, "to_generation_dict") else c for c in top_ranked] if top_ranked else []
        }
    )
    skills_used.append("presentation.humanize_response")
    reply_text = dial_skill_res.data.get("reply", "") if dial_skill_res else ""

    screen_path = None
    for chunk in top_ranked:
        chunk_screen = getattr(chunk, "screen_path", None)
        if chunk_screen:
            screen_path = chunk_screen
            break

    assistant_turn = session.add_assistant_turn(
        content=reply_text,
        media_refs=media_refs,
        shortcuts=shortcuts_list,
        screen_path=screen_path,
        evidence_classified=[c.to_response_dict() if hasattr(c, "to_response_dict") else c for c in top_ranked]
    )

    resp_dict = session.to_response_dict(assistant_turn)
    resp_dict["skills_used"] = skills_used
    resp_dict["diagnostic_thought"] = build_diagnostic_thought(state, user_msg, reply_text, skills_used)
    return resp_dict


# ---------------------------------------------------------------------------
# ENDPOINTS DE VISÃO COMPUTACIONAL E SIMULADORES CLIENTE / TÉCNICO
# ---------------------------------------------------------------------------

class ChatImageUploadRequest(BaseModel):
    session_id: str = ""
    image_base64: str
    message: str = ""


@app.post("/api/chat/image")
def chat_image_endpoint(req: ChatImageUploadRequest):
    """
    Recebe imagem (screenshot do erro ou tela do Master),
    processa via Gemini 3.6 Flash Vision,
    extrai erros/módulos/telas, adiciona como evidências FATO de fonte 'sistema',
    e dispara o fluxo de diagnóstico e prescrição.
    """
    session = session_store.get_or_create(req.session_id)
    state = session.state
    global GEMINI_API_ACTIVE

    # Limpar header data:image/png;base64,... se presente
    raw_b64 = req.image_base64
    mime_type = "image/png"
    if "," in raw_b64:
        header, raw_b64 = raw_b64.split(",", 1)
        if "jpeg" in header or "jpg" in header:
            mime_type = "image/jpeg"
        elif "webp" in header:
            mime_type = "image/webp"

    try:
        img_bytes = base64.b64decode(raw_b64)
    except Exception as e:
        return JSONResponse(status_code=400, content={"error": f"Base64 inválido: {e}"})

    # =========================================================================
    # Pipeline de Capacidades Especializadas (Fase 3 — Skills Registry)
    # =========================================================================
    registry = get_global_registry()
    skills_used = []

    # 1. Percepção Visual Estruturada (VisionSkill)
    vision_skill_res = registry.execute_single_capability(
        Capability.EXTRACT_ERROR_IMAGE,
        state,
        context={"image_bytes": img_bytes, "mime_type": mime_type, "user_message": req.message}
    )
    skills_used.append("perception.extract_error_image")
    vision_data = vision_skill_res.data if vision_skill_res else {}
    raw_entities = vision_data.get("entities", [])
    rejection_code = vision_data.get("rejection_code")
    module_detected = vision_data.get("module")
    all_errors = [e["value"] for e in raw_entities if e["type"] in ("rejection_code", "ecommerce_error")]

    # Se a imagem ou o texto acompanhante do usuário contém código de erro/rejeição
    if not rejection_code and req.message:
        rej_m = re.search(r'\b(481|783|539|204|694|10060)\b', req.message) or re.search(r'rejei[cç][aã]o\s*[:\-]?\s*(\d{3,5})', req.message, re.I)
        if rej_m:
            rejection_code = rej_m.group(1)
        elif any(w in req.message.lower() for w in ["10060", "timed out", "timeout", "tempo esgotado"]):
            rejection_code = "10060"
        if rejection_code and rejection_code not in all_errors:
            all_errors.append(rejection_code)

    # 2. Resolução de Domínio Especializado
    if rejection_code:
        state.intent = "erro_nota_fiscal"
        state.module = "Fiscal"
        registry.execute_single_capability(
            Capability.DIAGNOSE_FISCAL_REJECTION,
            state,
            context={"rejection_code": rejection_code}
        )
        skills_used.append("domain.fiscal.diagnose_rejection")
    elif any(e["type"] == "ecommerce_error" for e in raw_entities):
        e_err = next(e["value"] for e in raw_entities if e["type"] == "ecommerce_error")
        registry.execute_single_capability(
            Capability.DIAGNOSE_ECOMMERCE_ORDER,
            state,
            context={"ecommerce_error": e_err}
        )
        skills_used.append("domain.ecommerce.diagnose_order")

    # Registrar turno do usuário com a imagem
    summary_text = f"[Screenshot enviado: {', '.join(all_errors) if all_errors else 'Captura de tela'}]"
    user_turn_content = req.message or summary_text
    session.add_user_turn(user_turn_content)

    # Executar triagem no texto do usuário para preencher intenção e hipóteses se necessário
    if req.message:
        if len(session.turns) == 1:
            triage_engine.analyze_first_message(session, req.message)
        else:
            triage_engine.process_user_response(session, req.message)

    # 3. Raciocínio & Trava T1 (InvestigationSkill)
    inv_skill_res = registry.execute_single_capability(
        Capability.EVALUATE_HYPOTHESES,
        state,
        context={"rejection_code": rejection_code, "error_codes": all_errors, "user_message": req.message}
    )
    skills_used.append("reasoning.evaluate_hypotheses")

    # 4. Conhecimento Aterrado (GroundedRAGSkill)
    search_query = f"{state.intent or ''} {state.module or ''} {' '.join(all_errors)} {vision_data.get('summary_observation', '')}".strip()
    rag_skill_res = registry.execute_single_capability(
        Capability.RETRIEVE_KNOWLEDGE,
        state,
        context={"rejection_code": rejection_code, "query_text": search_query}
    )
    skills_used.append("knowledge.retrieve")
    rag_data = rag_skill_res.data if rag_skill_res else {}
    top_ranked = rag_data.get("top_ranked", [])
    media_refs_dict = rag_data.get("media_refs", [])
    shortcuts_list = rag_data.get("shortcuts", [])
    context_text = rag_data.get("context_text", "")

    media_refs = [
        MediaRef(
            path=m["path"],
            media_type=m["type"],
            title=m.get("title", ""),
            module=m.get("module", ""),
            screen=m.get("screen", "")
        )
        for m in media_refs_dict
    ]

    # 5. Apresentação Humanizada (DialogueSkill)
    dial_skill_res = registry.execute_single_capability(
        Capability.HUMANIZE_RESPONSE,
        state,
        context={
            "session": session,
            "user_message": req.message,
            "media_refs": media_refs,
            "shortcuts": shortcuts_list,
            "top_candidates": [c.to_generation_dict() for c in top_ranked] if top_ranked else []
        }
    )
    skills_used.append("presentation.humanize_response")
    reply_text = dial_skill_res.data.get("reply", "") if dial_skill_res else ""

    screen_path = None
    for chunk in top_ranked:
        if chunk.screen_path:
            screen_path = chunk.screen_path
            break

    assistant_turn = session.add_assistant_turn(
        content=reply_text,
        media_refs=media_refs,
        shortcuts=shortcuts_list,
        screen_path=screen_path,
        evidence_classified=[c.to_response_dict() for c in top_ranked]
    )

    resp_dict = session.to_response_dict(assistant_turn)
    resp_dict["skills_used"] = skills_used
    resp_dict["diagnostic_thought"] = build_diagnostic_thought(state, req.message, reply_text, skills_used)
    resp_dict["vision_result"] = {
        "error_codes": all_errors,
        "module": module_detected,
        "entities": raw_entities,
        "summary": vision_data.get("summary_observation", "")
    }
    return resp_dict


class ClientSubmitRequest(BaseModel):
    subject: str
    module: str
    urgency: str
    description: str








class TechActionRequest(BaseModel):
    session_id: str
    action_type: str
    notes: str = ""






@app.get("/api/chat/session/{session_id}")
def get_chat_session(session_id: str):
    s = session_store.get(session_id)
    if not s:
        return JSONResponse(status_code=404, content={"error": "Sessão não encontrada."})
    return {
        "session_id": s.session_id,
        "turns": [
            {
                "role": t.role,
                "content": t.content,
                "media_refs": [
                    {"path": m.path, "type": m.media_type, "title": m.title, "module": m.module, "screen": m.screen}
                    for m in t.media_refs
                ],
                "shortcuts": t.shortcuts,
                "screen_path": t.screen_path
            }
            for t in s.turns
        ],
        "investigation_state": s.state.to_dict()
    }


class GeminiKeyRequest(BaseModel):
    key: str


@app.post("/api/config/gemini_key")
def update_gemini_key_endpoint(req: GeminiKeyRequest):
    global GEMINI_API_KEY, EMBEDDING_API_ACTIVE, GEMINI_API_ACTIVE
    new_key = req.key.strip()
    if not new_key:
        return JSONResponse(status_code=400, content={"error": "A chave não pode ser vazia."})

    test_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.6-flash:generateContent?key={new_key}"
    payload = json.dumps({"contents": [{"parts": [{"text": "ping"}]}]}).encode("utf-8")
    try:
        test_req = urllib.request.Request(test_url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(test_req, timeout=6) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if not data.get("candidates"):
                return JSONResponse(status_code=400, content={"error": "A chave não retornou resposta do Gemini."})
    except urllib.error.HTTPError as e:
        err_msg = e.read().decode("utf-8", errors="replace")
        return JSONResponse(status_code=400, content={"error": f"Erro do Google AI ({e.code}): {err_msg[:160]}"})
    except Exception as e:
        return JSONResponse(status_code=400, content={"error": f"Falha na validação da chave: {e}"})

    GEMINI_API_KEY = new_key
    EMBEDDING_API_ACTIVE = True
    GEMINI_API_ACTIVE = True
    vision_analyzer.api_key = new_key

    # Salvar no .env
    env_file = PROJECT_ROOT / ".env"
    if env_file.exists():
        content = env_file.read_text(encoding="utf-8")
        if "GEMINI_API_KEY=" in content:
            new_content = re.sub(r"GEMINI_API_KEY=.*", f"GEMINI_API_KEY={new_key}", content)
        else:
            new_content = content + f"\nGEMINI_API_KEY={new_key}\n"
        env_file.write_text(new_content, encoding="utf-8")

    return {"status": "ok", "message": "Chave Gemini atualizada e validada com sucesso no Google AI!"}


@app.get("/api/config/gemini_status")
def gemini_status_endpoint():
    has_key = bool(GEMINI_API_KEY)
    masked = f"{GEMINI_API_KEY[:6]}...{GEMINI_API_KEY[-4:]}" if len(GEMINI_API_KEY) > 10 else "Nenhuma"
    return {
        "configured": has_key,
        "masked_key": masked,
        "embedding_active": EMBEDDING_API_ACTIVE,
        "dialogue_engine": "RomarioDialogueEngine (Senior)"
    }


@app.post("/api/chat/reset")
def reset_chat_session():
    new_session = session_store.get_or_create("")
    return {"session_id": new_session.session_id}


class DialogueFeedbackRequest(BaseModel):
    session_id: str = ""
    user_message: str = ""
    assistant_message: str = ""
    flaw_description: str = ""
    expected_response: str = ""


FEEDBACK_RULES_FILE = Path(__file__).parent / "dialogue_feedback_rules.json"


@app.post("/api/feedback/dialogue")
def report_dialogue_feedback(req: DialogueFeedbackRequest):
    """
    Registra reporte de fala inadequada / fora de hora do Romário,
    armazenando o contexto do diálogo para auditoria e ajuste contínuo.
    """
    import datetime
    feedbacks = []
    if FEEDBACK_RULES_FILE.exists():
        try:
            with open(FEEDBACK_RULES_FILE, "r", encoding="utf-8") as f:
                feedbacks = json.load(f)
        except Exception:
            feedbacks = []

    session = session_store.get(req.session_id) if req.session_id else None
    recent_turns = []
    if session:
        recent_turns = [{"role": t.role, "content": t.content} for t in session.turns[-4:]]

    entry = {
        "id": len(feedbacks) + 1,
        "timestamp": datetime.datetime.now().isoformat(),
        "session_id": req.session_id,
        "assistant_message": req.assistant_message,
        "flaw_description": req.flaw_description,
        "expected_response": req.expected_response,
        "recent_turns": recent_turns,
        "state_snapshot": {
            "intent": session.state.intent if session else None,
            "confirmed_cause": session.state.confirmed_cause if session else None,
            "module": session.state.module if session else None,
        } if session else {}
    }
    feedbacks.append(entry)

    with open(FEEDBACK_RULES_FILE, "w", encoding="utf-8") as f:
        json.dump(feedbacks, f, ensure_ascii=False, indent=2)

    return JSONResponse(content={"status": "ok", "message": "Feedback registrado com sucesso!", "entry_id": entry["id"]})


@app.get("/api/feedback/dialogue")
def list_dialogue_feedback():
    if FEEDBACK_RULES_FILE.exists():
        try:
            with open(FEEDBACK_RULES_FILE, "r", encoding="utf-8") as f:
                return JSONResponse(content={"feedbacks": json.load(f)})
        except Exception:
            pass
    return JSONResponse(content={"feedbacks": []})


@app.get("/api/conversations/live")
def get_live_conversations():
    """
    Retorna as conversas ativas em tempo real no simulador,
    permitindo auditoria instantânea do que o usuário enviou e do que o Romário respondeu.
    """
    result = []
    for sid, s in session_store._sessions.items():
        result.append({
            "session_id": sid,
            "turns_count": len(s.turns),
            "state": {
                "intent": s.state.intent,
                "module": s.state.module,
                "confirmed_cause": s.state.confirmed_cause,
                "resolution_status": s.state.resolution_status.value if hasattr(s.state.resolution_status, "value") else str(s.state.resolution_status),
                "hypotheses": [{"cause": h.cause, "confidence": round(h.confidence, 2)} for h in s.state.hypotheses],
                "missing_information": list(s.state.missing_information),
                "evidence_count": len(s.state.evidence),
                "evidence": [{"source": e.source, "content": e.content} for e in s.state.evidence]
            },
            "turns": [
                {
                    "role": t.role,
                    "content": t.content,
                    "media_refs": [m.path for m in t.media_refs] if t.media_refs else []
                }
                for t in s.turns
            ]
        })
    return JSONResponse(content={"total_sessions": len(result), "sessions": result})


# =============================================================================
# API DE INTEGRAÇÃO OFICIAL: ERP MASTER -> DESKCORTEX / MOVIDESK
# =============================================================================
class MasterCompanyInfo(BaseModel):
    cnpj: str | None = None
    corporate_name: str | None = None  # Razão Social
    trade_name: str | None = None      # Nome Fantasia
    client_code: str | None = None     # Código do cliente no Master/Movidesk
    branch: str | None = None          # Código/Nome da Filial

class MasterUserInfo(BaseModel):
    name: str | None = None            # Nome do usuário logado no Master
    email: str | None = None           # E-mail de contato
    username: str | None = None        # Login do sistema
    department: str | None = None      # Setor (ex: Faturamento, Financeiro, Fiscal)

class MasterIntegrationInfo(BaseModel):
    active: list[str] | None = None    # Ex: ["Mercado Livre", "Shopee", "Tray", "TEF", "Banco Inter"]
    current: str | None = None         # Ex: "Mercado Livre" (se o chamado for sobre um canal específico)

class MasterVersionInfo(BaseModel):
    number: str | None = None          # Ex: "12.4.2"
    is_beta: bool = False              # True se o cliente estiver usando versão BETA de testes
    build: str | None = None           # Ex: "2026.08.15"

class MasterServerInfo(BaseModel):
    environment_type: str | None = None # Ex: "Local", "Cloud Alternativa", "VPS", "Terminal Server"
    server_name: str | None = None      # Ex: "SRV-BD-01" ou IP local
    server_ip: str | None = None        # Ex: "192.168.0.17"
    database: str | None = None         # Ex: "Firebird 3.0", "SQL Server"
    os_info: str | None = None          # Ex: "Windows Server 2022"

class MasterSynchronizerInfo(BaseModel):
    name: str | None = None             # Ex: "SINC NUVEMSHOP"
    platform: str | None = None         # Ex: "NUVEMSHOP"
    routines: list[str] | str | None = None # Ex: ["PRODUTO", "PRODUTOPRECO", "ESTOQUE"] ou ["VENDA", "STATUS", "NOTAFISCAL"]
    version: str | None = None

class MasterSystemEnvironment(BaseModel):
    master_version: str | None = None  # Compatibilidade legada
    screen_path: str | None = None     # Ex: "Vendas > Faturamento > NF-e"
    module: str | None = "Fiscal"      # Fiscal, Estoque, Vendas, Financeiro, etc.
    database_type: str | None = None   # Firebird, SQL Server, etc.

class MasterTelemetryLoginRequest(BaseModel):
    client_type: str = "user"           # "user" ou "synchronizer"
    company: MasterCompanyInfo | None = None
    user: MasterUserInfo | None = None
    synchronizer: MasterSynchronizerInfo | None = None
    version: MasterVersionInfo | str | None = None
    server: MasterServerInfo | str | None = None

class MasterTicketIngestRequest(BaseModel):
    subject: str                       # Assunto breve do chamado (ex: "Erro ao emitir NF-e")
    description: str                   # Relato do cliente / dúvida
    error_code: str | None = None      # Ex: "481", "10060", "629" (se já capturado pelo Master)
    raw_error_log: str | None = None   # Mensagem de erro bruta / exception da tela do Master
    urgency: str | None = "normal"     # baixa, normal, alta, urgente
    client_type: str | None = "user"   # "user" ou "synchronizer"

    # OS 5 PILARES PRINCIPAIS SOLICITADOS:
    company: MasterCompanyInfo | None = None
    user: MasterUserInfo | None = None
    synchronizer: MasterSynchronizerInfo | None = None
    integrations: MasterIntegrationInfo | list[str] | str | None = None
    version: MasterVersionInfo | str | None = None
    server: MasterServerInfo | str | None = None

    # Contexto opcional de tela/módulo
    environment: MasterSystemEnvironment | None = None


@app.post(
    "/api/v1/master/telemetry/login",
    tags=["Integração ERP Master"],
    summary="Registro de Login / Telemetria Diária (Usuário ou Sincronizador)"
)
def master_telemetry_login(req: MasterTelemetryLoginRequest):
    """
    Recebe telemetria automática no momento do login no ERP Master:
    1. Login do Usuário: Empresa, Usuário, Versão, IP do Servidor (ex: 192.168.0.17)
    2. Login do Sincronizador: Empresa (EMP_CODIGO), Nome (SINC NUVEMSHOP), Versão, IP, Rotinas Ativas
    """
    comp = req.company.corporate_name if req.company else "Empresa N/A"
    who = req.user.name if req.user else (req.synchronizer.name if req.synchronizer else "Operador")
    ip = req.server.server_ip if (req.server and isinstance(req.server, MasterServerInfo)) else (req.server if isinstance(req.server, str) else "N/A")
    routines = req.synchronizer.routines if req.synchronizer else None
    print(f"--> [Telemetria] Login ({req.client_type}): Empresa: '{comp}' | Entidade: '{who}' | IP: {ip} | Rotinas: {routines}")
    return {
        "success": True,
        "message": f"Telemetria de login ({req.client_type}) registrada com sucesso.",
        "registered_context": {
            "client_type": req.client_type,
            "company": comp,
            "entity": who,
            "server_ip": ip,
            "routines": routines,
            "timestamp": time.time()
        }
    }


@app.post(
    "/api/v1/master/ticket",
    tags=["Integração ERP Master"],
    summary="Abertura e Triagem Inteligente de Chamado Direto do ERP Master"
)
def ingest_master_ticket(req: MasterTicketIngestRequest):
    """
    Endpoint dedicado para o ERP Master abrir chamados automaticamente com os 5 pilares operacionais:
    1. Empresa (Razão Social, Nome Fantasia, CNPJ, Código/Filial)
    2. Usuário (Nome, Login, E-mail, Setor)
    3. Integrações (Canais ativos: Mercado Livre, Shopee, Tray, etc.)
    4. Versão (Número da versão e flag 'is_beta')
    5. Servidor (Tipo: Local, Cloud Alternativa, VPS, Banco Firebird)
    
    Retorna diagnóstico imediato, solução homologada do Romário e payload pronto pro Movidesk.
    """
    comp_name = req.company.corporate_name if req.company else "N/A"
    print(f"--> [Ingest] Recebido chamado do Master: {req.subject} | Empresa: {comp_name}")
    
    # 1. Extração / Normalização de Código de Erro
    error_code = req.error_code
    full_text = f"{req.subject} {req.description} {req.raw_error_log or ''}"
    if not error_code:
        m_code = re.search(r'\b(10060|10061|10054|204|481|783|539|694|629|388|1824|20419)\b', full_text)
        if not m_code:
            m_code = re.search(r'rejei[cç][aã]o\s*[:\-]?\s*(\d{3,5})', full_text, re.I)
        if m_code:
            error_code = m_code.group(1) if m_code.lastindex else m_code.group(0)

    # 1.1 Processamento de Versão & Flag Beta
    ver_str = None
    is_beta = False
    if req.version:
        if isinstance(req.version, str):
            ver_str = req.version
            is_beta = "beta" in req.version.lower()
        elif isinstance(req.version, MasterVersionInfo):
            ver_str = req.version.number
            is_beta = req.version.is_beta or ("beta" in (req.version.number or "").lower())
    elif req.environment and req.environment.master_version:
        ver_str = req.environment.master_version
        is_beta = "beta" in ver_str.lower()

    # 1.2 Processamento de Servidor
    srv_str = None
    if req.server:
        if isinstance(req.server, str):
            srv_str = req.server
        elif isinstance(req.server, MasterServerInfo):
            parts = [req.server.environment_type, req.server.server_name, f"IP: {req.server.server_ip}" if req.server.server_ip else None, req.server.database]
            srv_str = " | ".join([p for p in parts if p])
    elif req.environment and req.environment.database_type:
        srv_str = f"Banco: {req.environment.database_type}"

    # 1.3 Processamento de Integrações
    integ_list = []
    if req.integrations:
        if isinstance(req.integrations, list):
            integ_list = [str(i) for i in req.integrations]
        elif isinstance(req.integrations, str):
            integ_list = [i.strip() for i in req.integrations.split(",") if i.strip()]
        elif isinstance(req.integrations, MasterIntegrationInfo):
            if req.integrations.active:
                integ_list.extend(req.integrations.active)
            if req.integrations.current and req.integrations.current not in integ_list:
                integ_list.append(f"Em foco: {req.integrations.current}")

    # 2. Inicializar Sessão do DeskCortex com os Metadados dos 5 Pilares
    session = session_store.get_or_create("")
    state = session.state

    module = req.environment.module if req.environment and req.environment.module else "Fiscal"
    state.module = module
    state.symptoms.append(f"[{req.urgency.upper()}] {req.subject}: {req.description}")

    # Registrar Fatos Iniciais (Evidências dos 5 Pilares no DeskCortex)
    if req.company:
        comp_str = f"Empresa: {req.company.corporate_name or req.company.trade_name or 'Cliente Master'}"
        if req.company.cnpj:
            comp_str += f" | CNPJ: {req.company.cnpj}"
        if req.company.client_code:
            comp_str += f" | Cód: {req.company.client_code}"
        if req.company.branch:
            comp_str += f" | Filial: {req.company.branch}"
        state.add_evidence(source="sistema", type=EvidenceType.FATO, content=comp_str)

    if req.user:
        user_str = f"Usuário: {req.user.name or req.user.username or 'Operador'}"
        if req.user.email:
            user_str += f" | E-mail: {req.user.email}"
        if req.user.department:
            user_str += f" | Setor: {req.user.department}"
        state.add_evidence(source="sistema", type=EvidenceType.FATO, content=user_str)

    if req.synchronizer:
        sync_routines = req.synchronizer.routines
        r_str = ", ".join(sync_routines) if isinstance(sync_routines, list) else str(sync_routines or "Geral")
        sync_desc = f"Sincronizador: {req.synchronizer.name or 'Sincronizador'} | Plataforma: {req.synchronizer.platform or 'N/A'} | Rotinas: {r_str}"
        state.add_evidence(source="sistema", type=EvidenceType.FATO, content=sync_desc)

    if integ_list:
        state.add_evidence(source="sistema", type=EvidenceType.FATO, content=f"Integrações ERP Master: {', '.join(integ_list)}")

    if ver_str:
        beta_tag = " [VERSÃO BETA]" if is_beta else " [Versão Estável]"
        state.add_evidence(source="sistema", type=EvidenceType.FATO, content=f"Versão ERP Master: {ver_str}{beta_tag}")

    if srv_str:
        state.add_evidence(source="sistema", type=EvidenceType.FATO, content=f"Servidor / Infraestrutura: {srv_str}")

    if req.environment and req.environment.screen_path:
        state.add_evidence(source="sistema", type=EvidenceType.FATO, content=f"Tela / Rota: {req.environment.screen_path}")

    if req.raw_error_log:
        state.add_evidence(source="sistema", type=EvidenceType.FATO, content=f"Log Técnico ERP: {req.raw_error_log[:500]}")

    if error_code:
        state.add_evidence(source="sistema", type=EvidenceType.FATO, content=f"Código de Rejeição Identificado: {error_code}")

    # 3. Executar Busca Híbrida de Conhecimento Aterrado
    query_search = f"{req.subject} {req.description} {error_code or ''}".strip()
    top_results = []
    print(f"--> [Ingest] Iniciando busca híbrida: '{query_search}'")
    try:
        embedding = get_query_embedding(query_search)
        print(f"--> [Ingest] Embedding obtido: {bool(embedding)}")
        with psycopg.connect(POSTGRES_DSN, connect_timeout=3, row_factory=dict_row) as conn:
            results = hybrid_search(conn, query_search, embedding, module=module, top_k=3)
            for r in results:
                top_results.append(dict(r))
    except Exception as e:
        print(f"Erro busca híbrida no ingest: {e}")

    # 4. Triagem Inteligente do Romário
    session.add_user_turn(req.description)
    triage = triage_engine.analyze_first_message(session, req.description)
    print(f"--> [Ingest] Triagem concluída. Intent: {state.intent}")

    # Se há código de erro SEFAZ direto, aplicar diagnóstico fiscal
    registry = get_global_registry()
    if error_code:
        state.intent = "erro_nota_fiscal"
        state.module = "Fiscal"
        registry.execute_single_capability(
            Capability.DIAGNOSE_FISCAL_REJECTION,
            state,
            context={"rejection_code": error_code}
        )

    # Gerar Resposta Inicial do Romário
    user_display_name = req.user.name.split()[0] if (req.user and req.user.name) else "Cliente"
    company_display = req.company.trade_name or req.company.corporate_name if req.company else None
    saudacao = f"Olá, **{user_display_name}**" + (f" da **{company_display}**" if company_display else "") + "! Sou o **Romário**, especialista técnico da Alternativa Sistemas."

    reply_body = RomarioDialogueEngine.generate_response(session, state, triage.next_question, top_candidates=top_results)
    full_reply = f"{saudacao}\n\n{reply_body}"
    print(f"--> [Ingest] Resposta Romário gerada com sucesso.")

    assistant_turn = session.add_assistant_turn(
        content=full_reply,
        shortcuts=top_results[0].get("shortcut_keys", []) if top_results else [],
        screen_path=top_results[0].get("screen_path") if top_results else (req.environment.screen_path if req.environment else None)
    )

    top_solution = top_results[0] if top_results else None
    # Retrieval scores cannot authorize automatic resolution.
    auto_resolvable = False

    # Registrar no TicketStore unificado para Triagem dos Técnicos (/tecnico) e Portal do Cliente (/cliente)
    ticket_id_created = None
    try:
        tk_data = {
            "subject": f"[{req.environment.module if req.environment else 'Suporte'}] {req.subject}",
            "description": req.description,
            "client": {
                "name": req.user.name if req.user else "Operador Master",
                "organization": req.company.trade_name or req.company.corporate_name if req.company else "Cliente Master",
                "email": req.user.email if req.user else "",
                "phone": ""
            },
            "responsible": "Lucas Souza",
            "category": "Análise Técnica",
            "service": f"Módulo {module}",
            "urgency": req.urgency.capitalize() if req.urgency else "Normal",
            "status": "Novo",
            "master_env": {
                "version": ver_str,
                "is_beta": is_beta,
                "server": srv_str,
                "integrations": integ_list
            },
            "attachments": [],
            "ai_internal_note": {
                "diagnosis": f"Triagem DeskCortex ({state.intent}). Causa provável: {state.confirmed_cause or (state.hypotheses[0].cause if state.hypotheses else 'Inconsistência cadastral ou operacional')}.",
                "possible_resolution": (top_results[0].get("solution") if top_results else reply_body),
                "confidence": "Não estimada; referências exigem validação",
                "screen_path": top_results[0].get("screen_path") if top_results else (req.environment.screen_path if req.environment else "ERP Master"),
                "shortcuts": top_results[0].get("shortcut_keys") if top_results else [],
                "references": [top_results[0].get("title")] if top_results else []
            }
        }
        created_tk = ticket_store.create(tk_data)
        ticket_id_created = created_tk["id"]
    except Exception as e:
        print(f"Erro ao salvar ticket no ticket_store: {e}")

    return {
        "success": True,
        "session_id": session.session_id,
        "ticket_id": ticket_id_created,
        "auto_resolvable": auto_resolvable,
        "triage": {
            "intent": state.intent,
            "module": state.module,
            "error_code": error_code,
            "resolution_status": state.resolution_status.value if hasattr(state.resolution_status, "value") else str(state.resolution_status),
            "confirmed_cause": state.confirmed_cause,
            "hypotheses": [{"cause": h.cause, "confidence_pct": round(h.confidence * 100)} for h in state.hypotheses[:3]]
        },
        "romario_reply": full_reply,
        "top_solution": top_solution,
        "recommended_solutions": top_results,
        "movidesk_ticket_ready": {
            "ticket_id": ticket_id_created,
            "subject": f"[{req.environment.module if req.environment else 'Suporte'}] {req.subject}",
            "urgency": req.urgency,
            "client_name": req.company.corporate_name if req.company else None,
            "client_cnpj": req.company.cnpj if req.company else None,
            "created_by_user": req.user.name if req.user else None,
            "master_version": ver_str,
            "is_beta": is_beta,
            "server": srv_str,
            "integrations": integ_list,
            "screen_path": req.environment.screen_path if req.environment else None
        }
    }


# =============================================================================
# API DO MOVIDESK UNIFICADO (/cliente e /tecnico)
# =============================================================================

class ClientTicketOpenRequest(BaseModel):
    company: str
    requester: str
    email: str = ""
    phone: str = ""
    hosting: str = "Vault"
    subject: str
    urgency: str = "Normal"
    description: str
    attachments: list[dict] = []










class TicketActionSaveRequest(BaseModel):
    action_type: str  # 'public' ou 'internal'
    content: str
    author: str = "Lucas Souza (Suporte)"
    time_spent: str = "15 min"
    new_status: str | None = None




# ===========================================================================
# ENDPOINTS: TICKETS SEMELHANTES & FEEDBACK DO TECNICO
# ===========================================================================
import re as _re_similar



def search_historical_tickets(conn, query_text: str, top_k: int = 5) -> list[dict]:
    """
    Busca chamados historicos reais em refined_tickets + raw_tickets
    com rigor estrito: NUNCA traz chamados aleatorios por numeros soltos ou ILIKE frouxo.
    """
    q_low = query_text.lower()

    # 1. Tags XML específicas (ex: vDesc, vProd)
    xml_tags = re.findall(r'\b(v[A-Z][a-zA-Z0-9]+)\b', query_text)

    # 2. Termos compostos fiscais / de erro / operacionais
    key_phrases = []
    if "inutiliz" in q_low or "inutiliza" in q_low:
        key_phrases.append("inutiliz")
    if "cfop" in q_low and ("csosn" in q_low or "sosn" in q_low or "permitido" in q_low):
        key_phrases.append("cfop")
        key_phrases.append("csosn")
    if "destinatario invalida" in q_low or ("destinatario" in q_low and "invalida" in q_low) or ("ie" in q_low and "destinatario" in q_low) or "ie do destinatario" in q_low:
        key_phrases.append("destinatario invalida")
        key_phrases.append("ie")
    if "regime tribut" in q_low or "crt" in q_low:
        key_phrases.append("regime tribut")
        key_phrases.append("crt")
    if "diverge" in q_low or "divergencia" in q_low:
        key_phrases.append("diverge")
    if "vdesc" in q_low or "desconto negativo" in q_low:
        key_phrases.append("vdesc")
        key_phrases.append("desconto")
    if "tdec" in q_low or "atomic type" in q_low:
        key_phrases.append("tdec")
        key_phrases.append("atomic type")

    # 3. Codigos de erro numericos SEFAZ contextualizados
    sefaz_codes = re.findall(r'(?:rejei[cç][aã]o|erro|valida[cç][aã]o)?\s*[:\s-]*\b(\d{3,4})\b', query_text, re.I)

    # Se nao ha tag XML nem frase técnica, extrair termos significativos (sem stopwords)
    if not xml_tags and not key_phrases:
        clean = re.sub(r'[^a-zA-Z0-9áéíóúâêîôûãõçÁÉÍÓÚÂÊÎÔÛÃÕÇ\s]', ' ', query_text)
        stopwords = {
            'para', 'com', 'pelo', 'pela', 'como', 'mais', 'este', 'esta', 'estou', 'estamos', 'favor', 'verificar',
            'fazer', 'faço', 'onde', 'qual', 'quando', 'sobre', 'tenho', 'duvida', 'ajuda', 'uma', 'nota', 'fiscal'
        }
        words = [w for w in clean.split() if len(w) > 3 and w.lower() not in stopwords]
        if words:
            for w in words[:3]:
                key_phrases.append(w)
        elif clean.split():
            key_phrases.append(clean.split()[0])

    if not xml_tags and not key_phrases:
        return []

    conditions = []
    params = []

    # Tags XML específicas
    for tag in xml_tags:
        conditions.append("(r.subject ILIKE %s OR r.problem_statement ILIKE %s)")
        params.extend([f"%{tag}%", f"%{tag}%"])

    # Se for sobre inutilizacao de notas fiscais
    if "inutiliz" in key_phrases:
        conditions.append("(r.subject ILIKE %s OR r.problem_statement ILIKE %s OR r.solution_statement ILIKE %s)")
        params.extend(["%inutiliz%", "%inutiliz%", "%inutiliz%"])

    # Se for sobre CFOP x CSOSN
    elif "cfop" in key_phrases and "csosn" in key_phrases:
        conditions.append("((r.subject ILIKE %s OR r.problem_statement ILIKE %s) AND (r.subject ILIKE %s OR r.problem_statement ILIKE %s))")
        params.extend(["%cfop%", "%cfop%", "%csosn%", "%csosn%"])

    # Se for sobre IE do destinatario invalida
    elif "destinatario invalida" in key_phrases:
        conditions.append("(r.subject ILIKE %s OR r.problem_statement ILIKE %s OR r.subject ILIKE %s)")
        params.extend(["%destinatario invalida%", "%destinatario invalida%", "%ie%invalida%"])

    # Se for sobre divergencia de regime tributario, exigir ambos os conceitos
    elif "regime tribut" in key_phrases and "diverge" in key_phrases:
        conditions.append("((r.subject ILIKE %s OR r.problem_statement ILIKE %s) AND (r.subject ILIKE %s OR r.problem_statement ILIKE %s))")
        params.extend(["%regime tribut%", "%regime tribut%", "%diverge%", "%diverge%"])
    elif key_phrases:
        for kp in key_phrases:
            conditions.append("(r.subject ILIKE %s OR r.problem_statement ILIKE %s)")
            params.extend([f"%{kp}%", f"%{kp}%"])

    if not conditions:
        return []

    where_sql = " OR ".join(conditions)
    sql = f"""
        SELECT r.external_id, r.subject, r.problem_statement, r.created_date,
               r.client_organization_name, rw.payload
        FROM refined_tickets r
        LEFT JOIN raw_tickets rw ON rw.external_id = r.external_id
        WHERE {where_sql}
        ORDER BY r.created_date DESC
        LIMIT {top_k * 3}
    """

    try:
        rows = conn.execute(sql, params).fetchall()
    except Exception as err:
        print(f"[DeskCortex/HistSearch] Erro SQL: {err}")
        return []

    results = []

    for r in rows:
        subj = r.get("subject") or ""
        prob = r.get("problem_statement") or ""
        full_text = f"{subj} {prob}".lower()

        # Calculo de score estrito e honesto baseado em densidade de match
        score = 0

        # Match em tag XML específica (ex: vDesc)
        for tag in xml_tags:
            if tag.lower() in full_text:
                score += 45
                if tag.lower() in subj.lower():
                    score += 25

        # Match em codigo numerico de erro
        for code in sefaz_codes:
            if f" {code} " in f" {full_text} ":
                score += 20

        # Match em frases chave
        for kp in key_phrases:
            if kp.lower() in full_text:
                score += 25
                if kp.lower() in subj.lower():
                    score += 20

        # Casos exatos
        if "cfop" in q_low and ("csosn" in q_low or "sosn" in q_low) and ("cfop" in full_text and "csosn" in full_text):
            score = max(score, 95)
        if ("destinatario invalida" in q_low or "ie" in q_low) and ("destinatario invalida" in full_text or "destinatário invalida" in full_text or "ie invalida" in full_text):
            score = max(score, 95)
        if "regime tribut" in q_low and "diverge" in q_low and "regime tribut" in full_text and "diverge" in full_text:
            score = max(score, 92)
        if "inutiliz" in q_low and "inutiliz" in full_text:
            score = max(score, 95)
        if "vdesc" in q_low and "vdesc" in full_text:
            score = max(score, 90)

        score = min(99, score)

        # CORTE ESTRITO: se score < 65%, DESCARTA totalmente!
        if score < 65:
            continue

        payload = r.get('payload') or {}
        actions = payload.get('actions', [])
        solution_text = ""
        for act in actions:
            desc = act.get('description') or ''
            clean = re.sub(r'<br\s*/?>', '\n', desc)
            clean = re.sub(r'<[^>]+>', ' ', clean)
            clean = re.sub(r'\s+', ' ', clean).strip()
            if any(k in clean for k in ['Resolução:', 'Resoluo:', 'Ações realizadas', 'Ajuste']):
                solution_text = clean
                break
            elif not solution_text and len(clean) > 40:
                solution_text = clean

        summary_solution = ""
        if 'Resolução:' in solution_text:
            summary_solution = solution_text.split('Resolução:')[1].split('Obrigado')[0].split('Obrigada')[0].strip()
        elif 'Resoluo:' in solution_text:
            summary_solution = solution_text.split('Resoluo:')[1].split('Obrigado')[0].split('Obrigada')[0].strip()
        elif 'Ações realizadas' in solution_text:
            summary_solution = solution_text.split('Ações realizadas')[1].split('Obrigado')[0].split('Obrigada')[0].strip()
        else:
            summary_solution = solution_text[:250]

        date_str = r['created_date'].strftime('%d/%m/%Y') if r['created_date'] else ''

        results.append({
            "node_id": r['external_id'],
            "source_type": "TICKET",
            "source_external_id": r['external_id'],
            "title": r['subject'],
            "problem": (r['problem_statement'] or '')[:300],
            "diagnosis": "Atendimento historico real registrado no Movidesk",
            "solution": summary_solution or "Verificar acoes completas no historico do chamado.",
            "screen_path": "Vendas / Nota Fiscal",
            "shortcut_keys": [],
            "modules": ["NFE", "Fiscal"],
            "detected_error_codes": [c for c in sefaz_codes if c in full_text],
            "match_pct": score,
            "date": date_str,
            "tier": "Historico Movidesk",
            "is_helpful": False,
            "technician_comment": ""
        })

    results.sort(key=lambda x: x['match_pct'], reverse=True)
    return results[:top_k]


SIMILAR_SEARCH_CACHE = {}




class SimilarFeedbackRequest(BaseModel):
    node_id: int
    is_helpful: bool
    comment: str = ""
    technician: str = "Lucas Souza"




# ---------------------------------------------------------------------------
# ENDPOINTS: HISTORICO COMPLETO DO MOVIDESK COM IMAGENS REAIS
# ---------------------------------------------------------------------------
HIST_CACHE_DIR = Path(__file__).parent / "output" / "historical_cache"
HIST_CACHE_DIR.mkdir(parents=True, exist_ok=True)
IMG_CACHE_DIR = Path(__file__).parent / "output" / "attachments" / "cache"
IMG_CACHE_DIR.mkdir(parents=True, exist_ok=True)


@app.get("/api/movidesk/image/{file_hash}", summary="Proxy e Cache de Imagens do Movidesk")
def get_movidesk_cached_image(file_hash: str):
    """
    Retorna a imagem do Movidesk a partir do cache local.
    Se nao estiver em cache, baixa em tempo real pelo endpoint oficial de storage e armazena.
    """
    # Checar se ja existe no cache local (.png, .jpg, .jpeg, .gif)
    for ext in [".png", ".jpg", ".jpeg", ".gif", ".webp"]:
        f_path = IMG_CACHE_DIR / f"{file_hash}{ext}"
        if f_path.exists() and f_path.stat().st_size > 0:
            media_type = "image/gif" if ext == ".gif" else ("image/jpeg" if ext in [".jpg", ".jpeg"] else "image/png")
            return FileResponse(f_path, media_type=media_type)

    # Se nao existe, baixar via API oficial de storage do Movidesk
    url = f"{MOVIDESK_BASE_URL}/storage/download?token={MOVIDESK_TOKEN}&id={file_hash}"
    req = urllib.request.Request(url, headers={"User-Agent": "DeskCortex/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = resp.read()
            ct = resp.headers.get("Content-Type", "image/png").lower()
            ext = ".gif" if "gif" in ct else (".jpg" if "jpeg" in ct or "jpg" in ct else ".png")
            save_path = IMG_CACHE_DIR / f"{file_hash}{ext}"
            save_path.write_bytes(data)
            return FileResponse(save_path, media_type=ct)
    except Exception as e:
        print(f"[DeskCortex/ImageProxy] Erro ao baixar imagem {file_hash}: {e}")
        return JSONResponse(status_code=404, content={"error": "Imagem nao encontrada ou expirada"})




@app.get("/api/knowledge/{node_id}", summary="Detalhes Completos do Artigo da Base de Conhecimento")
def get_knowledge_node_detail_endpoint(node_id: int):
    """
    Retorna o conteudo completo do Manual ou Artigo KB, incluindo
    a lista completa de imagens do Master em alta resolucao e passos tecnicos.
    """
    with psycopg.connect(POSTGRES_DSN, connect_timeout=4, row_factory=dict_row) as conn:
        node = conn.execute("SELECT * FROM ticket_knowledge_nodes WHERE id = %s", (node_id,)).fetchone()
        if not node:
            return JSONResponse(status_code=404, content={"error": "Artigo nao encontrado"})

        st = node.get("source_type") or "MANUAL"
        media_meta = node.get("media_metadata") or {}
        images = []
        
        if isinstance(media_meta, dict) and "images" in media_meta:
            for im in media_meta["images"]:
                images.append({
                    "url": f"/manual_media/{im}",
                    "caption": Path(im).name
                })
        elif node.get("media_path"):
            images.append({
                "url": f"/manual_media/{node['media_path']}",
                "caption": Path(node['media_path']).name
            })

        html_content = ""
        if st == "OFFICIAL_KB" and node.get("source_external_id"):
            raw_art = conn.execute("SELECT content_html FROM raw_kb_articles WHERE external_id = %s", (node["source_external_id"],)).fetchone()
            if raw_art and raw_art.get("content_html"):
                html_content = raw_art["content_html"]

        return {
            "id": node["id"],
            "source_type": st,
            "title": node.get("title") or "",
            "problem": node.get("problem") or "",
            "solution": node.get("solution") or "",
            "diagnosis": node.get("diagnosis") or "",
            "screen_path": node.get("screen_path") or "",
            "shortcut_keys": node.get("shortcut_keys") or [],
            "modules": node.get("modules") or [],
            "images": images,
            "html_content": html_content
        }


if __name__ == "__main__":
    print("\n" + "="*70)
    print("DeskCortex - Simulador de Atendimento Ativo!")
    print("Acesse no seu navegador: http://localhost:8000")
    print("="*70 + "\n")
    uvicorn.run(app, host="127.0.0.1", port=8000)
