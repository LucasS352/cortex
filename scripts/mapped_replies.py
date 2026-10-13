"""Reviewed procedures and original screenshots, independent of model wording.

Mappings require the current diagnostic message, not merely a related subject.
Media stays behind the existing Cortex login; never expose the manual library.
"""
from pathlib import Path
from urllib.parse import urlsplit
import os
import re
import unicodedata


def safe_url(value):
    value = str(value or '').strip()
    try:
        url = urlsplit(value)
        if (url.scheme in {'https', 'http'} and url.hostname and not url.username
                and not url.password and not url.query and not url.fragment
                and not re.search(r'[\s\\<>"\x00-\x1f]', value)):
            return value
    except ValueError:
        pass
    return ''


PAYMENT_REPLY = '''A mensagem indica que a forma de pagamento do pedido não está relacionada no Master. Confira o vínculo assim:

1. Acesse Cadastros → Formas de Pagamento e selecione a forma correspondente ao pagamento do pedido. Confirme se é crédito, débito ou outra modalidade; somente a bandeira Elo não define isso.
2. Com a forma selecionada, pressione Ctrl+R para abrir Relação - Site.
3. Confira o Site/Meu Site da integração do pedido. Se a relação estiver ausente, clique em Adicionar e preencha Código de fora com o código da forma de pagamento informado pelo integrador. Não use o UUID do erro como código de pagamento.
4. Confirme e salve a relação. Depois, reprocesse o pedido no sincronizador e confira se integrou sem a mensagem.

Se o código da forma de pagamento não aparecer na mensagem, envie o detalhe desse erro no sincronizador para identificarmos o valor correto antes de cadastrar a relação.'''


def match(query):
    text = ''.join(c for c in unicodedata.normalize('NFKD', query.lower())
                   if not unicodedata.combining(c))
    # Do not apply this recipe to generic missing orders or orders already
    # integrated without payment (ANY_PAGAMENTO_STATUS is a different issue).
    if not re.search(r'forma de pagamento[^\n.!?]{0,100}\bnao integrad[ao]\b', text):
        return None
    if re.search(r'ja (?:foi )?integrad|integra(?:m|dos?)? sem forma|any_pagamento_status', text):
        return None
    if re.search(r'\b(?:ja (?:fiz|cadastrei|relacionei|configurei)|nao resolveu|continua|persiste|mesmo apos|nao e (?:a )?forma)\b', text):
        return None
    return {
        'id': 'reviewed-payment-relation-v1', 'source_type': 'REVIEWED_PROCEDURE',
        'title': 'Forma de pagamento não integrada — relação pelo Ctrl+R',
        'problem': 'Forma de pagamento não integrada no sincronizador do Master',
        'solution': PAYMENT_REPLY, 'reviewed_reply': PAYMENT_REPLY,
        'applicable': True, 'answer_use': 'procedure_reference',
        'quality_flags': [], 'applicability_reasons': [],
        'reviewed_at': '2026-10-13',
        'provenance': 'Passos conferidos nos manuais Integrando formas de pagamento e transportadora e Erro de sincronizador; Ctrl+R confirmado por Lucas em 13/10/2026.',
        'resolved_examples': [{'ticket': 128116,
            'title': 'SUP - SNC - Forma de Pagamento não integrada',
            'status': 'Encerrado (consulta em 13/10/2026)', 'active': False,
            'resolution': 'Relação com a forma de pagamento realizada; pedido integrado com sucesso.'}],
        'images': [
            {'file': 'payment-selection.png', 'caption': 'Passos 1–2: selecione a forma correta e pressione Ctrl+R. A seleção do print é apenas exemplo.'},
            {'file': 'payment-relation.png', 'caption': 'Passo 3: Relação - Site e Código de fora. Site e valores do print são exemplos; use os dados da integração do pedido.'},
        ],
    }


def note_images(source):
    origin = safe_url(os.getenv('CORTEX_PUBLIC_URL', '').rstrip('/'))
    if not origin:
        return [], ['Imagens mapeadas disponíveis, mas CORTEX_PUBLIC_URL não foi configurada para gerar os links.'] if source.get('images') else []
    images, notices = [], []
    for item in source.get('images', []):
        name = item.get('file', '')
        if not re.fullmatch(r'[a-z0-9-]+\.png', name):
            continue
        if not (Path(__file__).parent / 'static' / 'reply-media' / name).is_file():
            notices.append('Imagem mapeada indisponível: ' + name)
            continue
        images.append({'url': origin + '/tech-assets/reply-media/' + name,
                       'caption': item['caption']})
    return images, notices
