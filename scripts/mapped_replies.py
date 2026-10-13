"""Reviewed procedures and original screenshots, independent of model wording.

Mappings require the current diagnostic message, not merely a related subject.
Media stays behind the existing Cortex login; never expose the manual library.
"""
from pathlib import Path
from urllib.parse import urlsplit
import base64
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


def safe_image_data(value):
    """Allow only small PNGs produced from the reviewed local media folder."""
    value = str(value or '')
    match = re.fullmatch(r'data:image/png;base64,([A-Za-z0-9+/=]+)', value)
    if not match or len(value) > 500_000:
        return ''
    try:
        raw = base64.b64decode(match.group(1), validate=True)
    except ValueError:
        return ''
    return value if len(raw) <= 350_000 and raw.startswith(b'\x89PNG\r\n\x1a\n') else ''


PAYMENT_REPLY = '''A mensagem indica que a forma de pagamento do pedido não está relacionada no Master. Confira o vínculo assim:

1. Acesse Cadastros → Formas de Pagamento e selecione a forma correspondente ao pagamento do pedido. Confirme se é crédito, débito ou outra modalidade; somente a bandeira Elo não define isso.
2. Com a forma selecionada, pressione Ctrl+R para abrir Relação - Site.
3. Confira o Site/Meu Site da integração do pedido. Se a relação estiver ausente, clique em Adicionar e preencha Código de fora com o código da forma de pagamento informado pelo integrador. Não use o UUID do erro como código de pagamento.
4. Confirme e salve a relação. Depois, reprocesse o pedido no sincronizador e confira se integrou sem a mensagem.

Se o código da forma de pagamento não aparecer na mensagem, envie o detalhe desse erro no sincronizador para identificarmos o valor correto antes de cadastrar a relação.'''

PRODUCT_REPLY = '''A mensagem indica que o produto do pedido ainda não está relacionado com o cadastro correspondente no Master. Faça a relação usando os identificadores exibidos no erro:

1. Abra o log do pedido e identifique `produto_id` e `cod_estoque`. Na mensagem, o valor apresentado como “código do integrador” corresponde ao `cod_estoque`; use o `produto_id` sem o sufixo de variação quando o log o apresentar dessa forma.
2. Acesse o cadastro do produto correto no Master e pressione Ctrl+R para abrir Relação - Site.
3. Selecione o site da integração e informe o `produto_id` em Código de fora e o `cod_estoque` em Adicional.
4. Confirme e salve a relação. Depois, force novamente o pedido no sincronizador e confira se ele foi integrado.

Antes de salvar, confirme no log quais valores pertencem ao produto desse pedido; os números mostrados na imagem são apenas exemplos.'''

STATE_REGISTRATION_REPLY = '''Olá!

A mensagem “Inscrição Estadual inválida” indica que devemos conferir a IE informada nos dados de entrega da nota fiscal.

1. Abra a nota fiscal no Master, acesse a aba Dados de Entrega e confira o número preenchido no campo RG/IE do destinatário.
2. Acesse https://dfe-portal.svrs.rs.gov.br/Nfe/Ccc, selecione a UF do destinatário, mantenha o ambiente Produção, informe o CNPJ e clique em Pesquisar por CNPJ.
3. No resultado, confirme se a Situação IE está como Habilitado. Estando habilitada, copie o número exibido em Inscrição Estadual (IE), corrija o campo RG/IE nos Dados de Entrega, salve a alteração e tente emitir a nota fiscal novamente.

Se a IE não estiver habilitada no CCC, não substitua o número por conta própria: confirme a situação cadastral do destinatário antes de uma nova emissão. Os dados apresentados nas imagens são apenas exemplos.'''


def normalized(value):
    return ''.join(c for c in unicodedata.normalize('NFKD', str(value or '').lower())
                   if not unicodedata.combining(c))


def match(query):
    text = normalized(query)
    if (re.search(r'\binscricao estadual\b[^\n.!?]{0,100}\binvalid[ao]\b', text) or
            re.search(r'\bie do destinatario\b[^\n.!?]{0,60}\binvalid[ao]\b', text)):
        return {
            'id': 'reviewed-state-registration-v1', 'source_type': 'REVIEWED_PROCEDURE',
            'title': 'https://dfe-portal.svrs.rs.gov.br/Nfe/Ccc',
            'problem': 'Inscrição Estadual inválida na emissão da nota fiscal',
            'solution': STATE_REGISTRATION_REPLY, 'reviewed_reply': STATE_REGISTRATION_REPLY,
            'applicable': True, 'answer_use': 'procedure_reference',
            'quality_flags': [], 'applicability_reasons': [],
            'reviewed_at': '2026-10-13',
            'source_url': 'https://dfe-portal.svrs.rs.gov.br/Nfe/Ccc',
            'provenance': 'Procedimento e imagens revisados por Lucas para o chamado 131662 em 13/10/2026.',
            'resolved_examples': [],
            'images': [
                {'file': 'state-registration-delivery.png', 'after_step': 1,
                 'caption': 'Passo 1: confira o campo RG/IE na aba Dados de Entrega da nota fiscal.'},
                {'file': 'state-registration-ccc-query.png', 'after_step': 2,
                 'caption': 'Passo 2: selecione a UF, informe o CNPJ e pesquise no CCC.'},
                {'file': 'state-registration-ccc-result.png', 'after_step': 3,
                 'caption': 'Passo 3: confirme Situação IE como Habilitado e copie a Inscrição Estadual. Os dados são exemplos.'},
            ],
        }
    if (re.search(r'produto\s+["“]?[^\n]{1,80}["”]?\s*\(codigo do integrador\)', text) and
            re.search(r'\(descricao\)\s*nao integrad[oa]', text)):
        return {
            'id': 'reviewed-product-relation-v1', 'source_type': 'REVIEWED_PROCEDURE',
            'title': 'Produto não integrado — relação pelo Ctrl+R',
            'problem': 'Produto do pedido não relacionado com o cadastro do Master',
            'solution': PRODUCT_REPLY, 'reviewed_reply': PRODUCT_REPLY,
            'applicable': True, 'answer_use': 'procedure_reference',
            'quality_flags': [], 'applicability_reasons': [],
            'reviewed_at': '2026-10-13',
            'provenance': 'Procedimento conferido no chamado resolvido 131672 e no manual Integrando formas de pagamento e transportadora.',
            'resolved_examples': [{'ticket': 131672,
                'title': 'SUP - SNC - Erro na integração de pedido | WBUY',
                'status': 'Resolvido (consulta em 13/10/2026)', 'active': False,
                'resolution': 'Produto relacionado pelo Ctrl+R: produto_id em Código de fora e cod_estoque em Adicional; pedido integrado com sucesso.'}],
            'images': [
                {'file': 'product-relation.png', 'caption': 'Relação - Site do produto: Código de fora, Adicional e Tipo. Os códigos do print são apenas exemplos.'},
            ],
        }
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
    images, notices = [], []
    for item in source.get('images', []):
        name = item.get('file', '')
        if not re.fullmatch(r'[a-z0-9-]+\.png', name):
            continue
        path = Path(__file__).parent / 'static' / 'reply-media' / name
        if not path.is_file():
            notices.append('Imagem mapeada indisponível: ' + name)
            continue
        images.append({'url': origin + '/procedure-images/' + name if origin else '',
                       'data_url': 'data:image/png;base64,' + base64.b64encode(path.read_bytes()).decode(),
                       'caption': item['caption'], 'after_step': item.get('after_step')})
    return images, notices
