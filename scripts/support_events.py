"""Minimal operational telemetry. No question, answer, credential or customer name.

Historical replay calls generate_note/build_reply directly and never records
events. Only the operational persistence/delivery boundary writes this store.
"""
import json
from contextlib import closing
from pathlib import Path
import re
import sqlite3
import time

STORE = Path(__file__).parent/'output/support_quality.sqlite3'
STATES = {'PROCEDIMENTO_CONFIRMADO','INVESTIGAR','ENCAMINHAR'}


def record(event_id, channel, decision, company_id=None):
    if not decision or decision.get('state') not in STATES:
        return False
    facts = decision.get('evidence') or {}
    terms = sorted({str(t)[:80] for field in ('codes','objects','integrations','entities')
                    for t in facts.get(field, []) if re.fullmatch(r'[\w :/-]{1,80}', str(t))})
    procedure = decision.get('procedure') or {}
    try:
        STORE.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(STORE, timeout=2)) as db, db:
            db.execute('''CREATE TABLE IF NOT EXISTS support_events (
                event_id text PRIMARY KEY,created_at integer NOT NULL,channel text NOT NULL,
                state text NOT NULL,company_id text,source_id text,source_version text,terms text NOT NULL)''')
            result = db.execute('INSERT OR IGNORE INTO support_events VALUES (?,?,?,?,?,?,?,?)',
                (str(event_id)[:160],int(time.time()),str(channel)[:40],decision['state'],
                 str(company_id)[:80] if company_id else None,
                 str(procedure.get('id') or '')[:100],str(procedure.get('version') or '')[:100],json.dumps(terms)))
            return result.rowcount == 1
    except (sqlite3.Error, OSError):
        # Telemetry failure never changes a support answer or retries delivery.
        return False


def snapshot():
    if not STORE.is_file():
        return {'status':'no_events','total':0,'states':[], 'investigation_terms':[], 'procedures':[], 'companies':[]}
    try:
        with closing(sqlite3.connect(STORE.resolve().as_uri()+'?mode=ro', uri=True, timeout=2)) as db:
            db.row_factory = sqlite3.Row
            states = [dict(r) for r in db.execute('SELECT state,count(*) events FROM support_events GROUP BY state')]
            procedures = [dict(r) for r in db.execute("""SELECT source_id,source_version,count(*) events
                FROM support_events WHERE source_id<>'' GROUP BY 1,2 ORDER BY events DESC LIMIT 20""")]
            companies = [dict(r) for r in db.execute('''SELECT company_id,state,count(*) events FROM support_events
                WHERE company_id IS NOT NULL GROUP BY 1,2 HAVING count(*)>1 ORDER BY events DESC LIMIT 20''')]
            terms = [dict(r) for r in db.execute("""SELECT j.value term,count(*) events FROM support_events e,
                json_each(e.terms) j WHERE e.state IN ('INVESTIGAR','ENCAMINHAR')
                GROUP BY j.value ORDER BY events DESC LIMIT 30""")]
        return {'status':'available','total':sum(s['events'] for s in states),'states':states,
                'investigation_terms':terms,'procedures':procedures,'companies':companies}
    except sqlite3.Error:
        return {'status':'unavailable','total':None,'states':[], 'investigation_terms':[], 'procedures':[], 'companies':[]}
