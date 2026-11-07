document.addEventListener('click',function(event){const control=event.target.closest('[data-report-print]');if(control){event.preventDefault();window.print();}});

// Native disclosure works without JavaScript. Desktop starts expanded; phone starts compact.
document.querySelectorAll('[data-report-filters]').forEach(function(panel) {
  const wide = window.matchMedia('(min-width: 768px)');
  function adapt() { panel.open = wide.matches || panel.hasAttribute('data-filter-errors'); }
  adapt();
  wide.addEventListener('change', adapt);
  panel.addEventListener('keydown', function(event) {
    if (event.key === 'Escape' && panel.open) {
      panel.open = false;
      panel.querySelector('summary').focus();
      event.preventDefault();
      event.stopPropagation();
    }
  });
});
