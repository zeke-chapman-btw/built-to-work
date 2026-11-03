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

/* Coordinated UX refinement: enhanced navigation and loss protection are opt-in. */
(() => {
  'use strict';
  const show = target => {
    if (!target) return;
    let node = target;
    while (node) { if (node.tagName === 'DETAILS') node.open = true; node = node.parentElement; }
    target.scrollIntoView({block:'start'});
    target.focus({preventScroll:true});
  };
  document.querySelectorAll('[data-sectioned-form]').forEach(form => {
    const chooser = form.querySelector('[data-form-section-chooser]');
    const groups = [...form.querySelectorAll('.customer-form-group')];
    const current = group => {
      if (chooser) chooser.value = '#' + group.id;
      form.querySelectorAll('[data-section-links] a').forEach(link => {
        if (link.hash === '#' + group.id) link.setAttribute('aria-current','step');
        else link.removeAttribute('aria-current');
      });
    };
    if (chooser) chooser.addEventListener('change', () => {
      if (!/^#hiring-group-\d+$/.test(chooser.value)) return;
      const group = groups.find(item => '#' + item.id === chooser.value);
      if (group) { group.open = true; current(group); show(group.querySelector('summary')); }
    });
    groups.forEach(group => {
      group.addEventListener('focusin', () => current(group));
      group.addEventListener('toggle', () => { if (group.open) current(group); });
    });
    form.addEventListener('click', event => {
      const link = event.target.closest('a[href^="#"]');
      if (!link || !form.contains(link)) return;
      const target = document.getElementById(link.hash.slice(1));
      if (!target || !form.contains(target)) return;
      event.preventDefault();
      show(target.tagName === 'DETAILS' ? target.querySelector('summary') : target);
    });
    const firstError = groups.find(group => group.hasAttribute('data-has-errors'));
    if (firstError || groups[0]) current(firstError || groups[0]);
  });
  document.querySelectorAll('form[data-unsaved-guard]').forEach(form => {
    const snapshot = () => JSON.stringify([...new FormData(form)].map(([key,value]) =>
      [key, typeof value === 'string' ? value : [value.name,value.size,value.lastModified]]));
    let baseline = snapshot(), submitting = false;
    const dirty = () => !submitting && snapshot() !== baseline;
    form.addEventListener('submit', () => { submitting = true; });
    window.addEventListener('pageshow', () => { submitting = false; });
    window.addEventListener('beforeunload', event => {
      if (dirty()) { event.preventDefault(); event.returnValue = ''; }
    });
    form.querySelectorAll('[data-leave-editor]').forEach(link => link.addEventListener('click', event => {
      if (!dirty()) return;
      if (!window.confirm('Leave without saving your changes?')) event.preventDefault();
      else submitting = true;
    }));
  });
})();
