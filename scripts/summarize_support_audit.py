"""Consolidate completed private replays; never infer human approval from a model."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import html
import json
from pathlib import Path
import statistics

from evaluate_real_support import OUT, ROOT, summary


def rate_text(value):
    n,d=value['numerator'],value['denominator']
    return f'{n}/{d} ({100*n/d:.1f}%)' if d else 'Não disponível (denominador 0)'


def consolidate(development, holdout, extension=None, retest=None, verified_holdout=None):
    folders=[development,holdout]+([extension] if extension else [])
    reports=[json.loads((folder/'report.json').read_text(encoding='utf-8')) for folder in folders]
    if reports[0]['manifest_sha256'] != reports[1]['manifest_sha256']:
        raise ValueError('Os conjuntos não pertencem ao mesmo manifesto congelado.')
    if extension:
        manifest=json.loads((OUT/'extension-manifest.json').read_text(encoding='utf-8'))
        if manifest['parent_manifest_sha256']!=reports[0]['manifest_sha256'] or manifest['sha256']!=reports[2]['manifest_sha256']:
            raise ValueError('Invalid extension lineage')
    cases=[row for report in reports for row in report['results']]
    record_folders={row['ticket']:folder for folder,report in zip(folders,reports) for row in report['results']}
    risk_observations=[]
    for folder,report in zip(folders,reports):
        for row in report['results']:
            critique=row.get('automated_review') or {}
            if row.get('eligible_for_primary_metrics') and (critique.get('verdict')=='incorrect' or critique.get('scores',{}).get('hallucinations')==0):
                risk_observations.append({'ticket':row['ticket'],'record':(folder/f"{row['ticket']}.json").resolve().as_posix(),
                    'method':'preliminary_automated_flag','rationale':critique.get('rationale'),
                    'status':'requires_technical_source_validation; a different retest does not prove a stable correction'})
    retest_history=[]
    if retest:
        retry=json.loads((retest/'report.json').read_text(encoding='utf-8'))
        for revised in retry['results']:
            index=next(i for i,row in enumerate(cases) if row['ticket']==revised['ticket'])
            previous_attempt=cases[index]
            retest_history.append({'ticket':revised['ticket'],'before_error':previous_attempt.get('error'),
                'after_error':revised.get('error'),'eligible_after':revised.get('eligible_for_primary_metrics')})
            # An infrastructure retest can recover execution, never override a failed opening gate.
            if revised.get('eligible_for_primary_metrics'):
                cases[index]=revised;record_folders[revised['ticket']]=retest
    if verified_holdout:
        verified=json.loads((verified_holdout/'report.json').read_text(encoding='utf-8'))
        if not extension or verified['manifest_sha256']!=reports[2]['manifest_sha256']:
            raise ValueError('Final reserved-set verification belongs to another extension')
        for revised in verified['results']:
            index=next(i for i,row in enumerate(cases) if row['ticket']==revised['ticket'])
            prior=cases[index]
            retest_history.append({'ticket':revised['ticket'],'kind':'final_reserved_verification',
                'before_error':prior.get('error'),'after_error':revised.get('error'),
                'before_verdict':prior.get('automated_review',{}).get('verdict'),
                'after_verdict':revised.get('automated_review',{}).get('verdict'),
                'eligible_after':revised.get('eligible_for_primary_metrics')})
            cases[index]=revised;record_folders[revised['ticket']]=verified_holdout
    if len({r['ticket'] for r in cases}) != len(cases):
        raise ValueError('Caso repetido entre desenvolvimento e conjunto reservado.')
    baseline=[json.loads(f.read_text(encoding='utf-8')) for folder in folders
              for f in sorted((folder/'baseline').glob('*.json'))]
    previous={r['ticket']:r for r in baseline}
    pairs=[(previous[r['ticket']],r) for r in cases if r['ticket'] in previous]
    joint=[(a,b) for a,b in pairs if a.get('eligible_for_primary_metrics') and b.get('eligible_for_primary_metrics')]
    causes=Counter(c for r in cases if r.get('eligible_for_primary_metrics')
                   for c in r.get('automated_review',{}).get('failure_causes',[]))
    measured=[r['latency_ms'] for r in cases if r.get('latency_ms',0)>0]
    final_hashes={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (ROOT/'scripts').glob('*.py')}
    changes={label:sorted(k for k,v in final_hashes.items() if report.get('code_sha256',{}).get(k)!=v)
             for label,report in zip(('development','holdout','extension'),reports)}
    result={'created_at':datetime.now(timezone.utc).isoformat(),'classification':'NÃO APTO PARA IMPLANTAÇÃO',
        'manifest_sha256':reports[0]['manifest_sha256'],'extension_manifest_sha256':reports[2]['manifest_sha256'] if extension else None,
        'selected_cases':len(cases),'infrastructure_retests':retest_history,
        'risk_observations':risk_observations,
        'input_eligible_cases':sum(r.get('opening_qualification',{}).get('status')=='passed' for r in cases),
        'generator_executions':sum(bool(r.get('context_sha256')) for r in cases),
        'generated_answers':sum(bool(r.get('response',{}).get('reply')) for r in cases),
        'summary':summary(cases),'development':reports[0]['summary'],'holdout':reports[1]['summary'],
        'paired_comparison':{'planned_pairs':len(pairs),'joint_eligible_pairs':len(joint),
            'before':summary([a for a,b in joint]),'after':summary([b for a,b in joint])},
        'failure_causes_automated':dict(causes),'cases_with_unknown_opening_author':sum(r.get('opening_authorship')=='unknown_in_import' for r in cases),
        'opening_exclusions':dict(Counter(r.get('opening_qualification',{}).get('reason') for r in cases if r.get('opening_qualification',{}).get('status')=='excluded')),
        'cases_with_omitted_attachments':sum(bool(r.get('attachments_omitted')) for r in cases),
        'replay_latency':{'measured':len(measured),'mean_ms':round(statistics.mean(measured)) if measured else None,
                          'median_ms':round(statistics.median(measured)) if measured else None,
                          'notice':'Inclui isolamento/cache do corpus e consultas; não mede SLA de produção.'},
        'final_code_sha256':final_hashes,'files_changed_since_run_start':changes,
        'blockers':['Aprovação e publicação dos primeiros procedimentos canônicos ainda pendentes.',
                    'Nenhuma certificação técnica humana dos resultados automáticos.',
                    'Autoria/anexos incompletos em parte do import e ausência de snapshots integrais do índice histórico.',
                    'Latência/disponibilidade e demais riscos precisam de validação em piloto autorizado.'],
        'cases':[]}
    lines=['# CORTEX — auditoria de suporte, 21/10/2026','',
        '**NÃO APTO PARA IMPLANTAÇÃO.** Há código e evidências disponíveis para revisão; a qualidade operacional ainda depende dos bloqueadores abaixo.','',
        'Nenhuma nota pública foi enviada, nenhum chamado real foi alterado e nenhum procedimento canônico foi publicado por esta avaliação.','',
        '## Execução e denominadores','',
        f"- Amostra congelada: {result['selected_cases']} chamados reais.",
        f"- Entradas elegíveis após auditoria: {result['input_eligible_cases']}; gerador efetivamente executado em {result['generator_executions']} casos; respostas capturadas em {result['generated_answers']}.",
        f"- Simulados: {len(cases)}; com resposta elegível: {result['summary']['eligible']}; excluídos/falhas: {result['summary']['excluded']}.",
        f"- Amostra inicial: {len(reports[0]['results'])} de desenvolvimento e {len(reports[1]['results'])} reservados; extensão independente: {len(reports[2]['results']) if extension else 0}.",
        f"- Revisões humanas: {result['summary']['human_reviewed']}; críticas automáticas válidas: {sum(r.get('automated_review',{}).get('status')=='reviewed' for r in cases)}.",
        f"- Abertura sem autoria verificável: {result['cases_with_unknown_opening_author']}; omissão registrada de anexos: {result['cases_with_omitted_attachments']}.",
        '', '## Observações automáticas preliminares','',
        '| Critério | Resultado |','| --- | --- |']
    for key,label in [('correct','Corretas segundo o avaliador automático'),('partial','Parcialmente corretas'),
                      ('incorrect','Incorretas'),('insufficient_evidence','Sem evidência suficiente'),
                      ('needs_technical_review','Precisam de revisão técnica')]:
        lines.append(f"| {label} | {rate_text(result['summary']['automated_preliminary_quality'][key])} |")
    for key,label in [('applicable_retrieval','Busca com pelo menos uma fonte marcada aplicável'),
                      ('without_approved_procedure','Sem procedimento aprovado para responder'),
                      ('no_dangerous_sql_detected','Sem SQL perigoso detectado pelo padrão objetivo')]:
        lines.append(f"| {label} | {rate_text(result['summary']['automated_only'][key])} |")
    for key,label in [('flagged_hallucinations','Alucinações sinalizadas pelo avaliador automático'),
                      ('safe_in_reviewer_assessment','Seguras na avaliação automática')]:
        lines.append(f"| {label} | {rate_text(result['summary']['automated_review_observations'][key])} |")
    lines+=['','Não interpretar linguagem convincente, aplicabilidade lexical ou ausência de SQL detectado como comprovação técnica. Uma crítica por modelo pode errar. As soluções históricas também aguardam validação técnica.','',
        '## Validação humana','',
        f"Acerto técnico humano: {rate_text(result['summary']['human_quality']['correct'])}. Nenhuma taxa humana foi inventada.",'',
        '## Comparação com a revisão anterior','',
        f"Foram planejados {len(pairs)} pares diversificados; {len(joint)} pares têm resposta elegível nas duas execuções. A tabela usa somente esses mesmos IDs.",'',
        '| Observação | Antes | Depois |','| --- | --- | --- |']
    for key,label in [('correct','Corretas (crítica automática)'),('partial','Parciais (crítica automática)'),('incorrect','Incorretas (crítica automática)'),('insufficient_evidence','Sem evidência (crítica automática)')]:
        lines.append(f"| {label} | {rate_text(result['paired_comparison']['before']['automated_preliminary_quality'][key])} | {rate_text(result['paired_comparison']['after']['automated_preliminary_quality'][key])} |")
    lines+=['','As avaliações do modelo têm denominadores próprios quando ele falha. A comparação não demonstra melhoria estatística nem substitui revisão humana.','',
        '## Causas e limitações','']
    lines += [f'- {cause}: {count} casos sinalizados automaticamente.' for cause,count in causes.most_common()]
    lines += [f'- Bloqueador: {b}' for b in result['blockers']]
    if risk_observations:
        lines+=['','### Divergências anteriores preservadas','',
            'Os itens abaixo foram sinalizados pelo avaliador em execuções anteriores elegíveis. Um reteste posterior com resposta diferente não comprova correção estável nem valida a solução histórica. Permanecem pendentes de revisão técnica das fontes.','']
        for risk in risk_observations:
            lines.append(f"- Chamado {risk['ticket']}: {risk['rationale']} [Execução sinalizada](<{risk['record']}>)")
    lines += [f'- Exclusão da entrada: {reason} — {count} casos.' for reason,count in result['opening_exclusions'].items()]
    lines += [f'- Retestes de infraestrutura preservados: {retest_history}. O avaliador passou a executar toda a geração antes da crítica automática, em processos separados.']
    lines += [f"- Tempos: {result['replay_latency']}. Medições zeradas por falha de preparação foram excluídas dessa estatística.",
        '- A entrada foi reconstruída a partir da primeira mensagem pública, sem assunto/status/resolução finais. Perfis atuais, fontes futuras e o próprio chamado/descendentes reconhecidos foram isolados.',
        '- A cronologia usa as datas das fontes e o conteúdo atualmente preservado, sem uma cópia de cada versão histórica do índice. Omissão de anexos limita a fidelidade visual.',
        '- A prioridade canônica e os fluxos legados receberam regressões próprias. O replay real usa o gerador de notas; não certifica ponta a ponta todas as interfaces.',
        f"- Arquivos alterados após o início de cada execução: {changes}. Os hashes iniciais e finais constam no JSON; esse limite impede alegar que todo o projeto foi certificado por uma única rodada.",
        '', '## Casos individuais','',
        '| Chamado | Categoria | Geração | Avaliação automática | Antes | Uso nas métricas | Evidência |','| --- | --- | --- | --- | --- | --- | --- |']
    for row in cases:
            folder=record_folders[row['ticket']]
            before=previous.get(row['ticket'])
            critique=row.get('automated_review') or {}
            verdict=critique.get('verdict') or critique.get('status') or 'não avaliado'
            old=(before or {}).get('automated_review',{}).get('verdict') or 'sem par/avaliação'
            link=(folder/f"{row['ticket']}.json").resolve().as_posix()
            eligibility='Sim' if row.get('eligible_for_primary_metrics') else 'Não: '+str(row.get('opening_qualification',{}).get('reason') or row.get('error') or 'sem resposta elegível')
            lines.append(f"| {row['ticket']} | {row['category']} | {row.get('response',{}).get('mode') or row.get('error')} | {verdict} | {old} | {eligibility} | [Registro completo](<{link}>) |")
            result['cases'].append({'ticket':row['ticket'],'category':row['category'],
                'record':link,'failure_diagnosis':row.get('error') or critique.get('failure_causes'),
                'eligible_for_primary_metrics':row.get('eligible_for_primary_metrics'),
                'opening_qualification':row.get('opening_qualification'),
                'correction':'Política compartilhada de fontes aprovadas; sem ajuste individual com o conjunto reservado.',
                'before_verdict':old,'after_verdict':verdict,
                'retest':{'before_mode':(before or {}).get('response',{}).get('mode'),
                          'after_mode':row.get('response',{}).get('mode')} if before else None})
    lines+=['','## Próximos passos para liberação','',
        'Revisar os casos e a resolução histórica com os técnicos, aprovar os procedimentos na prévia, validar o schema em homologação e executar um piloto interno restrito. A implantação em produção continua condicionada à autorização final do responsável.',
        '', 'Os arquivos são privados. A máscara automática não é garantia de anonimização completa; revisar antes de compartilhar.']
    return result,'\n'.join(lines)+'\n'


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--development',type=Path,default=OUT/'final-development')
    parser.add_argument('--holdout',type=Path,default=OUT/'final-holdout')
    parser.add_argument('--extension',type=Path,default=OUT/'clean-extension')
    parser.add_argument('--retest',type=Path,default=OUT/'final-retest')
    parser.add_argument('--verified-holdout',type=Path,default=OUT/'final-verified-holdout')
    args=parser.parse_args()
    result,markdown=consolidate(args.development,args.holdout,args.extension,args.retest,args.verified_holdout)
    (OUT/'final-report.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    (OUT/'final-report.md').write_text(markdown,encoding='utf-8')
    (OUT/'final-report.html').write_text('<!doctype html><meta charset="utf-8"><title>Auditoria CORTEX</title><style>body{font:15px system-ui;max-width:1150px;margin:30px auto}pre{white-space:pre-wrap;overflow-wrap:anywhere}</style><pre>'+html.escape(markdown)+'</pre>',encoding='utf-8')
    print(json.dumps({'classification':result['classification'],'simulated':result['summary']['attempted'],
                      'eligible':result['summary']['eligible'],'human_reviewed':result['summary']['human_reviewed']},ensure_ascii=True))


if __name__=='__main__':
    main()
