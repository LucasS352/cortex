"""Tests for the Portainer-specific environment file writer."""
from pathlib import Path
import tempfile
import unittest

from dotenv import dotenv_values

from prepare_portainer_env import write_portainer_env


class PreparePortainerEnvTests(unittest.TestCase):
    def test_writer_does_not_add_literal_quotes(self):
        values = {
            "CORTEX_PUBLIC_URL": "http://192.0.2.10",
            "CORTEX_ADMIN_USERNAME": "alternariva",
            "CORTEX_ADMIN_PASSWORD_HASH": "scrypt$16384$8$1$salt$hash",
            "OPTIONAL_VALUE": "",
        }
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "portainer.env"
            write_portainer_env(output, values)
            contents = output.read_text(encoding="utf-8")
            parsed = dotenv_values(output, interpolate=False)

        self.assertIn("CORTEX_PUBLIC_URL=http://192.0.2.10\n", contents)
        self.assertIn("CORTEX_ADMIN_PASSWORD_HASH=scrypt$16384$8$1$salt$hash\n", contents)
        self.assertIn("OPTIONAL_VALUE=\n", contents)
        self.assertNotIn("='", contents)
        self.assertNotIn('="', contents)
        self.assertEqual(dict(parsed), values)

    def test_writer_rejects_multiline_values(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "quebra de linha"):
                write_portainer_env(Path(directory) / "portainer.env", {"SECRET": "one\ntwo"})


if __name__ == "__main__":
    unittest.main()
