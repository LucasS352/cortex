"""Offline browser QA and reproducible timing comparison; never contacts Movidesk.

python scripts/verify_training_ui.py --baseline-ref dfd22de
Timings use fixed fixture latency (50 ms/read; 800 ms/live refresh), not production measurements.
"""
import argparse
import asyncio
import json
import statistics
import subprocess
import time
from pathlib import Path
from urllib.parse import urlsplit, parse_qs

from playwright.async_api import async_playwright

STATIC = Path(__file__).parent / 'static'
OUTPUT = Path(__file__).parent / 'output' / 'training-qa'
TAXONOMY = [{'key':'logistics','label':'Logística','description':'QA','nodes':[],'subtopics':[]}]
CANDIDATE = {'id':9,'ticket_external_id':42,'status':'pending','usefulness_score':70,
    'suggested_module':'logistics','suggested_subtopic':'general','extractor':'heuristic',
    'created_by':'Técnico QA','created_at':'2026-10-30T12:00:00Z','quality_checks':[],
    'similar_items':[],'source_answer_action_id':'2', 'answer_reviews':[],
    'extraction':{'title':'Procedimento QA','symptom':'Exemplo fictício','procedure':'1. Conferir cadastro e validar.',
                  'keywords':[], 'applicability':{},'images':[]},
    'ticket_context':{'subject':'Exemplo fictício','question':'Como conferir?',
        'technician_reply':'1. Conferir cadastro e validar.', 'images':[],
        'history':[{'id':2,'role':'technician','author':'QA','text':'1. Conferir cadastro e validar.',
                    'html':'<p>1. Conferir cadastro e validar.</p>','images':[]}]}}


async def check(browser, html, mode, capture=False):
    context = await browser.new_context(viewport={'width':1500,'height':1000})
    page = await context.new_page()
    errors, calls = [], []
    page.on('pageerror', lambda e: errors.append(str(e)))
    paused = {}

    async def route(request):
        url = urlsplit(request.request.url)
        path, query = url.path, parse_qs(url.query)
        if url.hostname != 'cortex.test':
            await request.fulfill(status=200, body='')
            return
        if path == '/admin':
            await request.fulfill(content_type='text/html', body='<iframe src="/treinamento" style="width:100%;height:95vh;border:0"></iframe>')
            return
        if path == '/treinamento':
            await request.fulfill(content_type='text/html', body=html)
            return
        if path == '/treinamento/tecnico':
            await request.fulfill(path=str(STATIC/'treinamento_tecnico.html'))
            return
        if path.startswith('/tech-assets/'):
            file = STATIC/path.removeprefix('/tech-assets/')
            if file.is_file(): await request.fulfill(path=str(file))
            else: await request.fulfill(status=404, body='')
            return
        calls.append(path)
        data = {}
        await asyncio.sleep(.8 if path.endswith('/respostas-tecnicas') else .05)
        if path.endswith('/taxonomia'): data = TAXONOMY
        elif path.endswith('/imagens'): data = []
        elif path.endswith('/overview'):
            data = {'totals':{'pending':60},'modules':[{**TAXONOMY[0],'items':0,'pending':60,'subtopics':[]}]}
        elif path.endswith('/candidatos'):
            offset=int(query.get('offset',['0'])[0]); limit=int(query.get('limit',['100'])[0])
            data=[{**CANDIDATE,'id':i,'title':f'Procedimento QA {i}'} for i in range(1,61)][offset:offset+limit]
        elif path.endswith('/candidatos/9'): data = CANDIDATE
        elif path.endswith('/respostas-tecnicas'):
            data = {'answers':[{'action_id':'2','html':'<p>1. Conferir cadastro e validar.</p>'}],'recommended_action_id':'2'}
        elif '/tecnico/tickets/' in path:
            ticket=int(path.split('/tickets/')[1].split('/')[0])
            if path.endswith('/agente'):
                assert request.request.headers.get('x-cortex-technician-pin') == '7531'
                paused[ticket] = request.request.post_data_json['paused']
                data={'ticket_id':ticket,'paused':paused[ticket],'actor':'QA'}
            else:
                data={'technician':'QA','taxonomy':TAXONOMY,'context':{'subject':'Exemplo fictício','answers':[],
                      'questions':[], 'technician_answers':[]},'agent_control':{'ticket_id':ticket,'paused':paused.get(ticket,False)}}
        await request.fulfill(content_type='application/json', body=json.dumps(data))

    await page.route('**/*', route)
    start = time.perf_counter()
    if mode == 'review':
        await page.goto('http://cortex.test/treinamento?candidate=9', wait_until='domcontentloaded')
        await page.locator('#dlg[open]').wait_for()
        elapsed = round((time.perf_counter()-start)*1000,2)
        assert await page.locator('#f_title').input_value() == 'Procedimento QA'
        if capture: await page.screenshot(path=str(OUTPUT/'candidate-review.png'))
    elif mode == 'queue':
        await page.goto('http://cortex.test/admin', wait_until='domcontentloaded')
        frame = page.frame_locator('iframe')
        await frame.locator('#list .row').first.wait_for()
        elapsed = round((time.perf_counter()-start)*1000,2)
        initial_calls = list(calls)
        if capture:
            assert await frame.locator('#list .row').count() == 50
            await frame.locator('#queueNext').click()
            await frame.locator('#queuePrev:not([disabled])').wait_for()
            assert await frame.locator('#list .row').count() == 10
            await frame.locator('#queuePrev').click()
            await frame.locator('#queueNext:not([disabled])').wait_for()
            await page.screenshot(path=str(OUTPUT/'curation-queue.png'))
    else:
        async def accept(dialog): await dialog.accept()
        page.on('dialog', accept)
        await page.goto('http://cortex.test/treinamento/tecnico?ticket=42')
        assert await page.locator('#ticketAgentToggle').is_disabled()
        await page.locator('#pin').fill('7531')
        await page.locator('#loadBtn').click()
        await page.locator('#ticketAgentToggle:not([disabled])').wait_for()
        await page.locator('#ticketAgentToggle').click()
        await page.get_by_role('button',name='Reativar agente',exact=True).wait_for()
        assert paused[42]
        await page.reload()
        await page.get_by_role('button',name='Reativar agente',exact=True).wait_for()
        if capture: await page.screenshot(path=str(OUTPUT/'technician-paused.png'))
        await page.locator('#ticket').fill('43')
        assert await page.locator('#ticketAgentToggle').is_disabled()
        await page.locator('#loadBtn').click()
        await page.locator('#ticketAgentToggle:not([disabled])').wait_for()
        assert await page.locator('#ticketAgentToggle').text_content() == 'Pausar agente neste chamado'
        await page.locator('#ticket').fill('42')
        await page.locator('#loadBtn').click()
        await page.get_by_role('button',name='Reativar agente',exact=True).wait_for()
        await page.locator('#ticketAgentToggle').click()
        await page.get_by_role('button',name='Pausar agente neste chamado',exact=True).wait_for()
        assert not paused[42]
        elapsed = 0
    assert not errors, errors
    await context.close()
    return {'ms':elapsed,'calls':initial_calls if mode == 'queue' else calls}


async def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--baseline-ref')
    args=parser.parse_args()
    OUTPUT.mkdir(parents=True,exist_ok=True)
    versions={'after':(STATIC/'treinamento.html').read_text(encoding='utf-8')}
    if args.baseline_ref:
        versions['before']=subprocess.check_output(['git','show',f'{args.baseline_ref}:scripts/static/treinamento.html']).decode('utf-8')
    report={'method':'offline fixtures; each API read 50 ms, extra live-refresh endpoint 800 ms','results':{}}
    async with async_playwright() as p:
        browser=await p.chromium.launch(channel='msedge')
        for name,html in versions.items():
            report['results'][name]={}
            for mode in ['queue','review']:
                runs=[await check(browser,html,mode,capture=(name=='after' and i==0)) for i in range(3)]
                report['results'][name][mode]={'median_ms':round(statistics.median(r['ms'] for r in runs),2),
                                             'calls':runs[0]['calls']}
        await check(browser,versions['after'],'technician',capture=True)
        from movidesk_note_worker import render_note
        note = render_note({'reply':'Bom dia!\n1. Confira o cadastro indicado.\n2. Salve e valide o resultado.',
            'sources':[{'title':'Manual de referência'}],
            'similar_tickets':[{'ticket':12345,'title':'Exemplo fictício','status':'Concluído',
                                'resolution':'Cadastro conferido e resultado validado.'}],
            'specialist':{'name':'Integrações'},
            'decision':{'state':'PROCEDIMENTO_CONFIRMADO','confidence':'ALTA'}}, 'CORTEX-AI-NOTE:qa')
        preview = '<!doctype html><html><meta charset="utf-8"><style>body{font:14px Arial;padding:24px;max-width:1000px;color:#24384d}p{margin:0 0 14px}</style>' + note + '</html>'
        (OUTPUT/'note-preview.html').write_text(preview,encoding='utf-8')
        page=await browser.new_page(viewport={'width':1100,'height':600})
        await page.set_content(preview)
        assert await page.locator('body > p').first.evaluate('(e)=>getComputedStyle(e).fontSize') == '14px'
        assert await page.locator('body > div p').first.evaluate('(e)=>getComputedStyle(e).fontSize') == '12px'
        await page.screenshot(path=str(OUTPUT/'note-preview.png'))
        await page.close()
        await browser.close()
    (OUTPUT/'timings.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report,indent=2))


if __name__ == '__main__': asyncio.run(main())
