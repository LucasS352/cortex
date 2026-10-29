/* Progressive enhancement: the native POST form also works without JavaScript. */
(() => {
  'use strict';
  const form = document.getElementById('login-form');
  const password = document.getElementById('pass');
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
    submit.disabled = true;
    submit.querySelector('span').textContent = 'Entrando…';
    form.setAttribute('aria-busy', 'true');
    status.textContent = 'Verificando suas credenciais.';
  });
  window.addEventListener('pageshow', () => {
    submit.disabled = false;
    submit.querySelector('span').textContent = 'Entrar';
    form.removeAttribute('aria-busy');
    status.textContent = '';
  });
})();
