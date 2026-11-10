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
})();
