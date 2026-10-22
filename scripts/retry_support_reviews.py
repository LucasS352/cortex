"""Retry missing automated critiques, without rerunning or relabeling valid Cortex answers."""
import argparse
from concurrent.futures import ProcessPoolExecutor
import json
from pathlib import Path

from evaluate_real_support import OUT, review_result, summary


def critique(plan):
    path,row=plan
    # Isolated reviewer process only. Production generation keeps its 25 s bound.
    import llm_client
    llm_client.DEADLINE_SECONDS=60
    result=review_result(row)
    result['reviewer_deadline_seconds']=60
    result['generation_rerun']=False
    return path,result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('folders',type=Path,nargs='+')
    args=parser.parse_args();plans=[]
    for folder in args.folders:
        if not folder.resolve().is_relative_to(OUT.resolve()):
            raise SystemExit('Reviewer retries are confined to private audit outputs.')
        for path in folder.rglob('*.json'):
            if not path.stem.isdigit():continue
            row=json.loads(path.read_text(encoding='utf-8'))
            if row.get('eligible_for_primary_metrics') and (row.get('automated_review') or {}).get('status') in ('unavailable','invalid_reviewer_output'):
                plans.append((str(path),row))
    reviewed=0
    # A fresh process for each critique prevents a timeout of the reviewer from
    # suppressing all subsequent critique attempts via shared circuit state.
    with ProcessPoolExecutor(max_workers=2,max_tasks_per_child=1) as pool:
        for path,result in pool.map(critique,plans):
            target=Path(path);row=json.loads(target.read_text(encoding='utf-8'))
            row.setdefault('automated_review_attempts',[]).append(row.get('automated_review'))
            row['automated_review']=result
            target.write_text(json.dumps(row,ensure_ascii=False,indent=2),encoding='utf-8')
            reviewed+=result.get('status')=='reviewed'
            print(target.parent.name,target.stem,result.get('status'),flush=True)
            if 'HTTP 429' in str(result.get('error') or '') or any(word in str(result.get('error') or '') for word in ('Créditos','Chave','negado')):
                raise SystemExit('Reviewer provider limit: valid answers and previous critiques preserved; no quality label invented.')
    for folder in args.folders:
        report_path=folder/'report.json'
        report=json.loads(report_path.read_text(encoding='utf-8'))
        report['results']=[json.loads((folder/f"{r['ticket']}.json").read_text(encoding='utf-8')) for r in report['results']]
        report['summary']=summary(report['results'])
        report['review_retry_policy']='Only missing/invalid preliminary critiques; isolated 60 s reviewer; original generations preserved.'
        report_path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        (folder/'summary.json').write_text(json.dumps(report['summary'],ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'retried':len(plans),'reviewed':reviewed}))


if __name__=='__main__':
    main()
