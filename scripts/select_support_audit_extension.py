"""Freeze additional genuine opening reports, excluding retrospective service logs."""
import argparse
from datetime import datetime, timezone
import json

import psycopg
from psycopg.rows import dict_row

from config import POSTGRES_DSN
from evaluate_real_support import BUCKETS, OUT, digest, opening_exclusion, original_case


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--limit',type=int,default=30)
    args=parser.parse_args()
    target=OUT/'extension-manifest.json'
    if target.exists():
        raise SystemExit('Extension sample already frozen.')
    parent=json.loads((OUT/'manifest.json').read_text(encoding='utf-8'))
    excluded={e['ticket'] for e in parent['cases']}
    selected=[];counts={};inspected=0
    with psycopg.connect(POSTGRES_DSN,row_factory=dict_row,
            options='-c default_transaction_read_only=on -c statement_timeout=30000') as conn:
        for category,pattern in BUCKETS.items():
            rows=conn.execute('''SELECT external_id,created_date FROM refined_tickets
                WHERE (coalesce(subject,'') || ' ' || coalesce(problem_statement,'')) ~* %s
                  AND base_status IN ('Closed','Resolved') AND quality_tier IN ('GOLD','SILVER')
                  AND coalesce(hard_block_reason,'')='' AND length(coalesce(solution_statement,''))>=60
                  AND external_id<>ALL(%s) ORDER BY created_date DESC,external_id DESC LIMIT 250''',
                  (pattern,list(excluded))).fetchall()
            accepted=0
            for row in rows:
                inspected+=1
                if row['external_id'] in excluded:
                    continue
                try:
                    _,opening,as_of,_,_=original_case(conn,row['external_id'])
                except ValueError:
                    continue
                text=opening['actions'][0]['description']
                # Require an actual reported difficulty/question, not a closure announcement.
                import re
                from knowledge_access import normalize
                if not re.search(r'erro|rejei|problema|como |nao |preciso|gostaria|poderia|ajuda|dificuldade|falha|duvida|solicit',normalize(text)):
                    continue
                selected.append({'ticket':row['external_id'],'category':category,
                    'historical_validation':'pending_human_review','created_at':str(row['created_date']),
                    'split':'holdout' if len(selected)%2==0 else 'development'})
                excluded.add(row['external_id']);accepted+=1
                if accepted>=3 or len(selected)>=args.limit:
                    break
            counts[category]=accepted
            if len(selected)>=args.limit:
                break
    manifest={'version':2,'created_at':datetime.now(timezone.utc).isoformat(),
        'parent_manifest_sha256':parent['sha256'],'selection':'Diversified real openings, at ticket creation, no retrospective resolution log.',
        'inspected_candidates':inspected,'accepted_by_category':counts,'cases':selected,'sha256':digest(selected)}
    target.write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'selected':len(selected),'inspected':inspected,'categories':counts},ensure_ascii=True))


if __name__=='__main__':
    main()
