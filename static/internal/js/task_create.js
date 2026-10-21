/* Progressive task creation. Native links/forms remain the no-script fallback. */
(() => {
  'use strict';
  const dialog = document.querySelector('#task-create-dialog');
  if (!dialog || typeof dialog.showModal !== 'function') return;
  const content = dialog.querySelector('[data-task-dialog-content]');
  const closeButton = dialog.querySelector('[data-task-close]');
  let opener = null, busy = false, created = false, generation = 0;
  const searches = new WeakMap();
  const focusable = () => [...dialog.querySelectorAll('a[href],button,input,select,textarea,[tabindex]')]
    .filter(el => !el.disabled && el.tabIndex >= 0 && el.getClientRects().length);
  const setBusy = value => {
    busy = value; dialog.setAttribute('aria-busy', String(value)); closeButton.disabled = value;
    dialog.querySelectorAll('button[type="submit"]').forEach(button => { button.disabled = value; });
  };
  const focusForm = () => {
    const target = content.querySelector('[data-task-errors]') || content.querySelector('input[name="title"]') || content.querySelector('h2');
    if (target) { if (target.tagName === 'H2') target.tabIndex = -1; target.focus(); }
  };
  const errorMessage = (message, href) => {
    content.replaceChildren();
    const text = document.createElement('p'); text.textContent = message; text.setAttribute('role','alert'); content.append(text);
    const link = document.createElement('a'); link.href = href; link.textContent = 'Open the full task form'; content.append(link); link.focus();
  };
  document.addEventListener('click', async event => {
    const trigger = event.target.closest('a[data-task-create]');
    if (!trigger || event.button > 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    const url = new URL(trigger.href, window.location.href);
    if (url.origin !== window.location.origin || !url.pathname.startsWith('/operations/tasks/quick-create/')) return;
    event.preventDefault();
    if (dialog.open) return;
    opener = trigger; created = false; const current = ++generation;
    content.textContent = 'Loading task form…'; dialog.showModal(); setBusy(false); closeButton.focus();
    try {
      const response = await fetch(url.href, {credentials:'same-origin',headers:{'X-Task-Modal':'1'}});
      const html = await response.text();
      if (!dialog.open || current !== generation) return;
      if (!response.ok) throw new Error('Form unavailable');
      content.innerHTML = html; focusForm();
    } catch (_) {
      if (dialog.open && current === generation) errorMessage('The task form could not be loaded. Use the full-page form or try again.',url.href);
    }
  });
  dialog.addEventListener('cancel', event => {
    if (busy) event.preventDefault();
  });
  dialog.addEventListener('keydown', event => {
    if (event.key !== 'Tab') return;
    const items = focusable();
    if (!items.length) { event.preventDefault(); closeButton.focus(); return; }
    if (event.shiftKey && document.activeElement === items[0]) { event.preventDefault(); items[items.length-1].focus(); }
    else if (!event.shiftKey && document.activeElement === items[items.length-1]) { event.preventDefault(); items[0].focus(); }
  });
  dialog.addEventListener('click', event => {
    if (event.target.closest('[data-task-close],[data-task-cancel]')) {
      event.preventDefault();
      if (!busy) dialog.close();
    }
  });
  dialog.addEventListener('close', async () => {
    ++generation; setBusy(false);
    const previous = opener;
    if (previous && previous.isConnected) previous.focus();
    const workspace = document.querySelector('[data-task-workspace]');
    if (!created || !workspace) return;
    try {
      const response = await fetch(window.location.href,{credentials:'same-origin'});
      if (!response.ok) throw new Error('Refresh unavailable');
      const parsed = new DOMParser().parseFromString(await response.text(),'text/html');
      const next = parsed.querySelector('[data-task-workspace]');
      if (!next) throw new Error('Workspace unavailable');
      workspace.innerHTML = next.innerHTML;
      workspace.querySelector('[data-task-status]').textContent = 'Task created. Your workspace is up to date.';
      const trigger = previous && previous.isConnected ? previous : document.querySelector('[data-global-task-create]'); if (trigger) trigger.focus();
    } catch (_) {
      const status = workspace.querySelector('[data-task-status]');
      if (status) status.textContent = 'Task created. Refresh this workspace to update the list.';
    }
  });
  document.addEventListener('submit', async event => {
    const form = event.target.closest('form[data-task-form]');
    if (!form || !dialog.contains(form)) return;
    event.preventDefault(); if (busy) return;
    const data = new FormData(form); setBusy(true);
    const feedback = form.querySelector('[data-task-feedback]'); if (feedback) feedback.textContent = 'Creating task…';
    try {
      const response = await fetch(form.action,{method:'POST',body:data,credentials:'same-origin',headers:{'X-Task-Modal':'1'}});
      if (response.status === 400) {
        content.innerHTML = await response.text(); setBusy(false); focusForm(); return;
      }
      if (!response.ok || !(response.headers.get('content-type') || '').includes('application/json')) throw new Error('Creation unavailable');
      const result = await response.json();
      const url = new URL(result.url,window.location.origin);
      if (url.origin !== window.location.origin) throw new Error('Invalid task destination');
      created = true; content.replaceChildren();
      const title = document.createElement('h2'); title.textContent = result.replayed ? 'Task already created' : 'Task created'; title.tabIndex = -1;
      const text = document.createElement('p'); text.textContent = result.title;
      const link = document.createElement('a'); link.className = 'button button-primary'; link.href = url.href; link.textContent = 'View task';
      const done = document.createElement('button'); done.type = 'button'; done.className = 'button secondary'; done.dataset.taskClose = ''; done.textContent = 'Back to my work';
      const actions = document.createElement('div'); actions.className = 'task-quick-actions'; actions.append(link,done);
      content.append(title,text,actions); setBusy(false); title.focus();
    } catch (_) {
      setBusy(false);
      if (feedback) feedback.textContent = 'We could not confirm task creation. Your entries are retained. Retry this same form to safely check or finish the request.';
    }
  });
  document.addEventListener('input', event => {
    const input = event.target.closest('[data-record-query]'); if (!input) return;
    const form = input.closest('[data-task-form]'); if (!form) return;
    const kind = input.dataset.recordQuery, select = form.querySelector('[data-record-select="'+kind+'"]');
    const status = input.parentElement.querySelector('.task-record-status');
    const prior = searches.get(input); if (prior) { clearTimeout(prior.timer); if (prior.controller) prior.controller.abort(); }
    const state = {controller:new AbortController()}; searches.set(input,state);
    state.timer = setTimeout(async () => {
      if (status) status.textContent = 'Searching…';
      const url = new URL(form.dataset.taskLookup,window.location.origin); url.searchParams.set('kind',kind); url.searchParams.set('q',input.value);
      try {
        const response = await fetch(url.href,{credentials:'same-origin',signal:state.controller.signal});
        if (!response.ok) throw new Error('Search unavailable');
        const result = await response.json(); if (searches.get(input) !== state) return;
        const selected = [...select.options].filter(option => option.selected).map(option => ({id:option.value,label:option.textContent}));
        const records = new Map(selected.map(record => [record.id,record])); result.results.forEach(record => records.set(record.id,record));
        const chosen = new Set(selected.map(record => record.id)); select.replaceChildren();
        records.forEach(record => { const option = document.createElement('option'); option.value = record.id; option.textContent = record.label; option.selected = chosen.has(record.id); select.append(option); });
        if (status) status.textContent = result.results.length ? result.results.length+' results. Existing selections are retained.' : 'No eligible records match. Existing selections are retained.';
      } catch (error) { if (error.name !== 'AbortError' && status) status.textContent = 'Search unavailable. Your existing choices are retained.'; }
    },250);
  });
})();
