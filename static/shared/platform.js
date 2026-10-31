/* Presentation only: preserve submitted values and existing server validation. */
(() => {
  'use strict';
  document.body.classList.add('btw-platform-enhanced');
  document.querySelectorAll('[data-section-chooser]').forEach(select => {
    select.addEventListener('change', () => {
      const selected = select.selectedOptions[0];
      if (selected && selected.value) window.location.assign(selected.value);
    });
  });
  const reveal = target => {
    let parent = target && target.parentElement;
    while (parent) { if (parent.tagName === 'DETAILS') parent.open = true; parent = parent.parentElement; }
  };
  document.querySelectorAll('[data-structured-form]').forEach(form => {
    const summary = form.querySelector('[data-error-summary]');
    if (summary) {
      form.querySelectorAll('.errorlist').forEach(error => reveal(error));
      summary.focus();
    }
    form.addEventListener('invalid', event => { reveal(event.target); }, true);
    form.addEventListener('click', event => {
      const link = event.target.closest('a[href^="#"]');
      if (!link) return;
      const target = document.getElementById(link.hash.slice(1));
      if (target) { reveal(target); if (target.tagName === 'DETAILS') target.open = true; target.focus(); }
    });
  });
  document.querySelectorAll('[data-section-links] a[href^="#"]').forEach(link => {
    link.addEventListener('click', () => { const group = document.getElementById(link.hash.slice(1)); if (group) group.open = true; });
  });
})();
