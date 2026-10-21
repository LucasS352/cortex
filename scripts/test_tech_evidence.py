"""Regression tests with synthetic tickets; no model or production DB calls."""
import json
import tempfile
import unittest
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

import tech_desk
from chat_session import session_store, ResolutionStatus
from tech_evidence import cards, detail, plain_text, retrieve, revision
from reranker import Reranker
from chat_session import InvestigationState


class EvidenceTests(unittest.TestCase):
    def test_generator_receives_procedure_but_public_metadata_does_not(self):
        node = Reranker().rerank([{"id": 1, "source_type": "MANUAL", "content_for_embedding": "Procedimento de teste", "semantic_sim": 0.9}], InvestigationState())[0]
        self.assertEqual(node.to_generation_dict()["content_text"], "Procedimento de teste")
        self.assertNotIn("content_text", node.to_response_dict())

    def test_sources_deduplicate_by_ticket_and_never_claim_validation(self):
        rows = [{"id": i, "source_external_id": 100, "solution": "<b>Ajuste</b>"} for i in (1, 2)]
        result = cards(rows, [1])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["relationship"], "provided_to_model")
        self.assertEqual(result[0]["validation_state"], "recorded_unverified")
        self.assertEqual(result[0]["solution_excerpt"], "Ajuste")

    def test_original_is_not_reconstructed_from_summary(self):
        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = [{"id": "tenant"}]
        conn.execute.return_value.fetchone.side_effect = [
            {"id": 1, "source_external_id": 100, "solution": "IA sugere X"}, None]
        result = detail(conn, 1)
        self.assertEqual(result["summary"]["solution"], "IA sugere X")
        self.assertEqual(result["original_status"], "unavailable")
        self.assertEqual(result["actions"], [])

    def test_original_actions_and_html_are_safe_plain_text(self):
        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = [{"id": "tenant"}]
        conn.execute.return_value.fetchone.side_effect = [
            {"id": 1, "source_external_id": 100},
            {"ingested_at": "2026-09-01", "payload": {"actions": [{"id": 9, "htmlDescription": '<script>alert(1)</script><p>Solução &amp; teste</p>'}]}}]
        result = detail(conn, 1)
        self.assertEqual(result["actions"][0]["text"], "Solução & teste")
        self.assertEqual(len(result["source_revision"]), 64)

    def test_multi_tenant_corpus_fails_closed(self):
        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = [{"id": "a"}, {"id": "b"}]
        with self.assertRaises(ValueError):
            retrieve(conn, "erro")

    def test_search_is_parameterized_and_paginates_without_llm(self):
        conn = MagicMock()
        conn.execute.return_value.fetchall.side_effect = [[{"id": "tenant"}], [{"id": "tenant"}],
            [{"id": i, "source_external_id": i, "title": "tickets", "lexical_rank": 0.2} for i in range(6)], [], [], [], []]
        with patch('knowledge_access.knowledge_index.ensure_coverage'), \
                patch('knowledge_access.curation.ensure_schema'):
            result = retrieve(conn, "'; DROP TABLE tickets;--")
        calls = [call.args for call in conn.execute.call_args_list]
        self.assertTrue(all("DROP TABLE" not in sql for sql, *_ in calls))
        bound_parameters = " ".join(repr(args[1:]) for args in calls)
        self.assertIn('"drop"', bound_parameters)
        self.assertIn('"table"', bound_parameters)
        self.assertEqual(result["next_offset"], 5)
        self.assertEqual(len(result["items"]), 5)


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(tech_desk, "REVIEW_DB", Path(self.temp.name) / "reviews.sqlite3")
        self.db_patch.start()
        self.app = FastAPI(); self.app.include_router(tech_desk.router)
        self.client = TestClient(self.app)
        self.session = session_store.get_or_create("")
        self.session.add_user_turn("Erro de exemplo")
        self.session.add_assistant_turn("Conferir a configuração", evidence_classified=[{"node_id": 1}])
        self.body = {"session_id": self.session.session_id, "expected_revision": revision(self.session),
                     "request_id": "request-example-123456", "decision": "approved",
                     "final_response": "Conferir a configuração", "selected_evidence_ids": []}

    def tearDown(self):
        self.client.close(); self.db_patch.stop(); self.temp.cleanup()

    def test_review_preserves_original_and_does_not_resolve_or_send(self):
        body = {**self.body, "decision": "edited", "final_response": "Solução corrigida", "correction_reason": "Versão diferente"}
        response = self.client.post('/api/tech/review', json=body)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["delivery_status"], "not_sent")
        self.assertNotEqual(self.session.state.resolution_status, ResolutionStatus.RESOLVED)
        with closing(tech_desk.review_connection()) as conn:
            row = conn.execute("SELECT * FROM reviews").fetchone()
            self.assertEqual(row["original_response"], self.body["final_response"])
            self.assertEqual(json.loads(row["request_json"])["final_response"], "Solução corrigida")

    def test_double_click_is_idempotent_and_other_reviewer_conflicts(self):
        self.assertEqual(self.client.post('/api/tech/review', json=self.body).status_code, 200)
        self.assertEqual(self.client.post('/api/tech/review', json=self.body).status_code, 200)
        other = {**self.body, "request_id": "another-request-123456"}
        self.assertEqual(self.client.post('/api/tech/review', json=other).status_code, 409)
        changed = {**self.body, "final_response": "changed"}
        self.assertEqual(self.client.post('/api/tech/review', json=changed).status_code, 409)

    def test_concurrent_approval_accepts_only_one(self):
        bodies = [{**self.body, "request_id": f"concurrent-request-{n}"} for n in range(2)]
        with ThreadPoolExecutor(max_workers=2) as executor:
            statuses = list(executor.map(lambda b: self.client.post('/api/tech/review', json=b).status_code, bodies))
        self.assertEqual(sorted(statuses), [200, 409])

    def test_new_turn_invalidates_review(self):
        self.session.add_user_turn("O erro mudou")
        self.assertEqual(self.client.post('/api/tech/review', json=self.body).status_code, 409)

    def test_invalid_reference_or_unexplained_edit_is_rejected(self):
        with patch.object(tech_desk, "connection"), patch.object(tech_desk, "detail", return_value=None):
            response = self.client.post('/api/tech/review', json={**self.body, "selected_evidence_ids": [999]})
            self.assertEqual(response.status_code, 422)
        self.assertEqual(self.client.post('/api/tech/review', json={**self.body, "decision": "edited"}).status_code, 422)

    def test_db_failure_is_explicit_and_draft_revision_unchanged(self):
        with patch.object(tech_desk, "connection", side_effect=ValueError("unavailable")):
            result = self.client.get(f'/api/tech/references/{self.session.session_id}')
        self.assertEqual(result.status_code, 503)
        self.assertEqual(revision(self.session), self.body["expected_revision"])


class DeskBoundaryTests(unittest.TestCase):
    def test_unauthenticated_request_host_and_origin_are_rejected(self):
        import os
        from simulator_app import app
        import web_auth
        environment = {
            "CORTEX_AUTH_REQUIRED": "true",
            "CORTEX_PUBLIC_URL": "http://testserver",
            "CORTEX_ADMIN_USERNAME": "admin",
            "CORTEX_ADMIN_PASSWORD_HASH": "scrypt$16384$8$1$" + "0" * 32 + "$" + "0" * 128,
            "CORTEX_SESSION_SECRET": "s" * 32,
            "MOVIDESK_WEBHOOK_SECRET": "m" * 32,
            "MASTER_CONFIG_WEBHOOK_SECRET": "c" * 32,
        }
        with patch.dict(os.environ, environment, clear=False), TestClient(app, client=("203.0.113.10", 12345)) as client:
            self.assertEqual(client.get('/api/tech/evidence/1').status_code, 401)
            self.assertEqual(client.get('/', headers={"Host": "untrusted.example"}).status_code, 400)
            client.cookies.set(web_auth.COOKIE, web_auth._issue())
            self.assertEqual(client.post('/api/tech/review', headers={"Origin": "https://untrusted.example"}, json={}).status_code, 403)

    def test_legacy_approval_cannot_bypass_revision_and_notes_stay_internal(self):
        from simulator_app import app
        session = session_store.get_or_create("")
        with TestClient(app) as client:
            response = client.post('/api/tech/action', json={"session_id": session.session_id, "action_type": "approve"})
            self.assertEqual(response.status_code, 409)
            response = client.post('/api/tech/action', json={"session_id": session.session_id,
                "action_type": "request_screenshot", "notes": "NOTA INTERNA DE TESTE"})
            self.assertEqual(response.status_code, 200)
            public = client.get(f'/api/chat/session/{session.session_id}')
            self.assertNotIn("NOTA INTERNA DE TESTE", public.text)
        self.assertEqual(session.internal_notes, ["NOTA INTERNA DE TESTE"])


if __name__ == '__main__':
    unittest.main()
