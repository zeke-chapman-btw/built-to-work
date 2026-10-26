/* Opt-in, presentation-only enhancements shared by event record surfaces. */
(() => {
  'use strict';
  // Per-tab list context belongs to this event; permission checks remain on every destination.
  const list=document.querySelector('[data-event-discovery]');
  const validList=raw=>{try{const u=new URL(raw,location.origin);return u.origin===location.origin && u.pathname==='/events/' && !u.hash && [...u.searchParams.keys()].every(k=>['view','q','event_type','date_from','date_to','page'].includes(k)) ? u : null;}catch{return null;}};
  const stored=(key)=>{try{return JSON.parse(sessionStorage.getItem(key));}catch{return null;}};
  if(list){
    list.querySelectorAll('[data-event-open]').forEach(link=>link.addEventListener('click',()=>{
      try{sessionStorage.setItem('btw.events.origin.'+link.dataset.eventOpen,JSON.stringify({url:location.pathname+location.search,y:scrollY}));}catch{}
    }));
    const state=stored('btw.events.restore');
    if(state && validList(state.url)?.href===location.href){
      try{sessionStorage.removeItem('btw.events.restore');}catch{}
      requestAnimationFrame(()=>requestAnimationFrame(()=>window.scrollTo(0,Number(state.y)||0)));
    }
  }
  const header=document.querySelector('[data-event-id]'),back=header?.querySelector('[data-event-list-back]');
  if(back && new URL(back.href).pathname==='/events/'){
    const state=stored('btw.events.origin.'+header.dataset.eventId),url=state && validList(state.url);
    if(url){back.href=url.href;back.addEventListener('click',()=>{try{sessionStorage.setItem('btw.events.restore',JSON.stringify(state));}catch{}});}
  }
  document.querySelectorAll('[data-record-table]').forEach(table => {
    const headings=[...table.querySelectorAll('thead th')].map(th=>th.textContent.trim());
    if (!headings.length || headings.length>6 || table.querySelector('[rowspan],[colspan]')) return;
    table.classList.add('btw-compact-table');
    table.querySelectorAll('tbody tr').forEach(row=>[...row.cells].forEach((cell,i)=>{cell.dataset.label=headings[i] || '';}));
  });
  document.querySelectorAll('.btw-event-section-picker').forEach(menu=>{menu.hidden=false;});
  document.querySelectorAll('[data-copy-value]').forEach(button=>button.addEventListener('click',async()=>{
    const input=document.querySelector(button.dataset.copyValue), status=document.querySelector('[data-copy-status]');
    if(!input || !status)return;
    try {await navigator.clipboard.writeText(input.value);status.textContent='Registration link copied.';}
    catch {input.focus();input.select();status.textContent='Select and copy the registration link below.';}
  }));
  document.querySelectorAll('[data-field-condition]').forEach(group=>{
    const control=group.closest('form')?.querySelector(group.dataset.fieldCondition),note=group.querySelector('[data-condition-retained]');
    if(!control)return;
    const update=()=>{
      const relevant=control.value===group.dataset.conditionValue;
      const retained=[...group.querySelectorAll('input,select,textarea')].some(input=>input.value.trim());
      group.hidden=!relevant && !retained && !group.querySelector('.errorlist');
      if(note)note.hidden=relevant || !retained;
    };
    control.addEventListener('change',update);group.addEventListener('input',update);update();
  });
  document.querySelectorAll('[data-record-form]').forEach(form=>{
    const buttons=[...form.querySelectorAll('button[type=submit]')];
    const state=buttons.map(b=>({b,disabled:b.disabled,label:b.textContent}));
    form.addEventListener('invalid',event=>{
      for (let p=event.target.parentElement;p && p!==form;p=p.parentElement) if(p.tagName==='DETAILS') p.open=true;
    },true);
    const errors=form.querySelector('.btw-form-errors,[data-record-recovery]');
    if(errors){errors.tabIndex=-1;errors.focus();}
    form.addEventListener('submit',event=>{
      if(form.dataset.saving){event.preventDefault();return;}
      form.dataset.saving='true';
      if(event.submitter?.name){const input=document.createElement('input');input.type='hidden';input.name=event.submitter.name;input.value=event.submitter.value;input.dataset.recordSubmitter='';form.append(input);}
      buttons.forEach(b=>{b.disabled=true;if(b===event.submitter)b.textContent='Saving…';});
      form.setAttribute('aria-busy','true');
    });
    window.addEventListener('pageshow',()=>{delete form.dataset.saving;form.removeAttribute('aria-busy');form.querySelectorAll('[data-record-submitter]').forEach(e=>e.remove());state.forEach(({b,disabled,label})=>{b.disabled=disabled;b.textContent=label;});});
  });
  // Progressive full-page forms complement the same record editor. Existing unmarked forms keep their behavior.
  const sectionSaving=new WeakSet();
  document.addEventListener('invalid',event=>{
    const form=event.target.form;
    if(!form?.matches('[data-section-form]'))return;
    for(let parent=event.target.parentElement;parent && parent!==form;parent=parent.parentElement)if(parent.tagName==='DETAILS')parent.open=true;
  },true);
  document.addEventListener('submit',async event=>{
    const form=event.target;
    if(!(form instanceof HTMLFormElement) || !form.matches('[data-section-form]') || form.closest('dialog'))return;
    const action=new URL(form.getAttribute('action') || location.href,location.href);
    if(action.origin!==location.origin || form.method.toLowerCase()!=='post')return;
    event.preventDefault();
    if(sectionSaving.has(form) || form.hasAttribute('data-save-blocked'))return;
    const data=new FormData(form),submitter=event.submitter;
    if(submitter?.name)data.append(submitter.name,submitter.value);
    const buttons=[...form.querySelectorAll('button[type=submit],button:not([type]),input[type=submit]')];
    const originals=buttons.map(button=>({button,disabled:button.disabled,text:button.textContent}));
    let status=form.querySelector('[data-section-status]');
    if(!status){status=document.createElement('section');status.dataset.sectionStatus='';status.className='btw-feedback';status.setAttribute('aria-live','polite');form.prepend(status);}
    const message=(text,alert=false)=>{status.replaceChildren();status.setAttribute('role',alert?'alert':'status');const p=document.createElement('p');p.textContent=text;status.append(p);};
    const recovery=text=>{
      form.setAttribute('data-save-blocked','');message(text,true);
      const link=document.createElement('a');link.href=location.pathname+location.search;link.target='_blank';link.rel='noopener';link.textContent='Open the current record';status.append(link);
      status.tabIndex=-1;status.focus();
    };
    sectionSaving.add(form);form.setAttribute('aria-busy','true');buttons.forEach(button=>button.disabled=true);message('Saving…');
    try{
      const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),15000);
      let response;
      try{response=await fetch(action.href,{method:'POST',body:data,credentials:'same-origin',signal:controller.signal,headers:{'Accept':'text/html','X-BTW-Full-Form':'1'}});}finally{clearTimeout(timeout);}
      const target=new URL(response.url,location.href);
      if(response.redirected && response.ok && target.origin===location.origin && !target.pathname.startsWith('/admin/login/')){location.assign(target.href);return;}
      const html=await response.text(),doc=new DOMParser().parseFromString(html,'text/html');
      const incoming=doc.querySelector('[data-section-form]');
      if(!incoming || ![200,400,409,422].includes(response.status))throw new Error('Save not confirmed');
      const messages=[...doc.querySelectorAll('.btw-form-errors li,.errorlist li,p[role=alert],p.alert')].map(e=>e.textContent.trim()).filter(Boolean);
      if(!messages.length)throw new Error('Save not confirmed');
      if(form.hasAttribute('data-server-review')){
        if(incoming.querySelector('script') || form.querySelector('input[type=file]'))throw new Error('Unsupported review response');
        const replacement=document.importNode(incoming,true);form.replaceWith(replacement);
        const errors=replacement.querySelector('.btw-form-errors,[role=alert]');if(errors){errors.tabIndex=-1;errors.focus();}
        return;
      }
      // Keep the original controls and selected files; transfer error presentation only.
      form.querySelectorAll('[aria-invalid=true]').forEach(el=>el.removeAttribute('aria-invalid'));
      incoming.querySelectorAll('[aria-invalid=true]').forEach(el=>{if(el.id){const current=form.querySelector('#'+CSS.escape(el.id));if(current)current.setAttribute('aria-invalid','true');}});
      message('Review the following before saving:',true);
      const list=document.createElement('ul');[...new Set(messages)].forEach(text=>{const li=document.createElement('li');li.textContent=text;list.append(li);});status.append(list);
      const combined=messages.join(' ').toLowerCase();
      const conflict=incoming.hasAttribute('data-save-blocked') || response.status===409 || (combined.includes('changed') && combined.includes('reload'));
      if(conflict){
        form.setAttribute('data-save-blocked','');
        const p=document.createElement('p');p.textContent='Your entries are retained. Copy anything you need before loading the latest version.';status.append(p);
        const link=document.createElement('a');link.href=location.pathname+location.search;link.textContent='Load the latest version';status.append(link);
      }else originals.forEach(({button,disabled,text})=>{button.disabled=disabled;button.textContent=text;});
      form.querySelectorAll('[aria-invalid=true]').forEach(el=>{for(let p=el.parentElement;p && p!==form;p=p.parentElement)if(p.tagName==='DETAILS')p.open=true;});
      status.tabIndex=-1;status.focus();
    }catch{
      recovery('We couldn’t confirm the save. Your entries are still here. Check the current record before trying again.');
    }finally{
      form.removeAttribute('aria-busy');sectionSaving.delete(form);
    }
  });

  // Only the opt-in full EventForm gains navigation-away protection. No autosave.
  document.querySelectorAll('[data-event-dirty-guard]').forEach(form => {
    let dirty = !!form.querySelector('.btw-form-errors,.errorlist,[data-record-recovery]');
    form.addEventListener('input', () => { dirty = true; });
    form.addEventListener('change', () => { dirty = true; });
    window.addEventListener('beforeunload', event => {
      if (dirty && !form.dataset.saving) { event.preventDefault(); event.returnValue = ''; }
    });
  });

})();
