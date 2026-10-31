"""Isolated browser regression checks. No real accounts or integrations.

Run with installed Playwright + Edge: python scripts/verify_login_ui.py
The real form/assets are served through intercepted requests; backend authentication
and cookie contracts are covered separately by test_login_experience.
"""
import json
import mimetypes
import shutil
from pathlib import Path
from urllib.parse import urlsplit, parse_qs

from playwright.sync_api import sync_playwright, expect

STATIC = Path(__file__).parent / 'static'
OUTPUT = Path(__file__).parent / 'output' / 'login-qa'


def parse_scale(transform_matrix: str) -> float:
    if not transform_matrix or transform_matrix == 'none':
        return 1.0
    if transform_matrix.startswith('matrix('):
        parts = [float(x.strip()) for x in transform_matrix[7:-1].split(',')]
        return parts[0]
    return 1.0


def run():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel='msedge')
        context = browser.new_context(viewport={'width': 1440, 'height': 900}, reduced_motion='no-preference')
        page = context.new_page()
        errors, posts, destinations, pending_requests = [], [], [], []
        mode = {'login': 'ok', 'destination': 'ok', 'hold_image': False, 'redirect': '/admin'}
        page.on('pageerror', lambda exc: errors.append(str(exc)))

        def route(request):
            path = urlsplit(request.request.url).path
            if path == '/login' and request.request.method == 'POST':
                posts.append(parse_qs(request.request.post_data))
                behavior = mode['login']
                if behavior == 'hang':
                    pending_requests.append(request)
                    return
                if behavior == 'network':
                    request.abort('failed')
                elif behavior == 'invalid':
                    request.fulfill(status=401, json={'error': 'Usuário ou senha inválidos.'})
                elif behavior == 'limited':
                    request.fulfill(status=429, json={'error': 'Muitas tentativas. Aguarde 15 minutos.'})
                elif behavior == 'server':
                    request.fulfill(status=502, body='Bad Gateway', content_type='text/html')
                else:
                    request.fulfill(json={'redirect': mode['redirect']})
            elif path == '/login':
                request.fulfill(path=str(STATIC / 'login.html'), content_type='text/html')
            elif path in ('/admin', '/atendimento', '/em-breve', '/senha'):
                destinations.append(path)
                if mode['destination'] != 'hang':
                    request.fulfill(
                        body=f'<!doctype html><html style="background:#061225;color:#e4edf7;font-family:sans-serif"><body style="margin:32px;background:#101b2b"><h1>Painel de teste ({path})</h1></body></html>',
                        content_type='text/html',
                    )
            elif path.startswith('/tech-assets/'):
                if mode['hold_image'] and path.endswith('neural-brain.webp'):
                    return
                file = STATIC / path.removeprefix('/tech-assets/')
                request.fulfill(path=str(file), content_type=mimetypes.guess_type(file.name)[0])
            else:
                request.fulfill(status=404, body='')

        context.route('http://cortex.test/**', route)

        def open_login():
            page.goto('http://cortex.test/login', wait_until='domcontentloaded')
            expect(page.locator('.password-toggle')).to_be_visible()
            page.locator('#user').fill('tester')
            page.locator('#pass').fill('TestPassword123!')

        # First click works even before a large background asset finishes loading.
        mode['hold_image'] = True
        mode['login'] = 'hang'
        open_login()
        page.locator('[type=submit]').click()
        expect(page.locator('body')).to_have_class('login-pending')
        assert not page.locator('body').evaluate("el => el.classList.contains('login-entering')")
        page.evaluate("document.querySelector('form').dispatchEvent(new Event('submit', {cancelable:true}))")
        page.wait_for_timeout(550)
        expect(page.locator('.login-panel')).to_have_css('opacity', '0')
        assert page.locator('.login-panel').evaluate('el => el.inert')
        # Still waiting for auth response: approach has NOT started yet.
        assert not page.locator('body').evaluate("el => el.classList.contains('login-entering')")
        pending_requests.pop().fulfill(json={'redirect': '/admin'})
        expect(page.locator('body')).to_have_class('login-pending login-zooming login-entering')
        page.wait_for_url('**/admin')
        assert len(posts) == len(destinations) == 1
        assert posts[0]['remember'] == ['0', '1']
        assert page.evaluate("localStorage.getItem('cortex-remembered-username')") == 'tester'
        assert page.evaluate('JSON.stringify(localStorage)').find('TestPassword') == -1
        mode['hold_image'] = False

        # Visual checks use the actual background renderer, not a stub.
        open_login()
        page.wait_for_function("document.querySelector('.brain-structure').naturalWidth > 0")
        page.wait_for_function("document.querySelector('#neural-canvas').dataset.sceneState === 'running'")
        page.screenshot(path=str(OUTPUT / 'form.png'))
        page.locator('[type=submit]').click()
        page.wait_for_timeout(420)
        page.screenshot(path=str(OUTPUT / 'transition.png'))
        # Confirm authentication and verify continuous camera approach sequence
        pending_requests.pop().fulfill(json={'redirect': '/admin'})
        expect(page.locator('body')).to_have_class('login-pending login-zooming login-entering')
        assert page.locator('.neural-scene').evaluate('el => getComputedStyle(el).willChange') == 'transform'
        page.wait_for_timeout(450)
        scale_mid = parse_scale(page.locator('.neural-scene').evaluate('el => getComputedStyle(el).transform'))
        brand_opacity = float(page.locator('.institutional-brand').evaluate('el => getComputedStyle(el).opacity'))
        assert brand_opacity < 0.65, f'Expected institutional logo to fade during approach, got opacity={brand_opacity}'
        page.screenshot(path=str(OUTPUT / 'approach-mid.png'))
        page.wait_for_timeout(400)
        scale_late = parse_scale(page.locator('.neural-scene').evaluate('el => getComputedStyle(el).transform'))
        page.screenshot(path=str(OUTPUT / 'approach-end.png'))
        # Verify camera is continuously advancing (never stopped before navigation)
        assert 1.02 < scale_mid < scale_late < 1.24, f'Expected continuous camera advance, got mid={scale_mid}, late={scale_late}'
        page.wait_for_url('**/admin')

        # Record a short video of the complete login -> camera approach -> dashboard transition.
        video_dir = OUTPUT / 'video-tmp'
        video_dir.mkdir(parents=True, exist_ok=True)
        rec_ctx = browser.new_context(
            viewport={'width': 1440, 'height': 900},
            reduced_motion='no-preference',
            record_video_dir=str(video_dir),
            record_video_size={'width': 1440, 'height': 900},
        )
        rec_ctx.route('http://cortex.test/**', route)
        rec_page = rec_ctx.new_page()
        mode['login'] = 'ok'
        rec_page.goto('http://cortex.test/login', wait_until='domcontentloaded')
        rec_page.wait_for_function("document.querySelector('.brain-structure').naturalWidth > 0")
        rec_page.wait_for_function("document.querySelector('#neural-canvas').dataset.sceneState === 'running'")
        rec_page.locator('#user').fill('lucas.souza')
        rec_page.locator('#pass').fill('TestPassword123!')
        rec_page.wait_for_timeout(450)
        rec_page.locator('[type=submit]').click()
        rec_page.wait_for_url('**/admin')
        rec_page.wait_for_timeout(500)
        video_path = rec_page.video.path()
        rec_ctx.unroute_all(behavior='ignoreErrors')
        rec_ctx.close()
        shutil.copyfile(video_path, OUTPUT / 'login-approach.webm')
        shutil.rmtree(video_dir, ignore_errors=True)
        mode['login'] = 'hang'

        for failure, message in [('invalid', 'Usuário ou senha inválidos.'),
                                 ('network', 'Não foi possível conectar'),
                                 ('limited', 'Muitas tentativas'), ('server', 'Não foi possível conectar')]:
            mode['login'] = failure
            open_login()
            page.locator('[type=submit]').click()
            expect(page.locator('#login-error')).to_contain_text(message)
            expect(page.locator('[type=submit]')).to_be_enabled()
            assert not page.locator('.login-panel').evaluate('el => el.inert')
            assert page.locator('body').evaluate('el => el.className') == ''
            assert page.locator('.neural-scene').evaluate('el => getComputedStyle(el).willChange') == 'auto'
            # Recover on the same page, without reload.
            mode['login'] = 'ok'
            page.locator('[type=submit]').click()
            page.wait_for_url('**/admin')

        # Virtual time exercises the real watchdog without a 15-second test sleep.
        page.clock.install()
        mode['login'] = 'hang'
        open_login()
        page.locator('[type=submit]').click()
        page.clock.fast_forward(4001)
        expect(page.locator('body')).to_have_class('login-pending login-slow')
        page.clock.fast_forward(11000)
        expect(page.locator('#login-error')).to_contain_text('A conexão demorou')
        expect(page.locator('[type=submit]')).to_be_enabled()
        mode['login'] = 'ok'
        page.emulate_media(reduced_motion='reduce')
        page.locator('[type=submit]').click()
        page.wait_for_url('**/admin')

        # Navigation timeout also restores the form instead of a stuck background.
        mode['destination'] = 'hang'
        open_login()
        with page.expect_request('**/admin'):
            page.locator('[type=submit]').click(no_wait_after=True)
        page.clock.fast_forward(20001)
        expect(page.locator('#login-error')).to_contain_text('O painel demorou')
        expect(page.locator('[type=submit]')).to_be_enabled()
        assert page.locator('body').evaluate('el => el.className') == ''
        assert page.locator('.neural-scene').evaluate('el => getComputedStyle(el).willChange') == 'auto'
        mode['destination'] = 'ok'
        page.locator('[type=submit]').click()
        page.wait_for_url('**/admin')

        # BFCache / history pageshow restoration resets transition classes cleanly.
        open_login()
        page.evaluate("""() => {
            document.body.classList.add('login-pending', 'login-zooming', 'login-entering');
            window.dispatchEvent(new PageTransitionEvent('pageshow', { persisted: true }));
        }""")
        assert page.locator('body').evaluate('el => el.className') == ''
        expect(page.locator('[type=submit]')).to_be_enabled()

        open_login()
        page.set_viewport_size({'width': 390, 'height': 844})
        page.screenshot(path=str(OUTPUT / 'mobile.png'), full_page=True)
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.locator('[name=remember][type=checkbox]').uncheck()
        assert page.evaluate("localStorage.getItem('cortex-remembered-username')") is None
        page.locator('[type=submit]').click()
        page.wait_for_url('**/admin')
        assert posts[-1]['remember'] == ['0']
        assert not errors, errors
        context.unroute_all(behavior='ignoreErrors')
        browser.close()
        print(json.dumps({
            'result': 'passed',
            'login_requests': len(posts),
            'scale_mid': round(scale_mid, 4),
            'scale_late': round(scale_late, 4),
            'screenshots': str(OUTPUT),
        }))


if __name__ == '__main__':
    run()
