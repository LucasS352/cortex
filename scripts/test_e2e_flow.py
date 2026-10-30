"""HTTP boundaries after removing the legacy technician/customer portals."""
import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
import web_auth


class RetiredPortalTests(unittest.TestCase):
    def setUp(self):
        from simulator_app import app
        self.app = app
        self.client = TestClient(app, base_url="https://localhost")
        self.addCleanup(self.client.close)

    def test_removed_pages_apis_and_assets_are_unavailable_for_every_session(self):
        routes = ["/tecnico", "/tecnico/", "/cliente", "/cliente/",
                  "/api/client/ticket", "/api/client/my-tickets", "/api/client/submit",
                  "/api/tickets/all", "/api/tickets/132625", "/api/tickets/132625/action",
                  "/api/tickets/132625/similar", "/api/tickets/132625/similar-feedback",
                  "/api/tickets/historical/132625", "/api/tech/queue", "/api/tech/action",
                  "/api/tech/analyze/old", "/api/tech/report/old",
                  "/tech-assets/movidesk_tecnico.html", "/tech-assets/movidesk_cliente.html",
                  "/tech-assets/movidesk_deskcortex.html"]
        admin = {"id": "admin", "username": "admin", "role": "super_admin"}
        for enabled, user in (("true", None), ("true", admin), ("false", None)):
            with patch.dict(os.environ, {"CORTEX_AUTH_REQUIRED": enabled}), \
                    patch.object(web_auth, "_load_user_session", return_value=user):
                for path in routes:
                    for method in ("GET", "POST"):
                        with self.subTest(enabled=enabled, authenticated=bool(user), path=path, method=method):
                            res = self.client.request(method, path, follow_redirects=False)
                            self.assertEqual(res.status_code, 404)
                            self.assertEqual(res.headers["cache-control"], "no-store")
                            self.assertNotIn("Todos os tickets", res.text)

    def test_legacy_handlers_are_not_registered(self):
        paths = [getattr(route, "path", "") for route in self.app.routes]
        self.assertEqual(paths.count("/"), 1)
        self.assertNotIn("/cliente", paths)
        self.assertNotIn("/tecnico", paths)
        self.assertFalse(any(p.startswith("/api/client/") or p.startswith("/api/tickets/") for p in paths))
        self.assertIn("/treinamento/tecnico", paths)
        # Included FastAPI routers may be lazy; exercise the PIN boundary
        # instead of relying on their internal route-table representation.
        result = self.client.put('/api/treinamento/tecnico/tickets/42/agente', json={'paused': True})
        self.assertEqual(result.status_code, 422)

    def test_home_redirects_to_current_admin_panel(self):
        admin = {"id": "admin", "username": "admin", "role": "super_admin"}
        with patch.dict(os.environ, {"CORTEX_AUTH_REQUIRED": "true"}), \
                patch.object(web_auth, "_load_user_session", return_value=admin):
            res = self.client.get("/", follow_redirects=False)
        self.assertEqual(res.status_code, 303)
        self.assertEqual(res.headers["location"], "/admin")


if __name__ == "__main__":
    unittest.main()
