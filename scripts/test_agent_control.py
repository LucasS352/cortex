"""Pilot gates: selected company, internal evidence, and immutable approval."""
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

import admin_console
import agent_control
import agent_operations as operations
import simulator_app


def public_ticket(company_id='company_7', *, age=0, kind='2'):
    return {'ticket_id': 131216, 'type': kind, 'base_status': 'Open', 'status': 'Novo',
            'current_as_of': datetime.now(timezone.utc) - timedelta(hours=age),
            'clients': [{'id': 'person_1', 'personType': 1,
                         'organization': {'id': company_id, 'businessName': 'Empresa piloto'}}]}


class EligibilityTest(unittest.TestCase):
    def setUp(self):
        self.settings = {**admin_console.DEFAULT_STATE, 'pilot_paused': False,
                         'pilot_organization_ids': ['company_7']}

    def test_selected_public_ticket_is_eligible(self):
        result = agent_control.eligibility(public_ticket(), self.settings)
        self.assertTrue(result['eligible'])
        self.assertEqual(result['organization_id'], 'company_7')

    def test_paused_stale_or_wrong_type_blocks(self):
        for ticket, settings in [
            (public_ticket(), {**self.settings, 'pilot_paused': True}),
            (public_ticket(age=49), self.settings),
            (public_ticket(kind='1'), self.settings),
            (public_ticket('other'), self.settings),
        ]:
            self.assertFalse(agent_control.eligibility(ticket, settings)['eligible'])

    def test_ambiguous_company_blocks(self):
        ticket = public_ticket()
        ticket['clients'].append({'id': 'person_2', 'personType': 1,
                                  'organization': {'id': 'company_8', 'businessName': 'Outra'}})
        self.assertFalse(agent_control.eligibility(ticket, self.settings)['eligible'])

    def test_internal_action_requires_exact_text_marker(self):
        run_id = 'a' * 32
        text = 'Procedimento conferido com o cliente e com a base de conhecimento.'
        marker = agent_control.approval_marker(run_id, text)
        ticket = {'id': 55, 'type': 1, 'actions': [{'id': 9, 'type': 1,
                  'description': f'<p>{marker}</p>'}]}
        with patch.object(agent_control, 'fetch_movidesk_ticket', return_value=ticket):
            self.assertEqual(agent_control.verify_internal_approval(55, run_id, text)['action_id'], 9)
            with self.assertRaises(HTTPException):
                agent_control.verify_internal_approval(55, run_id, text + ' alteração')


class PilotRouteTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(operations, 'OPERATIONS_DB', Path(self.temp.name) / 'runs.sqlite')
        self.state_patch = patch.object(admin_console, 'STATE_FILE', Path(self.temp.name) / 'settings.json')
        self.db_patch.start(); self.state_patch.start()
        self.client = TestClient(simulator_app.app)
        admin_console.set_state({'pilot_paused': False, 'pilot_organization_ids': ['company_7']})
        controls = {**admin_console.DEFAULT_STATE, 'min_interval_seconds': 0}
        self.run_id = operations.begin('Assunto', 'Contexto', [], controls, ticket_id=131216)
        operations.finish(self.run_id, draft='Procedimento conferido com a base oficial e a versão do cliente.')

    def tearDown(self):
        self.db_patch.stop(); self.state_patch.stop(); self.temp.cleanup()

    def test_internal_evidence_is_required_and_no_delivery_occurs(self):
        with patch.object(agent_control, 'verify_internal_relation', return_value={'verified_at': '2026-10-06T00:00:00Z'}), \
             patch.object(agent_control, 'ticket_snapshot', return_value=public_ticket()), \
             patch.object(agent_control, 'verify_internal_approval', side_effect=HTTPException(409, 'Sem selo')) as evidence:
            link = self.client.post(f'/api/admin/runs/{self.run_id}/internal-link', json={'internal_ticket_id': 12345})
            self.assertEqual(link.status_code, 200)
            payload = {'approved_text': 'Procedimento conferido com a base oficial e a versão do cliente.',
                       'note': 'Conferi a solução e o contexto.'}
            self.assertEqual(self.client.post(f'/api/admin/runs/{self.run_id}/pilot-approval', json=payload).status_code, 409)
            operations.review(self.run_id, 'approved', '')
            self.assertEqual(self.client.post(f'/api/admin/runs/{self.run_id}/pilot-approval', json=payload).status_code, 409)
            evidence.side_effect = None
            evidence.return_value = {'action_id': 77}
            approved = self.client.post(f'/api/admin/runs/{self.run_id}/pilot-approval', json=payload)
            self.assertEqual(approved.status_code, 200)
            self.assertEqual(approved.json()['delivery_state'], 'blocked_until_movidesk_test')
            self.assertEqual(operations.run_detail(self.run_id)['workflow']['approval_action_id'], '77')
            with self.assertRaises(HTTPException):
                operations.review(self.run_id, 'rejected', 'Mudança posterior')

    def test_pause_blocks_approval(self):
        operations.review(self.run_id, 'approved', '')
        operations.link_internal(self.run_id, 12345, datetime.now(timezone.utc).timestamp())
        admin_console.set_state({'pilot_paused': True})
        with patch.object(agent_control, 'ticket_snapshot', return_value=public_ticket()):
            response = self.client.post(f'/api/admin/runs/{self.run_id}/pilot-approval', json={
                'approved_text': 'Procedimento conferido com a base oficial e a versão do cliente.',
                'note': 'Conferi a solução e o contexto.'})
        self.assertEqual(response.status_code, 409)


if __name__ == '__main__':
    unittest.main()
