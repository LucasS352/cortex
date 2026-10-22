"""Requalify private replay records against a strict blind-opening gate.

Preserves generated responses and earlier eligibility; contaminated or
unreconstructable inputs are excluded, never silently replaced with a solution.
"""
import argparse
import json
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from config import POSTGRES_DSN
from evaluate_real_support import OUT, digest, original_case, summary


def qualify(conn,row):
    original_eligibility=row.get('eligibility_before_opening_audit',row.get('eligible_for_primary_metrics',False))
    row['eligibility_before_opening_audit']=original_eligibility
    try:
        _,opening,_,_,_=original_case(conn,row['ticket'])
        if row.get('context_sha256') and row['context_sha256']!=digest(opening):
            raise ValueError('Opening source changed after generation')
        gate={'status':'passed','reason':None}
    except ValueError as error:
        gate={'status':'excluded','reason':str(error)}
    row['opening_qualification']=gate
    row['eligible_for_primary_metrics']=bool(original_eligibility and gate['status']=='passed')
    return row


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('folders',type=Path,nargs='+')
    args=parser.parse_args();total=0;excluded=0
    with psycopg.connect(POSTGRES_DSN,row_factory=dict_row,
            options='-c default_transaction_read_only=on -c statement_timeout=30000') as conn:
        for folder in args.folders:
            if not folder.resolve().is_relative_to(OUT.resolve()):
                raise SystemExit('Opening audit is confined to private evaluation outputs.')
            report_path=folder/'report.json'
            if not report_path.is_file():
                raise SystemExit('Evaluation must finish before qualification: '+str(folder))
            for path in folder.rglob('*.json'):
                if not path.stem.isdigit():
                    continue
                row=qualify(conn,json.loads(path.read_text(encoding='utf-8')))
                path.write_text(json.dumps(row,ensure_ascii=False,indent=2),encoding='utf-8')
            report=json.loads(report_path.read_text(encoding='utf-8'))
            rows=[json.loads((folder/f"{r['ticket']}.json").read_text(encoding='utf-8')) for r in report['results']]
            report['results']=rows;report['summary']=summary(rows)
            if 'comparison' in report:
                baseline=[json.loads(path.read_text(encoding='utf-8')) for path in (folder/'baseline').glob('*.json')]
                report['comparison']['baseline']=summary(baseline)
                report['comparison']['candidate']=summary(rows)
            report['qualification_policy']='First public text at ticket creation (5 minute tolerance); no retrospective/resolved service logs; generation input hash unchanged.'
            report_path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
            (folder/'summary.json').write_text(json.dumps(report['summary'],ensure_ascii=False,indent=2),encoding='utf-8')
            total+=len(rows);excluded+=sum(r['opening_qualification']['status']=='excluded' for r in rows)
    print(json.dumps({'records':total,'excluded_by_opening_gate':excluded}))


if __name__=='__main__':
    main()
