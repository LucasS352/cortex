"""Cross-module retrieval regression, with original KB IDs and negative controls."""
import json,time
import psycopg
from psycopg.rows import dict_row
from config import POSTGRES_DSN
from knowledge_access import search,assessment
from knowledge_map import OUT

CASES=[
 ('como cadastrar produtos e grades','OFFICIAL_KB',207376),
 ('como gerar boleto no fluxo de vendas','OFFICIAL_KB',198734),
 ('como lançar inventário','OFFICIAL_KB',211368),
 ('como cadastrar unidade de estoque','OFFICIAL_KB',207370),
 ('como cancelar uma venda de balcão no pdv','OFFICIAL_KB',212182),
 ('como fazer transferência de estoque','OFFICIAL_KB',212797),
 ('SFT_XML_NOTA_FISCAL_SAIDA','PROBABLE_TICKET',122703),
 ('ORA-00942 SPT_VW_FATURA_PEDIDO_ECOMMERCE','PROBABLE_TICKET',128797),
]

def main():
    results=[]
    with psycopg.connect(POSTGRES_DSN,row_factory=dict_row,options='-c default_transaction_read_only=on -c statement_timeout=30000') as conn:
        for query,kind,expected in CASES:
            start=time.perf_counter();rows=search(conn,query,top_k=10)
            matches=[i+1 for i,r in enumerate(rows) if (r.get('metadata') or {}).get('external_id',r.get('source_external_id'))==expected and r['source_type']==kind]
            results.append(dict(query=query,expected=expected,rank=matches[0] if matches else None,
                                milliseconds=round((time.perf_counter()-start)*1000),assessment=assessment(rows),
                                first_titles=[r['title'] for r in rows[:3]]))
        negatives=[]
        for query in ('','como eu faço','ORA-99999 SFT_OBJETO_NAO_DOCUMENTADO','palavradesconhecidaqzxw987'):
            rows=search(conn,query)
            negatives.append(dict(query=query,results=len(rows),status=assessment(rows)['status']))
            assert not rows,query
        # KB is now retrievable in the technician route and resolves to original article.
        from tech_evidence import retrieve,detail
        retrieved=retrieve(conn,'como cadastrar produtos e grades')
        assert retrieved['items'][0]['source_type']=='OFFICIAL_KB'
        evidence=detail(conn,retrieved['items'][0]['node_id'])
        assert evidence['original_status']=='available' and '/kb/article/207376' in evidence['source_url']
    report=dict(results=results,negative_controls=negatives,
                found_top5=sum(r['rank'] is not None and r['rank']<=5 for r in results),total=len(results),
                note='Known-case regression across modules; not independent estimate of answer accuracy. No external model calls.')
    OUT.mkdir(parents=True,exist_ok=True)
    (OUT/'search-evaluation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))
    if report['found_top5']!=len(results):raise SystemExit(1)

if __name__=='__main__':main()
