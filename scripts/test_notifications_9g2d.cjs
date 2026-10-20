/* Exercise the shipped bell enhancements against a minimal DOM adapter. */
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync('static/internal/js/notifications.js','utf8');
let checks=0;
function check(value){assert.ok(value);checks++;}
function fixture(present=true){
 const handlers={};let focus=0,dialog=false;
 const inside={};const summary={focus(){focus++;}};
 const bell={open:false,querySelector:s=>summary,contains:t=>t===inside||t===summary};
 const document={querySelector:s=>s==='[data-notification-bell]'?(present?bell:null):(dialog?{}:null),addEventListener:(k,fn)=>handlers[k]=fn};
 vm.runInNewContext(source,{document});return {bell,handlers,inside,summary,focus:()=>focus,setDialog:v=>dialog=v};
}
let f=fixture(false);check(Object.keys(f.handlers).length===0);
f=fixture();check(!f.bell.open);check(typeof f.handlers.keydown==='function');
let prevented=false;
f.bell.open=true;f.handlers.keydown({key:'Escape',preventDefault(){prevented=true;}});
check(!f.bell.open);check(prevented);check(f.focus()===1);
f.bell.open=true;f.handlers.keydown({key:'Tab',preventDefault(){throw Error('Tab must remain native');}});check(f.bell.open);
f.setDialog(true);f.handlers.keydown({key:'Escape',preventDefault(){throw Error('Do not steal modal Escape');}});check(f.bell.open);check(f.focus()===1);
f.setDialog(false);f.handlers.click({target:f.inside});check(f.bell.open);
f.handlers.click({target:f.summary});check(f.bell.open);
f.handlers.click({target:{}});check(!f.bell.open);check(f.focus()===1);
f.handlers.keydown({key:'Escape',preventDefault(){throw Error('Closed bell must not consume Escape');}});check(f.focus()===1);
console.log('PASS notification bell: '+checks+' assertions');
