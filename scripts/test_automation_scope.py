import tempfile
import unittest
import sqlite3
import json
from pathlib import Path
from unittest.mock import patch
from datetime import datetime, timezone

import automation_scope as scope
import admin_console as admin
import movidesk_note_worker as worker
import test_movidesk_note_worker as fixtures

CUTOFF = datetime(2026,10,30,12,tzinfo=timezone.utc).timestamp()
STATE = {'pilot_paused':False,'automation_released_at':CUTOFF,'company_authorized_at':{'464':CUTOFF}}
TICKET = {'status':'Triagem','createdDate':'2026-10-30T12:00:01Z'}


class AdmissionTests(unittest.TestCase):
    def test_only_new_triage_ticket_is_allowed(self):
        self.assertTrue(scope.evaluate(TICKET,STATE,'464')['allowed'])
        for change, reason in [({'createdDate':'2026-10-29T12:00:00Z'},'chamado_anterior_a_liberacao'),
                               ({'createdDate':'2026-10-30T12:00:00Z'},'chamado_anterior_a_liberacao'),
                               ({'createdDate':None},'data_de_abertura_indisponivel'),
                               ({'createdDate':'invalid-date'},'data_de_abertura_indisponivel'),
                               ({'status':'DEV - Em coding'},'status_bloqueado_desenvolvimento')]:
            with self.subTest(change=change):
                result=scope.evaluate({**TICKET,**change},STATE,'464')
                self.assertFalse(result['allowed']);self.assertEqual(result['reason'],reason)

    def test_movidesk_novo_is_initial_intake(self):
        self.assertTrue(scope.evaluate({**TICKET,'status':'Novo'},STATE,'464')['allowed'])
        self.assertFalse(scope.evaluate({**TICKET,'status':'Novo','createdDate':'2026-10-29T12:00:00Z'},STATE,'464')['allowed'])

    def test_movidesk_utc_timestamp_without_suffix_is_supported(self):
        ticket={**TICKET,'status':'SUP - Triagem','createdDate':'2026-10-30T18:06:28.9735169'}
        self.assertTrue(scope.evaluate(ticket,STATE,'464')['allowed'])
        self.assertFalse(scope.evaluate({**ticket,'createdDate':'2026-10-29T18:06:28.9735169'},STATE,'464')['allowed'])

    def test_open_support_statuses_allowed_and_development_commercial_blocked(self):
        for status in ['Novo','Triagem','Em atendimento','Retorno Recebido','Recebido Suporte',
                       'Aguardando Financeiro','Aguardando implantação','IMP - Treinamento','SUP - Validação Gestor']:
            with self.subTest(status=status): self.assertTrue(scope.evaluate({**TICKET,'status':status},STATE,'464')['allowed'])
        for status in ['Aguardando Desenvolvimento','Recebido Desenvolvimento','DEV - Novo','DEV - Triagem feita',
                       'Aguardando Comercial','Comercial','Vendas','Fechado','Resolvido','Cancelado']:
            with self.subTest(status=status): self.assertFalse(scope.evaluate({**TICKET,'status':status},STATE,'464')['allowed'])
        for team in ['Desenvolvimento','Comercial',{'name':'Desenvolvimento - Dev'}]:
            self.assertFalse(scope.evaluate({**TICKET,'status':'Em atendimento','ownerTeam':team},STATE,'464')['allowed'])

    def test_legacy_dates_are_restored_from_real_audit_transitions_only(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'agent_operations.sqlite3'
            base={'pilot_paused':True,'auto_internal_note':False,'pilot_organization_ids':[]}
            authorized={**base,'pilot_organization_ids':['464']}
            activated={**authorized,'pilot_paused':False}
            published={**activated,'auto_internal_note':True}
            with sqlite3.connect(path) as db:
                db.execute('CREATE TABLE audit(id INTEGER PRIMARY KEY,created REAL,action TEXT,detail TEXT)')
                for i,(stamp,before,after) in enumerate([(100,base,authorized),(200,authorized,activated),
                    (250,activated,published),(300,published,published)]):
                    db.execute('INSERT INTO audit VALUES (?,?,?,?)',(i,stamp,'Configuração do agente',json.dumps({'before':before,'after':after})))
            db.close()
            state=scope.restore_release_dates(published,path)
            self.assertEqual(state['automation_released_at'],250)
            self.assertEqual(state['company_authorized_at'],{'464':100})
            existing={**state,'automation_released_at':900,'company_authorized_at':{'464':800}}
            self.assertEqual(scope.restore_release_dates(existing,path),existing)
            missing=Path(directory)/'absent.sqlite3'
            self.assertEqual(scope.restore_release_dates(published,missing),published)
            self.assertFalse(missing.exists())

    def test_authorization_and_activation_both_define_cutoff(self):
        self.assertFalse(scope.evaluate(TICKET,{**STATE,'company_authorized_at':{'464':CUTOFF+2}},'464')['allowed'])
        self.assertFalse(scope.evaluate(TICKET,{**STATE,'automation_released_at':CUTOFF+2},'464')['allowed'])
        for state in [{},{**STATE,'automation_released_at':None},{**STATE,'company_authorized_at':{}},
                      {**STATE,'pilot_paused':True}]:
            self.assertFalse(scope.evaluate(TICKET,state,'464')['allowed'])

    def test_release_dates_are_server_controlled_and_do_not_reset_on_regular_save(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(admin,'STATE_FILE',Path(directory)/'state.json'), \
             patch.object(admin.operations,'audit'), patch.object(admin.time,'time',return_value=1000):
            state=admin.set_state({'pilot_organization_ids':['464'],'pilot_paused':False})
            self.assertEqual(state['automation_released_at'],1000)
            self.assertEqual(state['company_authorized_at'],{'464':1000})
            with patch.object(admin.time,'time',return_value=2000):
                state=admin.set_state({'pilot_paused':False})
                self.assertEqual(state['automation_released_at'],1000)
                state=admin.set_state({'pilot_organization_ids':['464','465']})
                self.assertEqual(state['company_authorized_at'],{'464':1000,'465':2000})
                self.assertEqual(state['automation_released_at'],1000)
                state=admin.set_state({'auto_internal_note':True})
                self.assertEqual(state['automation_released_at'],2000)
                admin.set_state({'pilot_paused':True})
            with patch.object(admin.time,'time',return_value=3000):
                state=admin.set_state({'pilot_paused':False})
                self.assertEqual(state['automation_released_at'],3000)
            with self.assertRaises(Exception): admin.set_state({'automation_released_at':1})


class WorkerAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.WorkerTests();self.f.setUp()
        self.addCleanup(self.f.tearDown)
        self.f.api.data.update(TICKET)
        self.state={**STATE,'min_interval_seconds':0,'max_per_minute':60,'max_concurrent':2}
        worker.runtime_state.return_value=self.state
        worker.new_ticket_scope.side_effect=scope.evaluate

    def test_new_triage_ticket_passes_optional_rules_disabled(self):
        self.f.enqueue(Actions=[{'Id':1}])
        worker.run_once(self.f.settings,self.f.api,self.f.generate)
        self.assertEqual(len(self.f.api.writes),1)

    def test_new_novo_ticket_with_fixed_receipt_publishes_private_note(self):
        self.f.api.data['status']='Novo'
        self.f.api.data['actions'].append({'id':2,'type':2,
            'description':'O seu chamado foi recebido, e iremos realizar a triagem para um atendente do suporte auxiliar no atendimento.',
            'createdDate':'2026-10-30T12:00:02Z','createdBy':{'id':'technician','profileType':1}})
        self.f.enqueue(Actions=[{'Id':2}])
        worker.run_once(self.f.settings,self.f.api,self.f.generate)
        self.assertEqual(len(self.f.api.writes),1)

    def test_legacy_status_allowlist_does_not_block_new_policy(self):
        self.f.api.data['status']='Em atendimento'
        self.state['automation_rules']={'enabled':True,'statuses':['Triagem'],'events':['customer_message'],'companies':{}}
        self.f.enqueue(Actions=[{'Id':1}])
        worker.run_once(self.f.settings,self.f.api,self.f.generate)
        self.assertEqual(len(self.f.api.writes),1)

    def test_old_ticket_with_new_webhook_does_not_generate(self):
        self.f.api.data['createdDate']='2026-10-20T12:00:00Z'
        self.f.enqueue(Actions=[{'Id':1}])
        worker.run_once(self.f.settings,self.f.api,self.f.generate)
        self.f.generate.assert_not_called()
        self.assertFalse(self.f.api.writes)

    def test_release_change_during_generation_blocks_publication(self):
        def generate(*_):
            self.state['automation_released_at']=CUTOFF+2
            return {'reply':'Sugestão pronta.'}
        self.f.enqueue(Actions=[{'Id':1}])
        worker.run_once(self.f.settings,self.f.api,generate)
        self.assertFalse(self.f.api.writes)
