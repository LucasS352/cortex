"""Login presentation boundary and remember-me contract, with isolated identities."""
import base64
import os
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock
from uuid import UUID
from login_assets import preload_assets, presentation_paths

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
        self.lookup_mock = self.lookup.start()
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

    def test_enhanced_login_sets_cookie_without_fetching_destination(self):
        for role, target in [('super_admin', '/admin'), ('support_analyst', '/atendimento'),
                             ('director', '/em-breve')]:
            self.user['role'] = role
            response = self.client.post('/login', data={
                'username': 'tester', 'password': 'TestPassword123!', 'remember': '1'},
                headers={'origin': 'https://testserver', 'accept': 'application/json'})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()['redirect'], target)
            self.assertEqual(response.json()['preload'], list(preload_assets(target)))
            self.assertIn(f'Max-Age={web_auth.REMEMBER_SECONDS}', response.headers['set-cookie'])
            self.assertEqual(response.headers['cache-control'], 'no-store')
            self.assertTrue(web_auth._valid(response.cookies[web_auth.COOKIE]))
        self.user['must_change_password'] = True
        response = self.client.post('/login', data={'username': 'tester', 'password': 'TestPassword123!'},
                                    headers={'origin': 'https://testserver', 'accept': 'application/json'})
        self.assertEqual(response.json()['redirect'], '/senha')
        self.assertNotIn('Max-Age', response.headers['set-cookie'])

    def test_enhanced_login_errors_and_rate_limit_do_not_issue_cookie(self):
        headers = {'origin': 'https://testserver', 'accept': 'application/json'}
        for _ in range(8):
            response = self.client.post('/login', data={'username': 'tester', 'password': 'wrong'}, headers=headers)
            self.assertEqual(response.status_code, 401)
            self.assertEqual(response.json()['error'], 'Usuário ou senha inválidos.')
            self.assertNotIn('set-cookie', response.headers)
        response = self.client.post('/login', data={}, headers=headers)
        self.assertEqual(response.status_code, 429)
        self.assertIn('15 minutos', response.json()['error'])
        self.assertEqual(response.headers['cache-control'], 'no-store')
        response = self.client.post('/login', data={}, headers={**headers, 'origin': 'https://other.example'})
        self.assertEqual(response.status_code, 403)
        self.assertNotIn('set-cookie', response.headers)

    def test_remembered_session_is_revoked_after_session_version_changes(self):
        response = self.login('&remember=1')
        token = response.cookies[web_auth.COOKIE]
        self.user['session_version'] = 1
        self.assertFalse(web_auth._valid(token))
        self.assertEqual(self.client.get('/login', follow_redirects=False).status_code, 200)

    def test_fixed_assets_do_not_query_database_but_pages_still_validate_session(self):
        self.login('&remember=1')
        self.lookup_mock.reset_mock()
        for asset in preload_assets('/admin'):
            response = self.client.get(asset['href'])
            self.assertEqual(response.status_code, 200, asset)
            self.assertTrue(response.headers['cache-control'].startswith('private,'))
        self.lookup_mock.assert_not_called()
        # Revocation still prevents document/API access. No account data is cached.
        self.user['session_version'] = 1
        self.assertEqual(self.client.get('/admin').status_code, 401)
        self.assertEqual(self.client.get('/api/me').status_code, 401)
        self.assertGreater(self.lookup_mock.call_count, 0)

    def test_asset_shortcut_never_exposes_templates_uploads_or_unsigned_access(self):
        paths = presentation_paths()
        self.assertTrue(paths)
        self.assertNotIn('/tech-assets/admin-console.html', paths)
        self.assertNotIn('/training-media/private.png', paths)
        self.assertNotIn('/api/admin/overview', paths)
        asset = '/tech-assets/admin-console.js?v=test'
        for token in ('', 'forged', web_auth._issue() + 'tampered',
                      web_auth._issue(lifetime=-1)):
            self.client.cookies.set(web_auth.COOKIE, token)
            self.assertEqual(self.client.get(asset).status_code, 401)
        self.lookup_mock.assert_not_called()
        self.client.cookies.set(web_auth.COOKIE, web_auth._issue('test-user'))
        self.assertEqual(self.client.post(asset, headers={'origin': 'https://testserver'}).status_code, 405)

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


class IdentityLookupTests(unittest.TestCase):
    def test_one_indexed_query_without_schema_bootstrap_during_login(self):
        db = MagicMock()
        cursor = db.__enter__.return_value.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = None
        with patch('psycopg.connect', return_value=db), patch('web_auth.ensure_bootstrap_admin') as bootstrap:
            with patch.dict(os.environ, {'CORTEX_ADMIN_USERNAME': 'missing-admin'}):
                self.assertIsNone(web_auth.get_user_by_id_or_username('missing-admin'))
            bootstrap.assert_not_called()
            db.__enter__.return_value.execute.assert_not_called()
            cursor.execute.assert_called_once()
            sql, parameters = cursor.execute.call_args.args
            self.assertIn('id = %s', sql)
            self.assertNotIn('id::text =', sql)
            self.assertEqual(parameters, ('missing-admin', None))
            cursor.execute.reset_mock()
            identifier = '00000000-0000-0000-0000-000000000001'
            web_auth.get_user_by_id_or_username(identifier)
            self.assertEqual(cursor.execute.call_args.args[1], (identifier, UUID(identifier)))

    def test_bootstrap_admin_inserts_configured_admin_alongside_seeded_super_admin(self):
        db = MagicMock()
        conn = db.__enter__.return_value
        with patch('psycopg.connect', return_value=db), patch.dict(os.environ, {
            'CORTEX_ADMIN_USERNAME': 'alternativa',
            'CORTEX_ADMIN_PASSWORD_HASH': 'scrypt$16384$8$1$' + 'a' * 32 + '$' + 'b' * 128,
        }):
            web_auth.ensure_bootstrap_admin()
        insert_calls = [
            call for call in conn.execute.call_args_list
            if len(call.args) == 2 and 'INSERT INTO cortex_users' in call.args[0]
        ]
        self.assertEqual(len(insert_calls), 1)
        self.assertEqual(insert_calls[0].args[1][0], 'alternativa')
        conn.commit.assert_called_once()


if __name__ == '__main__':
    unittest.main()
