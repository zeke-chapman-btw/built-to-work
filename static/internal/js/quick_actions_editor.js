(() => {
  const editor = document.querySelector('.quick-actions-editor');
  if (!editor) return;
  const rows = [...editor.querySelectorAll('.quick-action-slot')];
  rows.forEach((row, index) => {
    row.querySelector('.quick-action-order').hidden = false;
    row.querySelectorAll('[data-direction]').forEach(button => {
      button.addEventListener('click', () => {
        const next = index + Number(button.dataset.direction);
        if (next < 0 || next >= rows.length) return;
        const currentSelect = row.querySelector('select');
        const nextSelect = rows[next].querySelector('select');
        [currentSelect.value, nextSelect.value] = [nextSelect.value, currentSelect.value];
        rows[next].querySelector('[data-direction="' + button.dataset.direction + '"]').focus();
      });
    });
    row.querySelector('[data-remove]').addEventListener('click', () => { row.querySelector('select').value = ''; });
  });
})();
