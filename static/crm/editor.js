/* Full-page CRM forms only. Domain validation and submissions remain server-side. */
(() => {
  const form = document.querySelector('[data-crm-unsaved]');
  if (!form) return;
  const original = new URLSearchParams(new FormData(form)).toString();
  let submitting = false;
  window.addEventListener('beforeunload', event => {
    if (!submitting && new URLSearchParams(new FormData(form)).toString() !== original) {
      event.preventDefault();
      event.returnValue = '';
    }
  });
  form.addEventListener('submit', () => { submitting = true; });
  document.querySelector('[data-crm-errors]')?.focus();
})();
