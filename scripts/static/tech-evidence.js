/* Internal desk: sources and independent search remain available without the LLM. */
(() => {
    'use strict';
    let current = null, epoch = 0, nextOffset = null, searchText = '', sending = false;
    let openEpoch = 0;
    const drafts = new Map(), selected = new Set();
    const make = (tag, text, parent) => {
        const el = document.createElement(tag);
        if (text !== undefined) el.textContent = text;
        if (parent) parent.append(el);
        return el;
    };
    const button = (text, fn, parent) => {
        const el = make('button', text, parent);
        el.type = 'button'; el.addEventListener('click', fn); return el;
    };
    async function json(url, options) {
        const controller = new AbortController();
        const timer = setTimeout(() => controller.abort(), 15000);
        try {
            const response = await fetch(url, {...options, signal: controller.signal});
            const data = await response.json();
            if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Não foi possível concluir a operação.');
            return data;
        } finally { clearTimeout(timer); }
    }
    const root = document.getElementById('tech-manuals-box');
    if (!root) return;
    root.classList.add('evidence-panel');
    make('h3', 'Chamados semelhantes e evidências', root);
    make('p', 'Confira o histórico original antes de aprovar. Similaridade não comprova a solução.', root);
    const sourceList = make('div', undefined, root);
    const form = make('form', undefined, root);
    const input = make('input', undefined, form);
    input.placeholder = 'Outro erro, sintoma ou #protocolo';
    input.maxLength = 2000; input.setAttribute('aria-label', 'Buscar outros chamados');
    const searchButton = button('Buscar outros chamados', () => search(false), form);
    form.addEventListener('submit', event => {event.preventDefault(); search(false);});
    const message = make('p', 'Abra um atendimento no simulador e atualize a Mesa do Técnico para consultar as referências.', root); message.setAttribute('role', 'status');
    const caseList = make('div', undefined, root);
    const more = button('Carregar mais', () => search(true), root); more.hidden = true;

    const editorBox = make('section', undefined, root);
    make('h3', 'Resposta revisada pelo técnico', editorBox);
    const editor = make('textarea', undefined, editorBox); editor.rows = 9;
    editor.setAttribute('aria-label', 'Resposta revisada pelo técnico');
    const reason = make('textarea', undefined, editorBox); reason.rows = 2;
    reason.placeholder = 'Se corrigiu, explique o motivo (diagnóstico errado, versão diferente…)';
    reason.setAttribute('aria-label', 'Motivo da correção');
    const save = button('Registrar resposta revisada', () => saveReview(), editorBox);
    const reviewStatus = make('p', '', editorBox); reviewStatus.setAttribute('role', 'status');
    input.disabled = searchButton.disabled = editor.disabled = reason.disabled = save.disabled = true;
    make('p', 'A revisão fica registrada localmente para curadoria. Não envia mensagem ao cliente nem encerra o chamado.', editorBox);
    editor.addEventListener('input', remember); reason.addEventListener('input', remember);
    function remember() {
        if (current) drafts.set(current.session_id, {text: editor.value, reason: reason.value, selected: [...selected]});
    }
    const dialog = make('dialog', undefined, document.body); dialog.className = 'evidence-dialog';
    const close = button('Fechar histórico', () => {openEpoch++; dialog.close();}, dialog);
    const detailBody = make('div', undefined, dialog);
    dialog.addEventListener('close', () => {openEpoch++;});
    async function open(item) {
        const token = ++openEpoch;
        detailBody.replaceChildren(); make('p', 'Carregando histórico…', detailBody);
        if (!dialog.open) dialog.showModal(); close.focus();
        try {
            const data = await json(item.detail_url);
            if (token !== openEpoch) return;
            detailBody.replaceChildren();
            make('h2', (data.ticket_id ? `Chamado #${data.ticket_id} — ` : '') + data.title, detailBody);
            make('p', `Estado registrado: ${data.status} • Importação: ${data.snapshot_at || 'Não informada'}`, detailBody);
            make('p', data.notice, detailBody);
            const summary = make('details', undefined, detailBody);
            make('summary', 'Resumo processado pelo Romário — não é o texto original', summary);
            make('pre', data.summary.solution || 'Sem solução resumida.', summary);
            make('h3', 'Conversa original', detailBody);
            if (data.original_status !== 'available') make('p', 'Histórico original indisponível. O resumo não substitui a conferência da fonte.', detailBody);
            for (const action of data.actions) {
                const entry = make('article', undefined, detailBody);
                make('h4', `Ação ${action.id ?? 'sem ID'} • ${action.author} • ${action.created_at || 'Sem data'}${action.is_internal ? ' • Nota interna' : ''}`, entry);
                make('pre', action.text || 'Ação sem texto.', entry);
            }
        } catch (error) {
            if (token !== openEpoch) return;
            detailBody.replaceChildren(); make('p', error.message, detailBody);
            button('Tentar novamente', () => open(item), detailBody);
        }
    }
    function renderCards(items, container, append = false) {
        if (!append) container.replaceChildren();
        for (const item of items) {
            const card = make('article', undefined, container);
            make('small', item.relationship === 'provided_to_model' ? 'Fornecido ao Romário nesta pré-resposta' : 'Outro chamado semelhante', card);
            button(`${item.ticket_id ? '#' + item.ticket_id + ' — ' : ''}${item.title}`, () => open(item), card);
            make('p', (item.modules || []).join(' / ') || 'Módulo não informado', card);
            make('small', `Versão na fonte: ${item.version_reference || 'Não informada'}`, card);
            make('p', item.solution_excerpt || 'Sem solução resumida. Confira o histórico.', card);
            make('small', 'Solução registrada; validação e compatibilidade de versão precisam ser conferidas.', card);
            const label = make('label', undefined, card);
            const checkbox = make('input', undefined, label); checkbox.type = 'checkbox'; checkbox.checked = selected.has(item.node_id);
            make('span', ' Usar como referência da minha revisão', label);
            checkbox.addEventListener('change', () => {
                if (checkbox.checked) selected.add(item.node_id); else selected.delete(item.node_id);
                remember();
            });
        }
    }
    async function search(append = false) {
        if (!current) return;
        const token = ++epoch, sid = current.session_id;
        if (!append) {searchText = input.value.trim(); nextOffset = null; caseList.replaceChildren();}
        more.hidden = true; message.textContent = 'Consultando chamados, sem depender da IA…';
        const params = new URLSearchParams({q: searchText, offset: String(append ? nextOffset || 0 : 0)});
        try {
            const data = await json(`/api/tech/references/${encodeURIComponent(sid)}?${params}`);
            if (token !== epoch || current.session_id !== sid) return;
            renderCards(data.sources, sourceList);
            if (!data.sources.length) make('p', 'Esta pré-resposta não registrou fontes utilizadas. Os exemplos abaixo foram buscados separadamente.', sourceList);
            renderCards(data.items, caseList, append);
            nextOffset = data.next_offset; more.hidden = nextOffset === null;
            message.textContent = data.items.length ? 'Abra os exemplos para comparar as soluções.' : 'Nenhum chamado de referência encontrado para esta busca.';
        } catch (error) {
            if (token === epoch) message.textContent = error.message;
        }
    }
    async function saveReview() {
        if (!current || sending) return;
        sending = true; save.disabled = true;
        const sid = current.session_id;
        try {
            const decision = !current.latest_solution ? 'manual' : editor.value === current.latest_solution ? 'approved' : 'edited';
            if (decision === 'edited' && !reason.value.trim()) throw new Error('Explique o motivo da correção para registrar a revisão.');
            const payload = {session_id: sid, expected_revision: current.revision, final_response: editor.value,
                selected_evidence_ids: [...selected], decision, correction_reason: reason.value};
            const signature = JSON.stringify(payload);
            // Retrying an uncertain response reuses its idempotency key.
            if (current.requestSignature !== signature) {current.requestSignature = signature; current.requestId = crypto.randomUUID();}
            await json('/api/tech/review', {method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({...payload, request_id: current.requestId})});
            if (current.session_id === sid) reviewStatus.textContent = 'Revisão registrada com sugestão original e referências. Chamado permanece em acompanhamento.';
        } catch (error) {if (current.session_id === sid) reviewStatus.textContent = error.message;}
        finally {sending = false; save.disabled = false;}
    }
    window.techEvidence = {
        load(data) {
            remember();
            const changed = !current || current.session_id !== data.session_id || current.revision !== data.revision;
            const previous = current;
            current = changed ? data : {...data, requestSignature: previous.requestSignature, requestId: previous.requestId};
            if (!changed) return;
            input.disabled = searchButton.disabled = editor.disabled = reason.disabled = save.disabled = false;
            epoch++; openEpoch++; dialog.close(); sourceList.replaceChildren(); caseList.replaceChildren();
            input.value = ''; selected.clear();
            const draft = drafts.get(data.session_id);
            editor.value = draft ? draft.text : data.latest_solution || '';
            reason.value = draft ? draft.reason : '';
            for (const id of draft?.selected || []) selected.add(id);
            reviewStatus.textContent = draft ? 'Rascunho preservado. Confira a pré-resposta atual antes de registrar.' : '';
            search(false);
        },
        edit() { editor.scrollIntoView({block: 'center'}); editor.focus(); },
        save: saveReview,
    };
})();
