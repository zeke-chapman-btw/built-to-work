/* Native disclosure provides the no-script bell. Escape and outside click are enhancements. */
(() => {
  'use strict';
  const bell=document.querySelector('[data-notification-bell]');
  if (!bell) return;
  const summary=bell.querySelector('summary');
  document.addEventListener('keydown',event => {
    if (event.key==='Escape' && bell.open && !document.querySelector('dialog[open]')) {
      event.preventDefault(); bell.open=false; summary.focus();
    }
  });
  document.addEventListener('click',event => {
    if (bell.open && !bell.contains(event.target)) bell.open=false;
  });
})();
