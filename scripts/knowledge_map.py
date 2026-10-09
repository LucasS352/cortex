"""Inventory every searchable node, reconcile originals, prioritize documentation gaps."""
import argparse
import hashlib
import html
import json
from collections import Counter,defaultdict
from datetime import datetime,timezone
from pathlib import Path
import psycopg
from psycopg.rows import dict_row
from config import POSTGRES_DSN,PROJECT_ROOT
from knowledge_access import quality,family,search,assessment,query_plan

OUT=PROJECT_ROOT/'output'/'knowledge-curation'
LABELS={'solution_missing_or_short':'Solução ausente ou curta','generic_resolution':'Resolução genérica',
        'version_not_recorded':'Versão não estruturada','screen_path_not_recorded':'Caminho da tela não estruturado',
        'unsafe_generalization_review':'Orientação que exige revisão','database_change_review':'Alteração de banco a revisar',
        'original_not_linked':'Fonte original não vinculada','video_without_transcript':'Vídeo sem transcrição detectável',
        'duplicate_text':'Texto repetido'}


def audit(conn):
    tenants=conn.execute('SELECT id FROM tenants LIMIT 2').fetchall()
    if len(tenants)!=1: raise ValueError('Mapa exige uma organização; não combinar bases implicitamente')
    tenant=tenants[0]['id']
    totals=conn.execute('''SELECT (SELECT count(*) FROM raw_tickets WHERE tenant_id=%s) raw_tickets,
        (SELECT count(*) FROM refined_tickets WHERE tenant_id=%s) refined_tickets,
        (SELECT count(*) FROM raw_kb_articles WHERE tenant_id=%s) articles''',(tenant,tenant,tenant)).fetchone()
    tickets={r['external_id'] for r in conn.execute('SELECT external_id FROM raw_tickets WHERE tenant_id=%s',(tenant,))}
    articles={r['external_id']:r for r in conn.execute('SELECT external_id,content_text,content_html FROM raw_kb_articles WHERE tenant_id=%s',(tenant,))}
    seen=Counter(); counts=Counter(); source_types=Counter(); categories=defaultdict(lambda:dict(nodes=0,tickets=0,official=0,manuals=0,flags=Counter(),examples=[]))
    catalog=[]; ticket_index=set(); article_index=set()
    with conn.cursor(name='coverage_nodes') as cur:
        cur.execute('''SELECT id,source_type,source_id,source_external_id,title,problem,solution,content_for_embedding,
                     modules,manual_category,screen_path,version_reference,metadata FROM ticket_knowledge_nodes ORDER BY id''')
        for row in cur:
            q=quality(row); flags=q['quality_flags']; typ=row['source_type']; external=row['source_external_id']
            url=None
            if typ=='OFFICIAL_KB':
                external=(row['metadata'] or {}).get('external_id')
                article=articles.get(external)
                if not article: flags.append('original_not_linked')
                else:
                    article_index.add(external)
                    if ('iframe' in (article['content_html'] or '').lower() or 'youtube.com' in (article['content_html'] or '').lower()) and len((article['content_text'] or '').strip())<900:
                        flags.append('video_without_transcript')
                    url=f'https://helpdesk.alternativasistemas.com.br/kb/article/{external}'
            elif typ=='MANUAL':
                root=(PROJECT_ROOT/'Instrucoes_master').resolve()
                relative=(row['source_id'] or '').removeprefix('MANUAL::')
                candidate=(root/relative).resolve()
                if not candidate.is_relative_to(root) or not candidate.is_file():
                    flags.append('original_not_linked')
            else:
                if external not in tickets: flags.append('original_not_linked')
                else:
                    ticket_index.add(external)
                    url=f'https://helpdesk.alternativasistemas.com.br/Ticket/Edit/{external}'
            fingerprint_text=((row['problem'] or '')+'\n'+(row['solution'] or '')).strip()
            # Empty/video-only bodies are missing text, not evidence of duplicates.
            if len(fingerprint_text)>80:
                fingerprint=hashlib.sha256(fingerprint_text.encode()).hexdigest()
                if seen[fingerprint]: flags.append('duplicate_text')
                seen[fingerprint]+=1
            counts.update(flags);source_types[typ]+=1
            groups=family(row)
            record=dict(id=row['id'],title=row['title'],source_type=typ,source_external_id=external,url=url,
                        families=groups,quality_flags=flags,answer_use=q['answer_use'])
            catalog.append(record)
            for group in groups:
                g=categories[group];g['nodes']+=1;g['official']+=int(typ=='OFFICIAL_KB');g['manuals']+=int(typ=='MANUAL');g['tickets']+=int(typ not in ('MANUAL','OFFICIAL_KB'));g['flags'].update(flags)
                severity=sum(f in flags for f in ('solution_missing_or_short','generic_resolution','video_without_transcript','original_not_linked','unsafe_generalization_review'))
                if severity: g['examples'].append(dict(record,severity=severity))
    unindexed=conn.execute('''SELECT coalesce(service_first_level,'Sem serviço') service,count(*) total
        FROM refined_tickets r WHERE tenant_id=%s AND NOT EXISTS(SELECT 1 FROM ticket_knowledge_nodes n
        WHERE n.source_type IN ('VALIDATED_TICKET','PROBABLE_TICKET') AND n.source_external_id=r.external_id)
        GROUP BY 1 ORDER BY total DESC''',(tenant,)).fetchall()
    backlog=[]
    for name,g in categories.items():
        gaps=g['flags'];g['examples']=sorted(g['examples'],key=lambda x:(-x['severity'],x['id']))[:8]
        priority=gaps['generic_resolution']*3+gaps['solution_missing_or_short']*2+gaps['video_without_transcript']*4+gaps['unsafe_generalization_review']*5
        backlog.append(dict(topic=name,priority=priority,**g))
    backlog.sort(key=lambda x:(-x['priority'],-x['nodes']))
    return dict(generated_at=datetime.now(timezone.utc).isoformat(),tenant=str(tenant),totals=dict(totals,nodes=len(catalog),
                indexed_tickets=len(ticket_index),indexed_articles=len(article_index),unindexed_tickets=len(tickets-ticket_index)),
                sources=dict(source_types),gaps=dict(counts),topics=backlog,unindexed_by_service=unindexed,
                labels=LABELS,catalog=catalog,
                limits=['Indicadores heurísticos, não validação técnica de soluções.',
                        'Famílias inferidas a partir de título e metadados; um registro pode estar em várias.',
                        'Ausência de versão/tela no campo estruturado não prova ausência no texto ou anexo.',
                        'Vínculo de manual verifica existência do arquivo local; não valida semanticamente sua extração.',
                        'Chamados fora do índice podem ser ruído ou exclusões intencionais: não são lacunas automaticamente.',
                        'Vídeos são candidatos a transcrição pelo tamanho do texto e presença de embed; conferir manualmente.'])


def render(report):
    e=html.escape
    rows=[]
    for t in report['topics']:
        examples=''.join('<li>'+('<a target="_blank" rel="noopener" href="'+e(r['url'])+'">'+e(r['title'] or '')+'</a>' if r['url'] else e(r['title'] or ''))+'<small> '+e(', '.join(LABELS[f] for f in r['quality_flags'] if f not in ('version_not_recorded','screen_path_not_recorded')))+' </small></li>' for r in t['examples'])
        gaps=' · '.join(f'{e(LABELS[k])}: {v:,}' for k,v in t['flags'].items() if k not in ('version_not_recorded','screen_path_not_recorded'))
        rows.append(f'<section class="topic"><h2>{e(t["topic"])}</h2><p>{t["nodes"]:,} registros · {t["official"]:,} artigos · {t["manuals"]:,} manuais · {t["tickets"]:,} chamados</p><p>{gaps}</p><details><summary>Exemplos que precisam de documentação ou revisão</summary><ul>{examples}</ul></details></section>')
    metrics=''.join(f'<div><b>{v:,}</b><span>{e(k)}</span></div>' for k,v in [('Chamados totais',report['totals']['raw_tickets']),('Registros pesquisáveis',report['totals']['nodes']),('Chamados representados no índice',report['totals']['indexed_tickets']),('Artigos representados',report['totals']['indexed_articles'])])
    limits=''.join('<li>'+e(x)+'</li>' for x in report['limits'])
    services=''.join(f'<tr><td>{e(r["service"])}</td><td>{r["total"]:,}</td></tr>' for r in report['unindexed_by_service'][:15])
    return '''<!doctype html><html lang="pt-BR"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Mapa do conhecimento · Alternativa</title>
<style>body{font:16px Segoe UI,sans-serif;margin:0;background:#eff4f6;color:#163541}header{background:#103441;color:white;padding:35px 6vw}h1{font-size:32px}main{padding:24px 6vw;max-width:1350px;margin:auto}.metrics{display:flex;gap:35px;flex-wrap:wrap}.metrics b{display:block;font-size:28px}.metrics span{font-size:13px}.topic{background:white;border:1px solid #d2e0e5;padding:22px;border-radius:10px;margin:16px 0}h2{font-size:22px;margin:0 0 10px}p,li{line-height:1.6}summary{cursor:pointer;color:#067b76}small{display:block;color:#667b83}a{color:#08726f}input{box-sizing:border-box;width:100%;padding:14px;font:inherit;border:1px solid #adc5cc;border-radius:8px}.limits{background:#fff3d9;padding:20px 35px;border-radius:8px}table{width:100%;border-collapse:collapse}td{padding:8px;border-bottom:1px solid #d2e0e5}td:last-child{text-align:right}.note{color:#bed5dd}</style>
<header><p>DESKCORTEX / COBERTURA DA BASE</p><h1>O que sabemos. O que falta registrar.</h1><p class="note">Inventário completo dos registros pesquisáveis e reconciliação com chamados e artigos originais.</p><div class="metrics">'''+metrics+'''</div></header><main><p><a href="index.html">Abrir fichas do piloto</a> · <a href="coverage.json">Inventário JSON</a> · Atualizado em '''+e(report['generated_at'])+'''</p><p>Prioridade considera frequência de resolução genérica, solução ausente, vídeos e orientações a revisar. A pontuação serve para ordenar trabalho de documentação; não mede confiança.</p><input id="filter" placeholder="Filtrar assunto: produtos, financeiro, integrações…" aria-label="Filtrar assuntos"><div id="topics">'''+''.join(rows)+'''</div><h2>Chamados ainda fora do índice, por serviço</h2><p>Essa lista ajuda a auditar a seleção da ingestão; nem todo chamado deve virar conhecimento.</p><table>'''+services+'''</table><h2>Como interpretar</h2><ul class="limits">'''+limits+'''</ul></main><script>document.getElementById('filter').addEventListener('input',function(){let q=this.value.toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g,'');for(let s of document.querySelectorAll('.topic'))s.hidden=!s.textContent.toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g,'').includes(q)})</script></html>'''


def main():
    p=argparse.ArgumentParser();p.add_argument('--query');args=p.parse_args()
    with psycopg.connect(POSTGRES_DSN,row_factory=dict_row,options='-c default_transaction_read_only=on -c statement_timeout=120000') as conn:
        if args.query:
            rows=search(conn,args.query,top_k=8)
            print(json.dumps(dict(plan=query_plan(args.query),assessment=assessment(rows),results=[{k:r.get(k) for k in ('id','title','source_type','source_external_id','retrieval_method','retrieval_score','matched_objects','matched_codes','answer_use','quality_flags')} for r in rows]),ensure_ascii=False,indent=2))
        else:
            report=audit(conn);OUT.mkdir(parents=True,exist_ok=True)
            (OUT/'coverage.json').write_text(json.dumps(report,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
            (OUT/'coverage.html').write_text(render(report),encoding='utf-8')
            print(json.dumps({k:v for k,v in report.items() if k not in ('catalog','topics','unindexed_by_service')},ensure_ascii=False,indent=2,default=str))
            print(OUT/'coverage.html')


if __name__=='__main__':main()
