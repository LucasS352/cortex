"""Evidence contract shared by drafts, replay evaluation and internal notes.

Confidence describes the strength of the selected evidence, never a model's
probability of being right. Similarity alone cannot authorize a procedure.
"""
import re

from knowledge_access import normalize, query_plan


def evidence(text):
    value = normalize(text)
    plan = query_plan(text)
    entities = [name for name, pattern in {
        'transportadora': r'\btransportadora\b|\bcarrier\b',
        'pagamento': r'\bforma de pagamento\b',
        'produto': r'\bproduto\b|\bsku\b',
        'nota_fiscal': r'\bnf[ -]?e\b|\bnfc[ -]?e\b|\bnota fiscal\b',
    }.items() if re.search(pattern, value)]
    mapping_failure = bool(re.search(
        r'\bnao (?:esta |foi |existe |ha |possui )?(?:\w+\s+){0,3}'
        r'(?:relacionad[ao]|vinculad[ao]|associad[ao]|integrad[ao]|configurad[ao]|relacionamento|vinculo)\b|'
        r'\bnao (?:esta |foi )?integr\w+\b.{0,80}\b(?:relacionamento|relacao|vinculo|transportadora|carrier)\b|'
        r'\bsem (?:relacionamento|vinculo|de[ /-]?para)\b|'
        r'\b(?:relacao|relacionamento|vinculo)\b.{0,55}\bnao encontrad[ao]\b|'
        r'\b(?:como (?:faco|fazer) (?:para |pra )?)?(?:relacionar|vincular|fazer o? ?de[ /-]?para)\b.{0,80}\berro\b|'
        r'\berro\b.{0,80}\b(?:relacionar|vincular|fazer o? ?de[ /-]?para|relacionamento da transportadora)\b', value))
    attempts = bool(re.search(
        r'\b(?:ja (?:fiz|configurei|relacionei|testei|verifiquei|reprocessei|cadastrei)|'
        r'continua|persiste|nao resolveu|mesmo apos)\b', value))
    concrete = bool(any(plan[k] for k in ('signatures', 'codes', 'objects', 'identifiers')) or
        re.search(r'\b(?:unauthorized|forbidden|exception|access violation|timeout|bad request)\b|'
                  r'http(?:/\d\.\d)?\s*[: -]?\s*[45]\d\d', value) or
        (mapping_failure and entities))
    return {'entities': entities, 'codes': plan['codes'], 'objects': plan['objects'],
            'integrations': [i for i in ('anymarket','mercado livre','tray','skyhub','shopee','vtex') if i in value],
            'mapping_failure': mapping_failure, 'attempt_already_failed': attempts,
            'concrete_error': concrete,
            'intent': 'error' if concrete else 'howto' if plan['howto'] else 'report'}


def decide(query, sources, cases=()):
    facts = evidence(query)
    primary = sources[0] if sources else {}
    usable = bool(primary.get('applicable') and primary.get('answer_use') != 'investigation_only')
    trusted = usable and (primary.get('evidence_status') == 'human_approved_procedure' or
                          primary.get('source_type') == 'REVIEWED_PROCEDURE')
    state = 'PROCEDIMENTO_CONFIRMADO' if trusted else 'INVESTIGAR'
    reasons = []
    if primary.get('context_conflict') or any('conflict' in r for r in primary.get('quality_flags', [])):
        state, usable, trusted = 'ENCAMINHAR', False, False
        reasons.append('conflicting_evidence')
    if facts['attempt_already_failed']:
        state = 'ENCAMINHAR'
        usable, trusted = False, False
        reasons.append('attempt_failed_requires_review_of_next_step')
    if not sources:
        reasons.append('no_applicable_procedure')
    elif not usable:
        reasons.extend(primary.get('applicability_reasons') or ['primary_evidence_incomplete'])
    elif not trusted:
        reasons.append('historical_or_manual_reference_requires_confirmation')
    pending = []
    if not trusted:
        if facts['concrete_error']:
            pending.append('resultado_da_operacao_e_contexto_da_falha')
        else:
            pending.append('retorno_exato_da_operacao')
        if not facts['integrations'] and facts['mapping_failure']:
            pending.append('integrador_utilizado')
    return {'schema_version': 1, 'state': state,
            'confidence': 'ALTA' if trusted else 'MEDIA' if usable else 'BAIXA',
            'can_propose_procedure': trusted, 'reasons': reasons, 'evidence': facts,
            'procedure': {'id': primary.get('source_id') or primary.get('id'),
                          'version': (primary.get('metadata') or {}).get('version_no') or
                                     (primary.get('id') if primary.get('source_type') == 'REVIEWED_PROCEDURE' else None),
                          'approved_by': (primary.get('metadata') or {}).get('approved_by'),
                          'approved_at': (primary.get('metadata') or {}).get('approved_at') or primary.get('reviewed_at'),
                          'provenance': primary.get('provenance'),
                          'sources': (primary.get('metadata') or {}).get('source_references') or [],
                          'title': primary.get('title')} if trusted else None,
            'pending_evidence': pending,
            'related_open_tickets': [c.get('external_id') for c in cases if c.get('is_active')],
            'reference_policy': 'Only resolved, documented applicable tickets; never manufacture a reference.'}


def investigation_reply(query, decision):
    """Fail closed without asking for a log/error already supplied."""
    facts = decision['evidence']
    if decision['state'] == 'ENCAMINHAR':
        return ('A orientação precisa de revisão técnica antes de um novo ajuste. '
                'Vou considerar o retorno e as verificações já informadas. '
                'O técnico precisará confrontar esse resultado com as fontes e a configuração '
                'atual da operação para definir o próximo passo com segurança.')
    if facts['intent'] == 'howto':
        return ('Entendi a orientação de uso solicitada. Ainda falta um procedimento aprovado '
                'aplicável para indicar as etapas com segurança. A tela e a operação informadas '
                'precisam de conferência técnica; não é necessário apresentar uma mensagem de erro '
                'para essa dúvida de uso.')
    if facts['concrete_error']:
        return ('O retorno informado será usado na investigação. Ainda falta uma fonte aplicável '
                'para orientar o ajuste com segurança. Em qual operação esse retorno aparece '
                'e qual resultado você esperava? Não é necessário reenviar o erro já informado.')
    return ('Para indicar um procedimento seguro, preciso do retorno apresentado ao executar '
            'a operação. Informe a mensagem exibida e a operação em que ela aparece, '
            'sem enviar credenciais ou dados pessoais completos.')


def response_violations(reply, primary=None):
    """High precision output checks; not a complete semantic/human review."""
    flags = []
    if re.search(r'\b(?:DELETE\s+FROM|DROP\s+TABLE|TRUNCATE|UPDATE\s+\w+\s+SET|ALTER\s+TABLE|GRANT)\b', reply, re.I):
        flags.append('database_mutation')
    supported = bool(primary and primary.get('applicable') and primary.get('answer_use') != 'investigation_only'
        and (primary.get('evidence_status') == 'human_approved_procedure' or primary.get('source_type') == 'REVIEWED_PROCEDURE'))
    if not supported and re.search(
        r'ctrl\s*\+\s*\w|\b(?:acesse|abra|selecione|clique|altere|marque|desmarque)\b.{0,70}'
        r'\b(?:menu|aba|campo|botao|cadastro|parametro)\b|\w\s*[→>]\s*\w', normalize(reply)):
        flags.append('unsupported_navigation')
    return flags
