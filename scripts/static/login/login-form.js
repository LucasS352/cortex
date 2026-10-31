/* Progressive enhancement: the native POST form also works without JavaScript. */
(() => {
  'use strict';
  const form = document.getElementById('login-form');
  const username = document.getElementById('user');
  const password = document.getElementById('pass');
  const remember = form.querySelector('input[type="checkbox"][name="remember"]');
  const panel = document.querySelector('.login-panel');
  const scene = document.querySelector('.neural-scene');
  const error = document.getElementById('login-error');
  let pending = false, controller, timeout, slowNotice, transitionTimer, navigating = false;
  const rememberedUsernameKey = 'cortex-remembered-username';
  try {
    const savedUsername = localStorage.getItem(rememberedUsernameKey);
    if (savedUsername && !username.value) username.value = savedUsername;
  } catch (_) { /* Storage may be disabled by the browser. */ }
  const toggle = document.querySelector('.password-toggle');
  const recovery = document.querySelector('.recovery-link');
  const help = document.getElementById('recovery-help');
  const submit = form.querySelector('[type=submit]');
  const status = document.getElementById('login-status');
  function preloadDestination(assets) {
    if (!Array.isArray(assets)) return;
    for (const asset of assets.slice(0, 32)) {
      if (!asset || typeof asset.href !== 'string' || !['style', 'script', 'image'].includes(asset.as)) continue;
      const url = new URL(asset.href, location.origin);
      if (url.origin !== location.origin || !url.pathname.startsWith('/tech-assets/')) continue;
      if ([...document.querySelectorAll('link[rel="preload"]')].some(link => link.href === url.href)) continue;
      const link = document.createElement('link');
      link.rel = 'preload'; link.as = asset.as; link.href = url.href;
      // Download only; scripts execute once, in the destination document.
      document.head.appendChild(link);
    }
  }
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
  function clearTimers() {
    clearTimeout(timeout);
    clearTimeout(slowNotice);
    clearTimeout(transitionTimer);
  }
  function reset(message = '') {
    clearTimers();
    controller?.abort();
    pending = false;
    navigating = false;
    document.body.classList.remove('login-pending', 'login-slow', 'login-entering', 'login-zooming');
    panel.inert = false;
    submit.disabled = false;
    submit.querySelector('span').textContent = 'Entrar';
    form.removeAttribute('aria-busy');
    status.textContent = '';
    error.textContent = message;
    error.hidden = !message;
    if (message) error.focus();
  }
  function saveUsername() {
    try {
      if (remember.checked && username.value.trim()) {
        localStorage.setItem(rememberedUsernameKey, username.value.trim());
      } else {
        localStorage.removeItem(rememberedUsernameKey);
      }
    } catch (_) { /* Login continues if storage is unavailable. */ }
  }
  form.addEventListener('submit', async event => {
    // Keep the native POST fallback for older browsers / disabled JavaScript.
    if (!window.fetch || !window.AbortController) return;
    event.preventDefault();
    if (pending) return;
    const body = new URLSearchParams(new FormData(form));
    pending = true;
    controller = new AbortController();
    const attempt = controller;
    error.hidden = true;
    submit.disabled = true;
    panel.inert = true;
    form.setAttribute('aria-busy', 'true');
    document.body.classList.add('login-pending');
    status.textContent = 'Entrando no Cortex…';
    slowNotice = setTimeout(() => {
      document.body.classList.add('login-slow');
      status.textContent = 'A conexão está demorando um pouco. Aguarde…';
    }, 4000);
    timeout = setTimeout(() => attempt.abort(), 15000);
    try {
      const response = await fetch(form.action, {
        method: 'POST', body, credentials: 'same-origin', cache: 'no-store',
        headers: { Accept: 'application/json' }, signal: attempt.signal,
      });
      const result = await response.json();
      if (!pending || attempt.signal.aborted) return;
      if (!response.ok) throw new Error(result.error || 'Não foi possível entrar. Tente novamente.');
      // Only destinations issued by our login gate are accepted.
      if (!['/admin', '/atendimento', '/em-breve', '/senha'].includes(result.redirect)) {
        throw new Error('Não foi possível abrir o painel. Tente novamente.');
      }
      clearTimeout(timeout);
      clearTimeout(slowNotice);
      document.body.classList.remove('login-slow');
      saveUsername();
      // Reuse the 2-second camera glide to download the destination's fixed UI.
      // A failed preload must never prevent a successful login from navigating.
      try { preloadDestination(result.preload); } catch (_) {}
      const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)').matches;
      const navigate = () => {
        if (!pending || navigating || attempt.signal.aborted) return;
        navigating = true;
        status.textContent = 'Abrindo seu painel…';
        try { sessionStorage.setItem('cortex-from-login', '1'); } catch (_) {}
        // A stalled document navigation must not leave a permanently hidden form.
        timeout = setTimeout(() => {
          if (navigating) window.stop();
          reset('O painel demorou para abrir. Tente entrar novamente.');
        }, 20000);
        window.location.replace(result.redirect);
      };
      if (reducedMotion) {
        navigate();
        return;
      }
      status.textContent = 'Entrando no universo Cortex…';
      document.body.classList.add('login-zooming');
      void scene?.offsetWidth;
      document.body.classList.add('login-entering');
      // Navigate at 2000ms while the 4.2s CSS camera glide is still smoothly advancing,
      // so the camera never stops before the destination document paints.
      transitionTimer = setTimeout(navigate, 2000);
    } catch (failure) {
      if (!pending || controller !== attempt) return;
      const message = failure.name === 'AbortError'
        ? 'A conexão demorou para responder. Tente entrar novamente.'
        : failure instanceof TypeError || failure instanceof SyntaxError
          ? 'Não foi possível conectar ao Cortex. Verifique sua conexão e tente novamente.'
          : failure.message;
      reset(message);
    }
  });
  remember.addEventListener('change', () => {
    if (!remember.checked) {
      try { localStorage.removeItem(rememberedUsernameKey); } catch (_) {}
    }
  });
  window.addEventListener('pagehide', () => {
    clearTimers();
    controller?.abort();
    pending = false;
    document.body.classList.remove('login-zooming');
  });
  // Initial pageshow may arrive after a fast submit while images are loading.
  // Only a back/forward cache restoration should reset an active attempt.
  window.addEventListener('pageshow', event => { if (event.persisted) reset(); });
})();
