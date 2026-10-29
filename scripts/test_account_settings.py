"""Test suite for master account settings, user administration, and self-service password changes."""
import os
import unittest
from unittest.mock import patch, MagicMock
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
import web_auth
from account_settings import router as account_settings_router


class AccountSettingsTests(unittest.TestCase):
    def setUp(self):
        self.master_user = {
            "id": uuid4(),
            "username": "lucas.souza",
            "name": "Lucas Souza",
            "email": "lucas.souza@alternativasistemas.com.br",
            "role": "super_admin",
            "password_hash": web_auth.hash_password("MasterPass123!"),
            "movidesk_person_id": None,
            "is_active": True,
            "must_change_password": False,
            "session_version": 0,
        }
        self.analyst_user = {
            "id": uuid4(),
            "username": "analista1",
            "name": "Analista Teste",
            "email": "analista1@alternativasistemas.com.br",
            "role": "support_analyst",
            "password_hash": web_auth.hash_password("AnalystPass123!"),
            "movidesk_person_id": "12345",
            "is_active": True,
            "must_change_password": True,
            "session_version": 0,
        }

        self.env = patch.dict(os.environ, {
            "CORTEX_AUTH_REQUIRED": "true",
            "CORTEX_SESSION_SECRET": "a" * 48,
            "CORTEX_PUBLIC_URL": "https://testserver",
        })
        self.env.start()

        self.app = FastAPI()
        self.current_user = self.master_user

        @self.app.middleware("http")
        async def inject_auth(request, call_next):
            request.state.cortex_authenticated = True
            request.state.cortex_user = self.current_user["username"]
            request.state.cortex_user_id = str(self.current_user["id"])
            request.state.cortex_role = self.current_user["role"]
            request.state.cortex_user_obj = self.current_user
            return await call_next(request)

        self.app.include_router(account_settings_router)
        self.client = TestClient(self.app, base_url="https://testserver")

    def tearDown(self):
        self.client.close()
        self.env.stop()

    def test_pages_render_html(self):
        # /senha page
        res = self.client.get("/senha")
        self.assertEqual(res.status_code, 200)
        self.assertIn("Alterar Senha", res.text)
        self.assertIn("cortex-logo-header.png", res.text)

        # /em-breve page
        self.current_user = self.analyst_user
        res = self.client.get("/em-breve")
        self.assertEqual(res.status_code, 200)
        self.assertIn("Analista Teste", res.text)
        self.assertIn("Analista de suporte", res.text)

    def test_non_master_cannot_access_accounts_api(self):
        self.current_user = self.analyst_user
        res = self.client.get("/api/admin/accounts")
        self.assertEqual(res.status_code, 403)


if __name__ == "__main__":
    unittest.main()
