/* Sign-up form question editor: answer list, and showing only the settings that apply. Works in the pop-up too. */
(function () {
  var seq = 0;
  function sync(root) {
    var typeField = root.closest('form').querySelector('[name=type]');
    var kind = typeField ? typeField.value : root.getAttribute('data-kind');
    root.querySelectorAll('[data-for-types]').forEach(function (el) {
      el.hidden = kind && el.getAttribute('data-for-types').split(' ').indexOf(kind) < 0;
    });
    var mode = root.querySelector('[name=show_when]:checked');
    var box = root.querySelector('[data-show-answer]');
    if (box) box.hidden = !mode || mode.value !== 'answer';
    var field = root.querySelector('[name=show_field]');
    var shown = 0;
    root.querySelectorAll('[data-show-values] .qe-chip').forEach(function (chip) {
      var input = chip.querySelector('input');
      var mine = field && field.value && input.value.indexOf(field.value + '::') === 0;
      chip.hidden = !mine;
      if (!mine) input.checked = false; else shown++;
    });
    var empty = root.querySelector('[data-show-empty]');
    if (empty) empty.hidden = shown > 0;
  }
  function init(root) {
    if (root.dataset.qeReady) return;
    root.dataset.qeReady = '1';
    sync(root);
  }
  function scan() { document.querySelectorAll('[data-question-editor]').forEach(init); }
  document.addEventListener('click', function (e) {
    var root = e.target.closest('[data-question-editor]');
    if (!root) return;
    var row = e.target.closest('.qe-opt');
    if (e.target.closest('[data-opt-add]')) {
      var tpl = root.querySelector('[data-opt-template]');
      var item = tpl.content.firstElementChild.cloneNode(true);
      var token = 'new' + (++seq);
      item.querySelector('[name=opt_token]').value = token;
      item.querySelector('[name=opt_alone]').value = token;
      root.querySelector('[data-opt-list]').appendChild(item);
      sync(root);
      item.querySelector('[name=opt_label]').focus();
    } else if (row && e.target.closest('[data-opt-remove]')) {
      row.remove();
    } else if (row && e.target.closest('[data-opt-up]') && row.previousElementSibling) {
      row.parentNode.insertBefore(row, row.previousElementSibling);
    } else if (row && e.target.closest('[data-opt-down]') && row.nextElementSibling) {
      row.parentNode.insertBefore(row.nextElementSibling, row);
    }
  });
  document.addEventListener('change', function (e) {
    var root = e.target.closest('form') && e.target.closest('form').querySelector('[data-question-editor]');
    if (root && /^(type|show_when|show_field)$/.test(e.target.name)) sync(root);
  });
  new MutationObserver(scan).observe(document.documentElement, {childList: true, subtree: true});
  document.addEventListener('DOMContentLoaded', scan);
})();
