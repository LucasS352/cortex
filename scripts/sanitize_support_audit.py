"""Private audit export hygiene; never writes the corpus or changes ticket IDs."""
import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import re

import psycopg
from config import POSTGRES_DSN
from evaluate_real_support import OUT, redact

TEXT_FIELDS={'original_question','context_to_ai','title','problem','solution','diagnosis',
    'content_for_embedding','reply','subject','provenance','historical_resolution','text',
    'answer','rationale','justifications','recorded_outcome','observed_history','observed_problem',
    'businessName','client_business_name','client_organization_name','client_email','name','contact_name','company_name'}
TECHNICAL_NAMES={'master','erp master','cortex','anymarket','mercado livre','tray','shopee',
                 'skyhub','oracle','firebird','vtex','alternativa sistemas','alternativa e sistemas',
                 'contato','atendimento','venda','vendas','produto','produtos','suporte','compras',
                 'financeiro','fiscal','cadastro','estoque','cliente','comercial','administrador',
                 'admin','sistema','tecnico','logistica','uappi','well','wehub','wbuy','nuvemshop'}


def identity_matcher(names):
    buckets=defaultdict(set)
    for name in names:
        name=str(name or '').strip().casefold()
        if len(name)<4 or name in TECHNICAL_NAMES:
            continue
        first=re.match(r'\w+',name)
        if first:
            buckets[first[0]].add(name)
    return {key:sorted(values,key=len,reverse=True) for key,values in buckets.items()}


def mask_text(text,buckets):
    lower=text.casefold()
    spans=[]; end=0
    for word in re.finditer(r'\w+',lower):
        if word.start()<end:
            continue
        for name in buckets.get(word[0],()):
            stop=word.start()+len(name)
            if lower.startswith(name,word.start()) and (stop==len(lower) or not lower[stop].isalnum()):
                spans.append((word.start(),stop));end=stop;break
    for start,stop in reversed(spans):
        text=text[:start]+'[identidade removida]'+text[stop:]
    text=re.sub(r'\[credencial[ _](?:removida|omitida)\](?:\s+removida\])*','[credencial_removida]',text)
    text=re.sub(r'(?im)(\b(?:login|usuario|usuário|user)\s*[:=]\s*)[^\r\n]+',r'\1[identificador removido]',text)
    text=re.sub(r'(?i)\b(?:authorization\s*[:=]\s*bearer|bearer)\s+[A-Za-z0-9._~+/=-]+','[credencial removida]',text)
    text=re.sub(r'\b\d{44}\b','[chave fiscal removida]',text)
    text=redact(text)
    # Private meeting/share paths can themselves be access tokens.
    return re.sub(r'https?://[^\s<>"\']+',
        lambda m: re.sub(r'(?<=//)([^/]+)(?:/.*)?',r'\1/[caminho removido]',m[0])
        if re.search(r'fathom\.video|zoom\.us|meet\.google|teams\.microsoft|dashboard\.',m[0],re.I) else m[0],text)


def transform(value,buckets,field=None,force=False):
    if isinstance(value,dict):
        return {k:transform(v,buckets,k,force or field=='justifications') for k,v in value.items()}
    if isinstance(value,list):
        return [transform(v,buckets,field,force) for v in value]
    if isinstance(value,str) and (force or field in TEXT_FIELDS):
        return mask_text(value,buckets)
    if isinstance(value,str) and re.search(r'https?://',value):
        return mask_text(value,{})
    return value


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('folders',type=Path,nargs='+')
    args=parser.parse_args()
    with psycopg.connect(POSTGRES_DSN,options='-c default_transaction_read_only=on -c statement_timeout=30000') as conn:
        rows=conn.execute('''SELECT DISTINCT client_business_name,client_organization_name
            FROM refined_tickets''').fetchall()
    buckets=identity_matcher(name for row in rows for name in row)
    count=0
    for folder in args.folders:
        if not folder.resolve().is_relative_to(OUT.resolve()):
            raise SystemExit('Sanitization is confined to the private support audit output.')
        for path in folder.rglob('*.json'):
            raw=path.read_bytes(); source=json.loads(raw)
            if not isinstance(source,dict):
                continue
            clean=transform(source,buckets)
            clean['export_privacy']={
                'method':'Known corpus identities and credential/contact patterns in narrative fields.',
                'original_file_sha256':source.get('export_privacy',{}).get('original_file_sha256') or hashlib.sha256(raw).hexdigest(),
                'notice':'Input/prompt hashes refer to generation-time content, before this additional export mask. This is not a guarantee of complete anonymization.'}
            path.write_text(json.dumps(clean,ensure_ascii=False,indent=2),encoding='utf-8')
            count+=1
        # Rebuild the viewer from masked records so old HTML cannot retain names.
        report=folder/'report.json'
        if report.is_file():
            import html
            records=json.loads(report.read_text(encoding='utf-8')).get('results',[])
            sections=['<h1>Auditoria privada — revisão técnica pendente</h1>']
            for row in records:
                sections.append('<h2>#'+str(row['ticket'])+'</h2><pre>'+html.escape(json.dumps(row,ensure_ascii=False,indent=2))+'</pre>')
            (folder/'report.html').write_text('<!doctype html><meta charset="utf-8"><style>body{font:15px system-ui;max-width:1100px;margin:30px auto}pre{white-space:pre-wrap;overflow-wrap:anywhere}</style>'+''.join(sections),encoding='utf-8')
    print('Private audit JSON exports masked:',count)


if __name__=='__main__':
    main()
