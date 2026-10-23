"""Ambiente de Treinamento do CORTEX: ticket -> candidato -> curadoria -> conhecimento canônico.

Regras de segurança (não negociáveis):
  1. Nenhum ticket vira conhecimento oficial sem decisão humana registrada em auditoria.
  2. Similaridade textual não basta: módulo, versão do Master, UF, integração e regime
     tributário são comparados antes de tratar duas soluções como equivalentes.
  3. O histórico Movidesk continua separado. Só versões publicadas são espelhadas em
     ticket_knowledge_nodes (source_id 'canon:<item>'), onde o RAG atual já busca.
"""
from __future__ import annotations

import json
import base64
import io
import re
import unicodedata
import urllib.request
import uuid
from pathlib import Path
from threading import RLock

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from PIL import Image, UnidentifiedImageError

from config import POSTGRES_DSN, GEMINI_API_KEY, GEMINI_EMBEDDING_MODEL, GEMINI_EMBEDDING_DIMENSIONS, PROJECT_ROOT
import knowledge_curation as taxonomy
import knowledge_access

MIGRATION = Path(PROJECT_ROOT) / 'infra' / '006_canonical_knowledge.sql'
SUGGESTION_THRESHOLD = 55
_schema_ready = False
_schema_lock = RLock()


# ───────────────────────────── infraestrutura ─────────────────────────────

def connect():
    return psycopg.connect(POSTGRES_DSN, row_factory=dict_row, connect_timeout=5,
                           options='-c statement_timeout=20000 -c lock_timeout=5000')


def ensure_schema():
    """Aplica a migration 006 (idempotente) — volumes antigos não rodam initdb de novo."""
    global _schema_ready
    with _schema_lock:
        if _schema_ready:
            return
        with connect() as conn:
            conn.execute(MIGRATION.read_text(encoding='utf-8'))
        _schema_ready = True


def audit(conn, entity, entity_id, action, actor, reason=None, before=None, after=None):
    dump = lambda v: Jsonb(json.loads(json.dumps(v, default=str))) if v is not None else None
    conn.execute('''INSERT INTO cortex_kb.audit(entity,entity_id,action,actor,reason,before,after)
        VALUES (%s,%s,%s,%s,%s,%s,%s)''', (entity, entity_id, action, actor, reason, dump(before), dump(after)))


def norm(value):
    text = unicodedata.normalize('NFKD', str(value or '').casefold())
    return re.sub(r'\s+', ' ', ''.join(c for c in text if not unicodedata.combining(c))).strip()


# ───────────────────────────── higienização (LGPD) ─────────────────────────────

_SANITIZERS = (
    (re.compile(r'(?i)\b(senha|password|passwd|token|secret|api[_ -]?key)\b\s*[:=]\s*\S+'), r'\1: [credencial removida]'),
    (re.compile(r'(?i)(://)[^/\s:@]+:[^/\s@]+@'), r'\1[credencial]@'),
    (re.compile(r'[\w.+-]+@[\w-]+\.[\w.-]+'), '[email]'),
    (re.compile(r'\b\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}\b'), '[cnpj]'),
    (re.compile(r'\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b'), '[cpf]'),
    (re.compile(r'(?<!\d)(?:\+?55\s?)?\(?\d{2}\)?\s?9?\d{4}-?\d{4}(?!\d)'), '[telefone]'),
)


def sanitize(text):
    text = str(text or '')
    for pattern, replacement in _SANITIZERS:
        text = pattern.sub(replacement, text)
    return text


# ───────────────────────────── 1. o ticket é útil? ─────────────────────────────

_WEAK = re.compile(r'reinici|sem retorno|nao respondeu|nao retornou|inatividade|encerrad[oa] automatic|'
                   r'duplicad|resolveu sozinh|normalizou sozinh|voltou a funcionar sozinh|abrir outro (ticket|chamado)')
_CONFIRM = re.compile(r'funcionou|deu certo|resolvid|resolveu|ok,? obrigad|agora (foi|esta|ta) (ok|certo|funcionando)|perfeito')
_TECHNICAL_SUCCESS = re.compile(r'integrad[oa] com sucesso|inclu[ií]d[oa] com sucesso|emitid[oa] com sucesso|'
                                r'transmitid[oa] com sucesso|processad[oa] com sucesso|resolvid[oa] com sucesso')
_STEPS = re.compile(r'(^|\n)\s*(\d+[.)-]|passo|- )|acess(e|ar) o menu|configura(c|r)|cadastr|marque|desmarque|altere|parametr|>')
_ERROR = re.compile(r'rejei(c|ç)|erro\s*\d+|codigo\s*\d+|exception|falha (na|ao)|\b[a-z]*error\b')
_AGENT_PROFILES = {1, 3}


def is_agent(action):
    created_by = action.get('createdBy') or {}
    return str(created_by.get('profileType')) in {str(value) for value in _AGENT_PROFILES} or str(action.get('type')) == '1'


def curation_actions(actions):
    """Return the real customer/technician conversation in chronological order."""
    from movidesk_note_worker import automatic_receipt_action
    return sorted((action for action in actions or [] if isinstance(action, dict)
                   and action.get('id') is not None and not action.get('isDeleted')
                   and 'CORTEX-AI-NOTE:' not in str(action.get('description') or '')
                   and not automatic_receipt_action(action)),
                  key=lambda action: (str(action.get('createdDate') or ''), int(action.get('id') or 0)))


def usefulness(ticket, actions=()):
    """Pontua 0-100 se um ticket resolvido tende a gerar conhecimento reutilizável.

    É triagem, não validação: mesmo 100 pontos só gera uma *sugestão* para revisão.
    """
    signals, score = [], 0

    def add(weight, label):
        nonlocal score
        score += weight
        signals.append({'weight': weight, 'label': label})

    if ticket.get('hard_block_reason'):
        return 0, [{'weight': -100, 'label': f"Bloqueado pela refinaria: {ticket['hard_block_reason']}"}], False
    if (ticket.get('base_status') or '') not in ('Resolved', 'Closed'):
        add(-40, 'Ticket ainda não resolvido')
    else:
        add(15, 'Ticket resolvido/fechado')
    solution = norm(ticket.get('solution_statement'))
    if len(solution) >= 80:
        add(20, 'Solução registrada com detalhe')
    elif len(solution) < 30:
        add(-15, 'Sem solução registrada')
    tier = (ticket.get('quality_tier') or '').upper()
    if tier == 'GOLD':
        add(15, 'Classificado como GOLD pela refinaria')
    elif tier == 'SILVER':
        add(8, 'Classificado como SILVER pela refinaria')
    evidence = (ticket.get('evidence_level') or '').upper()
    if evidence in ('STRONG', 'CONFIRMED', 'HIGH'):
        add(15, f'Evidência de resolução {evidence}')
    elif evidence == 'MEDIUM':
        add(8, 'Evidência de resolução média')

    texts = [norm(a.get('description')) for a in actions]
    full = ' '.join([norm(ticket.get('subject')), norm(ticket.get('problem_statement')), solution, *texts])
    if ticket.get('detected_error_codes') or _ERROR.search(full):
        add(10, 'Mensagem/código de erro identificável')
    agent_texts = [t for a, t in zip(actions, texts) if is_agent(a)]
    if _STEPS.search(solution) or any(_STEPS.search(t) for t in agent_texts):
        add(10, 'Técnico descreveu procedimento')
    if _TECHNICAL_SUCCESS.search(solution) or any(_TECHNICAL_SUCCESS.search(t) for t in agent_texts):
        add(10, 'Técnico registrou o resultado bem-sucedido')
    last_agent = max((i for i, a in enumerate(actions) if is_agent(a)), default=-1)
    if any(_CONFIRM.search(t) for a, t in list(zip(actions, texts))[last_agent + 1:] if not is_agent(a)):
        add(15, 'Cliente confirmou a solução')
    if _WEAK.search(solution) or (agent_texts and _WEAK.search(agent_texts[-1])):
        add(-25, 'Resolução fraca (reinício, sem retorno, duplicado ou inatividade)')
    if (ticket.get('noise_score') or 0) > 50:
        add(-10, 'Muito ruído na conversa')

    score = max(0, min(100, score))
    return score, signals, score >= SUGGESTION_THRESHOLD


# ───────────────────────────── 2. extração estruturada ─────────────────────────────

EXTRACTION_FIELDS = ('title', 'symptom', 'error_messages', 'root_cause', 'procedure', 'prerequisites',
                     'expected_result', 'applicability', 'module_hint', 'evidence', 'confidence')

PROMPT = """Você é o curador técnico da Alternativa Sistemas (ERP Master).
Transforme o atendimento abaixo em um PROCEDIMENTO TÉCNICO REUTILIZÁVEL.

Regras obrigatórias:
- Uma resposta do Cortex é uma proposta, não comprova que o cliente resolveu. Não trate o texto gerado pela IA como confirmação de sucesso.
- Use SOMENTE o que está no atendimento. Se algo não estiver explícito, use null ou lista vazia.
- Não invente versões, menus, telas ou causas. Não inclua nomes, empresas, CNPJ, e-mails ou telefones.
- "procedure" deve ser passo a passo numerado, genérico para qualquer cliente.
- "evidence" deve conter TRECHOS LITERAIS copiados do atendimento que comprovem causa, procedimento e confirmação.
- Se o atendimento não mostra uma solução real, devolva confidence "LOW" e explique em root_cause.

Responda APENAS com JSON válido neste formato:
{{"title": "...", "symptom": "...", "error_messages": ["..."], "root_cause": "...",
 "procedure": "1. ...\\n2. ...", "prerequisites": null, "expected_result": "...",
 "applicability": {{"master_version": null, "ufs": [], "integrations": [], "tax_regime": null}},
 "module_hint": "fiscal|products|integrations|sales|finance|logistics|purchases|registration|production|infrastructure|unclassified",
 "evidence": {{"root_cause_quote": null, "procedure_quote": null, "confirmation_quote": null}},
 "confidence": "HIGH|MEDIUM|LOW"}}

ATENDIMENTO #{ticket_id}
{ticket_text}
"""


def ticket_text(ticket, actions=(), limit=14000):
    labels = lambda a: 'Nota interna' if a.get('type') == 1 else ('Técnico' if is_agent(a) else 'Cliente')
    parts = [f"Assunto: {ticket.get('subject') or ''}",
             f"Serviço: {ticket.get('service_full') or ''}",
             f"Problema (refinaria): {ticket.get('problem_statement') or ''}",
             f"Solução registrada: {ticket.get('solution_statement') or ''}"]
    parts += [f"[Ação {a.get('id')} · {labels(a)}] {str(a.get('description') or '')[:1500]}"
              for a in curation_actions(actions)[-40:]
              if (a.get('description') or '').strip()]
    return sanitize('\n'.join(parts))[:limit]


def _as_list(value):
    if isinstance(value, str):
        value = [value]
    return [str(v).strip()[:300] for v in (value or []) if str(v or '').strip()][:15]


def _as_text(value, size=6000):
    return str(value).strip()[:size] if value not in (None, '', 'null') else None


def parse_extraction(raw):
    """Normaliza a saída do modelo; tolera cercas ```json e texto em volta."""
    if isinstance(raw, dict):
        data = raw
    else:
        match = re.search(r'\{.*\}', str(raw or ''), re.S)
        if not match:
            raise ValueError('O modelo não devolveu JSON.')
        data = json.loads(match.group(0))
    app = data.get('applicability') or {}
    ev = data.get('evidence') or {}
    result = {
        'title': _as_text(data.get('title'), 200),
        'symptom': _as_text(data.get('symptom')),
        'error_messages': _as_list(data.get('error_messages')),
        'root_cause': _as_text(data.get('root_cause')),
        'procedure': _as_text(data.get('procedure'), 10000),
        'prerequisites': _as_text(data.get('prerequisites')),
        'expected_result': _as_text(data.get('expected_result')),
        'applicability': {'master_version': _as_text(app.get('master_version'), 40),
                          'ufs': [u.upper()[:2] for u in _as_list(app.get('ufs'))],
                          'integrations': [norm(i) for i in _as_list(app.get('integrations'))],
                          'tax_regime': _as_text(app.get('tax_regime'), 60)},
        'module_hint': data.get('module_hint') if data.get('module_hint') in taxonomy.MODULE_NAMES else 'unclassified',
        'evidence': {k: _as_text(ev.get(k), 600) for k in ('root_cause_quote', 'procedure_quote', 'confirmation_quote')},
        'confidence': data.get('confidence') if data.get('confidence') in ('HIGH', 'MEDIUM', 'LOW') else 'LOW',
    }
    for key in ('title', 'symptom', 'procedure'):
        if not result[key]:
            raise ValueError(f'Extração sem o campo obrigatório "{key}".')
    return {k: sanitize(v) if isinstance(v, str) else v for k, v in result.items()}


def heuristic_extraction(ticket):
    """Fallback quando o modelo está indisponível: só reorganiza o que a refinaria já tem."""
    return parse_extraction({
        'title': ticket.get('subject'), 'symptom': ticket.get('problem_statement') or ticket.get('subject'),
        'error_messages': ticket.get('detected_error_codes') or [],
        'root_cause': None, 'procedure': ticket.get('solution_statement') or '(sem procedimento registrado)',
        'module_hint': taxonomy.classify(ticket.get('subject'), ticket.get('detected_modules') or [], ticket.get('service_full')),
        'confidence': 'LOW'})


def quote_found(quote, text):
    """Anti-alucinação: o trecho citado precisa existir (≥80% dos termos) no atendimento."""
    words = [w for w in norm(quote).split() if len(w) > 3]
    if len(words) < 3:
        return False
    hay = norm(text)
    return sum(w in hay for w in words) / len(words) >= 0.8


def quality_checks(extraction, source_text):
    ev, app = extraction['evidence'], extraction['applicability']
    checks = [
        ('error', 'Mensagem de erro identificada', bool(extraction['error_messages'])),
        ('cause', 'Causa raiz descrita', bool(extraction['root_cause'])),
        ('procedure_evidence', 'Procedimento comprovado por trecho do ticket', quote_found(ev.get('procedure_quote'), source_text)),
        ('confirmation', 'Solução confirmada no histórico', quote_found(ev.get('confirmation_quote'), source_text)),
        ('version', 'Versão do Master identificada', bool(app.get('master_version'))),
        ('module', 'Módulo classificado', extraction['module_hint'] != 'unclassified'),
        ('confidence', 'Confiança do extrator alta', extraction['confidence'] == 'HIGH'),
    ]
    return [{'key': k, 'label': label, 'ok': ok} for k, label, ok in checks]


def extract(ticket, actions=()):
    import llm_client
    text = ticket_text(ticket, actions)
    answer = llm_client.generate(PROMPT.format(ticket_id=ticket.get('external_id'), ticket_text=text),
                                 temperature=0.1, max_tokens=4096)
    if answer['ok']:
        try:
            return parse_extraction(answer['text']), text, 'gemini'
        except (ValueError, json.JSONDecodeError):
            pass
    return heuristic_extraction(ticket), text, 'heuristic'


# ───────────────────────────── 3. contexto, duplicidade e conflito ─────────────────────────────

def tokens(text):
    return {w for w in norm(text).split() if len(w) > 3}


def jaccard(a, b):
    a, b = tokens(a), tokens(b)
    return len(a & b) / len(a | b) if a and b else 0.0


def context_divergence(new_app, old_app):
    """Dimensões em que os dois procedimentos se aplicam a cenários diferentes."""
    old_app = old_app or {}
    reasons = []
    for key, label in (('integrations', 'integração'), ('ufs', 'UF')):
        a, b = set(new_app.get(key) or []), set(old_app.get(key) or [])
        if a and b and not a & b:
            reasons.append(f"{label} diferente ({', '.join(sorted(a))} × {', '.join(sorted(b))})")
    for key, label in (('master_version', 'versão do Master'), ('tax_regime', 'regime tributário')):
        a, b = norm(new_app.get(key)), norm(old_app.get(key) or old_app.get(key + '_min'))
        if a and b and a != b:
            reasons.append(f'{label} diferente ({a} × {b})')
    return reasons


def compare(extraction, module_key, existing):
    """Classifica a relação entre o candidato e um procedimento publicado.

    duplicate          -> mesmo procedimento: anexar o ticket como nova evidência.
    revision           -> mesmo problema, procedimento complementar: propor nova versão.
    conflict           -> mesmo problema, procedimento divergente no mesmo contexto: revisão humana.
    different_context  -> parecido, mas outro módulo/versão/UF/integração: pode virar variante.
    """
    if existing['module_key'] != module_key and 'unclassified' not in (existing['module_key'], module_key):
        return 'different_context', ['módulo diferente']
    divergence = context_divergence(extraction['applicability'], existing.get('applicability'))
    if divergence:
        return 'different_context', divergence
    same_error = bool({norm(e) for e in extraction['error_messages']} & {norm(e) for e in existing.get('error_messages') or []})
    problem_sim = max(jaccard(extraction['symptom'], existing['symptom']), 1.0 if same_error else 0.0)
    procedure_sim = jaccard(extraction['procedure'], existing['procedure'])
    if procedure_sim >= 0.55:
        return 'duplicate', [f'procedimento {procedure_sim:.0%} equivalente']
    if problem_sim < 0.25:
        return 'unrelated', []
    if procedure_sim >= 0.25:
        return 'revision', [f'mesmo problema, procedimento {procedure_sim:.0%} semelhante']
    return 'conflict', ['mesmo problema com procedimento divergente' + (' (mesma mensagem de erro)' if same_error else '')]


def find_related(conn, extraction, module_key, exclude_item=None):
    query = ' '.join([extraction['title'] or '', extraction['symptom'] or '', *extraction['error_messages']])[:1000]
    rows = conn.execute('''
        SELECT i.id AS item_id, i.title, i.module_key, i.subtopic_key, v.id AS version_id, v.version_no,
               v.symptom, v.procedure, v.error_messages, v.applicability,
               ts_rank(to_tsvector('portuguese', v.symptom||' '||v.root_cause||' '||v.procedure||' '||array_to_string(v.error_messages,' ')),
                       websearch_to_tsquery('portuguese', %(q)s)) AS rank
        FROM cortex_kb.items i JOIN cortex_kb.versions v ON v.id = i.current_version_id
        WHERE i.status = 'active' AND (%(ex)s::bigint IS NULL OR i.id <> %(ex)s)
          AND (to_tsvector('portuguese', v.symptom||' '||v.root_cause||' '||v.procedure||' '||array_to_string(v.error_messages,' '))
               @@ websearch_to_tsquery('portuguese', %(q)s) OR similarity(i.title, %(t)s) > 0.3)
        ORDER BY rank DESC LIMIT 8''', {'q': re.sub(r'[^\w\s]', ' ', query), 't': extraction['title'], 'ex': exclude_item}).fetchall()
    related = []
    for row in rows:
        relation, reasons = compare(extraction, module_key, row)
        if relation != 'unrelated':
            related.append({'item_id': row['item_id'], 'title': row['title'], 'version_no': row['version_no'],
                            'module_key': row['module_key'], 'relation': relation, 'reasons': reasons})
    order = {'conflict': 0, 'duplicate': 1, 'revision': 2, 'different_context': 3}
    return sorted(related, key=lambda r: order[r['relation']])


# ───────────────────────────── carga do ticket ─────────────────────────────

def load_ticket(conn, external_id):
    from tech_evidence import ensure_single_tenant
    tenant = ensure_single_tenant(conn)
    ticket = conn.execute('''SELECT * FROM refined_tickets WHERE tenant_id=%s AND external_id=%s
        ORDER BY refined_at DESC NULLS LAST LIMIT 1''', (tenant, external_id)).fetchone()
    if not ticket:
        raise LookupError(f'Ticket #{external_id} não encontrado no histórico sincronizado.')
    raw = conn.execute('''SELECT payload FROM raw_tickets WHERE tenant_id=%s AND external_id=%s
        ORDER BY ingested_at DESC LIMIT 1''', (tenant, external_id)).fetchone()
    actions = ((raw['payload'] or {}).get('actions') or []) if raw else []
    return dict(ticket), [a for a in actions if isinstance(a, dict)]


def refresh_live_ticket(external_id):
    """Refresh one ticket from Movidesk before curating it.

    Curating a stale snapshot can omit the technician's resolution and turn a
    useful ticket into a question-only draft. The manual curation action is a
    suitable place for this bounded, read-only Movidesk fetch.
    """
    from config import MOVIDESK_TOKEN
    if not MOVIDESK_TOKEN:
        raise RuntimeError('Token do Movidesk ausente; não foi possível atualizar o chamado antes da curadoria.')
    from movidesk_note_worker import Api, RetryJob, Settings, persist_operational_ticket
    api = Api(Settings(interval=0))
    try:
        ticket = api.ticket(external_id)
    except RetryJob as exc:
        raise RuntimeError('Não foi possível atualizar o chamado no Movidesk antes da curadoria: ' + str(exc)) from exc
    finally:
        api.client.close()
    persist_operational_ticket(ticket)
    return ticket


def _refresh_open_candidate(candidate_id, external_id, actor, force):
    """Re-extract an existing open candidate after its live ticket was refreshed."""
    with connect() as conn:
        ticket, actions = load_ticket(conn, external_id)
    actions = curation_actions(actions)
    score, signals, eligible = usefulness(ticket, actions)
    if not eligible and not force:
        raise PermissionError(json.dumps({'score': score, 'signals': signals}, ensure_ascii=False))
    extraction, text, extractor = extract(ticket, actions)
    module = extraction['module_hint']
    sub = taxonomy.subtopic(module, extraction['title'], [extraction['symptom'] or ''])
    checks = quality_checks(extraction, text)
    with connect() as conn:
        related = find_related(conn, extraction, module)
        status = 'conflict' if any(item['relation'] == 'conflict' for item in related) else 'pending'
        conn.execute('''UPDATE cortex_kb.conflicts SET status='dismissed',resolved_by=%s,resolved_at=now(),
            resolution_note='Candidato reextraído após atualização do chamado'
            WHERE candidate_id=%s AND status='open' ''', (actor, candidate_id))
        conn.execute('''UPDATE cortex_kb.candidates SET status=%s,usefulness_score=%s,usefulness_signals=%s,
            extraction=%s,quality_checks=%s,suggested_module=%s,suggested_subtopic=%s,similar_items=%s,
            extractor=%s WHERE id=%s''',
            (status, score, Jsonb(signals), Jsonb(extraction), Jsonb(checks), module, sub,
             Jsonb(related), extractor, candidate_id))
        for item in related:
            if item['relation'] == 'conflict':
                conn.execute('INSERT INTO cortex_kb.conflicts(item_id,candidate_id,reason) VALUES (%s,%s,%s)',
                             (item['item_id'], candidate_id,
                              f"Ticket #{external_id}: " + '; '.join(item['reasons'])))
        audit(conn, 'candidate', candidate_id, 'refreshed', actor,
              'Chamado atualizado no Movidesk e histórico completo reextraído',
              after={'ticket': external_id, 'score': score, 'status': status})
    return get_candidate(candidate_id)


# ───────────────────────────── 4. fila de curadoria ─────────────────────────────

def create_candidate(external_id, actor, force=False, action_id=None, module_key=None, proposal=None,
                     refresh_live=False):
    """Analista clicou em 'Transformar em Conhecimento' (ou aceitou uma sugestão)."""
    ensure_schema()
    if refresh_live:
        refresh_live_ticket(external_id)
    with connect() as conn:
        open_ = conn.execute("SELECT id FROM cortex_kb.candidates WHERE ticket_external_id=%s AND status IN ('pending','conflict')",
                             (external_id,)).fetchone()
    if open_:
        existing = get_candidate(open_['id'])
        if action_id and existing.get('source_action_id') != str(action_id):
            raise RuntimeError('Já existe revisão aberta de outra resposta deste ticket. Conclua ou rejeite antes de selecionar outra.')
        if proposal:
            with connect() as conn:
                extraction = dict(existing.get('extraction') or {})
                extraction.pop('review_request', None)
                extraction.update(title=proposal['title'], procedure=proposal['procedure'],
                                  customer_answer=proposal['answer'], images=proposal.get('images') or [],
                                  confidence='HUMAN_PROPOSAL')
                if proposal.get('keywords') is not None:
                    extraction['keywords'] = proposal['keywords']
                if proposal.get('validated_html') is not None:
                    extraction['validated_html'] = proposal['validated_html']
                target_mod = module_key or existing['suggested_module']
                valid_subs = dict(taxonomy.subtopic_options(target_mod))
                target_sub = (proposal.get('subtopic_key')
                              if proposal.get('subtopic_key') in valid_subs
                              else existing.get('suggested_subtopic') or 'general')
                checks = quality_checks(extraction, proposal['answer'] + '\n' + proposal['procedure'])
                conn.execute('''UPDATE cortex_kb.candidates SET extraction=%s,quality_checks=%s,
                    suggested_module=%s,suggested_subtopic=%s,extractor='technician_proposal',source_question_action_id=%s,
                    source_answer_action_id=%s WHERE id=%s''',
                    (Jsonb(extraction), Jsonb(checks), target_mod, target_sub,
                     proposal.get('question_action_id'), proposal.get('answer_action_id'), open_['id']))
                existing.update(extraction=extraction, quality_checks=checks,
                                suggested_module=target_mod, suggested_subtopic=target_sub,
                                extractor='technician_proposal')
                audit(conn, 'candidate', open_['id'], 'technician:revised', actor,
                      'Proposta reenviada para curadoria', after={'extraction': extraction})
            return existing
        if refresh_live and not action_id:
            return _refresh_open_candidate(open_['id'], external_id, actor, force)
        return existing
    with connect() as conn:
        ticket, actions = load_ticket(conn, external_id)
        actions = curation_actions(actions)
        if action_id:
            selected = select_answer(actions, action_id)
            from movidesk_note_worker import plain
            ticket = {**ticket, 'solution_statement': plain(selected['description'])}
            # Bound context to the selected answer; later topics cannot rewrite it.
            position = next(i for i, a in enumerate(actions) if str(a.get('id')) == str(action_id))
            actions = [a for a in actions[:position + 1] if 'CORTEX-AI-NOTE:' not in str(a.get('description') or '')]
            actions.append({**selected, 'description': ticket['solution_statement']})
        score, signals, eligible = usefulness(ticket, actions)
        if not eligible and not force:
            raise PermissionError(json.dumps({'score': score, 'signals': signals}, ensure_ascii=False))
    # Uma proposta escrita pelo técnico é a fonte primária do rascunho. Ela não
    # passa pelo modelo e continua isolada do RAG até a aprovação administrativa.
    if proposal:
        extraction = {
            'title': proposal['title'],
            'symptom': proposal.get('question') or sanitize(ticket.get('problem_statement') or ticket.get('subject') or proposal['title']),
            'error_messages': [],
            'root_cause': 'Orientação técnica proposta; causa deve ser confirmada na curadoria.',
            'procedure': proposal['procedure'],
            'customer_answer': proposal['answer'],
            'prerequisites': None,
            'expected_result': None,
            'applicability': {},
            'module_hint': module_key or 'unclassified',
            'images': proposal.get('images') or [],
            'evidence': {'root_cause_quote': None, 'procedure_quote': proposal['procedure'],
                         'confirmation_quote': None},
            'confidence': 'HUMAN_PROPOSAL',
        }
        if proposal.get('keywords'):
            extraction['keywords'] = proposal['keywords']
        if proposal.get('validated_html'):
            extraction['validated_html'] = proposal['validated_html']
        text, extractor = proposal['answer'] + '\n' + proposal['procedure'], 'technician_proposal'
    else:
        # Chamada ao modelo fora da transação: não segura conexão nem locks.
        extraction, text, extractor = extract(ticket, actions)
    module = module_key or extraction['module_hint']
    valid_subs = dict(taxonomy.subtopic_options(module))
    proposed_sub = (proposal.get('subtopic_key') or '').strip() if proposal else ''
    sub = (proposed_sub
           if proposed_sub and (proposed_sub in valid_subs or proposed_sub in ('general', 'geral'))
           else taxonomy.subtopic(module, extraction['title'], [extraction['symptom'] or '']))
    checks = quality_checks(extraction, text)
    with connect() as conn:
        related = find_related(conn, extraction, module)
        status = 'conflict' if any(r['relation'] == 'conflict' for r in related) else 'pending'
        row = conn.execute('''INSERT INTO cortex_kb.candidates
            (ticket_external_id,status,usefulness_score,usefulness_signals,extraction,quality_checks,
             suggested_module,suggested_subtopic,similar_items,extractor,created_by,source_action_id,
             source_question_action_id,source_answer_action_id)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (ticket_external_id) WHERE status IN ('pending','conflict') DO NOTHING RETURNING id''',
            (external_id, status, score, Jsonb(signals), Jsonb(extraction), Jsonb(checks), module, sub,
             Jsonb(related), extractor, actor, str(action_id) if action_id else None,
             proposal.get('question_action_id') if proposal else None,
             proposal.get('answer_action_id') if proposal else None)).fetchone()
        if not row:
            raise RuntimeError('Outra revisão foi aberta para este ticket. Recarregue a fila.')
        for r in related:
            if r['relation'] == 'conflict':
                conn.execute('INSERT INTO cortex_kb.conflicts(item_id,candidate_id,reason) VALUES (%s,%s,%s)',
                             (r['item_id'], row['id'], f"Ticket #{external_id}: " + '; '.join(r['reasons'])))
        audit(conn, 'candidate', row['id'], 'created', actor, after={'ticket': external_id, 'score': score, 'status': status})
    return get_candidate(row['id'])


def get_candidate(candidate_id):
    with connect() as conn:
        row = conn.execute('SELECT * FROM cortex_kb.candidates WHERE id=%s', (candidate_id,)).fetchone()
        reviews = conn.execute('''SELECT ticket_external_id,action_id,example_ticket_external_id,
            question_action_id,source_answer_action_id,rating,score,subtopic_key,keywords,comment,actor,updated_at
            FROM cortex_kb.answer_reviews WHERE candidate_id=%s ORDER BY updated_at DESC''', (candidate_id,)).fetchall()
    if not row:
        raise LookupError('Sugestão não encontrada.')
    return {**dict(row), 'answer_reviews': reviews}


def list_candidates(status=('pending', 'conflict'), module=None, limit=100):
    ensure_schema()
    with connect() as conn:
        return conn.execute('''SELECT id,ticket_external_id,status,usefulness_score,suggested_module,suggested_subtopic,
                   extraction->>'title' AS title, extraction->>'confidence' AS confidence, quality_checks, similar_items,
                   extractor, created_by, created_at
            FROM cortex_kb.candidates WHERE status = ANY(%s) AND (%s::text IS NULL OR suggested_module=%s)
            ORDER BY (status='conflict') DESC, usefulness_score DESC, created_at LIMIT %s''',
            (list(status), module, module, limit)).fetchall()


def suggest_tickets(limit=30, module=None):
    """Tickets resolvidos que parecem conhecimento e ainda não foram avaliados."""
    ensure_schema()
    from tech_evidence import ensure_single_tenant
    with connect() as conn:
        tenant = ensure_single_tenant(conn)
        rows = conn.execute('''
            WITH pool AS (
                SELECT DISTINCT ON (r.external_id) r.*
                FROM refined_tickets r
                WHERE r.tenant_id=%(t)s AND r.base_status IN ('Resolved','Closed') AND r.hard_block_reason IS NULL
                  AND COALESCE(length(r.solution_statement),0) >= 30
                  AND NOT EXISTS (SELECT 1 FROM cortex_kb.candidates c WHERE c.ticket_external_id=r.external_id)
                  AND NOT EXISTS (SELECT 1 FROM cortex_kb.sources s WHERE s.source_type='ticket' AND s.external_ref=r.external_id::text)
                ORDER BY r.external_id, r.refined_at DESC
            ), top AS (
                SELECT * FROM pool ORDER BY final_score DESC NULLS LAST, resolved_date DESC NULLS LAST LIMIT 300
            )
            SELECT top.*, raw.payload->'actions' AS actions FROM top
            LEFT JOIN LATERAL (SELECT payload FROM raw_tickets x WHERE x.tenant_id=top.tenant_id AND x.external_id=top.external_id
                               ORDER BY ingested_at DESC LIMIT 1) raw ON true''', {'t': tenant}).fetchall()
    out = []
    for row in rows:
        actions = [a for a in (row.pop('actions') or []) if isinstance(a, dict)]
        score, signals, eligible = usefulness(row, actions)
        mod = taxonomy.classify(row['subject'], row.get('detected_modules') or [], row.get('service_full'))
        if eligible and (not module or module == mod):
            out.append({'ticket_external_id': row['external_id'], 'subject': row['subject'], 'module_key': mod,
                        'score': score, 'signals': signals, 'resolved_date': row.get('resolved_date')})
    out.sort(key=lambda r: (-r['score'], -(r['ticket_external_id'])))
    return out[:limit]


# ───────────────────────────── 5. decisões humanas ─────────────────────────────

def _validate_content(c):
    for key in ('title', 'symptom', 'root_cause', 'procedure'):
        if len((c.get(key) or '').strip()) < 5:
            raise ValueError(f'Preencha "{key}" antes de publicar — conhecimento oficial precisa de causa e procedimento.')
    if c.get('customer_answer'):
        c['customer_answer'] = sanitize(c['customer_answer']).strip()
    if c['module_key'] == 'unclassified':
        raise ValueError('Selecione o módulo antes de publicar.')
    if c['module_key'] not in taxonomy.MODULE_NAMES and c['module_key'] not in {m['key'] for m in taxonomy_options()}:
        raise ValueError('Módulo inválido.')
    if c['subtopic_key'] not in dict(taxonomy.subtopic_options(c['module_key'])):
        raise ValueError('Subtópico inválido para o módulo.')
    for key in ('title', 'symptom', 'root_cause', 'procedure', 'prerequisites', 'expected_result'):
        if c.get(key):
            c[key] = sanitize(c[key]).strip()
    from datetime import date
    app = c.get('applicability') or {}
    for key in ('valid_from', 'valid_until'):
        if app.get(key): date.fromisoformat(app[key])
    if app.get('valid_from') and app.get('valid_until') and app['valid_from'] > app['valid_until']:
        raise ValueError('A validade final deve ser posterior à inicial.')
    allowed = {image['file'] for image in procedure_images()}
    steps = {int(n) for n in re.findall(r'^\s*(\d+)\.\s', c['procedure'], re.M)}
    image_names = set()
    for image in c.get('images') or []:
        placement = image.get('placement') or 'procedure'
        if image.get('file') not in allowed or placement not in ('answer', 'procedure'):
            raise ValueError('A imagem revisada ou seu local de exibição é inválido.')
        if placement == 'procedure' and image.get('after_step') not in steps:
            raise ValueError('Vincule cada imagem do procedimento a um passo numerado existente (1. …).')
        image['placement'] = placement
        image['after_step'] = image.get('after_step') if placement == 'procedure' else None
        image['name'] = sanitize(str(image.get('name') or image.get('caption') or 'Tela de referência')).strip()[:120]
        if len(image['name']) < 3 or norm(image['name']) in image_names:
            raise ValueError('Cada imagem precisa de um nome único com pelo menos 3 caracteres.')
        image_names.add(norm(image['name']))
        image['caption'] = sanitize(str(image.get('caption') or 'Tela de referência'))[:300]
    return c


def _embed(text, task_type='RETRIEVAL_DOCUMENT'):
    if not GEMINI_API_KEY:
        return None
    req = urllib.request.Request(
        f'https://generativelanguage.googleapis.com/v1beta/{GEMINI_EMBEDDING_MODEL}:embedContent?key={GEMINI_API_KEY}',
        data=json.dumps({'model': GEMINI_EMBEDDING_MODEL, 'content': {'parts': [{'text': text[:8000]}]},
                         'taskType': task_type}).encode(), headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            values = json.loads(r.read())['embedding']['values']
        # A dimensão acompanha o modelo configurado; vetor incompatível não é gravado.
        return values if len(values) == GEMINI_EMBEDDING_DIMENSIONS else None
    except Exception:
        return None


def document_text(item, version):
    app = version['applicability'] or {}
    scope = '; '.join(f'{k}: {", ".join(v) if isinstance(v, list) else v}' for k, v in app.items() if v)
    images = '; '.join(f"{image.get('name') or 'Imagem'}: {image.get('caption') or ''}"
                       for image in version.get('images') or [])
    return '\n'.join(p for p in (
        f"PROCEDIMENTO OFICIAL ALTERNATIVA: {item['title']}",
        f"Módulo: {taxonomy.MODULE_NAMES.get(item['module_key'], item['module_key'])}",
        f"Sintoma: {version['symptom']}",
        f"Mensagens de erro: {'; '.join(version['error_messages'])}" if version['error_messages'] else '',
        f"Causa: {version['root_cause']}",
        f"Aplica-se a: {scope}" if scope else '',
        f"Pré-requisitos: {version['prerequisites']}" if version.get('prerequisites') else '',
        f"Resposta pronta ao cliente: {version['customer_answer']}" if version.get('customer_answer') else '',
        f"Procedimento:\n{version['procedure']}",
        f"Imagens de apoio: {images}" if images else '',
        f"Resultado esperado: {version['expected_result']}" if version.get('expected_result') else '',
        f"Palavras-chave: {', '.join(version['keywords'])}" if version.get('keywords') else '') if p)


def _publish(conn, item_id, embedding_cache=None):
    """Espelha a versão vigente em ticket_knowledge_nodes — efeito imediato no RAG, sem deploy."""
    item = conn.execute('SELECT * FROM cortex_kb.items WHERE id=%s', (item_id,)).fetchone()
    version = conn.execute('SELECT * FROM cortex_kb.versions WHERE id=%s', (item['current_version_id'],)).fetchone()
    refs = [r['external_ref'] for r in conn.execute(
        "SELECT external_ref FROM cortex_kb.sources WHERE item_id=%s AND source_type='ticket' ORDER BY id", (item_id,))]
    source_refs = conn.execute('SELECT source_type,external_ref,version_id FROM cortex_kb.sources WHERE item_id=%s ORDER BY id', (item_id,)).fetchall()
    text = document_text(item, version)
    vector = embedding_cache
    app = version['applicability'] or {}
    values = dict(
        source_type='OFFICIAL_KB', source_id=f'canon:{item_id}', source_external_id=int(refs[0]) if refs else None,
        title=item['title'], content_for_embedding=text, problem=version['symptom'], diagnosis=version['root_cause'],
        solution=('\n\n'.join(p for p in (version.get('customer_answer'), version['procedure']) if p)),
        result=version['expected_result'],
        modules=[taxonomy.MODULE_NAMES.get(item['module_key'], item['module_key'])], detected_error_codes=version['error_messages'],
        version_reference=(app.get('master_version') or None), tier='KB', confidence='HIGH',
        validation_score=100, final_score=100, source_authority=100,
        validation_evidence=[f"{r['source_type']}:{r['external_ref']}" for r in source_refs][:50],
        metadata=Jsonb({'canonical_item_id': item_id, 'version_no': version['version_no'],
                        'applicability': app, 'approved_by': version['approved_by'],
                        'approved_at': str(version['approved_at']), 'subtopic': item['subtopic_key'],
                        'source_references': source_refs,
                        'images': version.get('images') or [], 'prerequisites': version.get('prerequisites')}),
        embedding=('[' + ','.join(map(str, vector)) + ']') if vector else None,
        embedding_generated=bool(vector), embedding_model=GEMINI_EMBEDDING_MODEL.split('/')[-1][:50])
    existing = conn.execute("SELECT id FROM ticket_knowledge_nodes WHERE source_id=%s", (f'canon:{item_id}',)).fetchone()
    cols = list(values)
    if existing:
        sets = ','.join(f'{c}=%s' + ('::vector' if c == 'embedding' else '') for c in cols)
        conn.execute(f'UPDATE ticket_knowledge_nodes SET {sets}, updated_at=now() WHERE id=%s', [*values.values(), existing['id']])
        node_id = existing['id']
    else:
        marks = ','.join('%s::vector' if c == 'embedding' else '%s' for c in cols)
        node_id = conn.execute(f'INSERT INTO ticket_knowledge_nodes ({",".join(cols)}) VALUES ({marks}) RETURNING id',
                               list(values.values())).fetchone()['id']
    conn.execute('UPDATE cortex_kb.versions SET published_node_id=%s, embedding_ok=%s WHERE id=%s',
                 (node_id, bool(vector), version['id']))
    return node_id


def _new_version(conn, item_id, content, actor, reason):
    previous = conn.execute("SELECT max(version_no) AS n FROM cortex_kb.versions WHERE item_id=%s", (item_id,)).fetchone()['n'] or 0
    conn.execute("UPDATE cortex_kb.versions SET status='superseded' WHERE item_id=%s AND status='published'", (item_id,))
    version = conn.execute('''INSERT INTO cortex_kb.versions
        (item_id,version_no,symptom,error_messages,root_cause,procedure,customer_answer,prerequisites,expected_result,
         applicability,keywords,status,change_reason,approved_by,images)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'published',%s,%s,%s) RETURNING *''',
        (item_id, previous + 1, content['symptom'], content.get('error_messages') or [], content['root_cause'],
         content['procedure'], content.get('customer_answer'), content.get('prerequisites'), content.get('expected_result'),
         Jsonb(content.get('applicability') or {}), content.get('keywords') or [], reason, actor, Jsonb(content.get('images') or []))).fetchone()
    conn.execute('''UPDATE cortex_kb.items SET current_version_id=%s, title=%s, module_key=%s, subtopic_key=%s,
        status='active', updated_at=now() WHERE id=%s''',
        (version['id'], content['title'], content['module_key'], content['subtopic_key'], item_id))
    return version


def approve(candidate_id, decision, content, actor, reason, target_item_id=None):
    """decision: 'new' (novo procedimento) | 'revise' (nova versão de item existente) | 'attach' (só evidência)."""
    if decision not in ('new', 'revise', 'attach'):
        raise ValueError('Decisão inválida.')
    if len((reason or '').strip()) < 5:
        raise ValueError('Informe o motivo da decisão (fica na auditoria).')
    if decision != 'new' and not target_item_id:
        raise ValueError('Selecione o procedimento existente.')
    if decision != 'attach':
        content = _validate_content(dict(content))
        embedding = _embed(document_text({'title': content['title'], 'module_key': content['module_key']},
                                         {**content, 'error_messages': content.get('error_messages') or [],
                                          'applicability': content.get('applicability') or {}}))
    ensure_schema()
    with connect() as conn:
        cand = conn.execute('SELECT * FROM cortex_kb.candidates WHERE id=%s FOR UPDATE', (candidate_id,)).fetchone()
        if not cand or cand['status'] not in ('pending', 'conflict'):
            raise RuntimeError('Esta sugestão já foi decidida. Recarregue a fila.')
        if decision == 'new':
            item_id = conn.execute('''INSERT INTO cortex_kb.items(title,module_key,subtopic_key,created_by)
                VALUES (%s,%s,%s,%s) RETURNING id''', (content['title'], content['module_key'], content['subtopic_key'], actor)).fetchone()['id']
        else:
            item = conn.execute("SELECT * FROM cortex_kb.items WHERE id=%s FOR UPDATE", (target_item_id,)).fetchone()
            if not item:
                raise LookupError('Procedimento não encontrado.')
            item_id = item['id']
        version = _new_version(conn, item_id, content, actor, reason) if decision != 'attach' else None
        conn.execute('''INSERT INTO cortex_kb.sources(item_id,version_id,source_type,external_ref,note)
            VALUES (%s,%s,'ticket',%s,%s) ON CONFLICT (item_id,source_type,external_ref) DO NOTHING''',
            (item_id, version['id'] if version else None, str(cand['ticket_external_id']), reason))
        _publish(conn, item_id, embedding if decision != 'attach' else
                 _cached_vector(conn, item_id))
        conn.execute('''UPDATE cortex_kb.candidates SET status=%s, decided_by=%s, decided_at=now(), decision_note=%s,
            resulting_item_id=%s WHERE id=%s''', ('merged' if decision == 'attach' else 'approved', actor, reason, item_id, candidate_id))
        conn.execute('''UPDATE cortex_kb.conflicts SET status='resolved', resolved_by=%s, resolved_at=now(), resolution_note=%s
            WHERE candidate_id=%s AND status='open' ''', (actor, f'{decision}: {reason}', candidate_id))
        audit(conn, 'candidate', candidate_id, f'approved:{decision}', actor, reason, before=dict(cand),
              after={'item_id': item_id, 'version_no': version['version_no'] if version else None})
    return item_detail(item_id)


def _cached_vector(conn, item_id):
    row = conn.execute("SELECT embedding::text AS v FROM ticket_knowledge_nodes WHERE source_id=%s AND embedding IS NOT NULL",
                       (f'canon:{item_id}',)).fetchone()
    return json.loads(row['v']) if row else None


def request_revision(candidate_id, actor, reason):
    if len((reason or '').strip()) < 5:
        raise ValueError('Informe o ajuste solicitado ao técnico.')
    ensure_schema()
    with connect() as conn:
        cand = conn.execute('SELECT * FROM cortex_kb.candidates WHERE id=%s FOR UPDATE', (candidate_id,)).fetchone()
        if not cand or cand['status'] not in ('pending', 'conflict'):
            raise RuntimeError('Esta sugestão já foi decidida.')
        request = {'actor': actor, 'reason': sanitize(reason)}
        conn.execute("UPDATE cortex_kb.candidates SET extraction=extraction || %s WHERE id=%s",
                     (Jsonb({'review_request': request}), candidate_id))
        audit(conn, 'candidate', candidate_id, 'revision:requested', actor, reason, before=dict(cand), after=request)
    return request


def revision_requests(ticket_id):
    try:
        with connect() as conn:
            return conn.execute("""SELECT id,extraction->'review_request' request FROM cortex_kb.candidates
                WHERE ticket_external_id=%s AND status IN ('pending','conflict')
                AND extraction ? 'review_request'""", (ticket_id,)).fetchall()
    except psycopg.Error:
        return []


def reject(candidate_id, actor, reason):
    if len((reason or '').strip()) < 5:
        raise ValueError('Informe o motivo da rejeição — ajuda a calibrar as próximas sugestões.')
    ensure_schema()
    with connect() as conn:
        cand = conn.execute('SELECT * FROM cortex_kb.candidates WHERE id=%s FOR UPDATE', (candidate_id,)).fetchone()
        if not cand or cand['status'] not in ('pending', 'conflict'):
            raise RuntimeError('Esta sugestão já foi decidida.')
        conn.execute("UPDATE cortex_kb.candidates SET status='rejected',decided_by=%s,decided_at=now(),decision_note=%s WHERE id=%s",
                     (actor, reason, candidate_id))
        conn.execute("UPDATE cortex_kb.conflicts SET status='dismissed',resolved_by=%s,resolved_at=now(),resolution_note=%s "
                     "WHERE candidate_id=%s AND status='open'", (actor, reason, candidate_id))
        audit(conn, 'candidate', candidate_id, 'rejected', actor, reason)


def deprecate(item_id, actor, reason):
    """Retira do RAG imediatamente; histórico e versões permanecem para auditoria."""
    if len((reason or '').strip()) < 5:
        raise ValueError('Informe o motivo da inativação.')
    ensure_schema()
    with connect() as conn:
        item = conn.execute("SELECT * FROM cortex_kb.items WHERE id=%s FOR UPDATE", (item_id,)).fetchone()
        if not item:
            raise LookupError('Procedimento não encontrado.')
        conn.execute("UPDATE cortex_kb.items SET status='deprecated', updated_at=now() WHERE id=%s", (item_id,))
        conn.execute("UPDATE cortex_kb.versions SET status='deprecated' WHERE item_id=%s AND status='published'", (item_id,))
        conn.execute("DELETE FROM ticket_knowledge_nodes WHERE source_id=%s", (f'canon:{item_id}',))
        audit(conn, 'item', item_id, 'deprecated', actor, reason, before=dict(item))


def revise_item(item_id, content, actor, reason):
    """Edição direta pelo curador (sem ticket de origem) — também gera nova versão."""
    if len((reason or '').strip()) < 5:
        raise ValueError('Informe o motivo da revisão.')
    content = _validate_content(dict(content))
    embedding = _embed(document_text(content, {**content, 'error_messages': content.get('error_messages') or [],
                                               'applicability': content.get('applicability') or {}}))
    ensure_schema()
    with connect() as conn:
        if not conn.execute("SELECT 1 FROM cortex_kb.items WHERE id=%s FOR UPDATE", (item_id,)).fetchone():
            raise LookupError('Procedimento não encontrado.')
        version = _new_version(conn, item_id, content, actor, reason)
        _publish(conn, item_id, embedding)
        audit(conn, 'item', item_id, 'revised', actor, reason, after={'version_no': version['version_no']})
    return item_detail(item_id)


def feedback(item_id, rating, actor, comment=None, ticket_external_id=None):
    """Sinal de uso. Avaliações negativas recorrentes abrem revisão, nunca alteram o conteúdo."""
    ensure_schema()
    with connect() as conn:
        item = conn.execute("SELECT * FROM cortex_kb.items WHERE id=%s AND status='active'", (item_id,)).fetchone()
        if not item:
            raise LookupError('Procedimento não encontrado ou inativo.')
        conn.execute('''INSERT INTO cortex_kb.feedback(item_id,version_id,rating,comment,ticket_external_id,actor)
            VALUES (%s,%s,%s,%s,%s,%s)''', (item_id, item['current_version_id'], rating, sanitize(comment) if comment else None,
                                            ticket_external_id, actor))
        stats = conn.execute('''SELECT count(*) FILTER (WHERE rating<0) AS neg, count(*) FILTER (WHERE rating>0) AS pos
            FROM cortex_kb.feedback WHERE version_id=%s''', (item['current_version_id'],)).fetchone()
        if stats['neg'] >= 3 and stats['neg'] > stats['pos'] and not conn.execute(
                "SELECT 1 FROM cortex_kb.conflicts WHERE item_id=%s AND status='open' AND candidate_id IS NULL", (item_id,)).fetchone():
            conn.execute("INSERT INTO cortex_kb.conflicts(item_id,reason) VALUES (%s,%s)",
                         (item_id, f"{stats['neg']} avaliações negativas na versão vigente — revisar procedimento"))
        return dict(stats)


# ───────────────────────────── consultas do painel ─────────────────────────────

def overview():
    ensure_schema()
    with connect() as conn:
        items = conn.execute('''SELECT module_key, subtopic_key, count(*) AS n FROM cortex_kb.items
            WHERE status='active' GROUP BY 1,2''').fetchall()
        pending = conn.execute('''SELECT suggested_module AS module_key, count(*) AS n FROM cortex_kb.candidates
            WHERE status IN ('pending','conflict') GROUP BY 1''').fetchall()
        totals = conn.execute('''SELECT
            (SELECT count(*) FROM cortex_kb.items WHERE status='active') AS active,
            (SELECT count(*) FROM cortex_kb.items WHERE status='deprecated') AS deprecated,
            (SELECT count(*) FROM cortex_kb.candidates WHERE status IN ('pending','conflict')) AS pending,
            (SELECT count(*) FROM cortex_kb.conflicts WHERE status='open') AS conflicts,
            (SELECT count(*) FROM cortex_kb.versions WHERE status='published' AND NOT embedding_ok) AS without_vector''').fetchone()
    tree = []
    for mod in taxonomy_options():
        key, label, description = mod['key'], mod['label'], mod['description']
        subs = {s: 0 for s, _ in taxonomy.subtopic_options(key)}
        for r in items:
            if r['module_key'] == key:
                subs[r['subtopic_key']] = subs.get(r['subtopic_key'], 0) + r['n']
        tree.append({'key': key, 'label': label, 'description': description,
                     'items': sum(subs.values()), 'pending': next((p['n'] for p in pending if p['module_key'] == key), 0),
                     'subtopics': [{'key': s, 'label': dict(taxonomy.subtopic_options(key)).get(s, s), 'items': n}
                                   for s, n in subs.items()]})
    return {'totals': dict(totals), 'modules': tree}


def list_items(module=None, subtopic=None, status='active', q=None, limit=200):
    ensure_schema()
    with connect() as conn:
        return conn.execute('''
            SELECT i.id, i.title, i.module_key, i.subtopic_key, i.status, i.updated_at, v.version_no, v.embedding_ok,
                   v.applicability, (SELECT count(*) FROM cortex_kb.sources s WHERE s.item_id=i.id) AS sources,
                   (SELECT count(*) FROM cortex_kb.conflicts c WHERE c.item_id=i.id AND c.status='open') AS open_conflicts,
                   (SELECT coalesce(sum(rating),0) FROM cortex_kb.feedback f WHERE f.version_id=v.id) AS feedback
            FROM cortex_kb.items i LEFT JOIN cortex_kb.versions v ON v.id=i.current_version_id
            WHERE i.status=%(st)s AND (%(m)s::text IS NULL OR i.module_key=%(m)s) AND (%(s)s::text IS NULL OR i.subtopic_key=%(s)s)
              AND (%(q)s::text IS NULL OR i.title ILIKE '%%'||%(q)s||'%%' OR v.procedure ILIKE '%%'||%(q)s||'%%'
                   OR array_to_string(v.error_messages,' ') ILIKE '%%'||%(q)s||'%%')
            ORDER BY open_conflicts DESC, i.updated_at DESC LIMIT %(l)s''',
            {'st': status, 'm': module, 's': subtopic, 'q': q, 'l': limit}).fetchall()


def item_detail(item_id):
    ensure_schema()
    with connect() as conn:
        item = conn.execute('SELECT * FROM cortex_kb.items WHERE id=%s', (item_id,)).fetchone()
        if not item:
            raise LookupError('Procedimento não encontrado.')
        return {**item,
                'versions': conn.execute('SELECT * FROM cortex_kb.versions WHERE item_id=%s ORDER BY version_no DESC', (item_id,)).fetchall(),
                'sources': conn.execute('SELECT * FROM cortex_kb.sources WHERE item_id=%s ORDER BY id', (item_id,)).fetchall(),
                'conflicts': conn.execute('SELECT * FROM cortex_kb.conflicts WHERE item_id=%s ORDER BY status, created_at DESC', (item_id,)).fetchall(),
                'audit': conn.execute("SELECT * FROM cortex_kb.audit WHERE entity='item' AND entity_id=%s ORDER BY at DESC LIMIT 50", (item_id,)).fetchall()}


def test_item_retrieval(item_id, query):
    """Executa a mesma recuperação do CORTEX e informa a posição do item esperado."""
    query = sanitize(query).strip()
    if len(query) < 5:
        raise ValueError('Escreva uma pergunta de teste com pelo menos 5 caracteres.')
    ensure_schema()
    with connect() as conn:
        item = conn.execute('SELECT id,title,status FROM cortex_kb.items WHERE id=%s', (item_id,)).fetchone()
        if not item:
            raise LookupError('Procedimento não encontrado.')
        if item['status'] != 'active':
            raise ValueError('Este procedimento está inativo e não participa das respostas da IA.')
        query_vector = _embed(query, 'RETRIEVAL_QUERY')
        rows = knowledge_access.search(conn, query, top_k=10, embedding=query_vector,
                                       source_types=('OFFICIAL_KB',), maintain_index=False)
    expected_source = f'canon:{item_id}'
    rank = next((position for position, row in enumerate(rows, 1)
                 if str(row.get('source_id')) == expected_source), None)
    results = []
    for position, row in enumerate(rows, 1):
        results.append({
            'rank': position,
            'source_id': row.get('source_id'),
            'title': row.get('title'),
            'score': float(row.get('combined_score') or 0),
            'retrieval_method': row.get('retrieval_method'),
            'is_expected': str(row.get('source_id')) == expected_source,
        })
    return {'query': query, 'item_id': item_id, 'item_title': item['title'],
            'found': rank is not None, 'rank': rank,
            'query_embedding_ok': query_vector is not None, 'results': results}


def resolve_conflict(conflict_id, actor, note, dismiss=False):
    if len((note or '').strip()) < 5:
        raise ValueError('Descreva como o conflito foi tratado.')
    ensure_schema()
    with connect() as conn:
        row = conn.execute("""UPDATE cortex_kb.conflicts SET status=%s, resolved_by=%s, resolved_at=now(), resolution_note=%s
            WHERE id=%s AND status='open' RETURNING item_id""", ('dismissed' if dismiss else 'resolved', actor, note, conflict_id)).fetchone()
        if not row:
            raise RuntimeError('Conflito já tratado.')
        audit(conn, 'item', row['item_id'], 'conflict_' + ('dismissed' if dismiss else 'resolved'), actor, note)


def taxonomy_options():
    ensure_schema()
    with connect() as conn:
        overrides = {r['key']: dict(r) for r in conn.execute('SELECT * FROM cortex_kb.taxonomy ORDER BY label')}
    result = []
    for key, label, description in taxonomy.MODULES:
        row = overrides.pop(key, {})
        result.append({'key': key, 'label': row.get('label', label), 'description': row.get('description', description),
                       'subtopics': taxonomy.subtopic_options(key)})
    result.extend({'key': k, 'label': r['label'], 'description': r['description'],
                   'subtopics': [('general', 'Geral / outros')]} for k, r in overrides.items())
    return result


def save_module(key, label, description, actor):
    if not re.fullmatch(r'[a-z][a-z0-9_]{1,49}', key) or key == 'unclassified':
        raise ValueError('Use uma chave de 2 a 50 letras minúsculas, números ou _. A classificar é reservado.')
    label = label.strip()
    if not 2 <= len(label) <= 80:
        raise ValueError('Nome do módulo deve ter de 2 a 80 caracteres.')
    ensure_schema()
    with connect() as conn:
        before = conn.execute('SELECT * FROM cortex_kb.taxonomy WHERE key=%s', (key,)).fetchone()
        conn.execute("""INSERT INTO cortex_kb.taxonomy(key,label,description,updated_by) VALUES (%s,%s,%s,%s)
            ON CONFLICT(key) DO UPDATE SET label=excluded.label, description=excluded.description,
            updated_by=excluded.updated_by, updated_at=now()""", (key,label,description[:500],actor))
        audit(conn, 'taxonomy', 0, 'module_saved', actor, before=before,
              after={'key': key, 'label': label, 'description': description})
    return taxonomy_options()


# ───────────────────────────── versões do Master ─────────────────────────────

RELEASE_STATUSES = {'active', 'monitoring', 'fixed', 'inactive'}


def _validate_release(data):
    result = dict(data)
    for key in ('version_label', 'title', 'narrative'):
        result[key] = sanitize(result.get(key)).strip()
        if len(result[key]) < 3:
            raise ValueError(f'Preencha {key.replace("_", " ")} com pelo menos 3 caracteres.')
    if len(result['version_label']) > 40 or len(result['title']) > 200 or len(result['narrative']) > 8000:
        raise ValueError('O aviso de versão excede o tamanho permitido.')
    result['impact'] = sanitize(result.get('impact')).strip()[:4000] or None
    result['workaround'] = sanitize(result.get('workaround')).strip()[:8000] or None
    result['status'] = result.get('status') or 'active'
    if result['status'] not in RELEASE_STATUSES:
        raise ValueError('Situação da versão inválida.')
    valid_modules = {m['key'] for m in taxonomy_options()}
    result['modules'] = list(dict.fromkeys(m for m in result.get('modules') or [] if m in valid_modules))[:20]
    result['error_messages'] = [sanitize(v).strip()[:500] for v in result.get('error_messages') or [] if str(v).strip()][:20]
    evidence = []
    for item in result.get('evidence') or []:
        kind = str(item.get('kind') or 'note')
        raw_value = str(item.get('value') or '').strip()
        value = raw_value[:1000] if kind in {'ticket', 'url'} else sanitize(raw_value).strip()[:1000]
        label = sanitize(item.get('label')).strip()[:200]
        if kind not in {'ticket', 'url', 'note'} or not value:
            continue
        if kind == 'ticket' and not value.isdigit():
            raise ValueError('Evidência do tipo ticket deve conter somente o número do chamado.')
        if kind == 'url' and not re.fullmatch(r'https?://[^\s]+', value):
            raise ValueError('Informe uma URL http ou https válida na evidência.')
        evidence.append({'kind': kind, 'value': value, 'label': label})
    result['evidence'] = evidence[:30]
    return result


def _publish_release(conn, row, vector=None):
    active = row['status'] in {'active', 'monitoring', 'fixed'}
    source_id = f"master-release:{row['id']}"
    if not active:
        conn.execute('DELETE FROM ticket_knowledge_nodes WHERE source_id=%s', (source_id,))
        return
    status_text = {'active': 'problema conhecido ativo', 'monitoring': 'em monitoramento',
                   'fixed': 'corrigido; confirmar se o cliente ainda usa esta versão'}[row['status']]
    evidence = row['evidence'] or []
    tickets = [e['value'] for e in evidence if e.get('kind') == 'ticket' and str(e.get('value')).isdigit()]
    links = [e for e in evidence if e.get('kind') == 'url']
    procedure = row['workaround'] or (
        f"1. Confirme se o cliente utiliza a versão {row['version_label']} do Master.\n"
        "2. Compare o comportamento e a mensagem apresentada com este aviso.\n"
        "3. Se coincidirem, encaminhe o caso ao suporte com a evidência e informe que existe uma ocorrência conhecida."
    )
    text = '\n'.join(filter(None, (
        f"AVISO OFICIAL DE VERSÃO DO MASTER {row['version_label']}: {row['title']}",
        f"Situação: {status_text}.", f"Comportamento conhecido: {row['narrative']}",
        f"Impacto: {row['impact']}" if row['impact'] else '',
        f"Mensagens: {'; '.join(row['error_messages'])}" if row['error_messages'] else '',
        f"Orientação:\n{procedure}",
    )))
    values = dict(source_type='OFFICIAL_KB', source_id=source_id,
        source_external_id=int(tickets[0]) if tickets else None, title=f"Master {row['version_label']} — {row['title']}",
        content_for_embedding=text, problem=row['narrative'], diagnosis=f"Comportamento conhecido na versão {row['version_label']} ({status_text}).",
        solution=procedure, result=row['impact'], modules=[taxonomy.MODULE_NAMES.get(m, m) for m in row['modules']],
        detected_error_codes=row['error_messages'], version_reference=row['version_label'], tier='KB', confidence='HIGH',
        validation_score=100, final_score=100, source_authority=100,
        validation_evidence=[f"ticket:{v}" for v in tickets][:50],
        metadata=Jsonb({'knowledge_kind': 'master_release', 'release_note_id': row['id'],
                        'status': row['status'], 'approved_by': row['approved_by'],
                        'evidence': evidence, 'source_url': links[0]['value'] if links else None}),
        embedding=('[' + ','.join(map(str, vector)) + ']') if vector else None,
        embedding_generated=bool(vector), embedding_model=GEMINI_EMBEDDING_MODEL.split('/')[-1][:50])
    existing = conn.execute('SELECT id FROM ticket_knowledge_nodes WHERE source_id=%s', (source_id,)).fetchone()
    cols = list(values)
    if existing:
        sets = ','.join(f'{c}=%s' + ('::vector' if c == 'embedding' else '') for c in cols)
        conn.execute(f'UPDATE ticket_knowledge_nodes SET {sets},updated_at=now() WHERE id=%s', [*values.values(), existing['id']])
    else:
        marks = ','.join('%s::vector' if c == 'embedding' else '%s' for c in cols)
        conn.execute(f'INSERT INTO ticket_knowledge_nodes ({",".join(cols)}) VALUES ({marks})', list(values.values()))


def save_master_release(data, actor, release_id=None):
    content = _validate_release(data)
    vector = _embed('\n'.join(filter(None, (content['version_label'], content['title'], content['narrative'],
                                             content.get('impact'), content.get('workaround'),
                                             ' '.join(content['error_messages'])))))
    ensure_schema()
    with connect() as conn:
        before = None
        if release_id:
            before = conn.execute('SELECT * FROM cortex_kb.master_release_notes WHERE id=%s FOR UPDATE', (release_id,)).fetchone()
            if not before:
                raise LookupError('Aviso de versão não encontrado.')
            row = conn.execute('''UPDATE cortex_kb.master_release_notes SET version_label=%s,title=%s,narrative=%s,
                impact=%s,workaround=%s,status=%s,modules=%s,error_messages=%s,evidence=%s,approved_by=%s,updated_at=now()
                WHERE id=%s RETURNING *''', (content['version_label'], content['title'], content['narrative'], content['impact'],
                content['workaround'], content['status'], content['modules'], content['error_messages'], Jsonb(content['evidence']),
                actor, release_id)).fetchone()
        else:
            row = conn.execute('''INSERT INTO cortex_kb.master_release_notes
                (version_label,title,narrative,impact,workaround,status,modules,error_messages,evidence,approved_by)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *''',
                (content['version_label'], content['title'], content['narrative'], content['impact'], content['workaround'],
                 content['status'], content['modules'], content['error_messages'], Jsonb(content['evidence']), actor)).fetchone()
        _publish_release(conn, row, vector)
        audit(conn, 'master_release', row['id'], 'updated' if release_id else 'created', actor,
              'Conhecimento de versão aprovado pelo técnico', before=dict(before) if before else None, after=dict(row))
        return dict(row)


def list_master_releases(status=None):
    ensure_schema()
    with connect() as conn:
        return conn.execute('''SELECT * FROM cortex_kb.master_release_notes
            WHERE (%s::text IS NULL OR status=%s) ORDER BY updated_at DESC,id DESC''', (status, status)).fetchall()


def select_answer(actions, action_id):
    selected = next((a for a in actions if str(a.get('id')) == str(action_id)), None)
    if not selected or 'CORTEX-AI-NOTE:' not in str(selected.get('description') or ''):
        raise ValueError('Selecione uma resposta do Cortex existente no ticket sincronizado.')
    return selected


def _parse_cortex_note(description):
    """Extrai de forma estruturada o conteúdo da resposta sugerida pela IA e seus metadados."""
    from movidesk_note_worker import plain
    raw = str(description or '')
    raw_with_breaks = re.sub(r'<\s*br\s*/?\s*>', '\n', raw, flags=re.I)
    raw_with_breaks = re.sub(r'<\s*/\s*(?:div|p|li|tr|h[1-6])\s*>', '\n', raw_with_breaks, flags=re.I)
    lines = [re.sub(r'[ \t]+', ' ', plain(line)).strip() for line in raw_with_breaks.splitlines()]
    text = sanitize('\n'.join(line for line in lines if line)).strip()

    specialist_match = re.search(r'Especialista acionado:\s*([^\n·|<]+)', text, re.I)
    decision_match = re.search(r'Decisão interna:\s*([^\n·|<]+)', text, re.I)
    obs_match = re.search(r'Observação interna:\s*(.+?)(?=\n\s*(?:Fontes usadas|Chamados semelhantes|Especialista acionado|CORTEX-AI-NOTE|$))', text, re.I | re.S)
    sources_match = re.search(r'Fontes usadas:\s*([^\n<]+)', text, re.I)

    similar_tickets = []
    for m in re.finditer(r'#(\d{4,9})\s*[—\-]\s*([^\n(]+?)(?:\s*\((?:score\s*)?([0-9.]+)\))?(?=\n|$)', text):
        similar_tickets.append({
            'ticket_id': int(m.group(1)),
            'subject': m.group(2).strip(),
            'score': float(m.group(3)) if m.group(3) else None,
        })

    sources_raw = sources_match.group(1).strip() if sources_match else ''
    sources_list = [s.strip() for s in re.split(r'[,;]', sources_raw) if s.strip() and s.strip().lower() not in ('nenhuma', 'none', '-')]
    for st in similar_tickets:
        label = f"#{st['ticket_id']}"
        if label not in sources_list:
            sources_list.append(label)

    # Extract clean suggested reply text (between "Sugestão de resposta ao cliente:" and internal footers)
    reply_match = re.search(
        r'Sugestão de resposta(?: ao cliente)?:\s*\n*(.+?)(?=\n\s*(?:Observação interna:|Fontes usadas:|Chamados semelhantes|Especialista acionado:|Decisão interna:)|\Z)',
        text, re.I | re.S
    )
    if reply_match:
        clean_reply = reply_match.group(1).strip()
    else:
        cleaned = re.sub(r'^.*?CORTEX-AI-NOTE:[a-z0-9:_-]+\s*', '', text, flags=re.I | re.S)
        cleaned = re.sub(r'^CORTEX\s*[—\-]\s*Apoio automático ao atendimento\s*', '', cleaned, flags=re.I)
        cleaned = re.sub(r'Especialista acionado:.*?\n', '', cleaned, flags=re.I)
        cleaned = re.sub(r'Decisão interna:.*?\n', '', cleaned, flags=re.I)
        cleaned = re.sub(r'\n\s*(?:Observação interna|Fontes usadas|Chamados semelhantes).*$', '', cleaned, flags=re.I | re.S)
        clean_reply = cleaned.strip() or text

    decision = decision_match.group(1).strip() if decision_match else None
    confidence = None
    max_score = max((st['score'] for st in similar_tickets if st.get('score') is not None), default=None)
    if max_score is not None:
        if max_score >= 0.78:
            confidence = 'Alta'
        elif max_score >= 0.55:
            confidence = 'Média'
        else:
            confidence = 'Baixa'
    elif decision:
        dl = decision.lower()
        if dl in ('respond', 'direct_reply', 'auto_reply') and sources_list:
            confidence = 'Alta'
        elif dl in ('respond', 'direct_reply', 'partial', 'suggest'):
            confidence = 'Média'
        elif dl in ('Clarify', 'clarify', 'escalate', 'blocked'):
            confidence = 'Baixa'
    elif sources_list:
        confidence = 'Alta' if len(sources_list) >= 2 else 'Média'

    return {
        'clean_reply': clean_reply,
        'specialist': specialist_match.group(1).strip() if specialist_match else None,
        'internal_decision': decision,
        'internal_observation': obs_match.group(1).strip() if obs_match else None,
        'sources': sources_list,
        'sources_count': len(sources_list),
        'confidence': confidence,
        'similar_tickets': similar_tickets,
    }


def _sanitize_rich_html(raw_html):
    """Sanitiza HTML de notas ou do editor rico preservando formatação segura (negrito, listas, links, imagens)."""
    if not raw_html:
        return ''
    cleaned = str(raw_html)
    cleaned = re.sub(r'<\s*(script|style|iframe|object|embed|form|input|button|meta|link)\b[^>]*>.*?<\s*/\s*\1\s*>', '', cleaned, flags=re.I | re.S)
    cleaned = re.sub(r'<\s*(script|style|iframe|object|embed|form|input|button|meta|link)\b[^>]*/?\s*>', '', cleaned, flags=re.I)
    cleaned = re.sub(r'\son[a-z]+\s*=\s*(?:"[^"]*"|\'[^\']*\'|[^\s>]+)', '', cleaned, flags=re.I)
    cleaned = re.sub(r'(href|src)\s*=\s*(["\']?)\s*javascript:[^"\'>\s]*\2', r'\1="#"', cleaned, flags=re.I)
    return sanitize(cleaned).strip()


def _status_label(status, base_status):
    raw = str(status or base_status or '').strip()
    if not raw:
        return 'Fechado'
    mapping = {
        'closed': 'Fechado',
        'resolved': 'Resolvido',
        'inattendance': 'Em atendimento',
        'in attendance': 'Em atendimento',
        'new': 'Novo',
        'stopped': 'Parado',
        'canceled': 'Cancelado',
        'cancelled': 'Cancelado',
    }
    return mapping.get(raw.lower(), raw)


def ticket_answers(ticket_id):
    ensure_schema()
    with connect() as conn:
        ticket, actions = load_ticket(conn, ticket_id)
    from movidesk_note_worker import plain, automatic_receipt_action
    ordered = sorted(actions, key=lambda a: (str(a.get('createdDate') or ''), int(a.get('id') or 0)))
    questions, answers, latest_question = [], [], None
    for action in ordered:
        description = str(action.get('description') or '')
        if action.get('id') is None or action.get('isDeleted') or automatic_receipt_action(action):
            continue
        text = sanitize(plain(description)).strip()
        if 'CORTEX-AI-NOTE:' in description:
            parsed = _parse_cortex_note(description)
            answers.append({
                'action_id': str(action['id']),
                'text': text,
                'clean_reply': parsed['clean_reply'],
                'created_at': action.get('createdDate'),
                'suggested_question_action_id': latest_question,
                'sources': parsed['sources'],
                'sources_count': parsed['sources_count'],
                'confidence': parsed['confidence'],
                'specialist': parsed['specialist'],
                'internal_decision': parsed['internal_decision'],
                'internal_observation': parsed['internal_observation'],
                'similar_tickets': parsed['similar_tickets'],
            })
        elif not is_agent(action) and str(action.get('type')) == '2' and len(text) >= 5:
            latest_question = str(action['id'])
            creator = action.get('createdBy') or {}
            author_name = sanitize(creator.get('businessName') or creator.get('userName') or creator.get('email') or 'Cliente').strip()
            questions.append({
                'action_id': latest_question,
                'text': text[:10000],
                'html': _sanitize_rich_html(description)[:25000],
                'created_at': action.get('createdDate'),
                'author': author_name,
            })

    subject = sanitize(ticket.get('subject')).strip()
    status_display = _status_label(ticket.get('status'), ticket.get('baseStatus'))
    created_at = ticket.get('createdDate') or (ordered[0].get('createdDate') if ordered else None)
    latest_ans = answers[-1] if answers else None
    latest_q = questions[-1] if questions else None

    ai_understanding = None
    if latest_ans and latest_ans.get('internal_observation'):
        ai_understanding = latest_ans['internal_observation']
    elif latest_q or subject:
        q_preview = (latest_q['text'] if latest_q else '')
        first_sentence = re.split(r'(?<=[.!?])\s+|\n+', q_preview.strip())[0] if q_preview else ''
        if subject and first_sentence and norm(subject) not in norm(first_sentence):
            ai_understanding = f"Solicitação referente a \"{subject}\": {first_sentence[:280]}"
        elif first_sentence:
            ai_understanding = f"O cliente solicita apoio técnico referente a: {first_sentence[:280]}"
        elif subject:
            ai_understanding = f"Atendimento analisado com foco no assunto: {subject}."

    additional_context = []
    if subject:
        additional_context.append({'label': 'Assunto do chamado', 'value': subject})
    if latest_ans and latest_ans.get('specialist'):
        additional_context.append({'label': 'Especialista acionado', 'value': latest_ans['specialist']})
    if latest_ans and latest_ans.get('internal_decision'):
        additional_context.append({'label': 'Decisão de roteamento', 'value': latest_ans['internal_decision']})
    if latest_ans and latest_ans.get('sources'):
        additional_context.append({'label': 'Fontes consultadas', 'value': ', '.join(latest_ans['sources'])})

    return {
        'ticket': ticket_id,
        'subject': subject,
        'status': status_display,
        'base_status': ticket.get('baseStatus') or ticket.get('status') or 'Closed',
        'created_at': created_at,
        'movidesk_url': f'https://alternativa.movidesk.com/Ticket/Edit/{ticket_id}',
        'ai_understanding': ai_understanding,
        'additional_context': additional_context,
        'questions': questions[-30:],
        'answers': answers[-30:],
    }


def ticket_technician_answers(ticket_id, allow_internal=True, refresh_live=False):
    """Notas treináveis e pareamento provável: pergunta do cliente -> resposta humana seguinte."""
    ensure_schema()
    if refresh_live:
        try:
            sync_ticket_from_movidesk(ticket_id)
        except Exception:
            pass
    with connect() as conn:
        try:
            ticket, actions = load_ticket(conn, ticket_id)
        except LookupError:
            sync_ticket_from_movidesk(ticket_id)
            ticket, actions = load_ticket(conn, ticket_id)
    from movidesk_note_worker import plain, automatic_receipt_action
    ordered = sorted(actions, key=lambda a: (str(a.get('createdDate') or ''), int(a.get('id') or 0)))
    questions, answers, latest_question = [], [], None
    auto_event_re = re.compile(
        r'(?:recebemos\s+a?\s*sua\s+solicita[cç][aã]o|seu\s+chamado\s+foi\s+recebido|'
        r'encerrad[oa]\s+automaticamente\s+por\s+inatividade|evento\s+autom[aá]tico\s+do\s+sistema)',
        re.I
    )
    for action in ordered:
        description = str(action.get('description') or '')
        if (action.get('id') is None or action.get('isDeleted') or
                'CORTEX-AI-NOTE:' in description or automatic_receipt_action(action)):
            continue
        text = sanitize(plain(description)).strip()
        if auto_event_re.search(text):
            continue
        action_type = str(action.get('type') or '2')
        is_public = action_type == '2'
        visibility = 'public' if is_public else 'internal'
        creator = action.get('createdBy') or {}
        author_name = sanitize(
            creator.get('businessName') or creator.get('userName') or creator.get('email') or ('Técnico' if is_agent(action) else 'Cliente')
        ).strip()
        if is_agent(action) and len(text) >= 20:
            if not is_public and not allow_internal:
                continue
            answers.append({
                'action_id': str(action['id']),
                'text': text[:10000],
                'html': _sanitize_rich_html(description)[:25000],
                'created_at': action.get('createdDate'),
                'suggested_question_action_id': latest_question,
                'author': author_name,
                'type': int(action_type) if action_type.isdigit() else 2,
                'is_public': is_public,
                'visibility': visibility,
            })
        elif not is_agent(action) and is_public and len(text) >= 5:
            latest_question = str(action['id'])
            questions.append({
                'action_id': latest_question,
                'text': text[:10000],
                'html': _sanitize_rich_html(description)[:25000],
                'created_at': action.get('createdDate'),
                'author': author_name,
            })

    # Prioriza a última resposta pública de técnico; se não houver pública, usa a última interna
    public_answers = [a for a in answers if a.get('is_public')]
    recommended = public_answers[-1] if public_answers else (answers[-1] if answers else None)

    return {
        'ticket': ticket_id,
        'subject': sanitize(ticket.get('subject')).strip(),
        'status': _status_label(ticket.get('status'), ticket.get('baseStatus')),
        'questions': questions[-30:],
        'answers': answers[-30:],
        'recommended_action_id': recommended['action_id'] if recommended else None,
    }


def save_answer_score(ticket_id, action_id, score, actor, module_key=None, comment=None):
    """Salva de maneira persistente a avaliação de 0 a 10 da resposta da IA sem publicar conhecimento automaticamente."""
    if not isinstance(score, int) or isinstance(score, bool) or score < 0 or score > 10:
        raise ValueError('A nota da resposta deve ser um número inteiro de 0 a 10.')
    ensure_schema()
    with connect() as conn:
        _, actions = load_ticket(conn, ticket_id)
        selected = select_answer(actions, action_id)
        existing = conn.execute(
            'SELECT * FROM cortex_kb.answer_reviews WHERE ticket_external_id=%s AND action_id=%s AND actor=%s',
            (ticket_id, str(action_id), actor)
        ).fetchone()
        valid_modules = {m['key'] for m in taxonomy_options()}
        resolved_module = (
            module_key if module_key in valid_modules and module_key != 'unclassified'
            else (existing['module_key'] if existing and existing.get('module_key') in valid_modules else 'geral')
        )
        derived_rating = 1 if score >= 7 else -1
        resolved_comment = sanitize(comment) if comment is not None else (existing['comment'] if existing else '')
        row = conn.execute(
            """INSERT INTO cortex_kb.answer_reviews
                (ticket_external_id, action_id, answer_text, rating, score, module_key, comment, actor, is_draft)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, true)
               ON CONFLICT (ticket_external_id, action_id, actor)
               DO UPDATE SET
                   score = excluded.score,
                   rating = excluded.rating,
                   module_key = excluded.module_key,
                   comment = excluded.comment,
                   updated_at = now()
               RETURNING id, score, rating, module_key, updated_at""",
            (
                ticket_id, str(action_id), sanitize(selected['description']),
                derived_rating, score, resolved_module, resolved_comment, actor,
            )
        ).fetchone()
        audit(
            conn, 'answer_review', row['id'], 'scored', actor,
            f'Nota {score}/10 atribuída à resposta da IA',
            before=dict(existing) if existing else None,
            after={'score': score, 'rating': derived_rating, 'ticket': ticket_id, 'action': str(action_id), 'module': row['module_key']}
        )
    return {
        'id': row['id'],
        'ticket_id': ticket_id,
        'action_id': str(action_id),
        'score': row['score'],
        'rating': row['rating'],
        'module_key': row['module_key'],
        'actor': actor,
        'updated_at': row['updated_at'],
    }


def save_review_draft(ticket_id, action_id, actor, score=None, module_key=None, subtopic_key=None,
                      keywords=None, proposed_title=None, proposed_answer=None, proposed_procedure=None,
                      validated_html=None, comment=None, example_ticket_id=None,
                      question_action_id=None, source_answer_action_id=None):
    """Salva o rascunho da validação do técnico no banco de dados para continuidade posterior."""
    if score is not None and (not isinstance(score, int) or isinstance(score, bool) or score < 0 or score > 10):
        raise ValueError('A nota da resposta deve ser um número inteiro de 0 a 10.')
    ensure_schema()
    with connect() as conn:
        _, actions = load_ticket(conn, ticket_id)
        selected = select_answer(actions, action_id)
        existing = conn.execute(
            'SELECT * FROM cortex_kb.answer_reviews WHERE ticket_external_id=%s AND action_id=%s AND actor=%s',
            (ticket_id, str(action_id), actor)
        ).fetchone()
        valid_modules = {m['key'] for m in taxonomy_options()}
        resolved_module = (
            module_key if module_key in valid_modules and module_key != 'unclassified'
            else (existing['module_key'] if existing and existing.get('module_key') in valid_modules else 'geral')
        )
        resolved_score = score if score is not None else (existing.get('score') if existing else None)
        derived_rating = (1 if resolved_score >= 7 else -1) if resolved_score is not None else (existing['rating'] if existing else -1)
        kw_list = _as_list(keywords if keywords is not None else (existing.get('keywords') if existing else []))
        clean_html = _sanitize_rich_html(validated_html) if validated_html is not None else (existing.get('validated_html') if existing else None)

        row = conn.execute(
            """INSERT INTO cortex_kb.answer_reviews
                (ticket_external_id, action_id, answer_text, rating, score, module_key, subtopic_key, keywords,
                 comment, actor, example_ticket_external_id, proposed_title, proposed_answer, proposed_procedure,
                 validated_html, question_action_id, source_answer_action_id, is_draft)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, true)
               ON CONFLICT (ticket_external_id, action_id, actor)
               DO UPDATE SET
                   rating = excluded.rating,
                   score = excluded.score,
                   module_key = excluded.module_key,
                   subtopic_key = excluded.subtopic_key,
                   keywords = excluded.keywords,
                   comment = excluded.comment,
                   example_ticket_external_id = excluded.example_ticket_external_id,
                   proposed_title = excluded.proposed_title,
                   proposed_answer = excluded.proposed_answer,
                   proposed_procedure = excluded.proposed_procedure,
                   validated_html = excluded.validated_html,
                   question_action_id = excluded.question_action_id,
                   source_answer_action_id = excluded.source_answer_action_id,
                   is_draft = true,
                   updated_at = now()
               RETURNING id, updated_at""",
            (
                ticket_id, str(action_id), sanitize(selected['description']),
                derived_rating, resolved_score, resolved_module,
                sanitize(subtopic_key).strip() or None, kw_list,
                sanitize(comment), actor,
                example_ticket_id or ticket_id,
                sanitize(proposed_title).strip() or None,
                sanitize(proposed_answer).strip() or None,
                sanitize(proposed_procedure).strip() or None,
                clean_html,
                str(question_action_id) if question_action_id else None,
                str(source_answer_action_id) if source_answer_action_id else None,
            )
        ).fetchone()
        audit(
            conn, 'answer_review', row['id'], 'draft_saved', actor,
            'Rascunho de curadoria salvo pelo técnico',
            before=dict(existing) if existing else None,
            after={
                'ticket': ticket_id, 'action': str(action_id), 'score': resolved_score,
                'module': resolved_module, 'subtopic': subtopic_key,
                'source_answer_action': source_answer_action_id,
            }
        )
    return {'id': row['id'], 'ticket_id': ticket_id, 'action_id': str(action_id), 'actor': actor, 'is_draft': True, 'updated_at': row['updated_at']}


def ticket_saved_reviews(ticket_id, actor=None):
    """Retorna avaliações e rascunhos salvos para o ticket."""
    ensure_schema()
    with connect() as conn:
        if actor:
            rows = conn.execute(
                """SELECT id, ticket_external_id, action_id, rating, score, module_key, subtopic_key, keywords,
                          comment, actor, example_ticket_external_id, proposed_title, proposed_answer,
                          proposed_procedure, validated_html, proposed_images, question_action_id,
                          source_answer_action_id, candidate_id, is_draft, created_at, updated_at
                   FROM cortex_kb.answer_reviews
                   WHERE ticket_external_id=%s
                   ORDER BY (actor=%s) DESC, updated_at DESC""",
                (ticket_id, actor)
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT id, ticket_external_id, action_id, rating, score, module_key, subtopic_key, keywords,
                          comment, actor, example_ticket_external_id, proposed_title, proposed_answer,
                          proposed_procedure, validated_html, proposed_images, question_action_id,
                          source_answer_action_id, candidate_id, is_draft, created_at, updated_at
                   FROM cortex_kb.answer_reviews
                   WHERE ticket_external_id=%s
                   ORDER BY updated_at DESC""",
                (ticket_id,)
            ).fetchall()
    return [dict(r) for r in rows]


TRAINING_MEDIA = Path(__file__).parent / 'output' / 'training_media'


def _save_proposal_images(images, procedure):
    if len(images or []) > 5:
        raise ValueError('Envie no máximo 5 prints por proposta.')
    steps = {int(n) for n in re.findall(r'^\s*(\d+)\.\s', procedure or '', re.M)}
    saved, names = [], set()
    TRAINING_MEDIA.mkdir(parents=True, exist_ok=True)
    for entry in images or []:
        placement = entry.get('placement') or 'procedure'
        step = entry.get('after_step') if placement == 'procedure' else None
        name = sanitize(entry.get('name')).strip()
        caption = sanitize(entry.get('caption')).strip()
        if placement not in ('answer', 'procedure'):
            raise ValueError('Escolha se a imagem aparece na resposta final ou no passo a passo.')
        if len(name) < 3 or len(name) > 120:
            raise ValueError('Dê um nome de 3 a 120 caracteres para cada imagem.')
        normalized_name = norm(name)
        if normalized_name in names:
            raise ValueError('Use um nome diferente para cada imagem.')
        names.add(normalized_name)
        if len(caption) < 3 or (placement == 'procedure' and step not in steps):
            raise ValueError('Cada print precisa de legenda e, no procedimento, deve estar ligado a um passo numerado existente.')
        match = re.fullmatch(r'data:image/(png|jpeg|webp);base64,([A-Za-z0-9+/=\r\n]+)',
                             str(entry.get('data_url') or ''), re.I)
        if not match:
            raise ValueError('Use apenas imagens PNG, JPEG ou WebP.')
        try:
            raw = base64.b64decode(match.group(2), validate=True)
        except ValueError as exc:
            raise ValueError('Um dos prints está corrompido.') from exc
        if not raw or len(raw) > 5 * 1024 * 1024:
            raise ValueError('Cada print deve ter no máximo 5 MB.')
        try:
            with Image.open(io.BytesIO(raw)) as source:
                if source.width * source.height > 25_000_000:
                    raise ValueError('A resolução do print é muito grande.')
                source.load()
                image = source.convert('RGBA' if source.mode in ('RGBA', 'LA') else 'RGB')
                image.thumbnail((2400, 2400))
                file_name = uuid.uuid4().hex + '.png'
                image.save(TRAINING_MEDIA / file_name, 'PNG', optimize=True)
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
            raise ValueError('Um dos arquivos não é uma imagem válida.') from exc
        saved.append({'file': file_name, 'name': name[:120],
                      'caption': caption[:300], 'placement': placement, 'after_step': step})
    return saved


def review_answer(ticket_id, action_id, rating, module_key, comment, actor, example_ticket_id=None,
                  proposed_title=None, proposed_answer=None, proposed_procedure=None, proposed_images=None,
                  question_action_id=None, source_answer_action_id=None, *,
                  subtopic_key=None, keywords=None, score=None, validated_html=None):
    if rating not in (-1, 1):
        raise ValueError('Avaliação inválida.')
    if score is not None and (not isinstance(score, int) or isinstance(score, bool) or score < 0 or score > 10):
        raise ValueError('A nota da resposta deve ser um número inteiro de 0 a 10.')
    if module_key == 'unclassified' or module_key not in {m['key'] for m in taxonomy_options()}:
        raise ValueError('Classifique o módulo da resposta.')
    if rating == -1 and (not isinstance(example_ticket_id, int) or isinstance(example_ticket_id, bool)
                         or example_ticket_id <= 0):
        raise ValueError('Informe um ticket-exemplo válido para a resposta que precisa melhorar.')
    proposal = None
    if rating == -1:
        proposed_title = sanitize(proposed_title).strip()
        proposed_answer = sanitize(proposed_answer).strip()
        proposed_procedure = sanitize(proposed_procedure).strip()
        if len(proposed_title) < 5 or len(proposed_answer) < 20:
            raise ValueError('Preencha o título e a resposta pronta ao cliente.')
        if len(proposed_procedure) < 20 or not re.search(r'^\s*1\.\s', proposed_procedure, re.M):
            raise ValueError('Escreva o passo a passo numerado, começando por “1.”.')
        question_text = None
        if question_action_id or source_answer_action_id:
            if source_answer_action_id and not question_action_id:
                raise ValueError('Selecione a nota da pergunta relacionada à resposta.')
            notes = ticket_technician_answers(example_ticket_id)
            question = next((q for q in notes['questions'] if q['action_id'] == str(question_action_id)), None)
            source_answer = (next((a for a in notes['answers'] if a['action_id'] == str(source_answer_action_id)), None)
                             if source_answer_action_id else None)
            if not question or (source_answer_action_id and not source_answer):
                raise ValueError('A pergunta ou resposta selecionada não pertence ao ticket indicado ou ainda não foi sincronizada.')
            question_text = question['text']
        saved_images = _save_proposal_images(proposed_images or [], proposed_procedure)
        proposal = {'title': proposed_title, 'answer': proposed_answer,
                    'procedure': proposed_procedure, 'images': saved_images, 'question': question_text,
                    'question_action_id': str(question_action_id) if question_action_id else None,
                    'answer_action_id': str(source_answer_action_id) if source_answer_action_id else None}
        if subtopic_key is not None:
            proposal['subtopic_key'] = sanitize(subtopic_key).strip()
        if keywords is not None:
            proposal['keywords'] = _as_list(keywords)
        if validated_html is not None:
            proposal['validated_html'] = _sanitize_rich_html(validated_html)
        if score is not None:
            proposal['score'] = score
    with connect() as conn:
        _, actions = load_ticket(conn, ticket_id)
        selected = select_answer(actions, action_id)
    # A proposta do técnico sempre fica isolada na fila. Somente a decisão
    # administrativa posterior pode publicá-la no índice usado pelo CORTEX.
    candidate = create_candidate(example_ticket_id, actor, True, None, module_key, proposal) if rating == -1 else None
    clean_subtopic = sanitize(subtopic_key).strip() if subtopic_key else None
    kw_list = _as_list(keywords) if keywords is not None else []
    clean_html = _sanitize_rich_html(validated_html) if validated_html else None
    with connect() as conn:
        row = conn.execute("""INSERT INTO cortex_kb.answer_reviews
            (ticket_external_id,action_id,answer_text,rating,score,module_key,subtopic_key,keywords,comment,actor,
             example_ticket_external_id,proposed_title,proposed_answer,proposed_procedure,validated_html,proposed_images,
             question_action_id,source_answer_action_id,candidate_id,is_draft)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,false) ON CONFLICT(ticket_external_id,action_id,actor)
            DO UPDATE SET rating=excluded.rating,score=COALESCE(excluded.score,answer_reviews.score),
            module_key=excluded.module_key,subtopic_key=COALESCE(excluded.subtopic_key,answer_reviews.subtopic_key),
            keywords=CASE WHEN array_length(excluded.keywords,1) IS NOT NULL THEN excluded.keywords ELSE answer_reviews.keywords END,
            comment=excluded.comment,
            example_ticket_external_id=excluded.example_ticket_external_id,
            proposed_title=excluded.proposed_title,proposed_answer=excluded.proposed_answer,
            proposed_procedure=excluded.proposed_procedure,
            validated_html=COALESCE(excluded.validated_html,answer_reviews.validated_html),
            proposed_images=excluded.proposed_images,
            question_action_id=excluded.question_action_id,source_answer_action_id=excluded.source_answer_action_id,
            candidate_id=COALESCE(excluded.candidate_id,answer_reviews.candidate_id),
            is_draft=false,updated_at=now() RETURNING id""",
            (ticket_id,str(action_id),sanitize(selected['description']),rating,score,module_key,clean_subtopic,kw_list,sanitize(comment),actor,
             example_ticket_id if rating == -1 else None,
             proposed_title if rating == -1 else None, proposed_answer if rating == -1 else None,
             proposed_procedure if rating == -1 else None, clean_html if rating == -1 else None,
             Jsonb(proposal['images'] if proposal else []),
             str(question_action_id) if rating == -1 and question_action_id else None,
             str(source_answer_action_id) if rating == -1 and source_answer_action_id else None,
             candidate['id'] if candidate else None)).fetchone()
        audit(conn,'answer_review',row['id'],'rated',actor,sanitize(comment),
              after={'rating':rating,'score':score,'ticket':ticket_id,'action':str(action_id),'module':module_key,
                     'subtopic':clean_subtopic,'keywords':kw_list,
                     'example_ticket':example_ticket_id if rating == -1 else None,
                     'question_action':question_action_id if rating == -1 else None,
                     'source_answer_action':source_answer_action_id if rating == -1 else None})
    return {'id':row['id'],'candidate_id':candidate['id'] if candidate else None,
            'example_ticket_id':example_ticket_id if rating == -1 else None}


def answer_reviews(limit=100):
    ensure_schema()
    with connect() as conn:
        return conn.execute("""SELECT id,ticket_external_id,action_id,rating,score,module_key,subtopic_key,keywords,comment,actor,
            example_ticket_external_id,proposed_title,proposed_answer,proposed_procedure,validated_html,proposed_images,
            question_action_id,source_answer_action_id,candidate_id,is_draft,created_at,updated_at FROM cortex_kb.answer_reviews
            ORDER BY updated_at DESC LIMIT %s""",(limit,)).fetchall()


def procedure_images():
    labels = {'state-registration-delivery':'NF-e — campo RG/IE', 'state-registration-ccc-query':'CCC — consulta por CNPJ',
        'state-registration-ccc-result':'CCC — resultado da inscrição estadual',
        'product-relation':'Produto — Relação Site', 'payment-selection':'Forma de pagamento — seleção',
        'payment-relation':'Forma de pagamento — Relação Site', 'duplicate-nfe-series-number':'NF-e — série e número',
        'ocorrencias-1':'Ocorrências — tela 1','ocorrencias-2':'Ocorrências — tela 2','ocorrencias-3':'Ocorrências — tela 3'}
    root = Path(__file__).parent / 'static' / 'reply-media'
    reviewed = [{'file': p.name, 'label': labels.get(p.stem,p.stem), 'url':'/procedure-images/'+p.name}
                for p in sorted(root.glob('*.png'))]
    uploaded = [{'file': p.name, 'label': 'Print enviado para curadoria', 'url':'/training-media/'+p.name}
                for p in sorted(TRAINING_MEDIA.glob('*.png'))]
    return reviewed + uploaded
