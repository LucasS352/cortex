"""Offline browser smoke test. No live integrations, account changes or publication."""
import json
from pathlib import Path
from playwright.sync_api import sync_playwright

STATIC = Path(__file__).parent / 'static'
OUTPUT = Path(__file__).parent / 'output' / 'governance-qa'
SETTINGS = {'pilot_paused': False, 'auto_internal_note': True, 'pilot_organization_ids': ['464'],
            'pilot_requester_emails': [], 'whitelist': [], 'specialists': [],
            'min_interval_seconds': 5, 'max_per_minute': 10, 'max_concurrent': 2,
            'source_types': [], 'draft_generation_enabled': True}
MONITOR = {'items': [], 'checks': [{'label': x, 'ok': True} for x in
           ['Governança habilitada', 'Empresas autorizadas', 'Publicação automática habilitada',
            'Servidor permite nota privada', 'Worker com atividade recente']],
           'mode': 'internal_note', 'worker_alive': True, 'counts': {}}


def route(request):
    from urllib.parse import urlsplit
    path = urlsplit(request.request.url).path
    if path == '/admin':
        request.fulfill(path=str(STATIC / 'admin-console.html'))
    elif path.startswith('/tech-assets/'):
        file = STATIC / path.removeprefix('/tech-assets/')
        if file.is_file():
            import mimetypes
            request.fulfill(path=str(file), content_type={'css':'text/css', 'js':'text/javascript'}.get(file.suffix[1:], mimetypes.guess_type(str(file))[0] or 'application/octet-stream'))
        else:
            request.fulfill(status=404, body='')
    else:
        data = {}
        if path == '/api/admin/settings': data = SETTINGS
        elif path == '/api/admin/automation/monitor': data = MONITOR
        elif path == '/api/me': data = {'id': '1', 'name': 'Administrador de teste', 'role': 'super_admin'}
        elif path == '/api/admin/statuses': data = [{'name': 'Novo'}, {'name': 'Triagem'}]
        elif path == '/api/admin/pilot/companies': data = {'items': [
            {'id': '464', 'name': 'Empresa autorizada', 'selected': True, 'open_tickets': 5},
            {'id': '465', 'name': 'Empresa não autorizada', 'selected': False, 'open_tickets': 17}]}
        elif path == '/api/admin/webhooks/receipts': data = {'total': 0, 'recent': [], 'notes': [],
            'worker': None, 'ceiling_mode': 'internal_note', 'effective_mode': 'internal_note',
            'requested_auto_internal_note': True, 'manual_mode': 'internal_note'}
        request.fulfill(content_type='application/json', body=json.dumps(data))


if __name__ == '__main__':
    OUTPUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(channel='msedge')
        page = browser.new_page(viewport={'width': 1672, 'height': 1000}, device_scale_factor=1)
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.route('http://cortex.test/**', route)
        page.goto('http://cortex.test/admin?view=agent')
        page.wait_for_selector('#gov-checks .gov-check')
        page.evaluate("go('agent')")
        page.locator('[data-tab="tab-operational"]').click()
        assert page.locator('.studio-tab-btn').count() == 7
        for theme in ['light', 'dark']:
            page.evaluate('(theme) => setTheme(theme)', theme)
            for tab in ['tab-operational', 'tab-companies', 'tab-triggers']:
                page.locator(f'[data-tab="{tab}"]').click()
                page.screenshot(path=str(OUTPUT / f'{theme}-{tab}.png'), full_page=True, animations='disabled')
        page.locator('[data-tab="tab-companies"]').click()
        page.locator('[data-company-detail="464"]').click()
        assert page.locator('#gov-company-dialog').is_visible()
        page.locator('#gov-company-close').click()
        page.set_viewport_size({'width': 390, 'height': 844})
        page.locator('[data-tab="tab-operational"]').click()
        page.screenshot(path=str(OUTPUT / 'mobile.png'), full_page=True)
        browser.close()
        assert not errors, errors
        print('Browser smoke passed: 7 tabs, themes, companies and dialog. Screenshots:', OUTPUT)
