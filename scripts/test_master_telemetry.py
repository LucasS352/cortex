"""Synthetic fixtures only. Set MASTER_TEST_POSTGRES=1 for transactional DB tests."""
import json
import os
import unittest
import uuid
from datetime import datetime, timezone

from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

from master_config import MasterConfigRequest, normalize, parse_ini, changed_fields
from master_telemetry import connection, save_report

NOW = datetime(2026, 9, 11, tzinfo=timezone.utc)
INI = '''[geral]
n_bancos=1
[conexao_1]
banco=/test/a.fdb
servidor=192.0.2.1
usuario=database-login-test
senha=super-private-test
[conexao_1]
banco=/test/b.fdb
servidor=192.0.2.2
password=another-private-test
unknown_key=unknown-private-test
#dll=ignored.dll
'''


def payload(**updates):
    return {'empresa': 'EMPRESA FICTÍCIA', 'usuario': 'Operador de teste', 'versao': ' v3.0 - 10/09/2026 08:03:18',
            'ticket': 'Ticket-EXEMPLO', 'servidor': '192.0.2.1', 'banco': '/test/a.fdb',
            'datahora': '10/09/2026 07:03:42', 'config': INI, **updates}


def report(**updates):
    return normalize(MasterConfigRequest.model_validate(payload(**updates)), received_at=NOW)


class ConfigTests(unittest.TestCase):
    def test_delphi_escaped_json_and_envelope_are_supported(self):
        value = json.loads(json.dumps({'status': 'ok', 'info': payload()}, ensure_ascii=True))
        self.assertEqual(MasterConfigRequest.model_validate(value).config, INI.strip())
        self.assertTrue(normalize(MasterConfigRequest.model_validate(value), NOW)['payload']['is_beta'])

    def test_beta_depends_on_ticket_and_clears_without_it(self):
        self.assertTrue(report()['payload']['is_beta'])
        for ticket in ('', '   ', None):
            self.assertFalse(report(ticket=ticket, versao='v3.0 BETA')['payload']['is_beta'])

    def test_duplicate_sections_and_active_database_remain_separate(self):
        result = report()['payload']
        self.assertEqual(result['connection_count'], 2)
        self.assertEqual(result['active_connection_occurrences'], [2])
        self.assertEqual(result['config_sections'][2]['values']['banco'], '/test/b.fdb')
        self.assertEqual(result['sanitization']['duplicate_sections'], 1)
        self.assertNotIn('is_beta', result['config_sections'][2])

    def test_secrets_and_unknown_values_never_reach_storage_projection(self):
        result = json.dumps(report()['payload'])
        for secret in ('super-private-test', 'another-private-test', 'unknown-private-test', 'database-login-test'):
            self.assertNotIn(secret, result)
        sections, _ = parse_ini('[conexao_1]\nservidor=firebird://user:password@host/db')
        self.assertEqual(sections[0]['values']['servidor'], '[REMOVIDO]')

    def test_compilation_timestamp_is_not_collection_timestamp(self):
        result = report()['payload']
        self.assertEqual(result['version'], 'v3.0')
        self.assertEqual(result['build_local'], '2026-09-10T08:03:18')
        self.assertIn('build_after_observation_check_clocks', result['warnings'])
        self.assertIn('07:03:42-03:00', result['observed_at'])

    def test_provisional_identity_does_not_merge_different_servers(self):
        self.assertNotEqual(report()['installation_key'], report(servidor='192.0.2.3')['installation_key'])
        self.assertEqual(report(instalacao_id='same')['installation_key'], report(instalacao_id='same', servidor='192.0.2.3')['installation_key'])

    def test_hash_is_stable_across_clock_warning_changes(self):
        req = MasterConfigRequest.model_validate(payload())
        early = normalize(req, datetime(2026, 9, 1, tzinfo=timezone.utc))
        late = normalize(req, NOW)
        self.assertEqual(early['payload_hash'], late['payload_hash'])
        self.assertFalse(early['clock_trusted'])

    def test_invalid_dates_or_empty_config_are_rejected_without_guessing(self):
        for patch in ({'datahora': '31/02/2026 00:00:00'}, {'config': 'not ini'}, {'servidor': 'firebird://u:p@host/db'}):
            with self.assertRaises(ValidationError):
                MasterConfigRequest.model_validate(payload(**patch))

    def test_api_validation_does_not_echo_ini_secrets(self):
        from simulator_app import app
        with TestClient(app) as client:
            response = client.post('/api/v1/master/telemetry/config/validate', json={'status': 'error', 'info': payload()})
            self.assertEqual(response.status_code, 422)
            self.assertNotIn('super-private-test', response.text)
            response = client.post('/api/v1/master/telemetry/config/validate', json=payload())
            self.assertEqual(response.status_code, 200)
            self.assertNotIn('super-private-test', response.text)


@unittest.skipUnless(os.getenv('MASTER_TEST_POSTGRES') == '1', 'Explicit PostgreSQL integration opt-in')
class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.conn = connection()
        self.installation = 'test-' + str(uuid.uuid4())

    def tearDown(self):
        self.conn.rollback()
        self.conn.close()

    def save(self, **updates):
        return save_report(self.conn, report(instalacao_id=self.installation, codigo_empresa='test-company', **updates))

    def test_retry_is_idempotent_and_does_not_store_raw_secrets(self):
        first = self.save(event_id='same-event')
        second = self.save(event_id='same-event')
        self.assertEqual(second['status'], 'duplicate')
        self.assertEqual(first['snapshot_id'], second['snapshot_id'])
        row = self.conn.execute('SELECT sanitized_payload FROM master_environment_snapshots WHERE id=%s', (first['snapshot_id'],)).fetchone()
        self.assertNotIn('super-private-test', json.dumps(row))

    def test_newer_stable_clears_beta_and_late_report_does_not_revert(self):
        self.save(event_id='first')
        latest = self.save(event_id='second', ticket='', datahora='10/09/2026 08:00:00')
        old = self.save(event_id='late', datahora='09/09/2026 08:00:00')
        self.assertTrue(latest['promoted']); self.assertFalse(old['promoted'])
        current = self.conn.execute('SELECT latest_snapshot_id FROM master_environments WHERE id=%s', (latest['environment_id'],)).fetchone()
        self.assertEqual(str(current['latest_snapshot_id']), latest['snapshot_id'])

    def test_same_event_id_with_changed_content_is_conflict(self):
        self.save(event_id='event')
        with self.assertRaises(HTTPException) as error:
            self.save(event_id='event', ticket='')
        self.assertEqual(error.exception.status_code, 409)

    def test_same_timestamp_change_is_historical_only(self):
        self.save(event_id='one')
        next_event = self.save(event_id='two', ticket='')
        self.assertFalse(next_event['promoted'])

    def test_unchanged_next_day_is_a_new_heartbeat(self):
        self.save(event_id='day-one')
        later = self.save(event_id='day-two', datahora='10/09/2026 20:00:00')
        self.assertEqual(later['changed_fields'], [])
        self.assertTrue(later['promoted'])


if __name__ == '__main__':
    unittest.main()
