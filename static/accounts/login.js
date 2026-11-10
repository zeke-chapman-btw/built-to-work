/* Full-page authentication forms only; no credentials are read or retained. */
(() => {
  const forms = [...document.querySelectorAll('form[data-unified-login]')];
  for (const form of forms) {
    const button = form.querySelector('button[type="submit"]');
    if (!button) continue;
    const original = button.textContent;
    let submitting = false;
    form.addEventListener('submit', event => {
      if (submitting) { event.preventDefault(); return; }
      if (!form.checkValidity()) return;
      submitting = true;
      button.disabled = true;
      button.textContent = form.dataset?.submittingLabel || 'Signing in…';
      form.setAttribute('aria-busy', 'true');
    });
    window.addEventListener('pageshow', () => {
      submitting = false;
      button.disabled = false;
      button.textContent = original;
      form.removeAttribute('aria-busy');
    });
  }
  document.querySelector('[data-login-errors]')?.focus();
})();
