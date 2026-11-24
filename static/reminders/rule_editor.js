// Reminder editor: only show the timing boxes that matter for the chosen timing.
(function () {
  function sync(box) {
    var picked = box.querySelector('[name="mode"]:checked') || box.querySelector('select[name="mode"], input[type="hidden"][name="mode"]');
    var isNew = picked && picked.value === 'new';
    var rep = box.querySelector('[name="repeat_every"]');
    var repeating = rep && +rep.value > 0;
    box.querySelectorAll('.rm-dated').forEach(function (el) { el.classList.toggle('rm-gone', !!isNew); });
    box.querySelectorAll('.rm-new-only').forEach(function (el) { el.classList.toggle('rm-gone', !isNew); });
    box.querySelectorAll('.rm-timed').forEach(function (el) { el.classList.toggle('rm-gone', !!isNew && !repeating); });
  }
  function all() { document.querySelectorAll('form').forEach(function (f) { if (f.querySelector('[data-rm-when]')) sync(f); }); }
  document.addEventListener('change', function (e) { var f = e.target.closest('form'); if (f && f.querySelector('[data-rm-when]')) sync(f); });
  document.addEventListener('input', function (e) { var f = e.target.closest('form'); if (f && f.querySelector('[data-rm-when]')) sync(f); });
  new MutationObserver(all).observe(document.documentElement, { childList: true, subtree: true });
  document.addEventListener('DOMContentLoaded', all);
})();
