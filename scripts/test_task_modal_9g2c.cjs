/* Runs the shipped enhancement against a small deterministic DOM adapter. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('static/internal/js/task_create.js','utf8');
function environment() {
  const handlers = {}, pending = [], requests = [], timers = [];
  const document = {activeElement:null,addEventListener(name,fn){handlers[name]=fn;},querySelector(s){return s==='#task-create-dialog'?dialog:s==='[data-task-workspace]'?workspace:null;},createElement(tag){return new Node(tag);}};
  class Node {
    constructor(tag='button'){this.tagName=tag.toUpperCase();this.disabled=false;this.tabIndex=0;this.isConnected=true;this.dataset={};this.children=[];this.attrs={};this.matches={};this.events={};this.textContent='';}
    focus(){document.activeElement=this;}
    setAttribute(k,v){this.attrs[k]=v;}
    getClientRects(){return [{}];}
    closest(s){return this.matches[s]||null;}
    querySelector(s){return this.matches[s]||null;}
    querySelectorAll(){return [];}
    append(...nodes){this.children.push(...nodes);}
    replaceChildren(...nodes){this.children=nodes;}
    addEventListener(k,fn){this.events[k]=fn;}
    get innerHTML(){return this.html||'';}
    set innerHTML(value){this.html=value;if(this===content){this.children=[];this.error=value==='INVALID'?error:null;}}
  }
  const close=new Node(), title=new Node('input'), error=new Node('div'), content=new Node('section'), dialog=new Node('dialog'), form=new Node('form'), trigger=new Node('a'), cancel=new Node('a'), submit=new Node(), feedback=new Node('p'), floating=new Node('a'), status=new Node('p'), workspace=new Node('section'), select=new Node('select'), input=new Node('input');
  trigger.href='https://local.invalid/operations/tasks/quick-create/';trigger.matches['a[data-task-create]']=trigger;
  close.matches['[data-task-close],[data-task-cancel]']=close;cancel.matches['[data-task-close],[data-task-cancel]']=cancel;
  form.action=trigger.href;form.matches['form[data-task-form]']=form;form.matches['[data-task-form]']=form;form.dataset.taskLookup='/operations/tasks/record-lookup/';form.matches['[data-task-feedback]']=feedback;form.matches['[data-record-select="events"]']=select;
  input.dataset.recordQuery='events';input.value='Synthetic';input.matches['[data-record-query]']=input;input.matches['[data-task-form]']=form;input.parentElement=new Node();input.parentElement.matches['.task-record-status']=status;
  const selected=new Node('option');selected.value='one';selected.textContent='Selected';selected.selected=true;select.children=[selected];Object.defineProperty(select,'options',{get(){return this.children;}});
  content.querySelector=s=>s==='[data-task-errors]'?content.error:s==='input[name="title"]'?title:null;
  dialog.matches['[data-task-dialog-content]']=content;dialog.matches['[data-task-close]']=close;
  dialog.contains=node=>node===form;
  dialog.querySelectorAll=s=>s==='button[type="submit"]'?[submit]:[close,title,cancel,submit];
  dialog.showModal=()=>{dialog.open=true;};dialog.close=()=>{dialog.open=false;return dialog.events.close();};
  workspace.matches['[data-task-status]']=status;workspace.matches['.task-add-floating']=floating;
  const fetch=(url,options={})=>{requests.push({url,options});return new Promise((resolve,reject)=>pending.push({resolve,reject}));};
  const context={document,window:{location:{href:'https://local.invalid/operations/tasks/?view=mine',origin:'https://local.invalid'}},URL,WeakMap,Map,Set,AbortController,FormData:class{constructor(f){this.form=f;}},fetch,setTimeout:fn=>{timers.push(fn);return timers.length;},clearTimeout:()=>{},DOMParser:class{parseFromString(){return{querySelector:()=>({innerHTML:'UPDATED'})};}}};
  vm.runInNewContext(source,context);
  const event=(target,extra={})=>({target,button:0,prevented:false,preventDefault(){this.prevented=true;},...extra});
  const respond=(status=200,text='FORM',json=null)=>pending.shift().resolve({ok:status>=200&&status<300,status,headers:{get:()=>json?'application/json':'text/html'},text:async()=>text,json:async()=>json});
  return{handlers,pending,requests,timers,document,dialog,form,trigger,close,title,error,content,cancel,submit,feedback,workspace,floating,status,input,select,event,respond};
}
async function flush(){await Promise.resolve();await Promise.resolve();await Promise.resolve();}
(async()=>{
  let checks=0;
  {
    const e=environment(), click=e.event(e.trigger);const opening=e.handlers.click(click);assert(click.prevented);assert(e.dialog.open);assert.equal(e.document.activeElement,e.close);e.respond();await opening;assert.equal(e.document.activeElement,e.title);
    e.submit.focus();const tab=e.event(e.submit,{key:'Tab'});e.dialog.events.keydown(tab);assert(tab.prevented);assert.equal(e.document.activeElement,e.close);
    const backwards=e.event(e.close,{key:'Tab',shiftKey:true});e.dialog.events.keydown(backwards);assert.equal(e.document.activeElement,e.submit);
    const escape=e.event(e.title);e.dialog.events.cancel(escape);assert(!escape.prevented);await e.dialog.close();assert.equal(e.document.activeElement,e.trigger);checks+=5;
  }
  {
    const e=environment();const first=e.handlers.click(e.event(e.trigger));await e.handlers.click(e.event(e.trigger));assert.equal(e.requests.length,1);await e.dialog.close();e.respond();await first;assert.equal(e.content.innerHTML,'');assert.equal(e.document.activeElement,e.trigger);checks+=2;
  }
  {
    const e=environment();const opening=e.handlers.click(e.event(e.trigger));e.respond();await opening;
    const post=e.handlers.submit(e.event(e.form));assert(e.submit.disabled);assert(e.close.disabled);await e.handlers.submit(e.event(e.form));assert.equal(e.requests.length,2);
    const escape=e.event(e.title);e.dialog.events.cancel(escape);assert(escape.prevented);
    const cancel=e.event(e.cancel);e.dialog.events.click(cancel);assert(cancel.prevented);assert(e.dialog.open);
    e.respond(400,'INVALID');await post;assert(!e.submit.disabled);assert.equal(e.document.activeElement,e.error);assert.equal(e.content.innerHTML,'INVALID');checks+=5;
    const retry=e.handlers.submit(e.event(e.form));e.pending.shift().reject(new Error('Synthetic network loss'));await retry;assert.equal(e.content.innerHTML,'INVALID');assert(e.feedback.textContent.includes('Retry this same form'));assert(!e.submit.disabled);checks+=2;
  }
  {
    const e=environment();const opening=e.handlers.click(e.event(e.trigger));e.respond();await opening;
    const post=e.handlers.submit(e.event(e.form));e.respond(201,'',{url:'/operations/tasks/synthetic/?return_to=safe',title:'<script>plain text</script>',replayed:false});await post;assert.equal(e.content.children[0].textContent,'Task created');assert.equal(e.content.children[1].textContent,'<script>plain text</script>');assert.equal(e.content.children[2].children[0].textContent,'View task');assert.equal(e.document.activeElement,e.content.children[0]);
    const closing=e.dialog.close();e.respond(200,'REFRESH');await closing;assert.equal(e.workspace.innerHTML,'UPDATED');assert.equal(e.document.activeElement,e.floating);assert(e.status.textContent.includes('up to date'));checks+=6;
  }
  {
    const e=environment();const opening=e.handlers.click(e.event(e.trigger));e.pending.shift().reject(new Error('Unavailable'));await opening;assert(e.content.children[0].textContent.includes('full-page'));assert.equal(e.content.children[1].href,e.trigger.href);checks+=2;
    e.handlers.input(e.event(e.input));const searching=e.timers.shift()();e.respond(200,'',{results:[{id:'two',label:'Another event'}]});await searching;assert.equal(e.select.options.length,2);assert.equal(e.select.options[0].selected,true);assert.equal(e.select.options[1].textContent,'Another event');checks+=3;
  }
  console.log('PASS task modal: '+checks+' assertions across keyboard, focus, close, duplicate submit, validation, retry, success, fallback and record lookup.');
})().catch(error=>{console.error(error);process.exitCode=1;});
