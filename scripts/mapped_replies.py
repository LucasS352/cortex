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


NFE_INUTILIZATION_URL = ('https://www.nfe.fazenda.gov.br/portal/consulta.aspx?'
                         'tipoConsulta=inutilizacao&tipoConteudo=nDmpH/MjKrg=')
NFE_QUERY_URL = ('https://www.nfe.fazenda.gov.br/portal/consultaRecaptcha.aspx?'
                 'tipoConsulta=resumo&tipoConteudo=7PhJ+gAVw2g=')
OFFICIAL_QUERY_URLS = {NFE_INUTILIZATION_URL, NFE_QUERY_URL}


def safe_url(value):
    value = str(value or '').strip()
    if value in OFFICIAL_QUERY_URLS:
        return value
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


PAYMENT_REPLY = '''A mensagem indica que a forma de pagamento recebida do integrador ainda não está relacionada no Master. Faça o de/para assim:

1. Acesse Cadastros → Formas de Pagamento e escolha no Master a forma que deve representar o pagamento recebido. Confirme se é crédito, débito, carteira ou outra modalidade; o nome ou a bandeira, isoladamente, não define a escolha correta.
2. Com essa forma de pagamento selecionada, pressione Ctrl+R para abrir Relação - Site.
3. Confira o Site/Meu Site correspondente ao integrador do pedido. Se a relação estiver ausente, clique em Adicionar e preencha Código de fora com o código ou nome externo informado pelo integrador. No exemplo “Shopee PayLater”, relacione exatamente o identificador recebido, após confirmar qual forma do Master deve representá-lo. Não use o UUID do erro como código de pagamento.
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

1. Abra a nota fiscal no Master, acesse a aba Dados de Entrega e anote o CNPJ, a UF e o número preenchido no campo RG/IE do destinatário.
2. Acesse https://dfe-portal.svrs.rs.gov.br/Nfe/Ccc, selecione a mesma UF da nota, mantenha o ambiente Produção, informe o CNPJ e clique em Pesquisar por CNPJ.
3. No resultado, confirme se a Situação IE está como Habilitado e compare a UF e a Inscrição Estadual (IE) com os dados do Master. Não utilize uma IE pertencente a outra UF.
4. Se houver divergência, corrija a IE no cadastro do cliente ou fornecedor para evitar novos erros e atualize também o campo RG/IE nos Dados de Entrega da nota atual. Recarregue os dados do destinatário na nota quando essa opção estiver disponível.
5. Salve a alteração, confira novamente CNPJ, UF e RG/IE e tente transmitir a NF-e.

Se a IE não estiver habilitada no CCC, se o CNPJ não possuir IE nessa UF ou se os dados continuarem divergentes, não informe “ISENTO” nem remova a IE por conta própria. Confirme a situação cadastral e o endereço com o destinatário antes de uma nova emissão. Os dados apresentados nas imagens são apenas exemplos.'''


DUPLICATE_NFE_REPLY = f'''Olá!

A rejeição 539, “Duplicidade de NF-e com diferença na Chave de Acesso”, indica que a SEFAZ já encontrou outra NF-e para o mesmo emitente, modelo, série e número, mas a chave enviada agora está diferente.

1. Abra a nota fiscal no Master e anote os campos Série e Número. Os valores mostrados na imagem abaixo são apenas exemplos; utilize os dados da nota que apresentou a rejeição.
2. Copie a chave de 44 dígitos apresentada na mensagem e consulte a situação da NF-e no Portal Nacional: {NFE_QUERY_URL}
3. Para verificar se a numeração foi inutilizada, acesse a consulta oficial:
{NFE_INUTILIZATION_URL}
Informe o CNPJ do emitente, o ano, o modelo, a série e a numeração correspondente. Essa tela consulta a inutilização da sequência; ela não substitui a consulta da NF-e pela chave.
4. Compare a série e o número do Master com a última NF-e válida dessa mesma série e confirme se outro sistema emissor utilizou a mesma sequência.
5. Se a NF-e encontrada pertencer à mesma operação, não gere outra numeração. Recupere o XML autorizado e solicite a importação ou vinculação da nota ao movimento correto no Master.
6. Se a numeração já tiver sido usada por outra operação e esta for uma nova nota, ajuste a sequência somente após confirmar com a contabilidade qual foi o último número válido da série. Salve e tente transmitir novamente.

Não exclua a nota, altere a série ou avance a numeração por tentativa. Se a consulta apresentar situação cancelada, denegada ou inutilizada, encaminhe os resultados ao suporte antes de fazer alterações.'''


def normalized(value):
    return ''.join(c for c in unicodedata.normalize('NFKD', str(value or '').lower())
                   if not unicodedata.combining(c))


def match(query):
    text = normalized(query)
    if re.search(r'\bduplicidade de nf[ -]?e\b[^\n.!?]{0,100}\bdiferenca na chave de acesso\b', text):
        return {
            'id': 'reviewed-duplicate-nfe-key-v1', 'source_type': 'REVIEWED_PROCEDURE',
            'title': 'Consulta de inutilização no Portal Nacional da NF-e',
            'problem': 'Rejeição 539 — Duplicidade de NF-e com diferença na Chave de Acesso',
            'solution': DUPLICATE_NFE_REPLY, 'reviewed_reply': DUPLICATE_NFE_REPLY,
            'applicable': True, 'answer_use': 'procedure_reference',
            'quality_flags': [], 'applicability_reasons': [], 'reviewed_at': '2026-10-13',
            'source_url': NFE_INUTILIZATION_URL,
            'provenance': ('Procedimento revisado com base na regra oficial da rejeição 539; '
                           'resultados confirmados nos chamados 127706, 117855 e 115902.'),
            'resolved_examples': [
                {'ticket': 127706,
                 'title': 'SUP - NFE - Duplicidade de NF-e com diferença na Chave de Acesso',
                 'status': 'Fechado — transmissão confirmada', 'active': False,
                 'resolution': ('A numeração e a série corretas foram verificadas; após o ajuste, '
                                'a nota foi transmitida com sucesso.')},
                {'ticket': 117855, 'title': 'SUP - NFE - Nota Duplicada',
                 'status': 'Fechado — transmissão confirmada', 'active': False,
                 'resolution': ('Outro sistema já havia utilizado o número da série. Foi conferido o último '
                                'número válido, ajustada a sequência e a nota foi transmitida.')},
                {'ticket': 115902,
                 'title': 'SUP - NFE - Duplicidade de NF-e com diferença na Chave de Acesso',
                 'status': 'Fechado — emissão confirmada', 'active': False,
                 'resolution': ('A chave foi consultada na SEFAZ e a última sequência foi confirmada com a '
                                'contabilidade. O número foi ajustado e as emissões seguintes ocorreram sem o erro.')},
            ],
            'images': [
                {'file': 'duplicate-nfe-series-number.png', 'after_step': 1,
                 'caption': ('Passo 1: confira Série e Número na nota fiscal. '
                             'Os valores destacados são apenas exemplos.')},
            ],
        }
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
            'provenance': ('Procedimento e imagens revisados por Lucas para o chamado 131662; '
                           'resultados confirmados nos chamados encerrados 129530, 118832, 99143 e 104696.'),
            'resolved_examples': [
                {'ticket': 129530, 'title': 'SUP - NFE - IE do destinatário inválida',
                 'status': 'Fechado — emissão confirmada', 'active': False,
                 'resolution': ('A Inscrição Estadual foi atualizada e a nota foi transmitida com sucesso. '
                                'Confirma o ajuste da IE antes de uma nova transmissão.')},
                {'ticket': 118832,
                 'title': 'SUP - NFE - Erro ao emitir nota fiscal | IE do destinatário inválida',
                 'status': 'Fechado — emissão confirmada', 'active': False,
                 'resolution': ('O CNPJ foi consultado, a IE divergente foi corrigida no cadastro do cliente, '
                                'os dados da nota foram recarregados e a nota foi emitida.')},
                {'ticket': 99143,
                 'title': 'Erro ao emitir NF-e | IE do destinatário inválida',
                 'status': 'Fechado — transmissão confirmada', 'active': False,
                 'resolution': ('Os dados do cadastro do cliente estavam divergentes. Após ajustar o cadastro '
                                'e a NF-e, a nota foi transmitida.')},
                {'ticket': 104696, 'title': 'SUP - ERR - Erro Emissão NF',
                 'status': 'Fechado — emissão confirmada', 'active': False,
                 'resolution': ('A IE estava gravada com pontuação. Após conferir e ajustar o formato do número, '
                                'a nota foi emitida sem erros.')},
            ],
            'images': [
                {'file': 'state-registration-delivery.png', 'after_step': 1,
                 'caption': 'Passo 1: confira o campo RG/IE na aba Dados de Entrega da nota fiscal.'},
                {'file': 'state-registration-ccc-query.png', 'after_step': 2,
                 'caption': 'Passo 2: selecione a UF, informe o CNPJ e pesquise no CCC.'},
                {'file': 'state-registration-ccc-result.png', 'after_step': 3,
                 'caption': 'Passo 3: confirme Situação IE como Habilitado e compare a UF e a Inscrição Estadual. Os dados são exemplos.'},
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
    payment_missing = (
        re.search(r'forma de pagamento[^\n.!?]{0,240}\bnao (?:esta )?'
                  r'(?:integrad[ao]|configurad[ao]|relacionad[ao]|associad[ao]|cadastrad[ao])\b', text) or
        re.search(r'rela[cç][aã]o (?:da |de )?forma de pagamento[^\n.!?]{0,100}\bnao encontrad[ao]\b', text)
    )
    if not payment_missing:
        return None
    if re.search(r'ja (?:foi )?integrad|integra(?:m|dos?)? sem forma|any_pagamento_status', text):
        return None
    if re.search(r'\b(?:ja (?:fiz|cadastrei|relacionei|configurei)|nao resolveu|continua|persiste|mesmo apos|nao e (?:a )?forma)\b', text):
        return None
    return {
        'id': 'reviewed-payment-relation-v1', 'source_type': 'REVIEWED_PROCEDURE',
        'title': 'Forma de pagamento não integrada/configurada — relação pelo Ctrl+R',
        'problem': 'Forma de pagamento do integrador sem relação no Master',
        'solution': PAYMENT_REPLY, 'reviewed_reply': PAYMENT_REPLY,
        'applicable': True, 'answer_use': 'procedure_reference',
        'quality_flags': [], 'applicability_reasons': [],
        'reviewed_at': '2026-10-13',
        'provenance': ('Passos conferidos nos manuais Integrando formas de pagamento e transportadora '
                       'e Erro de sincronizador; Ctrl+R confirmado por Lucas em 13/10/2026. '
                       'Resultado também confirmado no chamado 131288.'),
        'resolved_examples': [{'ticket': 131288,
            'title': 'SUP - SNC - Pedidos sem integrar | Anymarket',
            'status': 'Fechado — integração confirmada', 'active': False,
            'resolution': ('O de/para da forma de pagamento foi realizado e o pedido foi integrado. '
                           'O chamado confirma a causa e o resultado; o procedimento detalhado vem do material revisado.')},
            {'ticket': 128116,
            'title': 'SUP - SNC - Forma de Pagamento não integrada',
            'status': 'Encerrado (consulta em 13/10/2026)', 'active': False,
            'resolution': 'Relação com a forma de pagamento realizada; pedido integrado com sucesso.'}],
        'images': [
            {'file': 'payment-selection.png', 'after_step': 2,
             'caption': 'Passos 1–2: selecione a forma correta e pressione Ctrl+R. A seleção do print é apenas exemplo.'},
            {'file': 'payment-relation.png', 'after_step': 3,
             'caption': 'Passo 3: Relação - Site e Código de fora. Site e valores do print são exemplos; use os dados da integração do pedido.'},
        ],
    }


def note_images(source):
    origin = safe_url(os.getenv('CORTEX_PUBLIC_URL', '').rstrip('/'))
    images, notices = [], []
    for item in source.get('images', []):
        name = item.get('file', '')
        if not re.fullmatch(r'[a-z0-9-]+\.png', name):
            continue
        reviewed = Path(__file__).parent / 'static' / 'reply-media' / name
        uploaded = Path(__file__).parent / 'output' / 'training_media' / name
        path = reviewed if reviewed.is_file() else uploaded
        if not path.is_file():
            notices.append('Imagem mapeada indisponível: ' + name)
            continue
        route = '/procedure-images/' if path == reviewed else '/training-media/'
        images.append({'url': origin + route + name if origin else '',
                       'data_url': 'data:image/png;base64,' + base64.b64encode(path.read_bytes()).decode(),
                       'name': item.get('name') or item.get('caption') or 'Imagem de apoio',
                       'caption': item['caption'], 'placement': item.get('placement') or 'procedure',
                       'after_step': item.get('after_step')})
    return images, notices
