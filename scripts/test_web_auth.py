"""Authentication boundary checks for the Portainer deployment."""
import os
import asyncio
import httpx
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import web_auth
from web_auth import hash_password


class WebAuthTests(unittest.TestCase):
    def setUp(self):
        web_auth._failures.clear()
        self.env = patch.dict(os.environ, {
            "CORTEX_AUTH_REQUIRED": "true",
            "CORTEX_ADMIN_USERNAME": "admin",
            "CORTEX_ADMIN_PASSWORD_HASH": hash_password("StrongTestPassword123!"),
            "CORTEX_SESSION_SECRET": "a" * 48,
            "MOVIDESK_WEBHOOK_SECRET": "b" * 48,
            "MASTER_CONFIG_WEBHOOK_SECRET": "m" * 48,
            "CORTEX_PUBLIC_URL": "https://localhost",
        })
        self.env.start()
        from simulator_app import app
        self.client = TestClient(app, base_url="https://localhost")

    def tearDown(self):
        self.client.close()
        self.env.stop()

    def test_private_routes_require_login(self):
        self.assertEqual(self.client.get("/api/admin/settings").status_code, 401)
        page = self.client.get("/admin", headers={"accept": "text/html"}, follow_redirects=False)
        self.assertEqual(page.status_code, 303)
        self.assertEqual(page.headers["location"], "/login")
        self.assertEqual(self.client.get("/tech-assets/admin-console.js").status_code, 401)
        for path in ('/api/treinamento/staging','/api/treinamento/metricas-suporte',
                     '/api/treinamento/staging/unknown/imagens/0'):
            self.assertEqual(self.client.get(path).status_code,401)

    def test_technician_editor_is_public_but_data_requires_pin(self):
        page = self.client.get('/treinamento/tecnico')
        self.assertEqual(page.status_code, 200)
        self.assertIn('Identificação do técnico', page.text)
        self.assertEqual(page.headers['x-frame-options'], 'DENY')
        data = self.client.get('/api/treinamento/tecnico/tickets/132084')
        self.assertEqual(data.status_code, 422)

    def test_only_reviewed_procedure_images_are_public(self):
        image = self.client.get('/procedure-images/payment-selection.png')
        self.assertEqual(image.status_code, 200)
        self.assertEqual(image.headers['content-type'], 'image/png')
        self.assertIn('public', image.headers['cache-control'])
        self.assertEqual(self.client.get('/procedure-images/../admin-console.js').status_code, 401)
        self.assertEqual(self.client.get('/procedure-images/unknown.png').status_code, 404)

    def test_configured_password_length_and_hash_verification(self):
        with self.assertRaises(ValueError):
            hash_password("short")
        password = "Test12345"
        with patch.dict(os.environ, {"CORTEX_ADMIN_PASSWORD_HASH": hash_password(password)}):
            self.assertTrue(web_auth.verify_password(password))
            self.assertFalse(web_auth.verify_password("incorrect"))

    def test_login_session_and_logout(self):
        response = self.client.post("/login", data={"username": "admin", "password": "StrongTestPassword123!"},
                                    headers={"origin": "https://localhost"}, follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertIn("Secure", response.headers["set-cookie"])
        self.assertIn("HttpOnly", response.headers["set-cookie"])
        self.assertEqual(self.client.get("/api/admin/settings").status_code, 200)
        self.assertEqual(self.client.get("/logout", follow_redirects=False).status_code, 303)
        self.assertEqual(self.client.get("/api/admin/settings").status_code, 401)

    def test_authenticated_training_allows_only_same_origin_framing(self):
        self.client.cookies.set(web_auth.COOKIE, web_auth._issue())
        training = self.client.get("/treinamento")
        self.assertEqual(training.status_code, 200)
        self.assertEqual(training.headers["x-frame-options"], "SAMEORIGIN")
        self.assertEqual(training.headers["content-security-policy"], "frame-ancestors 'self'")

        # The login page must never be frameable, including by Cortex itself.
        self.client.cookies.delete(web_auth.COOKIE)
        login = self.client.get("/login")
        self.assertEqual(login.headers["x-frame-options"], "DENY")

    def test_manifest_declares_authenticated_fetch(self):
        self.client.cookies.set(web_auth.COOKIE, web_auth._issue())
        admin = self.client.get("/admin")
        self.assertEqual(admin.status_code, 200)
        self.assertIn('rel="manifest" href="/tech-assets/cortex.webmanifest" crossorigin="use-credentials"',
                      admin.text)
        self.assertEqual(self.client.get("/tech-assets/cortex.webmanifest").status_code, 200)

    def test_admin_contains_training_deep_link_router(self):
        self.client.cookies.set(web_auth.COOKIE, web_auth._issue())
        script = self.client.get("/tech-assets/admin-console.js")
        self.assertEqual(script.status_code, 200)
        self.assertIn("function openRequestedView()", script.text)
        self.assertIn("$('training-frame').src='/treinamento'", script.text)
        self.assertIn("go(requested)", script.text)

        training = self.client.get("/treinamento")
        self.assertIn("if (window.top === window.self)", training.text)
        self.assertIn("window.location.replace(target)", training.text)

    def test_wrong_origin_and_wrong_password(self):
        wrong_origin = self.client.post("/login", data={"username": "admin", "password": "StrongTestPassword123!"},
                                        headers={"origin": "https://evil.example"})
        self.assertEqual(wrong_origin.status_code, 403)
        wrong_pass = self.client.post("/login", data={"username": "admin", "password": "wrong"},
                                      headers={"origin": "https://localhost"})
        self.assertNotIn("cortex_session", wrong_pass.cookies)
        self.assertEqual(wrong_pass.status_code, 401)

    def test_healthcheck_with_and_without_authentication(self):
        for enabled in ("true", "false"):
            for ready in (True, False):
                with self.subTest(auth=enabled, database=ready), patch.dict(os.environ, {"CORTEX_AUTH_REQUIRED": enabled}), \
                        patch("web_auth._database_ready", return_value=ready):
                    response = self.client.get("/healthz")
                    self.assertEqual(response.status_code, 200 if ready else 503)
                    self.assertEqual(response.json(), {"status": "ok" if ready else "unavailable"})

    def test_database_failure_does_not_expose_connection_details(self):
        with patch("psycopg.connect", side_effect=RuntimeError("private-database-password")):
            response = self.client.get("/healthz")
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("private-database-password", response.text)

    def test_http_ip_login_uses_working_cookie(self):
        # No Secure cookie on explicitly configured HTTP; HTTPS remains Secure.
        with patch.dict(os.environ, {"CORTEX_PUBLIC_URL": "http://127.0.0.1"}):
            web_auth.validate_configuration()
            from simulator_app import app
            with TestClient(app, base_url="http://127.0.0.1") as client:
                response = client.post("/login", data={"username": "admin", "password": "StrongTestPassword123!"},
                                       headers={"origin": "http://127.0.0.1"}, follow_redirects=False)
                self.assertEqual(response.status_code, 303)
                self.assertNotIn("Secure", response.headers["set-cookie"])
                self.assertIn("HttpOnly", response.headers["set-cookie"])
                self.assertEqual(client.get("/api/admin/settings").status_code, 200)

    def test_login_branding_and_no_cache(self):
        response = self.client.get("/login")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Alternativa Sistemas", response.text)
        self.assertEqual(response.headers["cache-control"], "no-store")

    def test_unicode_username_is_rejected_without_server_error(self):
        response = self.client.post("/login", data={"username": "usuário", "password": "wrong"},
                                    headers={"origin": "https://localhost"})
        self.assertEqual(response.status_code, 401)

    def test_tampered_expired_and_malformed_sessions_are_rejected(self):
        token = web_auth._issue()
        self.assertTrue(web_auth._valid(token))
        for invalid in (token + "x", "invalid", "x" * 513, token.rsplit(".", 1)[0] + ".á"):
            self.assertFalse(web_auth._valid(invalid))
        with patch("web_auth.time.time", return_value=0):
            expired = web_auth._issue()
        self.assertFalse(web_auth._valid(expired))

    def test_session_does_not_allow_cross_origin_writes(self):
        self.client.cookies.set(web_auth.COOKIE, web_auth._issue())
        for origin in (None, "https://evil.example"):
            response = self.client.post("/api/admin/settings", json={},
                                        headers={"origin": origin} if origin else {})
            self.assertEqual(response.status_code, 403)

    def test_repeated_bad_logins_are_limited(self):
        for _ in range(8):
            response = self.client.post("/login", data={"username": "admin", "password": "wrong"},
                                        headers={"origin": "https://localhost"})
            self.assertEqual(response.status_code, 401)
        self.assertEqual(self.client.post("/login", data={}, headers={"origin": "https://localhost"}).status_code, 429)

    def test_missing_credentials_fail_closed(self):
        for key in ("CORTEX_ADMIN_USERNAME", "CORTEX_ADMIN_PASSWORD_HASH", "CORTEX_SESSION_SECRET", "MOVIDESK_WEBHOOK_SECRET", "MASTER_CONFIG_WEBHOOK_SECRET"):
            with self.subTest(key=key), patch.dict(os.environ, {key: ""}):
                with self.assertRaises(RuntimeError):
                    web_auth.validate_configuration()

    def test_simultaneous_bad_logins_share_attempt_limit(self):
        from simulator_app import app

        async def concurrent_attempts():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://localhost") as client:
                return await asyncio.gather(*(client.post("/login", data={"username": "admin", "password": "wrong"},
                    headers={"origin": "https://localhost"}) for _ in range(12)))

        responses = asyncio.run(concurrent_attempts())
        self.assertEqual(sum(r.status_code == 401 for r in responses), 8)
        self.assertEqual(sum(r.status_code == 429 for r in responses), 4)

    def test_public_url_must_be_an_origin(self):
        for value in ("", "ftp://host", "http://host/admin", "http://user:pass@host", "http://host?x=1", "http://host:bad"):
            with self.subTest(value=value), patch.dict(os.environ, {"CORTEX_PUBLIC_URL": value}):
                with self.assertRaises(RuntimeError):
                    web_auth.validate_configuration()


if __name__ == "__main__":
    unittest.main()
