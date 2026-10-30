"""Local, read-mostly administration console for DeskCortex."""
from __future__ import annotations

import json
import hashlib
import csv
import io
import base64
import os
import re
import sqlite3
import time
import urllib.error
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from threading import RLock
from urllib.parse import quote, unquote, urlparse

import psycopg
from psycopg.rows import dict_row
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, Field

from config import POSTGRES_DSN, GEMINI_API_KEY
import agent_operations as operations
import agent_control
import knowledge_curation as curation
import overview_insights as insights
import ticket_dashboard
import ticket_sync_control
import ticket_trends as trends
import ticket_flow
import agent_evaluation
import movidesk_webhook
import movidesk_note_worker
import triage_router
import knowledge_access
from typing import Literal
from datetime import date

router = APIRouter()
STATIC = Path(__file__).parent / "static"
STATE_FILE = Path(__file__).parent / "output" / "agent_admin_state.json"
_lock = RLock()
_catalog_cache = {'at': 0, 'rows': []}

OPEN_FACT_COLUMNS = """
    f.external_id, f.dashboard_data->>'subject' AS subject,
    f.dashboard_data->>'category' AS category,
    f.dashboard_data->>'urgency' AS urgency,
    f.dashboard_data->>'status' AS status,
    f.dashboard_data->>'base_status' AS base_status,
    NULLIF(f.dashboard_data->>'created_date', '')::timestamptz AS created_date,
    f.dashboard_data->>'problem_statement' AS problem_statement,
    f.dashboard_data->>'technical_narrative' AS technical_narrative,
    f.dashboard_data->>'client_business_name' AS client_business_name,
    f.dashboard_data->'clients' AS clients,
    f.dashboard_data->'owner' AS owner,
    f.dashboard_data->>'owner_team' AS owner_team,
    f.current_as_of
"""

DEFAULT_SPECIALISTS = [
    {
        "id": "products",
        "name": "Processos & Cadastros Master",
        "icon": "📦",
        "modules": ["products", "registration"],
        "enabled": True,
        "description": "Dúvidas de operação: cadastro de produtos, grade (tamanho/cor), tabelas de preço, inventário e atalhos de tela.",
        "prompt": "Você é especialista em usabilidade e processos do ERP Master. Oriente o cliente com o passo a passo exato dos menus (ex: Estoque > Produtos > F3 > aba Grade) e atalhos de tela reais. Nunca sugira comandos SQL ou alterações no banco para dúvidas operacionais.",
        "keywords": ["produto", "cadastro", "grade", "tamanho", "cor", "preco", "tabela de preco", "inventario", "estoque", "f3", "f4"]
    },
    {
        "id": "fr3_logistics",
        "name": "FastReport FR3 & Etiquetas",
        "icon": "🏷️",
        "modules": ["logistics", "products"],
        "enabled": True,
        "description": "Layout de etiquetas, arquivos .fr3, impressoras térmicas (Zebra, Argox, Elgin), margens e relatórios personalizados.",
        "prompt": "Você é especialista em FastReport (.fr3) e impressão de etiquetas do ERP Master. Oriente configurações de página, bandas de dados, margens em milímetros, fontes de código de barras (Code128, EAN) e dimensões de bobina nas impressoras térmicas. Diferencie problemas de layout físico de problemas de API/marketplace.",
        "keywords": [".fr3", "fastreport", "etiqueta", "zebra", "argox", "elgin", "termica", "bobina", "margem", "cortada", "desalinhada", "codigo de barras", "relatorio personalizado"]
    },
    {
        "id": "reports",
        "name": "Relatórios & Indicadores",
        "icon": "📊",
        "modules": ["reports"],
        "enabled": True,
        "description": "Catálogo e uso dos relatórios de Vendas, Estoque, Financeiro, Compras, Fiscal e indicadores como margem, markup, CMV e Curva ABC.",
        "prompt": (
            "Você é especialista no catálogo de relatórios e indicadores do ERP Master. "
            "Identifique a necessidade de negócio, selecione o relatório adequado e explique seus filtros, "
            "colunas e interpretação usando somente procedimentos recuperados da base de conhecimento. "
            "Consulte fontes de todos os módulos relacionados ao relatório. Diferencie margem/markup/CMV "
            "de margem física de impressão e diferencie uso do relatório de erro, divergência ou customização FR3. "
            "Nunca invente caminho de menu, nome de campo ou regra de cálculo ausente nas fontes."
        ),
        "keywords": [
            "relatorio", "relatorios", "indicador", "indicadores", "margem de venda", "margem de lucro",
            "markup", "cmv", "rentabilidade", "curva abc", "estatistica de vendas", "vendas por produto",
            "posicao de estoque", "fechamento", "projecao de compras"
        ]
    },
    {
        "id": "integrations",
        "name": "Hubs & Integrações",
        "icon": "🔌",
        "modules": ["integrations"],
        "enabled": True,
        "description": "AnyMarket, Mercado Livre, Shopee, Loja Integrada, VTEX, Sincronizador Master, ocorrências e APIs de marketplace.",
        "prompt": "Você é especialista em integrações e e-commerce do ERP Master da Alternativa Sistemas.\n\nREGRA PADRÃO PARA FALHAS DE INTEGRAÇÃO:\nQuando o cliente relatar que um produto não subiu, pedido não desceu, estoque ou status não atualizou no site/marketplace:\n1. Oriente SEMPRE a conferir a tela de Ocorrências no Sincronizador do Master:\n   - Abrir o Sincronizador do Master (conferir se os serviços na lateral esquerda estão com ícone verde).\n   - Clicar no botão 'Ocorrências' no canto inferior esquerdo.\n   - No topo da grade, filtrar a coluna 'Integração' selecionando o integrador com problema (ex: LOJA INTEGRADA, MERCADOLIVRE, ANYMARKET, SHOPEE).\n   - No campo de busca inferior, definir o período (ex: Últimos 7 dias), digitar o código do produto ou número da venda/pedido e clicar em 'Procurar'.\n   - A tela exibirá a ocorrência com a mensagem de erro exata retornada pela API.\n   - Instrua o cliente a selecionar a mensagem de erro, dar Ctrl+C e colar no chamado (ou anexar o print).\n2. Se o cliente já tiver colado o erro da ocorrência, analise o código do erro e forneça o ajuste exato.",
        "keywords": [
            "loja integrada", "lojas integradas", "site", "subindo para o site", "subindo", "subiu", "nao sobe",
            "nao desce", "sincronizador", "ocorrencias", "ocorrencia", "integracao", "integracoes", "integrador",
            "anymarket", "mercado livre", "mercadolivre", "shopee", "vtex", "tray", "magalu", "marketplace",
            "pedido nao desceu", "produto nao subiu", "estoque nao atualiza", "status nao atualiza", "api token"
        ]
    },
    {
        "id": "fiscal",
        "name": "Fiscal & SEFAZ",
        "icon": "📑",
        "modules": ["fiscal"],
        "enabled": True,
        "description": "NF-e, NFC-e, Rejeições SEFAZ (204, 539, 694), CFOP, CSOSN, CST, DIFAL e transmissão de documentos.",
        "prompt": "Você é especialista fiscal do ERP Master. Decodifique os erros e rejeições da SEFAZ apontando exatamente qual campo ou tag no cadastro do Master precisa de correção. Nunca forneça consultoria tributária ou determine alíquotas por conta própria.",
        "keywords": ["nfe", "nfce", "rejeicao", "sefaz", "cfop", "csosn", "cst", "difal", "xml", "danfe", "transmissao", "204", "539", "481", "783"]
    },
    {
        "id": "sales_pdv",
        "name": "PDV & Frente de Caixa",
        "icon": "🏪",
        "modules": ["sales"],
        "enabled": True,
        "description": "Operação de balcão, caixa travado, cupom fiscal pendente, contingência, sangria e suprimento.",
        "prompt": "Você é especialista na Frente de Caixa (PDV) do ERP Master. Priorize rapidez e clareza absoluta, pois o operador está com cliente no balcão. Oriente procedimentos de desbloqueio, contingência offline e fechamento de cupom.",
        "keywords": ["caixa", "pdv", "balcao", "cupom", "travado", "contingencia", "sangria", "suprimento", "gaveta", "leitor"]
    },
    {
        "id": "infrastructure",
        "name": "Banco de Dados & Infraestrutura",
        "icon": "🗄️",
        "modules": ["infrastructure"],
        "enabled": True,
        "description": "Banco Firebird (.fdb), config.ini, travamentos Delphi, conexões de rede e scripts homologados.",
        "prompt": "Você é especialista de infraestrutura e banco Firebird do ERP Master. Diagnostique quedas de serviço, arquivos de configuração (.ini) e violações de chave. Nunca forneça scripts destrutivos; recomende sempre backup antes de qualquer intervenção.",
        "keywords": ["firebird", ".fdb", "config.ini", "gfix", "violation", "delphi", "rede", "servidor", "conexao", "backup"]
    }
]

DEFAULT_STATE = {
    "draft_generation_enabled": True,
    "source_types": ["OFFICIAL_KB", "VALIDATED_TICKET", "PROBABLE_TICKET", "MANUAL"],
    "human_review_required": True,
    "auto_send_enabled": False,
    "min_interval_seconds": 5,
    "max_per_minute": 10,
    "max_concurrent": 2,
    "pilot_person_ids": [],
    "pilot_organization_ids": [],
    "pilot_requester_emails": ["lucas.teste@alternativa.com.br"],
    "pilot_paused": True,
    "sandbox_mode": True,
    "whitelist": ["lucas.teste@alternativa.com.br"],
    "auto_internal_note": False,
    "automation_released_at": None,
    "company_authorized_at": {},
    "specialists": DEFAULT_SPECIALISTS,
}

TOPICS = {
    "Fiscal": ["nfe", "nf-e", "nfce", "nf-ce", "nota fiscal", "sefaz", "cfop", "inscrição estadual", "rejeição"],
    "Integrações": ["integração", "integracao", "sincronizador", "marketplace", "anymarket", "skyhub", "wehub", "vtex", "tray"],
    "Produtos": ["produto", "cadastro", "preço", "preco", "grade", "estoque", "sku"],
    "Vendas e pedidos": ["pedido", "venda", "faturamento", "workflow", "status do pedido"],
    "Logística": ["frete", "transportadora", "peso cúbico", "etiqueta", "rastreamento", "rastreio", "expedição"],
    "Financeiro": ["boleto", "duplicata", "contas a pagar", "contas a receber", "cobrança", "pagamento"],
    "Infraestrutura": ["banco de dados", "firebird", "conexão", "servidor", "backup", "lentidão", "access violation"],
}


def _local_only(request: Request):
    if getattr(request.state, "cortex_authenticated", False):
        user = getattr(request.state, 'cortex_user_obj', None) or {}
        if user.get('role') and user['role'] != 'super_admin':
            raise HTTPException(403, 'Acesso administrativo necessário.')
        return
    host = request.client.host if request.client else ""
    if host not in {"127.0.0.1", "::1", "testclient", "localhost"}:
        raise HTTPException(403, "Console administrativo disponível somente nesta máquina.")


def get_state():
    with _lock:
        try:
            state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            merged = {**DEFAULT_STATE, **state, "human_review_required": True, "auto_send_enabled": False}
            configured = merged.get("specialists") or []
            configured_ids = {item.get("id") for item in configured if isinstance(item, dict)}
            # New built-in specialists must appear after an application update
            # without overwriting prompts or enable/disable choices already saved.
            merged["specialists"] = list(configured) + [
                item for item in DEFAULT_SPECIALISTS if item.get("id") not in configured_ids
            ]
            from automation_scope import restore_release_dates
            return restore_release_dates(merged, STATE_FILE.parent / 'agent_operations.sqlite3')
        except (OSError, ValueError, TypeError):
            return dict(DEFAULT_STATE)


def set_state(update: dict, actor=None):
    with _lock:
        return _set_state(update, actor)


def _set_state(update: dict, actor=None):
    allowed = {
        "draft_generation_enabled", "source_types", "min_interval_seconds",
        "max_per_minute", "max_concurrent", "pilot_person_ids", "pilot_organization_ids",
        "pilot_requester_emails",
        "pilot_paused", "sandbox_mode", "whitelist", "auto_internal_note", "specialists", "automation_rules"
    }
    if set(update) - allowed:
        raise HTTPException(422, "Configuração não permitida.")
    state = get_state()
    previous = dict(state)
    if 'automation_rules' in update:
        import automation_rules
        with _conn() as conn:
            known = {r['status'] for r in conn.execute('SELECT DISTINCT status FROM refined_tickets WHERE status IS NOT NULL').fetchall()}
        state['automation_rules'] = automation_rules.validate(update['automation_rules'], known)
        if set(state['automation_rules']['companies']) - set(state['pilot_organization_ids']):
            raise HTTPException(422, 'Exceções exigem uma empresa previamente autorizada.')
    for key, low, high in [('min_interval_seconds', 0, 300), ('max_per_minute', 1, 60), ('max_concurrent', 1, 5)]:
        if key in update:
            if type(update[key]) is not int or not low <= update[key] <= high:
                raise HTTPException(422, "Limite de processamento inválido.")
            state[key] = update[key]
    if "draft_generation_enabled" in update:
        state["draft_generation_enabled"] = bool(update["draft_generation_enabled"])
    if "pilot_paused" in update:
        if type(update["pilot_paused"]) is not bool:
            raise HTTPException(422, "Estado do piloto inválido.")
        state["pilot_paused"] = update["pilot_paused"]
    if "sandbox_mode" in update:
        state["sandbox_mode"] = bool(update["sandbox_mode"])
    if "auto_internal_note" in update:
        state["auto_internal_note"] = bool(update["auto_internal_note"])
    if "whitelist" in update:
        raw_wl = update["whitelist"]
        if not isinstance(raw_wl, list):
            raise HTTPException(422, "Whitelist deve ser uma lista.")
        clean_wl = [str(x).strip() for x in raw_wl if str(x).strip()]
        state["whitelist"] = list(dict.fromkeys(clean_wl))[:100]
    if "pilot_requester_emails" in update:
        raw_emails = update["pilot_requester_emails"]
        if not isinstance(raw_emails, list):
            raise HTTPException(422, "Solicitantes autorizados devem ser uma lista.")
        clean_emails = [str(value).strip().casefold() for value in raw_emails if str(value).strip()]
        if (len(clean_emails) > 100 or
                any(not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value) for value in clean_emails)):
            raise HTTPException(422, "E-mail de solicitante inválido.")
        state["pilot_requester_emails"] = list(dict.fromkeys(clean_emails))
    if "specialists" in update:
        specs = update["specialists"]
        if not isinstance(specs, list):
            raise HTTPException(422, "Especialistas devem ser uma lista.")
        state["specialists"] = specs
    if "source_types" in update:
        permitted = {"OFFICIAL_KB", "VALIDATED_TICKET", "PROBABLE_TICKET", "MANUAL"}
        sources = update["source_types"]
        if not isinstance(sources, list) or not set(sources) <= permitted:
            raise HTTPException(422, "Tipos de fonte inválidos.")
        state["source_types"] = sorted(set(sources))
    for key in ("pilot_person_ids", "pilot_organization_ids"):
        if key in update:
            values = update[key]
            maximum = 10000 if key == 'pilot_organization_ids' else 100
            if not isinstance(values, list) or len(values) > maximum or any(not isinstance(x, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", x) for x in values):
                raise HTTPException(422, "Identificadores do piloto inválidos.")
            state[key] = sorted(set(values))
    state["human_review_required"] = True
    state["auto_send_enabled"] = False
    now = time.time()
    selected = set(state['pilot_organization_ids'])
    authorized_at = previous.get('company_authorized_at') or {}
    state['company_authorized_at'] = {key: authorized_at.get(key, now) for key in selected}
    released = not state['pilot_paused']
    was_released = not previous.get('pilot_paused', True)
    publication_started = state['auto_internal_note'] and not previous.get('auto_internal_note', False)
    explicit_release = bool(set(update) & {'pilot_paused','auto_internal_note','pilot_organization_ids'})
    if released and (not was_released or publication_started or
                     (explicit_release and not state.get('automation_released_at'))):
        state['automation_released_at'] = now
    with _lock:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        temp = STATE_FILE.with_suffix(".tmp")
        temp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temp, STATE_FILE)
    operations.audit('Configuração do agente', {'actor': actor, 'before': previous, 'after': state})
    return state


def _conn():
    return psycopg.connect(POSTGRES_DSN, connect_timeout=5, row_factory=dict_row,
                           options="-c statement_timeout=15000 -c default_transaction_read_only=on")


@router.get('/api/admin/ticket-context/{ticket_id}')
def ticket_context(ticket_id: int, request: Request):
    _local_only(request)
    if ticket_id <= 0:
        raise HTTPException(422, 'Número de chamado inválido.')
    with _conn() as conn:
        row = conn.execute('''SELECT r.external_id,r.subject,r.problem_statement,
            r.client_business_name,r.created_date,w.payload->>'type' AS ticket_type
            FROM refined_tickets r JOIN raw_tickets w ON w.id=r.raw_ticket_id
            WHERE r.external_id=%s AND w.source_provider='movidesk'
            ORDER BY r.refined_at DESC LIMIT 1''', (ticket_id,)).fetchone()
    if not row:
        raise HTTPException(404, 'Chamado não encontrado na base local.')
    return {'ticket_id': row['external_id'], 'subject': row['subject'] or '',
            'problem': row['problem_statement'] or '',
            'company': row['client_business_name'] or '',
            'ticket_type': row['ticket_type'],
            'created_at': row['created_date'].isoformat() if row['created_date'] else None}


@router.get('/api/admin/evaluations')
def evaluation_cases(request: Request):
    _local_only(request)
    return agent_evaluation.list_cases()


@router.get('/api/admin/webhooks/receipts')
def webhook_receipts(request: Request):
    _local_only(request)
    result = movidesk_webhook.receipt_summary()
    ceiling = os.getenv('CORTEX_AUTONOTE_MODE', 'disabled')
    state = get_state()
    selected = state.get('pilot_organization_ids') or []
    requesters = state.get('pilot_requester_emails') or []
    enabled = (not state.get('pilot_paused', True) and
               state.get('auto_internal_note', False) and bool(selected))
    result['ceiling_mode'] = ceiling
    result['effective_mode'] = ('internal_note' if ceiling == 'internal_note' and enabled else
                                'dry_run' if ceiling in {'dry_run', 'internal_note'} else 'disabled')
    result['selected_organizations'] = len(selected)
    result['selected_requesters'] = len(requesters)
    result['pilot_paused'] = state.get('pilot_paused', True)
    result['requested_auto_internal_note'] = state.get('auto_internal_note', False)
    pilot_ready = (not result['pilot_paused'] and bool(selected))
    result['manual_mode'] = ('internal_note' if ceiling == 'internal_note' and pilot_ready else 'disabled')
    # Backward-compatible name used by older panels.
    result['configured_mode'] = result['effective_mode']
    return result


def persist_live_ticket(ticket):
    """Update one ticket and its company links without rebuilding the snapshot."""
    try:
        movidesk_note_worker.persist_operational_ticket(ticket)
    except movidesk_note_worker.RetryJob as exc:
        raise ValueError(str(exc)) from exc


@router.post('/api/admin/webhooks/pull/{ticket_id}')
def pull_webhook_ticket(ticket_id: int, request: Request):
    """Fetch a pilot ticket now and enqueue its latest customer action."""
    _local_only(request)
    if ticket_id <= 0:
        raise HTTPException(422, 'Número do chamado inválido.')
    try:
        base_settings = movidesk_note_worker.Settings.load()
        settings = movidesk_note_worker.effective_settings(base_settings, STATE_FILE)
        if not settings.manual_internal_note:
            raise HTTPException(409, 'Ative o piloto e mantenha empresa e solicitante autorizados para usar o processamento manual.')
        api = movidesk_note_worker.Api(settings)
        try:
            ticket = api.ticket(ticket_id)
        finally:
            api.client.close()
    except HTTPException:
        raise
    except (ValueError, movidesk_note_worker.RetryJob) as exc:
        raise HTTPException(503, 'Não foi possível consultar o chamado no Movidesk: ' + str(exc)) from exc

    allowed, reason = movidesk_note_worker.eligible(ticket, settings)
    if not allowed:
        labels = {
            'fora_do_piloto': 'A empresa do chamado não está autorizada no piloto.',
            'chamado_interno_ou_excluido': 'O chamado é interno ou está excluído.',
            'chamado_finalizado': 'O chamado já está finalizado.',
            'sem_nova_mensagem_do_cliente': 'A última interação não é uma nova mensagem do cliente.',
            'solicitante_fora_do_piloto': 'O solicitante da última mensagem não está autorizado no piloto.',
        }
        raise HTTPException(409, labels.get(reason, 'O chamado não está elegível para o piloto.'))

    # Keep this exact live ticket available to dashboards and company-scoped
    # retrieval. A manual pull must not require a full Movidesk synchronization.
    try:
        persist_live_ticket(ticket)
    except (psycopg.Error, ValueError, TypeError, KeyError) as exc:
        raise HTTPException(503, 'O chamado foi consultado no Movidesk, mas não pôde ser atualizado na base local.') from exc

    history = movidesk_note_worker.actions(ticket, settings)
    latest = history[-1]
    key, action_id = movidesk_note_worker.identity(ticket, settings)
    marker = movidesk_note_worker.PREFIX + key
    if movidesk_note_worker.marker_present(ticket, marker):
        return {'ticket_id': ticket_id, 'action_id': action_id, 'status': 'already_processed'}

    # The nonce makes an explicit retry a new queue event. The outbox identity
    # still guarantees that repeated clicks cannot publish the same note twice.
    payload = {
        'Id': int(ticket['id']),
        'Actions': [{'Id': int(latest.get('id') or 0),
                     'Description': latest.get('description') or ''}],
        'CortexManualPull': time.time_ns(),
    }
    try:
        movidesk_webhook.enqueue_payload(payload)
    except (ValueError, sqlite3.Error) as exc:
        raise HTTPException(503, 'Não foi possível colocar o chamado na fila.') from exc
    return {'ticket_id': ticket_id, 'action_id': action_id, 'status': 'queued',
            'mode': settings.mode, 'ticket_refreshed': True}


@router.post('/api/admin/evaluations/seed')
def seed_evaluation_cases(request: Request):
    _local_only(request)
    return agent_evaluation.seed()


class EvaluationLabel(BaseModel):
    expected_modules: list[str]
    expected_answer: str


@router.put('/api/admin/evaluations/{ticket_id}')
def label_evaluation_case(ticket_id: int, body: EvaluationLabel, request: Request):
    _local_only(request)
    try:
        return agent_evaluation.label(ticket_id, body.expected_modules, body.expected_answer)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(404, 'Caso não encontrado na amostra.') from exc


class _ArticleMedia(HTMLParser):
    def __init__(self):
        super().__init__()
        self.items = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        src = attrs.get('src') or ''
        if tag == 'img':
            match = re.search(r'movidesk-files/([a-fA-F0-9]{32})', src)
            if match:
                self.items.append({'kind': 'image', 'url': f'/api/image/{match.group(1)}',
                                   'label': attrs.get('alt') or 'Imagem do artigo'})
        elif tag == 'iframe':
            parsed = urlparse(src)
            if parsed.hostname in {'www.youtube.com', 'youtube.com', 'www.youtube-nocookie.com'}:
                match = re.fullmatch(r'/embed/([a-zA-Z0-9_-]{6,20})', parsed.path)
                if match:
                    self.items.append({'kind': 'video', 'url': f'https://www.youtube.com/watch?v={match.group(1)}',
                                       'label': attrs.get('title') or 'Vídeo do artigo'})


def _source_media(node, raw_html=''):
    parser = _ArticleMedia()
    parser.feed(raw_html or '')
    items = parser.items
    meta = node.get('metadata') or {}
    for attachment in meta.get('attachments', []):
        if not attachment.get('is_image'):
            continue
        relative = (attachment.get('relative_path') or '').replace('\\', '/')
        parts = relative.split('/')
        if len(parts) == 3 and parts[0] == 'attachments' and all(p not in ('', '.', '..') for p in parts[1:]):
            items.append({'kind': 'image', 'url': '/attachments/' + '/'.join(quote(p) for p in parts[1:]),
                          'label': attachment.get('file_name') or 'Imagem do chamado'})
    media_meta = node.get('media_metadata') or {}
    manual_paths = media_meta.get('images', []) if isinstance(media_meta, dict) else []
    if not manual_paths and node.get('media_path'):
        manual_paths = [node['media_path']]
    for path in manual_paths:
        if isinstance(path, str) and path and not Path(path).is_absolute() and '..' not in Path(path).parts:
            items.append({'kind': 'image', 'url': '/manual_media/' + '/'.join(quote(p) for p in Path(path).parts),
                          'label': Path(path).name})
    frame_dir = Path(__file__).parent / 'output' / 'curation_media' / str(node['id'])
    if frame_dir.is_dir():
        selected = re.findall(rf'/curation_media/{node["id"]}/(frame-\d+\.jpg)', node.get('solution_override') or '')
        frames = [frame_dir / name for name in dict.fromkeys(selected)] if selected else sorted(frame_dir.glob('frame-*.jpg'))
        for frame in frames[:16]:
            if not frame.is_file(): continue
            items.append({'kind': 'image', 'url': f'/curation_media/{node["id"]}/{frame.name}',
                          'label': f'Quadro do vídeo · {int(frame.stem.split("-")[-1])//60:02d}:{int(frame.stem.split("-")[-1])%60:02d}'})
    return items[:40]


def _local_image(url):
    decoded = unquote(url or '')
    roots = {
        '/curation_media/': Path(__file__).parent / 'output' / 'curation_media',
        '/attachments/': Path(__file__).parent / 'output' / 'attachments',
        '/manual_media/': Path(__file__).parent.parent / 'Instrucoes_master',
        '/api/image/': Path(__file__).parent / 'output' / 'attachments' / 'cache',
    }
    for prefix, root in roots.items():
        if not decoded.startswith(prefix): continue
        relative = decoded.removeprefix(prefix)
        if prefix == '/api/image/': relative += '.png'
        path = (root / relative).resolve()
        if root.resolve() not in path.parents or not path.is_file() or path.stat().st_size > 1_000_000:
            return None
        mime = {'jpg':'image/jpeg','jpeg':'image/jpeg','png':'image/png',
                'gif':'image/gif','webp':'image/webp'}.get(path.suffix.lower().lstrip('.'))
        return (path, mime) if mime else None
    return None


def _topic(subject: str, problem: str):
    text = (subject + " " + problem).casefold()
    # Put more specific families first to make counts mutually exclusive.
    for topic, terms in TOPICS.items():
        if any(term in text for term in terms):
            return topic
    return "Outros"


@router.get("/admin", response_class=HTMLResponse)
def admin_page(request: Request):
    _local_only(request)
    return HTMLResponse((STATIC / "admin-console.html").read_text(encoding="utf-8"))


class TicketSyncScheduleUpdate(BaseModel):
    enabled: bool
    time: str = Field(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")


@router.get("/api/admin/ticket-sync")
def ticket_sync_status(request: Request):
    _local_only(request)
    try:
        return ticket_sync_control.status()
    except psycopg.Error as exc:
        raise HTTPException(503, "Não foi possível consultar o estado da sincronização.") from exc


@router.get("/api/admin/flow")
def flow_report(request: Request, days: int = Query(30, ge=7, le=90),
                ticket_type: Literal['public', 'internal', 'all'] = 'public'):
    _local_only(request)
    try:
        return ticket_flow.report(days, ticket_type)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except psycopg.Error as exc:
        raise HTTPException(503, "Não foi possível consultar a vazão de chamados.") from exc


@router.get("/api/admin/flow/tickets")
def flow_tickets(request: Request, day: date, event: Literal['entered', 'resolved', 'closed'],
                 ticket_type: Literal['public', 'internal', 'all'] = 'public'):
    _local_only(request)
    try:
        return ticket_flow.tickets_for_day(day, event, ticket_type)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except psycopg.Error as exc:
        raise HTTPException(503, "Não foi possível listar os chamados do dia.") from exc


@router.post("/api/admin/ticket-sync", status_code=202)
def start_ticket_sync(request: Request):
    _local_only(request)
    try:
        result = ticket_sync_control.start_sync("manual")
        operations.audit("Atualização manual de chamados", {"pid": result["pid"]})
        return result
    except ticket_sync_control.SyncBusy as exc:
        raise HTTPException(409, str(exc)) from exc
    except psycopg.Error as exc:
        raise HTTPException(503, "Banco indisponível para iniciar a atualização.") from exc


@router.put("/api/admin/ticket-sync/schedule")
def update_ticket_sync_schedule(request: Request, payload: TicketSyncScheduleUpdate):
    _local_only(request)
    previous = ticket_sync_control.schedule()
    try:
        updated = ticket_sync_control.save_schedule(payload.enabled, payload.time)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    operations.audit("Agenda de atualização de chamados", {"before": previous, "after": updated})
    return updated


@router.get("/api/admin/overview")
def overview(request: Request, queue: str = Query("", pattern="^(|support|development|customer|other)$")):
    _local_only(request)
    try:
        with _conn() as conn:
            tickets = conn.execute(f"SELECT {OPEN_FACT_COLUMNS} FROM current_open_ticket_facts f "
                                   "ORDER BY created_date DESC NULLS LAST").fetchall()
            kpis = conn.execute("""
                SELECT COUNT(*) AS open_tickets,
                       COUNT(*) FILTER (WHERE dashboard_data->>'base_status'='Stopped') AS waiting_tickets,
                       COUNT(*) FILTER (WHERE dashboard_data->>'urgency' ILIKE '%urgent%'
                           OR dashboard_data->>'urgency' ILIKE '%alta%') AS urgent_tickets,
                       COUNT(*) FILTER (WHERE NULLIF(dashboard_data->>'created_date','')::timestamptz
                           >= NOW() - INTERVAL '30 days') AS created_30d
                FROM current_open_ticket_facts
            """).fetchone()
            sources = conn.execute("SELECT source_type, COUNT(*) AS total FROM ticket_knowledge_nodes GROUP BY source_type ORDER BY total DESC").fetchall()
            quality = conn.execute("SELECT quality_tier, COUNT(*) AS total FROM current_open_tickets GROUP BY quality_tier ORDER BY total DESC").fetchall()
            refreshed = conn.execute("SELECT MAX(completed_at) AS latest FROM movidesk_sync_runs WHERE status='completed'").fetchone()["latest"]
            sync_run = conn.execute("SELECT status FROM movidesk_sync_runs ORDER BY id DESC LIMIT 1").fetchone()
        analyzed = ticket_dashboard.prepare(tickets, refreshed, _topic)
        for row in analyzed:
            row['organization'] = row['company_name'] if row['company_id'] not in (
                ticket_dashboard.UNKNOWN_COMPANY, ticket_dashboard.AMBIGUOUS_COMPANY) else None
        decision = insights.summarize(analyzed, queue)
        topics = {}
        status_counts = {}
        for row in tickets:
            topic = _topic(row["subject"] or "", row["problem_statement"] or "")
            topics[topic] = topics.get(topic, 0) + 1
            label = row["status"] or "Sem status"
            status_counts[label] = status_counts.get(label, 0) + 1
        return {
            "sync_in_progress": bool(sync_run and sync_run["status"] == "running"),
            "decision": decision,
            "kpis": {k: int(v or 0) for k, v in kpis.items()},
            "topics": sorted(({"name": k, "count": v} for k, v in topics.items()), key=lambda x: (-x["count"], x["name"])),
            "statuses": sorted(({"name": k, "count": v} for k, v in status_counts.items()), key=lambda x: (-x["count"], x["name"])),
            "sources": [{"name": r["source_type"], "count": int(r["total"])} for r in sources],
            "quality": [{"name": r["quality_tier"] or "Sem classificação", "count": int(r["total"])} for r in quality],
            "refreshed_at": refreshed.isoformat() if refreshed else None,
            "snapshot_notice": "Indicadores da última sincronização local; não são consulta ao vivo do Movidesk.",
            "settings": get_state(),
        }
    except psycopg.Error as exc:
        raise HTTPException(503, "Banco de dados indisponível para leitura.") from exc


@router.get("/api/admin/metrics/departments.csv")
def export_departments(request: Request):
    data = overview(request, queue="")
    if data['sync_in_progress']:
        raise HTTPException(409, "Aguarde a conclusão da sincronização para exportar os totais.")
    output = io.StringIO()
    writer = csv.writer(output, delimiter=';')
    writer.writerow(['Data da sincronização (UTC)', 'Grupo de responsabilidade', 'Chamados abertos', 'Percentual do total'])
    total = data['kpis']['open_tickets']
    for row in data['decision']['departments']:
        name = row['name']
        if name.startswith(('=', '+', '-', '@')):
            name = "'" + name
        writer.writerow([data['refreshed_at'], name, row['count'],
                         f"{row['count'] / total * 100:.2f}".replace('.', ',') if total else '0'])
    return Response(content=('\ufeff' + output.getvalue()).encode('utf-8'), media_type='text/csv; charset=utf-8',
                    headers={'Content-Disposition': 'attachment; filename="cortex-chamados-por-setor.csv"'})


@router.get('/api/admin/trends')
def ticket_intake_trends(request: Request, start: date, end: date,
                         month: str = Query('', pattern=r'^(|\d{4}-(0[1-9]|1[0-2]))$')):
    _local_only(request)
    try:
        first, after_last = trends.bounds(start, end)
        previous_start, previous_end = trends.previous_dates(start, end)
        before, _ = trends.bounds(previous_start, previous_end)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    try:
        with _conn() as conn:
            rows = conn.execute('''
                SELECT external_id, subject, created_date
                FROM refined_tickets
                WHERE created_date >= %s AND created_date < %s
                ORDER BY created_date DESC
            ''', (first, after_last)).fetchall()
            previous_total = conn.execute('''
                SELECT COUNT(*) AS total FROM refined_tickets
                WHERE created_date >= %s AND created_date < %s
            ''', (before, first)).fetchone()['total']
        return trends.summarize(rows, start, end, month, _topic, int(previous_total))
    except psycopg.Error as exc:
        raise HTTPException(503, 'Não foi possível consultar o histórico de chamados.') from exc


@router.get("/api/admin/tickets")
def tickets(request: Request, q: str = Query("", max_length=200), topic: str = Query("", max_length=40),
            status: str = Query("", max_length=80),
            queue: str = Query("", pattern="^(|support|development|customer|other)$"),
            age: str = Query("", pattern=r"^(|0-7|8-30|31-90|91\+|30\+|unknown)$"),
            high: bool = False, order: str = Query(
                "recent", pattern="^(recent|oldest|priority|age_asc|age_desc)$"),
            company: str = Query("", max_length=100), owner: str = Query("", max_length=100),
            team: str = Query("", max_length=200), organization: str = Query("", max_length=500),
            department: str = Query("", max_length=200),
            page: int = Query(1, ge=1, le=1000), limit: int = Query(20, ge=5, le=100)):
    _local_only(request)
    try:
        with _conn() as conn:
            rows = conn.execute(f"SELECT {OPEN_FACT_COLUMNS} FROM current_open_ticket_facts f").fetchall()
            latest = conn.execute("SELECT status FROM movidesk_sync_runs ORDER BY id DESC LIMIT 1").fetchone()
        reference = rows[0]["current_as_of"] if rows else None
        prepared = ticket_dashboard.prepare(rows, reference, _topic)
        data = ticket_dashboard.dashboard(prepared, dict(q=q, topic=topic, status=status,
            queue=queue, age=age, high=high, order=order, company=company, owner=owner,
            team=team, organization=organization, department=department), page, limit)
        data["refreshed_at"] = reference.isoformat() if reference else None
        data["sync_status"] = latest["status"] if latest else None
        return data
    except psycopg.Error as exc:
        raise HTTPException(503, "Não foi possível consultar a fila de chamados.") from exc


def _catalog():
    with _lock:
        if time.monotonic() - _catalog_cache['at'] < 60:
            return _catalog_cache['rows']
        curation.ensure_schema()
        with _conn() as conn:
            rows = conn.execute('''SELECT kn.id,kn.title,kn.modules,kn.manual_category,kn.source_type,
                c.module_key,c.subtopic_key,c.status,
                length(COALESCE(c.solution_override,kn.solution,'')) >= 30 AS has_procedure,
                length(COALESCE(c.version_note,kn.version_reference,'')) > 0 AS has_version
                FROM ticket_knowledge_nodes kn
                LEFT JOIN knowledge_curation c ON c.node_id=kn.id''').fetchall()
        catalog=[]
        for r in rows:
            module=r['module_key'] or curation.classify(r['title'],r['modules'],r['manual_category'])
            catalog.append({**r,'module':module,
                'subtopic':r['subtopic_key'] or curation.subtopic(module,r['title'],r['modules']),
                'state':r['status'] or 'draft'})
        _catalog_cache.update(at=time.monotonic(), rows=catalog)
        return catalog


@router.get('/api/admin/knowledge/modules')
def knowledge_modules(request: Request):
    _local_only(request)
    try:
        rows = _catalog()
        result = []
        for key, label, description in curation.MODULES:
            subset = [r for r in rows if r['module'] == key]
            subtopics=[{'key':subkey,'label':sublabel,
                'total':sum(r['subtopic']==subkey for r in subset)}
                for subkey,sublabel in curation.subtopic_options(key)]
            result.append({'key': key, 'label': label, 'description': description,
                'total': len(subset), 'draft': sum(r['state']=='draft' for r in subset),
                'approved': sum(r['state']=='approved' for r in subset),
                'excluded': sum(r['state']=='excluded' for r in subset),
                'without_procedure': sum(not r['has_procedure'] for r in subset),
                'without_version': sum(not r['has_version'] for r in subset),
                'subtopics':subtopics})
        return result
    except psycopg.Error as exc:
        raise HTTPException(503, 'Não foi possível carregar os módulos.') from exc


@router.get("/api/admin/knowledge")
def knowledge(request: Request, q: str = Query("", max_length=200), source: str = Query("", max_length=30),
              module: str = Query('', max_length=40), subtopic: str = Query('',max_length=40),
              state: Literal['active','all','draft','approved','excluded','needs_procedure','needs_version','videos','videos_pending'] = 'active',
              page: int = Query(1, ge=1, le=1000), limit: int = Query(20, ge=5, le=100)):
    _local_only(request)
    if module and module not in curation.MODULE_NAMES:
        raise HTTPException(422, 'Módulo inválido.')
    if subtopic and (not module or subtopic not in dict(curation.subtopic_options(module))):
        raise HTTPException(422,'Subtema inválido.')
    try:
        catalog = _catalog()
        video_node_ids = set()
        if state in ('videos', 'videos_pending'):
            with _conn() as conn:
                video_node_ids = {r['id'] for r in conn.execute('''SELECT kn.id FROM ticket_knowledge_nodes kn
                    JOIN raw_kb_articles raw ON raw.external_id=(kn.metadata->>'external_id')::bigint
                    WHERE kn.source_type='OFFICIAL_KB' AND raw.content_html LIKE '%%youtube.com/embed/%%' ''').fetchall()}
        ids = [r['id'] for r in catalog if (not module or r['module']==module)
               and (not subtopic or r['subtopic']==subtopic)
               and (state not in ('videos','videos_pending') or r['id'] in video_node_ids)
               and (state=='all' or (r['state']!='excluded' if state=='active' else r['state']==state)
                    or (state=='videos' and r['state']!='excluded')
                    or (state=='videos_pending' and r['state']!='excluded' and not r['has_procedure'])
                    or (state=='needs_procedure' and r['state']!='excluded' and not r['has_procedure'])
                    or (state=='needs_version' and r['state']!='excluded' and not r['has_version']))]
        if not ids: return {'items': [], 'total': 0, 'page': page, 'limit': limit}
        with _conn() as conn:
            rows = conn.execute("""
                SELECT kn.id, kn.source_type, kn.source_external_id, COALESCE(c.title_override,kn.title) AS title,
                       COALESCE(c.problem_override,kn.problem) AS problem,
                       COALESCE(c.solution_override,kn.solution) AS solution,c.prerequisites,c.expected_result,
                       kn.modules,kn.final_score,kn.validation_evidence,kn.created_at_source,kn.metadata,
                       kn.version_reference,c.version_note,c.keywords,c.status AS curation_status,c.revision,
                       c.module_key,c.updated_at AS curated_at
                FROM ticket_knowledge_nodes kn LEFT JOIN knowledge_curation c ON c.node_id=kn.id
                WHERE kn.id=ANY(%s) AND (%s='' OR kn.source_type=%s)
                  AND (%s='' OR COALESCE(c.title_override,kn.title) ILIKE %s OR COALESCE(c.problem_override,kn.problem,'') ILIKE %s
                  OR COALESCE(c.solution_override,kn.solution,'') ILIKE %s OR COALESCE(kn.content_for_embedding,'') ILIKE %s
                  OR kn.source_external_id::text=%s OR array_to_string(c.keywords,' ') ILIKE %s)
                ORDER BY CASE kn.source_type WHEN 'OFFICIAL_KB' THEN 0 WHEN 'MANUAL' THEN 1
                         WHEN 'VALIDATED_TICKET' THEN 2 ELSE 3 END,
                         kn.created_at_source DESC NULLS LAST,kn.id DESC
                LIMIT %s OFFSET %s
            """, (ids,source,source,q,f"%{q}%",f"%{q}%",f"%{q}%",f"%{q}%",q,f"%{q}%",limit,(page-1)*limit)).fetchall()
            total = conn.execute("""
                SELECT COUNT(*) AS n FROM ticket_knowledge_nodes kn LEFT JOIN knowledge_curation c ON c.node_id=kn.id
                WHERE kn.id=ANY(%s) AND (%s='' OR kn.source_type=%s)
                  AND (%s='' OR COALESCE(c.title_override,kn.title) ILIKE %s OR COALESCE(c.problem_override,kn.problem,'') ILIKE %s
                  OR COALESCE(c.solution_override,kn.solution,'') ILIKE %s OR COALESCE(kn.content_for_embedding,'') ILIKE %s
                  OR kn.source_external_id::text=%s OR array_to_string(c.keywords,' ') ILIKE %s)
            """, (ids,source,source,q,f"%{q}%",f"%{q}%",f"%{q}%",f"%{q}%",q,f"%{q}%")).fetchone()["n"]
        by_id = {r['id']:r for r in catalog}
        return {"items": [{"id": r["id"], "source_type": r["source_type"], "source_external_id": r["source_external_id"],
                           "title": r["title"], "problem": (r["problem"] or "")[:800], "solution": (r["solution"] or "")[:1200],
                           "prerequisites":r['prerequisites'],"expected_result":r['expected_result'],"keywords":r['keywords'] or [],
                           "modules": r["modules"] or [], "score": r["final_score"], "validated": bool(r["validation_evidence"]),
                           "module_key": by_id[r['id']]['module'],"module_name": curation.MODULE_NAMES[by_id[r['id']]['module']],
                           "subtopic_key":by_id[r['id']]['subtopic'],
                           "subtopic_name":dict(curation.subtopic_options(by_id[r['id']]['module'])).get(by_id[r['id']]['subtopic'],'Geral / outros'),
                           "curation_status": by_id[r['id']]['state'],"revision": r['revision'] or 0,
                           "version_reference": r['version_note'] or r['version_reference'],
                           "created_at": r["created_at_source"].isoformat() if r["created_at_source"] else None,
                           "source_url": (f"https://helpdesk.alternativasistemas.com.br/kb/article/{(r['metadata'] or {}).get('external_id')}"
                                          if r["source_type"] == "OFFICIAL_KB" and (r["metadata"] or {}).get("external_id")
                                          else f"https://helpdesk.alternativasistemas.com.br/Ticket/Edit/{r['source_external_id']}"
                                          if r["source_external_id"] else None)} for r in rows],
                "total": total, "page": page, "limit": limit}
    except psycopg.Error as exc:
        raise HTTPException(503, "Não foi possível consultar as fontes de conhecimento.") from exc


@router.get('/api/admin/knowledge/{node_id}')
def knowledge_detail(request: Request, node_id: int):
    _local_only(request)
    curation.ensure_schema()
    with _conn() as conn:
        node = conn.execute('''SELECT kn.id,kn.source_type,kn.source_external_id,kn.title,kn.problem,kn.solution,
            kn.content_for_embedding,kn.modules,kn.manual_category,kn.version_reference,kn.created_at_source,
            kn.metadata,kn.media_path,kn.media_metadata,c.module_key,c.subtopic_key,c.status,c.title_override,c.problem_override,c.solution_override,
            c.prerequisites,c.expected_result,
            c.version_note,c.keywords,c.image_annotations,c.change_reason,c.revision,c.updated_at FROM ticket_knowledge_nodes kn
            LEFT JOIN knowledge_curation c ON c.node_id=kn.id WHERE kn.id=%s''',(node_id,)).fetchone()
        if not node: raise HTTPException(404,'Fonte não encontrada.')
        article = None
        if node['source_type'] == 'OFFICIAL_KB' and (node['metadata'] or {}).get('external_id'):
            article = conn.execute('''SELECT content_text,content_html FROM raw_kb_articles
                WHERE external_id=%s LIMIT 1''', ((node['metadata'] or {})['external_id'],)).fetchone()
        history = conn.execute('''SELECT revision,change_reason,changed_at,old_value,new_value
            FROM knowledge_curation_history WHERE node_id=%s ORDER BY revision DESC LIMIT 20''',(node_id,)).fetchall()
    result = dict(node)
    result['module_key'] = result['module_key'] or curation.classify(result['title'],result['modules'],result['manual_category'])
    result['subtopic_key'] = result['subtopic_key'] or curation.subtopic(result['module_key'],result['title'],result['modules'])
    result['status'] = result['status'] or 'draft'
    result['revision'] = result['revision'] or 0
    result['content_for_embedding'] = (result['content_for_embedding'] or '')[:12000]
    result['keywords'] = result['keywords'] or []
    result['image_annotations'] = result['image_annotations'] or []
    result['original_content_text'] = ((article or {}).get('content_text') or '')[:25000]
    result['media'] = _source_media(result, (article or {}).get('content_html') or '')
    error_file = Path(__file__).parent / 'output' / 'curation_media' / str(node_id) / 'extraction_error.json'
    result['media_error'] = (json.loads(error_file.read_text(encoding='utf-8')).get('message')
                             if error_file.is_file() and not any(
                                 item.get('kind') == 'image' and item.get('url', '').startswith(f'/curation_media/{node_id}/')
                                 for item in result['media']) else None)
    result['history'] = [dict(r) for r in history]
    return result


@router.post('/api/admin/knowledge/{node_id}/suggest')
def suggest_knowledge(request: Request, node_id: int):
    """Generate a review-only proposal from source evidence, never approve it."""
    _local_only(request)
    if not GEMINI_API_KEY:
        raise HTTPException(503, 'Chave da IA não configurada.')
    source = knowledge_detail(request, node_id)
    parts = []
    usable_images = []
    for item in source['media']:
        url = item.get('url', '')
        if item.get('kind') != 'image':
            continue
        local = _local_image(url)
        if not local:
            continue
        path, mime = local
        usable_images.append(item)
        parts.append({'text': f'Imagem {len(usable_images)}: {item.get("label") or path.name}'})
        parts.append({'inline_data': {'mime_type': mime,
            'data': base64.b64encode(path.read_bytes()).decode('ascii')}})
        if len(usable_images) >= 8:
            break
    source_text = '\n'.join(str(source.get(key) or '') for key in
        ('title', 'problem', 'solution', 'original_content_text', 'content_for_embedding'))[:19000]
    if len(source_text.strip()) < 180 and not usable_images:
        raise HTTPException(422, 'Esta fonte não tem texto ou quadros suficientes para gerar passos. Anexe uma gravação ou documente a operação.')
    if not usable_images:
        video = next((item['url'] for item in source['media'] if item.get('kind') == 'video'), None)
        if video:
            parts.append({'file_data': {'file_uri': video}})
    instruction = '''Você prepara um RASCUNHO de procedimento do ERP Master para revisão humana.
Use somente o texto e as imagens fornecidos. Não infira botões, campos, caminhos ou resultados ausentes.
Se a fonte não sustenta uma sequência completa, descreva apenas passos visíveis e liste lacunas.
Não use dados específicos do cliente do exemplo como valores padrão. Informe se a versão parece antiga.
Devolva JSON puro com as chaves: problem, prerequisites, steps (array de objetos text e image_indexes), expected_result, version_note, gaps.
image_indexes são números das imagens fornecidas, de 1 em diante, apenas quando realmente sustentam o passo.
Cada passo deve ser uma ação verificável, em português brasileiro.
FONTE:\n'''+source_text
    parts.insert(0, {'text': instruction})
    payload = {'contents': [{'parts': parts}], 'generationConfig':
        {'temperature': 0.1, 'maxOutputTokens': 3000, 'responseMimeType': 'application/json'}}
    req = urllib.request.Request(
        'https://generativelanguage.googleapis.com/v1beta/models/gemini-3.6-flash:generateContent',
        data=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
        headers={'Content-Type': 'application/json', 'x-goog-api-key': GEMINI_API_KEY}, method='POST')
    try:
        with urllib.request.urlopen(req, timeout=75) as response:
            raw = json.loads(response.read().decode('utf-8'))
        proposal = json.loads(raw['candidates'][0]['content']['parts'][0]['text'])
    except urllib.error.HTTPError as exc:
        if exc.code in (402, 429):
            raise HTTPException(503, 'A geração por IA está sem créditos ou no limite de uso. O rascunho salvo continua disponível.') from exc
        raise HTTPException(502, 'A IA não conseguiu analisar esta fonte. Confira o vídeo e as imagens originais.') from exc
    except (OSError, ValueError, KeyError, IndexError) as exc:
        raise HTTPException(502, 'A IA não retornou um procedimento estruturado. Tente novamente ou revise a fonte.') from exc
    steps = []
    for number, step in enumerate(proposal.get('steps', [])[:12], 1):
        if not isinstance(step, dict) or not str(step.get('text') or '').strip():
            continue
        steps.append(f'{number}. {str(step["text"]).strip()[:1000]}')
        for index in step.get('image_indexes', [])[:2]:
            if isinstance(index, int) and 1 <= index <= len(usable_images):
                image = usable_images[index - 1]
                steps.append(f'![{image["label"]}]({image["url"]})')
        steps.append('')
    return {'problem': str(proposal.get('problem') or '')[:12000],
            'prerequisites': str(proposal.get('prerequisites') or '')[:8000],
            'solution': '\n'.join(steps).strip(),
            'expected_result': str(proposal.get('expected_result') or '')[:8000],
            'version_note': str(proposal.get('version_note') or '')[:500],
            'gaps': proposal.get('gaps') or [], 'status': 'draft',
            'media_used': len(usable_images)}


@router.post('/api/admin/knowledge/{node_id}/extract-frames')
def extract_knowledge_frames(request: Request, node_id: int):
    """Build visual evidence for one official video; no textual claims are generated."""
    _local_only(request)
    source = knowledge_detail(request, node_id)
    if source['source_type'] != 'OFFICIAL_KB':
        raise HTTPException(422, 'A extração está disponível para vídeos de artigos oficiais.')
    video = next((item['url'] for item in source['media'] if item.get('kind') == 'video'), None)
    match = re.fullmatch(r'https://www\.youtube\.com/watch\?v=([A-Za-z0-9_-]{11})', video or '')
    if not match:
        raise HTTPException(422, 'Este artigo não contém um vídeo do YouTube válido.')
    try:
        from video_frames import extract_frames
        manifest = extract_frames(node_id, match.group(1))
    except Exception as exc:
        raise HTTPException(502, f'Não foi possível extrair os quadros: {str(exc)[:180]}') from exc
    updated = knowledge_detail(request, node_id)
    return {'frame_count': len(manifest['frame_seconds']), 'media': updated['media']}


class KnowledgeUpdate(BaseModel):
    module_key: str
    subtopic_key: str
    status: Literal['draft','approved','excluded']
    title_override: str | None = Field(default=None,max_length=500)
    problem_override: str | None = Field(default=None,max_length=12000)
    solution_override: str | None = Field(default=None,max_length=20000)
    prerequisites: str | None = Field(default=None,max_length=8000)
    expected_result: str | None = Field(default=None,max_length=8000)
    keywords: list[str] = Field(default_factory=list,max_length=30)
    version_note: str | None = Field(default=None,max_length=500)
    image_annotations: list[dict] = Field(default_factory=list,max_length=40)
    change_reason: str = Field(min_length=8,max_length=2000)
    expected_revision: int = Field(ge=0)


@router.put('/api/admin/knowledge/{node_id}')
def update_knowledge(request: Request,node_id: int,body: KnowledgeUpdate):
    _local_only(request)
    curation.ensure_schema()
    if body.status=='approved' and len((body.title_override or '').strip()) < 8:
        raise HTTPException(422,'Para aprovar, informe um título claro para a fonte.')
    if body.status=='approved' and len((body.problem_override or '').strip()) < 20:
        raise HTTPException(422,'Para aprovar, descreva quando o procedimento se aplica.')
    if body.status=='approved' and len((body.solution_override or '').strip()) < 30:
        raise HTTPException(422,'Para aprovar, registre um procedimento revisado com pelo menos 30 caracteres.')
    if body.status=='approved' and len((body.expected_result or '').strip()) < 15:
        raise HTTPException(422,'Para aprovar, descreva o resultado esperado com pelo menos 15 caracteres.')
    payload=body.model_dump(exclude={'expected_revision'})
    try:
        with curation.connect() as conn:
            result=curation.curate(conn,node_id,payload,body.expected_revision)
        with _lock: _catalog_cache['at']=0
        return {'saved':True,'revision':result['revision'],'status':result['status']}
    except LookupError as exc: raise HTTPException(404,str(exc)) from exc
    except (ValueError,RuntimeError) as exc: raise HTTPException(409,str(exc)) from exc
    except psycopg.Error as exc: raise HTTPException(503,'Não foi possível salvar a revisão.') from exc


class SettingsUpdate(BaseModel):
    automation_rules: dict | None = None
    draft_generation_enabled: bool | None = None
    source_types: list[str] | None = Field(default=None, max_length=4)
    min_interval_seconds: int | None = Field(default=None, ge=0, le=300)
    max_per_minute: int | None = Field(default=None, ge=1, le=60)
    max_concurrent: int | None = Field(default=None, ge=1, le=5)
    pilot_person_ids: list[str] | None = Field(default=None, max_length=100)
    pilot_organization_ids: list[str] | None = Field(default=None, max_length=10000)
    pilot_requester_emails: list[str] | None = Field(default=None, max_length=100)
    pilot_paused: bool | None = None
    sandbox_mode: bool | None = None
    whitelist: list[str] | None = Field(default=None, max_length=100)
    auto_internal_note: bool | None = None
    specialists: list[dict] | None = None


@router.post('/api/admin/specialists/reset')
def reset_specialists(request: Request):
    _local_only(request)
    return set_state({'specialists': DEFAULT_SPECIALISTS})


class PilotSelection(BaseModel):
    selected: bool


class InternalLink(BaseModel):
    internal_ticket_id: int = Field(ge=1)


class PilotApproval(BaseModel):
    approved_text: str = Field(min_length=30, max_length=8000)
    note: str = Field(min_length=8, max_length=4000)


class ReviewUpdate(BaseModel):
    verdict: Literal['approved', 'corrected', 'rejected']
    note: str = Field(default='', max_length=4000)


@router.get('/api/admin/performance')
def performance(request: Request, days: int = Query(30, ge=1, le=365)):
    _local_only(request)
    return operations.performance(days)


@router.get('/api/admin/runs')
def response_runs(request: Request, days: int = Query(30, ge=1, le=365), verdict: Literal['', 'pending', 'approved', 'corrected', 'rejected'] = '', page: int = Query(1, ge=1), limit: int = Query(15, ge=1, le=100)):
    _local_only(request)
    return operations.runs(days, verdict, page, limit)


@router.get('/api/admin/agent-center')
def agent_center(request: Request):
    _local_only(request)
    return agent_control.catalog()


@router.get('/api/admin/pilot/companies')
def pilot_companies(request: Request, search: str = Query('', max_length=120),
                    page: int = Query(1, ge=1), limit: int = Query(15, ge=1, le=50)):
    _local_only(request)
    try:
        return agent_control.company_catalog(search, page, limit, get_state()['pilot_organization_ids'])
    except psycopg.Error as exc:
        raise HTTPException(503, 'Cadastro de empresas indisponível.') from exc


def _all_company_scope():
    ids = agent_control.all_selectable_company_ids()
    token = hashlib.sha256(json.dumps(ids, separators=(',', ':')).encode()).hexdigest()
    return ids, token


class AllCompaniesAuthorization(BaseModel):
    token: str = Field(pattern=r'^[a-f0-9]{64}$')


@router.get('/api/admin/pilot/companies/authorize-all')
def all_companies_preview(request: Request):
    _local_only(request)
    try:
        ids, token = _all_company_scope()
    except psycopg.Error:
        raise HTTPException(503, 'Cadastro de empresas indisponível.') from None
    selected = set(get_state()['pilot_organization_ids'])
    return {'total': len(ids), 'new': len(set(ids) - selected), 'token': token}


@router.post('/api/admin/pilot/companies/authorize-all')
def authorize_all_companies(request: Request, body: AllCompaniesAuthorization):
    _local_only(request)
    try:
        ids, token = _all_company_scope()
    except psycopg.Error:
        raise HTTPException(503, 'Cadastro de empresas indisponível.') from None
    if body.token != token:
        raise HTTPException(409, 'O cadastro mudou. Confira novamente antes de autorizar todas as empresas.')
    selected = set(get_state()['pilot_organization_ids'])
    user = getattr(request.state, 'cortex_user_obj', None) or {}
    settings = set_state({'pilot_organization_ids': sorted(selected | set(ids))},
                         actor=user.get('id') or getattr(request.state, 'cortex_user', None))
    return {'added': len(set(ids) - selected), 'selected_count': len(settings['pilot_organization_ids']),
            'pilot_paused': settings['pilot_paused'], 'auto_internal_note': settings['auto_internal_note']}


@router.put('/api/admin/pilot/companies/{organization_id}')
def select_pilot_company(request: Request, organization_id: str, body: PilotSelection):
    _local_only(request)
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', organization_id):
        raise HTTPException(422, 'Identificador da empresa inválido.')
    if body.selected:
        try:
            company = agent_control.selectable_company(organization_id)
        except psycopg.Error as exc:
            raise HTTPException(503, 'Cadastro de empresas indisponível.') from exc
    else:
        company = {'id': organization_id}
    ids = set(get_state()['pilot_organization_ids'])
    if body.selected:
        ids.add(organization_id)
    else:
        ids.discard(organization_id)
    settings = set_state({'pilot_organization_ids': sorted(ids)})
    return {'company': company, 'selected': body.selected,
            'selected_count': len(settings['pilot_organization_ids']), 'pilot_paused': settings['pilot_paused']}


@router.get('/api/admin/pilot/preview/{ticket_id}')
def pilot_preview(request: Request, ticket_id: int):
    _local_only(request)
    try:
        ticket = agent_control.ticket_snapshot(ticket_id)
    except psycopg.Error as exc:
        raise HTTPException(503, 'Fotografia do chamado indisponível.') from exc
    result = agent_control.eligibility(ticket, get_state())
    return {'ticket_id': ticket_id, 'subject': ticket['subject'] if ticket else None,
            'status': ticket['status'] if ticket else None, **result}


@router.post('/api/admin/runs/{run_id}/internal-link')
def link_run_internal(request: Request, run_id: str, body: InternalLink):
    _local_only(request)
    run = operations.run_detail(run_id)
    if run['status'] != 'completed' or not run['ticket_id']:
        raise HTTPException(409, 'A geração precisa estar concluída e vinculada ao chamado público.')
    verification = agent_control.verify_internal_relation(run['ticket_id'], body.internal_ticket_id)
    workflow = operations.link_internal(run_id, body.internal_ticket_id, time.time())
    return {'verification': verification, 'workflow': workflow}


@router.post('/api/admin/runs/{run_id}/pilot-approval')
def approve_run_for_pilot(request: Request, run_id: str, body: PilotApproval):
    _local_only(request)
    run = operations.run_detail(run_id)
    if not run['ticket_id']:
        raise HTTPException(409, 'A geração não está vinculada a um chamado.')
    try:
        ticket = agent_control.ticket_snapshot(run['ticket_id'])
    except psycopg.Error as exc:
        raise HTTPException(503, 'Fotografia do chamado indisponível.') from exc
    eligibility = agent_control.eligibility(ticket, get_state())
    if not eligibility['eligible']:
        raise HTTPException(409, 'Piloto indisponível: ' + ' '.join(eligibility['reasons']))
    workflow = run['workflow']
    if not workflow:
        raise HTTPException(409, 'Vincule primeiro o chamado interno relacionado.')
    if time.time() - workflow['relation_verified_at'] > 24 * 3600:
        raise HTTPException(409, 'Vínculo interno antigo; confirme novamente no Movidesk.')
    evidence = agent_control.verify_internal_approval(workflow['internal_ticket_id'], run_id, body.approved_text)
    reviewer = getattr(request.state, 'cortex_user', 'admin')
    saved = operations.approve_for_pilot(run_id, body.approved_text, body.note, str(reviewer), evidence['action_id'])
    return {'approved_at': saved['approved_at'], 'delivery_state': saved['delivery_state'],
            'internal_ticket_id': saved['internal_ticket_id'], 'approved_sha256': saved['approved_sha256'],
            'internal_action_id': evidence['action_id']}


@router.put('/api/admin/runs/{run_id}/review')
def review_response(request: Request, run_id: str, body: ReviewUpdate):
    _local_only(request)
    if body.verdict != 'approved' and not body.note.strip():
        raise HTTPException(422, 'Descreva a correção ou o motivo da rejeição.')
    operations.review(run_id, body.verdict, body.note.strip())
    return {'saved': True}


@router.get('/api/admin/audit')
def audit_history(request: Request):
    _local_only(request)
    return operations.audit_log()


@router.get("/api/admin/settings")
def read_settings(request: Request):
    _local_only(request)
    return get_state()


@router.put("/api/admin/settings")
def update_settings(request: Request, update: SettingsUpdate):
    _local_only(request)
    user = getattr(request.state, 'cortex_user_obj', None) or {}
    return set_state({k: v for k, v in update.model_dump().items() if v is not None}, actor=user.get('id') or getattr(request.state, 'cortex_user', None))


@router.get('/api/admin/automation/monitor')
def automation_monitor(request: Request, ticket_id: int | None = Query(None, ge=1)):
    _local_only(request)
    import automation_monitor as monitor
    return monitor.report(get_state(), ticket_id)


@router.post('/api/admin/automation/preview/{ticket_id}')
def automation_preview(request: Request, ticket_id: int):
    _local_only(request)
    if ticket_id <= 0:
        raise HTTPException(422, 'Número inválido.')
    # Explicitly dry-run: this path has no writer or outbox mutation.
    settings = movidesk_note_worker.effective_settings(movidesk_note_worker.Settings.load())
    from dataclasses import replace
    settings = replace(settings, mode='dry_run')
    api = movidesk_note_worker.Api(settings)
    try:
        ticket = api.ticket(ticket_id)
        ok, reason = movidesk_note_worker.eligible(ticket, settings)
        if not ok:
            raise HTTPException(409, reason)
        controls = get_state()
        run = operations.begin('Simulação sem publicação', '', [], controls, ticket_id=ticket_id)
        try:
            result = movidesk_note_worker.generate_note(ticket, settings, api)
            operations.finish(run, draft=result.get('reply'))
            return {'reply': result.get('reply'), 'published': False}
        except Exception:
            operations.finish(run, error='Falha na simulação')
            raise HTTPException(503, 'Não foi possível gerar a prévia.') from None
    finally:
        api.client.close()


@router.get("/api/admin/statuses")
def statuses(request: Request):
    _local_only(request)
    try:
        with _conn() as conn:
            rows = conn.execute("""SELECT status, COUNT(*) AS n FROM refined_tickets
                WHERE base_status NOT IN ('Closed','Canceled','Resolved') GROUP BY status ORDER BY n DESC""").fetchall()
        return [{"name": r["status"] or "Sem status", "count": int(r["n"])} for r in rows]
    except psycopg.Error as exc:
        raise HTTPException(503, "Status indisponível.") from exc


class TicketMessage(BaseModel):
    role: Literal['client', 'agent']
    content: str = Field(min_length=1, max_length=8000)
    meta: dict = Field(default_factory=dict)


class TicketSimulationRequest(BaseModel):
    ticket_id: int | None = None
    subject: str = Field(min_length=3, max_length=300)
    client_name: str = Field(default="Cliente", max_length=120)
    client_email: str = Field(default="", max_length=200)
    messages: list[TicketMessage] = Field(min_length=1, max_length=40)


class TicketImageRequest(BaseModel):
    data_url: str = Field(min_length=30, max_length=5_600_000)


@router.post('/api/admin/ticket-image')
def ticket_image(request: Request, body: TicketImageRequest):
    _local_only(request)
    from ticket_image import transcribe
    try:
        return transcribe(body.data_url)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc


@router.post("/api/admin/simulate-ticket")
def simulate_ticket(request: Request, body: TicketSimulationRequest):
    """Função síncrona de propósito: o FastAPI a executa no threadpool e o
    restante do painel continua respondendo enquanto a IA trabalha."""
    _local_only(request)
    import ticket_simulator
    state = get_state()
    result = ticket_simulator.simulate(body.subject, body.client_name,
                                       [m.model_dump() for m in body.messages], state)
    whitelist = {w.strip().lower() for w in state.get("whitelist", [])}
    result["sandbox"] = {"enabled": state.get("sandbox_mode", True),
                         "authorized": body.client_email.strip().lower() in whitelist,
                         "writes_to_movidesk": False}
    return result


@router.get("/api/admin/llm-status")
def llm_status(request: Request):
    _local_only(request)
    import llm_client
    return llm_client.status()


@router.get("/api/admin/playbooks")
def list_playbooks(request: Request):
    _local_only(request)
    import ticket_simulator
    return ticket_simulator.load_playbooks()


@router.put("/api/admin/playbooks")
def update_playbooks(request: Request, items: list[dict]):
    _local_only(request)
    import ticket_simulator
    try:
        saved = ticket_simulator.save_playbooks(items)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    operations.audit('Playbooks do agente', {'count': len(saved)})
    return saved
