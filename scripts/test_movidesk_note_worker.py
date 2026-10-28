import copy
import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import httpx
from fastapi.testclient import TestClient
import movidesk_webhook as receiver
import movidesk_note_worker as worker


def ticket():
    return {'id': 42, 'type': 2, 'subject': 'Erro na etiqueta', 'baseStatus': 'New',
            'clients': [{'id': 'client', 'email': 'lucas.teste@alternativa.com.br',
                         'organization': {'id': '464', 'businessName': 'Empresa teste'}}],
            'actions': [{'id': 1, 'type': 2, 'description': 'Não consigo emitir a etiqueta.',
                         'createdDate': '2026-10-09T10:00:00Z',
                         'createdBy': {'id': 'client', 'profileType': 2}}]}


class FakeApi:
    def __init__(self):
        self.data = ticket()
        self.writes = []
        self.timeout_after_write = False

    def ticket(self, _id):
        return copy.deepcopy(self.data)

    def internal_note(self, tid, content):
        self.writes.append((tid, content))
        self.data['actions'].append({'id': len(self.data['actions']) + 1, 'type': 1,
                                    'description': content, 'createdDate': '2026-10-09T11:00:00Z',
                                    'createdBy': {'id': 'bot', 'profileType': 1}})
        if self.timeout_after_write:
            raise httpx.ReadTimeout('timeout')
        return SimpleNamespace(status_code=200)


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(receiver, 'WEBHOOK_DB', Path(self.tmp.name) / 'queue.sqlite3')
        self.secret_patch = patch.object(receiver, 'MOVIDESK_WEBHOOK_SECRET', 'x' * 40)
        self.db_patch.start()
        self.secret_patch.start()
        self.operational_sync = patch.object(worker, 'persist_operational_ticket').start()
        self.client = TestClient(receiver.app)
        self.settings = worker.Settings('internal_note', 'bot', frozenset({'464'}),
                                        requester_emails=frozenset({'lucas.teste@alternativa.com.br'}))
        self.api = FakeApi()
        self.generate = Mock(return_value={'reply': 'Confira o campo com o técnico antes de alterar.', 'sources': []})

    def tearDown(self):
        patch.stopall()
        self.tmp.cleanup()

    def enqueue(self, **fields):
        result = self.client.post('/webhooks/movidesk/tickets/' + 'x' * 40, json={'Id': 42, **fields})
        self.assertEqual(result.status_code, 200)

    def retry_now(self):
        with receiver._connect() as db:
            db.execute('UPDATE webhook_events SET available_at=0')

    def states(self):
        return [r['state'] for r in receiver.receipt_summary()['recent']]

    def test_pasted_movidesk_image_is_exposed_as_an_action_attachment(self):
        action = {'htmlDescription': '''<p>Veja o erro:</p><img
            src="https://s3.amazonaws.com/movidesk-files/973C426DD20BEF2F32E0E237BDEA3FC5?AWSAccessKeyId=x&amp;Expires=1">'''}
        self.assertEqual(worker.action_attachments(action), [{
            'path': '973C426DD20BEF2F32E0E237BDEA3FC5',
            'fileName': 'print_inline_1.png', 'inline': True}])

    def test_pasted_external_image_is_not_downloaded(self):
        action = {'htmlDescription': '<img src="https://example.com/customer-controlled.png">'}
        self.assertEqual(worker.action_attachments(action), [])

    def test_storage_redirects_are_restricted_to_movidesk_and_s3(self):
        self.assertTrue(worker.safe_storage_url('https://alternativasistemas.movidesk.com/Storage/Download?id=x'))
        self.assertTrue(worker.safe_storage_url('https://s3.amazonaws.com/movidesk-files/x?Signature=y'))
        self.assertFalse(worker.safe_storage_url('http://alternativasistemas.movidesk.com/Storage/Download?id=x'))
        self.assertFalse(worker.safe_storage_url('https://example.com/image.png'))
        self.assertFalse(worker.safe_storage_url('https://movidesk.com@example.com/image.png'))

    def test_inline_image_changes_customer_message_identity(self):
        first = ticket()
        first['actions'][0]['htmlDescription'] = (
            '<img src="https://s3.amazonaws.com/movidesk-files/' + 'A' * 32 + '?Expires=1">')
        second = copy.deepcopy(first)
        second['actions'][0]['htmlDescription'] = (
            '<img src="https://s3.amazonaws.com/movidesk-files/' + 'B' * 32 + '?Expires=1">')
        self.assertNotEqual(worker.identity(first, self.settings)[0], worker.identity(second, self.settings)[0])

    def test_webhook_to_private_note_deduplicates_different_events_and_bot_loop(self):
        self.enqueue(Actions=[{'Id': 1}])
        worker.run_once(self.settings, self.api, self.generate)
        self.assertEqual(self.states(), ['completed'])
        self.enqueue(Actions=[{'Id': 2}], Status='Em atendimento')
        worker.run_once(self.settings, self.api, self.generate)
        self.assertEqual(len(self.api.writes), 1)
        self.generate.assert_called_once()
        self.assertIn(worker.PREFIX, self.api.writes[0][1])
        self.assertGreaterEqual(self.operational_sync.call_count, 1)

    def test_operational_sync_failure_retries_before_generation(self):
        self.enqueue(Actions=[{'Id': 1}])
        self.operational_sync.side_effect = worker.RetryJob('banco indisponível')
        worker.run_once(self.settings, self.api, self.generate)
        self.generate.assert_not_called()
        self.assertEqual(self.api.writes, [])
        self.assertEqual(self.states(), ['retry'])

    def test_runtime_control_requires_selected_company_active_pilot_and_private_toggle(self):
        state_file = Path(self.tmp.name) / 'agent-state.json'
        base = worker.Settings('internal_note', 'bot', runtime_control=True)
        state_file.write_text(json.dumps({'pilot_organization_ids': ['464'],
                                          'pilot_requester_emails': ['lucas.teste@alternativa.com.br'],
                                          'pilot_paused': False, 'auto_internal_note': True}), encoding='utf-8')
        active = worker.effective_settings(base, state_file)
        self.assertEqual(active.mode, 'internal_note')
        self.assertEqual(active.organizations, frozenset({'464'}))
        for update in ({'pilot_organization_ids': [], 'pilot_requester_emails': ['lucas.teste@alternativa.com.br'],
                        'pilot_paused': False, 'auto_internal_note': True},
                       {'pilot_organization_ids': ['464'], 'pilot_requester_emails': [],
                        'pilot_paused': False, 'auto_internal_note': True},
                       {'pilot_organization_ids': ['464'], 'pilot_requester_emails': ['lucas.teste@alternativa.com.br'],
                        'pilot_paused': True, 'auto_internal_note': True},
                       {'pilot_organization_ids': ['464'], 'pilot_requester_emails': ['lucas.teste@alternativa.com.br'],
                        'pilot_paused': False, 'auto_internal_note': False}):
            state_file.write_text(json.dumps(update), encoding='utf-8')
            guarded = worker.effective_settings(base, state_file)
            self.assertEqual(guarded.mode, 'dry_run')

    def test_missing_or_invalid_runtime_control_never_publishes(self):
        base = worker.Settings('internal_note', 'bot', runtime_control=True)
        missing = worker.effective_settings(base, Path(self.tmp.name) / 'missing.json')
        self.assertEqual(missing.mode, 'dry_run')
        self.assertFalse(missing.organizations)
        bad = Path(self.tmp.name) / 'bad.json'
        bad.write_text('{invalid', encoding='utf-8')
        self.assertEqual(worker.effective_settings(base, bad).mode, 'dry_run')

    def test_runtime_company_selection_cannot_be_bypassed_by_environment_ticket(self):
        state_file = Path(self.tmp.name) / 'agent-state.json'
        state_file.write_text(json.dumps({'pilot_organization_ids': ['464'],
                                          'pilot_requester_emails': ['lucas.teste@alternativa.com.br'],
                                          'pilot_paused': False, 'auto_internal_note': True}), encoding='utf-8')
        base = worker.Settings('internal_note', 'bot', frozenset({'other'}), frozenset({'42'}),
                               runtime_control=True)
        controlled = worker.effective_settings(base, state_file)
        self.assertEqual(controlled.organizations, frozenset({'464'}))
        self.assertFalse(controlled.tickets)

    def test_new_customer_message_gets_a_new_note(self):
        self.enqueue(Actions=[{'Id': 1}])
        worker.run_once(self.settings, self.api, self.generate)
        self.api.data['actions'].append({'id': 3, 'type': 2, 'description': 'Continua após conferir.',
                                        'createdDate': '2026-10-09T12:00:00Z',
                                        'createdBy': {'id': 'client', 'profileType': 2}})
        self.enqueue(Actions=[{'Id': 3}])
        worker.run_once(self.settings, self.api, self.generate)
        self.assertEqual(len(self.api.writes), 2)

    def test_manual_only_skips_automatic_event_without_model_and_processes_button_event(self):
        state_file = Path(self.tmp.name) / 'agent-state.json'
        state_file.write_text(json.dumps({'pilot_organization_ids': ['464'],
                                          'pilot_requester_emails': ['lucas.teste@alternativa.com.br'],
                                          'pilot_paused': False, 'auto_internal_note': False}), encoding='utf-8')
        controlled = worker.Settings('internal_note', 'bot', runtime_control=True)
        with patch.object(worker, 'ADMIN_STATE_FILE', state_file):
            self.enqueue(Actions=[{'Id': 1}])
            worker.run_once(controlled, self.api, self.generate)
            self.generate.assert_not_called()
            self.assertEqual(self.api.writes, [])
            self.operational_sync.assert_called_once()
            self.assertEqual(receiver.receipt_summary()['recent'][0]['outcome'], 'automatico_desativado')

            self.api.data['actions'].append({'id': 3, 'type': 2, 'description': 'Mensagem do teste manual.',
                                             'createdDate': '2026-10-09T12:00:00Z',
                                             'createdBy': {'id': 'client', 'profileType': 2}})
            self.client.post('/webhooks/movidesk/tickets/' + 'x' * 40,
                             json={'Id': 42, 'Actions': [{'Id': 3}], 'CortexManualPull': 1})
            worker.run_once(controlled, self.api, self.generate)
        self.generate.assert_called_once()
        self.assertEqual(len(self.api.writes), 1)
        self.assertEqual(receiver.receipt_summary()['recent'][0]['state'], 'completed')

    def test_dry_run_keeps_reviewable_note_without_patch(self):
        self.enqueue()
        settings = worker.Settings('dry_run', 'bot', frozenset({'464'}),
                                   requester_emails=frozenset({'lucas.teste@alternativa.com.br'}))
        worker.run_once(settings, self.api, self.generate)
        self.assertEqual(self.api.writes, [])
        self.assertEqual(self.states(), ['preview'])
        self.assertIn('Confira o campo', receiver.receipt_summary()['notes'][0]['text'])

    def test_unknown_company_is_not_in_pilot(self):
        self.api.data['clients'][0]['organization']['id'] = '999'
        self.enqueue()
        worker.run_once(self.settings, self.api, self.generate)
        self.generate.assert_not_called()
        self.assertEqual(self.states(), ['skipped'])

    def test_unauthorized_requester_is_skipped_before_generation(self):
        self.api.data['clients'][0]['email'] = 'outro.cliente@alternativa.com.br'
        self.enqueue()
        worker.run_once(self.settings, self.api, self.generate)
        self.generate.assert_not_called()
        self.assertEqual(self.states(), ['skipped'])
        self.assertEqual(receiver.receipt_summary()['recent'][0]['outcome'],
                         'solicitante_fora_do_piloto')

    def test_recent_agent_reply_prevents_automated_followup(self):
        self.api.data['actions'].append({'id': 2, 'type': 2, 'description': 'Já estou verificando.',
                                        'createdDate': '2026-10-09T12:00:00Z',
                                        'createdBy': {'id': 'agent', 'profileType': 1}})
        self.enqueue()
        worker.run_once(self.settings, self.api, self.generate)
        self.generate.assert_not_called()

    def test_movidesk_opening_receipt_does_not_hide_customer_message(self):
        self.api.data['actions'].append({'id': 2, 'type': 2,
            'description': 'Olá! O seu chamado foi recebido, e iremos realizar a triagem para um atendente do suporte auxiliar no atendimento.',
            'createdDate': '2026-10-09T12:00:00Z',
            'createdBy': {'id': 'agent', 'profileType': 3}})
        self.enqueue(Actions=[{'Id': 2}])
        worker.run_once(self.settings, self.api, self.generate)
        self.generate.assert_called_once()
        self.assertEqual(len(self.api.writes), 1)
        self.assertEqual(receiver.receipt_summary()['recent'][0]['state'], 'completed')

    def test_customer_reply_during_generation_does_not_receive_old_draft(self):
        def generate(*_):
            self.api.data['actions'][0]['description'] = 'Na verdade é outro erro.'
            return {'reply': 'Sugestão antiga'}
        self.enqueue()
        worker.run_once(self.settings, self.api, generate)
        self.assertEqual(self.api.writes, [])
        self.assertEqual(self.states(), ['skipped'])

    def test_timeout_after_write_reconciles_without_second_patch(self):
        self.enqueue()
        self.api.timeout_after_write = True
        worker.run_once(self.settings, self.api, self.generate)
        self.assertEqual(self.states(), ['retry'])
        self.retry_now()
        worker.run_once(self.settings, self.api, self.generate)
        self.assertEqual(self.states(), ['completed'])
        self.assertEqual(len(self.api.writes), 1)

    def test_uncertain_write_never_repeats_without_confirmation(self):
        self.enqueue()
        self.api.internal_note = Mock(side_effect=httpx.ReadTimeout('timeout'))
        worker.run_once(self.settings, self.api, self.generate)
        for _ in range(4):
            self.retry_now()
            worker.run_once(self.settings, self.api, self.generate)
        self.api.internal_note.assert_called_once()
        self.assertEqual(self.states(), ['needs_review'])

    def test_webhook_ahead_of_api_retries_instead_of_using_old_conversation(self):
        self.enqueue(Actions=[{'Id': 20}])
        worker.run_once(self.settings, self.api, self.generate)
        self.generate.assert_not_called()
        self.assertEqual(self.states(), ['retry'])

    def test_edited_action_waits_for_updated_text_even_when_id_is_unchanged(self):
        self.enqueue(Actions=[{'Id': 1, 'Description': 'Texto editado pelo cliente'}])
        worker.run_once(self.settings, self.api, self.generate)
        self.generate.assert_not_called()
        self.assertEqual(self.states(), ['retry'])

    def test_model_failure_does_not_publish_error_as_a_note(self):
        self.enqueue()
        self.generate.side_effect = worker.RetryJob('Gemini indisponível')
        worker.run_once(self.settings, self.api, self.generate)
        self.assertEqual(self.api.writes, [])
        self.assertEqual(self.states(), ['retry'])

    def test_claim_serializes_workers_and_recovers_expired_lease(self):
        self.enqueue(Actions=[{'Id': 1}])
        self.enqueue(Actions=[{'Id': 2}])
        first = worker.claim(self.settings)
        self.assertIsNotNone(first)
        self.assertIsNone(worker.claim(self.settings))
        with receiver._connect() as db:
            db.execute('UPDATE webhook_events SET lease_until=0')
        self.assertIsNotNone(worker.claim(self.settings))

    def test_http_contract_only_appends_internal_action(self):
        api = worker.Api(self.settings)
        try:
            api.request = Mock(return_value=SimpleNamespace(status_code=200))
            api.internal_note(42, '<p>Teste</p>')
            call = api.request.call_args
            self.assertEqual(call.args, ('PATCH', '/tickets'))
            self.assertEqual(call.kwargs['json'], {'actions': [{'type': 1, 'description': '<p>Teste</p>',
                                                              'createdBy': {'id': 'bot'}}]})
        finally:
            api.client.close()

    def test_render_does_not_execute_generated_html(self):
        result = worker.render_note({'reply': '<script>alert(1)</script>'}, 'marker')
        self.assertNotIn('<script>', result)
        self.assertIn('&lt;script&gt;', result)

    def test_render_identifies_youtube_url_as_a_visible_link(self):
        url = 'https://www.youtube.com/watch?v=Q5m3pZ341zQ'
        result = worker.render_note({'reply': 'Vídeo tutorial: ' + url}, 'marker')
        self.assertIn('href="' + url + '"', result)
        self.assertIn('target="_blank"', result)
        self.assertIn('color:#2563eb', result)
        self.assertIn('text-decoration:underline', result)

    def test_named_images_follow_answer_and_selected_procedure_step(self):
        pixel = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII='
        result = worker.render_note({'reply':'Resposta pronta.\n1. Abra o menu.\n2. Selecione o relatório.',
            'images':[{'name':'Visão geral','caption':'Tela inicial','placement':'answer','after_step':None,'data_url':pixel},
                      {'name':'Aba fornecedor','caption':'Seleção correta','placement':'procedure','after_step':2,'data_url':pixel}]}, 'marker')
        self.assertLess(result.index('Visão geral'), result.index('1. Abra o menu'))
        self.assertGreater(result.index('Aba fornecedor'), result.index('2. Selecione o relatório'))

    def test_render_appends_company_similar_tickets_with_status(self):
        result = worker.render_note({'reply': 'Confira o cadastro.', 'similar_tickets': [
            {'ticket': 131549, 'title': 'Erro de etiqueta', 'status': 'Resolvido', 'active': False},
            {'ticket': 140000, 'title': 'Falha recorrente', 'status': 'Em atendimento', 'active': True},
        ]}, 'marker')
        self.assertIn('Tickets semelhantes:', result)
        self.assertIn('/131549', result)
        self.assertIn('#140000 — Em atendimento', result)

    def test_refinery_does_not_learn_from_ai_suggestions(self):
        from refine_tickets_v2 import process_ticket_v2
        data = ticket()
        data['actions'].append({'id': 2, 'type': 1, 'description': 'Procedimento não executado ' + worker.PREFIX + '123'})
        record = process_ticket_v2('raw', 'tenant', 42, data)
        self.assertNotIn('Procedimento não executado', record['solution_statement'])

    def test_render_suppresses_generic_captions_and_headers(self):
        pixel = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII='
        result = worker.render_note({
            'reply': 'Passo a passo:\n1. Acesse o menu.\n2. Salve.',
            'images': [
                {
                    'name': 'Print da resolução 1',
                    'caption': 'Captura de tela importada da resolução técnica (print 1)',
                    'placement': 'answer',
                    'after_answer_paragraph': 1,
                    'data_url': pixel
                },
                {
                    'name': 'Menu de Configurações',
                    'caption': 'Configuração de impostos',
                    'placement': 'procedure',
                    'after_step': 2,
                    'data_url': pixel
                }
            ]
        }, 'marker')
        # Generic caption and header must be suppressed
        self.assertNotIn('Captura de tela importada da resolução técnica', result)
        self.assertNotIn('Print da resolução 1', result)
        # Custom descriptive name and caption must be rendered
        self.assertIn('Menu de Configurações', result)
        self.assertIn('Configuração de impostos', result)

    def test_render_procedure_info_human_readable(self):
        result = worker.render_note({
            'reply': 'Orientações.',
            'decision': {
                'state': 'PROCEDIMENTO_CONFIRMADO',
                'confidence': 'alta',
                'procedure': {
                    'id': 'canon:1',
                    'title': 'relatorio Venda por produto.',
                    'version': 1,
                    'approved_by': 'alternativa'
                }
            }
        }, 'marker')
        self.assertIn('<strong>Procedimento oficial:</strong> #1 — relatorio Venda por produto. (v1 · aprovado por alternativa)', result)
        self.assertNotIn('{"id":', result)
        self.assertNotIn('canon:1', result)

    def test_render_curatorship_link_confirmed_vs_pending(self):
        with patch.dict('os.environ', {'CORTEX_PUBLIC_URL': 'http://localhost:8001'}):
            res_confirmed = worker.render_note({
                'reply': 'Orientações.',
                'feedback_ticket': 132273,
                'decision': {'state': 'PROCEDIMENTO_CONFIRMADO'}
            }, 'marker')
            self.assertIn('Sugerir ajuste ou melhoria neste procedimento', res_confirmed)
            self.assertNotIn('Preparar resolução técnica e enviar à curadoria', res_confirmed)

            res_pending = worker.render_note({
                'reply': 'Orientações.',
                'feedback_ticket': 132273,
                'decision': {'state': 'RESPOSTA_AVALIADA'}
            }, 'marker')
            self.assertIn('Preparar resolução técnica e enviar à curadoria', res_pending)
            self.assertNotIn('Sugerir ajuste ou melhoria neste procedimento', res_pending)


if __name__ == '__main__':
    unittest.main()
