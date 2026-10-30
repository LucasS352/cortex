import tempfile
import unittest
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
                               ({'createdDate':'2026-10-30T13:00:00'},'data_de_abertura_indisponivel'),
                               ({'status':'Novo'},'fora_da_triagem'),({'status':'Em atendimento'},'fora_da_triagem')]:
            with self.subTest(change=change):
                result=scope.evaluate({**TICKET,**change},STATE,'464')
                self.assertFalse(result['allowed']);self.assertEqual(result['reason'],reason)

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
