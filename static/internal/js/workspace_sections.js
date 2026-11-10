/* Enhance native fragment links without intercepting navigation or form actions. */
(() => {
  function reveal(hash) {
    if (!hash || !hash.startsWith('#event-')) return;
    const target = document.getElementById(hash.slice(1));
    if (!target || !target.closest('.event-editor-form')) return;
    if (target instanceof HTMLDetailsElement) target.open = true;
    target.scrollIntoView({block: 'start'});
  }
  document.addEventListener('click', event => {
    const link = event.target.closest('.btw-section-nav a');
    if (link) reveal(link.hash);
  });
  window.addEventListener('hashchange', () => reveal(location.hash));
  reveal(location.hash);
})();
