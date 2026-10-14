"""Regras críticas do ambiente de treinamento (sem banco)."""
import knowledge_training as kt

GOOD = {'base_status': 'Resolved', 'quality_tier': 'GOLD', 'evidence_level': 'STRONG', 'subject': 'Rejeição 539 NF-e',
        'solution_statement': '1. Acesse o menu Fiscal > Parâmetros. 2. Altere a série da NF-e para a próxima disponível. '
                              '3. Retransmita a nota. Duplicidade de numeração corrigida.'}
ACTIONS = [{'type': 2, 'createdBy': {'profileType': 2}, 'description': 'Erro rejeição 539 ao emitir nota'},
           {'type': 2, 'createdBy': {'profileType': 1}, 'description': 'Acesse o menu Fiscal e altere a série'},
           {'type': 2, 'createdBy': {'profileType': 2}, 'description': 'Funcionou, obrigado!'}]


def ext(**over):
    base = {'title': 'Rejeição 539 duplicidade', 'symptom': 'Nota rejeitada com 539 duplicidade de NF-e',
            'error_messages': ['Rejeição 539'], 'root_cause': 'Numeração repetida', 'procedure': 'Alterar série da nota fiscal e retransmitir',
            'applicability': {}, 'module_hint': 'fiscal', 'confidence': 'HIGH', 'evidence': {}}
    base.update(over)
    return kt.parse_extraction(base)


def test_useful_ticket_is_suggested():
    score, signals, eligible = kt.usefulness(GOOD, ACTIONS)
    assert eligible and score >= 75
    assert any('confirmou' in s['label'] for s in signals)


def test_weak_resolution_is_not_suggested():
    weak = {**GOOD, 'quality_tier': '', 'evidence_level': '', 'solution_statement': 'Cliente não respondeu, encerrado por inatividade.'}
    assert not kt.usefulness(weak, [])[2]


def test_open_or_blocked_ticket_never_suggested():
    assert not kt.usefulness({**GOOD, 'base_status': 'InAttendance'}, ACTIONS)[2]
    assert kt.usefulness({**GOOD, 'hard_block_reason': 'PII'}, ACTIONS)[:1] == (0,)


def test_sanitize_removes_personal_data_and_credentials():
    text = kt.sanitize('Contato joao@cliente.com.br, CNPJ 12.345.678/0001-90, tel (11) 98765-4321, senha: abc123')
    assert 'joao@' not in text and '12.345' not in text and '98765' not in text and 'abc123' not in text


def test_parse_extraction_accepts_fenced_json_and_rejects_missing_procedure():
    parsed = kt.parse_extraction('```json\n{"title":"T","symptom":"S","procedure":"1. x","module_hint":"bogus"}\n```')
    assert parsed['module_hint'] == 'unclassified' and parsed['confidence'] == 'LOW'
    try:
        kt.parse_extraction('{"title":"T","symptom":"S"}')
        assert False
    except ValueError:
        pass


def test_hallucinated_quote_is_flagged():
    source = kt.ticket_text(GOOD, ACTIONS)
    real = ext(evidence={'procedure_quote': 'Acesse o menu Fiscal e altere a série', 'confirmation_quote': None})
    fake = ext(evidence={'procedure_quote': 'Reinstale o certificado digital A3 no servidor', 'confirmation_quote': None})
    check = lambda e: next(c['ok'] for c in kt.quality_checks(e, source) if c['key'] == 'procedure_evidence')
    assert check(real) and not check(fake)


def existing(**over):
    base = {'module_key': 'fiscal', 'symptom': 'Nota rejeitada 539 duplicidade NF-e', 'error_messages': ['Rejeição 539'],
            'procedure': 'Alterar série da nota fiscal e retransmitir', 'applicability': {}}
    base.update(over)
    return base


def test_same_procedure_is_duplicate():
    assert kt.compare(ext(), 'fiscal', existing())[0] == 'duplicate'


def test_divergent_procedure_same_context_is_conflict():
    other = ext(procedure='Inutilizar faixa de numeração na SEFAZ antes de emitir novamente')
    assert kt.compare(other, 'fiscal', existing())[0] == 'conflict'


def test_divergence_explained_by_context_is_not_conflict():
    other = ext(procedure='Inutilizar faixa de numeração na SEFAZ', applicability={'ufs': ['RS']})
    relation, reasons = kt.compare(other, 'fiscal', existing(applicability={'ufs': ['SP']}))
    assert relation == 'different_context' and 'UF' in reasons[0]


def test_other_module_is_different_context():
    assert kt.compare(ext(), 'integrations', existing())[0] == 'different_context'
