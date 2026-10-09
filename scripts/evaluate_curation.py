"""Local regression checks, not a measure of general diagnostic accuracy."""
import json
from unittest.mock import patch
import psycopg
from psycopg.rows import dict_row
from curation import POSTGRES_DSN, OUT, all_cards, search, save_card, review, validate_card

CASES = [
    ("qryXMLSTF ORA-00942 SFT_XML_NOTA_FISCAL_SAIDA", "safetech-xml-indisponivel"),
    ("não consigo buscar XML da nota na Safetech", "safetech-xml-indisponivel"),
    ("SFT_VW_FATURA_PEDIDO_ECOMMERCE consulta lenta", "safetech-consulta-faturamento"),
    ("DB Monitor SQL sem retorno notas não chegam", "safetech-consulta-faturamento"),
    ("ORA-00942 qryConsulta SPT_VW_FATURA_PEDIDO_ECOMMERCE", "safetech-ora00942-view"),
    ("duplicidade NF-e cStat AcBR envio síncrono", "nfe-duplicidade-retorno-sincrono"),
    ("como selecionar certificado digital na empresa", "nfe-configurar-certificado"),
    ("Empresas/Lojas aba NFe PIN", "nfe-configurar-certificado"),
    ("classe não registrada PLP", "nfe-classe-nao-registrada"),
    ("capicom regasm schemas", "nfe-classe-nao-registrada"),
]


def run():
    results=[]
    with psycopg.connect(POSTGRES_DSN,row_factory=dict_row) as conn:
        tenants=conn.execute("SELECT DISTINCT tenant_id FROM knowledge_curation.cards").fetchall()
        if len(tenants)!=1:
            raise ValueError("Regression fixture expects one pilot tenant")
        tenant=tenants[0]['tenant_id']
        cards=all_cards(conn,tenant)
        sources={r['source_key']:r for r in conn.execute("SELECT * FROM knowledge_curation.sources WHERE tenant_id=%s",(tenant,))}
        for card in cards:
            validate_card(card,sources)
        for query,expected in CASES:
            ranks=[c['key'] for _,c in search(cards,query)]
            results.append(dict(query=query,expected=expected,rank=ranks.index(expected)+1 if expected in ranks else None,top3=ranks[:3]))
        baseline=conn.execute("SELECT count(*) n FROM knowledge_curation.card_versions WHERE tenant_id=%s",(tenant,)).fetchone()['n']
        for c in cards:
            save_card(conn,tenant,{k:v for k,v in c.items() if k not in ('revision','status')})
        after=conn.execute("SELECT count(*) n FROM knowledge_curation.card_versions WHERE tenant_id=%s",(tenant,)).fetchone()['n']
        assert baseline==after, 'Idempotency failure'
        target=next(c for c in cards if c['kind']=='curated')
        try:
            review(conn,tenant,target['key'],'stale-revision','test','approved','Test only')
            raise AssertionError('Stale review accepted')
        except ValueError:
            pass
        candidate=next(c for c in cards if c['kind']=='candidate')
        try:
            review(conn,tenant,candidate['key'],candidate['revision'],'test','approved','Test only')
            raise AssertionError('Raw cluster approved')
        except ValueError:
            pass
        # Valid audit write tested inside a transaction rolled back below.
        with patch('curation.load_sources',return_value=sources):
            review(conn,tenant,target['key'],target['revision'],'regression-test','approved','Temporary transaction test')
        row=conn.execute("SELECT status FROM knowledge_curation.cards WHERE tenant_id=%s AND card_key=%s",(tenant,target['key'])).fetchone()
        assert row['status']=='approved'
        modified={k:v for k,v in target.items() if k not in ('revision','status')}
        modified['summary']+=' Test changed source/context.'
        save_card(conn,tenant,modified)
        row=conn.execute("SELECT status FROM knowledge_curation.cards WHERE tenant_id=%s AND card_key=%s",(tenant,target['key'])).fetchone()
        assert row['status']=='needs_review', 'Changed document retained approval'
        conn.rollback()
        other=all_cards(conn,'00000000-0000-0000-0000-000000000000')
        assert not other, 'Tenant isolation failure'
        report=dict(cases=results,top1=sum(r['rank']==1 for r in results),
                    top3=sum(r['rank'] is not None and r['rank']<=3 for r in results),total=len(results),
                    verified_cards=len(cards), verified_quotes=sum(len(c['claims']) for c in cards),
                    checks=['exact_evidence','idempotent_upsert','stale_revision_rejected','candidate_approval_blocked',
                            'review_audit_write','changed_card_requires_review','tenant_isolation','test_writes_rolled_back'],
                    limitation='Small curated regression set; not independent validation or general accuracy measurement')
        OUT.mkdir(parents=True,exist_ok=True)
        (OUT/'evaluation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(report,ensure_ascii=False,indent=2))
        if report['top3']!=len(results):
            raise SystemExit(1)


if __name__=='__main__':run()
