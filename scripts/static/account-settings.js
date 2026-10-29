(() => {
  'use strict';
  const el = id => document.getElementById(id);
  const dialog = el('account-dialog'), form = el('account-form');
  let users = [], roles = [], currentId = '', editing = null, busy = false, sequence = 0;
  const safe = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  async function request(path, options = {}) {
    const response = await fetch(path, {headers:{'Content-Type':'application/json'}, ...options});
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      if (response.status === 401) throw new Error('Sua sessão expirou. Entre novamente.');
      const detail = data.detail || data.error;
      throw new Error(typeof detail === 'string' ? detail : 'Confira os campos informados e tente novamente.');
    }
    return data;
  }
  function themeStatus() { el('account-theme-status').textContent = `Modo ${document.documentElement.dataset.theme === 'light' ? 'claro' : 'escuro'} selecionado.`; }
  document.querySelectorAll('[data-theme-choice]').forEach(button => button.addEventListener('click', themeStatus));
  themeStatus();
  function selectTab(name, focus = false) {
    document.querySelectorAll('[data-settings-tab]').forEach(button => {
      const selected = button.dataset.settingsTab === name;
      button.setAttribute('aria-selected', String(selected)); button.tabIndex = selected ? 0 : -1;
      el(`settings-${button.dataset.settingsTab}`).hidden = !selected;
      if (selected && focus) button.focus();
    });
  }
  document.querySelectorAll('[data-settings-tab]').forEach(button => {
    button.addEventListener('click', () => selectTab(button.dataset.settingsTab));
    button.addEventListener('keydown', event => {
      if (['ArrowLeft','ArrowRight','Home','End'].includes(event.key)) {
        event.preventDefault(); selectTab(event.key === 'Home' ? 'appearance' : event.key === 'End' ? 'users' : button.dataset.settingsTab === 'users' ? 'appearance' : 'users', true);
      }
    });
  });
  el('settings-open-users').addEventListener('click', () => selectTab('users', true));
  function render() {
    const query = el('account-search').value.trim().toLocaleLowerCase('pt-BR');
    const filter = el('account-filter').value;
    const visible = users.filter(user => [user.name,user.username,user.email].join(' ').toLocaleLowerCase('pt-BR').includes(query) && (filter === 'all' || user.is_active === (filter === 'active')));
    el('account-list-status').textContent = `${users.length} conta(s) · ${users.filter(u => u.is_active).length} ativa(s) · ${visible.length} exibida(s)`;
    el('account-list').innerHTML = visible.map(user => `<tr><td><strong>${safe(user.name)} ${user.id === currentId ? '<span class="account-badge">Você</span>' : ''}</strong><small>${safe(user.username)}</small><small>${safe(user.email || 'Sem e-mail')}</small></td><td>${safe(roles.find(role => role.value === user.role)?.label || user.role)}</td><td><span class="account-badge ${user.is_active ? 'account-badge-active' : ''}">${user.is_active ? 'Ativo' : 'Inativo'}</span></td><td>${user.must_change_password ? 'Troca de senha pendente' : 'Senha definida'}</td><td><button class="button button-light" type="button" data-edit-account="${safe(user.id)}" aria-label="Editar ${safe(user.name)}">Editar</button></td></tr>`).join('') || '<tr><td colspan="5">Nenhum usuário encontrado.</td></tr>';
  }
  window.loadAccountSettings = async () => {
    themeStatus();
    const currentTheme = document.documentElement.dataset.theme || 'dark';
    document.querySelectorAll('[data-theme-choice]').forEach(button => {
      button.setAttribute('aria-pressed', String(button.dataset.themeChoice === currentTheme));
    });
    const run = ++sequence;
    el('account-list-status').textContent = 'Carregando contas…'; el('account-new').disabled = true;
    try {
      const data = await request('/api/admin/accounts');
      if (run !== sequence) return;
      users = data.users; roles = data.roles; currentId = data.current_user_id;
      el('account-role').innerHTML = roles.map(role => `<option value="${safe(role.value)}">${safe(role.label)}</option>`).join('');
      el('account-role-guide').innerHTML = '<dl>' + roles.map(role => `<dt>${safe(role.label)}</dt><dd>${safe(role.description)}</dd>`).join('') + '</dl>';
      const current = users.find(user => user.id === currentId);
      el('settings-current-user').textContent = current ? `${current.name} · ${current.username} · Master` : 'Conta master conectada';
      render(); el('account-new').disabled = false;
    } catch (error) { if (run === sequence) { el('account-list-status').textContent = error.message; el('account-list').replaceChildren(); } }
  };
  function roleHelp() { el('account-role-description').textContent = roles.find(role => role.value === el('account-role').value)?.description || ''; }
  function openAccount(user = null) {
    editing = user; form.reset(); el('account-form-message').hidden = true;
    const self = user?.id === currentId;
    el('account-dialog-title').textContent = user ? 'Editar usuário' : 'Criar usuário';
    el('account-save').textContent = user ? 'Salvar alterações' : 'Criar usuário';
    el('account-name').value = user?.name || '';
    el('account-username').value = user?.username || ''; el('account-username').disabled = !!user;
    el('account-email').value = user?.email || ''; el('account-role').value = user?.role || 'support_analyst';
    el('account-movidesk').value = user?.movidesk_person_id || '';
    el('account-active').checked = user?.is_active ?? true; el('account-change').checked = user?.must_change_password ?? true;
    ['account-role','account-active','account-change'].forEach(id => el(id).disabled = self);
    el('account-password').required = !user; el('account-password').disabled = self;
    el('account-password-label').hidden = self; el('account-self-help').hidden = !self;
    el('account-password-caption').textContent = user ? 'Nova senha temporária (opcional)' : 'Senha temporária';
    el('account-password-help').textContent = user ? 'Deixe em branco para manter a senha. Ao redefinir, as sessões são encerradas e a troca será obrigatória.' : 'Use pelo menos 8 caracteres. Entregue a senha diretamente à pessoa.';
    roleHelp(); dialog.showModal(); el('account-name').focus();
  }
  const close = () => { if (!busy) { dialog.close(); form.reset(); editing = null; } };
  ['account-close','account-cancel'].forEach(id => el(id).addEventListener('click', close));
  dialog.addEventListener('cancel', event => { if (busy) event.preventDefault(); });
  dialog.addEventListener('close', () => { form.reset(); editing = null; });
  el('account-new').addEventListener('click', () => openAccount());
  el('account-list').addEventListener('click', event => { const button = event.target.closest('[data-edit-account]'); if (button) openAccount(users.find(user => user.id === button.dataset.editAccount)); });
  el('account-reload').addEventListener('click', window.loadAccountSettings);
  el('account-search').addEventListener('input', render); el('account-filter').addEventListener('change', render);
  el('account-role').addEventListener('change', roleHelp);
  el('account-password').addEventListener('input', () => { if (editing && editing.id !== currentId) { el('account-change').checked = !!el('account-password').value || editing.must_change_password; el('account-change').disabled = !!el('account-password').value; } });
  form.addEventListener('submit', async event => {
    event.preventDefault(); if (busy || !form.reportValidity()) return;
    const payload = {name:el('account-name').value.trim(), email:el('account-email').value.trim() || null, role:el('account-role').value,
      movidesk_person_id:el('account-movidesk').value.trim() || null, is_active:el('account-active').checked, must_change_password:el('account-change').checked};
    if (!editing) payload.username = el('account-username').value;
    if (!editing || el('account-password').value) payload.password = el('account-password').value;
    busy = true; el('account-save').disabled = true; el('account-form-message').hidden = true;
    try {
      const result = await request('/api/admin/accounts' + (editing ? '/' + editing.id : ''), {method:editing ? 'PUT' : 'POST', body:JSON.stringify(payload)});
      busy = false; close(); await window.loadAccountSettings();
      el('account-list-status').textContent = `${result.name}: conta salva com sucesso.`;
    } catch (error) { el('account-form-message').textContent = error.message; el('account-form-message').hidden = false; }
    finally { delete payload.password; busy = false; el('account-save').disabled = false; }
  });
  if (new URLSearchParams(location.search).get('tab') === 'users') selectTab('users');
  if (el('view-settings').classList.contains('active')) window.loadAccountSettings();

  // --------------------------------------------------------------------------
  // PINs DE TREINAMENTO E CURADORIA
  // --------------------------------------------------------------------------
  let technicianPins = [];
  const pinModal = el('pin-modal');
  const pinForm = el('pin-modal-form');

  async function loadPins() {
    const counter = el('pins-counter');
    if (counter) counter.textContent = 'Carregando PINs...';
    try {
      technicianPins = await request('/api/admin/pins');
      renderPins();
    } catch (err) {
      if (counter) counter.textContent = 'Erro ao carregar PINs: ' + err.message;
    }
  }

  function renderPins() {
    const query = (el('pins-filter-query')?.value || '').trim().toLowerCase();
    const tbody = el('pins-table-body');
    const counter = el('pins-counter');
    if (!tbody) return;

    const visible = technicianPins.filter(p =>
      p.name.toLowerCase().includes(query) || p.pin.includes(query)
    );

    if (counter) {
      counter.textContent = `${technicianPins.length} técnico(s) · ${technicianPins.filter(p => p.is_active).length} ativo(s) · ${visible.length} exibido(s)`;
    }

    if (!visible.length) {
      tbody.innerHTML = '<tr><td colspan="5" style="text-align:center; padding:24px; color:var(--cx-text-muted);">Nenhum PIN encontrado.</td></tr>';
      return;
    }

    tbody.innerHTML = visible.map(p => `
      <tr>
        <td><strong>${safe(p.name)}</strong></td>
        <td><code style="font-size:14px; letter-spacing:2px; font-weight:700; background:var(--cx-surface-soft); padding:4px 8px; border-radius:4px; border:1px solid var(--cx-border);">${safe(p.pin)}</code></td>
        <td><span class="account-badge ${p.is_active ? 'account-badge-active' : ''}">${p.is_active ? 'Ativo' : 'Inativo'}</span></td>
        <td><small style="color:var(--cx-text-muted);">${safe(p.created_by || 'master')}</small></td>
        <td>
          <div style="display:flex; gap:6px;">
            <button class="button button-light" type="button" data-edit-pin="${safe(p.id)}" style="padding:4px 10px; font-size:12px;">Editar</button>
            <button class="button button-light" type="button" data-delete-pin="${safe(p.id)}" style="padding:4px 10px; font-size:12px; color:var(--cx-danger);">Excluir</button>
          </div>
        </td>
      </tr>
    `).join('');

    tbody.querySelectorAll('[data-edit-pin]').forEach(btn => {
      btn.addEventListener('click', () => {
        const id = btn.dataset.editPin;
        const found = technicianPins.find(p => p.id === id);
        if (found) openPinModal(found);
      });
    });

    tbody.querySelectorAll('[data-delete-pin]').forEach(btn => {
      btn.addEventListener('click', async () => {
        const id = btn.dataset.deletePin;
        const found = technicianPins.find(p => p.id === id);
        if (!found) return;
        if (!confirm(`Deseja realmente excluir o PIN de ${found.name}?`)) return;
        try {
          await request(`/api/admin/pins/${id}`, { method: 'DELETE' });
          await loadPins();
        } catch (err) {
          alert('Erro ao excluir PIN: ' + err.message);
        }
      });
    });
  }

  function openPinModal(pinObj = null) {
    if (!pinModal) return;
    const errBox = el('pin-modal-error');
    if (errBox) errBox.hidden = true;

    el('pin-modal-title').textContent = pinObj ? 'Editar PIN de Técnico' : 'Novo PIN de Treinamento';
    el('pin-modal-id').value = pinObj ? pinObj.id : '';
    el('pin-modal-name').value = pinObj ? pinObj.name : '';
    el('pin-modal-code').value = pinObj ? pinObj.pin : '';
    el('pin-modal-active').checked = pinObj ? !!pinObj.is_active : true;

    if (typeof pinModal.showModal === 'function') {
      pinModal.showModal();
    } else {
      pinModal.setAttribute('open', '');
    }
  }

  function closePinModal() {
    if (!pinModal) return;
    if (typeof pinModal.close === 'function') {
      pinModal.close();
    } else {
      pinModal.removeAttribute('open');
    }
  }

  el('btn-create-pin')?.addEventListener('click', () => openPinModal(null));
  el('pins-refresh-btn')?.addEventListener('click', () => loadPins());
  el('pins-filter-query')?.addEventListener('input', () => renderPins());
  el('pin-modal-close')?.addEventListener('click', () => closePinModal());
  el('pin-modal-cancel')?.addEventListener('click', () => closePinModal());

  pinForm?.addEventListener('submit', async (e) => {
    e.preventDefault();
    const id = el('pin-modal-id').value;
    const name = el('pin-modal-name').value.trim();
    const pin = el('pin-modal-code').value.trim();
    const isActive = el('pin-modal-active').checked;
    const errBox = el('pin-modal-error');

    if (!/^\d{4}$/.test(pin)) {
      if (errBox) { errBox.textContent = 'O PIN deve conter exatamente 4 dígitos numéricos.'; errBox.hidden = false; }
      return;
    }

    try {
      if (id) {
        await request(`/api/admin/pins/${id}`, {
          method: 'PUT',
          body: JSON.stringify({ name, pin, is_active: isActive })
        });
      } else {
        await request('/api/admin/pins', {
          method: 'POST',
          body: JSON.stringify({ name, pin })
        });
      }
      closePinModal();
      await loadPins();
    } catch (err) {
      if (errBox) {
        errBox.textContent = err.message;
        errBox.hidden = false;
      }
    }
  });

  // Call loadPins when switching to pins tab
  const origSelectTab = selectTab;
  selectTab = function(name, focus) {
    origSelectTab(name, focus);
    if (name === 'pins') {
      loadPins();
    }
  };

})();
