"""Review-only staging import. Publication requires an explicit admin decision.

No migration, automatic approval or startup publication. The staged document
hash is checked again at approval; publication uses the existing version/audit
transaction and has an idempotency key scoped to the source card revision.
"""
from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

import knowledge_training as kt
import knowledge_curation as taxonomy
from config import PROJECT_ROOT

STAGING = Path(PROJECT_ROOT) / 'output/master_screens/staging_cards.json'
REVIEWED = Path(PROJECT_ROOT) / 'output/support_audit/reviewed_procedures.json'


def checksum(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def previews():
    result = []
    seen_cards = set()
    superseded = {'master:anymarket:transportadora-relacao-site': 'reviewed-anymarket-carrier-relation-v1'}
    if REVIEWED.is_file():
        seen_cards.update(c.get('card_id') for c in json.loads(REVIEWED.read_text(encoding='utf-8')))
    for source in (STAGING, REVIEWED):
        if not source.is_file():
            continue
        for card in json.loads(source.read_text(encoding='utf-8')):
            if source == STAGING and superseded.get(card.get('card_id')) in seen_cards:
                continue
            module = card.get('module_key') or taxonomy.classify(card['title'], [card.get('module', '')])
            procedure = '\n'.join(f"{s['number']}. {s['instruction']}" for s in card.get('steps', []))
            content = {
                'title': card['title'], 'module_key': module, 'subtopic_key': 'general',
                'symptom': card.get('symptom') or '; '.join(card.get('intents', [])) or card['title'],
                'error_messages': card.get('error_messages', []),
                'root_cause': card.get('root_cause') or 'Causa deve ser confirmada pelo administrador na revisão.',
                'procedure': card.get('procedure') or procedure,
                'customer_answer': card.get('customer_answer'),
                'prerequisites': card.get('prerequisites'), 'expected_result': card.get('expected_result'),
                'applicability': card.get('applicability') or {}, 'keywords': card.get('keywords') or [],
                'images': [i for i in card.get('images', []) if i.get('file')],
            }
            result.append({'id': checksum([str(source.relative_to(PROJECT_ROOT)), card['card_id']])[:24],
                           'sha256': checksum(card), 'card_id': card['card_id'],
                           'content': content, 'source_manual': card.get('source_manual'),
                           'source_images': [i for i in card.get('images', []) if i.get('path')],
                           'evidence': card.get('evidence') or [],
                           'status': 'preview', 'auto_publish': False})
    return result


def detail(card_id):
    card = next((c for c in previews() if c['id'] == card_id), None)
    if not card:
        raise LookupError('Prévia não encontrada.')
    return card


def image_path(card_id, index):
    card = detail(card_id)
    if index < 0 or index >= len(card['source_images']):
        raise LookupError('Imagem da prévia não encontrada.')
    image = card['source_images'][index]
    root = (Path(PROJECT_ROOT) / 'assets/screens').resolve()
    path = (Path(PROJECT_ROOT) / image['path']).resolve()
    if not path.is_relative_to(root) or not path.is_file() or path.suffix.lower() != '.png':
        raise ValueError('Imagem de staging indisponível ou fora da pasta permitida.')
    if path.stat().st_size > 5 * 1024 * 1024:
        raise ValueError('Imagem de staging maior que 5 MB.')
    if image.get('sha256') and hashlib.sha256(path.read_bytes()).hexdigest() != image['sha256']:
        raise RuntimeError('A imagem mudou. Reconstrua a prévia antes de aprovar.')
    return path


def approve(card_id, expected_sha256, content, actor, reason, target_item_id=None):
    card = detail(card_id)
    if card['sha256'] != expected_sha256:
        raise RuntimeError('O card mudou. Reabra a prévia antes de aprovar.')
    if len(reason.strip()) < 5 or not actor.strip():
        raise ValueError('Informe responsável e motivo da aprovação.')
    # Only paths from the fixed local staging package, confined to assets/screens.
    images = []
    for index, image in enumerate(card['source_images']):
        path = image_path(card_id, index)
        images.append({**image, 'placement': 'procedure',
                       'data_url': 'data:image/png;base64,' + base64.b64encode(path.read_bytes()).decode()})
    content = dict(content)
    content.setdefault('error_messages', [])
    content['images'] = list(content.get('images') or []) + kt._save_proposal_images(images, content['procedure'])
    content = kt._validate_content(content)
    kt.ensure_schema()
    key = 'staging:' + card_id + ':' + expected_sha256
    with kt.connect() as conn:
        conn.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', (key,))
        existing = conn.execute("SELECT item_id FROM cortex_kb.sources WHERE source_type='manual' AND external_ref=%s", (key,)).fetchone()
        if existing:
            return {'id': existing['item_id'], 'already_imported': True}
        if target_item_id:
            if not conn.execute('SELECT id FROM cortex_kb.items WHERE id=%s FOR UPDATE', (target_item_id,)).fetchone():
                raise LookupError('Procedimento de destino não encontrado.')
            item_id = target_item_id
        else:
            related = kt.find_related(conn, content, content['module_key'])
            if any(r['relation'] in ('duplicate', 'conflict') for r in related):
                raise RuntimeError('Há procedimento duplicado ou conflitante. Revise a base e informe o procedimento de destino.')
            item_id = conn.execute('''INSERT INTO cortex_kb.items(title,module_key,subtopic_key,created_by)
                VALUES (%s,%s,%s,%s) RETURNING id''',
                (content['title'], content['module_key'], content['subtopic_key'], actor)).fetchone()['id']
        version = kt._new_version(conn, item_id, content, actor, reason)
        conn.execute('''INSERT INTO cortex_kb.sources(item_id,version_id,source_type,external_ref,note)
            VALUES (%s,%s,'manual',%s,%s)''', (item_id, version['id'], key, reason))
        for ref in card['evidence']:
            if ref.get('type') not in ('ticket', 'manual', 'kb_article', 'analyst'):
                continue
            conn.execute('''INSERT INTO cortex_kb.sources(item_id,version_id,source_type,external_ref,note)
                VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING''',
                (item_id, version['id'], ref['type'], str(ref['ref']), reason))
        if card['source_manual']:
            conn.execute('''INSERT INTO cortex_kb.sources(item_id,version_id,source_type,external_ref,note)
                VALUES (%s,%s,'manual',%s,%s) ON CONFLICT DO NOTHING''',
                (item_id, version['id'], card['source_manual'], reason))
        kt._publish(conn, item_id)  # Text retrieval works immediately; no external embedding call.
        kt.audit(conn, 'item', item_id, 'staging:approved', actor, reason,
                 before={'card_id': card['card_id'], 'sha256': expected_sha256},
                 after={'version_no': version['version_no'], 'content': content})
    return {'id': item_id, 'version': version['version_no'], 'already_imported': False}


def prepare_reviewed():
    """Export existing repository-reviewed cases to a private preview, not the DB."""
    import mapped_replies
    questions = [
        'AnyMarket transportadora Carrier sem relacionamento',
        'Forma de pagamento não integrada',
        'Produto "123" (código do integrador) "Produto" (descrição) não integrado',
        'Rejeição 539 Duplicidade de NF-e com diferença na Chave de Acesso',
        'IE do destinatário inválida',
    ]
    cards = []
    reviewed_context = {
        'reviewed-anymarket-carrier-relation-v1': (
            'Ausência do relacionamento entre a transportadora do Master e o Carrier retornado pela AnyMarket.',
            'Identificar a transportadora correta e o Carrier da ocorrência do próprio pedido.',
            'Pedido integrado sem a mensagem de transportadora não relacionada.'),
        'reviewed-payment-relation-v1': (
            'A forma de pagamento recebida do integrador não possui a relação correspondente no Master.',
            'Confirmar a modalidade de pagamento e identificar o código ou nome externo recebido na ocorrência.',
            'Relação salva e pedido integrado sem a mensagem de forma de pagamento não integrada.'),
        'reviewed-product-relation-v1': (
            'O produto recebido no pedido não está relacionado ao cadastro correspondente no Master.',
            'Identificar produto_id e cod_estoque na ocorrência e confirmar o cadastro correto do produto.',
            'Produto relacionado e pedido integrado após novo processamento.'),
        'reviewed-duplicate-nfe-key-v1': (
            'A rejeição informa outra chave para o mesmo emitente, modelo, série e número; a situação da nota precisa de consulta oficial.',
            'Identificar série, número e chave retornada; confirmar com a contabilidade a operação e a sequência quando necessário.',
            'Situação da nota e origem da duplicidade conferidas, com tratamento definido conforme o procedimento revisado.'),
        'reviewed-state-registration-v1': (
            'A emissão foi rejeitada por IE inválida; é necessário conferir IE, CNPJ, UF e situação cadastral oficial antes do ajuste.',
            'Identificar os dados do destinatário na nota e consultar o CCC para a mesma UF, sem enviar dados completos no atendimento.',
            'Dados conferidos e corrigidos quando confirmada a divergência; nova transmissão validada ou situação cadastral encaminhada para análise.'),
    }
    for question in questions:
        source = mapped_replies.match(question)
        if not source:
            continue
        cause, prerequisites, expected = reviewed_context[source['id']]
        images=[dict(image) for image in source.get('images') or []]
        for image in images:
            image.setdefault('name', image['caption'][:120])
            image.setdefault('placement', 'procedure')
            if source['id']=='reviewed-product-relation-v1':
                image['after_step']=3
        cards.append({'card_id': source['id'], 'title': source['problem'], 'symptom': source['problem'],
                      'procedure': source['solution'], 'customer_answer': None,
                      'module_key': taxonomy.classify(source['problem'], []),
                      'images': images,
                      'evidence': [{'type': 'ticket', 'ref': c['ticket']} for c in source.get('resolved_examples', [])] +
                                  [{'type': 'analyst', 'ref': source.get('provenance') or source['id']}],
                      'root_cause': cause, 'prerequisites': prerequisites, 'expected_result': expected,
                      'applicability': {'integrations':['AnyMarket']} if source['id']=='reviewed-anymarket-carrier-relation-v1' else {},
                      'status': 'REVIEW_REQUIRED'})
    manual = Path(PROJECT_ROOT)/'Instrucoes_master/Relatorios/Produtos por Fabricante ou Fornecedor.md'
    if manual.is_file():
        text = manual.read_text(encoding='utf-8')
        procedure = text.split('## Passo a passo',1)[1].split('## Resposta padrão',1)[0].strip()
        answer = text.split('## Resposta padrão',1)[1].split('## Correção registrada',1)[0].strip()
        cards.append({'card_id':'manual:produtos-por-fabricante-fornecedor',
            'title':'Relatório de Produtos por Fabricante/Fornecedor','module_key':'products',
            'symptom':'Consultar produtos, quantidade e data de compra por fabricante ou fornecedor.',
            'root_cause':'Dúvida de navegação: selecionar a aba Fornecedor para consultar informações de compras.',
            'prerequisites':'Identificar o fabricante ou fornecedor desejado e confirmar se a consulta é sobre compras.',
            'expected_result':'Produtos do fabricante/fornecedor consultados; informações de compras pela aba Fornecedor.',
            'procedure':procedure,'customer_answer':answer,'status':'REVIEW_REQUIRED',
            'source_manual':str(manual.relative_to(PROJECT_ROOT)).replace('\\','/'),
            'evidence':[{'type':'analyst','ref':'Validação humana registrada no manual em 19/10/2026'}]})
    REVIEWED.parent.mkdir(parents=True, exist_ok=True)
    REVIEWED.write_text(json.dumps(cards, ensure_ascii=False, indent=2), encoding='utf-8')
    return len(cards)


if __name__ == '__main__':
    print(f'{prepare_reviewed()} procedimentos exportados para prévia; nenhum publicado.')
