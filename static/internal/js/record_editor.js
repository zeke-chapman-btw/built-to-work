/* Opt-in progressive record editor. Domain endpoints own validation and return URLs. */
(() => {
  'use strict';
  if (document.documentElement.dataset.btwRecordEditorReady) return;
  document.documentElement.dataset.btwRecordEditorReady = 'true';
  const dialog = document.querySelector('[data-record-editor-dialog]');
  const content = dialog?.querySelector('[data-editor-content]');
  let opener = null, busy = false, dirty = false, generation = 0, ownHistory = false, historyToken = 0;
  const sameOrigin = href => { try { const u = new URL(href, location.href); return u.origin === location.origin && ['http:', 'https:'].includes(u.protocol) ? u : null; } catch { return null; } };
  const request = async (url, options = {}) => {
    const controller = new AbortController(), timeout = setTimeout(() => controller.abort(),15000);
    try { return await fetch(url,{...options,signal:controller.signal}); } finally { clearTimeout(timeout); }
  };
  const activeForm = () => content?.querySelector('[data-editor-form]');
  const feedback = (text, error = false) => {
    let status = content?.querySelector('[data-editor-status]');
    if (!status && content) { status = document.createElement('p'); status.dataset.editorStatus = ''; content.append(status); }
    if (status) { status.textContent = text; status.setAttribute('role', error ? 'alert' : 'status'); }
  };
  const focusPanel = () => {
    const target = content.querySelector('[data-editor-recovery],.btw-form-errors') || content.querySelector('[data-editor-autofocus] input,[data-editor-autofocus] select,[data-editor-autofocus] textarea') || content.querySelector('input:not([type=hidden]),select,textarea') || content.querySelector('h2');
    if (target) { if (!target.matches('input,select,textarea')) target.tabIndex = -1; target.focus(); }
  };
  const setBusy = value => {
    busy = value; dialog?.setAttribute('aria-busy', String(value));
    content?.querySelectorAll('button[type=submit]').forEach(b => { b.disabled = value || !!activeForm()?.hasAttribute('data-save-blocked'); b.textContent = value ? (activeForm()?.dataset.saveLabel || 'Saving…') : b.dataset.originalLabel; });
    content?.querySelectorAll('[data-editor-close]').forEach(b => { b.disabled = value; });
  };
  const fitViewport = () => { if (dialog?.open) dialog.style.setProperty('--btw-editor-height', (window.visualViewport?.height || innerHeight) + 'px'); };
  const render = html => {
    const parsed = new DOMParser().parseFromString(html, 'text/html');
    const panel = parsed.querySelector('[data-editor-panel]');
    if (!panel || panel.querySelector('script')) throw new Error('Invalid editor response');
    content.replaceChildren(panel); dialog.setAttribute('aria-labelledby','record-editor-title');
    content.querySelectorAll('[data-editor-close]').forEach(b => { b.hidden = false; });
    content.querySelectorAll('button[type=submit]').forEach(b => { b.dataset.originalLabel = b.textContent; });
    setBusy(false); fitViewport(); focusPanel();
  };
  const rememberEditor = () => { history.pushState({...history.state,btwRecordEditor:historyToken},'',location.href); ownHistory = true; };
  const close = () => { if (!busy) dialog.close(); };
  const requestClose = () => {
    if (busy) return;
    if (!dirty) { close(); return; }
    let prompt = content.querySelector('[data-editor-discard-prompt]');
    if (!prompt) {
      prompt = document.createElement('section'); prompt.className = 'btw-feedback btw-feedback-warning btw-discard-prompt'; prompt.dataset.editorDiscardPrompt = '';
      const text = document.createElement('p'); text.textContent = 'Discard unsaved changes? Your changes have not been saved.'; text.setAttribute('role','alert');
      const keep = document.createElement('button'); keep.type = 'button'; keep.className = 'button button-primary'; keep.dataset.editorKeep = ''; keep.textContent = 'Keep editing';
      const discard = document.createElement('button'); discard.type = 'button'; discard.className = 'button secondary'; discard.dataset.editorDiscard = ''; discard.textContent = 'Discard changes';
      prompt.append(text, keep, discard); content.prepend(prompt);
    }
    prompt.querySelector('[data-editor-keep]').focus();
  };
  if (dialog && typeof dialog.showModal === 'function') {
    document.addEventListener('click', async event => {
      const link = event.target.closest('a[data-record-editor]');
      if (!link || event.button > 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
      const url = sameOrigin(link.href); if (!url || dialog.open) return;
      event.preventDefault(); opener = link; dirty = false; const current = ++generation;
      content.replaceChildren();
      const loading = document.createElement('p'); loading.textContent = 'Loading…'; loading.setAttribute('role','status');
      const cancel = document.createElement('button'); cancel.type = 'button'; cancel.className = 'button secondary'; cancel.dataset.editorClose = ''; cancel.textContent = 'Cancel';
      content.append(loading,cancel); dialog.showModal(); historyToken = Date.now(); rememberEditor(); fitViewport(); cancel.focus();
      try {
        const response = await request(url.href, {credentials:'same-origin',headers:{'X-BTW-Editor':'1'}});
        const html = await response.text();
        if (!dialog.open || current !== generation) return;
        if (!response.ok || response.headers.get('X-BTW-Editor') !== '1') throw new Error('Editor unavailable');
        render(html);
      } catch {
        if (!dialog.open || current !== generation) return;
        content.replaceChildren();
        const p = document.createElement('p'); p.setAttribute('role','alert'); p.textContent = 'The editor could not be loaded. Open the full form or close and try again.';
        const fallback = document.createElement('a'); fallback.href = url.href; fallback.textContent = 'Open full form';
        const cancel = document.createElement('button'); cancel.type = 'button'; cancel.textContent = 'Close'; cancel.dataset.editorClose = ''; cancel.className = 'button secondary';
        content.append(p,fallback,cancel); fallback.focus();
      }
    });
    dialog.addEventListener('click', async event => {
      const link = event.target.closest('[data-editor-reload]'); if (!link) return;
      const url = sameOrigin(link.href); if (!url) return;
      event.preventDefault(); if (busy) return; setBusy(true);
      try {
        const response = await request(url.href,{credentials:'same-origin',headers:{'X-BTW-Editor':'1'}});
        if (!response.ok || response.headers.get('X-BTW-Editor') !== '1') throw new Error('Reload unavailable');
        render(await response.text()); dirty = false;
      } catch { setBusy(false); feedback('The latest version could not be loaded. Your entries are still retained.',true); }
    });
    dialog.addEventListener('input', () => { dirty = true; });
    dialog.addEventListener('change', () => { dirty = true; });
    dialog.addEventListener('cancel', event => { event.preventDefault(); requestClose(); });
    dialog.addEventListener('click', event => {
      if (event.target.closest('[data-editor-close],[data-editor-cancel]')) { event.preventDefault(); requestClose(); }
      if (event.target.closest('[data-editor-keep]')) { event.preventDefault(); content.querySelector('[data-editor-discard-prompt]')?.remove(); if (!ownHistory) rememberEditor(); focusPanel(); }
      if (event.target.closest('[data-editor-discard]')) { event.preventDefault(); close(); }
    });
    dialog.addEventListener('close', () => { ++generation; dirty = false; busy = false; if (ownHistory && history.state?.btwRecordEditor === historyToken) { ownHistory = false; history.back(); } if (opener?.isConnected) opener.focus(); });
    window.addEventListener('popstate', () => {
      if (!dialog.open || !ownHistory) return;
      ownHistory = false;
      if (busy) { rememberEditor(); feedback('A save is in progress. Please wait.'); return; }
      requestClose();
    });
    dialog.addEventListener('keydown', event => {
      if (event.key !== 'Tab') return;
      const fields = [...dialog.querySelectorAll('a[href],button,input,select,textarea,[tabindex]')].filter(e => !e.disabled && e.tabIndex >= 0 && e.getClientRects().length && !e.closest('details:not([open]) > :not(summary)'));
      if (!fields.length) return;
      if (event.shiftKey && document.activeElement === fields[0]) { event.preventDefault(); fields.at(-1).focus(); }
      else if (!event.shiftKey && document.activeElement === fields.at(-1)) { event.preventDefault(); fields[0].focus(); }
    });
    dialog.addEventListener('submit', async event => {
      const form = event.target.closest('[data-editor-form]'); if (!form) return;
      event.preventDefault(); if (busy || form.hasAttribute('data-save-blocked')) return;
      const url = sameOrigin(form.action); if (!url) return;
      const data = new FormData(form); if (event.submitter?.name) data.append(event.submitter.name,event.submitter.value); setBusy(true); feedback('Saving…');
      try {
        const response = await request(url.href,{method:'POST',body:data,credentials:'same-origin',headers:{'X-BTW-Editor':'1'}});
        if (response.headers.get('X-BTW-Editor') !== '1') throw new Error('Unconfirmed response');
        if ([400,409].includes(response.status)) { render(await response.text()); dirty = true; return; }
        if (!response.ok || !(response.headers.get('Content-Type') || '').includes('application/json')) throw new Error('Save unavailable');
        const result = await response.json(), target = sameOrigin(result.url); if (!target) throw new Error('Invalid destination');
        ownHistory = false; dirty = false; feedback('Saved. Returning to your list…');
        if (target.pathname === location.pathname && target.search === location.search) {
          const state = {...history.state}; delete state.btwRecordEditor; history.replaceState(state,'',target.href); location.reload();
        } else location.replace(target.href);
      } catch {
        setBusy(false); feedback(form.dataset.safeRetry === 'true' ? 'We could not confirm the save. Your entries are retained. Retry this same form to safely check or finish the request.' : 'We could not confirm the save. Your entries are retained. Check the event in another tab before trying again so you do not create a duplicate record.',true);
      }
    });
    window.visualViewport?.addEventListener('resize', fitViewport); window.addEventListener('resize',fitViewport);
  }
  // Full-page fallback still receives validation focus and native saving feedback.
  document.querySelectorAll('.btw-editor-fallback [data-editor-form]').forEach(form => {
    const errors = form.querySelector('[data-editor-recovery],.btw-form-errors'); if (errors) { errors.tabIndex = -1; errors.focus(); }
    const button = form.querySelector('button[type=submit]'), label = button.textContent, blocked = button.disabled;
    form.addEventListener('submit', event => { if (button.disabled) { event.preventDefault(); return; } button.disabled = true; button.textContent = form.dataset.saveLabel || 'Saving…'; });
    window.addEventListener('pageshow', () => { button.disabled = blocked; button.textContent = label; });
  });
})();
