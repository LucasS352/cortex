"""Shared, explainable retrieval. Similarity is never diagnostic confidence."""
import re
import unicodedata
from difflib import get_close_matches
import knowledge_curation as curation
import knowledge_index
from collections import Counter

STOP = set('a o os as de do da dos das e em um uma para por no na nos nas com como que eu meu minha esta estou erro error problema fazer faco fica nao ao pelo pela mais esse essa tem la favor ajuda consigo preciso quero saber bom dia ola ocorre qual onde master erp sistema contexto mensagem atual integradores ver quantas quanto quantos quanta tive teve tinha ter isso estes estas aquele aquela aquilo consegue pode podemos poderia sobre alguem gente aqui ali assim gostaria informacao informacoes duvida dando tentando tentar tentando esta ta tao foi fui iria'.split())
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
}
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
    'Infraestrutura': ('firebird','sql','oracle','backup','servidor','instalacao'),
}


def normalize(s):
    return ''.join(c for c in unicodedata.normalize('NFKD',str(s or '').lower()) if not unicodedata.combining(c))


def query_plan(query):
    text=normalize(query)
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
    # Quoted OR operands parsed by PostgreSQL; all user text stays parameterized.
    fts=' OR '.join('"'+w+'"' for w in expansions)
    protocol=re.fullmatch(r'#?(\d{1,9})',query.strip())
    return dict(query=query,product_creation=product_creation,objects=objects,codes=sorted(set(codes)),identifiers=identifiers,signatures=signatures,terms=words,expanded_terms=expansions,
                fts=fts,protocol=int(protocol[1]) if protocol else None,
                howto=bool(re.search(r'\b(como|onde|criar|cadastrar|configurar|procedimento|passo a passo)\b',text)))


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
        for variants in (('importar','importacao'), ('exportar','exportacao'),
                         ('cancelar','cancelamento'), ('transferencia','transferir'),
                         ('kit','kits'), ('grade','grades','variacao'), ('foto','fotos','imagem','imagens')):
            if any(re.search(r'\b'+term+r'\b', normalize(query)) for term in variants):
                if not any(re.search(r'\b'+term+r'\b', purpose) for term in variants):
                    reasons.append('requested_operation_not_matched')
    if conflict: reasons.append('integration_context_mismatch')
    # A shared noun is insufficient when the customer's stated failure differs.
    # E.g. "IE inválida" must not inherit a procedure for "contribuinte isento".
    symptom=normalize(' '.join(str(row.get(k) or '') for k in ('title','problem','diagnosis')))
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
                 (t.endswith('s') and len(t)>4 and t[:-1] in compact) or
                 any(alias in compact for alias in ALIASES.get(t,()))]
        if len(matched)<2 or len(matched)/max(1,len(terms))<0.6:
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


def rank_rows(rows,plan,module='TODOS',top_k=5):
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
        score+=4 if module!='TODOS' and normalize(module) in normalize(' '.join(r.get('modules') or [])) else 0
        r.update(q)
        r.update(retrieval_score=round(score,4),matched_objects=objects,matched_codes=codes,matched_terms=matched,
                 matched_identifiers=identifiers,matched_signatures=signatures,
                 retrieval_method='exact_signature' if objects or codes or identifiers or signatures or protocol else 'text_and_optional_vector',
                 combined_score=min(.99,max(.01,.5+score/200)),lexical_score=lexical,
                 semantic_sim=semantic,formatted_date=str(r.get('created_at_source') or '')[:10])
        results.append(r)
    return sorted(results,key=lambda r:(-r['retrieval_score'],r['id']))[:top_k]


def search(conn,query,module='TODOS',top_k=5,embedding=None,source_types=None,maintain_index=True):
    if maintain_index:
        knowledge_index.ensure_coverage()
        curation.ensure_schema()
    # Nodes lack tenant IDs; never silently mix organizations.
    tenants=conn.execute('SELECT id FROM tenants ORDER BY id LIMIT 2').fetchall()
    if len(tenants)!=1: raise ValueError('Pesquisa exige corpus de uma única organização')
    plan=query_plan(query)
    top_k=max(1,min(int(top_k),50))
    if not plan['terms'] and not plan['objects'] and not plan['codes'] and plan['protocol'] is None: return []
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
        rows+=conn.execute(f"""SELECT *,ts_rank_cd({title_expr},websearch_to_tsquery('portuguese',%s)) AS lexical_rank
             FROM ticket_knowledge_nodes WHERE {title_expr} @@ websearch_to_tsquery('portuguese',%s)
             ORDER BY lexical_rank DESC,id DESC LIMIT 100""",(plan['fts'],plan['fts'])).fetchall()
        # Dedicated official-document lane prevents repetitive tickets from
        # crowding short manuals/videos out of the candidate budget.
        rows+=conn.execute(f"""SELECT *,ts_rank_cd({title_expr},websearch_to_tsquery('portuguese',%s)) AS lexical_rank
             FROM ticket_knowledge_nodes WHERE source_type IN ('OFFICIAL_KB','MANUAL')
             AND {title_expr} @@ websearch_to_tsquery('portuguese',%s)
             ORDER BY lexical_rank DESC,id DESC LIMIT 60""",(plan['fts'],plan['fts'])).fetchall()
        rows+=conn.execute(f"""SELECT *,ts_rank_cd({expr},websearch_to_tsquery('portuguese',%s)) AS lexical_rank
             FROM ticket_knowledge_nodes WHERE {expr} @@ websearch_to_tsquery('portuguese',%s)
             ORDER BY lexical_rank DESC,id DESC LIMIT 100""",(plan['fts'],plan['fts'])).fetchall()
        if embedding:
            vector='['+','.join(str(float(v)) for v in embedding)+']'
            rows.extend(conn.execute("""SELECT *,0.0 AS lexical_rank,1-(embedding <=> %s::vector) AS semantic_sim
                FROM ticket_knowledge_nodes WHERE embedding_generated AND embedding IS NOT NULL
                ORDER BY embedding <=> %s::vector LIMIT 30""",(vector,vector)).fetchall())
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
    ranked=rank_rows(rows,plan,module,top_k)
    historical_ids=[r.get('source_external_id') for r in ranked
                    if r.get('source_type') in ('VALIDATED_TICKET','PROBABLE_TICKET') and r.get('source_external_id')]
    if historical_ids:
        current={r['external_id']:r for r in conn.execute('''SELECT external_id,base_status,quality_tier,hard_block_reason
            FROM refined_tickets WHERE tenant_id=%s AND external_id=ANY(%s)''',
            (tenants[0]['id'],historical_ids)).fetchall()}
        for row in ranked:
            if row.get('source_type') not in ('VALIDATED_TICKET','PROBABLE_TICKET'): continue
            original=current.get(row.get('source_external_id'))
            if not original or original['base_status'] not in ('Closed','Resolved') or original['hard_block_reason'] or original['quality_tier'] not in ('GOLD','SILVER'):
                row['answer_use']='investigation_only'
                row['quality_flags'].append('historical_source_requires_review')
    kb_ids=[(r.get('metadata') or {}).get('external_id') for r in ranked if r.get('source_type')=='OFFICIAL_KB']
    if kb_ids:
        kb={r['external_id']:r for r in conn.execute('SELECT external_id,content_text,content_html FROM raw_kb_articles WHERE tenant_id=%s AND external_id=ANY(%s)',(tenants[0]['id'],kb_ids)).fetchall()}
        for row in ranked:
            if row.get('source_type')!='OFFICIAL_KB': continue
            aid=(row.get('metadata') or {}).get('external_id');article=kb.get(aid)
            row['source_url']=f'https://helpdesk.alternativasistemas.com.br/kb/article/{aid}' if article else None
            if not article:
                row['quality_flags'].append('original_not_linked');row['answer_use']='investigation_only'
            else:
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
        row.update(gate)
        if any(reason!='integration_context_mismatch' for reason in gate['applicability_reasons']):
            row['answer_use']='investigation_only'
            row['quality_flags']+=gate['applicability_reasons']
    return ranked


def assessment(rows):
    if not rows:
        return dict(status='insufficient_evidence',message='Nenhuma fonte correspondente encontrada. Solicitar mensagem completa, tela e versão; registrar lacuna.',can_support_procedure=False)
    usable=[r for r in rows if r.get('answer_use')!='investigation_only' and r.get('applicable') is not False]
    if rows[0].get('answer_use')=='investigation_only' or rows[0].get('applicable') is False:
        return dict(status='primary_source_incomplete',
                    message='A fonte mais correspondente não sustenta um procedimento completo. Conferir original/transcrever vídeo antes de preencher passos; outros resultados são apenas apoio.',
                    can_support_procedure=False,quality_gaps=dict(Counter(f for r in rows for f in r.get('quality_flags',[]))))
    return dict(status='references_require_validation' if usable else 'investigation_only',
                message='Fontes encontradas não confirmam a causa no cliente. Conferir procedimento, versão e resultado.' if usable else 'Os registros não descrevem correção suficiente para orientar um procedimento.',
                can_support_procedure=bool(usable),quality_gaps=dict(Counter(f for r in rows for f in r.get('quality_flags',[]))))
