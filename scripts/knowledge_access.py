"""Shared, explainable retrieval. Similarity is never diagnostic confidence."""
import re
import unicodedata
from difflib import get_close_matches
import knowledge_curation as curation
import knowledge_index
from collections import Counter

STOP = set('a o os as de do da dos das e em um uma para por no na nos nas com como que eu meu meus minha minhas esta estou erro error problema fazer faco fica nao ao pelo pela mais esse essa tem la favor ajuda consigo preciso quero saber bom dia ola ocorre qual onde master erp sistema contexto mensagem atual integradores ver quantas quanto quantos quanta tive teve tinha ter isso estes estas aquele aquela aquilo consegue pode podemos poderia sobre alguem gente aqui ali assim gostaria informacao informacoes duvida dando tentando tentar esta ta tao foi fui iria agora mostra mostrar mostrou foram mes mensal algum alguma alguns algumas possa posso podem puder boa tarde noite pessoal existe existem sabem queria precisava verificar ajudar chamado ticket cliente clientes empresa loja obrigado obrigada certo possivel tirar dar mas faz funciona acertar passo emitida emitido conseguimos gentileza'.split())
DOMAIN_SPELLING = ('destinatario', 'remetente', 'inscricao', 'estadual', 'invalida', 'rejeicao',
                   'sincronizador', 'integracao', 'etiqueta', 'transportadora', 'fabricante')
ALIASES = {
    'criar': ('criar','cadastrar','cadastro','cadastrando'),
    'cadastrando': ('cadastrar','cadastro','criar'),
    'nfe': ('nfe','nf-e','nota fiscal'), 'nf': ('nfe','nota fiscal'),
    'grade': ('grade','variacao','subvariacao'), 'grades': ('grade','variacao','subvariacao'),
    'cadastrar': ('cadastrar','cadastro'), 'cadastro': ('cadastro','cadastrar'),
    'sincroniza': ('sincronizar','integracao','sincronizador'),
    'sincronizacao': ('sincronizacao','integracao','sincronizador'),
    'boleto': ('boleto','cnab','cobranca'),
    'venda': ('venda','vendas'), 'vendas': ('vendas','venda'),
    'produto': ('produto','produtos'), 'produtos': ('produtos','produto'),
    'emitir': ('emitir','emissao','gerar'), 'gerar': ('gerar','emitir','emissao'),
    'cancelar': ('cancelar','cancelamento'), 'cancelamento': ('cancelamento','cancelar'),
    'lancar': ('lancar','lancamento'), 'lancamento': ('lancamento','lancar'),
    'importar': ('importar','importacao','entrada'), 'importacao': ('importacao','importar','entrada'),
    'exportar': ('exportar','exportacao'),
    'devolucao': ('devolucao','devolver'),
    'comissao': ('comissao','comissoes'),
    'vendedor': ('vendedor','vendedores'),
    # Customers normally ask for "margem" while the Master screen and KB use
    # the product name "Markup". Keep both vocabularies in the same intent.
    'margem': ('margem','markup','mark-up','lucro'),
    'markup': ('markup','mark-up','margem','lucro'),
}
ACCENT_MAP = {
    'relatorio': 'relatório', 'relatorios': 'relatórios',
    'inventario': 'inventário', 'inventarios': 'inventários',
    'destinatario': 'destinatário', 'bancario': 'bancário', 'bancaria': 'bancária',
    'usuario': 'usuário', 'usuarios': 'usuários', 'unitario': 'unitário',
    'tributario': 'tributário', 'tributaria': 'tributária', 'necessario': 'necessário',
    'estatistica': 'estatística', 'analitico': 'analítico',
    'codigo': 'código', 'codigos': 'códigos', 'numero': 'número', 'numeros': 'números',
    'periodo': 'período', 'minimo': 'mínimo', 'maximo': 'máximo', 'liquido': 'líquido',
    'conferencia': 'conferência', 'divergencia': 'divergência',
    'ocorrencia': 'ocorrência', 'ocorrencias': 'ocorrências',
    'transferencia': 'transferência', 'pendencia': 'pendência', 'referencia': 'referência',
    'invalida': 'inválida', 'invalido': 'inválido', 'valida': 'válida', 'valido': 'válido',
    'serie': 'série', 'especie': 'espécie',
    'preco': 'preço', 'precos': 'preços', 'servico': 'serviço', 'servicos': 'serviços',
    'endereco': 'endereço', 'orcamento': 'orçamento', 'lancamento': 'lançamento',
    'lancar': 'lançar', 'balanco': 'balanço', 'cobranca': 'cobrança', 'diferenca': 'diferença',
    'modulo': 'módulo', 'modulos': 'módulos', 'calculo': 'cálculo', 'vinculo': 'vínculo',
    'titulo': 'título', 'titulos': 'títulos', 'credito': 'crédito', 'debito': 'débito',
    'historico': 'histórico', 'parametro': 'parâmetro', 'parametros': 'parâmetros',
    'automatico': 'automático', 'automatica': 'automática', 'eletronica': 'eletrônica',
    'gondola': 'gôndola',
    'comissao': 'comissão', 'comissoes': 'comissões',
    'emissao': 'emissão', 'impressao': 'impressão', 'exclusao': 'exclusão',
    'inclusao': 'inclusão', 'transmissao': 'transmissão', 'permissao': 'permissão',
    'permissoes': 'permissões', 'versao': 'versão', 'conexao': 'conexão', 'padrao': 'padrão',
    'rejeicao': 'rejeição', 'rejeicoes': 'rejeições',
}
BROAD_TERMS = {'produto', 'produtos', 'venda', 'vendas', 'nota', 'notas', 'fiscal', 'fiscais',
               'pedido', 'pedidos', 'emitir', 'gerar', 'lancar', 'consultar', 'relatorio',
               'relatorios', 'tela', 'compra', 'compras', 'estoque', 'financeiro'}
GROUPS = {
    'Fiscal': ('fiscal','nf-e','nfe','nota_fiscal','nota fiscal','sefaz','danfe','cfop'),
    'Produtos e estoque': ('produto','estoque','grade','inventario'),
    'Integrações': ('integracao','sincron','ecommerce','marketplace','anymarket','safetech','vtex','tray'),
    'Financeiro': ('financeiro','boleto','cnab','cobranca','contas a'),
    'Vendas': ('venda','pedido','orcamento','faturamento'),
    'Compras': ('compra','fornecedor'),
    'Produção': ('producao','materia prima'),
    'Cadastros': ('cadastro','cliente','fabricante'),
    'Logística': ('correios','sigep','transport','expedicao','frete'),
    'Produção': ('producao','materia prima'),
    'Infraestrutura': ('firebird','sql','oracle','backup','servidor','instalacao'),
}

VECTOR_INDEX_DIMENSIONS = 3072


def normalize(s):
    return ''.join(c for c in unicodedata.normalize('NFKD',str(s or '').lower()) if not unicodedata.combining(c))


def accent_variants(word):
    out = [word]
    if word in ACCENT_MAP:
        out.append(ACCENT_MAP[word])
    elif word.endswith('cao') and len(word) >= 5:
        out.append(word[:-3] + 'ção')
    elif word.endswith('coes') and len(word) >= 6:
        out.append(word[:-4] + 'ções')
    elif word.endswith('ao') and len(word) >= 5:
        out.append(word[:-2] + 'ão')
    elif word.endswith('oes') and len(word) >= 5:
        out.append(word[:-3] + 'ões')
    return list(dict.fromkeys(out))


def _paired_fts(words):
    import itertools
    groups = []
    for w in words[:8]:
        syns = []
        for a in ALIASES.get(w, (w,)):
            for v in accent_variants(a):
                if v not in syns:
                    syns.append(v)
        groups.append((w, syns[:4]))
    if not groups:
        return ''
    if len(groups) == 1:
        return ' OR '.join('"' + v + '"' for v in groups[0][1])
    specific = [syns for w, syns in groups if w not in BROAD_TERMS]
    broad = [syns for w, syns in groups if w in BROAD_TERMS]
    pairs = []
    if specific and broad:
        for s in specific[:4]:
            for b in broad[:4]:
                pairs.extend(f'"{va}" "{vb}"' for va in s for vb in b)
        if len(specific) >= 2:
            for sa, sb in itertools.combinations(specific[:4], 2):
                pairs.extend(f'"{va}" "{vb}"' for va in sa for vb in sb)
    else:
        for (_, sa), (_, sb) in itertools.combinations(groups[:5], 2):
            pairs.extend(f'"{va}" "{vb}"' for va in sa for vb in sb)
    return ' OR '.join(dict.fromkeys(pairs))


def query_plan(query):
    text=normalize(query)
    report = bool(re.search(
        r'\b(?:relatorio|relatorios|indicador|indicadores|markup|cmv|rentabilidade|curva abc|estatistica de vendas)\b|'
        r'\b(?:projecao de compras|posicao de estoque|demonstrativo de venda analitico|dva)\b|'
        r'\bcomissao\b.{0,50}\bvendedor\b|'
        r'\bmargem\b.{0,50}\b(?:venda|vendas|lucro|produto|produtos)\b|'
        r'\b(?:venda|vendas|lucro|produto|produtos)\b.{0,50}\bmargem\b', text))
    product_creation = bool(re.search(r'\b(?:criar|cadastrar|cadastro)\b.{0,35}\bprodutos?\b', text))
    identifiers=sorted(set(re.findall(r'\b[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}\b',text)))
    # Opaque IDs can recur across different causes. Keep them separate from
    # mandatory database/rejection codes, so a new ID can still retrieve the
    # same diagnostic message. A matching message is stronger than an ID.
    signatures=list(dict.fromkeys(re.sub(r'\s+',' ',s).strip(' .!') for s in
        re.findall(r'atencao\s*[!:]\s*([^\r\n]{15,240})',text)))[:4]
    for value in re.findall(r'(?:\brejeicao\s*(?:\d{3}\s*)?[: -]\s*|\berro\s*:\s*|\berro\s+no\s+retorno\s+da\s+plataforma\s*(?:\(\s*\d+\s*\))?\s*:\s*|\bmensagem\s*:\s*|\bcodigo\s+\d{3}\s*[-:]\s*)([^\r\n]{15,240})',text):
        value=re.sub(r'\s+',' ',value).strip(' .!\'"')
        if len(re.findall(r'\b[a-z]{3,}\b',value)) >= 3 and not re.match(r'https?[:/]|\[?[a-f0-9]{8}-',value):
            signatures.append(value)
    signatures=list(dict.fromkeys(signatures))[:6]
    objects=sorted(set(re.findall(r'\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b',text)))
    codes=sorted(set(re.findall(r'\bora-\d{5}\b',text)))
    codes+=['rejection:'+x for x in re.findall(r'(?:rejeicao\s*[: -]?\s*)(\d{3})\b',text)]
    codes+=['rejection:'+x for x in re.findall(r'\b(\d{3})\s*[-:]\s*rejeicao',text)]
    words=[]
    for word in re.findall(r'[a-z][a-z0-9]*',text):
        if len(word)<3 or word in STOP: continue
        if len(word)>=7 and word not in DOMAIN_SPELLING:
            correction=get_close_matches(word,DOMAIN_SPELLING,n=1,cutoff=.85)
            if correction: word=correction[0]
        if word not in words: words.append(word)
        if len(words)>=18: break
    expansions=list(dict.fromkeys(a for w in words for a in ALIASES.get(w,(w,))))
    fts_tokens=list(dict.fromkeys(v for w in expansions for v in accent_variants(w)))
    # Quoted OR operands parsed by PostgreSQL; all user text stays parameterized.
    fts=' OR '.join('"'+w+'"' for w in fts_tokens)
    fts_paired=_paired_fts(words) or fts
    protocol=re.fullmatch(r'#?(\d{1,9})',query.strip())
    return dict(query=query,product_creation=product_creation,objects=objects,codes=sorted(set(codes)),identifiers=identifiers,signatures=signatures,terms=words,expanded_terms=expansions,
                fts=fts,fts_paired=fts_paired,protocol=int(protocol[1]) if protocol else None,
                howto=bool(re.search(r'\b(como|onde|criar|cadastrar|configurar|procedimento|passo a passo)\b',text)),
                report=report)


def report_topic_match(plan, title):
    """Match the requested business report to its official catalog name."""
    query = normalize(plan.get('query') or '')
    title = normalize(title)
    if re.search(r'\b(?:fr3|fastreport|etiqueta|bobina|impressora|zebra|argox)\b', query):
        return False
    rules = (
        (bool(re.search(r'\b(?:margem|markup|rentabilidade|lucro)\b', query)),
         r'\b(?:markup|margem|rentabilidade)\b'),
        (bool(re.search(r'\bvendas? por produtos?\b', query)),
         r'\bestatistica\b.*\bvendas?\b|\bvendas?\b.*\bprodutos?\b'),
        (bool(re.search(r'\bestatisticas? de vendas?\b', query)),
         r'\bestatistica\b.*\bvendas?\b'),
        ('projecao' in query and 'compra' in query, r'\bprojecao\b.*\bcompr'),
        ('curva abc' in query and 'produto' in query, r'\bcurva abc\b.*\bprodutos?\b'),
        ('curva abc' in query and 'produto' not in query, r'\bcurva abc\b'),
        ('posicao de estoque' in query, r'\bposicao de estoque\b'),
        ('estoque' in query and 'posicao de estoque' not in query, r'\bestoque\b'),
        ('frete' in query, r'\bestatistica\b.*\bfrete\b|\bfrete\b'),
        ('gerencial' in query, r'\brelatorio gerencial\b'),
        ('comissao' in query and 'vendedor' in query,
         r'\bcomiss\w*\b.*\bvendedor\w*\b'),
        (bool(re.search(r'\bdemonstrativo de vendas? analitico\b|\bdva\b', query)),
         r'\bdemonstrativo de vendas? analitico\b|\bdva\b'),
    )
    active = [pattern for condition, pattern in rules if condition]
    return bool(active and any(re.search(pattern, title) for pattern in active))


def family(row):
    text=normalize(' '.join(row.get('modules') or [])+' '+(row.get('manual_category') or '')+' '+(row.get('title') or ''))
    hits=[name for name,terms in GROUPS.items() if any(t in text for t in terms)]
    return hits or ['Sem classificação']


def quality(row):
    solution=(row.get('solution') or '').strip()
    # Ignore boilerplate when judging whether a procedure was actually recorded.
    body=re.split(r'obrigado por entrar|voce ja|__',normalize(solution))[0].strip()
    generic=bool(re.search(r'sistema atualizado|correcao do dev|erro (?:foi )?(?:ajustado|corrigido)|ajuste necessario|devidos ajustes',body)) and not re.search(r'\b(?:acesse|selecione|preencha|parametro|campo|tabela|causa|configure|alterar|cadastro)\b',body)
    flags=[]
    if len(body)<45: flags.append('solution_missing_or_short')
    if generic: flags.append('generic_resolution')
    if not row.get('version_reference'): flags.append('version_not_recorded')
    if not row.get('screen_path'): flags.append('screen_path_not_recorded')
    raw=(row.get('content_for_embedding') or '')+' '+solution
    if '00000000000' in raw or 'desconto 100%' in normalize(raw): flags.append('unsafe_generalization_review')
    if re.search(r'\b(?:DELETE FROM|UPDATE\s+\w+\s+SET|DROP TABLE|TRUNCATE|GRANT)\b',raw,re.I): flags.append('database_change_review')
    official=row.get('source_type') in ('OFFICIAL_KB','KB')
    manual=row.get('source_type')=='MANUAL'
    evidence='official_document' if official else 'manual_requires_original_check' if manual else 'historical_case_unverified'
    if row.get('hard_block_reason'): flags.append('source_hard_block')
    if row.get('source_type') == 'INVESTIGATIVE': flags.append('unvalidated_source')
    if any(f in flags for f in ('solution_missing_or_short','unsafe_generalization_review',
                               'database_change_review','source_hard_block','unvalidated_source')) or generic:
        use='investigation_only'
    else:
        use='procedure_reference' if official else 'supporting_reference'
    return dict(evidence_status=evidence,answer_use=use,quality_flags=flags,
                confidence_notice='Relevância não é probabilidade de diagnóstico correto; conferir fonte e aplicabilidade.')


INTEGRATORS = ('loja integrada','anymarket','mercado livre','mercadolivre','shopee',
               'vtex','tray','magalu','amazon','nuvemshop','skyhub','uappi','well','wehub','wbuy')


def integrator_names(text):
    names={name for name in INTEGRATORS if re.search(r'\b'+re.escape(name)+r'\b',normalize(text))}
    return {('uappi/well' if n in ('uappi','well') else 'mercado livre' if n=='mercadolivre' else n) for n in names}


_RELATION_ENTITIES = {
    'produto': r'\b(?:produtos?|sku|item|itens)\b',
    'pagamento': r'\b(?:formas? de pagamento|pagamentos?)\b',
    'transportadora': r'\b(?:transportadoras?|carrier|frete)\b',
}
_RELATION_FAILURE = re.compile(
    r'\bnao (?:esta |estao |foi |foram |existe |ha |possui )?(?:\w+\s+){0,3}'
    r'(?:relacionad[ao]s?|vinculad[ao]s?|associad[ao]s?|integrad[ao]s?|integra|integram|relacionamento|vinculo)\b|'
    r'\bsem (?:o |um |uma )?(?:relacao|relacionamento|vinculo|de[ /-]?para|'
    r'codigo (?:externo|de fora)|tipo\s*[\'"]?s\b)|'
    r'\b(?:relacao|relacionamento|vinculo|codigo (?:externo|de fora))\b.{0,20}'
    r'\b(?:ausente|inexistente|nao encontrad[ao])\b')


def mapping_failure_entities(query):
    """Identify the entity reported as failing, excluding nearby successful relations."""
    targets = set()
    clauses = re.split(r'[,;\n.!?]|\b(?:mas|porem|entretanto|porque|devido a)\b', normalize(query))
    for clause in clauses:
        mentions = sorted((match.start(), match.end(), entity)
                          for entity, pattern in _RELATION_ENTITIES.items()
                          for match in re.finditer(pattern, clause))
        for index, (_start, end, entity) in enumerate(mentions):
            stop = mentions[index + 1][0] if index + 1 < len(mentions) else len(clause)
            if _RELATION_FAILURE.search(clause[end:stop][:100]):
                targets.add(entity)
    return targets


def applicability(row, query, context=None):
    """Shared evidence gate. Retrieval rank is never diagnostic confidence."""
    plan=query_plan(query)
    body=' '.join(str(row.get(k) or '') for k in ('title','problem','diagnosis','solution','content_for_embedding','content_text'))
    compact=re.sub(r'\s+',' ',normalize(body))
    objects,codes=anchor_matches(plan,body)
    signatures=[s for s in plan['signatures'] if s in compact]
    expected=integrator_names(query if context is None else context)
    actual=integrator_names(body)
    conflict=bool(expected and actual and expected.isdisjoint(actual))
    reasons=[]
    if plan['howto']:
        purpose = normalize(str(row.get('title') or '') + ' ' + str(row.get('problem') or ''))
        for variants in (('importar','importacao','entrada'), ('exportar','exportacao'),
                         ('cancelar','cancelamento'), ('transferencia','transferir'),
                         ('kit','kits'), ('grade','grades','variacao'), ('foto','fotos','imagem','imagens')):
            if any(re.search(r'\b'+term+r'\b', normalize(query)) for term in variants):
                if not any(re.search(r'\b'+term+r'\b', purpose) for term in variants):
                    reasons.append('requested_operation_not_matched')
    if row.get('evidence_status') == 'human_approved_procedure':
        from datetime import date
        scope = (row.get('metadata') or {}).get('applicability') or {}
        context_text = normalize(query if context is None else context)
        for field in ('master_version', 'tax_regime', 'ufs', 'integrations'):
            values = scope.get(field)
            if not values: continue
            values = values if isinstance(values, list) else [values]
            if not any(re.search(r'(?<!\w)' + re.escape(normalize(v)) + r'(?!\w)', context_text) for v in values):
                reasons.append('canonical_scope_requires_confirmation:' + field)
        try:
            if scope.get('valid_from') and date.today() < date.fromisoformat(scope['valid_from']):
                reasons.append('canonical_not_yet_valid')
            if scope.get('valid_until') and date.today() > date.fromisoformat(scope['valid_until']):
                reasons.append('canonical_expired')
        except (ValueError, TypeError):
            reasons.append('canonical_invalid_validity')
    if conflict: reasons.append('integration_context_mismatch')
    # A shared noun is insufficient when the customer's stated failure differs.
    # E.g. "IE inválida" must not inherit a procedure for "contribuinte isento".
    symptom=normalize(' '.join(str(row.get(k) or '') for k in ('title','problem','diagnosis')))
    failed_entities = mapping_failure_entities(query)
    source_entities = {entity for entity, pattern in _RELATION_ENTITIES.items()
                       if re.search(pattern, symptom)}
    if failed_entities - source_entities:
        reasons.append('mapping_entity_not_matched')
    qualifier_groups=(('invalida','invalido','invalid'), ('isento','isenta'),
                      ('nao informada','nao informado'), ('maior','superior'), ('menor','inferior'))
    for variants in qualifier_groups:
        if any(re.search(r'\b'+re.escape(term)+r'\b', normalize(query)) for term in variants):
            if not any(re.search(r'\b'+re.escape(term)+r'\b', symptom) for term in variants):
                reasons.append('reported_symptom_not_matched')
    if plan['signatures'] and not signatures: reasons.append('diagnostic_message_not_matched')
    if plan['codes'] and not codes: reasons.append('diagnostic_code_not_matched')
    if plan['objects'] and not objects: reasons.append('technical_object_not_matched')
    diagnostic=bool(plan['signatures'] or plan['codes'] or plan['objects'] or plan['identifiers']
                    or re.search(r'http(?:/\d\.\d)?\s*[: -]?\s*[45]\d\d|bad request', normalize(query)))
    # UUIDs emitted by the synchronizer are diagnostic anchors too. Previously
    # they could retrieve the exact ticket but were then discarded as generic.
    matched_identifiers=[item for item in plan['identifiers'] if item in compact]
    if diagnostic and not (signatures or codes or objects or matched_identifiers): reasons.append('generic_error_only')
    if not diagnostic:
        terms=plan['terms']
        matched=[t for t in terms if t in compact or
                 (len(t)>3 and (t+'s') in compact) or
                 (t.endswith('s') and len(t)>4 and t[:-1] in compact) or
                 (t.endswith(('ao','oes')) and len(t)>5 and t[:-3] in compact) or
                 any(alias in compact for alias in ALIASES.get(t,()))]
        min_ratio = 0.5 if len(terms) >= 4 else 0.6
        if (len(terms) == 1 and not matched) or (len(terms) >= 2 and len(matched) < 2) or (len(matched) < 3 and len(matched)/max(1,len(terms)) < min_ratio):
            reasons.append('insufficient_subject_overlap')
    return {'applicable':not reasons,'applicability_reasons':reasons,'context_conflict':conflict,
            'matched_signatures':signatures,'matched_identifiers':matched_identifiers,
            'diagnostic_query':diagnostic}


def anchor_matches(plan,text):
    value=normalize(text)
    objects=[x for x in plan['objects'] if re.search(r'(?<![a-z0-9_])'+re.escape(x)+r'(?![a-z0-9_])',value)]
    codes=[]
    for c in plan['codes']:
        if c.startswith('rejection:'):
            n=c.split(':')[1]
            if re.search(r'(?:rejeicao\s*[: -]?\s*'+n+r'\b|\b'+n+r'\s*[-:]\s*rejeicao)',value): codes.append(c)
        elif c in value: codes.append(c)
    return objects,codes


def rank_rows(rows,plan,module='TODOS',top_k=5,service_hint=''):
    results=[]
    seen=set()
    for source in rows:
        r=dict(source)
        text=' '.join(str(r.get(k) or '') for k in ('title','problem','diagnosis','solution','content_for_embedding'))
        text += ' ' + ' '.join(r.get('search_keywords') or [])
        objects,codes=anchor_matches(plan,text)
        if plan['objects'] and not objects: continue
        if plan['codes'] and not codes: continue
        title=normalize(r.get('title'))
        body=normalize(text)
        compact_body=re.sub(r'\s+',' ',body)
        identifiers=[s for s in plan.get('identifiers',[]) if s in body]
        signatures=[s for s in plan.get('signatures',[]) if s in compact_body]
        matched=[w for w in plan['terms'] if w in body]
        lexical=float(r.get('lexical_rank') or 0)
        semantic=float(r.get('semantic_sim') or 0)
        protocol=plan['protocol'] is not None and r.get('source_external_id')==plan['protocol']
        if not objects and not codes and not identifiers and not signatures and not protocol and lexical<=0 and not matched: continue
        key=(r.get('source_type'),r.get('source_id') or r['id'])
        if key in seen: continue
        seen.add(key)
        score=100*len(objects)+70*len(codes)+200*protocol+min(lexical,1)*12+min(semantic,1)*5
        score+=180*len(identifiers)+350*len(signatures)
        def title_hit(w):
            root=w[:-1] if w.endswith('s') and len(w)>4 else w
            if root.startswith('cadastr'): root='cadastr'
            return root in title or any(a in title for a in ALIASES.get(w,()))
        title_terms=[w for w in plan['terms'] if title_hit(w)]
        score+=sum(12 if title_hit(w) else 1 for w in matched)
        # Plural/inflected title matches can be absent from exact substring hits.
        score+=sum(12 for w in title_terms if w not in matched)
        if plan.get('howto'):
            score += 60 * len(title_terms) / max(1, len(plan['terms']))
            if title.startswith('como ') and len(title_terms) >= 2:
                score += 65 if len(title_terms) == len(plan['terms']) else 25
            score+=12 if re.search(r'como |cadastr|procedimento|passo a passo',title) else 0
            score-=12 if title.startswith('dev -') else 0
            if r.get('source_type') in ('OFFICIAL_KB','MANUAL') and len(title_terms)>=2:
                score+=18
        if plan.get('product_creation'):
            # General creation must not rank margin, import, kits or permissions above the basic tutorial.
            if re.search(r'\b(?:cadastro de|cadastrar|cadastrando) produtos?\b', title):
                score += 35
            for qualifier in ('kit','grade','variacao','importar','exportar','imagem','foto','margem','custo','acesso','usuario','categoria','tipo'):
                if qualifier in title and qualifier not in normalize(plan['query']):
                    score -= 35
            for qualifier in ('kit','grade','foto'):
                if qualifier in normalize(plan['query']) and qualifier not in title:
                    score -= 60
        if plan.get('howto') and 'massa' in title and 'massa' not in normalize(plan['query']):
            score -= 50
        q=quality(r)
        score+=3 if q['evidence_status']=='official_document' else 0
        score-=8 if q['answer_use']=='investigation_only' else 0
        if plan.get('report'):
            score += 18 if r.get('source_type') == 'OFFICIAL_KB' else 0
            if report_topic_match(plan, title):
                score += 55 if r.get('source_type') == 'OFFICIAL_KB' else 12
            score -= 25 if re.search(r'\bdev\s*-\s*(?:err|mel|mod)\b', title) else 0
            score -= 5 if r.get('source_type') in ('VALIDATED_TICKET', 'PROBABLE_TICKET') else 0
        score+=4 if module!='TODOS' and normalize(module) in normalize(' '.join(r.get('modules') or [])) else 0
        metadata = r.get('metadata') if isinstance(r.get('metadata'), dict) else {}
        service = normalize(metadata.get('service_full') or '')
        hint = normalize(service_hint)
        if service and hint and (service == hint or service in hint or hint in service):
            score += 18
        r.update(q)
        r.update(retrieval_score=round(score,4),matched_objects=objects,matched_codes=codes,matched_terms=matched,
                 matched_identifiers=identifiers,matched_signatures=signatures,
                 retrieval_method='exact_signature' if objects or codes or identifiers or signatures or protocol else 'text_and_optional_vector',
                 combined_score=min(.99,max(.01,.5+score/200)),lexical_score=lexical,
                 semantic_sim=semantic,formatted_date=str(r.get('created_at_source') or '')[:10])
        results.append(r)
    return sorted(results,key=lambda r:(-r['retrieval_score'],r['id']))[:top_k]


def search(conn,query,module='TODOS',top_k=5,embedding=None,source_types=None,maintain_index=True,service_hint=''):
    if maintain_index:
        knowledge_index.ensure_coverage()
        curation.ensure_schema()
    # Nodes lack tenant IDs; never silently mix organizations.
    tenants=conn.execute('SELECT id FROM tenants ORDER BY id LIMIT 2').fetchall()
    if len(tenants)!=1: raise ValueError('Pesquisa exige corpus de uma única organização')
    plan=query_plan(query)
    top_k=max(1,min(int(top_k),50))
    if not plan['terms'] and not plan['objects'] and not plan['codes'] and plan['protocol'] is None: return []
    approved = approved_procedures(conn, query)
    if approved:
        return approved[:top_k]
    rows=[]
    # Broad OR search can exhaust the candidate budget with recent tickets.
    # Reserve a title lane requiring every meaningful concept for how-to queries.
    if plan['howto'] and len(plan['terms']) >= 2:
        clauses, parameters = [], []
        for term in plan['terms']:
            if term in ('pra', 'por', 'favor'):
                continue
            variants = ALIASES.get(term, (term,))
            roots = list(dict.fromkeys('cadastr' if v.startswith('cadastr') else
                                      v[:-1] if v.endswith('s') and len(v)>4 else v for v in variants))
            clauses.append('(' + ' OR '.join('lower(title) ILIKE %s' for _ in roots) + ')')
            parameters.extend('%' + root.replace('%', '\\%').replace('_', '\\_') + '%' for root in roots)
        if clauses:
            rows += conn.execute("SELECT *,0.0 AS lexical_rank FROM ticket_knowledge_nodes WHERE " +
                "source_type IN ('OFFICIAL_KB','MANUAL') AND " + ' AND '.join(clauses) +
                ' ORDER BY id DESC LIMIT 100', parameters).fetchall()
    expression="(COALESCE(title,'') || ' ' || COALESCE(content_for_embedding,''))"
    # Dedicated signature lane avoids generic product/synchronizer titles
    # filling the candidate budget before the actual error is considered.
    for needle in plan['identifiers'][:4]:
        escaped=needle.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')
        rows.extend(conn.execute(f"SELECT *,0.0 AS lexical_rank FROM ticket_knowledge_nodes WHERE {expression} ILIKE %s ORDER BY id DESC LIMIT 150",('%'+escaped+'%',)).fetchall())
    for signature in plan['signatures']:
        expr="to_tsvector('portuguese', coalesce(title,'') || ' ' || coalesce(problem,'') || ' ' || coalesce(solution,''))"
        rows.extend(conn.execute(f"SELECT *,0.0 AS lexical_rank FROM ticket_knowledge_nodes WHERE {expr} @@ phraseto_tsquery('portuguese',%s) ORDER BY id DESC LIMIT 150",(signature,)).fetchall())
    # One exact identifier yields candidates; full object/code requirements are checked after retrieval.
    needles=plan['objects']+plan['codes']
    if needles:
        for needle in needles[:6]:
            needle=needle.removeprefix('rejection:')
            escaped=needle.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')
            rows.extend(conn.execute(f"SELECT *,0.0 AS lexical_rank FROM ticket_knowledge_nodes WHERE {expression} ILIKE %s ORDER BY id DESC LIMIT 150",('%'+escaped+'%',)).fetchall())
    elif plan['protocol'] is not None:
        rows+=conn.execute('SELECT *,0.0 AS lexical_rank FROM ticket_knowledge_nodes WHERE source_external_id=%s ORDER BY id DESC LIMIT 30',(plan['protocol'],)).fetchall()
    else:
        expr="to_tsvector('portuguese', coalesce(title,'') || ' ' || coalesce(problem,'') || ' ' || coalesce(solution,''))"
        title_expr="to_tsvector('portuguese',coalesce(title,''))"
        fts_paired = plan.get('fts_paired') or plan['fts']
        rows+=conn.execute(f"""SELECT *,ts_rank_cd({title_expr},websearch_to_tsquery('portuguese',%s)) AS lexical_rank
             FROM ticket_knowledge_nodes WHERE {title_expr} @@ websearch_to_tsquery('portuguese',%s)
             ORDER BY lexical_rank DESC,id DESC LIMIT 150""",(plan['fts'],plan['fts'])).fetchall()
        # Dedicated official-document lane prevents repetitive tickets from
        # crowding short manuals/videos out of the candidate budget.
        official_limit = 200 if plan.get('report') else 80
        rows+=conn.execute(f"""SELECT *,ts_rank_cd({title_expr},websearch_to_tsquery('portuguese',%s)) AS lexical_rank
             FROM ticket_knowledge_nodes WHERE source_type IN ('OFFICIAL_KB','MANUAL')
             AND ({title_expr} @@ websearch_to_tsquery('portuguese',%s) OR {expr} @@ websearch_to_tsquery('portuguese',%s))
             ORDER BY lexical_rank DESC,id DESC LIMIT {official_limit}""",(plan['fts'],plan['fts'],fts_paired)).fetchall()
        rows+=conn.execute(f"""SELECT *,ts_rank_cd({expr},websearch_to_tsquery('portuguese',%s)) AS lexical_rank
             FROM ticket_knowledge_nodes WHERE {expr} @@ websearch_to_tsquery('portuguese',%s)
             ORDER BY lexical_rank DESC,id DESC LIMIT 150""",(fts_paired,fts_paired)).fetchall()
        if embedding and len(embedding) == VECTOR_INDEX_DIMENSIONS:
            vector='['+','.join(str(float(v)) for v in embedding)+']'
            rows.extend(conn.execute("""SELECT *,0.0 AS lexical_rank,
                1-(embedding::halfvec(3072) <=> %s::halfvec(3072)) AS semantic_sim
                FROM ticket_knowledge_nodes
                WHERE embedding_generated IS TRUE AND embedding IS NOT NULL
                ORDER BY embedding::halfvec(3072) <=> %s::halfvec(3072) LIMIT 30""",
                (vector, vector)).fetchall())
    # Corrected text has its own lexical lane because original imported text
    # and embeddings deliberately remain unchanged for provenance.
    rows.extend(conn.execute('''SELECT kn.*,0.0 AS lexical_rank FROM ticket_knowledge_nodes kn
        JOIN knowledge_curation c ON c.node_id=kn.id
        WHERE c.status!='excluded' AND to_tsvector('portuguese',
          CASE WHEN c.status='approved' THEN coalesce(c.title_override,'') || ' ' || coalesce(c.problem_override,'') || ' ' || coalesce(c.solution_override,'') ELSE '' END
          || ' ' || array_to_string(c.keywords,' '))
          @@ websearch_to_tsquery('portuguese',%s)
        ORDER BY c.updated_at DESC LIMIT 100''',(plan['fts'],)).fetchall())
    edits = curation.overlays(conn,{r['id'] for r in rows})
    rows = [curation.apply_overlay(r,edits.get(r['id'])) for r in rows
            if not edits.get(r['id']) or edits[r['id']]['status']!='excluded']
    # Avoid transferring the large vector into responses or model payloads.
    for r in rows: r.pop('embedding',None)
    if source_types:
        rows=[r for r in rows if r.get('source_type') in source_types]
    ranked=rank_rows(rows,plan,module,top_k,service_hint)
    historical_ids=[r.get('source_external_id') for r in ranked
                    if r.get('source_type') in ('VALIDATED_TICKET','PROBABLE_TICKET') and r.get('source_external_id')]
    if historical_ids:
        current={r['external_id']:r for r in conn.execute('''SELECT external_id,base_status,quality_tier,hard_block_reason
            FROM refined_tickets WHERE tenant_id=%s AND external_id=ANY(%s)''',
            (tenants[0]['id'],historical_ids)).fetchall()}
        for row in ranked:
            if row.get('source_type') not in ('VALIDATED_TICKET','PROBABLE_TICKET'): continue
            original=current.get(row.get('source_external_id'))
            if not original or original['base_status'] not in ('Closed','Resolved') or original['hard_block_reason'] or original['quality_tier'] not in ('GOLD','SILVER','REVIEW'):
                row['answer_use']='investigation_only'
                row['quality_flags'].append('historical_source_requires_review')
    kb_ids=[(r.get('metadata') or {}).get('external_id') for r in ranked if r.get('source_type')=='OFFICIAL_KB']
    if kb_ids:
        kb={r['external_id']:r for r in conn.execute('SELECT external_id,content_text,content_html FROM raw_kb_articles WHERE tenant_id=%s AND external_id=ANY(%s)',(tenants[0]['id'],kb_ids)).fetchall()}
        for row in ranked:
            if row.get('source_type')!='OFFICIAL_KB': continue
            if str(row.get('source_id') or '').startswith('canon:'):
                canonical_reference(conn, row)
                continue
            aid=(row.get('metadata') or {}).get('external_id');article=kb.get(aid)
            row['source_url']=f'https://helpdesk.alternativasistemas.com.br/kb/article/{aid}' if article else None
            # The original "Agrupado por Produto" screen was reported obsolete
            # by support on 2026-10-23. Keep the article visible for audit, but
            # do not turn its former navigation into a customer instruction.
            if str(aid) == '310538':
                row['quality_flags'].append('obsolete_screen_confirmed')
                row['answer_use']='investigation_only'
            if not article:
                row['quality_flags'].append('original_not_linked');row['answer_use']='investigation_only'
            else:
                # The node keeps a compact embedding excerpt, but generation
                # needs the complete official article (filters and caveats are
                # often documented after the first 2,000 characters).
                full_text = (article['content_text'] or '').strip()
                if len(full_text) > len((row.get('solution') or '').strip()):
                    row['solution'] = full_text[:12000]
                # Preserve tutorials as structured evidence even without a transcript.
                import html
                original = html.unescape(article['content_html'] or '')
                ids = re.findall(r'(?:youtube\.com/(?:embed/|watch\?v=)|youtu\.be/)([A-Za-z0-9_-]{11})(?![A-Za-z0-9_-])', original)
                row['videos'] = [{'title': row.get('title') or 'Tutorial',
                                  'url': 'https://youtu.be/' + video,
                                  'article_url': row['source_url']} for video in dict.fromkeys(ids)]
            if article and ('iframe' in (article['content_html'] or '').lower() or 'youtube.com' in (article['content_html'] or '').lower()) and len((article['content_text'] or '').strip())<900:
                row['quality_flags'].append('video_without_transcript');row['answer_use']='investigation_only'
                row['evidence_status']='video_reference_without_transcript'
    for row in ranked:
        gate=applicability(row,query)
        title = normalize(row.get('title') or '')
        if (plan.get('report') and row.get('source_type') in ('OFFICIAL_KB', 'MANUAL')
                and not report_topic_match(plan, row.get('title') or '')
                and any(report_topic_match(plan, name) for name in (
                    'Estatística de Vendas', 'Curva ABC de Produtos', 'Posição de Estoque',
                    'Projeção de Compras', 'Gerenciamento de Markup de Vendas',
                    'Comissão por Vendedor', 'Demonstrativo de Venda Analítico'))):
            gate = {**gate, 'applicable': False,
                    'applicability_reasons': gate['applicability_reasons'] + ['requested_report_not_matched']}
        if (plan.get('howto') and row.get('source_type') == 'OFFICIAL_KB'
                and re.match(r'^(?:dev\s*-\s*(?:err|mel|mod)|err\s*-)', title)):
            row['quality_flags'].append('change_or_error_article_not_usage_guide')
            row['answer_use'] = 'investigation_only'
        if (plan.get('report') and plan.get('howto') and
                row.get('source_type') in ('VALIDATED_TICKET', 'PROBABLE_TICKET')):
            solution = normalize(row.get('solution') or '')
            report_navigation = bool(re.search(
                r'\b(?:mais opcoes|relatorios\s*[>→-]|fica na tela|acesse|abra|selecione|filtre|filtrar)\b',
                solution))
            issue_ticket = bool(re.search(
                r'\b(?:erro|divergencia|corrigir|problema|nao funciona|lentidao)\b', title))
            if issue_ticket or not report_navigation or not report_topic_match(plan, title):
                row['quality_flags'].append('historical_report_not_usage_guide')
                row['answer_use'] = 'investigation_only'
        if (plan.get('report') and re.search(r'\bvendas? por produtos?\b', normalize(query))
                and row.get('source_type') == 'OFFICIAL_KB'
                and re.search(r'\b(?:markup|margem|rentabilidade)\b', normalize(row.get('title') or ''))):
            gate = {**gate, 'applicable': False,
                    'applicability_reasons': gate['applicability_reasons'] + ['requested_report_not_matched']}
        if (plan.get('report') and row.get('source_type') == 'OFFICIAL_KB'
                and row.get('videos') and report_topic_match(plan, row.get('title') or '')):
            reasons = [reason for reason in gate['applicability_reasons']
                       if reason != 'insufficient_subject_overlap']
            gate = {**gate, 'applicability_reasons': reasons, 'applicable': not reasons}
        row.update(gate)
        if any(reason!='integration_context_mismatch' for reason in gate['applicability_reasons']):
            row['answer_use']='investigation_only'
            row['quality_flags']+=gate['applicability_reasons']
    return ranked


def assessment(rows):
    if not rows:
        return dict(status='insufficient_evidence',message='Nenhuma fonte correspondente encontrada. Solicitar mensagem completa, tela e versão; registrar lacuna.',can_support_procedure=False)
    from support_policy import decide
    policy = decide('', rows)
    if not policy['can_propose_procedure']:
        return dict(status='references_require_validation',
                    message='Históricos e documentos sem aprovação aplicável são apoio à investigação; não autorizam passos operacionais.',
                    can_support_procedure=False,support_decision=policy)
    usable=[r for r in rows if r.get('answer_use')!='investigation_only' and r.get('applicable') is not False]
    if rows[0].get('answer_use')=='investigation_only' or rows[0].get('applicable') is False:
        return dict(status='primary_source_incomplete',
                    message='A fonte mais correspondente não sustenta um procedimento completo. Conferir original/transcrever vídeo antes de preencher passos; outros resultados são apenas apoio.',
                    can_support_procedure=False,quality_gaps=dict(Counter(f for r in rows for f in r.get('quality_flags',[]))))
    return dict(status='references_require_validation' if usable else 'investigation_only',
                message='Fontes encontradas não confirmam a causa no cliente. Conferir procedimento, versão e resultado.' if usable else 'Os registros não descrevem correção suficiente para orientar um procedimento.',
                can_support_procedure=bool(usable),quality_gaps=dict(Counter(f for r in rows for f in r.get('quality_flags',[]))))


def canonical_reference(conn, row):
    """Published human knowledge has its own original; it is not a raw KB article."""
    source_id = str(row.get('source_id') or '')
    if source_id.startswith('master-release:'):
        release_id = source_id.partition(':')[2]
        current = conn.execute("""SELECT id,version_label,status,approved_by FROM cortex_kb.master_release_notes
            WHERE id=%s AND status IN ('active','monitoring','fixed')""", (int(release_id),)).fetchone() if release_id.isdigit() else None
        if not current:
            row['answer_use'] = 'investigation_only'
            row['quality_flags'].append('master_release_not_current')
            return
        row['provenance'] = f"Aviso da versão {current['version_label']} aprovado por {current['approved_by']}"
        row['evidence_status'] = 'human_approved_procedure'
        row['answer_use'] = 'procedure_reference'
        return
    item_id = source_id.partition(':')[2]
    current = None
    if item_id.isdigit():
        current = conn.execute("""SELECT i.id,v.version_no,v.approved_by,v.applicability
            FROM cortex_kb.items i JOIN cortex_kb.versions v ON v.id=i.current_version_id
            WHERE i.id=%s AND i.status='active' AND v.status='published'""", (int(item_id),)).fetchone()
    metadata = row.get('metadata') or {}
    if not current or current['version_no'] != metadata.get('version_no'):
        row['answer_use'] = 'investigation_only'
        row['quality_flags'].append('canonical_version_not_current')
        return
    row['provenance'] = f"Procedimento aprovado por {current['approved_by']} · versão {current['version_no']}"
    row['images'] = metadata.get('images') or []
    row['evidence_status'] = 'human_approved_procedure'
    # This remains subject to the same error/topic applicability gate as all sources.
    row['answer_use'] = 'procedure_reference'


def approved_procedures(conn, query):
    """Dedicated lane: applicable current human knowledge precedes historical guesses."""
    plan = query_plan(query)
    rows = conn.execute("""SELECT *,0.0 AS lexical_rank FROM ticket_knowledge_nodes
        WHERE (source_id LIKE 'canon:%%' OR source_id LIKE 'master-release:%%') AND to_tsvector('portuguese',
        coalesce(title,'') || ' ' || coalesce(problem,'') || ' ' || coalesce(solution,''))
        @@ websearch_to_tsquery('portuguese',%s) ORDER BY updated_at DESC LIMIT 60""", (plan['fts'],)).fetchall()
    approved = []
    for row in rank_rows(rows,plan,top_k=10):
        row.pop('embedding',None)
        canonical_reference(conn,row)
        row.update(applicability(row,query))
        if row.get('applicable') and row.get('answer_use') == 'procedure_reference':
            approved.append(row)
    return approved[:3]
