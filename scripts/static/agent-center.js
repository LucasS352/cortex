/* ==========================================================================
   DeskCortex — Agent Studio & Policies Interactive Controller
   ========================================================================== */

const studioState = {
  activeTab: 'tab-sandbox',
  whitelist: [],
  pilotRequesterEmails: [],
  pilotCompanies: [],
  selectedCompanyIds: [],
  specialists: []
};

function initStudioTabs() {
  document.querySelectorAll('.studio-tab-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.studio-tab-btn').forEach(b => b.classList.remove('active'));
      document.querySelectorAll('.studio-tab-content').forEach(c => c.classList.remove('active'));
      btn.classList.add('active');
      const tabId = btn.dataset.tab;
      const content = $(tabId);
      if (content) content.classList.add('active');
      studioState.activeTab = tabId;

      // Se entrou no playground e nao tem historico ainda, carrega o cenario padrao sob demanda
      if (tabId === 'tab-playground' && currentTicket.messages.length === 0) {
        loadTicketScenario('loja_integrada');
      }
    });
  });
}

// --- Whitelist Tags Management ---
function renderWhitelistTags() {
  const container = $('whitelist-tags-container');
  if (!container) return;
  if (!studioState.whitelist.length) {
    container.innerHTML = '<span class="muted" style="font-size:12px; padding:6px 0;">Nenhuma conta na lista. Adicione e-mails ou nomes de clientes de teste.</span>';
    return;
  }
  container.innerHTML = studioState.whitelist.map((item, idx) => `
    <span class="whitelist-tag">
      <span>${esc(item)}</span>
      <button type="button" class="tag-remove" data-idx="${idx}" title="Remover da Whitelist">×</button>
    </span>
  `).join('');

  container.querySelectorAll('.tag-remove').forEach(btn => {
    btn.addEventListener('click', (e) => {
      const idx = Number(e.currentTarget.dataset.idx);
      studioState.whitelist.splice(idx, 1);
      renderWhitelistTags();
      checkPlaygroundSandboxStatus();
    });
  });
}

function addWhitelistItem(val) {
  const clean = (val || '').trim();
  if (!clean) return;
  if (!studioState.whitelist.includes(clean)) {
    studioState.whitelist.push(clean);
    renderWhitelistTags();
    checkPlaygroundSandboxStatus();
  }
  $('whitelist-input').value = '';
}

function renderPilotRequesterTags() {
  const container = $('pilot-requester-tags-container');
  if (!container) return;
  if (!studioState.pilotRequesterEmails.length) {
    container.innerHTML = '<span class="muted" style="font-size:12px; padding:6px 0;">Todos os solicitantes das empresas autorizadas estão liberados (sem filtro por e-mail).</span>';
    return;
  }
  container.innerHTML = studioState.pilotRequesterEmails.map((email, idx) => `
    <span class="whitelist-tag">
      <span>${esc(email)}</span>
      <button type="button" class="tag-remove" data-idx="${idx}" title="Remover solicitante">×</button>
    </span>`).join('');
  container.querySelectorAll('.tag-remove').forEach(btn => {
    btn.addEventListener('click', (event) => {
      studioState.pilotRequesterEmails.splice(Number(event.currentTarget.dataset.idx), 1);
      renderPilotRequesterTags();
    });
  });
}

function addPilotRequesterEmail(value) {
  const email = (value || '').trim().toLowerCase();
  if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) {
    showError('Informe um e-mail válido para o solicitante autorizado.');
    return;
  }
  if (!studioState.pilotRequesterEmails.includes(email)) studioState.pilotRequesterEmails.push(email);
  renderPilotRequesterTags();
  $('pilot-requester-input').value = '';
}

function renderPilotCompanies() {
  if (window.renderGovernanceCompanies) return window.renderGovernanceCompanies();
  const container = $('pilot-company-results');
  if (!container) return;
  if (!studioState.pilotCompanies.length) {
    container.innerHTML = '<p class="muted">Nenhuma empresa encontrada. Pesquise pelo nome ou ID do Movidesk.</p>';
    return;
  }
  container.innerHTML = studioState.pilotCompanies.map(company => `
    <button type="button" class="pilot-company-row ${company.selected ? 'selected' : ''}"
      data-company-id="${esc(company.id)}" data-selected="${company.selected ? 'true' : 'false'}">
      <span><b>${esc(company.name)}</b><small>ID ${esc(company.id)} · ${fmt(company.open_tickets)} chamado(s) aberto(s)</small></span>
      <strong>${company.selected ? '✓ Autorizada' : '+ Autorizar'}</strong>
    </button>`).join('');
}

let pilotCompanyRequest = 0;
let pilotCompanySearchTimer;
async function loadPilotCompanies(search = '') {
  const request = ++pilotCompanyRequest;
  const container = $('pilot-company-results');
  if (container) container.innerHTML = '<p class="muted">Consultando empresas…</p>';
  try {
    const data = await api(`/api/admin/pilot/companies?search=${encodeURIComponent(search)}&limit=30`);
    if (request !== pilotCompanyRequest) return;
    studioState.pilotCompanies = data.items || [];
    renderPilotCompanies();
  } catch (err) {
    if (request !== pilotCompanyRequest) return;
    if (container) container.innerHTML = '<p class="muted">Não foi possível consultar as empresas.</p>';
    showError(err.message);
  }
}

async function togglePilotCompany(companyId, selected) {
  await api(`/api/admin/pilot/companies/${encodeURIComponent(companyId)}`, {
    method: 'PUT', body: JSON.stringify({ selected })
  });
  const settings = await api('/api/admin/settings');
  renderStudioSettings(settings);
}

// --- Render Studio Settings ---
function renderStudioSettings(settings) {
  if ($('pilot-scope-status')) {
    const released = settings.automation_released_at;
    const date = released ? new Date(released * 1000).toLocaleString('pt-BR', {timeZone:'America/Sao_Paulo'}) : null;
    $('pilot-scope-status').textContent = 'Somente chamados criados após a liberação e enquanto estiverem em Triagem. ' +
      (date ? `Liberação: ${date} (Brasília). Novas empresas contam a partir da própria autorização.` :
        'A data de liberação será registrada ao ativar a governança e salvar as configurações.');
  }
  window.governanceSettings = settings;
  window.refreshGovernance?.();
  studioState.whitelist = Array.isArray(settings.whitelist) ? [...settings.whitelist] : ['lucas.teste@alternativa.com.br'];
  studioState.pilotRequesterEmails = Array.isArray(settings.pilot_requester_emails) ? [...settings.pilot_requester_emails] : [];
  studioState.specialists = Array.isArray(settings.specialists) ? JSON.parse(JSON.stringify(settings.specialists)) : [];

  // Sandbox switch & controls
  const sandboxOn = !settings.pilot_paused;
  studioState.selectedCompanyIds = Array.isArray(settings.pilot_organization_ids) ? [...settings.pilot_organization_ids] : [];
  $('setting-sandbox-mode').checked = sandboxOn;
  $('setting-draft-enabled').checked = !!settings.draft_generation_enabled;
  $('setting-auto-internal-note').checked = !!settings.auto_internal_note;
  renderPilotRequesterTags();
  loadPilotCompanies($('pilot-company-search')?.value || '');
  loadWebhookReceipts();

  // Badge Status
  const badge = $('studio-status-badge');
  const pill = $('studio-mode-pill');
  if (settings.pilot_paused) {
    badge.className = 'studio-badge-pulse paused';
    badge.textContent = 'AUTOMAÇÃO PAUSADA';
    pill.textContent = 'Nenhuma nota será publicada';
  } else if (!settings.automation_released_at) {
    badge.className = 'studio-badge-pulse paused';
    badge.textContent = 'CONFIRME A LIBERAÇÃO';
    pill.textContent = 'Salve as configurações para liberar somente chamados novos em Triagem';
  } else if (studioState.selectedCompanyIds.length) {
    badge.className = 'studio-badge-pulse';
    badge.textContent = 'EMPRESAS AUTORIZADAS';
    pill.textContent = studioState.pilotRequesterEmails.length
      ? `Restrito a ${studioState.selectedCompanyIds.length} empresa(s) e ${studioState.pilotRequesterEmails.length} solicitante(s)`
      : `Liberado para todas as pessoas de ${studioState.selectedCompanyIds.length} empresa(s) autorizada(s)`;
  } else {
    badge.className = 'studio-badge-pulse paused';
    badge.textContent = 'SELECIONE EMPRESAS';
    pill.textContent = 'Nenhuma empresa autorizada';
  }

  // Sliders
  $('pace-min-interval').value = settings.min_interval_seconds ?? 5;
  $('pace-min-interval-val').textContent = `${$('pace-min-interval').value} s`;
  $('pace-max-minute').value = settings.max_per_minute ?? 10;
  $('pace-max-minute-val').textContent = `${$('pace-max-minute').value} req/min`;
  $('pace-max-concurrent').value = settings.max_concurrent ?? 2;
  $('pace-max-concurrent-val').textContent = `${$('pace-max-concurrent').value}`;

  // Source types
  const permittedSources = new Set(settings.source_types || ['OFFICIAL_KB', 'VALIDATED_TICKET', 'PROBABLE_TICKET', 'MANUAL']);
  document.querySelectorAll('.source-toggle').forEach(t => {
    t.checked = permittedSources.has(t.value);
  });

  renderWhitelistTags();
  renderSpecialistsCards();
  checkPlaygroundSandboxStatus();
}

// --- Specialists Rendering ---
function renderSpecialistsCards() {
  const grid = $('specialists-cards-grid');
  if (!grid) return;

  const countActive = studioState.specialists.filter(s => s.enabled !== false).length;
  $('active-specialists-count').textContent = countActive;

  grid.innerHTML = studioState.specialists.map((spec, idx) => `
    <article class="specialist-card" data-idx="${idx}">
      <div class="spec-card-top">
        <div class="spec-info-row">
          <span class="spec-avatar">${esc(spec.icon || '🧠')}</span>
          <div class="spec-title-box">
            <h4>${esc(spec.name)}</h4>
            <span class="spec-status-pill ${spec.enabled !== false ? 'active' : 'inactive'}">
              ${spec.enabled !== false ? 'ATIVO' : 'DESATIVADO'}
            </span>
          </div>
        </div>
        <label class="switch" title="Ativar ou desativar este especialista">
          <input type="checkbox" class="spec-toggle" data-idx="${idx}" ${spec.enabled !== false ? 'checked' : ''}>
          <span></span>
        </label>
      </div>

      <p class="spec-desc">${esc(spec.description || 'Sem descrição cadastrada.')}</p>

      <div class="spec-keywords-block">
        <span class="spec-kw-title">Gatilhos de Acionamento:</span>
        <div class="spec-kw-list">
          ${(spec.keywords || []).slice(0, 8).map(k => `<span class="spec-kw-chip">${esc(k)}</span>`).join('')}
          ${(spec.keywords || []).length > 8 ? `<span class="spec-kw-chip">+${spec.keywords.length - 8}</span>` : ''}
        </div>
      </div>

      <div class="spec-card-actions">
        <button type="button" class="button button-quiet edit-spec-btn" data-idx="${idx}">
          ✏️ Editar Prompt & Diretrizes
        </button>
        ${spec.id && ['products','fr3_logistics','integrations','fiscal','sales_pdv','infrastructure'].includes(spec.id) ? '' : `
          <button type="button" class="button button-quiet delete-spec-btn" data-idx="${idx}" title="Excluir especialista" style="color:#ef4444;">
            Excluir
          </button>
        `}
      </div>
    </article>
  `).join('');

  // Toggle events
  grid.querySelectorAll('.spec-toggle').forEach(chk => {
    chk.addEventListener('change', (e) => {
      const idx = Number(e.currentTarget.dataset.idx);
      studioState.specialists[idx].enabled = e.currentTarget.checked;
      renderSpecialistsCards();
    });
  });

  // Edit events
  grid.querySelectorAll('.edit-spec-btn').forEach(btn => {
    btn.addEventListener('click', (e) => {
      const idx = Number(e.currentTarget.dataset.idx);
      openSpecialistModal(studioState.specialists[idx], idx);
    });
  });

  // Delete events
  grid.querySelectorAll('.delete-spec-btn').forEach(btn => {
    btn.addEventListener('click', (e) => {
      const idx = Number(e.currentTarget.dataset.idx);
      if (confirm(`Deseja excluir o especialista "${studioState.specialists[idx].name}"?`)) {
        studioState.specialists.splice(idx, 1);
        renderSpecialistsCards();
      }
    });
  });
}

// --- Specialist Modal ---
let editingSpecIdx = null;

function openSpecialistModal(spec, idx = null) {
  editingSpecIdx = idx;
  const modal = $('specialist-modal');
  $('specialist-modal-title').textContent = idx !== null ? `Editar ${spec.name}` : 'Criar Novo Especialista';
  $('spec-edit-id').value = spec?.id || `spec_${Date.now()}`;
  $('spec-edit-name').value = spec?.name || '';
  $('spec-edit-icon').value = spec?.icon || '🧠';
  $('spec-edit-desc').value = spec?.description || '';
  $('spec-edit-keywords').value = (spec?.keywords || []).join(', ');
  $('spec-edit-prompt').value = spec?.prompt || '';
  modal.showModal();
}

function closeSpecialistModal() {
  const modal = $('specialist-modal');
  if (modal.open) modal.close();
}

// --- Save Studio Settings ---
async function saveAllStudioSettings() {
  const btn = $('save-studio-btn');
  btn.disabled = true;
  btn.textContent = 'Salvando...';

  const source_types = [...document.querySelectorAll('.source-toggle:checked')].map(x => x.value);
  const payload = {
    sandbox_mode: true,
    pilot_paused: !$('setting-sandbox-mode').checked,
    auto_internal_note: $('setting-auto-internal-note').checked,
    whitelist: studioState.whitelist,
    pilot_requester_emails: studioState.pilotRequesterEmails,
    draft_generation_enabled: $('setting-draft-enabled').checked,
    min_interval_seconds: Number($('pace-min-interval').value),
    max_per_minute: Number($('pace-max-minute').value),
    max_concurrent: Number($('pace-max-concurrent').value),
    source_types,
    specialists: studioState.specialists
  };

  try {
    const updated = await api('/api/admin/settings', {
      method: 'PUT',
      body: JSON.stringify(payload)
    });
    renderStudioSettings(updated);
    btn.textContent = '✓ Configurações Salvas!';
    setTimeout(() => { btn.textContent = 'Salvar Configurações'; }, 2000);
  } catch (err) {
    showError('Erro ao salvar configurações: ' + err.message);
    btn.textContent = 'Salvar Configurações';
  } finally {
    btn.disabled = false;
  }
}

async function savePilotToggle(inputId, settingName, toStored = value => value, fromStored = value => !!value) {
  const input = $(inputId);
  const status = $('pilot-toggle-save-status');
  if (!input) return;

  const requestedValue = input.checked;
  let saved = false;
  input.disabled = true;
  if (status) status.textContent = 'Salvando alteração…';

  try {
    const updated = await api('/api/admin/settings', {
      method: 'PUT',
      body: JSON.stringify({ [settingName]: toStored(requestedValue) })
    });
    input.checked = fromStored(updated[settingName]);
    saved = true;
    if (status) status.textContent = 'Alteração salva.';
  } catch (err) {
    input.checked = !requestedValue;
    if (status) status.textContent = 'A alteração não foi salva.';
    showError('Erro ao salvar configuração: ' + err.message);
  } finally {
    input.disabled = false;
  }
  if (saved) await loadWebhookReceipts();
}

// --- Playground Simulation & Raio-X ---
function checkPlaygroundSandboxStatus() {
  const clientInput = $('playground-client');
  const checkLabel = $('playground-sandbox-check');
  if (!clientInput || !checkLabel) return;

  const val = clientInput.value.trim().toLowerCase();
  const isSandbox = $('setting-sandbox-mode')?.checked ?? true;
  if (!isSandbox) {
    checkLabel.textContent = 'Ambiente de Produção Geral (Sem restrição)';
    checkLabel.style.color = '#0284c7';
    return;
  }

  const isAllowed = studioState.whitelist.some(item => item.toLowerCase().includes(val) || val.includes(item.toLowerCase()));
  if (isAllowed && val.length > 3) {
    checkLabel.textContent = '✓ Autorizado pelo Sandbox (Permite simular & disparar nota)';
    checkLabel.style.color = '#10b981';
  } else {
    checkLabel.textContent = '⚠️ Não está na Whitelist (Simulação livre apenas no painel)';
    checkLabel.style.color = '#f59e0b';
  }
}

// ==========================================================================
// MOVIDESK REALISTIC TICKET SIMULATOR (PLAYGROUND MULTI-TURNO)
// ==========================================================================

const TICKET_SCENARIOS = {
  loja_integrada: {
    id: 184920,
    subject: "Produto não sobe para a Loja Integrada",
    client_name: "Lucas Souza",
    client_email: "lucas.teste@alternativa.com.br",
    channel: "Loja Integrada",
    spec_chip: "Hubs & Integrações",
    initial_message: "Olá! Cadastramos produtos novos no ERP Master hoje cedo, mas eles não estão subindo para a Loja Integrada de jeito nenhum. Como proceder para identificar onde está travando?"
  },
  mercado_livre: {
    id: 184925,
    subject: "Venda do Mercado Livre não desceu para faturamento",
    client_name: "Lucas Souza",
    client_email: "lucas.teste@alternativa.com.br",
    channel: "Mercado Livre",
    spec_chip: "Hubs & Integrações",
    initial_message: "Boa tarde! Tivemos duas vendas aprovadas no Mercado Livre há mais de 1 hora e até agora o pedido não apareceu na tela de faturamento do Master. O que pode ser?"
  },
  etiqueta_fr3: {
    id: 184930,
    subject: "Etiqueta Zebra saindo cortada no FastReport .fr3",
    client_name: "Lucas Souza",
    client_email: "lucas.teste@alternativa.com.br",
    channel: "FastReport / Zebra",
    spec_chip: "FastReport FR3 & Etiquetas",
    initial_message: "Bom dia, estamos imprimindo etiquetas de expedição na nossa impressora Zebra, mas a margem do topo está cortando o código de barras e a transportadora recusa. Podem ajudar a ajustar?"
  },
  nfe_rejeicao: {
    id: 184935,
    subject: "Rejeição 539 ao emitir nota fiscal no ERP Master",
    client_name: "Lucas Souza",
    client_email: "lucas.teste@alternativa.com.br",
    channel: "SEFAZ / NF-e",
    spec_chip: "Fiscal & SEFAZ",
    initial_message: "Olá equipe, estamos tentando transmitir uma NF-e de venda urgente e está dando Rejeição 539: Duplicidade de NF-e com diferença na chave de acesso. Como resolver?"
  }
};

let pendingTicketImage = null;
let ticketImageReadId = 0;

function clearTicketImage() {
  pendingTicketImage = null;
  ticketImageReadId++;
  const preview = $('ticket-image-preview');
  if (preview) { preview.hidden = true; preview.removeAttribute('src'); }
  if ($('ticket-image-remove')) $('ticket-image-remove').hidden = true;
  if ($('ticket-image-notice')) $('ticket-image-notice').hidden = true;
}

let currentTicket = {
  ticket_id: 184920,
  subject: "Produto não sobe para a Loja Integrada",
  client_name: "Lucas Souza",
  client_email: "lucas.teste@alternativa.com.br",
  messages: []
};

function formatTimeNow() {
  const d = new Date();
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

function formatTimelineMessage(raw) {
  if (!raw) return '';
  // 1. Isolar imagens Markdown: ![alt](url)
  const images = [];
  let text = String(raw).replace(/!\[([^\]]*)\]\(([^)]+)\)/g, (match, alt, src) => {
    const idx = images.length;
    images.push({ alt: alt.trim(), src: src.trim() });
    return `__MD_IMG_${idx}__`;
  });

  // 2. Escapar caracteres HTML no texto restante para seguranca
  text = esc(text);

  // 3. Formatar negrito: **texto**
  text = text.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');

  // 4. Formatar codigo inline: `codigo`
  text = text.replace(/`([^`]+)`/g, '<code>$1</code>');

  // Format prose before inserting image cards: their HTML must not be split
  // into paragraphs or acquire line breaks inside tags.
  text = text.split(/\n\s*\n/).map(p => /^__MD_IMG_\d+__$/.test(p.trim())
    ? p.trim() : `<p>${p.replace(/\n/g, '<br>')}</p>`).join('');

  // 5. Reinserir imagens formatadas no card de Evidencia Visual (estilo Enterprise)
  images.forEach((img, idx) => {
    const safeAlt = esc(img.alt);
    const safeSrc = esc(img.src);
    const cleanCaption = safeAlt.replace(/^Passo\s*\d+\s*[-–:]\s*/i, '');
    const imgHtml = `
      <div class="evidence-visual-card">
        <div class="evidence-visual-header">
          <div class="evidence-visual-title">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
            <span>Evidência visual — ${cleanCaption}</span>
          </div>
          <a href="${safeSrc}" target="_blank" rel="noopener noreferrer" class="evidence-visual-ext" title="Abrir imagem original em nova aba">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>
          </a>
        </div>
        <div class="evidence-visual-frame">
          <img src="${safeSrc}" alt="${safeAlt}" loading="lazy" class="evidence-visual-img" onclick="window.open(this.src, '_blank')">
        </div>
      </div>
    `;
    text = text.replace(`__MD_IMG_${idx}__`, imgHtml);
  });

  // 6. Converter paragrafos e quebras de linha
  return text;
}

function renderTimeline() {
  const feed = $('ticket-timeline-feed');
  if (!feed) return;

  if (!currentTicket.messages || currentTicket.messages.length === 0) {
    feed.innerHTML = `
      <div class="empty-timeline-prompt">
        <div class="empty-prompt-icon">
          <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>
        </div>
        <h4>Simulador de Chamados Pronto</h4>
        <p>Selecione um cenário de teste em <b>Mais ações</b> acima para simular a resposta e diagnóstico do Cortex.</p>
      </div>
    `;
    return;
  }

  feed.innerHTML = currentTicket.messages.map(m => {
    const isClient = m.role === 'client';
    return `
      <div class="timeline-message ${isClient ? 'client' : 'agent'}">
        <div class="timeline-avatar ${isClient ? 'client-avatar' : 'cortex-avatar'}">
          ${isClient ? 'LS' : `
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M12 2a10 10 0 0 1 10 10c0 5.52-4.48 10-10 10S2 17.52 2 12A10 10 0 0 1 12 2z"/>
              <path d="M12 6v6l4 2"/>
            </svg>
          `}
        </div>
        <div class="message-bubble-wrapper">
          <div class="message-head">
            <div class="message-author-group">
              <span class="message-author">${isClient ? esc(currentTicket.client_name) : 'Cortex'}</span>
              <span class="message-badge ${isClient ? 'client-pill' : 'ai-suggested'}">
                ${isClient ? 'Cliente' : 'Resposta sugerida'}
              </span>
            </div>
            <span class="message-time">Hoje, ${m.timestamp || formatTimeNow()}</span>
          </div>
          <div class="message-body ${isClient ? 'client-body' : 'agent-body'}">
            ${isClient ? esc(m.content).replace(/\n/g, '<br>') : formatTimelineMessage(m.content)}
            ${isClient && /^data:image\/(png|jpeg|webp);base64,[A-Za-z0-9+/=]+$/.test(m.image || '') ? `<img src="${m.image}" alt="Print enviado pelo cliente" style="display:block;max-width:100%;max-height:400px;margin-top:10px">` : ''}
          </div>
        </div>
      </div>
    `;
  }).join('');

  feed.scrollTop = feed.scrollHeight;
}

function updateStepperStage(stage) {
  const s1 = $('step-1');
  const s2 = $('step-2');
  const s3 = $('step-3');
  const l1 = $('step-line-1');
  const l2 = $('step-line-2');

  [s1, s2, s3].forEach(el => el?.classList.remove('active'));
  [l1, l2].forEach(el => el?.classList.remove('active'));

  if (stage === 'evidence_collection') {
    s1?.classList.add('active');
  } else if (stage === 'root_cause_analysis') {
    s1?.classList.add('active');
    l1?.classList.add('active');
    s2?.classList.add('active');
  } else if (stage === 'resolved' || stage === 'awaiting_validation') {
    s1?.classList.add('active');
    l1?.classList.add('active');
    s2?.classList.add('active');
    l2?.classList.add('active');
    s3?.classList.add('active');
  } else {
    s1?.classList.add('active');
  }
}

function renderQuickReplies(replies) {
  const container = $('quick-replies-list');
  if (!container) return;

  if (!replies || replies.length === 0) {
    container.innerHTML = '<span class="muted" style="font-size:11px;">Nenhuma sugestão para esta etapa.</span>';
    return;
  }

  container.innerHTML = replies.map(r => {
    let cleanLabel = r;
    if (cleanLabel.length > 38) {
      cleanLabel = cleanLabel.slice(0, 36) + '…';
    }
    return `
      <button type="button" class="quick-reply-chip" data-full="${esc(r)}" title="${esc(r)}">
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
        <span>${esc(cleanLabel)}</span>
      </button>
    `;
  }).join('');

  container.querySelectorAll('.quick-reply-chip').forEach(btn => {
    btn.addEventListener('click', () => {
      const input = $('ticket-reply-input');
      if (input) {
        input.value = btn.dataset.full || btn.textContent.trim();
        input.focus();
      }
    });
  });
}

function renderCopilotSources(sources) {
  const list = $('copilot-sources-list');
  if (!list) return;

  if (!sources || sources.length === 0) {
    list.innerHTML = `
      <div class="diag-box-row">
        <div class="diag-box-left">
          <div class="diag-box-icon">
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3"/><path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5"/></svg>
          </div>
          <div class="diag-box-text">
            <small class="muted">Nenhuma evidência consultada nesta rodada.</small>
          </div>
        </div>
      </div>
    `;
    return;
  }

  list.innerHTML = sources.map((s, i) => `
    <div class="diag-source-item">
      <div class="diag-source-title">
        <b>${s.source_external_id ? 'Chamado ' + esc(String(s.source_external_id)) : '#' + (i+1)} · ${esc(s.title || 'Referência técnica')}</b>
        <span class="source-tag-tier ${s.tier === 'GOLD' ? 'gold' : ''}">${esc(s.tier || s.source_type)}</span>
      </div>
      <p class="diag-source-desc">${esc((s.excerpt || s.solution || s.problem || '').slice(0, 160))}</p>
      <small>${s.used_in_answer ? 'Fonte utilizada na resposta' : 'Resultado da busca; não utilizado na resposta'}</small>
      ${s.context_conflict ? '<p class="diag-source-desc">Integrador diferente: confirmar aplicabilidade.</p>' : ''}
    </div>
  `).join('');
}

async function sendClientMessage(messageText) {
  if (!messageText.trim() && !pendingTicketImage) return;
  const attachment = pendingTicketImage;
  const content = messageText.trim() || 'Enviei um print do erro, mas o texto não pôde ser extraído.';
  clearTicketImage();

  // 1. Adiciona a mensagem do cliente no estado local
  currentTicket.messages.push({
    role: 'client',
    content,
    image: attachment?.dataUrl,
    meta: attachment ? {screenshot_unreadable: !attachment.readable} : {},
    timestamp: formatTimeNow()
  });
  renderTimeline();

  // Limpa o input
  const input = $('ticket-reply-input');
  if (input) input.value = '';

  // 2. Mostra indicador de digitação da IA
  const typing = $('agent-typing-indicator');
  if (typing) typing.hidden = false;

  const replyBtn = $('ticket-reply-btn');
  if (replyBtn) replyBtn.disabled = true;

  const startTime = Date.now();

  try {
    // 3. Dispara a requisição multi-turno para o backend
    const res = await api('/api/admin/simulate-ticket', {
      method: 'POST',
      body: JSON.stringify({
        ticket_id: currentTicket.ticket_id,
        subject: currentTicket.subject,
        client_name: currentTicket.client_name,
        client_email: currentTicket.client_email,
        messages: currentTicket.messages.map(({image, ...message}) => message)
      })
    });

    const elapsed = ((Date.now() - startTime) / 1000).toFixed(1);
    const auditTime = $('copilot-audit-time');
    if (auditTime) auditTime.textContent = `Resposta gerada em ${elapsed}s`;

    // 4. Adiciona a resposta da IA no feed
    currentTicket.messages.push({
      role: 'agent',
      content: res.reply || 'Procedimento registrado pelo assistente.',
      meta: res.meta || {},
      timestamp: formatTimeNow()
    });

    // 5. Atualiza o status do ticket
    const statusBadge = $('ticket-status-badge');
    const statusDot = $('ticket-status-dot');
    const statusBox = $('diag-status-text');
    if (res.stage === 'resolved') {
      if (statusBadge) statusBadge.textContent = 'Resolvido';
      if (statusDot) statusDot.className = 'pill-dot success';
      if (statusBox) statusBox.textContent = 'Resolvido';
    } else if (res.stage === 'awaiting_validation') {
      if (statusBadge) statusBadge.textContent = 'Aguardando validação';
      if (statusDot) statusDot.className = 'pill-dot warning';
      if (statusBox) statusBox.textContent = 'Aguardando resultado do teste';
    } else if (res.needs_human) {
      if (statusBadge) statusBadge.textContent = res.origin === 'validacao_contexto' ? 'Confirmar integrador' : 'Revisão técnica necessária';
      if (statusDot) statusDot.className = 'pill-dot warning';
      if (statusBox) statusBox.textContent = res.handoff_reason || 'Conferir aplicabilidade da referência';
    } else {
      if (statusBadge) statusBadge.textContent = 'Aguardando cliente';
      if (statusDot) statusDot.className = 'pill-dot warning';
      if (statusBox) statusBox.textContent = 'Em análise';
    }

    // 6. Atualiza o Copilot a partir do problema ativo, não do título original.
    updateStepperStage(res.stage);

    const activeIssue = res.conversation?.issues?.find(i => i.id === res.conversation.active_issue_id);
    if ($('ticket-channel-chip')) {
      $('ticket-channel-chip').textContent = res.route?.modules?.join(' / ') || 'Assunto em identificação';
    }

    if (res.specialist) {
      if ($('copilot-spec-name')) $('copilot-spec-name').textContent = res.specialist.name || 'Especialista';
      if ($('copilot-spec-reason')) $('copilot-spec-reason').textContent = activeIssue ? `Problema ${activeIssue.id}: ${activeIssue.subject}` : 'Roteado pelo assunto atual';
      if ($('ticket-specialist-chip')) $('ticket-specialist-chip').textContent = res.specialist.name || '';
    } else {
      if ($('copilot-spec-name')) $('copilot-spec-name').textContent = 'Triagem';
      if ($('ticket-specialist-chip')) $('ticket-specialist-chip').textContent = 'Triagem';
      if ($('copilot-spec-reason')) $('copilot-spec-reason').textContent = activeIssue?.subject || 'Identificando o assunto atual';
    }

    renderCopilotSources(res.sources || []);
    renderQuickReplies(res.suggested_replies || []);

  } catch (err) {
    showError('Erro na simulação do chamado: ' + err.message);
  } finally {
    if (typing) typing.hidden = true;
    if (replyBtn) replyBtn.disabled = false;
    renderTimeline();
  }
}

async function readTicketImage(file) {
  const notice = $('ticket-image-notice');
  const submit = $('ticket-reply-btn');
  const picker = $('ticket-image-btn');
  if (!file || !notice || submit?.disabled) return;
  notice.hidden = false;
  if (!['image/png', 'image/jpeg', 'image/webp'].includes(file.type) || file.size > 4 * 1024 * 1024) {
    notice.textContent = 'Selecione um print PNG, JPEG ou WebP de até 4 MB.';
    return;
  }
  if (submit) submit.disabled = true;
  if (picker) picker.disabled = true;
  notice.textContent = 'Lendo o texto do print…';
  const ticketAtStart = currentTicket;
  const readId = ++ticketImageReadId;
  try {
    const dataUrl = await new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result);
      reader.onerror = reject;
      reader.readAsDataURL(file);
    });
    if (currentTicket !== ticketAtStart || readId !== ticketImageReadId) return;
    pendingTicketImage = {dataUrl, readable: false};
    const preview = $('ticket-image-preview');
    if (preview) { preview.src = dataUrl; preview.hidden = false; }
    if ($('ticket-image-remove')) $('ticket-image-remove').hidden = false;
    const result = await api('/api/admin/ticket-image', {method: 'POST', body: JSON.stringify({data_url: dataUrl})});
    if (currentTicket !== ticketAtStart || readId !== ticketImageReadId) return;
    const input = $('ticket-reply-input');
    if (result.text && input) {
      const combined = [input.value.trim(), result.text].filter(Boolean).join('\n\n');
      if (combined.length > 8000) throw new Error('Recorte o print para mostrar apenas a mensagem de erro.');
      pendingTicketImage.readable = true;
      input.value = combined;
      input.focus();
    }
    notice.textContent = result.notice;
  } catch (error) {
    if (currentTicket === ticketAtStart && readId === ticketImageReadId) notice.textContent = 'Print anexado. ' + (error.message || 'Não foi possível ler o texto. Copie e cole a mensagem de erro.');
  } finally {
    if (submit) submit.disabled = false;
    if (picker) picker.disabled = false;
    if ($('ticket-image-input')) $('ticket-image-input').value = '';
  }
}

async function loadTicketScenario(key) {
  clearTicketImage();
  // Fecha o dropdown de mais ações se estiver aberto
  const dropdownMenu = $('scenario-dropdown-menu');
  if (dropdownMenu) dropdownMenu.hidden = true;

  // Marca botão ativo na barra de cenários
  document.querySelectorAll('.scenario-btn, .scenario-option-btn').forEach(b => b.classList.remove('active'));
  document.querySelectorAll(`[data-scenario="${key}"]`).forEach(b => b.classList.add('active'));

  if (key === 'new_ticket') {
    const customSubject = prompt('Digite o assunto do novo chamado:', 'Dúvida sobre cadastro de grade no Master');
    if (!customSubject) return;
    const customMsg = prompt('Digite a mensagem inicial do cliente:', 'Como faço para cadastrar um produto com grade de tamanho e cor no ERP Master?');
    if (!customMsg) return;

    currentTicket = {
      ticket_id: Math.floor(100000 + Math.random() * 900000),
      subject: customSubject,
      client_name: 'Lucas Souza',
      client_email: 'lucas.teste@alternativa.com.br',
      messages: []
    };

    $('ticket-number').textContent = `#TKT-${currentTicket.ticket_id}`;
    $('ticket-subject-title').textContent = currentTicket.subject;
    if ($('ticket-channel-chip')) $('ticket-channel-chip').textContent = 'Personalizado';
    if ($('ticket-specialist-chip')) $('ticket-specialist-chip').textContent = 'Analisando...';

    sendClientMessage(customMsg);
    return;
  }

  const sc = TICKET_SCENARIOS[key];
  if (!sc) return;

  currentTicket = {
    ticket_id: sc.id,
    subject: sc.subject,
    client_name: sc.client_name,
    client_email: sc.client_email,
    messages: []
  };

  $('ticket-number').textContent = `#TKT-${sc.id}`;
  $('ticket-subject-title').textContent = sc.subject;
  $('ticket-client-name').textContent = sc.client_name;
  if ($('ticket-channel-chip')) $('ticket-channel-chip').textContent = sc.channel || 'Integração';
  if ($('ticket-specialist-chip')) $('ticket-specialist-chip').textContent = sc.spec_chip;

  // Inicia o chamado enviando a primeira mensagem do cliente
  sendClientMessage(sc.initial_message);
}

// --- Initialize All Events ---
function initAgentStudio() {
  initStudioTabs();

  // Whitelist input events
  $('whitelist-add-btn')?.addEventListener('click', () => {
    addWhitelistItem($('whitelist-input').value);
  });
  $('whitelist-input')?.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      addWhitelistItem(e.currentTarget.value);
    }
  });
  $('pilot-requester-add-btn')?.addEventListener('click', () => {
    addPilotRequesterEmail($('pilot-requester-input').value);
  });
  $('pilot-requester-input')?.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      addPilotRequesterEmail(e.currentTarget.value);
    }
  });
  $('pilot-company-search-btn')?.addEventListener('click', () => {
    clearTimeout(pilotCompanySearchTimer);
    loadPilotCompanies($('pilot-company-search').value.trim());
  });
  $('pilot-company-search')?.addEventListener('input', (e) => {
    clearTimeout(pilotCompanySearchTimer);
    pilotCompanySearchTimer = setTimeout(() => loadPilotCompanies(e.target.value.trim()), 300);
  });
  $('pilot-company-search')?.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      clearTimeout(pilotCompanySearchTimer);
      loadPilotCompanies(e.currentTarget.value.trim());
    }
  });
  $('pilot-company-authorize-all')?.addEventListener('click', async () => {
    const button = $('pilot-company-authorize-all');
    const status = $('pilot-company-bulk-status');
    button.disabled = true;
    status.textContent = 'Conferindo empresas ativas…';
    try {
      const preview = await api('/api/admin/pilot/companies/authorize-all');
      if (!preview.new) { status.textContent = 'Todas as empresas ativas já estão autorizadas.'; return; }
      const confirmed = confirm(`Autorizar todas as ${preview.total} empresas ativas do cadastro atual? Serão adicionadas ${preview.new} empresas.\n\nA ação vale para todo o cadastro, independentemente da busca. Se a automação já estiver ativa, as novas autorizações passam a valer nos eventos elegíveis. As demais políticas e pausas continuam sendo respeitadas. Empresas cadastradas depois exigirão nova autorização.`);
      if (!confirmed) { status.textContent = 'Autorização em lote cancelada.'; return; }
      const result = await api('/api/admin/pilot/companies/authorize-all', {
        method:'POST', body:JSON.stringify({token:preview.token})
      });
      renderStudioSettings(await api('/api/admin/settings'));
      status.textContent = `${result.added} empresa(s) autorizada(s). Total: ${result.selected_count}.`;
    } catch (e) { status.textContent = e.message; }
    finally { button.disabled = false; }
  });
  $('pilot-company-results')?.addEventListener('click', async (e) => {
    const row = e.target.closest('.pilot-company-row');
    if (!row) return;
    row.disabled = true;
    try { await togglePilotCompany(row.dataset.companyId, row.dataset.selected !== 'true'); }
    catch (err) { showError(err.message); row.disabled = false; }
  });

  // Slider visual updates
  $('pace-min-interval')?.addEventListener('input', (e) => {
    $('pace-min-interval-val').textContent = `${e.target.value} s`;
  });
  $('pace-max-minute')?.addEventListener('input', (e) => {
    $('pace-max-minute-val').textContent = `${e.target.value} req/min`;
  });
  $('pace-max-concurrent')?.addEventListener('input', (e) => {
    $('pace-max-concurrent-val').textContent = `${e.target.value}`;
  });

  // Save button
  $('save-studio-btn')?.addEventListener('click', saveAllStudioSettings);
  $('setting-sandbox-mode')?.addEventListener('change', () => {
    // The UI switch means "active", while the stored setting means "paused".
    savePilotToggle('setting-sandbox-mode', 'pilot_paused', value => !value, value => !value);
  });
  $('setting-auto-internal-note')?.addEventListener('change', () => {
    savePilotToggle('setting-auto-internal-note', 'auto_internal_note');
  });

  // Reset defaults button
  $('reset-default-specialists')?.addEventListener('click', async () => {
    if (confirm('Deseja restaurar os 6 especialistas padrão do sistema?')) {
      try {
        const res = await api('/api/admin/specialists/reset', { method: 'POST' });
        renderStudioSettings(res);
      } catch (err) {
        showError(err.message);
      }
    }
  });

  // Add Specialist Button
  $('add-specialist-btn')?.addEventListener('click', () => {
    openSpecialistModal(null, null);
  });

  // Modal events
  $('specialist-modal-close')?.addEventListener('click', closeSpecialistModal);
  $('specialist-modal-cancel')?.addEventListener('click', closeSpecialistModal);
  $('specialist-form')?.addEventListener('submit', (e) => {
    e.preventDefault();
    const newSpec = {
      id: $('spec-edit-id').value,
      name: $('spec-edit-name').value.trim(),
      icon: $('spec-edit-icon').value.trim() || '🧠',
      description: $('spec-edit-desc').value.trim(),
      keywords: $('spec-edit-keywords').value.split(',').map(x => x.trim()).filter(Boolean),
      prompt: $('spec-edit-prompt').value.trim(),
      enabled: true
    };

    if (editingSpecIdx !== null) {
      studioState.specialists[editingSpecIdx] = { ...studioState.specialists[editingSpecIdx], ...newSpec };
    } else {
      studioState.specialists.push(newSpec);
    }
    closeSpecialistModal();
    renderSpecialistsCards();
  });

  // Ticket Simulator Scenarios & Replies
  document.querySelectorAll('.scenario-btn[data-scenario], .scenario-option-btn[data-scenario]').forEach(btn => {
    btn.addEventListener('click', () => {
      loadTicketScenario(btn.dataset.scenario);
    });
  });

  $('btn-new-ticket')?.addEventListener('click', () => {
    loadTicketScenario('new_ticket');
  });

  // Mais ações dropdown toggle
  const moreActionsBtn = $('btn-more-actions');
  const dropdownMenu = $('scenario-dropdown-menu');
  if (moreActionsBtn && dropdownMenu) {
    moreActionsBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      dropdownMenu.hidden = !dropdownMenu.hidden;
    });
    document.addEventListener('click', (e) => {
      if (!dropdownMenu.contains(e.target) && e.target !== moreActionsBtn) {
        dropdownMenu.hidden = true;
      }
    });
  }

  // Voltar para lista button
  $('btn-back-to-list')?.addEventListener('click', () => {
    document.querySelector('.studio-tab-btn[data-tab="tab-sandbox"]')?.click();
  });

  // Editor toolbar formatting helpers
  document.querySelectorAll('.reply-toolbar-left .toolbar-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const input = $('ticket-reply-input');
      if (!input) return;
      const text = btn.textContent.trim();
      if (text === 'B') {
        const start = input.selectionStart, end = input.selectionEnd;
        const val = input.value;
        const selected = val.substring(start, end) || 'texto';
        input.value = val.substring(0, start) + `**${selected}**` + val.substring(end);
        input.focus();
      }
    });
  });

  $('ticket-reply-form')?.addEventListener('submit', (e) => {
    e.preventDefault();
    const input = $('ticket-reply-input');
    if (input && (input.value.trim() || pendingTicketImage)) {
      sendClientMessage(input.value.trim());
    }
  });
  $('ticket-image-remove')?.addEventListener('click', clearTicketImage);
  $('ticket-image-btn')?.addEventListener('click', () => $('ticket-image-input')?.click());
  $('ticket-image-input')?.addEventListener('change', (event) => readTicketImage(event.target.files?.[0]));
  $('ticket-reply-input')?.addEventListener('paste', (event) => {
    const item = Array.from(event.clipboardData?.items || []).find(value => value.type.startsWith('image/'));
    const file = item?.getAsFile() || Array.from(event.clipboardData?.files || []).find(value => value.type.startsWith('image/'));
    if (file) {
      event.preventDefault();
      readTicketImage(file);
    }
  });

  // Renderiza timeline vazia ou existente sem disparar chamada de rede desnecessaria no load
  renderTimeline();

  // Load initial settings
  api('/api/admin/settings').then(renderStudioSettings).catch(err => showError(err.message));
}

// Hook into DOM ready
document.addEventListener('DOMContentLoaded', initAgentStudio);

window.loadAgentCenter = function() {
  if (state?.settings) renderStudioSettings(state.settings);
  else api('/api/admin/settings').then(renderStudioSettings).catch(() => {});
};

