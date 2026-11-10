// Copies a participant ID (or other short text) to the clipboard.
(function () {
  document.addEventListener('click', function (event) {
    var button = event.target.closest('[data-copy-text]');
    if (!button || !navigator.clipboard) return;
    navigator.clipboard.writeText(button.getAttribute('data-copy-text')).then(function () {
      var original = button.getAttribute('title') || '';
      button.setAttribute('title', 'Copied');
      button.classList.add('is-copied');
      setTimeout(function () {
        button.setAttribute('title', original);
        button.classList.remove('is-copied');
      }, 1500);
    });
  });
})();
