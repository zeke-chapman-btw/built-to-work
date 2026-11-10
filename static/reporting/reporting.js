document.addEventListener('click',function(event){const control=event.target.closest('[data-report-print]');if(control){event.preventDefault();const menu=control.closest('[data-report-export]');if(menu){menu.open=false;menu.querySelector('summary').focus();}window.print();}});

document.querySelectorAll('[data-report-export]').forEach(function(menu) {
  menu.addEventListener('keydown', function(event) {
    if (event.key === 'Escape' && menu.open) {
      menu.open = false;
      menu.querySelector('summary').focus();
      event.preventDefault();
      event.stopPropagation();
    }
  });
  menu.addEventListener('click', function(event) {
    if (event.target.closest('a')) menu.open = false;
  });
  document.addEventListener('click', function(event) {
    if (menu.open && !menu.contains(event.target)) menu.open = false;
  });
});

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

// New overview disclosures use the same keyboard recovery as report filters.
document.querySelectorAll('[data-report-disclosure]').forEach(function(panel) {
  panel.addEventListener('keydown', function(event) {
    if (event.key === 'Escape' && panel.open) {
      panel.open = false;
      panel.querySelector('summary').focus();
      event.preventDefault();
      event.stopPropagation();
    }
  });
});
// Print all original metric/context content, then restore each user's disclosure state.
let reportPrintState = null;
window.addEventListener('beforeprint', function() {
  if (reportPrintState) return;
  reportPrintState = Array.from(document.querySelectorAll('[data-report-print-expand]'), function(panel) {
    const state = { panel: panel, open: panel.open };
    panel.open = true;
    return state;
  });
});
window.addEventListener('afterprint', function() {
  (reportPrintState || []).forEach(function(state) { state.panel.open = state.open; });
  reportPrintState = null;
});
