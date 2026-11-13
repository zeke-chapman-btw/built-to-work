/* Opt-in progressive record editor. Domain endpoints own validation and return URLs. */
(() => {
  'use strict';
  if (document.documentElement.dataset.btwRecordEditorReady) return;
  document.documentElement.dataset.btwRecordEditorReady = 'true';
  const dialog = document.querySelector('[data-record-editor-dialog]');
  const content = dialog?.querySelector('[data-editor-content]');
  let managerOrigin = null, managerStack = [], managerURL = null, pendingManagerBack = false, historySettled = null, closing = false;
  let originScroll = {x:0,y:0}, originalRestoration = history.scrollRestoration;
  let opener = null, busy = false, dirty = false, generation = 0, ownHistory = false, historyToken = 0;
  const sameOrigin = href => { try { const u = new URL(href, location.href); return u.origin === location.origin && ['http:', 'https:'].includes(u.protocol) ? u : null; } catch { return null; } };
  const request = async (url, options = {}) => {
    const controller = new AbortController(), timeout = setTimeout(() => controller.abort(),15000);
    try { return await fetch(url,{...options,signal:controller.signal}); } finally { clearTimeout(timeout); }
  };
  // Restore the originating action after a successful server-confirmed reload.
  // This is tab-local, one-use UI state and never grants access to a record.
  const returnFocusKey = 'btwRecordEditorReturn';
  try {
    const saved = JSON.parse(sessionStorage.getItem(returnFocusKey) || 'null');
    sessionStorage.removeItem(returnFocusKey);
    if (saved && saved.path === location.pathname + location.search && Date.now() - saved.at < 60000) {
      const action = [...document.querySelectorAll('a[data-record-editor]')].find(a => a.href === saved.opener);
      if (action) requestAnimationFrame(() => { const menu=action.closest('details[data-ui-menu]'); const target=menu && !menu.open ? menu.querySelector('summary') : action; target?.focus({preventScroll:true}); window.scrollTo(saved.x,saved.y); });
    }
  } catch { /* Browser storage can be disabled; ordinary navigation still works. */ }
  const rememberReturnFocus = target => {
    if (!opener?.isConnected) return;
    try { sessionStorage.setItem(returnFocusKey,JSON.stringify({path:target.pathname+target.search,opener:opener.href,x:scrollX,y:scrollY,at:Date.now()})); } catch {}
  };
  const activeForm = () => content?.querySelector('[data-editor-form]');
  const feedback = (text, error = false) => {
    let status = content?.querySelector('[data-editor-status]');
    if (!status && content) { status = document.createElement('p'); status.dataset.editorStatus = ''; content.append(status); }
    if (status) { status.textContent = text; status.setAttribute('role', error ? 'alert' : 'status'); }
  };
  const focusPanel = () => {
    const visible = selector => [...content.querySelectorAll(selector)].find(e => e.getClientRects().length && !e.disabled);
    const target = visible('[data-editor-recovery],.btw-form-errors,.errorlist') || (content.querySelector('[data-profile-manager-panel]') ? visible('h2') : null) || visible('[data-editor-autofocus] input,[data-editor-autofocus] select,[data-editor-autofocus] textarea') || visible('input:not([type=hidden]),select,textarea') || visible('h2');
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
    content.replaceChildren(panel);
    const manager = panel.querySelector('[data-profile-manager-panel]');
    if (manager) {
      manager.querySelectorAll('a[href]').forEach(a => { const resolved=new URL(a.getAttribute('href'),managerURL || location.href); const u=sameOrigin(resolved.href); if(u)a.href=u.href; if(u && u.pathname.startsWith('/participants/') && !/download/.test(u.pathname)) a.dataset.editorNavigate=''; });
      manager.querySelectorAll('form').forEach(f => { if(manager.hasAttribute('data-editor-conflict'))f.dataset.saveBlocked=''; f.dataset.editorForm=''; f.dataset.cautiousSave=''; if(!f.hasAttribute('action') || !f.getAttribute('action')) f.action=managerURL; });
    }
    const back=panel.querySelector('[data-editor-manager-back]');if(back)back.hidden=!managerStack.length;
    dialog.setAttribute('aria-labelledby','record-editor-title');
    content.querySelectorAll('[data-editor-close]').forEach(b => { b.hidden = false; });
    content.querySelectorAll('button[type=submit]').forEach(b => { b.dataset.originalLabel = b.textContent; });
    setBusy(false); fitViewport(); focusPanel();
  };
  const rememberEditor = () => { history.pushState({...history.state,btwRecordEditor:historyToken},'',location.href); ownHistory = true; };
  const close = async () => {
    if(busy || closing)return;
    closing=true;
    const shouldPop=ownHistory && history.state?.btwRecordEditor===historyToken;
    ownHistory=false;
    // Finish our same-URL history entry while the modal still owns focus. This
    // prevents native scroll restoration moving the next pointer target.
    historySettled=new Promise(resolve=>{
      let timer,done=false;
      const finish=()=>{if(done)return;done=true;clearTimeout(timer);window.removeEventListener('popstate',finish);requestAnimationFrame(resolve);};
      if(shouldPop){window.addEventListener('popstate',finish,{once:true});timer=setTimeout(finish,1500);history.back();}
      else finish();
    });
    await historySettled;historySettled=null;
    ++generation;dirty=false;busy=false;
    dialog.close();
    if(opener?.isConnected){const menu=opener.closest('details[data-ui-menu]');const target=menu && !menu.open ? menu.querySelector('summary') : opener;target?.focus({preventScroll:true});}
    window.scrollTo(originScroll.x,originScroll.y);
    history.scrollRestoration=originalRestoration;
    closing=false;
  };
  const returnManager = () => {
    const prior=managerStack.pop();if(!prior)return;
    pendingManagerBack=false;managerURL=prior.url;dirty=false;render(prior.html);
    content.querySelector(`[data-manager-focus="${prior.focus}"]`)?.focus();
    dialog.scrollTop=prior.scroll;
  };
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
      const url = sameOrigin(link.dataset.profileView || link.href); if (!url || dialog.open) return;
      event.preventDefault();
      if(historySettled)await historySettled;
      if(!link.isConnected || dialog.open)return;
      opener = link; dirty = false; managerOrigin = link.hasAttribute('data-profile-manager') ? new URL(location.href) : null; managerStack=[]; managerURL=url.href;
      originScroll={x:scrollX,y:scrollY};originalRestoration=history.scrollRestoration;history.scrollRestoration='manual';
      const current = ++generation;
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
      const back=event.target.closest('[data-editor-manager-back]');
      if(back){event.preventDefault();if(busy)return;if(dirty){pendingManagerBack=true;requestClose();return;}returnManager();return;}
      const link=event.target.closest('a[data-editor-navigate]');
      if(!link || event.button>0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey)return;
      const url=sameOrigin(link.dataset.profileView || link.href);if(!url)return;event.preventDefault();if(busy)return;
      if(dirty){feedback('Save or cancel your changes before opening another record.',true);return;}
      const focus='manager-'+Date.now();link.dataset.managerFocus=focus;
      const prior={url:managerURL,html:content.innerHTML,scroll:dialog.scrollTop,focus};
      setBusy(true);
      try{const response=await request(url.href,{credentials:'same-origin',headers:{'X-BTW-Editor':'1'}});if(!response.ok || response.headers.get('X-BTW-Editor')!=='1')throw new Error('Unavailable');const html=await response.text();managerStack.push(prior);managerURL=url.href;render(html);dirty=false;}
      catch{setBusy(false);feedback('This view could not be opened here. Your current information is retained.',true);}
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
    dialog.addEventListener('input', event => { if(!event.target.closest('[data-editor-query]')) dirty = true; });
    dialog.addEventListener('change', event => { if(!event.target.closest('[data-editor-query]')) dirty = true; });
    dialog.addEventListener('cancel', event => { event.preventDefault(); requestClose(); });
    dialog.addEventListener('click', event => {
      if (event.target.closest('[data-editor-close],[data-editor-cancel]')) { event.preventDefault(); if(event.target.closest('[data-editor-cancel]') && managerStack.length){if(dirty){pendingManagerBack=true;requestClose();}else returnManager();}else{pendingManagerBack=false;requestClose();} }
      if (event.target.closest('[data-editor-keep]')) { event.preventDefault(); pendingManagerBack=false; content.querySelector('[data-editor-discard-prompt]')?.remove(); if (!ownHistory) rememberEditor(); focusPanel(); }
      if (event.target.closest('[data-editor-discard]')) { event.preventDefault(); if(pendingManagerBack && managerStack.length){returnManager();if(!ownHistory)rememberEditor();}else close(); }
    });
    window.addEventListener('popstate', () => {
      if (!dialog.open || !ownHistory) return;
      ownHistory = false;
      if (busy) { rememberEditor(); feedback('A save is in progress. Please wait.'); return; }
      if(managerStack.length){if(dirty){pendingManagerBack=true;requestClose();}else{returnManager();rememberEditor();}return;}
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
      const query=event.target.closest('[data-editor-query]');
      if(query){event.preventDefault();if(busy||dirty)return;const url=sameOrigin(query.action);if(!url)return;url.search=new URLSearchParams(new FormData(query)).toString();setBusy(true);try{const response=await request(url.href,{credentials:'same-origin',headers:{'X-BTW-Editor':'1'}});if(!response.ok||response.headers.get('X-BTW-Editor')!=='1')throw new Error('Unavailable');managerURL=url.href;render(await response.text());}catch{setBusy(false);feedback('The selected records could not be loaded.',true);}return;}
      const form = event.target.closest('[data-editor-form]'); if (!form) return;
      event.preventDefault(); if (busy || form.hasAttribute('data-save-blocked')) return;
      const url = sameOrigin(form.action); if (!url) return;
      const data = new FormData(form); if (event.submitter?.name) data.append(event.submitter.name,event.submitter.value); setBusy(true); feedback('Saving…');
      try {
        const response = await request(url.href,{method:'POST',body:data,credentials:'same-origin',headers:{'X-BTW-Editor':'1'}});
        if (response.headers.get('X-BTW-Editor') !== '1') throw new Error('Unconfirmed response');
        if ([400,409].includes(response.status)) {
          const selectedFiles=[...form.querySelectorAll('input[type=file]')].filter(input=>input.files?.length);
          render(await response.text());
          selectedFiles.forEach(input=>{const replacement=[...content.querySelectorAll('input[type=file]')].find(field=>field.name===input.name);if(replacement){for(const key of ['aria-invalid','aria-describedby']){if(replacement.hasAttribute(key))input.setAttribute(key,replacement.getAttribute(key));}replacement.replaceWith(input);}});
          dirty = true; return;
        }
        if (!response.ok || !(response.headers.get('Content-Type') || '').includes('application/json')) throw new Error('Save unavailable');
        const result = await response.json(); if(typeof result.next==='string'){const next=sameOrigin(result.next);if(!next)throw new Error('Invalid destination');const step=await request(next.href,{credentials:'same-origin',headers:{'X-BTW-Editor':'1'}});if(!step.ok||step.headers.get('X-BTW-Editor')!=='1')throw new Error('Unavailable');setBusy(false);render(await step.text());return;} if(typeof result.url!=='string' || !sameOrigin(result.url)) throw new Error('Invalid destination'); const target=managerOrigin || sameOrigin(result.url);
        rememberReturnFocus(target);
        ownHistory = false; dirty = false; history.scrollRestoration=originalRestoration; feedback('Saved. Returning to your list…');
        if (target.pathname === location.pathname && target.search === location.search) {
          const state = {...history.state}; delete state.btwRecordEditor; history.replaceState(state,'',target.href); location.reload();
        } else location.replace(target.href);
      } catch {
        if(form.hasAttribute('data-cautious-save')){
          form.setAttribute('data-save-blocked','');setBusy(false);
          feedback('We could not confirm the save. Your entries are retained. Check the current records before trying again.',true);
          const target=sameOrigin(form.querySelector('[name=return_to]')?.value);
          if(target){const link=document.createElement('a');link.href=target.href;link.target='_blank';link.rel='noopener';link.textContent='Open current records';content.querySelector('[data-editor-status]').append(' ',link);}
          return;
        }
        setBusy(false); feedback(form.dataset.safeRetry === 'true' ? 'We could not confirm the save. Your entries are retained. Retry this same form to safely check or finish the request.' : 'We could not confirm the save. Your entries are retained. Check the event in another tab before trying again so you do not create a duplicate record.',true);
      }
    });
    window.visualViewport?.addEventListener('resize', fitViewport); window.addEventListener('resize',fitViewport);
    window.addEventListener('pagehide',()=>{if(dialog.open)history.scrollRestoration=originalRestoration;});
  }
  // Full-page fallback still receives validation focus and native saving feedback.
  document.querySelectorAll('.btw-editor-fallback [data-editor-form]:not([data-section-form])').forEach(form => {
    const errors = form.querySelector('[data-editor-recovery],.btw-form-errors'); if (errors) { errors.tabIndex = -1; errors.focus(); }
    const button = form.querySelector('button[type=submit]'), label = button.textContent, blocked = button.disabled;
    form.addEventListener('submit', event => { if (button.disabled) { event.preventDefault(); return; } button.disabled = true; button.textContent = form.dataset.saveLabel || 'Saving…'; });
    window.addEventListener('pageshow', () => { button.disabled = blocked; button.textContent = label; });
  });
})();
