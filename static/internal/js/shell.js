(() => {
  const sidebar = document.getElementById('internal-sidebar');
  const toggle = document.querySelector('[data-menu-toggle]');
  const scrim = document.querySelector('[data-scrim]');
  if (!sidebar || !toggle || !scrim) return;
  const mobile = window.matchMedia('(max-width: 1023px)');
  const background = [...document.querySelectorAll('.skip-link, .main-column > :not(.topbar), .topbar > *')];
  const previousInert = new Map();
  const isOpen = () => mobile.matches && sidebar.classList.contains('open');
  const restoreBackground = () => {
    previousInert.forEach((inert, element) => { element.inert = inert; });
    previousInert.clear();
  };
  const closeMenu = (returnFocus = true) => {
    sidebar.classList.remove('open');
    document.body.classList.remove('nav-open');
    scrim.hidden = true;
    restoreBackground();
    sidebar.inert = mobile.matches;
    if (mobile.matches) sidebar.setAttribute('aria-hidden', 'true');
    else sidebar.removeAttribute('aria-hidden');
    toggle.setAttribute('aria-expanded', 'false');
    toggle.setAttribute('aria-label', 'Open navigation');
    if (returnFocus && mobile.matches) toggle.focus();
  };
  const openMenu = () => {
    if (!mobile.matches) return;
    sidebar.inert = false;
    sidebar.removeAttribute('aria-hidden');
    sidebar.classList.add('open');
    document.body.classList.add('nav-open');
    background.forEach(element => {
      previousInert.set(element, element.inert);
      element.inert = true;
    });
    scrim.hidden = false;
    toggle.setAttribute('aria-expanded', 'true');
    toggle.setAttribute('aria-label', 'Close navigation');
    sidebar.querySelector('a')?.focus();
  };
  const focusable = () => [
    ...sidebar.querySelectorAll('a[href], button:not([disabled]), [tabindex="0"]'), toggle, scrim,
  ].filter(element => !element.inert && element.getClientRects().length);
  toggle.addEventListener('click', () => isOpen() ? closeMenu() : openMenu());
  scrim.addEventListener('click', () => closeMenu());
  sidebar.querySelectorAll('a').forEach(link => link.addEventListener('click', () => {
    if (isOpen()) closeMenu(false);
  }));
  document.addEventListener('keydown', event => {
    if (!isOpen()) return;
    if (event.key === 'Escape') {
      event.preventDefault();
      closeMenu();
    } else if (event.key === 'Tab') {
      const items = focusable();
      const first = items[0], last = items[items.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault(); last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault(); first.focus();
      }
    }
  });
  document.addEventListener('focusin', event => {
    if (isOpen() && !sidebar.contains(event.target) && event.target !== toggle && event.target !== scrim) {
      sidebar.querySelector('a')?.focus();
    }
  });
  mobile.addEventListener('change', () => {
    // CSS may already have hidden the focused backdrop; open state survives.
    const wasOpen = sidebar.classList.contains('open');
    const focused = document.activeElement;
    closeMenu(false);
    if (mobile.matches && sidebar.contains(focused)) toggle.focus();
    else if (!mobile.matches && (wasOpen || focused === toggle || focused === scrim)) sidebar.querySelector('a')?.focus();
  });
  closeMenu(false);
})();

// Scrolling tab rows (phones): bring the current tab into view instead of leaving it cut off.
document.addEventListener('DOMContentLoaded', function () {
  document.querySelectorAll('nav').forEach(function (nav) {
    if (nav.scrollWidth <= nav.clientWidth + 1) return;
    var current = nav.querySelector('[aria-current="page"], .is-active');
    if (!current) return;
    var left = current.offsetLeft - nav.offsetLeft;
    var target = left - (nav.clientWidth - current.offsetWidth) / 2;
    nav.scrollLeft = Math.max(0, target);
  });
});

// Print buttons: <button data-print> prints the current page with the print stylesheet.
document.addEventListener('click', function (event) {
  var button = event.target.closest && event.target.closest('[data-print]');
  if (button) { event.preventDefault(); window.print(); }
});
