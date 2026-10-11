"""Render the real Portainer files to catch Compose interpolation regressions."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import unittest

from web_auth import hash_password

ROOT = Path(__file__).resolve().parent.parent
FILES = ("docker-compose.yml", "docker-compose.yaml", "compose.portainer.yml")


@unittest.skipUnless(shutil.which("docker"), "Docker Compose is required")
class DeploymentConfigTests(unittest.TestCase):
    def test_portainer_files_render_with_auth_and_literal_caddy_variable(self):
        password_hash = hash_password("DisposableTestPassword!")
        env = {**os.environ, "POSTGRES_PASSWORD": "test-db-only", "CORTEX_ADMIN_PASSWORD_HASH": password_hash,
               "CORTEX_SESSION_SECRET": "s" * 48, "MOVIDESK_WEBHOOK_SECRET": "w" * 48,
               "CORTEX_PUBLIC_URL": "http://192.0.2.10", "CORTEX_DOMAIN": "must-not-be-interpolated",
               "CORTEX_AUTH_REQUIRED": "false"}
        for filename in FILES:
            with self.subTest(file=filename):
                result = subprocess.run(["docker", "compose", "--env-file", os.devnull, "-f", filename,
                                         "config", "--format", "json"], cwd=ROOT, env=env,
                                        capture_output=True, text=True, check=True)
                services = json.loads(result.stdout)["services"]
                self.assertEqual(services["app"]["environment"]["CORTEX_AUTH_REQUIRED"], "true")
                self.assertEqual(services["app"]["environment"]["CORTEX_ADMIN_PASSWORD_HASH"].replace("$$", "$"), password_hash)
                self.assertEqual(services["caddy"]["environment"]["CORTEX_DOMAIN"], "http://192.0.2.10")
                # Compose escapes dollar signs when serializing a reusable config.
                command = services["caddy"]["entrypoint"][2].replace("$$", "$")
                self.assertIn("{$CORTEX_DOMAIN} {", command)
                self.assertNotIn("must-not-be-interpolated", command)
                for private in ("app", "webhook", "postgres", "note-worker"):
                    self.assertFalse(services[private].get("ports"))

    def test_missing_login_credentials_reject_deployment(self):
        env = {**os.environ, "POSTGRES_PASSWORD": "test-db-only", "CORTEX_ADMIN_PASSWORD_HASH": "",
               "CORTEX_SESSION_SECRET": "s" * 48, "MOVIDESK_WEBHOOK_SECRET": "w" * 48,
               "CORTEX_PUBLIC_URL": "http://192.0.2.10"}
        result = subprocess.run(["docker", "compose", "--env-file", os.devnull, "-f", FILES[0], "config", "--quiet"],
                                cwd=ROOT, env=env, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("CORTEX_ADMIN_PASSWORD_HASH", result.stderr)


if __name__ == "__main__":
    unittest.main()
