(() => {
  const sidebar = document.getElementById('internal-sidebar');
  const toggle = document.querySelector('[data-menu-toggle]');
  const scrim = document.querySelector('[data-scrim]');
  if (!sidebar || !toggle || !scrim) return;
  const closeMenu = (returnFocus = true) => {
    sidebar.classList.remove('open');
    document.body.classList.remove('nav-open');
    scrim.hidden = true;
    toggle.setAttribute('aria-expanded', 'false');
    toggle.setAttribute('aria-label', 'Open navigation');
    if (returnFocus) toggle.focus();
  };
  toggle.addEventListener('click', () => {
    const opening = !sidebar.classList.contains('open');
    sidebar.classList.toggle('open', opening);
    document.body.classList.toggle('nav-open', opening);
    scrim.hidden = !opening;
    toggle.setAttribute('aria-expanded', String(opening));
    toggle.setAttribute('aria-label', opening ? 'Close navigation' : 'Open navigation');
    if (opening) sidebar.querySelector('a')?.focus();
  });
  scrim.addEventListener('click', () => closeMenu());
  sidebar.querySelectorAll('.nav-item').forEach(link => link.addEventListener('click', () => closeMenu(false)));
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && sidebar.classList.contains('open')) closeMenu();
  });
})();
