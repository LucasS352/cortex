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
    ticket_sync_control.start_scheduler()
    try:
        yield
    finally:
        ticket_sync_control.stop_scheduler()


app = FastAPI(title="DeskCortex — Simulador de Atendimento", lifespan=lifespan,
              docs_url=None, redoc_url=None, openapi_url=None)


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


@app.get("/cliente", response_class=HTMLResponse)
def get_cliente_page():
    html_path = Path(__file__).parent / "static" / "movidesk_cliente.html"
    return HTMLResponse(html_path.read_text(encoding="utf-8"))


@app.get("/tecnico", response_class=HTMLResponse)
def get_tecnico_page():
    html_path = Path(__file__).parent / "static" / "movidesk_tecnico.html"
    return HTMLResponse(html_path.read_text(encoding="utf-8"))


@app.get("/", response_class=HTMLResponse)
def root_redirect():
    return HTMLResponse('''
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <title>DeskCortex — Alternativa Sistemas</title>
            <meta http-equiv="refresh" content="0; url=/tecnico">
        </head>
        <body style="font-family: sans-serif; padding: 40px; text-align: center;">
            <h2>Redirecionando para a Mesa do Técnico...</h2>
            <p>Se não for redirecionado automaticamente, escolha:</p>
            <p>
                <a href="/tecnico" style="padding: 10px 20px; background: #0078d4; color: white; text-decoration: none; border-radius: 4px; font-weight: bold; margin-right: 10px;">Acessar /tecnico (Mesa do Técnico)</a>
                <a href="/cliente" style="padding: 10px 20px; background: #107c41; color: white; text-decoration: none; border-radius: 4px; font-weight: bold;">Acessar /cliente (Portal do Cliente)</a>
            </p>
        </body>
        </html>
    ''')


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








@app.get("/", response_class=HTMLResponse)
def index():
    movidesk_file = Path(__file__).parent / "static" / "movidesk_deskcortex.html"
    if movidesk_file.exists():
        return HTMLResponse(content=movidesk_file.read_text(encoding="utf-8"))
    html_content = """<!DOCTYPE html>
<html lang="pt-BR" class="dark">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>DeskCortex — Simulador de Atendimento ERP Master</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://unpkg.com/lucide@latest"></script>
    <script>
        tailwind.config = {
            darkMode: 'class',
            theme: {
                extend: {
                    colors: {
                        brand: { 50: '#eef2ff', 500: '#6366f1', 600: '#4f46e5', 700: '#4338ca', 900: '#1e1b4b' },
                        surface: { 800: '#1e293b', 850: '#172033', 900: '#0f172a', 950: '#090d16' }
                    }
                }
            }
        }
    </script>
    <style>
        .custom-scroll::-webkit-scrollbar { width: 6px; }
        .custom-scroll::-webkit-scrollbar-thumb { background: #334155; border-radius: 4px; }
        .kb-rich-content { color: #e2e8f0; line-height: 1.6; font-size: 0.925rem; }
        .kb-rich-content img { max-width: 100%; height: auto; border-radius: 0.75rem; margin: 1.25rem 0; border: 1px solid #334155; box-shadow: 0 10px 15px -3px rgba(0, 0, 0, 0.5); transition: transform 0.2s; }
        .kb-rich-content img:hover { transform: scale(1.01); border-color: #6366f1; }
        .kb-rich-content iframe { width: 100%; aspect-ratio: 16/9; min-height: 380px; border-radius: 0.75rem; margin: 1.25rem 0; border: 1px solid #334155; box-shadow: 0 10px 15px -3px rgba(0, 0, 0, 0.5); }
        .kb-rich-content a { color: #818cf8; text-decoration: underline; font-weight: 500; }
        .kb-rich-content a:hover { color: #a5b4fc; }
        .kb-rich-content p { margin-bottom: 0.85rem; }
        .kb-rich-content h1, .kb-rich-content h2, .kb-rich-content h3 { color: #ffffff; font-weight: 700; margin-top: 1.5rem; margin-bottom: 0.75rem; }
        .kb-rich-content ul, .kb-rich-content ol { margin-left: 1.5rem; margin-bottom: 0.85rem; list-style-type: disc; }
        .kb-rich-content table { width: 100%; border-collapse: collapse; margin: 1.25rem 0; font-size: 0.85rem; }
        .kb-rich-content th, .kb-rich-content td { border: 1px solid #334155; padding: 0.5rem 0.75rem; }
        .kb-rich-content th { background-color: #1e293b; color: #f8fafc; }
        .ticket-rich-content { color: #cbd5e1; line-height: 1.6; font-size: 0.85rem; }
        .ticket-rich-content img { max-width: 100%; height: auto; border-radius: 0.5rem; margin: 0.75rem 0; border: 1px solid #334155; box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.5); cursor: zoom-in; transition: transform 0.2s; }
        .ticket-rich-content img:hover { border-color: #6366f1; transform: scale(1.01); }
        .ticket-rich-content p { margin-bottom: 0.6rem; }
    </style>


</head>
<body class="bg-surface-950 text-slate-100 min-h-screen flex flex-col font-sans selection:bg-brand-500 selection:text-white">

    <!-- Top Bar -->
    <header class="border-b border-slate-800 bg-surface-900/80 backdrop-blur sticky top-0 z-40 px-6 py-4 flex items-center justify-between">
        <div class="flex items-center gap-3">
            <div class="h-10 w-10 rounded-xl bg-gradient-to-tr from-brand-600 to-indigo-400 flex items-center justify-center shadow-lg shadow-brand-500/20">
                <i data-lucide="brain-circuit" class="w-6 h-6 text-white"></i>
            </div>
            <div>
                <div class="flex items-center gap-2">
                    <h1 class="text-xl font-bold bg-gradient-to-r from-white via-slate-200 to-slate-400 bg-clip-text text-transparent">DeskCortex</h1>
                    <span class="text-xs px-2 py-0.5 rounded-full bg-indigo-500/20 text-indigo-300 font-semibold border border-indigo-500/30">Copilot v1 (RAG + pgvector)</span>
                </div>
                <p class="text-xs text-slate-400">Simulador de Atendimento da Alternativa Sistemas • ERP Master</p>
            </div>
        </div>
        <div class="flex items-center gap-3 text-xs">
            <div class="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-emerald-500/10 border border-emerald-500/30 text-emerald-400">
                <span class="w-2 h-2 rounded-full bg-emerald-400 animate-pulse"></span>
                <span>Base Ativa: <b>29.305 Conhecimentos</b></span>
            </div>
            <button onclick="openApiKeyModal()" class="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-indigo-500/15 hover:bg-indigo-500/25 border border-indigo-500/30 text-indigo-300 font-semibold transition cursor-pointer" title="Configurar ou alterar chave Gemini AI">
                <i data-lucide="key" class="w-3.5 h-3.5 text-indigo-400"></i>
                <span id="header-gemini-status">IA: Romário (Sênior)</span>
            </button>
        </div>
        <a href="/ambientes" class="text-sm font-semibold text-indigo-300 hover:text-white">Ambientes Master</a>
    </header>

    <!-- Tab Navigation -->
    <div class="border-b border-slate-800 bg-surface-950 px-6 py-2 sticky top-[73px] z-30">
        <div class="max-w-7xl mx-auto flex items-center justify-between">
            <div class="flex items-center gap-2">
                <button onclick="switchTab('single')" id="tab-btn-single" class="px-3.5 py-2 rounded-xl text-xs font-semibold flex items-center gap-1.5 bg-indigo-600 text-white shadow-lg shadow-indigo-600/20 transition">
                    <i data-lucide="file-search" class="w-3.5 h-3.5"></i>
                    Fase 1: Chamados Únicos
                </button>
                <button style="display:none" onclick="switchTab('chat')" id="tab-btn-chat" class="px-3.5 py-2 rounded-xl text-xs font-semibold flex items-center gap-1.5 bg-slate-800 text-slate-300 hover:text-white border border-slate-700 transition">
                    <i data-lucide="brain" class="w-3.5 h-3.5 text-emerald-400"></i>
                    Fase 2: Chat Investigativo
                </button>
                <button onclick="switchTab('client')" id="tab-btn-client" class="px-3.5 py-2 rounded-xl text-xs font-semibold flex items-center gap-1.5 bg-slate-800 text-slate-300 hover:text-white border border-slate-700 transition">
                    <i data-lucide="user" class="w-3.5 h-3.5 text-blue-400"></i>
                    👤 Abertura de chamado
                    <span class="px-1.5 py-0.2 rounded text-[9px] bg-blue-500/20 text-blue-300 border border-blue-500/30">Cliente</span>
                </button>
                <button onclick="switchTab('tech')" id="tab-btn-tech" class="px-3.5 py-2 rounded-xl text-xs font-semibold flex items-center gap-1.5 bg-slate-800 text-slate-300 hover:text-white border border-slate-700 transition">
                    <i data-lucide="wrench" class="w-3.5 h-3.5 text-amber-400"></i>
                    🔧 Mesa do Técnico
                    <span class="px-1.5 py-0.2 rounded text-[9px] bg-amber-500/20 text-amber-300 border border-amber-500/30">N1/N2</span>
                </button>
            </div>
            <div class="text-[11px] text-slate-400 hidden sm:flex items-center gap-1.5">
                <span class="w-2 h-2 rounded-full bg-emerald-400"></span>
                <span>Hierarquia: <b>MANUAL (100) &gt; KB (90) &gt; GOLD (85) &gt; SILVER (70)</b></span>
            </div>
        </div>
    </div>

    <!-- Main Container: View Single (Fase 1) -->
    <main id="view-single" class="flex-1 max-w-7xl w-full mx-auto p-6 grid grid-cols-1 lg:grid-cols-12 gap-8">
        
        <!-- Left: Form Simulator (5 cols) -->
        <div class="lg:col-span-5 space-y-6">
            <div class="bg-surface-900 border border-slate-800 rounded-2xl p-6 shadow-xl space-y-5">
                <div class="flex items-center justify-between">
                    <h2 class="text-base font-semibold text-slate-100 flex items-center gap-2">
                        <i data-lucide="ticket-plus" class="w-5 h-5 text-indigo-400"></i>
                        Simulação de Novo Chamado
                    </h2>
                    <span class="text-xs text-slate-400">Relato do Cliente</span>
                </div>

                <!-- Fast Scenarios -->
                <div>
                    <label class="block text-xs font-semibold text-slate-400 mb-2 uppercase tracking-wider">Cenários Rápidos da Alternativa</label>
                    <div class="flex flex-wrap gap-1.5">
                        <button onclick="loadScenario(1)" class="text-xs px-2.5 py-1 rounded-lg bg-slate-800 hover:bg-brand-600/30 text-slate-300 border border-slate-700 transition">🧾 Rejeição 20419 CSOSN</button>
                        <button onclick="loadScenario(2)" class="text-xs px-2.5 py-1 rounded-lg bg-slate-800 hover:bg-brand-600/30 text-slate-300 border border-slate-700 transition">💥 SQL Error -206 VD_HASH</button>
                        <button onclick="loadScenario(3)" class="text-xs px-2.5 py-1 rounded-lg bg-slate-800 hover:bg-brand-600/30 text-slate-300 border border-slate-700 transition">📦 Estoque Tray / Anymarket</button>
                        <button onclick="loadScenario(4)" class="text-xs px-2.5 py-1 rounded-lg bg-slate-800 hover:bg-brand-600/30 text-slate-300 border border-slate-700 transition">🏦 Boleto Bancário CNAB</button>
                    </div>
                </div>

                <!-- Input Subject -->
                <div>
                    <label for="subject" class="block text-xs font-medium text-slate-300 mb-1.5">Assunto do Chamado</label>
                    <input type="text" id="subject" placeholder="Ex: Erro ao emitir NFC-e rejeição 20419" 
                        class="w-full bg-surface-950 border border-slate-700 rounded-xl px-4 py-2.5 text-sm text-white placeholder-slate-500 focus:outline-none focus:border-brand-500 focus:ring-1 focus:ring-brand-500 transition">
                </div>

                <!-- Select Module -->
                <div class="grid grid-cols-2 gap-3">
                    <div>
                        <label for="module" class="block text-xs font-medium text-slate-300 mb-1.5">Módulo ERP (Opcional)</label>
                        <select id="module" class="w-full bg-surface-950 border border-slate-700 rounded-xl px-3 py-2 text-sm text-white focus:outline-none focus:border-brand-500 transition">
                            <option value="TODOS">Todos os Módulos</option>
                            <option value="Fiscal">Fiscal (NF-e, NFC-e, SPED)</option>
                            <option value="Vendas">Vendas / Faturamento</option>
                            <option value="Estoque">Estoque / Produtos</option>
                            <option value="Marketplace">Marketplaces / Sincronizador</option>
                            <option value="Financeiro">Financeiro / Boletos</option>
                            <option value="Banco">Banco de Dados / Firebird</option>
                        </select>
                    </div>
                    <div>
                        <label for="top_k" class="block text-xs font-medium text-slate-300 mb-1.5">Resultados (Top K)</label>
                        <select id="top_k" class="w-full bg-surface-950 border border-slate-700 rounded-xl px-3 py-2 text-sm text-white focus:outline-none focus:border-brand-500 transition">
                            <option value="3">Top 3 mais relevantes</option>
                            <option value="5" selected>Top 5 mais relevantes</option>
                            <option value="10">Top 10 mais relevantes</option>
                        </select>
                    </div>
                </div>

                <!-- Input Problem Body -->
                <div>
                    <label for="problem" class="block text-xs font-medium text-slate-300 mb-1.5">Descrição / Mensagem do Cliente / Log de Erro</label>
                    <textarea id="problem" rows="6" placeholder="Cole aqui o relato que o cliente enviou no ticket ou o log de erro retornado pelo Master..." 
                        class="w-full bg-surface-950 border border-slate-700 rounded-xl px-4 py-3 text-sm text-white placeholder-slate-500 focus:outline-none focus:border-brand-500 focus:ring-1 focus:ring-brand-500 transition custom-scroll"></textarea>
                </div>

                <!-- Submit Button -->
                <button onclick="performDiagnostic()" id="btn-search" 
                    class="w-full bg-gradient-to-r from-brand-600 via-indigo-600 to-indigo-500 hover:from-brand-500 hover:to-indigo-400 text-white font-semibold py-3 px-4 rounded-xl shadow-lg shadow-brand-500/25 flex items-center justify-center gap-2 transition active:scale-[0.99]">
                    <i data-lucide="sparkles" class="w-5 h-5"></i>
                    <span>Diagnosticar com DeskCortex</span>
                </button>

                <!-- Draft Button -->
                <button onclick="generateDraft()" id="btn-draft"
                    class="w-full hidden bg-gradient-to-r from-emerald-600 via-teal-600 to-emerald-500 hover:from-emerald-500 hover:to-teal-400 text-white font-semibold py-3 px-4 rounded-xl shadow-lg shadow-emerald-500/25 flex items-center justify-center gap-2 transition active:scale-[0.99]">
                    <i data-lucide="mail-check" class="w-5 h-5"></i>
                    <span>✍️ Gerar Resposta Pronta para o Cliente</span>
                </button>
            </div>
        </div>

        <!-- Right: Results Feed & AI Response (7 cols) -->
        <div class="lg:col-span-7 flex flex-col space-y-4">
            
            <!-- Draft Response Panel (Exibido no topo do feed de soluções) -->
            <div id="draft-panel" class="hidden bg-surface-900 border-2 border-emerald-500/50 rounded-2xl p-5 shadow-2xl space-y-3">
                <div class="flex items-center justify-between">
                    <div class="flex items-center gap-2.5">
                        <div class="w-8 h-8 rounded-xl bg-emerald-500/20 text-emerald-400 flex items-center justify-center border border-emerald-500/30">
                            <i data-lucide="sparkles" class="w-4 h-4"></i>
                        </div>
                        <div>
                            <h2 class="text-sm font-bold text-white flex items-center gap-2">
                                Sugestão de Resposta ao Cliente (DeskCortex Copilot)
                            </h2>
                            <p class="text-[11px] text-emerald-400">Sintetizado via IA com base nos chamados resolvidos e na Base de Conhecimento</p>
                        </div>
                    </div>
                    <span class="text-xs px-2.5 py-1 rounded-full bg-emerald-500/20 text-emerald-300 font-semibold border border-emerald-500/30">Rascunho Pronto</span>
                </div>

                <div id="draft-loading" class="hidden text-center py-6 text-emerald-400 text-sm space-y-2">
                    <div class="animate-spin inline-block"><i data-lucide="loader-2" class="w-6 h-6 inline"></i></div>
                    <div class="font-medium text-slate-300">Sintetizando histórico e redigindo resposta com o Gemini...</div>
                </div>

                <textarea id="draft-text" rows="11"
                    class="w-full bg-surface-950 border border-slate-700 focus:border-emerald-500 rounded-xl p-4 text-xs text-slate-100 leading-relaxed focus:outline-none focus:ring-1 focus:ring-emerald-500 transition custom-scroll font-sans"
                    placeholder="O rascunho de resposta aparecerá aqui..."></textarea>

                <div class="flex items-center justify-between pt-1">
                    <span class="text-[11px] text-slate-400 flex items-center gap-1">
                        <i data-lucide="info" class="w-3.5 h-3.5 text-slate-500"></i>
                        Revise os dados antes de colar no HelpDesk Movidesk.
                    </span>
                    <div class="flex items-center gap-2">
                        <button onclick="closeDraft()" 
                            class="py-2 px-3 rounded-xl bg-slate-800 hover:bg-slate-700 text-slate-300 border border-slate-700 text-xs font-semibold transition">
                            Fechar
                        </button>
                        <button onclick="copyDraft()" id="btn-copy-draft"
                            class="py-2 px-4 rounded-xl bg-emerald-600 hover:bg-emerald-500 text-white font-semibold text-xs flex items-center gap-1.5 shadow-lg shadow-emerald-600/30 transition">
                            <i data-lucide="copy" class="w-3.5 h-3.5"></i> Copiar Resposta
                        </button>
                    </div>
                </div>
            </div>

            <div class="flex items-center justify-between">
                <h2 class="text-base font-semibold text-slate-100 flex items-center gap-2">
                    <i data-lucide="layers" class="w-5 h-5 text-emerald-400"></i>
                    Soluções e Conhecimento Recomendado
                </h2>
                <div id="status-timing" class="text-xs text-slate-400"></div>
            </div>


            <!-- Empty State -->
            <div id="empty-state" class="flex-1 bg-surface-900 border border-dashed border-slate-800 rounded-2xl flex flex-col items-center justify-center p-12 text-center text-slate-400">
                <div class="h-16 w-16 rounded-2xl bg-slate-800 flex items-center justify-center mb-4 text-slate-500">
                    <i data-lucide="file-search" class="w-8 h-8"></i>
                </div>
                <h3 class="text-base font-semibold text-slate-200 mb-1">Nenhum chamado simulado ainda</h3>
                <p class="text-xs max-w-sm text-slate-400 mb-4">Escolha um dos cenários rápidos ao lado ou digite uma dúvida real do Master para ver a recuperação de conhecimento e os prints em ação.</p>
                <button onclick="loadScenario(1); performDiagnostic();" class="text-xs text-brand-400 hover:text-brand-300 font-medium">Testar Cenário 1: Rejeição 20419 &rarr;</button>
            </div>

            <!-- Loading Spinner -->
            <div id="loading-state" class="hidden flex-1 bg-surface-900 border border-slate-800 rounded-2xl flex flex-col items-center justify-center p-12 text-center">
                <div class="animate-spin text-brand-500 mb-4">
                    <i data-lucide="loader-2" class="w-10 h-10"></i>
                </div>
                <h3 class="text-base font-semibold text-slate-200">Consultando o Cérebro do DeskCortex...</h3>
                <p class="text-xs text-slate-400 mt-1">Gerando embedding 3072d e calculando score híbrido (FTS + pgvector)...</p>
            </div>

            <!-- Results List -->
            <div id="results-container" class="hidden space-y-4"></div>

        </div>

    </main>

    <!-- Chat Investigativo View (Fase 2) -->
    <main id="view-chat" class="hidden flex-1 max-w-7xl w-full mx-auto p-6 grid grid-cols-1 lg:grid-cols-12 gap-8">
        
        <!-- Chat Area (8 cols) -->
        <div class="lg:col-span-8 flex flex-col bg-surface-900 border border-slate-800 rounded-2xl shadow-xl overflow-hidden h-[820px]">
            
            <!-- Chat Header -->
            <div class="px-6 py-4 border-b border-slate-800 bg-surface-950 flex items-center justify-between">
                <div class="flex items-center gap-3">
                    <div class="h-9 w-9 rounded-xl bg-emerald-500/20 border border-emerald-500/30 flex items-center justify-center text-emerald-400">
                        <i data-lucide="bot" class="w-5 h-5"></i>
                    </div>
                    <div>
                        <div class="flex items-center gap-2">
                            <h3 class="text-sm font-bold text-slate-100">Analista Sênior DeskCortex</h3>
                            <span class="text-[10px] px-2 py-0.5 rounded-full bg-emerald-500/20 text-emerald-300 font-semibold border border-emerald-500/30">Investigativo Multi-Turn</span>
                        </div>
                        <p class="text-[11px] text-slate-400">Especialista no ERP Master • Triagem Ativa & Diagnóstico Causal</p>
                    </div>
                </div>
                <div class="flex items-center gap-2">
                    <button onclick="resetChat()" class="px-3 py-1.5 rounded-xl bg-slate-800 hover:bg-slate-700 text-slate-300 border border-slate-700 text-xs font-semibold flex items-center gap-1.5 transition">
                        <i data-lucide="rotate-ccw" class="w-3.5 h-3.5"></i>
                        Nova Investigação
                    </button>
                </div>
            </div>

            <!-- Quick Start Scenarios -->
            <div class="px-6 py-2.5 bg-surface-900/60 border-b border-slate-800/60 flex items-center gap-2 overflow-x-auto text-xs">
                <span class="text-slate-500 text-[11px] whitespace-nowrap font-medium flex items-center gap-1">
                    <i data-lucide="sparkles" class="w-3.5 h-3.5 text-brand-400"></i>
                    Testar sintoma:
                </span>
                <button onclick="sendQuickChat('meu pedido não caiu no master')" class="px-2.5 py-1 rounded-lg bg-slate-800/80 hover:bg-brand-600/20 text-slate-300 border border-slate-700/60 whitespace-nowrap transition">
                    📦 Pedido não caiu no Master
                </button>
                <button onclick="sendQuickChat('rejeição 20419 na nota fiscal')" class="px-2.5 py-1 rounded-lg bg-slate-800/80 hover:bg-brand-600/20 text-slate-300 border border-slate-700/60 whitespace-nowrap transition">
                    🧾 Rejeição 20419 NF-e
                </button>
                <button onclick="sendQuickChat('estoque divergente após importação')" class="px-2.5 py-1 rounded-lg bg-slate-800/80 hover:bg-brand-600/20 text-slate-300 border border-slate-700/60 whitespace-nowrap transition">
                    📊 Estoque Divergente
                </button>
                <button onclick="sendQuickChat('não consigo logar no master erro firebird')" class="px-2.5 py-1 rounded-lg bg-slate-800/80 hover:bg-brand-600/20 text-slate-300 border border-slate-700/60 whitespace-nowrap transition">
                    🔒 Falha Acesso Firebird
                </button>
            </div>

            <!-- Messages Container -->
            <div id="chat-messages" class="flex-1 p-6 overflow-y-auto space-y-4 custom-scroll">
                <!-- Welcome Message -->
                <div class="flex gap-3 items-start">
                    <div class="h-8 w-8 rounded-lg bg-emerald-500/20 border border-emerald-500/30 flex items-center justify-center text-emerald-400 shrink-0 mt-0.5">
                        <i data-lucide="bot" class="w-4 h-4"></i>
                    </div>
                    <div class="flex-1 bg-surface-800 border border-slate-700/60 rounded-2xl rounded-tl-sm p-4 text-xs text-slate-200 shadow-md space-y-2">
                        <p class="font-semibold text-slate-100 text-sm">Olá! Sou o Analista de Suporte Sênior do DeskCortex.</p>
                        <p class="text-slate-300 leading-relaxed">Estou conectado aos manuais técnicos internos e aos procedimentos comprovados do ERP Master. Se algum processo falhou ou você encontrou uma divergência, relate o sintoma que faremos a triagem e investigação ativa juntos!</p>
                        <div class="text-[11px] text-slate-400 bg-surface-900/60 rounded-lg p-2.5 border border-slate-800">
                            💡 <b>Como investigo:</b> Não chuto respostas. Faço apenas as perguntas essenciais para isolar a causa raiz, indico onde olhar no Master e apresento os atalhos e prints exatos da solução.
                        </div>
                    </div>
                </div>
            </div>

            <!-- Typing indicator -->
            <div id="chat-typing" class="hidden px-6 py-2.5 flex items-center gap-2 text-xs text-emerald-400 bg-surface-950/80 border-t border-slate-800">
                <i data-lucide="loader-2" class="w-3.5 h-3.5 animate-spin"></i>
                <span>Analista investigando o Master e consultando manuais oficiais...</span>
            </div>

            <!-- Chat Input Area -->
            <div class="p-4 border-t border-slate-800 bg-surface-950 space-y-2">
                <!-- Preview de imagem anexada -->
                <div id="chat-img-preview-bar" class="hidden flex items-center gap-3 p-2 bg-surface-900 border border-slate-700 rounded-xl">
                    <img id="chat-img-preview-thumb" src="" class="w-12 h-12 object-cover rounded-lg border border-slate-700">
                    <div class="flex-1 min-w-0">
                        <div id="chat-img-preview-name" class="text-xs text-slate-200 font-semibold truncate">print.png</div>
                        <div class="text-[10px] text-emerald-400 flex items-center gap-1">
                            <i data-lucide="eye" class="w-3 h-3"></i>
                            Pronto para análise visual com Gemini 3.6 Flash
                        </div>
                    </div>
                    <button type="button" onclick="clearChatImage()" class="text-slate-400 hover:text-red-400 p-1.5 rounded-lg hover:bg-slate-800">
                        <i data-lucide="x" class="w-4 h-4"></i>
                    </button>
                </div>

                <form id="chat-form" onsubmit="event.preventDefault(); submitChatMessage();" class="flex items-end gap-2.5">
                    <input type="file" id="chat-file-input" accept="image/*" class="hidden" onchange="handleChatImageSelect(event)">
                    <button type="button" onclick="document.getElementById('chat-file-input').click()" title="Anexar print ou foto do erro (ou cole com Ctrl+V)"
                        class="p-3 rounded-xl bg-slate-800 hover:bg-slate-700 text-slate-300 hover:text-white border border-slate-700 transition shrink-0">
                        <i data-lucide="paperclip" class="w-4 h-4 text-brand-400"></i>
                    </button>
                    <textarea id="chat-input" rows="1"
                        placeholder="Digite o sintoma, código de erro ou cole um print com Ctrl+V..."
                        onkeydown="handleChatInputKeydown(event)"
                        oninput="autoResizeTextarea(this)"
                        class="flex-1 bg-surface-900 border border-slate-700 focus:border-emerald-500 rounded-xl px-4 py-3 text-xs text-slate-100 placeholder-slate-500 focus:outline-none focus:ring-1 focus:ring-emerald-500 transition resize-none max-h-32 leading-relaxed"></textarea>
                    <button type="submit" id="btn-chat-send"
                        class="px-5 py-3 rounded-xl bg-emerald-600 hover:bg-emerald-500 text-white font-semibold text-xs flex items-center gap-2 shadow-lg shadow-emerald-600/20 transition shrink-0 h-[42px]">
                        <span>Enviar</span>
                        <i data-lucide="send" class="w-3.5 h-3.5"></i>
                    </button>
                </form>
                <div class="flex items-center justify-between text-[10px] text-slate-500 px-1">
                    <span>💡 Pressione <kbd class="px-1 py-0.5 rounded bg-slate-800 text-slate-400 border border-slate-700">Enter</kbd> para enviar ou <kbd class="px-1 py-0.5 rounded bg-slate-800 text-slate-400 border border-slate-700">Shift+Enter</kbd> para nova linha</span>
                    <span>📸 Suporta <kbd class="px-1 py-0.5 rounded bg-slate-800 text-slate-400 border border-slate-700">Ctrl+V</kbd> para colar print</span>
                </div>
            </div>

        </div>

        <!-- Evidence Engine & State Panel (4 cols) -->
        <div class="lg:col-span-4 space-y-6">
            
            <!-- Investigation Card -->
            <div class="bg-surface-900 border border-slate-800 rounded-2xl p-5 shadow-xl space-y-4">
                <div class="flex items-center justify-between border-b border-slate-800 pb-3">
                    <h3 class="text-xs font-bold text-slate-200 uppercase tracking-wider flex items-center gap-2">
                        <i data-lucide="activity" class="w-4 h-4 text-emerald-400"></i>
                        Estado da Investigação
                    </h3>
                    <span id="state-status-badge" class="px-2 py-0.5 rounded-full text-[10px] font-bold bg-slate-800 text-slate-400 border border-slate-700">
                        AGUARDANDO
                    </span>
                </div>

                <!-- Attributes -->
                <div class="space-y-2 text-xs">
                    <div class="flex justify-between items-center py-1 border-b border-slate-800/40">
                        <span class="text-slate-400">Intenção:</span>
                        <span id="state-intent" class="font-semibold text-slate-200">—</span>
                    </div>
                    <div class="flex justify-between items-center py-1 border-b border-slate-800/40">
                        <span class="text-slate-400">Módulo do Master:</span>
                        <span id="state-module" class="font-semibold text-indigo-300">—</span>
                    </div>
                    <div class="flex justify-between items-center py-1 border-b border-slate-800/40">
                        <span class="text-slate-400">Integração:</span>
                        <span id="state-integration" class="font-semibold text-emerald-400">—</span>
                    </div>
                    <div class="flex justify-between items-center py-1 border-b border-slate-800/40">
                        <span class="text-slate-400">Entidade / Pedido:</span>
                        <span id="state-entity" class="font-mono text-amber-300">—</span>
                    </div>
                </div>

                <!-- Evidence Section -->
                <div class="space-y-2 pt-1">
                    <div class="text-[11px] font-bold text-slate-400 uppercase tracking-wider flex items-center gap-1.5">
                        <i data-lucide="shield-check" class="w-3.5 h-3.5 text-emerald-400"></i>
                        Evidence Engine (Fatos vs Hipóteses)
                    </div>
                    <div id="state-evidence-list" class="space-y-1.5 max-h-48 overflow-y-auto custom-scroll text-xs">
                        <div class="text-[11px] text-slate-500 italic">Nenhuma evidência registrada ainda.</div>
                    </div>
                </div>

                <!-- Cause Confirmed -->
                <div id="confirmed-cause-container" class="hidden pt-2">
                    <div class="p-3 rounded-xl bg-emerald-500/10 border border-emerald-500/30 space-y-1">
                        <div class="text-[10px] font-bold text-emerald-400 uppercase tracking-wider flex items-center gap-1">
                            <i data-lucide="check-circle-2" class="w-3.5 h-3.5"></i>
                            Causa Raiz Confirmada
                        </div>
                        <div id="state-confirmed-cause" class="text-xs font-semibold text-emerald-200"></div>
                    </div>
                </div>

                <!-- Shortcuts Section -->
                <div class="space-y-2 pt-1">
                    <div class="text-[11px] font-bold text-slate-400 uppercase tracking-wider flex items-center gap-1.5">
                        <i data-lucide="command" class="w-3.5 h-3.5 text-indigo-400"></i>
                        Atalhos Relevantes do Sistema
                    </div>
                    <div id="state-shortcuts-list" class="flex flex-wrap gap-1.5">
                        <span class="text-[11px] text-slate-500 italic">Nenhum atalho no momento.</span>
                    </div>
                </div>

            </div>

            <!-- Media Refs Gallery -->
            <div id="media-gallery-card" class="bg-surface-900 border border-slate-800 rounded-2xl p-5 shadow-xl space-y-3">
                <div class="text-xs font-bold text-slate-200 uppercase tracking-wider flex items-center gap-2 border-b border-slate-800 pb-3">
                    <i data-lucide="image" class="w-4 h-4 text-brand-400"></i>
                    Manuais & GIFs do Caso
                </div>
                <div id="state-media-list" class="space-y-2">
                    <div class="text-[11px] text-slate-500 italic">Nenhuma mídia anexada a esta etapa.</div>
                </div>
            </div>

        </div>

    </main>

    <!-- View Client (Simulador de Abertura do Cliente) -->
    <main id="view-client" class="hidden flex-1 max-w-7xl w-full mx-auto p-6 grid grid-cols-1 lg:grid-cols-12 gap-8">
        <!-- Left: Abertura do Chamado (4 cols) -->
        <div class="lg:col-span-4 space-y-6">
            <div class="bg-surface-900 border border-slate-800 rounded-2xl p-6 shadow-xl space-y-4">
                <div class="flex items-center gap-3 border-b border-slate-800 pb-3">
                    <div class="w-10 h-10 rounded-xl bg-blue-500/10 border border-blue-500/30 flex items-center justify-center text-blue-400 shrink-0">
                        <i data-lucide="ticket-plus" class="w-5 h-5"></i>
                    </div>
                    <div>
                        <h2 class="text-sm font-bold text-slate-100">Portal de Atendimento</h2>
                        <p class="text-[11px] text-slate-400">Alternativa Sistemas • Cliente ERP Master</p>
                    </div>
                </div>

                <form id="client-open-form" onsubmit="event.preventDefault(); startClientSession();" class="space-y-3.5 text-xs">
                    <div>
                        <label class="block text-slate-400 font-semibold mb-1">Título / Resumo do Problema</label>
                        <input type="text" id="client-subject" required placeholder="Ex: Pedido não caiu no Master"
                            class="w-full bg-surface-950 border border-slate-700 rounded-xl px-3 py-2 text-slate-200 focus:outline-none focus:border-blue-500">
                    </div>

                    <div class="grid grid-cols-2 gap-3">
                        <div>
                            <label class="block text-slate-400 font-semibold mb-1">Módulo</label>
                            <select id="client-module" class="w-full bg-surface-950 border border-slate-700 rounded-xl px-2.5 py-2 text-slate-200 focus:outline-none focus:border-blue-500">
                                <option value="Ecommerce">E-commerce / Marketplaces</option>
                                <option value="Fiscal">Fiscal / NF-e</option>
                                <option value="Financeiro">Financeiro / Boletos</option>
                                <option value="Estoque">Estoque / Produtos</option>
                                <option value="Configuracoes">Acesso / Usuários</option>
                                <option value="Vendas">Vendas / PDV</option>
                            </select>
                        </div>
                        <div>
                            <label class="block text-slate-400 font-semibold mb-1">Urgência</label>
                            <select id="client-urgency" class="w-full bg-surface-950 border border-slate-700 rounded-xl px-2.5 py-2 text-slate-200 focus:outline-none focus:border-blue-500">
                                <option value="medium">Média</option>
                                <option value="high">Alta</option>
                                <option value="critical">Crítica (Operação Parada)</option>
                                <option value="low">Baixa (Dúvida)</option>
                            </select>
                        </div>
                    </div>

                    <div>
                        <label class="block text-slate-400 font-semibold mb-1">O que está acontecendo?</label>
                        <textarea id="client-description" rows="3" required placeholder="Descreva o que ocorreu, tela em que estava ou código de erro..."
                            class="w-full bg-surface-950 border border-slate-700 rounded-xl p-3 text-slate-200 focus:outline-none focus:border-blue-500 resize-none"></textarea>
                    </div>

                    <!-- Cenários de Teste Rápidos para o Cliente -->
                    <div class="space-y-1.5 pt-1">
                        <span class="text-[10px] text-slate-500 uppercase font-bold tracking-wider">Preenchimento Rápido:</span>
                        <div class="flex flex-wrap gap-1">
                            <button type="button" onclick="fillClientScenario(1)" class="text-[11px] px-2 py-1 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-300 border border-slate-700">📦 Pedido AnyMarket não caiu</button>
                            <button type="button" onclick="fillClientScenario(2)" class="text-[11px] px-2 py-1 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-300 border border-slate-700">🧾 NF Rejeição 783</button>
                            <button type="button" onclick="fillClientScenario(3)" class="text-[11px] px-2 py-1 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-300 border border-slate-700">💳 Boleto não imprime</button>
                        </div>
                    </div>

                    <button type="submit" id="btn-start-client" class="w-full py-2.5 rounded-xl bg-blue-600 hover:bg-blue-500 text-white font-semibold flex items-center justify-center gap-2 shadow-lg shadow-blue-600/20 transition">
                        <i data-lucide="sparkles" class="w-4 h-4"></i>
                        <span>Abrir chamado</span>
                    </button>
                </form>
            </div>

            <p class="text-sm text-slate-400">Descreva o problema e envie o chamado. A equipe técnica analisará sua solicitação.</p>
        </div>
        <section class="lg:col-span-8 p-6 rounded-xl border border-slate-700">
            <h2 class="font-bold">Confirmação de abertura</h2>
            <p id="client-receipt" role="status" class="my-4 text-sm">Seu protocolo aparecerá aqui após o envio.</p>
        </section>
    </main>

    <!-- View Tech (Mesa do Técnico de Suporte N1/N2) -->
    <main id="view-tech" class="hidden flex-1 max-w-7xl w-full mx-auto p-6 space-y-6">
        <section class="p-4 rounded-xl border border-slate-700 bg-surface-800">
            <h2 class="font-bold">Fila de chamados do cliente</h2>
            <button id="tech-analyze" onclick="analyzeSelectedTicket()" class="my-3 p-2 rounded bg-indigo-600 text-sm">Gerar sugestão interna com IA</button>
            <p id="tech-analysis-status" role="status" class="text-xs"></p>
            <p id="queue-status" class="text-xs text-slate-400 my-2">Atualização automática a cada 5 segundos. Sessões locais temporárias.</p>
            <div id="client-ticket-queue" class="flex flex-wrap gap-2"></div>
        </section>
        <!-- Top Toolbar -->
        <div class="bg-surface-900 border border-slate-800 rounded-2xl p-4 shadow-xl flex flex-wrap items-center justify-between gap-4">
            <div class="flex items-center gap-3">
                <div class="w-10 h-10 rounded-xl bg-amber-500/10 border border-amber-500/30 flex items-center justify-center text-amber-400 shrink-0">
                    <i data-lucide="wrench" class="w-5 h-5"></i>
                </div>
                <div>
                    <h2 class="text-sm font-bold text-white flex items-center gap-2">
                        Mesa do Especialista de Suporte
                        <span class="px-2 py-0.2 rounded-full text-[10px] bg-amber-500/20 text-amber-300 border border-amber-500/30">N1 / N2</span>
                    </h2>
                    <p class="text-[11px] text-slate-400">Dossiê Investigativo Gerado pelo DeskCortex para Tomada de Decisão Humana</p>
                </div>
            </div>

            <div class="flex items-center gap-3">
                <button type="button" onclick="loadLatestTechCase()" class="px-3.5 py-2 rounded-xl text-xs font-semibold bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700 flex items-center gap-2 transition">
                    <i data-lucide="refresh-cw" class="w-3.5 h-3.5 text-amber-400"></i>
                    <span>Atualizar com Última Investigação</span>
                </button>
            </div>
        </div>

        <!-- 3-Column Diagnostic Workspace -->
        <div class="grid grid-cols-1 lg:grid-cols-12 gap-6">
            <!-- Col 1: Dossiê do Chamado (3 cols) -->
            <div class="lg:col-span-3 space-y-5">
                <div class="bg-surface-900 border border-slate-800 rounded-2xl p-5 shadow-xl space-y-4">
                    <h3 class="text-xs font-bold text-slate-200 uppercase tracking-wider border-b border-slate-800 pb-2.5 flex items-center gap-2">
                        <i data-lucide="file-text" class="w-4 h-4 text-blue-400"></i>
                        Dados do Chamado
                    </h3>
                    <div class="space-y-3 text-xs">
                        <div>
                            <span class="text-slate-400 block text-[10px] uppercase">Protocolo / Sessão</span>
                            <span id="tech-session-id" class="font-mono text-slate-200 font-bold break-all">—</span>
                        </div>
                        <div>
                            <span class="text-slate-400 block text-[10px] uppercase">Módulo / Integração</span>
                            <div class="flex items-center gap-2 mt-0.5">
                                <span id="tech-module-badge" class="px-2 py-0.5 rounded bg-slate-800 text-slate-300 font-semibold border border-slate-700">—</span>
                                <span id="tech-integration-badge" class="px-2 py-0.5 rounded bg-slate-800 text-slate-300 font-semibold border border-slate-700">—</span>
                            </div>
                        </div>
                        <div>
                            <span class="text-slate-400 block text-[10px] uppercase">Entidade Afetada</span>
                            <span id="tech-entity-badge" class="text-slate-200 font-semibold">—</span>
                        </div>
                        <div>
                            <span class="text-slate-400 block text-[10px] uppercase">Sintomas Relatados</span>
                            <div id="tech-symptoms-list" class="text-slate-300 bg-surface-950 p-2.5 rounded-xl border border-slate-800 mt-1 max-h-32 overflow-y-auto">
                                Nenhuma sessão ativa.
                            </div>
                        </div>
                    </div>
                </div>

                <!-- Prints enviados pelo cliente com tags Vision -->
                <div class="bg-surface-900 border border-slate-800 rounded-2xl p-5 shadow-xl space-y-3">
                    <h3 class="text-xs font-bold text-slate-200 uppercase tracking-wider border-b border-slate-800 pb-2.5 flex items-center gap-2">
                        <i data-lucide="image" class="w-4 h-4 text-emerald-400"></i>
                        Prints do Cliente & Visão
                    </h3>
                    <div id="tech-vision-prints" class="space-y-2 text-xs text-slate-400">
                        <span class="italic text-[11px] text-slate-500">Nenhum print enviado neste chamado.</span>
                    </div>
                </div>
            </div>

            <!-- Col 2: Cérebro do DeskCortex (5 cols) -->
            <div class="lg:col-span-5 space-y-5">
                <div class="bg-surface-900 border border-slate-800 rounded-2xl p-5 shadow-xl space-y-4">
                    <div class="flex items-center justify-between border-b border-slate-800 pb-2.5">
                        <h3 class="text-xs font-bold text-slate-200 uppercase tracking-wider flex items-center gap-2">
                            <i data-lucide="cpu" class="w-4 h-4 text-emerald-400"></i>
                            Diagnóstico DeskCortex
                        </h3>
                        <span id="tech-resolution-status" class="px-2 py-0.5 rounded-full text-[10px] font-bold bg-slate-800 text-slate-400 border border-slate-700">
                            AGUARDANDO
                        </span>
                    </div>

                    <!-- Causa Confirmada -->
                    <div id="tech-confirmed-cause-card" class="p-3 rounded-xl bg-emerald-500/10 border border-emerald-500/30 space-y-1">
                        <div class="text-[10px] text-emerald-400 font-bold uppercase tracking-wider flex items-center gap-1">
                            <i data-lucide="check-circle" class="w-3.5 h-3.5"></i>
                            Causa Raiz Isolada
                        </div>
                        <div id="tech-confirmed-cause-text" class="text-sm font-bold text-white">Aguardando investigação</div>
                    </div>

                    <!-- Hipóteses Ativas & Eliminadas -->
                    <div class="space-y-2">
                        <span class="text-[11px] font-bold text-slate-400 uppercase tracking-wider block">Hipóteses Avaliadas</span>
                        <div id="tech-hypotheses-list" class="space-y-2"></div>
                    </div>

                    <!-- Procedimento Prescrito -->
                    <div class="space-y-2 pt-2 border-t border-slate-800">
                        <span class="text-[11px] font-bold text-slate-200 uppercase tracking-wider flex items-center gap-1.5">
                            <i data-lucide="clipboard-list" class="w-3.5 h-3.5 text-brand-400"></i>
                            Pré-resposta do Romário — revisão necessária
                        </span>
                        <div id="tech-prescription-box" class="text-xs text-slate-300 bg-surface-950 p-3.5 rounded-xl border border-slate-800 leading-relaxed max-h-48 overflow-y-auto">
                            O procedimento recomendado será gerado assim que o diagnóstico for concluído.
                        </div>
                    </div>

                    <!-- Atalhos e Manuais -->
                    <div id="tech-manuals-box" class="space-y-2 pt-2 border-t border-slate-800"></div>
                </div>
            </div>

            <!-- Col 3: Ações do Técnico & Eficiência (4 cols) -->
            <div class="lg:col-span-4 space-y-5">
                <!-- Ações -->
                <div class="bg-surface-900 border border-slate-800 rounded-2xl p-5 shadow-xl space-y-4">
                    <h3 class="text-xs font-bold text-slate-200 uppercase tracking-wider border-b border-slate-800 pb-2.5 flex items-center gap-2">
                        <i data-lucide="check-square" class="w-4 h-4 text-amber-400"></i>
                        Ações do Especialista
                    </h3>

                    <div class="space-y-2.5">
                        <button type="button" onclick="executeTechAction('approve')" class="w-full py-2.5 px-3 rounded-xl bg-emerald-600 hover:bg-emerald-500 text-white font-semibold text-xs flex items-center justify-center gap-2 shadow-lg shadow-emerald-600/20 transition">
                            <i data-lucide="check" class="w-4 h-4"></i>
                            <span>Registrar Resposta Revisada</span>
                        </button>

                        <button type="button" onclick="executeTechAction('edit')" class="w-full py-2 px-3 rounded-xl bg-slate-800 hover:bg-slate-700 text-slate-200 font-semibold text-xs flex items-center justify-center gap-2 border border-slate-700 transition">
                            <i data-lucide="edit-3" class="w-4 h-4 text-blue-400"></i>
                            <span>Editar Prescrição Manualmente</span>
                        </button>

                        <button type="button" onclick="executeTechAction('request_screenshot')" class="w-full py-2 px-3 rounded-xl bg-slate-800 hover:bg-slate-700 text-slate-200 font-semibold text-xs flex items-center justify-center gap-2 border border-slate-700 transition">
                            <i data-lucide="camera" class="w-4 h-4 text-purple-400"></i>
                            <span>Solicitar Print Específico ao Cliente</span>
                        </button>

                        <button type="button" onclick="executeTechAction('escalate')" class="w-full py-2 px-3 rounded-xl bg-red-500/10 hover:bg-red-500/20 text-red-300 font-semibold text-xs flex items-center justify-center gap-2 border border-red-500/30 transition">
                            <i data-lucide="arrow-up-right" class="w-4 h-4 text-red-400"></i>
                            <span>Escalar para N2 / Desenvolvimento</span>
                        </button>
                    </div>

                    <div>
                        <label class="block text-[10px] text-slate-400 font-semibold uppercase mb-1">Anotações Internas do Técnico</label>
                        <textarea id="tech-notes" rows="2" placeholder="Observações que não aparecem para o cliente..."
                            class="w-full bg-surface-950 border border-slate-700 rounded-xl p-2.5 text-xs text-slate-200 focus:outline-none focus:border-amber-500 resize-none"></textarea>
                    </div>
                </div>

                <!-- Dashboard de Eficiência -->
                <div class="bg-surface-900 border border-slate-800 rounded-2xl p-5 shadow-xl space-y-3">
                    <h3 class="text-xs font-bold text-slate-200 uppercase tracking-wider border-b border-slate-800 pb-2 flex items-center gap-2">
                        <i data-lucide="trending-up" class="w-4 h-4 text-emerald-400"></i>
                        Ganho de Eficiência Estimado
                    </h3>
                    <div class="grid grid-cols-2 gap-3 text-xs">
                        <div class="bg-surface-950 p-3 rounded-xl border border-slate-800 text-center">
                            <span class="text-slate-400 block text-[10px] uppercase">Tempo com IA</span>
                            <span class="text-base font-bold text-emerald-400 font-mono">~2 min</span>
                            <span class="text-[9px] text-slate-500 block">vs ~25 min manual</span>
                        </div>
                        <div class="bg-surface-950 p-3 rounded-xl border border-slate-800 text-center">
                            <span class="text-slate-400 block text-[10px] uppercase">Ping-Pong</span>
                            <span class="text-base font-bold text-blue-400 font-mono">2 turnos</span>
                            <span class="text-[9px] text-slate-500 block">vs 6 a 8 perguntas</span>
                        </div>
                    </div>
                    <div class="text-[11px] text-slate-400 bg-surface-950 p-2.5 rounded-xl border border-slate-800 leading-relaxed">
                        ⚡ <b>Redução de 90%</b> no tempo de isolamento de causa para pedidos não integrados e rejeições fiscais do ERP Master.
                    </div>
                </div>
            </div>
        </div>
    </main>

    <!-- Modal de Visualização de Imagem / Print -->
    <div id="image-modal" class="hidden fixed inset-0 z-[100] bg-black/90 backdrop-blur-md flex items-center justify-center p-4" onclick="closeImageModal()">
        <div class="bg-surface-900 border border-slate-700 rounded-2xl max-w-5xl w-full max-h-[92vh] overflow-hidden flex flex-col shadow-2xl" onclick="event.stopPropagation()">
            <div class="px-5 py-3 border-b border-slate-800 flex items-center justify-between bg-surface-950">
                <div class="flex items-center gap-2">
                    <div class="w-7 h-7 rounded-lg bg-indigo-500/20 border border-indigo-500/30 flex items-center justify-center text-indigo-400">
                        <i data-lucide="image" class="w-4 h-4"></i>
                    </div>
                    <h3 id="modal-title" class="text-sm font-semibold text-slate-200 truncate">Visualizador de Imagem</h3>
                </div>
                <button onclick="closeImageModal()" class="text-slate-400 hover:text-white p-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 transition" title="Fechar (ESC)">
                    <i data-lucide="x" class="w-5 h-5"></i>
                </button>
            </div>
            <div class="flex-1 overflow-auto p-4 flex items-center justify-center bg-black/40">
                <img id="modal-img" src="" alt="Print do chamado" class="max-w-full max-h-[80vh] object-contain rounded-lg shadow-2xl">
            </div>
        </div>
    </div>

    <!-- Modal de Reportar Resposta Inadequada / Feedback de Diálogo -->
    <div id="report-flaw-modal" class="hidden fixed inset-0 z-50 bg-black/80 backdrop-blur-sm flex items-center justify-center p-4" onclick="closeReportFlawModal()">
        <div class="bg-surface-900 border border-amber-500/40 rounded-2xl max-w-xl w-full p-5 flex flex-col shadow-2xl space-y-4" onclick="event.stopPropagation()">
            <div class="flex items-center justify-between border-b border-slate-800 pb-3">
                <div class="flex items-center gap-2 text-amber-400 font-bold text-sm">
                    <span class="text-base">🚩</span>
                    <span>Reportar Fala Inadequada / Ajustar Romário em Tempo Real</span>
                </div>
                <button onclick="closeReportFlawModal()" class="text-slate-400 hover:text-white p-1 rounded-lg hover:bg-slate-800 transition">
                    <i data-lucide="x" class="w-4 h-4"></i>
                </button>
            </div>
            
            <div class="space-y-1.5">
                <span class="text-[11px] text-slate-400 font-semibold block uppercase">Fala do Romário reportada:</span>
                <div id="report-flaw-msg" class="p-2.5 rounded-xl bg-surface-950 border border-slate-800 text-xs text-slate-300 max-h-32 overflow-y-auto leading-relaxed italic font-mono"></div>
            </div>

            <div class="space-y-1">
                <label class="text-xs font-semibold text-slate-300">O que ele falou de errado ou no momento inadequado?</label>
                <textarea id="report-flaw-reason" rows="2" placeholder="Ex: Eu já mandei o print e o erro na mensagem anterior, e ele pediu o erro de novo como se não tivesse visto..." class="w-full bg-surface-950 border border-slate-700 rounded-xl p-2.5 text-xs text-slate-200 placeholder-slate-500 focus:outline-none focus:border-amber-500"></textarea>
            </div>

            <div class="space-y-1">
                <label class="text-xs font-semibold text-slate-300">O que ele deveria ter feito ou falado aqui?</label>
                <textarea id="report-flaw-expected" rows="2" placeholder="Ex: Já diagnosticar timeout da SEFAZ 10060, orientar a aguardar 3 min e consultar na SEFAZ sem ficar reenviando..." class="w-full bg-surface-950 border border-slate-700 rounded-xl p-2.5 text-xs text-slate-200 placeholder-slate-500 focus:outline-none focus:border-amber-500"></textarea>
            </div>

            <div id="report-modal-msg" class="text-xs hidden p-2 rounded-lg"></div>

            <div class="flex justify-end gap-2 pt-2 border-t border-slate-800">
                <button onclick="closeReportFlawModal()" class="px-3.5 py-1.5 rounded-xl bg-surface-800 hover:bg-surface-700 text-xs text-slate-300 transition">Cancelar</button>
                <button onclick="submitDialogueFeedback()" id="btn-submit-feedback" class="px-4 py-1.5 rounded-xl bg-amber-500 hover:bg-amber-400 text-slate-950 font-bold text-xs flex items-center gap-1.5 transition shadow-md shadow-amber-500/20">
                    <span>Salvar & Ajustar IA</span>
                </button>
            </div>
        </div>
    </div>

    <!-- Modal de Visualização do Chamado Completo (Movidesk) -->
    <div id="ticket-modal" class="hidden fixed inset-0 z-50 bg-black/80 backdrop-blur-sm flex items-center justify-center p-4" onclick="closeTicketModal()">
        <div class="bg-surface-900 border border-slate-700 rounded-2xl max-w-5xl w-full max-h-[92vh] overflow-hidden flex flex-col shadow-2xl" onclick="event.stopPropagation()">
            <!-- Header -->
            <div class="px-6 py-4 border-b border-slate-800 flex items-start justify-between bg-surface-950">
                <div class="space-y-1">
                    <div class="flex items-center gap-2">
                        <span id="tm-id-badge" class="px-2.5 py-0.5 rounded-full text-xs font-mono font-bold bg-brand-500/20 text-brand-300 border border-brand-500/30"></span>
                        <span id="tm-status-badge" class="px-2.5 py-0.5 rounded-full text-xs font-semibold"></span>
                        <span id="tm-date" class="text-xs text-slate-400"></span>
                    </div>
                    <h3 id="tm-subject" class="text-base font-bold text-white"></h3>
                    <div id="tm-client" class="text-xs text-slate-400"></div>
                </div>
                <button onclick="closeTicketModal()" class="text-slate-400 hover:text-white p-2 rounded-xl bg-slate-800 hover:bg-slate-700 transition">
                    <i data-lucide="x" class="w-5 h-5"></i>
                </button>
            </div>

            <!-- Body (Timeline) -->
            <div id="tm-body" class="flex-1 overflow-y-auto p-6 space-y-4 custom-scroll bg-surface-900"></div>
        </div>
    </div>


    <!-- Modal de Configuração da Chave Gemini AI -->
    <div id="api-key-modal" class="hidden fixed inset-0 z-50 bg-black/80 backdrop-blur-sm flex items-center justify-center p-4" onclick="closeApiKeyModal()">
        <div class="bg-surface-900 border border-slate-700 rounded-2xl max-w-lg w-full overflow-hidden flex flex-col shadow-2xl" onclick="event.stopPropagation()">
            <div class="px-6 py-4 border-b border-slate-800 flex items-center justify-between bg-surface-950">
                <div class="flex items-center gap-2">
                    <div class="w-8 h-8 rounded-lg bg-indigo-500/20 border border-indigo-500/30 flex items-center justify-center text-indigo-400">
                        <i data-lucide="key" class="w-4 h-4"></i>
                    </div>
                    <div>
                        <h3 class="text-sm font-bold text-white">Configuração Gemini AI Studio</h3>
                        <p class="text-[11px] text-slate-400">Alterne para uma nova chave de API do Google AI Studio</p>
                    </div>
                </div>
                <button onclick="closeApiKeyModal()" class="text-slate-400 hover:text-white p-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 transition">
                    <i data-lucide="x" class="w-4 h-4"></i>
                </button>
            </div>
            <div class="p-6 space-y-4 text-xs">
                <div class="p-3 bg-indigo-950/40 border border-indigo-500/30 rounded-xl text-indigo-200 leading-relaxed">
                    💡 <b>Dica:</b> Crie uma chave gratuita em <a href="https://aistudio.google.com/app/apikey" target="_blank" class="underline text-indigo-400 font-semibold">aistudio.google.com</a>. O DeskCortex testa a chave imediatamente antes de salvar no ambiente.
                </div>
                <div>
                    <label class="block text-slate-300 font-semibold mb-1">Chave de API do Gemini:</label>
                    <input type="password" id="input-gemini-key" placeholder="AIzaSy..." class="w-full bg-surface-950 border border-slate-700 rounded-xl px-3.5 py-2.5 text-xs text-white placeholder-slate-500 focus:outline-none focus:border-indigo-500 font-mono">
                </div>
                <div id="key-modal-msg" class="text-xs hidden"></div>
                <div class="flex items-center justify-end gap-2 pt-2">
                    <button onclick="closeApiKeyModal()" class="px-3.5 py-2 rounded-xl bg-slate-800 text-slate-300 hover:text-white transition">Cancelar</button>
                    <button onclick="saveApiKey()" id="btn-save-key" class="px-4 py-2 rounded-xl bg-indigo-600 hover:bg-indigo-500 text-white font-semibold flex items-center gap-1.5 shadow transition">
                        <i data-lucide="check" class="w-4 h-4"></i>
                        <span>Validar & Salvar</span>
                    </button>
                </div>
            </div>
        </div>
    </div>


    <script>
        lucide.createIcons();

        function openApiKeyModal() {
            document.getElementById('api-key-modal').classList.remove('hidden');
            document.getElementById('input-gemini-key').focus();
        }

        function closeApiKeyModal() {
            document.getElementById('api-key-modal').classList.add('hidden');
        }

        async function saveApiKey() {
            const key = document.getElementById('input-gemini-key').value.trim();
            if (!key) return;
            const btn = document.getElementById('btn-save-key');
            const msg = document.getElementById('key-modal-msg');
            btn.disabled = true;
            btn.innerHTML = '<i data-lucide="loader-2" class="w-4 h-4 animate-spin"></i><span>Validando no Google AI...</span>';
            lucide.createIcons();
            msg.classList.add('hidden');
            try {
                const resp = await fetch('/api/config/gemini_key', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ key })
                });
                const data = await resp.json();
                if (resp.ok) {
                    msg.className = "text-xs p-2.5 rounded-lg bg-emerald-500/20 text-emerald-300 border border-emerald-500/30";
                    msg.innerText = data.message || "Chave atualizada e validada!";
                    msg.classList.remove('hidden');
                    document.getElementById('header-gemini-status').innerText = "IA: Gemini Ativo";
                    setTimeout(closeApiKeyModal, 1500);
                } else {
                    msg.className = "text-xs p-2.5 rounded-lg bg-rose-500/20 text-rose-300 border border-rose-500/30";
                    msg.innerText = data.error || "Erro ao validar chave.";
                    msg.classList.remove('hidden');
                }
            } catch(err) {
                msg.className = "text-xs p-2.5 rounded-lg bg-rose-500/20 text-rose-300 border border-rose-500/30";
                msg.innerText = "Erro de conexão: " + err.message;
                msg.classList.remove('hidden');
            } finally {
                btn.disabled = false;
                btn.innerHTML = '<i data-lucide="check" class="w-4 h-4"></i><span>Validar & Salvar</span>';
                lucide.createIcons();
            }
        }

        const scenarios = {
            1: {
                subject: "Rejeição 20419 ao emitir NFC-e no Caixa",
                module: "Fiscal",
                problem: "Bom dia, ao tentar emitir cupom fiscal para consumidor no PDV, o sistema retorna rejeição 20419: O CSOSN não foi informado no item da nota. Já revisamos o cadastro da empresa e não encontramos onde alterar."
            },
            2: {
                subject: "Dynamic SQL Error -206 Column unknown VD_HASH",
                module: "Banco",
                problem: "Ocorreu erro durante a baixa de pedido integrado com o marketplace: Dynamic SQL Error SQL error code = -206 Column unknown VD_HASH at line 5 column 7. Pedido não integra e trava o sincronizador."
            },
            3: {
                subject: "Estoque no Master não atualiza no marketplace Tray",
                module: "Marketplace",
                problem: "Cliente entrou em contato reclamando que fez entrada de nota com 50 unidades do produto SKU 9923, mas na Tray continua zerado há mais de 3 horas. Sincronizador ativo e sem log de erro aparente."
            },
            4: {
                subject: "Erro ao gerar remessa CNAB 240 Banco Itaú",
                module: "Financeiro",
                problem: "Ao tentar gerar o arquivo de remessa de boletos para o Itaú, ocorre erro de campo obrigatório da conta corrente não preenchido ou agência inválida."
            }
        };

        function loadScenario(id) {
            const sc = scenarios[id];
            if (sc) {
                document.getElementById('subject').value = sc.subject;
                document.getElementById('module').value = sc.module;
                document.getElementById('problem').value = sc.problem;
            }
        }

        async function performDiagnostic() {
            const subject = document.getElementById('subject').value.trim();
            const problem = document.getElementById('problem').value.trim();
            const module = document.getElementById('module').value;
            const top_k = parseInt(document.getElementById('top_k').value);

            if (!subject && !problem) {
                alert("Por favor, preencha o assunto ou a mensagem do chamado.");
                return;
            }

            document.getElementById('empty-state').classList.add('hidden');
            document.getElementById('results-container').classList.add('hidden');
            document.getElementById('loading-state').classList.remove('hidden');

            const t0 = performance.now();

            try {
                const response = await fetch('/api/search', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ subject, problem, module, top_k })
                });

                const data = await response.json();
                const elapsed = (performance.now() - t0).toFixed(0);
                const results = data.results || [];
                document.getElementById('status-timing').innerText = `Recuperado em ${elapsed}ms (${results.length} resultados)`;

                renderResults(results);

                // Armazenar resultados para geração de rascunho
                window._lastResults = results;
                if (results.length > 0) {
                    document.getElementById('btn-draft').classList.remove('hidden');
                    document.getElementById('draft-panel').classList.add('hidden');
                    document.getElementById('draft-text').value = '';
                }
            } catch (err) {
                alert("Erro ao consultar o DeskCortex: " + err);

            } finally {
                document.getElementById('loading-state').classList.add('hidden');
            }
        }

        async function generateDraft() {
            const subject = document.getElementById('subject').value.trim();
            const problem = document.getElementById('problem').value.trim();
            const results = window._lastResults || [];
            if (!results.length) { alert("Faça uma busca primeiro!"); return; }

            document.getElementById('btn-draft').classList.add('hidden');
            document.getElementById('draft-panel').classList.remove('hidden');
            document.getElementById('draft-loading').classList.remove('hidden');
            document.getElementById('draft-text').value = '';
            lucide.createIcons();

            try {
                const res = await fetch('/api/draft', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ subject, problem, top_results: results.slice(0,3) })
                });
                const data = await res.json();
                if (data.draft) {
                    document.getElementById('draft-text').value = data.draft;
                } else {
                    document.getElementById('draft-text').value = "Erro ao gerar rascunho: " + (data.error || "Tente novamente.");
                }
            } catch(e) {
                document.getElementById('draft-text').value = "Erro de conexão: " + e.message;
            } finally {
                document.getElementById('draft-loading').classList.add('hidden');
            }
        }

        function copyDraft() {
            const txt = document.getElementById('draft-text').value;
            if (!txt) return;
            navigator.clipboard.writeText(txt).then(() => {
                const btn = document.getElementById('btn-copy-draft') || event.target.closest('button');
                const orig = btn.innerHTML;
                btn.innerHTML = '<i data-lucide="check" class="w-4 h-4"></i> Copiado com Sucesso!';
                lucide.createIcons();
                setTimeout(() => { btn.innerHTML = orig; lucide.createIcons(); }, 2000);
            });
        }

        function closeDraft() {
            document.getElementById('draft-panel').classList.add('hidden');
            document.getElementById('btn-draft').classList.remove('hidden');
        }


        function renderResults(results) {
            const container = document.getElementById('results-container');
            container.innerHTML = '';

            if (!results || results.length === 0) {
                container.innerHTML = '<div class="p-6 text-center text-slate-400 bg-surface-900 rounded-2xl border border-slate-800">Nenhuma recomendação encontrada para estes termos.</div>';
                container.classList.remove('hidden');
                return;
            }

            results.forEach((item, index) => {
                const card = document.createElement('div');
                card.className = "bg-surface-900 border border-slate-800 rounded-2xl p-5 shadow-lg space-y-4 transition hover:border-slate-700";

                // Tier Header
                let tierBadge = "";
                let authorityBadge = `<span class="text-xs px-2 py-0.5 rounded bg-slate-800 text-slate-300 font-mono">Autoridade: ${item.source_authority}</span>`;
                
                if (item.source_type === "OFFICIAL_KB") {
                    tierBadge = `<span class="inline-flex items-center gap-1.5 text-xs px-2.5 py-1 rounded-full bg-blue-500/10 text-blue-400 font-semibold border border-blue-500/30">📘 Base de Conhecimento Oficial</span>`;
                } else if (item.source_type === "MANUAL") {
                    tierBadge = `<span class="inline-flex items-center gap-1.5 text-xs px-2.5 py-1 rounded-full bg-emerald-500/10 text-emerald-400 font-semibold border border-emerald-500/30">📖 Manual Master Oficial</span>`;
                } else if (item.tier === "GOLD") {
                    tierBadge = `<span class="inline-flex items-center gap-1.5 text-xs px-2.5 py-1 rounded-full bg-amber-500/10 text-amber-400 font-semibold border border-amber-500/30">🌟 Chamado GOLD (Solução Comprovada)</span>`;
                } else {
                    tierBadge = `<span class="inline-flex items-center gap-1.5 text-xs px-2.5 py-1 rounded-full bg-slate-500/10 text-slate-300 font-semibold border border-slate-600">🥈 Chamado SILVER (Procedimento Sugerido)</span>`;
                }

                const extIdStr = item.source_external_id ? `#${item.source_external_id} • ` : "";
                const similarityScore = Math.min(99.9, item.combined_score * 100).toFixed(1);

                // Data de Criação / Recência
                let dateBadge = "";
                if (item.formatted_date) {
                    const is2026 = item.formatted_date.includes('2026');
                    const is2025 = item.formatted_date.includes('2025');
                    const badgeClass = is2026 
                        ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/30 font-bold' 
                        : (is2025 ? 'bg-indigo-500/10 text-indigo-300 border-indigo-500/30' : 'bg-slate-800 text-slate-400 border-slate-700');
                    dateBadge = `<span class="text-xs px-2 py-0.5 rounded ${badgeClass} font-mono border flex items-center gap-1"><i data-lucide="calendar" class="w-3 h-3"></i> ${item.formatted_date}</span>`;
                }

                // Evidência
                let evidenceHtml = "";
                if (item.validation_evidence && item.validation_evidence.length > 0) {
                    evidenceHtml = `<div class="flex items-center gap-1.5 text-xs text-emerald-400 bg-emerald-500/10 px-2.5 py-1 rounded-lg border border-emerald-500/20">
                        <i data-lucide="shield-check" class="w-4 h-4"></i>
                        <span>Evidência: <b>${item.validation_evidence.join(', ')}</b></span>
                    </div>`;
                }

                // Anexos / Imagens
                let attachmentsHtml = "";
                const meta = item.metadata || {};
                const atts = meta.attachments || [];
                const images = atts.filter(a => a.is_image);

                if (images.length > 0) {
                    attachmentsHtml = `
                        <div class="mt-3 pt-3 border-t border-slate-800/80">
                            <label class="block text-xs font-semibold text-indigo-300 mb-2 flex items-center gap-1.5">
                                <i data-lucide="image" class="w-4 h-4"></i>
                                <span>${images.length} Print(s) e Imagens Registradas:</span>
                            </label>
                            <div class="flex flex-wrap gap-2">
                                ${images.map(img => {
                                    let src = img.relative_path || '';
                                    const m = src.match(/([A-Fa-f0-9]{32})/);
                                    if (m && (src.includes('movidesk-files') || !src.includes('attachments/'))) {
                                        src = '/api/image/' + m[1];
                                    } else if (!src.startsWith('/') && !src.startsWith('http')) {
                                        src = '/' + src;
                                    }
                                    const isGif = img.is_gif;
                                    const badge = isGif ? '<span class="absolute top-1 left-1 bg-purple-600 text-[9px] font-bold text-white px-1.5 py-0.5 rounded shadow">GIF</span>' : '';
                                    return `
                                        <div onclick="openImageModal('${src}', '${img.file_name}')" 
                                            class="group relative cursor-pointer rounded-lg overflow-hidden border border-slate-700 hover:border-brand-500 transition w-28 h-20 bg-slate-950 flex items-center justify-center">
                                            <img src="${src}" alt="${img.file_name}" class="w-full h-full object-cover group-hover:scale-105 transition" onerror="this.parentElement.style.display='none'">
                                            ${badge}
                                            <span class="absolute inset-0 bg-black/40 opacity-0 group-hover:opacity-100 flex items-center justify-center text-white transition">
                                                <i data-lucide="maximize-2" class="w-4 h-4"></i>
                                            </span>
                                        </div>
                                    `;
                                }).join('')}
                            </div>

                        </div>
                    `;
                }

                // Error codes badges
                let errorBadges = "";
                if (item.detected_error_codes && item.detected_error_codes.length > 0) {
                    errorBadges = `<div class="flex flex-wrap gap-1 mt-1">` + 
                        item.detected_error_codes.map(c => `<span class="text-[11px] px-2 py-0.5 rounded bg-rose-500/10 text-rose-400 font-mono border border-rose-500/20">Código: ${c}</span>`).join('') +
                        `</div>`;
                }

                card.innerHTML = `
                    <div class="flex items-start justify-between gap-3">
                        <div class="flex flex-wrap items-center gap-2">
                            ${tierBadge}
                            ${authorityBadge}
                            ${dateBadge}
                            <span class="text-xs px-2 py-0.5 rounded bg-brand-500/10 text-brand-300 font-mono">Score: ${similarityScore}%</span>
                        </div>
                        <span class="text-xs text-slate-500 font-mono">Rank #${index + 1}</span>
                    </div>


                    <!-- Card Header -->
                    <div>
                        <h3 class="text-sm font-bold text-white mb-1.5">${extIdStr}${item.title}</h3>
                        ${evidenceHtml}
                        ${errorBadges}
                    </div>

                    <!-- Problema Relatado (se houver) -->
                    ${item.problem && item.problem.length > 10 ? `
                        <div class="bg-surface-950/60 rounded-xl p-3 border border-slate-800/80 text-xs space-y-1">
                            <div class="font-semibold text-slate-400 flex items-center gap-1.5 uppercase tracking-wider text-[11px]">
                                <i data-lucide="help-circle" class="w-3.5 h-3.5 text-amber-400"></i>
                                Problema Relatado
                            </div>
                            <div class="text-slate-300 whitespace-pre-line leading-relaxed line-clamp-3">${item.problem}</div>
                        </div>
                    ` : ''}

                    <!-- Solução / Procedimento -->
                    <div class="bg-surface-950 rounded-xl p-4 border border-slate-800 text-xs space-y-2">
                        <div class="font-semibold text-emerald-400 flex items-center gap-1.5 uppercase tracking-wider text-[11px]">
                            <i data-lucide="check-circle" class="w-4 h-4"></i>
                            Procedimento / Solução Registrada
                        </div>
                        <div class="text-slate-200 whitespace-pre-line leading-relaxed">${item.solution || 'Procedimento registrado no atendimento completo.'}</div>
                        ${(!item.solution || item.solution.length < 40 || item.solution.includes("Analista de Suporte") || item.solution.includes("Obrigado por entrar em contato")) ? `
                            <div class="mt-2 text-[11px] text-amber-300/80 bg-amber-500/10 p-2.5 rounded-lg border border-amber-500/20 flex items-center gap-1.5">
                                <i data-lucide="info" class="w-3.5 h-3.5 flex-shrink-0"></i>
                                <span>Solução executada via conexão remota ou ligação. Clique em <b>"Abrir Chamado Completo"</b> abaixo para ver toda a conversa e ações.</span>
                            </div>
                        ` : ''}
                    </div>

                    ${attachmentsHtml}

                    <!-- Footer: Ação de Abrir Chamado + Feedback HITL -->
                    <div class="flex flex-wrap items-center justify-between gap-3 pt-3 text-xs border-t border-slate-800">
                        <!-- Botão de Chamado Completo -->
                        <div>
                            ${item.source_type === 'OFFICIAL_KB' ? `
                                <button onclick="openFullTicket(null, '${item.source_id}', '${item.source_type}', ${item.id})" 
                                    class="px-3 py-1.5 rounded-lg bg-blue-600/20 hover:bg-blue-600/30 text-blue-300 border border-blue-500/30 text-xs font-semibold flex items-center gap-1.5 transition">
                                    <i data-lucide="book-open" class="w-4 h-4"></i>
                                    <span>Ver Artigo Completo na KB &rarr;</span>
                                </button>
                            ` : item.source_type === 'MANUAL' ? `
                                <button onclick="openManualModal(${item.id})" 
                                    class="px-3 py-1.5 rounded-lg bg-emerald-600/20 hover:bg-emerald-600/30 text-emerald-300 border border-emerald-500/40 text-xs font-semibold flex items-center gap-1.5 transition hover:scale-[1.01]">
                                    <i data-lucide="book-open-check" class="w-4 h-4 text-emerald-400"></i>
                                    <span>Abrir Procedimento Completo do Manual &rarr;</span>
                                </button>
                            ` : `
                                <button onclick="openFullTicket(${item.source_external_id}, '${item.source_id}', '${item.source_type}', ${item.id})" 
                                    class="px-3 py-1.5 rounded-lg bg-indigo-600/20 hover:bg-indigo-600/30 text-indigo-300 border border-indigo-500/30 text-xs font-semibold flex items-center gap-1.5 transition">
                                    <i data-lucide="file-text" class="w-4 h-4"></i>
                                    <span>Abrir Chamado Completo #${item.source_external_id} &rarr;</span>
                                </button>
                            `}
                        </div>

                        <!-- Feedback Buttons -->
                        <div class="flex items-center gap-2">
                            <span class="text-slate-500 text-[11px] hidden sm:inline">Ajudou?</span>
                            <button onclick="sendFeedback(${item.id}, 'APPROVED')" class="px-2.5 py-1 rounded-lg bg-emerald-500/10 hover:bg-emerald-500/20 text-emerald-400 border border-emerald-500/30 flex items-center gap-1 transition">
                                <i data-lucide="thumbs-up" class="w-3.5 h-3.5"></i> Resolveu
                            </button>
                            <button onclick="sendFeedback(${item.id}, 'EDITED')" class="px-2.5 py-1 rounded-lg bg-indigo-500/10 hover:bg-indigo-500/20 text-indigo-300 border border-indigo-500/30 flex items-center gap-1 transition">
                                <i data-lucide="edit-3" class="w-3.5 h-3.5"></i> Ajustar
                            </button>
                            <button onclick="sendFeedback(${item.id}, 'REJECTED')" class="px-2.5 py-1 rounded-lg bg-rose-500/10 hover:bg-rose-500/20 text-rose-400 border border-rose-500/30 flex items-center gap-1 transition">
                                <i data-lucide="thumbs-down" class="w-3.5 h-3.5"></i> Rejeitar
                            </button>
                        </div>
                    </div>
                `;

                container.appendChild(card);
            });

            container.classList.remove('hidden');
            lucide.createIcons();
        }

        async function openManualModal(nodeId) {
            const modal = document.getElementById('ticket-modal');
            const body = document.getElementById('tm-body');
            body.innerHTML = '<div class="flex justify-center p-12"><div class="animate-spin text-emerald-500"><i data-lucide="loader-2" class="w-8 h-8"></i></div></div>';
            modal.classList.remove('hidden');
            lucide.createIcons();

            try {
                const res = await fetch(`/api/manual/${nodeId}`);
                if (!res.ok) {
                    const err = await res.json().catch(() => ({}));
                    throw new Error(err.error || `HTTP ${res.status}`);
                }
                const doc = await res.json();

                document.getElementById('tm-id-badge').innerText = 'MANUAL ERP MASTER';
                document.getElementById('tm-id-badge').className = 'px-2.5 py-0.5 rounded-full text-xs font-mono font-bold bg-emerald-500/20 text-emerald-300 border border-emerald-500/30';
                document.getElementById('tm-status-badge').innerText = 'Procedimento Homologado';
                document.getElementById('tm-status-badge').className = 'px-2.5 py-0.5 rounded-full text-xs font-semibold bg-emerald-500/20 text-emerald-400 border border-emerald-500/30';
                document.getElementById('tm-subject').innerText = doc.title || 'Manual do ERP Master';
                document.getElementById('tm-date').innerText = doc.manual_category ? `Categoria: ${doc.manual_category}` : 'Base de Conhecimento';

                let shortcutsHtml = '';
                if (doc.shortcut_keys && doc.shortcut_keys.length > 0) {
                    shortcutsHtml = ` • Atalhos: ` + doc.shortcut_keys.map(k => `<kbd class="px-1.5 py-0.5 bg-slate-800 text-emerald-400 font-mono text-[11px] rounded border border-slate-700 font-bold">${k}</kbd>`).join(' ');
                }
                let screenHtml = doc.screen_path ? `<span>Caminho: <b class="text-white">${doc.screen_path}</b></span>` : '';
                let modHtml = doc.modules ? `<span>Módulo: <b>${Array.isArray(doc.modules) ? doc.modules.join(', ') : doc.modules}</b></span>` : '';
                document.getElementById('tm-client').innerHTML = `${modHtml} ${screenHtml ? ' • ' + screenHtml : ''} ${shortcutsHtml}`;

                // Imagens e Prints do Manual
                let images = [];
                const meta = doc.media_metadata || {};
                if (meta.images && Array.isArray(meta.images)) {
                    images = meta.images;
                } else if (doc.media_path) {
                    images = [doc.media_path];
                }

                let galleryHtml = '';
                if (images.length > 0) {
                    galleryHtml = `
                        <div class="rounded-xl border border-slate-800 bg-surface-950 p-4 space-y-3">
                            <div class="text-xs font-bold text-indigo-300 flex items-center gap-1.5 uppercase tracking-wider">
                                <i data-lucide="image" class="w-4 h-4"></i>
                                <span>${images.length} Print(s) e Mídias Ilustrativas do Procedimento:</span>
                            </div>
                            <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
                                ${images.map(img => {
                                    const src = `/manual_media/${img}`;
                                    const safeTitle = (doc.title || 'Print do Manual').replace(/'/g, "\\'");
                                    return `
                                        <div onclick="openImageModal('${src}', '${safeTitle}')" 
                                             class="group cursor-pointer rounded-xl overflow-hidden border border-slate-800 bg-slate-950 hover:border-emerald-500/50 transition shadow-lg relative">
                                            <img src="${src}" alt="Print do Manual" class="w-full h-44 object-cover group-hover:scale-105 transition" onerror="this.parentElement.style.display='none'">
                                            <div class="absolute inset-0 bg-black/40 opacity-0 group-hover:opacity-100 flex items-center justify-center text-white transition gap-1.5 text-xs font-semibold">
                                                <i data-lucide="maximize-2" class="w-4 h-4"></i>
                                                <span>Ampliar Print</span>
                                            </div>
                                        </div>
                                    `;
                                }).join('')}
                            </div>
                        </div>
                    `;
                }

                // Passo a passo guiado se houver flow
                let flowHtml = '';
                if (meta.flow && Array.isArray(meta.flow) && meta.flow.length > 0) {
                    flowHtml = `
                        <div class="rounded-xl border border-indigo-500/30 bg-indigo-500/10 p-4 space-y-2">
                            <div class="text-xs font-bold text-indigo-300 flex items-center gap-1.5 uppercase tracking-wider">
                                <i data-lucide="list-ordered" class="w-4 h-4"></i>
                                <span>Fluxo de Navegação no Sistema</span>
                            </div>
                            <ol class="space-y-2 text-xs text-slate-200 list-decimal pl-5 leading-relaxed">
                                ${meta.flow.map(step => `<li>${step}</li>`).join('')}
                            </ol>
                        </div>
                    `;
                }

                // Transformar URLs em links clicáveis
                function linkify(text) {
                    if (!text) return '';
                    return text.replace(/(https?:[/][/][^\\s<]+)/g, '<a href="$1" target="_blank" class="text-indigo-400 underline hover:text-indigo-300 font-semibold inline-flex items-center gap-1">$1 <i data-lucide="external-link" class="w-3 h-3 inline"></i></a>');
                }

                // Detectar se há chamados citados no texto
                let ticketButtonsHtml = '';
                const allText = (doc.problem || '') + ' ' + (doc.solution || '');
                const citedTickets = allText.match(/Ticket[/]Edit[/]([0-9]+)/g);
                if (citedTickets && citedTickets.length > 0) {
                    const uniqueTicketIds = [...new Set(citedTickets.map(t => t.split('/')[2]))];
                    ticketButtonsHtml = `
                        <div class="p-3 bg-indigo-950/40 rounded-xl border border-indigo-500/30 flex flex-wrap items-center gap-2">
                            <span class="text-xs text-indigo-300 font-semibold flex items-center gap-1">
                                <i data-lucide="file-symlink" class="w-4 h-4"></i>
                                Chamado(s) de Referência no Movidesk:
                            </span>
                            ${uniqueTicketIds.map(tid => `
                                <button onclick="openFullTicket(${tid}, '${tid}', 'PROBABLE_TICKET')" 
                                    class="px-2.5 py-1 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-white font-semibold text-xs flex items-center gap-1 shadow transition">
                                    <i data-lucide="file-text" class="w-3.5 h-3.5"></i>
                                    <span>Abrir Chamado #${tid}</span>
                                </button>
                            `).join('')}
                        </div>
                    `;
                }

                body.innerHTML = `
                    <div class="space-y-4">
                        ${ticketButtonsHtml}

                        <!-- Problema / Mensagem de Erro -->
                        ${doc.problem ? `
                            <div class="rounded-xl border border-rose-500/30 bg-rose-500/10 p-4 space-y-2">
                                <div class="text-xs font-bold text-rose-400 flex items-center gap-1.5 uppercase tracking-wide">
                                    <i data-lucide="alert-triangle" class="w-4 h-4"></i>
                                    <span>Problema Identificado / Mensagem de Erro</span>
                                </div>
                                <div class="text-xs text-slate-200 whitespace-pre-line leading-relaxed">${linkify(doc.problem)}</div>
                            </div>
                        ` : ''}

                        <!-- Solução / Procedimento -->
                        <div class="rounded-xl border border-emerald-500/30 bg-emerald-500/10 p-5 space-y-3 shadow-inner">
                            <div class="text-xs font-bold text-emerald-400 flex items-center gap-1.5 uppercase tracking-wide">
                                <i data-lucide="check-circle-2" class="w-4 h-4"></i>
                                <span>Procedimento Homologado de Resolução no ERP Master</span>
                            </div>
                            <div class="text-xs text-slate-100 whitespace-pre-line leading-relaxed text-[13px] font-normal">${linkify(doc.solution || doc.content_for_embedding || 'Procedimento registrado no manual.')}</div>
                        </div>

                        ${flowHtml}
                        ${galleryHtml}

                        <!-- Rodapé -->
                        <div class="p-3 bg-surface-950 rounded-xl border border-slate-800 text-xs text-slate-400 flex items-center justify-between">
                            <span class="flex items-center gap-1.5">
                                <i data-lucide="shield-check" class="w-4 h-4 text-emerald-400"></i>
                                Documento Técnico Homologado • Alternativa Sistemas
                            </span>
                            <span class="font-mono text-[11px] text-slate-500">ID Nó: #${doc.id}</span>
                        </div>
                    </div>
                `;

            } catch (e) {
                console.error('Erro manual:', e);
                body.innerHTML = `<div class="text-rose-400 text-center p-8 space-y-2">
                    <div class="font-bold">Erro ao carregar detalhes do manual oficial.</div>
                    <div class="text-xs text-slate-500">${e.message || ''}</div>
                </div>`;
            }
            lucide.createIcons();
        }

        async function openFullTicket(sourceExternalId, sourceId, sourceType, nodeId) {
            if (sourceType === 'MANUAL' || (!sourceExternalId && nodeId)) {
                return openManualModal(nodeId || sourceId);
            }

            const modal = document.getElementById('ticket-modal');
            const body = document.getElementById('tm-body');
            body.innerHTML = '<div class="flex justify-center p-12"><div class="animate-spin text-brand-500"><i data-lucide="loader-2" class="w-8 h-8"></i></div></div>';
            modal.classList.remove('hidden');
            lucide.createIcons();

            if (sourceType === 'OFFICIAL_KB') {
                try {
                    const res = await fetch(`/api/kb/${sourceId}`);
                    if (!res.ok) {
                        const err = await res.json().catch(() => ({}));
                        throw new Error(err.error || `HTTP ${res.status}`);
                    }
                    const kb = await res.json();
                    document.getElementById('tm-id-badge').innerText = 'BASE DE CONHECIMENTO';
                    document.getElementById('tm-status-badge').innerText = 'Artigo Homologado';
                    document.getElementById('tm-status-badge').className = 'px-2.5 py-0.5 rounded-full text-xs font-semibold bg-blue-500/20 text-blue-400 border border-blue-500/30';
                    document.getElementById('tm-subject').innerText = kb.title;
                    document.getElementById('tm-date').innerText = kb.created_at_source ? new Date(kb.created_at_source).toLocaleDateString('pt-BR') : '';
                    document.getElementById('tm-client').innerHTML = '<span>Documentação Técnica Oficial ERP Master</span>';

                    body.innerHTML = `
                        <div class="space-y-4">
                            ${kb.summary ? `<div class="p-4 bg-brand-950/40 rounded-xl border border-brand-800/40 text-brand-200 text-sm"><b>Resumo:</b> ${kb.summary}</div>` : ''}
                            <div class="p-6 bg-surface-950 rounded-xl border border-slate-800 kb-rich-content">
                                ${kb.content_html || kb.content_text || 'Sem conteúdo.'}
                            </div>
                        </div>
                    `;

                    // Anexar visualizador de zoom em todas as imagens do artigo
                    const kbImgs = body.querySelectorAll('.kb-rich-content img');
                    kbImgs.forEach(img => {
                        img.style.cursor = 'zoom-in';
                        img.title = 'Clique para ampliar imagem';
                        img.addEventListener('click', () => openImageModal(img.src, img.alt || 'Print da Base de Conhecimento'));
                    });

                } catch (e) {
                    console.error('Erro KB:', e);
                    body.innerHTML = `<div class="text-rose-400 text-center p-6">Erro ao carregar artigo da Base de Conhecimento.<br><small class="text-slate-500">${e.message || ''}</small></div>`;
                }

            } else {
                try {
                    if (!sourceExternalId || sourceExternalId === 'null' || sourceExternalId === 'undefined') {
                        if (nodeId) return openManualModal(nodeId);
                        throw new Error("Identificador do chamado não encontrado.");
                    }
                    const res = await fetch(`/api/ticket/${sourceExternalId}`);
                    if (!res.ok) {
                        const err = await res.json().catch(() => ({}));
                        throw new Error(err.error || `HTTP ${res.status}`);
                    }
                    const ticket = await res.json();
                    if (!ticket || !ticket.id) {
                        throw new Error("Dados do chamado não disponíveis.");
                    }
                    document.getElementById('tm-id-badge').innerText = `CHAMADO #${ticket.id}`;
                    document.getElementById('tm-status-badge').innerText = ticket.status || 'Fechado';
                    document.getElementById('tm-status-badge').className = 'px-2.5 py-0.5 rounded-full text-xs font-semibold bg-emerald-500/20 text-emerald-400 border border-emerald-500/30';
                    document.getElementById('tm-subject').innerText = ticket.subject || 'Chamado sem assunto';
                    document.getElementById('tm-date').innerText = ticket.createdDate ? new Date(ticket.createdDate).toLocaleString('pt-BR') : '';
                    
                    let clientName = (ticket.clients && ticket.clients[0]) ? (ticket.clients[0].businessName || ticket.clients[0].corporateName || ticket.clients[0].organization?.corporateName || 'Cliente Alternativa') : 'Cliente Alternativa';
                    let ownerName = ticket.owner ? (ticket.owner.businessName || 'Atendente') : 'Suporte';
                    document.getElementById('tm-client').innerHTML = `<span>Empresa: <b>${clientName}</b></span> • <span>Responsável: <b>${ownerName}</b></span> • <span>Categoria: <b>${ticket.category || 'Suporte'}</b></span>`;

                    const actions = ticket.actions || [];
                    let actionsHtml = `<div class="space-y-4">`;

                    actions.forEach((act, idx) => {
                        const isAgent = act.type === 2 || act.origin === 2;
                        const roleLabel = isAgent ? 'Atendente / Suporte' : 'Cliente / Solicitante';
                        const roleBadge = isAgent 
                            ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20' 
                            : 'bg-blue-500/10 text-blue-400 border-blue-500/20';
                        const dateStr = act.createdDate ? new Date(act.createdDate).toLocaleString('pt-BR') : '';

                        let attsHtml = "";
                        if (act.attachments && act.attachments.length > 0) {
                            attsHtml = `
                                <div class="mt-3 pt-2.5 border-t border-slate-800/80 flex flex-wrap gap-2">
                                    ${act.attachments.map(a => {
                                        const name = a.fileName || 'anexo';
                                        const isImg = /\\.(png|jpg|jpeg|gif|webp)$/i.test(name);
                                        return `
                                            <a href="/attachments/${ticket.id}/${name}" target="_blank" 
                                                class="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-slate-800 hover:bg-slate-700 text-[11px] text-indigo-300 border border-slate-700 transition">
                                                <i data-lucide="${isImg ? 'image' : 'file-text'}" class="w-3.5 h-3.5"></i>
                                                <span>${name}</span>
                                            </a>
                                        `;
                                    }).join('')}
                                </div>
                            `;
                        }

                        const contentHtml = act.htmlDescription ? `
                            <div class="ticket-rich-content leading-relaxed">${act.htmlDescription}</div>
                        ` : `
                            <div class="text-xs text-slate-300 whitespace-pre-line leading-relaxed">${act.description || '(Sem texto registrado)'}</div>
                        `;

                        actionsHtml += `
                            <div class="rounded-xl border border-slate-800 p-4 bg-surface-950 space-y-2">

                                <div class="flex items-center justify-between text-xs">
                                    <div class="flex items-center gap-2">
                                        <span class="font-bold text-white">Ação #${act.id || (idx+1)}</span>
                                        <span class="px-2 py-0.5 rounded text-[11px] font-semibold border ${roleBadge}">${roleLabel}</span>
                                    </div>
                                    <span class="text-slate-500 font-mono text-[11px]">${dateStr}</span>
                                </div>
                                ${contentHtml}
                                ${attsHtml}
                            </div>
                        `;
                    });

                    actionsHtml += `</div>`;
                    body.innerHTML = actionsHtml;

                    // Anexar visualizador de zoom em todas as imagens do chamado
                    const ticketImgs = body.querySelectorAll('.ticket-rich-content img');
                    ticketImgs.forEach(img => {
                        img.addEventListener('click', () => openImageModal(img.src, img.alt || 'Print do chamado'));
                    });
                } catch (e) {
                    console.error('Erro chamado:', e);
                    body.innerHTML = `
                        <div class="text-center p-8 space-y-4">
                            <div class="text-rose-400 font-semibold">Erro ao carregar detalhes do chamado.</div>
                            <div class="text-slate-500 text-sm">${e.message || 'Tente novamente em alguns instantes.'}</div>
                            <button onclick="openFullTicket(${sourceExternalId}, '${sourceId}', '${sourceType}', ${nodeId})"
                                class="px-4 py-2 rounded-lg bg-brand-500/20 hover:bg-brand-500/30 text-brand-300 border border-brand-500/30 text-sm transition">
                                Tentar novamente
                            </button>
                        </div>
                    `;
                }
            }
            lucide.createIcons();
        }



        function closeTicketModal() {
            document.getElementById('ticket-modal').classList.add('hidden');
        }

        function openImageModal(imgSrc, fileName) {
            document.getElementById('modal-img').src = imgSrc;
            document.getElementById('modal-title').innerText = fileName;
            document.getElementById('image-modal').classList.remove('hidden');
        }

        function closeImageModal() {
            document.getElementById('image-modal').classList.add('hidden');
        }

        document.addEventListener('keydown', function(e) {
            if (e.key === 'Escape') {
                const imgModal = document.getElementById('image-modal');
                if (imgModal && !imgModal.classList.contains('hidden')) {
                    closeImageModal();
                    return;
                }
                const ticketModal = document.getElementById('ticket-modal');
                if (ticketModal && !ticketModal.classList.contains('hidden')) {
                    closeTicketModal();
                    return;
                }
                const reportModal = document.getElementById('report-flaw-modal');
                if (reportModal && !reportModal.classList.contains('hidden')) {
                    closeReportFlawModal();
                    return;
                }
            }
        });

        async function sendFeedback(nodeId, feedbackType) {
            try {
                await fetch('/api/feedback', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ node_id: nodeId, feedback_type: feedbackType })
                });
                alert(`Feedback (${feedbackType}) registrado com sucesso para o Knowledge Node #${nodeId}!`);
            } catch (err) {
                console.error(err);
            }
        }

        // =========================================================================
        // MODO CHAT INVESTIGATIVO MULTI-TURN (FASE 2) & SIMULADORES
        // =========================================================================
        let currentChatSessionId = '';
        let currentClientSessionId = '';
        let currentTechSessionId = '';
        let currentActiveTab = 'single';
        let chatAttachedImageBase64 = null;
        let clientAttachedImageBase64 = null;

        function switchTab(tab) {
            if (tab === 'chat') tab = 'client';
            currentActiveTab = tab;
            const tabs = ['single', 'chat', 'client', 'tech'];
            const btnSingle = document.getElementById('tab-btn-single');
            const btnChat = document.getElementById('tab-btn-chat');
            const btnClient = document.getElementById('tab-btn-client');
            const btnTech = document.getElementById('tab-btn-tech');

            const viewSingle = document.getElementById('view-single');
            const viewChat = document.getElementById('view-chat');
            const viewClient = document.getElementById('view-client');
            const viewTech = document.getElementById('view-tech');

            // Hide all views
            viewSingle.classList.add('hidden');
            viewChat.classList.add('hidden');
            viewClient.classList.add('hidden');
            viewTech.classList.add('hidden');

            // Reset button classes
            const inactiveClass = "px-3.5 py-2 rounded-xl text-xs font-semibold flex items-center gap-1.5 bg-slate-800 text-slate-300 hover:text-white border border-slate-700 transition";
            btnSingle.className = inactiveClass;
            btnChat.className = inactiveClass;
            btnClient.className = inactiveClass;
            btnTech.className = inactiveClass;

            if (tab === 'chat') {
                viewChat.classList.remove('hidden');
                btnChat.className = "px-3.5 py-2 rounded-xl text-xs font-semibold flex items-center gap-1.5 bg-emerald-600 text-white shadow-lg shadow-emerald-600/20 transition";
                setTimeout(() => document.getElementById('chat-input').focus(), 50);
            } else if (tab === 'client') {
                viewClient.classList.remove('hidden');
                btnClient.className = "px-3.5 py-2 rounded-xl text-xs font-semibold flex items-center gap-1.5 bg-blue-600 text-white shadow-lg shadow-blue-600/20 transition";
                setTimeout(() => document.getElementById('client-subject').focus(), 50);
            } else if (tab === 'tech') {
                viewTech.classList.remove('hidden');
                btnTech.className = "px-3.5 py-2 rounded-xl text-xs font-semibold flex items-center gap-1.5 bg-amber-600 text-white shadow-lg shadow-amber-600/20 transition";
                loadLatestTechCase();
                refreshClientQueue();
            } else {
                viewSingle.classList.remove('hidden');
                btnSingle.className = "px-3.5 py-2 rounded-xl text-xs font-semibold flex items-center gap-1.5 bg-indigo-600 text-white shadow-lg shadow-indigo-600/20 transition";
            }
            lucide.createIcons();
        }

        async function resetChat() {
            try {
                const resp = await fetch('/api/chat/reset', { method: 'POST' });
                const data = await resp.json();
                currentChatSessionId = data.session_id;

                const messagesContainer = document.getElementById('chat-messages');
                messagesContainer.innerHTML = `
                    <div class="flex gap-3 items-start">
                        <div class="h-8 w-8 rounded-lg bg-emerald-500/20 border border-emerald-500/30 flex items-center justify-center text-emerald-400 shrink-0 mt-0.5">
                            <i data-lucide="bot" class="w-4 h-4"></i>
                        </div>
                        <div class="flex-1 bg-surface-800 border border-slate-700/60 rounded-2xl rounded-tl-sm p-4 text-xs text-slate-200 shadow-md space-y-2">
                            <p class="font-semibold text-slate-100 text-sm">Nova investigação iniciada!</p>
                            <p class="text-slate-300 leading-relaxed">Pode relatar o novo sintoma, código de erro ou dúvida técnica do ERP Master que conduzirei a triagem com você.</p>
                        </div>
                    </div>
                `;
                renderState({
                    resolution_status: 'open',
                    intent: '—',
                    module: '—',
                    integration: '—',
                    entity: null,
                    evidence: [],
                    hypotheses: [],
                    confirmed_cause: null
                }, [], []);
                document.getElementById('chat-input').value = '';
                document.getElementById('chat-input').focus();
                lucide.createIcons();
            } catch (err) {
                console.error('Erro reset chat:', err);
            }
        }

        function sendQuickChat(msg) {
            document.getElementById('chat-input').value = msg;
            submitChatMessage();
        }

        function formatChatMessageText(text) {
            if (!text) return '';
            let formatted = text
                .replace(/&/g, '&amp;')
                .replace(/</g, '&lt;')
                .replace(/>/g, '&gt;');

            // Bold **text**
            formatted = formatted.replace(/\\*\\*(.*?)\\*\\*/g, '<b class="text-slate-100 font-bold">$1</b>');

            // Code `code`
            formatted = formatted.replace(/`([^`]+)`/g, '<code class="px-1.5 py-0.5 rounded bg-slate-900 text-emerald-300 font-mono text-[11px] border border-slate-800">$1</code>');

            // Headers ###
            formatted = formatted.replace(/^### (.*$)/gim, '<div class="text-xs font-bold text-slate-100 uppercase tracking-wider mt-3 mb-1 text-emerald-400">$1</div>');
            formatted = formatted.replace(/^## (.*$)/gim, '<div class="text-sm font-bold text-slate-100 mt-3 mb-1">$1</div>');

            // Markdown Links [title](url)
            formatted = formatted.replace(/\\[([^\\]]+)\\]\\((https?:\\/\\/[^\\s\\)]+)\\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer" class="inline-flex items-center gap-1 text-blue-400 hover:text-blue-300 underline font-semibold transition">$1 <svg class="w-3 h-3 inline" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14"></path></svg></a>');

            // Line breaks
            formatted = formatted.replace(/\\n/g, '<br>');
            return formatted;
        }

        async function submitChatMessage() {
            const input = document.getElementById('chat-input');
            const msg = input.value.trim();
            if (!msg) return;

            input.value = '';
            appendMessage('user', msg);

            // Mostrar typing indicator
            const typing = document.getElementById('chat-typing');
            typing.classList.remove('hidden');

            const messagesContainer = document.getElementById('chat-messages');
            messagesContainer.scrollTop = messagesContainer.scrollHeight;

            const startTime = Date.now();
            try {
                const resp = await fetch('/api/chat', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ session_id: currentChatSessionId, message: msg })
                });
                const data = await resp.json();
                const elapsed = Date.now() - startTime;
                if (elapsed < 1400) {
                    await new Promise(r => setTimeout(r, 1400 - elapsed));
                }
                currentChatSessionId = data.session_id;

                appendMessage('assistant', data.reply, data.shortcuts, data.media_refs, data.screen_path);
                renderState(data.investigation_state, data.shortcuts, data.media_refs);
            } catch (err) {
                console.error('Erro envio chat:', err);
                appendMessage('assistant', '⚠️ Ocorreu uma falha na comunicação com o cérebro do DeskCortex: ' + err.message);
            } finally {
                typing.classList.add('hidden');
                messagesContainer.scrollTop = messagesContainer.scrollHeight;
                lucide.createIcons();
            }
        }

        function appendMessage(role, text, shortcuts = [], mediaRefs = [], screenPath = null) {
            const container = document.getElementById('chat-messages');
            const isUser = role === 'user';

            let shortcutsHtml = '';
            if (shortcuts && shortcuts.length > 0) {
                shortcutsHtml = `
                    <div class="pt-2 flex items-center gap-1.5 flex-wrap">
                        <span class="text-[10px] uppercase font-bold text-slate-400">Atalhos:</span>
                        ${shortcuts.map(s => `<kbd class="px-2 py-0.5 rounded bg-slate-900 text-emerald-400 font-mono text-[11px] font-bold border border-emerald-500/30 shadow">${s}</kbd>`).join('')}
                    </div>
                `;
            }

            let pathHtml = '';
            if (screenPath) {
                pathHtml = `
                    <div class="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-surface-900/90 text-indigo-300 text-[11px] font-medium border border-indigo-500/30 my-1">
                        <i data-lucide="compass" class="w-3.5 h-3.5"></i>
                        <span>Caminho: <b>${screenPath}</b></span>
                    </div>
                `;
            }

            let mediaHtml = '';
            if (mediaRefs && mediaRefs.length > 0) {
                mediaHtml = `
                    <div class="pt-2 space-y-2">
                        <div class="text-[10px] uppercase font-bold text-slate-400 flex items-center gap-1">
                            <i data-lucide="image" class="w-3 h-3 text-brand-400"></i>
                            Mídia / GIF de Orientação:
                        </div>
                        <div class="grid grid-cols-1 gap-2">
                            ${mediaRefs.map(m => `
                                <div class="rounded-xl border border-slate-700 bg-surface-950 p-2.5 space-y-1.5">
                                    <div class="flex items-center justify-between">
                                        <span class="text-[11px] font-bold text-slate-200">${m.title || 'Manual do Master'}</span>
                                        <span class="text-[10px] px-1.5 py-0.5 rounded bg-brand-500/20 text-brand-300 uppercase">${m.type}</span>
                                    </div>
                                    <div class="relative group cursor-pointer overflow-hidden rounded-lg border border-slate-800" onclick="openImageModal('/manual_media/${m.path}', '${m.title || 'Print do Master'}')">
                                        <img src="/manual_media/${m.path}" alt="${m.title}" class="max-h-48 w-full object-cover object-top hover:scale-105 transition duration-200">
                                        <div class="absolute inset-0 bg-black/40 opacity-0 group-hover:opacity-100 flex items-center justify-center text-white text-xs font-semibold gap-1 transition">
                                            <i data-lucide="zoom-in" class="w-4 h-4"></i> Clique para ampliar
                                        </div>
                                    </div>
                                    ${m.screen ? `<div class="text-[10px] text-slate-400">Tela: <span class="text-slate-300">${m.screen}</span></div>` : ''}
                                </div>
                            `).join('')}
                        </div>
                    </div>
                `;
            }

            const bubbleHtml = isUser ? `
                <div class="flex gap-3 justify-end items-start">
                    <div class="max-w-[80%] bg-indigo-600 rounded-2xl rounded-tr-sm p-3.5 text-xs text-white shadow-md">
                        <p class="leading-relaxed whitespace-pre-wrap">${text}</p>
                    </div>
                    <div class="h-8 w-8 rounded-lg bg-indigo-500/20 border border-indigo-500/30 flex items-center justify-center text-indigo-300 shrink-0 mt-0.5">
                        <i data-lucide="user" class="w-4 h-4"></i>
                    </div>
                </div>
            ` : `
                <div class="flex gap-3 items-start">
                    <div class="h-8 w-8 rounded-lg bg-emerald-500/20 border border-emerald-500/30 flex items-center justify-center text-emerald-400 shrink-0 mt-0.5">
                        <i data-lucide="bot" class="w-4 h-4"></i>
                    </div>
                    <div class="max-w-[85%] bg-surface-800 border border-slate-700/60 rounded-2xl rounded-tl-sm p-4 text-xs text-slate-200 shadow-md space-y-2">
                        ${pathHtml}
                        <div class="leading-relaxed space-y-1">${formatChatMessageText(text)}</div>
                        ${shortcutsHtml}
                        ${mediaHtml}
                    </div>
                </div>
            `;

            container.insertAdjacentHTML('beforeend', bubbleHtml);
            lucide.createIcons();
        }

        function renderState(state, shortcuts = [], mediaRefs = []) {
            if (!state) return;

            // Status Badge
            const badge = document.getElementById('state-status-badge');
            const status = state.resolution_status || 'open';
            if (status === 'investigating') {
                badge.className = "px-2 py-0.5 rounded-full text-[10px] font-bold bg-amber-500/20 text-amber-300 border border-amber-500/30 animate-pulse";
                badge.innerText = "INVESTIGANDO";
            } else if (status === 'prescribing' || status === 'resolved') {
                badge.className = "px-2 py-0.5 rounded-full text-[10px] font-bold bg-emerald-500/20 text-emerald-300 border border-emerald-500/30";
                badge.innerText = "PRESCREVENDO SOLUÇÃO";
            } else {
                badge.className = "px-2 py-0.5 rounded-full text-[10px] font-bold bg-slate-800 text-slate-400 border border-slate-700";
                badge.innerText = "AGUARDANDO";
            }

            // Attributes
            document.getElementById('state-intent').innerText = state.intent || '—';
            document.getElementById('state-module').innerText = state.module || '—';
            document.getElementById('state-integration').innerText = state.integration || '—';

            const entityElem = document.getElementById('state-entity');
            if (state.entity) {
                entityElem.innerText = `${state.entity.type || 'item'} #${state.entity.id || ''}`;
            } else {
                entityElem.innerText = '—';
            }

            // Evidence Engine List
            const evList = document.getElementById('state-evidence-list');
            const evidence = state.evidence || [];
            const hypotheses = state.hypotheses || [];

            if (evidence.length === 0 && hypotheses.length === 0) {
                evList.innerHTML = '<div class="text-[11px] text-slate-500 italic">Nenhuma evidência registrada ainda.</div>';
            } else {
                let itemsHtml = '';

                // Fatos
                evidence.forEach(ev => {
                    const isFact = ev.type === 'fato' || ev.type === 'confirmacao' || ev.type === 'conclusao';
                    itemsHtml += `
                        <div class="p-2 rounded-lg ${isFact ? 'bg-emerald-500/10 border border-emerald-500/20' : 'bg-slate-800/80 border border-slate-700'} space-y-0.5">
                            <div class="flex items-center justify-between text-[10px]">
                                <span class="font-bold ${isFact ? 'text-emerald-400' : 'text-slate-400'} uppercase">${ev.type}</span>
                                <span class="text-slate-500">${ev.source}</span>
                            </div>
                            <div class="text-[11px] text-slate-200">${ev.content}</div>
                        </div>
                    `;
                });

                // Hipóteses
                hypotheses.forEach(h => {
                    const confPct = Math.round(h.confidence * 100);
                    itemsHtml += `
                        <div class="p-2 rounded-lg bg-indigo-500/10 border border-indigo-500/20 space-y-1">
                            <div class="flex items-center justify-between text-[10px]">
                                <span class="font-bold text-indigo-400 uppercase">HIPÓTESE</span>
                                <span class="font-mono text-indigo-300 font-bold">${confPct}%</span>
                            </div>
                            <div class="text-[11px] text-slate-200 font-medium">${h.cause}</div>
                            <div class="w-full bg-slate-800 rounded-full h-1.5 overflow-hidden">
                                <div class="bg-indigo-500 h-1.5 rounded-full" style="width: ${confPct}%"></div>
                            </div>
                        </div>
                    `;
                });

                evList.innerHTML = itemsHtml;
            }

            // Confirmed Cause
            const confirmedContainer = document.getElementById('confirmed-cause-container');
            const confirmedText = document.getElementById('state-confirmed-cause');
            if (state.confirmed_cause) {
                confirmedContainer.classList.remove('hidden');
                confirmedText.innerText = state.confirmed_cause;
            } else {
                confirmedContainer.classList.add('hidden');
            }

            // Shortcuts
            const scList = document.getElementById('state-shortcuts-list');
            if (shortcuts && shortcuts.length > 0) {
                scList.innerHTML = shortcuts.map(s => `
                    <kbd class="px-2.5 py-1 rounded-lg bg-slate-900 text-emerald-400 font-mono text-xs font-bold border border-emerald-500/30 shadow">${s}</kbd>
                `).join('');
            } else {
                scList.innerHTML = '<span class="text-[11px] text-slate-500 italic">Nenhum atalho no momento.</span>';
            }

            // Media Refs Gallery
            const mediaList = document.getElementById('state-media-list');
            if (mediaRefs && mediaRefs.length > 0) {
                mediaList.innerHTML = mediaRefs.map(m => `
                    <div class="rounded-xl border border-slate-700 bg-surface-950 p-2 space-y-1">
                        <div class="text-[11px] font-bold text-slate-200 truncate">${m.title || 'Mídia'}</div>
                        <img src="/manual_media/${m.path}" alt="${m.title}"
                             class="h-28 w-full object-cover rounded-lg cursor-pointer hover:opacity-90 transition"
                             onclick="openImageModal('/manual_media/${m.path}', '${m.title}')">
                    </div>
                `).join('');
            } else {
                mediaList.innerHTML = '<div class="text-[11px] text-slate-500 italic">Nenhuma mídia anexada a esta etapa.</div>';
            }

            lucide.createIcons();
        }

        // =========================================================================
        // UTILITÁRIOS: TEXTAREA AUTO-RESIZE & PASTE (CTRL+V) & UPLOAD DE IMAGEM
        // =========================================================================
        function autoResizeTextarea(el) {
            el.style.height = 'auto';
            el.style.height = Math.min(el.scrollHeight, 128) + 'px';
        }

        function handleChatInputKeydown(e) {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                submitChatMessage();
            }
        }

        function handleClientInputKeydown(e) {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                submitClientChatMessage();
            }
        }

        function setChatImagePreview(base64Data, fileName) {
            chatAttachedImageBase64 = base64Data;
            document.getElementById('chat-img-preview-thumb').src = base64Data;
            document.getElementById('chat-img-preview-name').innerText = fileName || 'screenshot.png';
            document.getElementById('chat-img-preview-bar').classList.remove('hidden');
        }

        function clearChatImage() {
            chatAttachedImageBase64 = null;
            document.getElementById('chat-file-input').value = '';
            document.getElementById('chat-img-preview-bar').classList.add('hidden');
        }

        function handleChatImageSelect(e) {
            const file = e.target.files[0];
            if (!file) return;
            const reader = new FileReader();
            reader.onload = (evt) => setChatImagePreview(evt.target.result, file.name);
            reader.readAsDataURL(file);
        }

        function setClientImagePreview(base64Data, fileName) {
            clientAttachedImageBase64 = base64Data;
            document.getElementById('client-img-preview-thumb').src = base64Data;
            document.getElementById('client-img-preview-name').innerText = fileName || 'screenshot.png';
            document.getElementById('client-img-preview-bar').classList.remove('hidden');
        }

        function clearClientImage() {
            clientAttachedImageBase64 = null;
            document.getElementById('client-file-input').value = '';
            document.getElementById('client-img-preview-bar').classList.add('hidden');
        }

        function handleClientImageSelect(e) {
            const file = e.target.files[0];
            if (!file) return;
            const reader = new FileReader();
            reader.onload = (evt) => setClientImagePreview(evt.target.result, file.name);
            reader.readAsDataURL(file);
        }

        // Suporte global para colar imagens com Ctrl+V diretamente no chat ativo!
        window.addEventListener('paste', (e) => {
            return; // Chat desativado nesta etapa do produto.
            const items = (e.clipboardData || e.originalEvent.clipboardData).items;
            for (let item of items) {
                if (item.type.indexOf('image') !== -1) {
                    const blob = item.getAsFile();
                    const reader = new FileReader();
                    reader.onload = (evt) => {
                        if (currentActiveTab === 'client') {
                            setClientImagePreview(evt.target.result, 'print_colado.png');
                        } else {
                            setChatImagePreview(evt.target.result, 'print_colado.png');
                        }
                    };
                    reader.readAsDataURL(blob);
                    break;
                }
            }
        });

        // =========================================================================
        // SIMULADOR CLIENTE (ROMÁRIO) — CONTROLE & FLUXO
        // =========================================================================
        const clientScenarios = {
            1: {
                subject: "Pedido 105829 da AnyMarket não caiu no Master",
                module: "Ecommerce",
                urgency: "high",
                description: "Olá, fiz uma venda no Mercado Livre via AnyMarket com o pedido 105829, mas ele não apareceu no ERP Master para emitir a nota. Podem me ajudar?"
            },
            2: {
                subject: "Rejeição 783 na emissão da NF-e 4521",
                module: "Fiscal",
                urgency: "critical",
                description: "Bom dia, ao tentar transmitir a NF-e 4521 dá Rejeição: 783 - Vinculação do CFOP à operação interestadual. Não sei o que corrigir."
            },
            3: {
                subject: "Erro ao emitir boleto do Banco Bradesco",
                module: "Financeiro",
                urgency: "medium",
                description: "O sistema não está gerando a segunda via do boleto para o cliente. Aparece erro ao tentar imprimir o boleto do Bradesco."
            }
        };

        function fillClientScenario(id) {
            const sc = clientScenarios[id];
            if (sc) {
                document.getElementById('client-subject').value = sc.subject;
                document.getElementById('client-module').value = sc.module;
                document.getElementById('client-urgency').value = sc.urgency;
                document.getElementById('client-description').value = sc.description;
            }
        }

        async function startClientSession() {
            const subject = document.getElementById('client-subject').value.trim();
            const module = document.getElementById('client-module').value;
            const urgency = document.getElementById('client-urgency').value;
            const description = document.getElementById('client-description').value.trim();

            if (!subject || !description) return;

            const btn = document.getElementById('btn-start-client');
            btn.disabled = true;
            btn.innerHTML = '<i data-lucide="loader-2" class="w-4 h-4 animate-spin"></i><span>Registrando chamado...</span>';
            lucide.createIcons();

            try {
                const resp = await fetch('/api/client/submit', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ subject, module, urgency, description })
                });
                const data = await resp.json();
                if (!resp.ok) throw new Error('Não foi possível abrir o chamado.');
                currentClientSessionId = data.session_id;
                document.getElementById('client-receipt').textContent = 'Chamado recebido. Protocolo: ' + data.session_id + '. A equipe técnica fará a análise.';
                document.getElementById('client-open-form').reset();
            } catch (err) {
                alert('Erro ao iniciar atendimento: ' + err.message);
            } finally {
                btn.disabled = false;
                btn.innerHTML = '<i data-lucide="sparkles" class="w-4 h-4"></i><span>Abrir chamado</span>';
                lucide.createIcons();
            }
        }

        async function submitClientChatMessage() {
            const input = document.getElementById('client-chat-input');
            const message = input.value.trim();
            const imgBase64 = clientAttachedImageBase64;

            if (!message && !imgBase64) return;

            // Se for imagem com ou sem texto
            if (imgBase64) {
                const sendMsg = message || 'Enviei um print da tela para você analisar o erro:';
                appendClientChatMessage('user', sendMsg, [{ path: imgBase64, is_user_upload: true }]);
                clearClientImage();
                input.value = '';
                input.style.height = 'auto';

                document.getElementById('client-typing').classList.remove('hidden');
                const startTime = Date.now();
                try {
                    const resp = await fetch('/api/chat/image', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            session_id: currentClientSessionId,
                            image_base64: imgBase64,
                            message: sendMsg
                        })
                    });
                    const data = await resp.json();
                    const elapsed = Date.now() - startTime;
                    if (elapsed < 1400) {
                        await new Promise(r => setTimeout(r, 1400 - elapsed));
                    }
                    if (data.session_id) currentClientSessionId = data.session_id;
                    appendClientChatMessage('assistant', data.reply || data.content || '', data.media_refs, data.shortcuts, data.skills_used, data.diagnostic_thought);
                } catch (err) {
                    appendClientChatMessage('assistant', 'Desculpe, ocorreu uma instabilidade ao analisar o print. Pode digitar a mensagem de erro?');
                } finally {
                    document.getElementById('client-typing').classList.add('hidden');
                }
                return;
            }

            // Mensagem de texto normal
            appendClientChatMessage('user', message);
            input.value = '';
            input.style.height = 'auto';

            document.getElementById('client-typing').classList.remove('hidden');
            const startTime = Date.now();
            try {
                const resp = await fetch('/api/chat', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        session_id: currentClientSessionId,
                        message: message
                    })
                });
                const data = await resp.json();
                const elapsed = Date.now() - startTime;
                if (elapsed < 1400) {
                    await new Promise(r => setTimeout(r, 1400 - elapsed));
                }
                if (data.session_id) currentClientSessionId = data.session_id;
                appendClientChatMessage('assistant', data.reply || data.content || '', data.media_refs, data.shortcuts, data.skills_used, data.diagnostic_thought);
            } catch (err) {
                appendClientChatMessage('assistant', 'Desculpe, ocorreu um erro ao consultar o sistema: ' + err.message);
            } finally {
                document.getElementById('client-typing').classList.add('hidden');
            }
        }

        function appendClientChatMessage(role, text, mediaRefs = [], shortcuts = [], skillsUsed = [], diagnosticThought = null) {
            const container = document.getElementById('client-chat-messages');
            const isUser = role === 'user';
            const msgEl = document.createElement('div');
            msgEl.className = `flex gap-3 items-start ${isUser ? 'flex-row-reverse' : ''}`;

            let avatarHtml = '';
            if (isUser) {
                avatarHtml = `<div class="w-8 h-8 rounded-full bg-slate-700 border border-slate-600 flex items-center justify-center text-slate-200 shrink-0 text-xs font-bold">VC</div>`;
            } else {
                avatarHtml = `<div class="w-8 h-8 rounded-full bg-blue-600 border border-blue-500 flex items-center justify-center text-white shrink-0 text-xs font-bold">R</div>`;
            }

            let bubbleClass = isUser
                ? 'bg-blue-600/90 text-white rounded-2xl rounded-tr-sm border border-blue-500/40 p-3.5 shadow-md max-w-xl text-xs leading-relaxed space-y-2'
                : 'bg-surface-800 text-slate-200 rounded-2xl rounded-tl-sm border border-slate-700/60 p-4 shadow-md max-w-2xl text-xs leading-relaxed space-y-2.5';

            let mediaHtml = '';
            if (mediaRefs && mediaRefs.length > 0) {
                mediaHtml = mediaRefs.map(m => {
                    const src = m.is_user_upload ? m.path : `/manual_media/${m.path}`;
                    return `
                        <div class="mt-2 rounded-xl overflow-hidden border border-slate-700 bg-surface-950 p-2 cursor-pointer hover:border-blue-500 transition"
                             onclick="openImageModal('${src}', '${m.title || 'Print do chamado'}')">
                            <img src="${src}" class="max-h-48 rounded-lg object-contain mx-auto">
                            <div class="text-[11px] text-slate-400 font-semibold text-center mt-1.5">${m.title || 'Visualizar Print'}</div>
                        </div>
                    `;
                }).join('');
            }

            let shortcutsHtml = '';
            if (shortcuts && shortcuts.length > 0) {
                shortcutsHtml = `
                    <div class="flex items-center gap-1.5 flex-wrap pt-1 text-[11px]">
                        <span class="text-slate-400 font-bold">Atalhos no Master:</span>
                        ${shortcuts.map(s => `<kbd class="px-2 py-0.5 rounded bg-surface-900 border border-slate-700 font-mono text-emerald-400 font-bold">${s}</kbd>`).join('')}
                    </div>
                `;
            }

            let videoCardHtml = '';
            const ytMatch = text.match(/https?:\\/\\/(?:www\\.)?(?:youtube\\.com\\/watch\\?v=|youtu\\.be\\/)([\\w-]+)(?:[^\\s"'>\\)]*)/);
            if (ytMatch) {
                const ytUrl = ytMatch[0];
                videoCardHtml = `
                    <div class="mt-2.5 p-2.5 rounded-xl bg-red-950/40 border border-red-500/30 flex items-center justify-between gap-3 shadow-inner">
                        <div class="flex items-center gap-2.5">
                            <div class="w-8 h-8 rounded-lg bg-red-600/20 border border-red-500/40 flex items-center justify-center text-red-400 shrink-0">
                                <svg class="w-4 h-4 fill-current" viewBox="0 0 24 24"><path d="M19.615 3.184c-3.604-.246-11.631-.245-15.23 0-3.897.266-4.356 2.62-4.385 8.816.029 6.185.484 8.549 4.385 8.816 3.6.245 11.626.246 15.23 0 3.897-.266 4.356-2.62 4.385-8.816zm-10.615 12.816v-8l8 3.993-8 4.007z"/></svg>
                            </div>
                            <div>
                                <div class="text-[11px] font-bold text-slate-200">Vídeo Tutorial Oficial Alternativa Sistemas</div>
                                <div class="text-[10px] text-slate-400">Assista o passo a passo com telas reais no YouTube</div>
                            </div>
                        </div>
                        <a href="${ytUrl}" target="_blank" rel="noopener noreferrer" class="px-3 py-1.5 rounded-lg bg-red-600 hover:bg-red-500 text-white text-[11px] font-bold flex items-center gap-1.5 transition shadow-sm shrink-0">
                            <span>Assistir</span>
                            <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14"></path></svg>
                        </a>
                    </div>
                `;
            }

            let skillsHtml = '';
            if (skillsUsed && skillsUsed.length > 0) {
                const labelMap = {
                    'perception.extract_error_image': 'Visão & OCR Resiliente',
                    'perception.extract_entities_text': 'Entidades Visuais',
                    'domain.fiscal.diagnose_rejection': 'Domínio Fiscal (SEFAZ)',
                    'domain.fiscal.validate_key': 'Validação Chave 44d',
                    'domain.ecommerce.diagnose_order': 'Domínio E-commerce',
                    'knowledge.retrieve': 'Grounded RAG',
                    'knowledge.tutorials_media': 'Vídeos & Mídias Oficiais',
                    'reasoning.evaluate_hypotheses': 'Evidence Engine (Trava T1)',
                    'presentation.humanize_response': 'Persona Romário'
                };
                const tags = skillsUsed.map(s => labelMap[s] || s);
                skillsHtml = `
                    <div class="flex items-center gap-1 flex-wrap pt-1 text-[10px] text-slate-400">
                        <span class="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-blue-500/10 border border-blue-500/30 text-blue-300 font-mono">
                            ⚡ Skills Ativas: ${tags.join(' • ')}
                        </span>
                    </div>
                `;
            }

            let handoffCardHtml = '';
            const isHandoff = !isUser && (
                text.includes('Mesa do Técnico') ||
                text.includes('especialista humano') ||
                text.includes('transferir o seu atendimento') ||
                text.includes('transferindo o seu atendimento') ||
                text.includes('direcionando o seu atendimento')
            );
            if (isHandoff) {
                handoffCardHtml = `
                    <div class="mt-3 p-3.5 rounded-xl bg-amber-500/15 border border-amber-500/40 flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3 shadow-md">
                        <div class="flex items-center gap-2.5">
                            <div class="w-8 h-8 rounded-lg bg-amber-500/25 border border-amber-500/50 flex items-center justify-center text-amber-300 shrink-0">
                                <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z"></path></svg>
                            </div>
                            <div>
                                <div class="text-xs font-bold text-amber-200">Atendimento Transferido para Especialista Humano</div>
                                <div class="text-[10px] text-slate-300">Dossiê e histórico prontos na Mesa do Técnico</div>
                            </div>
                        </div>
                        <button onclick="sendToTechDesk()" class="w-full sm:w-auto px-3.5 py-2 rounded-xl bg-amber-500 hover:bg-amber-400 text-slate-950 font-bold text-xs flex items-center justify-center gap-1.5 transition shadow-md shadow-amber-500/20 shrink-0">
                            <span>Ir para Mesa do Técnico</span>
                            <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M14 5l7 7m0 0l-7 7m7-7H3"></path></svg>
                        </button>
                    </div>
                `;
                const btnSendTech = document.getElementById('btn-send-to-tech');
                if (btnSendTech) btnSendTech.classList.remove('hidden');
            }

            let thoughtHtml = '';
            if (diagnosticThought && !isUser) {
                const dt = diagnosticThought;
                const intentLabel = dt.intent || 'Não identificado';
                const modLabel = dt.module || 'ERP Master';
                const confirmed = dt.confirmed_cause;
                const reason = dt.decision_reason || '';
                const missing = (dt.missing_information && dt.missing_information.length > 0)
                    ? dt.missing_information.join(', ')
                    : null;
                const hyps = dt.hypotheses || [];

                let hypsHtml = '';
                if (hyps.length > 0) {
                    hypsHtml = `
                        <div class="space-y-1.5 pt-1">
                            <span class="text-[10px] text-slate-400 font-semibold uppercase tracking-wider block">Hipóteses em Investigação (Trava T1):</span>
                            <div class="grid grid-cols-1 sm:grid-cols-2 gap-1.5">
                                ${hyps.map(h => `
                                    <div class="bg-surface-950/80 border border-slate-800 rounded-lg p-2 text-[10px]">
                                        <div class="flex justify-between items-center text-slate-300 font-medium">
                                            <span class="truncate">${h.cause}</span>
                                            <span class="font-mono font-bold text-indigo-400 shrink-0 ml-1">${h.confidence_pct}%</span>
                                        </div>
                                        <div class="w-full bg-slate-800 rounded-full h-1 mt-1.5 overflow-hidden">
                                            <div class="bg-indigo-500 h-1 rounded-full" style="width: ${h.confidence_pct}%"></div>
                                        </div>
                                    </div>
                                `).join('')}
                            </div>
                        </div>
                    `;
                }

                thoughtHtml = `
                    <details class="mt-2 text-[11px] bg-slate-900/90 border border-indigo-500/30 rounded-xl overflow-hidden shadow-inner transition group">
                        <summary class="px-3 py-2 cursor-pointer bg-indigo-950/30 hover:bg-indigo-950/50 text-indigo-300 font-semibold flex items-center justify-between select-none transition">
                            <div class="flex items-center gap-1.5">
                                <span class="text-sm">🧠</span>
                                <span class="font-bold text-indigo-200">Raciocínio & Diagnóstico da IA</span>
                                <span class="text-[10px] px-1.5 py-0.5 rounded bg-indigo-500/20 text-indigo-300 border border-indigo-500/30 font-mono">Trajetória Cognitiva</span>
                            </div>
                            <span class="text-[10px] text-slate-400 group-open:rotate-180 transition-transform duration-200">▼</span>
                        </summary>
                        <div class="p-3 space-y-2.5 border-t border-indigo-500/20 bg-surface-950/90 text-slate-300 leading-relaxed">
                            <div class="grid grid-cols-2 sm:grid-cols-3 gap-2 text-[10px]">
                                <div class="bg-surface-900 border border-slate-800 rounded-lg p-2">
                                    <span class="text-slate-500 block">Intenção:</span>
                                    <span class="text-emerald-400 font-bold font-mono">${intentLabel}</span>
                                </div>
                                <div class="bg-surface-900 border border-slate-800 rounded-lg p-2">
                                    <span class="text-slate-500 block">Módulo ERP:</span>
                                    <span class="text-indigo-300 font-bold">${modLabel}</span>
                                </div>
                                <div class="bg-surface-900 border border-slate-800 rounded-lg p-2 col-span-2 sm:col-span-1">
                                    <span class="text-slate-500 block">Status Causal:</span>
                                    <span class="${confirmed ? 'text-emerald-400 font-bold' : 'text-amber-400 font-bold'}">${confirmed ? 'Causa Confirmada' : 'Em Investigação'}</span>
                                </div>
                            </div>

                            ${missing ? `
                                <div class="bg-amber-500/10 border border-amber-500/30 rounded-lg p-2 text-[10px]">
                                    <span class="text-amber-400 font-bold block mb-0.5">ℹ️ Informação Pendente para Confirmação:</span>
                                    <span class="text-amber-200">${missing}</span>
                                </div>
                            ` : ''}

                            ${confirmed ? `
                                <div class="bg-emerald-500/10 border border-emerald-500/30 rounded-lg p-2 text-[10px]">
                                    <span class="text-emerald-400 font-bold block mb-0.5">🎯 Causa Raiz Confirmada (Trava T1):</span>
                                    <span class="text-emerald-200">${confirmed}</span>
                                </div>
                            ` : ''}

                            <div class="bg-slate-900 border border-slate-800 rounded-lg p-2.5 space-y-1">
                                <span class="text-[10px] text-slate-400 font-semibold uppercase tracking-wider block">Por que tomou essa decisão:</span>
                                <p class="text-[11px] text-slate-200 leading-relaxed font-sans">${reason}</p>
                            </div>

                            ${hypsHtml}
                        </div>
                    </details>
                `;
            }

            let feedbackBtnHtml = '';
            if (!isUser) {
                const encodedText = encodeURIComponent(text);
                feedbackBtnHtml = `
                    <div class="flex justify-end pt-1">
                        <button onclick="openReportFlawModal(this)"
                                data-reply="${encodedText}"
                                title="Reportar fala inadequada ou ajustar o comportamento do Romário"
                                class="inline-flex items-center gap-1.5 text-[10px] text-slate-400 hover:text-amber-400 transition cursor-pointer px-2 py-0.5 rounded-lg bg-surface-900/80 hover:bg-surface-700/80 border border-slate-700/60 hover:border-amber-500/40">
                            <span>🚩</span>
                            <span>Reportar Fala / Ajustar IA</span>
                        </button>
                    </div>
                `;
            }

            msgEl.innerHTML = `
                ${avatarHtml}
                <div class="${bubbleClass}">
                    <div>${formatChatMessageText(text)}</div>
                    ${videoCardHtml}
                    ${mediaHtml}
                    ${shortcutsHtml}
                    ${handoffCardHtml}
                    ${thoughtHtml}
                    ${skillsHtml}
                    ${feedbackBtnHtml}
                </div>
            `;
            container.appendChild(msgEl);
            container.scrollTop = container.scrollHeight;
            lucide.createIcons();
        }

        let activeReportReplyText = '';
        function openReportFlawModal(btn) {
            const raw = btn.getAttribute('data-reply') || '';
            activeReportReplyText = decodeURIComponent(raw);
            document.getElementById('report-flaw-msg').innerText = activeReportReplyText;
            document.getElementById('report-flaw-reason').value = '';
            document.getElementById('report-flaw-expected').value = '';
            const msgEl = document.getElementById('report-modal-msg');
            msgEl.className = 'text-xs hidden p-2 rounded-lg';
            msgEl.innerText = '';
            document.getElementById('report-flaw-modal').classList.remove('hidden');
        }

        function closeReportFlawModal() {
            document.getElementById('report-flaw-modal').classList.add('hidden');
        }

        async function submitDialogueFeedback() {
            const reason = document.getElementById('report-flaw-reason').value.trim();
            const expected = document.getElementById('report-flaw-expected').value.trim();
            if (!reason && !expected) {
                alert('Por favor, descreva o que estava inadequado ou o que o Romário deveria ter dito.');
                return;
            }

            const btn = document.getElementById('btn-submit-feedback');
            const msgEl = document.getElementById('report-modal-msg');
            btn.disabled = true;
            btn.innerText = 'Salvando...';

            try {
                const resp = await fetch('/api/feedback/dialogue', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        session_id: currentClientSessionId || currentChatSessionId || '',
                        assistant_message: activeReportReplyText,
                        flaw_description: reason,
                        expected_response: expected
                    })
                });
                const res = await resp.json();
                msgEl.className = 'text-xs block p-2 rounded-lg bg-emerald-500/20 border border-emerald-500/40 text-emerald-300 font-medium';
                msgEl.innerText = '✅ Feedback registrado com sucesso! Regra arquivada para ajuste imediato.';
                setTimeout(() => {
                    closeReportFlawModal();
                    btn.disabled = false;
                    btn.innerHTML = '<span>Salvar & Ajustar IA</span>';
                }, 1200);
            } catch (err) {
                msgEl.className = 'text-xs block p-2 rounded-lg bg-red-500/20 border border-red-500/40 text-red-300';
                msgEl.innerText = 'Erro ao enviar feedback: ' + err.message;
                btn.disabled = false;
                btn.innerHTML = '<span>Salvar & Ajustar IA</span>';
            }
        }

        function resetClientChat() {
            document.getElementById('client-chat-messages').innerHTML = `
                <div class="flex gap-3 items-start">
                    <div class="w-8 h-8 rounded-full bg-blue-600/30 border border-blue-500/40 flex items-center justify-center text-blue-400 shrink-0 mt-0.5 font-bold text-xs">
                        R
                    </div>
                    <div class="flex-1 bg-surface-800 border border-slate-700/60 rounded-2xl rounded-tl-sm p-4 text-xs text-slate-200 shadow-md space-y-2">
                        <p class="font-bold text-white text-sm">Olá! Sou o Romário, técnico de suporte do ERP Master.</p>
                        <p class="text-slate-300 leading-relaxed">
                            Preencha os dados ao lado para abrirmos o seu chamado, ou descreva o problema diretamente aqui no chat.
                            Se tiver um print da tela com o erro, pode colar com <b>Ctrl+V</b> ou anexar pelo botão <b>📎</b>!
                        </p>
                    </div>
                </div>
            `;
            currentClientSessionId = '';
            document.getElementById('client-subject').value = '';
            document.getElementById('client-description').value = '';
            document.getElementById('btn-send-to-tech').classList.add('hidden');
        }

        async function analyzeSelectedTicket() {
            const sid = currentTechSessionId || currentClientSessionId;
            const status = document.getElementById('tech-analysis-status');
            if (!sid) {status.textContent = 'Selecione um chamado na fila.'; return;}
            const button = document.getElementById('tech-analyze'); button.disabled = true;
            status.textContent = 'Consultando referências e preparando sugestão interna…';
            try {
                const response = await fetch('/api/tech/analyze/' + sid, {method:'POST'});
                if (!response.ok) throw new Error('Não foi possível gerar a sugestão. Você pode consultar as evidências e escrever a resposta manualmente.');
                status.textContent = 'Sugestão gerada para revisão do técnico.';
                if (sid === (currentTechSessionId || currentClientSessionId)) await loadLatestTechCase();
            } catch(e) {status.textContent = e.message;}
            finally {button.disabled = false;}
        }
        let queueBusy = false;
        async function refreshClientQueue() {
            if (queueBusy) return;
            queueBusy = true;
            try {
                const response = await fetch('/api/tech/queue', {signal: AbortSignal.timeout(10000)});
                if (!response.ok) throw new Error();
                const items = await response.json();
                const queue = document.getElementById('client-ticket-queue');
                queue.replaceChildren();
                document.getElementById('queue-status').textContent = items.length + ' chamado(s). Atualização automática a cada 5 segundos. Sessões locais temporárias.';
                for (const item of items) {
                    const button = document.createElement('button');
                    button.className = 'rounded-lg border border-slate-600 p-3 text-left text-xs hover:bg-slate-700';
                    button.textContent = item.subject + ' · ' + item.module + ' · ' + item.urgency + ' · ' + item.session_id.slice(0,8);
                    button.onclick = () => {currentTechSessionId = item.session_id; loadLatestTechCase();};
                    queue.append(button);
                }
            } catch (_) {
                document.getElementById('queue-status').textContent = 'Fila indisponível. Tentando novamente automaticamente.';
            } finally {queueBusy = false;}
        }
        setInterval(() => {if(currentActiveTab === 'tech') refreshClientQueue();}, 5000);
        window.addEventListener('DOMContentLoaded', () => {
            const area = new URLSearchParams(location.search).get('area');
            switchTab(area === 'tech' ? 'tech' : 'client');
        });

        function sendToTechDesk() {
            currentTechSessionId = currentClientSessionId;
            switchTab('tech');
            loadLatestTechCase();
        }

        // =========================================================================
        // MESA DO TÉCNICO (N1/N2) — CARREGAMENTO & AÇÕES
        // =========================================================================
        async function loadLatestTechCase() {
            const sid = currentTechSessionId || currentClientSessionId || currentChatSessionId;
            if (!sid) {
                document.getElementById('tech-session-id').innerText = 'Nenhuma sessão ativa iniciada';
                return;
            }

            document.getElementById('tech-session-id').innerText = sid;

            try {
                const resp = await fetch(`/api/tech/report/${sid}`);
                if (!resp.ok) return;
                const data = await resp.json();
                if (sid !== (currentTechSessionId || currentClientSessionId || currentChatSessionId)) return;
                renderTechDashboard(data);
            } catch (err) {
                console.error('Erro ao carregar caso na mesa do técnico:', err);
            }
        }

        function renderTechDashboard(data) {
            const st = data.investigation_state || {};
            window.techEvidence?.load(data);

            // Badges
            document.getElementById('tech-module-badge').innerText = st.module || 'Não especificado';
            document.getElementById('tech-integration-badge').innerText = st.integration || 'Direto / ERP';
            document.getElementById('tech-entity-badge').innerText = st.entity ? `${st.entity.type.toUpperCase()} #${st.entity.id}` : 'Não identificada';

            // Status
            const resStatusEl = document.getElementById('tech-resolution-status');
            resStatusEl.innerText = (st.resolution_status || 'OPEN').toUpperCase();
            if (st.confirmed_cause) {
                resStatusEl.className = "px-2.5 py-0.5 rounded-full text-[10px] font-bold bg-emerald-500/20 text-emerald-300 border border-emerald-500/30";
            } else {
                resStatusEl.className = "px-2.5 py-0.5 rounded-full text-[10px] font-bold bg-amber-500/20 text-amber-300 border border-amber-500/30";
            }

            // Sintomas
            const symptomsList = document.getElementById('tech-symptoms-list');
            if (st.symptoms && st.symptoms.length > 0) {
                symptomsList.innerHTML = st.symptoms.map(s => `• ${s}`).join('<br>');
            } else {
                symptomsList.innerText = 'Nenhum sintoma registrado.';
            }

            // Causa Confirmada
            const causeText = document.getElementById('tech-confirmed-cause-text');
            if (st.confirmed_cause) {
                causeText.innerText = st.confirmed_cause.replace(/_/g, ' ').toUpperCase();
            } else {
                causeText.innerText = 'Ainda não confirmada com 100% de certeza (Investigação em andamento)';
            }

            // Hipóteses
            const hypList = document.getElementById('tech-hypotheses-list');
            let hypHtml = '';
            if (st.hypotheses && st.hypotheses.length > 0) {
                hypHtml += st.hypotheses.map(h => {
                    const pct = Math.round(h.confidence * 100);
                    return `
                        <div class="bg-surface-950 p-2 rounded-xl border border-slate-800 space-y-1">
                            <div class="flex justify-between text-xs font-semibold text-slate-200">
                                <span>${h.cause.replace(/_/g, ' ')}</span>
                                <span class="font-mono text-emerald-400 font-bold">${pct}%</span>
                            </div>
                            <div class="w-full bg-slate-800 h-1.5 rounded-full overflow-hidden">
                                <div class="bg-emerald-500 h-full rounded-full" style="width: ${pct}%"></div>
                            </div>
                        </div>
                    `;
                }).join('');
            }

            if (st.eliminated_hypotheses && st.eliminated_hypotheses.length > 0) {
                hypHtml += '<div class="pt-1.5"><span class="text-[10px] text-red-400 font-bold uppercase tracking-wider">Causas Eliminadas pela Investigação:</span></div>';
                hypHtml += st.eliminated_hypotheses.map(eh => `
                    <div class="text-[11px] text-slate-400 bg-red-500/5 border border-red-500/20 p-2 rounded-lg flex items-start gap-1.5">
                        <span class="text-red-400 font-bold">✗</span>
                        <span><b>${eh.cause.replace(/_/g, ' ')}:</b> ${eh.reason}</span>
                    </div>
                `).join('');
            }
            hypList.innerHTML = hypHtml || '<div class="text-xs text-slate-500 italic">Nenhuma hipótese calculada.</div>';

            // Prescrição Box
            const prescBox = document.getElementById('tech-prescription-box');
            if (data.latest_solution) {
                prescBox.innerHTML = formatChatMessageText(data.latest_solution);
            } else {
                prescBox.innerText = 'O DeskCortex ainda não concluiu a prescrição para este chamado.';
            }

            // Prints & Visão Computacional
            const printsDiv = document.getElementById('tech-vision-prints');
            if (data.vision_evidences && data.vision_evidences.length > 0) {
                printsDiv.innerHTML = data.vision_evidences.map(ev => `
                    <div class="bg-emerald-500/10 border border-emerald-500/20 p-2 rounded-lg text-[11px] text-emerald-300 flex items-start gap-1.5">
                        <i data-lucide="eye" class="w-3.5 h-3.5 shrink-0 mt-0.5"></i>
                        <span>${ev.content}</span>
                    </div>
                `).join('');
            } else {
                printsDiv.innerHTML = '<span class="italic text-[11px] text-slate-500">Nenhum print analisado até o momento.</span>';
            }

            lucide.createIcons();
        }

        async function executeTechAction(actionType) {
            if (actionType === 'approve') return window.techEvidence?.save();
            if (actionType === 'edit') return window.techEvidence?.edit();
            const sid = currentTechSessionId || currentClientSessionId || currentChatSessionId;
            if (!sid) {
                alert('Nenhuma sessão ativa para executar a ação.');
                return;
            }

            const notes = document.getElementById('tech-notes').value;

            try {
                const resp = await fetch('/api/tech/action', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        session_id: sid,
                        action_type: actionType,
                        notes: notes
                    })
                });
                const result = await resp.json();
                if (!resp.ok) throw new Error(result.error || result.detail || 'Falha ao registrar ação.');
                alert(result.message || 'Ação registrada com sucesso na Mesa do Especialista!');
                loadLatestTechCase();
            } catch (err) {
                alert('Erro ao registrar ação: ' + err.message);
            }
        }
    </script>
    <link rel="stylesheet" href="/tech-assets/tech-evidence.css">
    <script src="/tech-assets/tech-evidence.js"></script>
</body>
</html>
    """
    return HTMLResponse(content=html_content)


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


@app.get('/api/tech/queue')
def client_ticket_queue():
    return session_store.client_tickets()


@app.post("/api/client/submit")
def client_submit_endpoint(req: ClientSubmitRequest):
    """
    Abertura de chamado pelo cliente, inicializando a sessão com o Romário.
    """
    session = session_store.get_or_create("")
    session.client_ticket = {'subject': req.subject, 'module': req.module, 'urgency': req.urgency}
    state = session.state

    # Inicializar sintomas e atributos
    state.symptoms.append(f"[{req.urgency.upper()}] {req.subject}: {req.description}")
    state.module = req.module

    session.add_user_turn(req.description)
    return {"session_id": session.session_id, "status": "received"}


@app.post('/api/tech/analyze/{session_id}')
def analyze_client_ticket(session_id: str):
    session = session_store.get(session_id)
    if not session or not session.client_ticket:
        return JSONResponse(status_code=404, content={"error": "Chamado não encontrado."})
    description = next((t.content for t in session.turns if t.role == 'user'), '')
    results = search_endpoint(SearchRequest(subject=session.client_ticket['subject'], problem=description, module=session.state.module))['results']
    result = draft_response(DraftRequest(subject=session.client_ticket['subject'], problem=description, top_results=results))
    if isinstance(result, JSONResponse):
        return result
    session.add_assistant_turn(content=result['draft'], evidence_classified=results[:3])
    return {"status": "ready"}


class TechActionRequest(BaseModel):
    session_id: str
    action_type: str
    notes: str = ""


@app.post("/api/tech/action")
def tech_action_endpoint(req: TechActionRequest):
    if req.action_type in ("approve", "edit"):
        return JSONResponse(status_code=409, content={"error": "Use a revisão versionada em /api/tech/review."})
    if req.action_type not in ("request_screenshot", "escalate"):
        return JSONResponse(status_code=422, content={"error": "Ação inválida."})
    session = session_store.get(req.session_id)
    if not session:
        return JSONResponse(status_code=404, content={"error": "Sessão não encontrada."})

    action_messages = {
        "approve": "Prescrição aprovada pelo especialista e registrada no chamado como solução oficial!",
        "edit": "Modo de edição manual registrado no dossiê técnico.",
        "request_screenshot": "Solicitação de print específico gerada e vinculada à triagem do cliente.",
        "escalate": "Chamado escalado com dossiê investigativo completo para o time N2 / Engenharia."
    }

    if req.action_type == "approve":
        session.state.resolution_status = ResolutionStatus.RESOLVED
    elif req.action_type == "escalate":
        session.state.resolution_status = ResolutionStatus.ESCALATED

    if req.notes:
        session.internal_notes.append(req.notes)

    return {
        "status": "ok",
        "action": req.action_type,
        "message": action_messages.get(req.action_type, "Ação registrada com sucesso."),
        "resolution_status": session.state.resolution_status.value
    }


@app.get("/api/tech/report/{session_id}")
def tech_report_endpoint(session_id: str):
    session = session_store.get(session_id)
    if not session:
        return JSONResponse(status_code=404, content={"error": "Sessão não encontrada."})

    state = session.state

    # Encontrar última resposta do assistente para exibir como prescrição
    latest_solution = ""
    for t in reversed(session.turns):
        if t.role == "assistant":
            latest_solution = t.content
            break

    # Filtrar evidências geradas por visão computacional
    vision_evidences = [
        {"content": e.content, "type": e.type.value}
        for e in state.evidence
        if e.source == "sistema" and ("visualmente" in e.content.lower() or "detectado" in e.content.lower() or "erro" in e.content.lower())
    ]

    return {
        "session_id": session.session_id,
        "investigation_state": state.to_dict(),
        "latest_solution": latest_solution,
        "revision": evidence_revision(session),
        "vision_evidences": vision_evidences,
        "turns_count": len(session.turns)
    }


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


@app.post("/api/client/ticket", summary="Abertura de Chamado pelo Portal do Cliente")
def open_client_ticket_endpoint(req: ClientTicketOpenRequest):
    """
    Abertura de chamado pelo cliente via /cliente:
    - Extrai erro SEFAZ / telecom se houver
    - Executa busca híbrida de conhecimento aterrado nos manuais e tickets reais
    - Gera a Nota Interna da IA (DeskCortex) com diagnóstico e resolução recomendada
    - Salva no TicketStore, caindo imediatamente na Triagem do Técnico (/tecnico)
    """
    # Sanitizar HTML e remover data:image base64 para busca sem poluir embeddings
    raw_desc = req.description or ""
    inline_images = re.findall(r'src=["\'](data:image/[^;]+;base64,[^"\']+)["\']', raw_desc)
    synced_attachments = list(req.attachments)
    for idx, img_data in enumerate(inline_images):
        if not any(a.get("data") == img_data for a in synced_attachments):
            synced_attachments.append({
                "name": f"print_inline_{idx + 1}.png",
                "data": img_data,
                "type": "image/png"
            })

    clean_desc = re.sub(r'data:image/[^;]+;base64,[^\s"\'<>]+', '', raw_desc)
    clean_desc = re.sub(r'<img[^>]*>', ' [print da tela] ', clean_desc)
    clean_desc = re.sub(r'<[^>]+>', ' ', clean_desc)
    clean_desc = ' '.join(clean_desc.split())

    # ── Visão Computacional (DeskCortex Vision): Inspecionar prints e telas anexadas ──
    vision_info = {
        "analyzed": False,
        "error_codes": [],
        "error_messages": [],
        "screen_path": "",
        "module": "",
        "summary": "",
        "extracted_text": ""
    }
    vision_text_parts = []

    for att in synced_attachments:
        att_data = att.get("data") or ""
        att_type = att.get("type") or ""
        if "image" in att_type or "data:image" in att_data:
            try:
                raw_b64 = att_data
                mime_type = "image/png"
                if "," in raw_b64:
                    hdr, raw_b64 = raw_b64.split(",", 1)
                    if "jpeg" in hdr or "jpg" in hdr:
                        mime_type = "image/jpeg"
                    elif "webp" in hdr:
                        mime_type = "image/webp"
                img_bytes = base64.b64decode(raw_b64)
                if len(img_bytes) > 200:
                    print(f"[DeskCortex/Vision] Analisando anexo visual: {att.get('name', 'imagem')} ({len(img_bytes)} bytes)...")
                    vres = vision_analyzer.analyze_screenshot(img_bytes, mime_type=mime_type)
                    if vres:
                        vision_info["analyzed"] = True
                        vision_info["error_codes"] = vres.error_codes
                        vision_info["error_messages"] = vres.error_messages
                        vision_info["screen_path"] = vres.screen_path or ""
                        vision_info["module"] = vres.module_detected or ""
                        vision_info["summary"] = vres.summary_observation or ""
                        if vres.raw_response and not vres.error_messages:
                            vision_info["extracted_text"] = vres.raw_response[:400]

                        if vres.error_codes:
                            vision_text_parts.extend(vres.error_codes)
                        if vres.error_messages:
                            vision_text_parts.extend(vres.error_messages)
                        elif vres.raw_response and len(vres.raw_response) > 20:
                            clean_ocr = re.sub(r'[^a-zA-Z0-9áéíóúâêîôûãõçÁÉÍÓÚÂÊÎÔÛÃÕÇ\s]', ' ', vres.raw_response)
                            ocr_words = [w for w in clean_ocr.split() if len(w) > 3][:15]
                            vision_text_parts.append(" ".join(ocr_words))
                        print(f"[DeskCortex/Vision] Sucesso! Erros lidos: {vres.error_codes} | Mensagens: {vres.error_messages}")
                        break
            except Exception as v_err:
                print(f"[DeskCortex/Vision] Falha ao inspecionar anexo visual: {v_err}")

    error_code = None
    full_text = f"{req.subject} {clean_desc}"
    m_code = re.search(r'\b(10060|10061|10054|204|481|783|539|694|629|388|1824|20419)\b', full_text)
    if not m_code:
        m_code = re.search(r'rejei[cç][aã]o\s*[:\-]?\s*(\d{3,5})', full_text, re.I)
    if m_code:
        error_code = m_code.group(1) if m_code.lastindex else m_code.group(0)

    # Se o cliente não digitou o código de erro, usar o código lido no print pela visão computacional
    if not error_code and vision_info["error_codes"]:
        error_code = vision_info["error_codes"][0]

    vision_snippet = " ".join(vision_text_parts)
    query_search = f"{req.subject} {clean_desc} {vision_snippet} {error_code or ''}".strip()
    top_results = []
    try:
        embedding = get_query_embedding(query_search)
        with psycopg.connect(POSTGRES_DSN, connect_timeout=3, row_factory=dict_row) as conn:
            if embedding:
                results = hybrid_search(conn, query_search, embedding, module="TODOS", top_k=3)
            else:
                results = lexical_search(conn, query_search, module="TODOS", top_k=3)
            for r in results:
                top_results.append(dict(r))
    except Exception as e:
        print(f"[DeskCortex] Erro na busca de conhecimento do chamado cliente: {e}")

    top_sol = top_results[0] if top_results else None

    # Síntese Inteligente com TriageSynthesizer
    synth_desc = clean_desc
    if vision_info["analyzed"] and vision_info["error_messages"]:
        synth_desc = f"{clean_desc}\n[Print do Cliente]: {' | '.join(vision_info['error_messages'])}".strip()

    triage_pkg = TriageSynthesizer.synthesize(
        subject=req.subject,
        description=synth_desc,
        error_code=error_code,
        requester_name=req.requester,
        company_name=req.company,
        search_results=top_results
    )

    steps_text = "\n".join([f"{i+1}. {s}" for i, s in enumerate(triage_pkg["procedure_steps"])])

    new_ticket = {
        "subject": req.subject,
        "description": req.description,
        "client": {
            "name": req.requester,
            "organization": req.company,
            "email": req.email,
            "phone": req.phone
        },
        "responsible": "Lucas Souza",
        "category": "Análise Técnica",
        "service": "Suporte Técnico ERP Master",
        "urgency": req.urgency,
        "status": "Novo",
        "master_env": {
            "hosting": req.hosting,
            "version": "12.4.2",
            "is_beta": False,
            "server": f"{req.hosting} Datacenter | Firebird 3.0" if req.hosting != "Localhost" else "127.0.0.1 (Localhost / Próprio) | Firebird 3.0",
            "integrations": ["Mercado Livre", "Shopee", "Nuvemshop"]
        },
        "attachments": synced_attachments,
        "ai_internal_note": {
            "diagnosis": triage_pkg["diagnosis"],
            "procedure_steps": triage_pkg["procedure_steps"],
            "possible_resolution": steps_text,
            "internal_action_html": triage_pkg["internal_action_html"],
            "client_ready_response": triage_pkg["client_ready_response"],
            "confidence": triage_pkg["confidence"],
            "screen_path": triage_pkg["screen_path"],
            "shortcuts": triage_pkg["shortcuts"],
            "references": [top_sol.get("title")] if top_sol else ["Base de Conhecimento Master"],
            "vision_analysis": vision_info
        }
    }

    created = ticket_store.create(new_ticket)
    return {
        "success": True,
        "ticket_id": created["id"],
        "ticket": created
    }


@app.get("/api/client/my-tickets", summary="Listagem de Chamados Restritos ao Cliente")
def client_my_tickets_endpoint(company: str = "", user: str = "", email: str = ""):
    return ticket_store.get_by_client(client_name=user, organization=company, email=email)


@app.get("/api/tickets/all", summary="Todos os Tickets para Triagem do Técnico")
def get_all_tickets_endpoint(q: str = ""):
    tickets = ticket_store.list_all(q)
    return {
        "total": len(tickets),
        "tickets": tickets
    }


@app.get("/api/tickets/{ticket_id}", summary="Detalhes Completos do Chamado para Atendimento Técnico")
def get_ticket_details_endpoint(ticket_id: int):
    t = ticket_store.get(ticket_id)
    if not t:
        return JSONResponse(status_code=404, content={"error": "Ticket não encontrado."})
    return t


class TicketActionSaveRequest(BaseModel):
    action_type: str  # 'public' ou 'internal'
    content: str
    author: str = "Lucas Souza (Suporte)"
    time_spent: str = "15 min"
    new_status: str | None = None


@app.post("/api/tickets/{ticket_id}/action", summary="Adicionar Acao e Salvar Chamado")
def add_ticket_action_endpoint(ticket_id: int, req: TicketActionSaveRequest):
    updated = ticket_store.add_action(
        ticket_id=ticket_id,
        action_type=req.action_type,
        author=req.author,
        content=req.content,
        time_spent=req.time_spent,
        new_status=req.new_status
    )
    if not updated:
        return JSONResponse(status_code=404, content={"error": "Ticket nao encontrado."})
    return {"success": True, "ticket": updated}


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


@app.get("/api/tickets/{ticket_id}/similar", summary="Buscar Tickets e KB Semelhantes ao Chamado")
def get_similar_tickets_endpoint(ticket_id: int, q: str = ""):
    """Retorna ticket_results (chamados historicos) e knowledge_results (KB/Manual)."""
    ticket = ticket_store.get(ticket_id)
    if not ticket:
        return JSONResponse(status_code=404, content={"error": "Ticket nao encontrado."})

    clean_q = q.strip().lower()

    # Cache instantâneo (< 1ms): se já carregou este chamado e não há filtro customizado
    if not clean_q and ticket.get("similar_cache", {}).get("search_version") == "evidence-v1":
        return ticket["similar_cache"]

    cache_key = (ticket_id, clean_q)
    if clean_q and cache_key in SIMILAR_SEARCH_CACHE:
        return SIMILAR_SEARCH_CACHE[cache_key]

    ai_note = ticket.get("ai_internal_note", {})

    def strip_html(raw):
        c = _re_similar.sub(r"<[^>]+>", " ", raw or "")
        return _re_similar.sub(r"\s+", " ", c).strip()[:350]

    def build_clean_query(t):
        # Query fiel baseada no assunto, relato do cliente e dados visuais extraidos do print
        subj = t.get("subject", "").strip()
        desc = strip_html(t.get("description", ""))
        
        va = t.get("ai_internal_note", {}).get("vision_analysis", {})
        va_terms = []
        if va.get("error_codes"):
            va_terms.extend(va["error_codes"])
        if va.get("error_messages"):
            va_terms.extend(va["error_messages"])
        elif va.get("extracted_text"):
            clean_va = re.sub(r'[^a-zA-Z0-9áéíóúâêîôûãõçÁÉÍÓÚÂÊÎÔÛÃÕÇ\s]', ' ', va["extracted_text"])
            va_terms.append(" ".join([w for w in clean_va.split() if len(w) > 3][:12]))

        va_str = " ".join(va_terms)
        return f"{subj} {desc} {va_str}".strip()[:350]

    # Busca on-demand com query limpa e enriquecida por visao
    query_text = (q if q else build_clean_query(ticket)).strip()[:350]
    kb_results, tk_results = [], []
    evidence_assessment = {"status": "search_unavailable", "can_support_procedure": False}

    try:
        emb = get_query_embedding(query_text)
        with psycopg.connect(POSTGRES_DSN, connect_timeout=4, row_factory=dict_row) as conn:
            if emb:
                raw = hybrid_search(conn, query_text, emb, module="TODOS", top_k=15)
            else:
                raw = lexical_search(conn, query_text, module="TODOS", top_k=15)

            from knowledge_access import assessment
            evidence_assessment = assessment(raw)
            for r in raw:
                st = r.get("source_type") or "TICKET"
                sc = float(r.get("combined_score", r.get("final_score", 0.0)))
                
                # Normalizacao de pontuacao realista com corte estrito
                if sc >= 1.2:
                    pct = min(99, int(90 + (sc - 1.2) * 8))
                elif sc >= 0.8:
                    pct = int(80 + (sc - 0.8) * 25)
                elif sc >= 0.5:
                    pct = int(65 + (sc - 0.5) * 50)
                else:
                    pct = int(sc * 100)

                # CORTE ESTRITO: se relevância < 65%, nao exibe
                if pct < 65:
                    continue

                node = {
                    "node_id": r.get("id"),
                    "source_type": st,
                    "source_external_id": r.get("source_external_id"),
                    "title": r.get("title") or "",
                    "problem": r.get("problem") or "",
                    "diagnosis": r.get("diagnosis") or "",
                    "solution": r.get("solution") or "",
                    "screen_path": r.get("screen_path") or "",
                    "shortcut_keys": r.get("shortcut_keys") or [],
                    "modules": r.get("modules") or [],
                    "detected_error_codes": r.get("detected_error_codes") or [],
                    "match_pct": pct,
                    "retrieval_score": r.get("retrieval_score"),
                    "answer_use": r.get("answer_use"),
                    "quality_flags": r.get("quality_flags", []),
                    "source_url": r.get("source_url"),
                    "date": r.get("formatted_date") or "",
                    "tier": r.get("tier") or "",
                    "is_helpful": False,
                    "technician_comment": "",
                }
                if st in ("MANUAL", "OFFICIAL_KB"):
                    kb_results.append(node)
                else:
                    tk_results.append(node)

            # Buscar tambem chamados com termos tecnicos especificos no refined_tickets
            # Corpus expansion must pass the same signature and evidence checks.
            extra_tickets = []

            # Unificar por external_id ou node_id mantendo a maior pontuacao
            unified_map = {}
            for item in (tk_results + extra_tickets):
                eid = str(item.get("source_external_id") or item.get("node_id"))
                if eid not in unified_map or item["match_pct"] > unified_map[eid]["match_pct"]:
                    unified_map[eid] = item

            final_tickets = list(unified_map.values())
            final_tickets.sort(key=lambda x: x["match_pct"], reverse=True)
            tk_results = [t for t in final_tickets if t["match_pct"] >= 65][:5]

    except Exception as e:
        print(f"[DeskCortex/Similar] Erro na busca: {e}")

    kb_results.sort(key=lambda x: x["match_pct"], reverse=True)
    kb_results = [k for k in kb_results if k["match_pct"] >= 65][:5]

    # Atualizar cache preservando feedback existente do tecnico
    old_fb = {str(r.get("node_id")): r for r in ai_note.get("similar_refs", [])}
    for ref in (kb_results + tk_results):
        nid = str(ref.get("node_id"))
        if nid in old_fb:
            ref["is_helpful"] = old_fb[nid].get("is_helpful", False)
            ref["technician_comment"] = old_fb[nid].get("technician_comment", "")

    ai_note["similar_refs"] = kb_results + tk_results
    ticket_store._save()

    resp_data = {
        "ticket_id": ticket_id,
        "knowledge_results": kb_results,
        "ticket_results": tk_results,
        "cached": False,
        "search_version": "evidence-v1",
        "evidence_assessment": evidence_assessment,
    }

    if not clean_q:
        ticket["similar_cache"] = resp_data
        ticket_store._save()
    else:
        SIMILAR_SEARCH_CACHE[cache_key] = resp_data

    return resp_data


class SimilarFeedbackRequest(BaseModel):
    node_id: int
    is_helpful: bool
    comment: str = ""
    technician: str = "Lucas Souza"


@app.post("/api/tickets/{ticket_id}/similar-feedback", summary="Registrar Feedback de Ticket Semelhante")
def similar_feedback_endpoint(ticket_id: int, req: SimilarFeedbackRequest):
    """Registra validacao do tecnico: eleva confianca e salva nota interna."""
    updated = ticket_store.update_similar_feedback(
        ticket_id=ticket_id,
        node_id=req.node_id,
        is_helpful=req.is_helpful,
        comment=req.comment,
        technician=req.technician,
    )
    if not updated:
        return JSONResponse(status_code=404, content={"error": "Ticket nao encontrado."})
    ai_note = updated.get("ai_internal_note", {})
    return {
        "success": True,
        "new_confidence": ai_note.get("confidence"),
        "validated_by_technician": ai_note.get("validated_by_technician", False),
    }


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


@app.get("/api/tickets/historical/{ticket_id}", summary="Detalhes Completos do Chamado Historico do Movidesk")
def get_historical_ticket_detail_endpoint(ticket_id: int):
    """
    Retorna o chamado historico real completo com todas as acoes,
    htmlDescription formatado e imagens reescritas para visualizacao inline com zoom.
    """
    cache_file = HIST_CACHE_DIR / f"{ticket_id}.json"
    if cache_file.exists():
        try:
            with open(cache_file, "r", encoding="utf-8") as fp:
                return json.load(fp)
        except Exception:
            pass

    url = f"{MOVIDESK_BASE_URL}/tickets?token={MOVIDESK_TOKEN}&id={ticket_id}&$expand=actions"
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "DeskCortex/1.0"})
    
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"[DeskCortex/HistoricalDetail] Erro ao consultar API do Movidesk: {e}")
        return JSONResponse(status_code=500, content={"error": f"Falha ao consultar Movidesk: {str(e)}"})

    processed_actions = []
    for act in data.get("actions", []):
        if act.get("isDeleted"):
            continue
            
        html = act.get("htmlDescription") or act.get("description") or ""
        
        # Reescrever URLs expiradas da AWS S3 para nossa rota local de proxy/cache
        def _replace_img(match):
            f_hash = match.group(1)
            return f'src="/api/movidesk/image/{f_hash}"'
            
        html = re.sub(r'src=["\']https?://s3\.amazonaws\.com/movidesk-files/([A-F0-9]{32})[^"\']*["\']', _replace_img, html)
        
        created_by = act.get("createdBy") or {}
        author_name = created_by.get("businessName") or "Analista de Suporte"
        
        created_at_raw = act.get("createdDate") or ""
        created_at_fmt = ""
        if created_at_raw:
            try:
                dt_part = created_at_raw.split("T")[0]
                tm_part = created_at_raw.split("T")[1][:5]
                y, m, d = dt_part.split("-")
                created_at_fmt = f"{d}/{m}/{y} {tm_part}"
            except Exception:
                created_at_fmt = created_at_raw[:16]

        processed_actions.append({
            "id": act.get("id"),
            "type": act.get("type"),
            "author": author_name,
            "created_at": created_at_fmt,
            "html_content": html
        })

    clients = data.get("clients") or [{}]
    client_name = clients[0].get("businessName") if clients else "Cliente"

    created_raw = data.get("createdDate") or ""
    created_fmt = ""
    if created_raw:
        try:
            dt_part = created_raw.split("T")[0]
            tm_part = created_raw.split("T")[1][:5]
            y, m, d = dt_part.split("-")
            created_fmt = f"{d}/{m}/{y} {tm_part}"
        except Exception:
            created_fmt = created_raw[:16]

    result = {
        "id": data.get("id"),
        "subject": data.get("subject"),
        "status": data.get("status") or "Fechado",
        "created_date": created_fmt,
        "client": client_name,
        "actions": processed_actions
    }

    try:
        with open(cache_file, "w", encoding="utf-8") as fp:
            json.dump(result, fp, ensure_ascii=False, indent=2)
    except Exception:
        pass

    return result


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
