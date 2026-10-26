'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');

function fullFormFixture(optIn=true){
  const events={}, formEvents={};
  const form={dataset:{},querySelector:()=>null,addEventListener:(name,fn)=>{formEvents[name]=fn;}};
  const document={querySelector:()=>null,querySelectorAll:selector=>selector==='[data-event-dirty-guard]'&&optIn?[form]:[],addEventListener:()=>{}};
  const window={addEventListener:(name,fn)=>{events[name]=fn;}};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../static/internal/js/record_presentation.js'),'utf8'),{document,window,WeakSet});
  return {form,formEvents,events};
}
test('only an edited opt-in full EventForm requests a navigation-away warning',()=>{
  const f=fullFormFixture();let prevented=false;
  const event=()=>({preventDefault(){prevented=true;},returnValue:undefined});
  f.events.beforeunload(event());assert.equal(prevented,false);
  f.formEvents.input();const e=event();f.events.beforeunload(e);
  assert.equal(prevented,true);assert.equal(e.returnValue,'');
});
test('accepted native submission does not warn, and a failed/restored form remains guarded',()=>{
  const f=fullFormFixture();f.formEvents.change();f.form.dataset.saving='true';
  let count=0;f.events.beforeunload({preventDefault(){count++;}});assert.equal(count,0);
  delete f.form.dataset.saving;
  f.events.beforeunload({preventDefault(){count++;}});assert.equal(count,1);
});
test('unrelated full-page forms receive no new guard',()=>{
  const f=fullFormFixture(false);assert.equal(f.events.beforeunload,undefined);
});

function menuFixture(){
  const handlers={};let focused=0;
  const inside={},outside={};
  const menu={open:true,contains:t=>t===inside,querySelector:()=>({focus(){focused++;}})};
  const document={addEventListener:(name,fn)=>{handlers[name]=fn;},querySelectorAll:()=>menu.open?[menu]:[]};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../static/internal/js/ui_patterns.js'),'utf8'),{document});
  return {handlers,menu,inside,outside,focus:()=>focused};
}
test('Escape in More closes it and returns focus to its summary',()=>{
  const f=menuFixture();let prevented=false;
  f.handlers.keydown({key:'Escape',target:f.inside,preventDefault(){prevented=true;}});
  assert.equal(f.menu.open,false);assert.equal(f.focus(),1);assert.equal(prevented,true);
});
test('outside pointer closes More without trapping focus; inside pointer retains it',()=>{
  const f=menuFixture();f.handlers.pointerdown({target:f.inside});assert.equal(f.menu.open,true);
  f.handlers.pointerdown({target:f.outside});assert.equal(f.menu.open,false);assert.equal(f.focus(),0);
});
