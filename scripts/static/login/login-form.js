/* Progressive enhancement: the native POST form also works without JavaScript. */
(() => {
  'use strict';
  const form = document.getElementById('login-form');
  const username = document.getElementById('user');
  const password = document.getElementById('pass');
  const remember = form.querySelector('input[type="checkbox"][name="remember"]');
  const rememberedUsernameKey = 'cortex-remembered-username';
  try {
    const savedUsername = localStorage.getItem(rememberedUsernameKey);
    if (savedUsername) username.value = savedUsername;
  } catch (_) { /* Storage may be disabled by the browser. */ }
  const toggle = document.querySelector('.password-toggle');
  const recovery = document.querySelector('.recovery-link');
  const help = document.getElementById('recovery-help');
  const submit = form.querySelector('[type=submit]');
  const status = document.getElementById('login-status');
  toggle.hidden = recovery.hidden = false;
  toggle.addEventListener('click', () => {
    const showing = password.type === 'password';
    password.type = showing ? 'text' : 'password';
    toggle.setAttribute('aria-pressed', String(showing));
    toggle.setAttribute('aria-label', showing ? 'Ocultar senha' : 'Mostrar senha');
  });
  recovery.addEventListener('click', () => {
    help.hidden = !help.hidden;
    recovery.setAttribute('aria-expanded', String(!help.hidden));
  });
  form.addEventListener('submit', () => {
    try {
      if (remember.checked && username.value.trim()) {
        localStorage.setItem(rememberedUsernameKey, username.value.trim());
      } else {
        localStorage.removeItem(rememberedUsernameKey);
      }
    } catch (_) { /* Login continues if storage is unavailable. */ }
    submit.disabled = true;
    submit.querySelector('span').textContent = 'Entrando…';
    form.setAttribute('aria-busy', 'true');
    status.textContent = 'Verificando suas credenciais.';
  });
  remember.addEventListener('change', () => {
    if (!remember.checked) {
      try { localStorage.removeItem(rememberedUsernameKey); } catch (_) {}
    }
  });
  window.addEventListener('pageshow', () => {
    submit.disabled = false;
    submit.querySelector('span').textContent = 'Entrar';
    form.removeAttribute('aria-busy');
    status.textContent = '';
  });
})();
