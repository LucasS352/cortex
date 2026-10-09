"""Authentication boundary checks for the Portainer deployment."""
import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from web_auth import hash_password


class WebAuthTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {
            "CORTEX_AUTH_REQUIRED": "true",
            "CORTEX_ADMIN_USERNAME": "admin",
            "CORTEX_ADMIN_PASSWORD_HASH": hash_password("StrongTestPassword123!"),
            "CORTEX_SESSION_SECRET": "a" * 48,
            "MOVIDESK_WEBHOOK_SECRET": "b" * 48,
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

    def test_login_session_and_logout(self):
        response = self.client.post("/login", data={"username": "admin", "password": "StrongTestPassword123!"},
                                    headers={"origin": "https://localhost"}, follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertIn("Secure", response.headers["set-cookie"])
        self.assertIn("HttpOnly", response.headers["set-cookie"])
        self.assertEqual(self.client.get("/api/admin/settings").status_code, 200)
        self.assertEqual(self.client.get("/logout", follow_redirects=False).status_code, 303)
        self.assertEqual(self.client.get("/api/admin/settings").status_code, 401)

    def test_wrong_origin_and_wrong_password(self):
        wrong_origin = self.client.post("/login", data={"username": "admin", "password": "StrongTestPassword123!"},
                                        headers={"origin": "https://evil.example"})
        self.assertEqual(wrong_origin.status_code, 403)
        wrong_pass = self.client.post("/login", data={"username": "admin", "password": "wrong"},
                                      headers={"origin": "https://localhost"})
        self.assertNotIn("cortex_session", wrong_pass.cookies)


if __name__ == "__main__":
    unittest.main()
