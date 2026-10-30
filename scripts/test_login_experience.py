"""Login presentation boundary and remember-me contract, with isolated identities."""
import base64
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient
import web_auth


class LoginExperienceTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {
            'CORTEX_AUTH_REQUIRED': 'true', 'CORTEX_PUBLIC_URL': 'https://testserver',
            'CORTEX_SESSION_SECRET': 'test-only-secret-' * 4,
        })
        self.env.start()
        self.user = {'id': 'test-user', 'username': 'tester', 'role': 'support_analyst',
                     'is_active': True, 'password_hash': web_auth.hash_password('TestPassword123!')}
        self.lookup = patch('web_auth.get_user_by_id_or_username', return_value=self.user)
        self.lookup.start()
        web_auth._failures.clear()
        app = FastAPI()
        app.middleware('http')(web_auth.gate)
        app.mount('/tech-assets', StaticFiles(directory=str(Path(__file__).parent / 'static')))
        self.client = TestClient(app, base_url='https://testserver')

    def tearDown(self):
        self.client.close()
        self.lookup.stop()
        self.env.stop()

    def login(self, extra=''):
        return self.client.post('/login', content='username=tester&password=TestPassword123%21' + extra,
            headers={'origin': 'https://testserver', 'content-type': 'application/x-www-form-urlencoded'},
            follow_redirects=False)

    def test_public_assets_are_exact_and_read_only(self):
        for path in web_auth.LOGIN_ASSETS:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers['x-content-type-options'], 'nosniff')
                self.assertEqual(self.client.head(path).status_code, 200)
                self.assertEqual(self.client.post(path).status_code, 401)
        for path in ('/tech-assets/admin-console.js', '/tech-assets/login.html',
                     '/tech-assets/login/../admin-console.js', '/tech-assets/login/unknown.js'):
            self.assertEqual(self.client.get(path).status_code, 401)

    def test_session_cookie_when_unchecked(self):
        response = self.login('&remember=0')
        self.assertEqual(response.status_code, 303)
        cookie = response.headers['set-cookie']
        self.assertNotIn('Max-Age', cookie)
        self.assertNotIn('expires=', cookie.lower())
        self.assertIn('HttpOnly', cookie)
        self.assertIn('Secure', cookie)
        self.assertIn('SameSite=lax', cookie)
        self.assertTrue(web_auth._valid(response.cookies[web_auth.COOKIE]))

    def test_checked_login_persists_for_thirty_days(self):
        for extra in ('&remember=1', '&remember=0&remember=1'):
            response = self.login(extra)
            self.assertEqual(response.status_code, 303)
            self.assertEqual(response.headers['location'], '/atendimento')
            self.assertIn(f'Max-Age={web_auth.REMEMBER_SECONDS}', response.headers['set-cookie'])
            encoded = response.cookies[web_auth.COOKIE].split('.')[0]
            payload = base64.urlsafe_b64decode(encoded + '=' * (-len(encoded) % 4)).decode()
            expiry = int(payload.split(':')[1])
            self.assertAlmostEqual(expiry - web_auth.time.time(), web_auth.REMEMBER_SECONDS, delta=2)
            with patch('web_auth.time.time', return_value=web_auth.time.time() + web_auth.SESSION_SECONDS + 1):
                self.assertTrue(web_auth._valid(response.cookies[web_auth.COOKIE]))
                landing = self.client.get('/login', follow_redirects=False)
                self.assertEqual(landing.status_code, 303)
                self.assertEqual(landing.headers['location'], '/atendimento')

    def test_legacy_post_without_checkbox_uses_session_cookie(self):
        response = self.login()
        self.assertNotIn('Max-Age', response.headers['set-cookie'])

    def test_invalid_login_retains_form_and_error(self):
        response = self.client.post('/login', data={'username': 'tester', 'password': 'wrong'},
                                    headers={'origin': 'https://testserver'})
        self.assertEqual(response.status_code, 401)
        self.assertIn('role="alert"', response.text)
        self.assertIn('Usuário ou senha inválidos.', response.text)
        self.assertIn('method="post" action="/login"', response.text)
        self.assertNotIn(web_auth.COOKIE, response.cookies)

    def test_origin_logout_and_expiry_stay_enforced(self):
        wrong_origin = self.client.post('/login', data={'username': 'tester', 'password': 'TestPassword123!'},
                                        headers={'origin': 'https://other.example'})
        self.assertEqual(wrong_origin.status_code, 403)
        self.login('&remember=0')
        token = self.client.cookies[web_auth.COOKIE]
        with patch('web_auth.time.time', return_value=web_auth.time.time() + web_auth.SESSION_SECONDS + 1):
            self.assertFalse(web_auth._valid(token))
        self.assertEqual(self.client.get('/logout', follow_redirects=False).status_code, 303)
        self.assertNotIn(web_auth.COOKIE, self.client.cookies)


if __name__ == '__main__':
    unittest.main()
