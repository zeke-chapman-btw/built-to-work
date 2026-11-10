(() => {
  'use strict';
  document.querySelectorAll('[data-workforce-filters]').forEach(panel => {
    const wide = window.matchMedia('(min-width: 768px)');
    function adapt() { panel.open = wide.matches || panel.hasAttribute('data-filter-errors'); }
    adapt();
    wide.addEventListener('change', adapt);
    panel.addEventListener('keydown', event => {
      if (event.key === 'Escape' && panel.open) {
        panel.open = false; panel.querySelector('summary').focus();
        event.preventDefault(); event.stopPropagation();
      }
    });
  });
})();
