"""Editorial layer over imported knowledge. Imported records remain intact."""
from __future__ import annotations

import re
import unicodedata
import json
import base64
from html import escape as xml_escape
from pathlib import Path
from threading import RLock

import psycopg
from psycopg.rows import dict_row

from config import POSTGRES_DSN
from psycopg.types.json import Jsonb
from PIL import Image

MODULES = (
    ('products', 'Produtos e estoque', 'Grade, cadastro, preços, fotos e inventário'),
    ('fiscal', 'Fiscal e notas', 'NF-e, NFC-e, rejeições, CFOP e tributos'),
    ('integrations', 'Integrações', 'Marketplaces, sincronizadores e APIs'),
    ('sales', 'Vendas e pedidos', 'Pedido, orçamento, workflow e faturamento'),
    ('finance', 'Financeiro', 'Boletos, contas e conciliação'),
    ('logistics', 'Logística', 'Transportadoras, etiquetas e rastreio'),
    ('purchases', 'Compras', 'Fornecedores e entrada de mercadorias'),
    ('registration', 'Cadastros', 'Clientes, empresas e parametrizações'),
    ('production', 'Produção', 'Ordens, matéria-prima e processos'),
    ('infrastructure', 'Infraestrutura', 'Banco de dados, instalação e acesso'),
    ('unclassified', 'A classificar', 'Registros ainda sem módulo definido'),
)
MODULE_NAMES = {key: label for key, label, _ in MODULES}
SUBTOPICS = {
    'products': [('grades','Grade e variações',('grade','variacao','subvariacao','tamanho','cor do produto')),
                 ('prices','Preços',('preco','tabela de preco','balcao','promocao')),
                 ('photos','Fotos e mídia',('foto','imagem','midia')),
                 ('tax','Tributação do produto',('tributacao','ncm','cest','icms','pis','cofins')),
                 ('inventory','Estoque e inventário',('estoque','inventario','saldo','lote')),
                 ('registration','Cadastro de produto',('cadastro','cadastrar','produto','sku'))],
    'fiscal': [('rejections','Rejeições',('rejeicao','rejeitado','sefaz erro')),
               ('nfce','NFC-e',('nfce','nf-ce')),
               ('nfe','NF-e',('nfe','nf-e','nota fiscal')),
               ('cfop','CFOP e tributação',('cfop','icms','pis','cofins','tributacao')),
               ('certificate','Certificado e transmissão',('certificado','transmissao','sefaz'))],
    'integrations': [('anymarket','Anymarket',('anymarket',)),('mercadolivre','Mercado Livre',('mercado livre','mercadolivre')),
                     ('tray','Tray',('tray',)),('vtex','VTEX',('vtex',)),('wehub','WeHub',('wehub',)),
                     ('synchronizer','Sincronização',('sincron','integracao','api','marketplace'))],
    'sales': [('orders','Pedidos',('pedido',)),('workflow','Workflow e status',('workflow','status')),
              ('billing','Faturamento',('faturamento','faturar','nota'))],
    'finance': [('billing','Boletos e cobrança',('boleto','cnab','cobranca')),
                ('receivables','Contas a receber',('receber','duplicata','credito')),
                ('payments','Pagamentos',('pagamento','pagar','conciliacao'))],
    'logistics': [('labels','Etiquetas',('etiqueta',)),('tracking','Rastreamento',('rastreio','rastreamento','objeto')),
                  ('carriers','Transportadoras',('transportadora','frete','correios','sigep'))],
}
SUB_SUBTOPICS = {
    'anymarket': [
        ('anymarket:pedidos', 'Pedidos e faturamento', ('pedido', 'faturar', 'faturamento', 'cancelamento', 'ordem', 'order', 'status do pedido')),
        ('anymarket:produtos', 'Produtos e anúncios', ('produto', 'anuncio', 'publicacao', 'variacao', 'sku', 'catalogo', 'codigo de barras')),
        ('anymarket:estoque_preco', 'Estoque e preços', ('estoque', 'preco', 'saldo', 'atualizacao de estoque', 'tabela de preco')),
        ('anymarket:transportadora', 'Transportadoras e frete', ('transportadora', 'frete', 'carrier', 'rastreio', 'tracking', 'envio')),
        ('anymarket:atributos', 'Atributos obrigatórios', ('atributo', 'caracteristica', 'campo obrigatorio', 'ficha tecnica', 'categoria')),
        ('anymarket:auth', 'Token e autenticação', ('token', 'autenticacao', 'credencial', 'chave', 'conexao', 'autorizacao', 'login')),
        ('anymarket:callbacks', 'Callbacks e webhooks', ('callback', 'webhook', 'notificacao', 'fila de sincronizacao')),
    ],
    'mercadolivre': [
        ('mercadolivre:pedidos', 'Pedidos e vendas', ('pedido', 'faturamento', 'cancelamento', 'venda')),
        ('mercadolivre:anuncios', 'Anúncios e produtos', ('anuncio', 'publicacao', 'variacao', 'sku')),
        ('mercadolivre:envios', 'Mercado Envios e frete', ('envio', 'mercado envios', 'frete', 'etiqueta', 'coleta')),
        ('mercadolivre:perguntas', 'Perguntas e mensagens', ('pergunta', 'mensagem', 'pos-venda')),
        ('mercadolivre:auth', 'Token e conexão', ('token', 'autenticacao', 'conexao')),
    ],
    'tray': [
        ('tray:pedidos', 'Pedidos e faturamento', ('pedido', 'faturar', 'faturamento')),
        ('tray:produtos', 'Produtos e variações', ('produto', 'variacao', 'sku', 'preco')),
        ('tray:frete', 'Frete e envio', ('frete', 'envio', 'transportadora')),
        ('tray:auth', 'Token e conexão', ('token', 'api key', 'autenticacao')),
    ],
    'vtex': [
        ('vtex:pedidos', 'Pedidos (OMS)', ('pedido', 'oms', 'faturamento')),
        ('vtex:catalogo', 'Catálogo e SKU', ('catalogo', 'sku', 'produto', 'especificacao')),
        ('vtex:estoque_preco', 'Estoque e preço', ('estoque', 'preco', 'inventario')),
        ('vtex:logistica', 'Logística e doca', ('logistica', 'doca', 'transportadora')),
    ],
    'rejections': [
        ('rejections:icms', 'ICMS e DIFAL', ('icms', 'difal', 'partilha', 'fcp')),
        ('rejections:dados', 'Dados Emitente / Destinatário', ('destinatario', 'emitente', 'cnpj', 'ie', 'endereco', 'cep')),
        ('rejections:sefaz', 'Sefaz indisponível / timeout', ('sefaz', 'indisponivel', 'timeout', 'comunicacao')),
        ('rejections:duplicidade', 'Duplicidade de NF-e', ('duplicidade', 'duplicada')),
        ('rejections:total', 'Total e fechamento de valores', ('total', 'valor', 'base de calculo', 'soma')),
    ],
    'grades': [
        ('grades:estrutura', 'Estrutura e tamanhos', ('tamanho', 'cor', 'grade', 'estrutura')),
        ('grades:ean', 'Código de barras e EAN', ('ean', 'codigo de barras', 'gtin')),
    ],
    'prices': [
        ('prices:tabelas', 'Tabelas e regras atacado/varejo', ('tabela', 'atacado', 'varejo', 'markup')),
        ('prices:promocoes', 'Preços promocionais', ('promocao', 'promocional', 'desconto')),
    ],
    'inventory': [
        ('inventory:saldo', 'Saldo e movimentação', ('saldo', 'movimentacao', 'ajuste', 'kardex')),
        ('inventory:lote', 'Lote e validade', ('lote', 'validade', 'vencimento')),
    ],
}
STATUSES = {'draft', 'approved', 'excluded'}
_schema_ready = False
_schema_lock = RLock()


def normalize(value):
    return ''.join(ch for ch in unicodedata.normalize('NFKD', str(value or '').casefold()) if not unicodedata.combining(ch))


def classify(title, modules, category=''):
    """Best-effort filing, not an assertion that the procedure is valid."""
    tags = normalize(' '.join(modules or []) + ' ' + (category or ''))
    title = normalize(title)
    # Prioritize explicit module tags. Bare titles are a fallback for untagged KBs.
    if re.search(r'nfs-?e|nfse|nota fiscal de servico',title): return 'fiscal'
    if re.search(r'mapa de producao|ordem de producao|sistema de producao',title): return 'production'
    if re.search(r'nf-?e|nfc-?e|nota.?fiscal|fiscal|sped|sefaz', tags): return 'fiscal'
    if re.search(r'produto|estoque|inventario|grade|sku', tags): return 'products'
    if re.search(r'integr|sinc|market|ecommerce|anymarket|mercado livre|tray|vtex|shopee|nuvemshop|bling|wehub|tiny', tags): return 'integrations'
    if re.search(r'transport|logistic|correios|sigep|frete|expedicao', tags): return 'logistics'
    if re.search(r'finance|boleto|cnab|cobranca|pagamento', tags): return 'finance'
    if re.search(r'venda|pedido|faturamento|orcamento', tags): return 'sales'
    if re.search(r'compra|fornecedor', tags): return 'purchases'
    if re.search(r'producao|materia prima', tags): return 'production'
    if re.search(r'firebird|sql|database|banco|infra|backup|servidor|instalacao', tags): return 'infrastructure'
    if re.search(r'cadastro|cliente|empresa|configurac', tags): return 'registration'
    if re.search(r'nf-?e|nfc-?e|nfs-?e|nota fiscal|sefaz|rejeicao|cfop', title): return 'fiscal'
    if re.search(r'produto|estoque|inventario|grade|sku|preco', title): return 'products'
    if re.search(r'integr|sincron|marketplace|anymarket|mercado livre|tray|vtex|shopee|wehub|shopify|gubee', title): return 'integrations'
    if re.search(r'transport|correios|frete|etiqueta|rastreio|expedicao', title): return 'logistics'
    if re.search(r'boleto|cnab|duplicata|pagamento|financeir', title): return 'finance'
    if re.search(r'pedido|venda|orcamento|workflow|faturamento', title): return 'sales'
    if re.search(r'compra|fornecedor', title): return 'purchases'
    if re.search(r'producao|materia prima',title): return 'production'
    if re.search(r'firebird|banco de dados|backup|servidor|conexao', title): return 'infrastructure'
    return 'unclassified'


def subtopic(module_key,title,modules,deep=False):
    for text in (normalize(title),normalize(' '.join(modules or []))):
        for key,_,terms in SUBTOPICS.get(module_key,[]):
            if any(term in text for term in terms):
                if deep and key in SUB_SUBTOPICS:
                    for sub_key,_,sub_terms in SUB_SUBTOPICS[key]:
                        if any(term in text for term in sub_terms):
                            return sub_key
                return key
    return 'general'


def subtopic_options(module_key):
    options = []
    for key,label,_ in SUBTOPICS.get(module_key,[]):
        options.append((key,label))
        for sub_key,sub_label,_ in SUB_SUBTOPICS.get(key,[]):
            options.append((sub_key, f"{label} > {sub_label}"))
    options.append(('general','Geral / outros'))
    return options


def connect(read_only=False):
    return psycopg.connect(POSTGRES_DSN, connect_timeout=5, row_factory=dict_row,
        options='-c statement_timeout=12000' + (' -c default_transaction_read_only=on' if read_only else ''))


def ensure_schema():
    global _schema_ready
    with _schema_lock:
      if _schema_ready: return
      with connect() as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS knowledge_curation (
            node_id bigint PRIMARY KEY REFERENCES ticket_knowledge_nodes(id) ON DELETE CASCADE,
            module_key text NOT NULL, subtopic_key text NOT NULL DEFAULT 'general',
            status text NOT NULL CHECK (status IN ('draft','approved','excluded')),
            title_override text, problem_override text, solution_override text,
            prerequisites text, expected_result text,
            version_note text, change_reason text NOT NULL,
            revision integer NOT NULL DEFAULT 1, updated_at timestamptz NOT NULL DEFAULT now()
        )''')
        conn.execute("ALTER TABLE knowledge_curation ADD COLUMN IF NOT EXISTS subtopic_key text NOT NULL DEFAULT 'general'")
        conn.execute("ALTER TABLE knowledge_curation ADD COLUMN IF NOT EXISTS prerequisites text")
        conn.execute("ALTER TABLE knowledge_curation ADD COLUMN IF NOT EXISTS expected_result text")
        conn.execute("ALTER TABLE knowledge_curation ADD COLUMN IF NOT EXISTS keywords text[] NOT NULL DEFAULT '{}'")
        conn.execute("ALTER TABLE knowledge_curation ADD COLUMN IF NOT EXISTS image_annotations jsonb NOT NULL DEFAULT '[]'::jsonb")
        conn.execute('''CREATE TABLE IF NOT EXISTS knowledge_curation_history (
            id bigserial PRIMARY KEY, node_id bigint NOT NULL, revision integer NOT NULL,
            old_value jsonb, new_value jsonb NOT NULL, change_reason text NOT NULL,
            changed_at timestamptz NOT NULL DEFAULT now()
        )''')
      _schema_ready = True


def apply_overlay(row, edit):
    """Apply only approved corrections to model-facing fields; draft metadata stays editorial."""
    result = dict(row)
    if edit:
        result['curation_status'] = edit['status']
        result['curation_module'] = edit['module_key']
        result['curation_subtopic'] = edit['subtopic_key']
        result['curation_revision'] = edit['revision']
        # Search synonyms are editorial metadata; they do not assert that draft steps are valid.
        result['search_keywords'] = edit.get('keywords') or []
        if edit['status'] == 'approved':
            for field in ('title', 'problem', 'solution'):
                value = edit.get(field + '_override')
                if value is not None: result[field] = value
            if edit.get('version_note'): result['version_reference'] = edit['version_note']
            if edit.get('prerequisites') or edit.get('expected_result'):
                result['solution'] = '\n'.join(part for part in (
                    'Pré-requisitos: '+edit['prerequisites'] if edit.get('prerequisites') else '',
                    'Procedimento: '+(result.get('solution') or ''),
                    'Resultado esperado: '+edit['expected_result'] if edit.get('expected_result') else '') if part)
            for mark in edit.get('image_annotations') or []:
                url = mark['url']
                result['solution'] = (result.get('solution') or '').replace(
                    '](' + url + ')', '](' + url[:-4] + '.marked.svg)')
    else:
        result['curation_status'] = 'draft'
        result['curation_module'] = classify(result.get('title'), result.get('modules'), result.get('manual_category'))
        result['curation_subtopic'] = subtopic(result['curation_module'],result.get('title'),result.get('modules'))
        result['curation_revision'] = 0
        result['search_keywords'] = []
    return result


def overlays(conn, ids):
    if not ids: return {}
    return {r['node_id']: r for r in conn.execute('SELECT * FROM knowledge_curation WHERE node_id=ANY(%s)', (list(ids),)).fetchall()}


def render_marked_images(node_id, annotations):
    """Build self-contained SVG screenshots; original video frames remain untouched."""
    root = Path(__file__).parent / 'output' / 'curation_media' / str(node_id)
    grouped = {}
    for mark in annotations:
        grouped.setdefault(mark['url'], []).append(mark)
    for url, marks in grouped.items():
        frame = root / Path(url).name
        if not frame.is_file():
            raise ValueError(f'Quadro do vídeo não encontrado: {frame.name}')
        with Image.open(frame) as image:
            width, height = image.size
        encoded = base64.b64encode(frame.read_bytes()).decode('ascii')
        radius = max(12, round(min(width, height) * 0.027))
        circles = []
        for number, mark in enumerate(marks, 1):
            x, y = round(mark['x'] * width), round(mark['y'] * height)
            label = xml_escape(str(mark['label']), quote=True)
            circles.append(f'<g aria-label="{label}"><circle cx="{x}" cy="{y}" r="{radius+4}" fill="#fff"/>'
                           f'<circle cx="{x}" cy="{y}" r="{radius}" fill="#d92d50" stroke="#fff" stroke-width="2"/>'
                           f'<text x="{x}" y="{y+round(radius*0.37)}" fill="#fff" text-anchor="middle" '
                           f'font-family="Arial,sans-serif" font-size="{round(radius*1.15)}" font-weight="bold">{number}</text></g>')
        svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
               f'viewBox="0 0 {width} {height}" role="img">'
               f'<image href="data:image/jpeg;base64,{encoded}" width="{width}" height="{height}"/>'
               + ''.join(circles) + '</svg>')
        frame.with_suffix('.marked.svg').write_text(svg, encoding='utf-8')


def curate(conn, node_id, update, expected_revision):
    """Optimistic editing; every change stores the previous and resulting state."""
    if update['module_key'] not in MODULE_NAMES or update['status'] not in STATUSES or update['subtopic_key'] not in dict(subtopic_options(update['module_key'])):
        raise ValueError('Módulo ou estado inválido.')
    keywords = [str(value).strip() for value in update.get('keywords', [])]
    if len(keywords) > 30 or any(not value or len(value) > 80 for value in keywords):
        raise ValueError('Informe até 30 palavras-chave, com no máximo 80 caracteres cada.')
    keywords = list(dict.fromkeys(keywords))
    annotations = update.get('image_annotations') or []
    if len(annotations) > 40:
        raise ValueError('Informe no máximo 40 marcações de imagem.')
    for mark in annotations:
        if not isinstance(mark, dict) or not re.fullmatch(rf'/curation_media/{node_id}/frame-\d+\.jpg', str(mark.get('url', ''))):
            raise ValueError('Imagem da marcação inválida.')
        if not isinstance(mark.get('x'), (int, float)) or not isinstance(mark.get('y'), (int, float)) or not 0 <= mark['x'] <= 1 or not 0 <= mark['y'] <= 1:
            raise ValueError('Posição da marcação inválida.')
        if not str(mark.get('label', '')).strip() or len(str(mark['label'])) > 120:
            raise ValueError('Descreva cada marcação em até 120 caracteres.')
    node = conn.execute('SELECT id,title,problem,solution,modules,manual_category FROM ticket_knowledge_nodes WHERE id=%s FOR UPDATE', (node_id,)).fetchone()
    if not node: raise LookupError('Fonte não encontrada.')
    old = conn.execute('SELECT * FROM knowledge_curation WHERE node_id=%s FOR UPDATE', (node_id,)).fetchone()
    revision = old['revision'] if old else 0
    if revision != expected_revision: raise RuntimeError('Esta fonte foi alterada. Recarregue antes de salvar.')
    if annotations:
        render_marked_images(node_id, annotations)
    next_revision = revision + 1
    result = conn.execute('''INSERT INTO knowledge_curation
        (node_id,module_key,subtopic_key,status,title_override,problem_override,solution_override,prerequisites,expected_result,version_note,keywords,image_annotations,change_reason,revision)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT(node_id) DO UPDATE SET module_key=EXCLUDED.module_key,subtopic_key=EXCLUDED.subtopic_key,status=EXCLUDED.status,
        title_override=EXCLUDED.title_override,problem_override=EXCLUDED.problem_override,
        solution_override=EXCLUDED.solution_override,prerequisites=EXCLUDED.prerequisites,
        expected_result=EXCLUDED.expected_result,version_note=EXCLUDED.version_note,keywords=EXCLUDED.keywords,image_annotations=EXCLUDED.image_annotations,
        change_reason=EXCLUDED.change_reason,revision=EXCLUDED.revision,updated_at=now()
        RETURNING *''', (node_id, update['module_key'],update['subtopic_key'], update['status'], update['title_override'],
                         update['problem_override'], update['solution_override'],update['prerequisites'],update['expected_result'],
                         update['version_note'], keywords, Jsonb(annotations), update['change_reason'], next_revision)).fetchone()
    conn.execute('''INSERT INTO knowledge_curation_history(node_id,revision,old_value,new_value,change_reason)
        VALUES (%s,%s,%s,%s,%s)''', (node_id,next_revision,
            Jsonb(json.loads(json.dumps(dict(old), default=str))) if old else None,
            Jsonb(json.loads(json.dumps(dict(result), default=str))), update['change_reason']))
    return dict(result)
