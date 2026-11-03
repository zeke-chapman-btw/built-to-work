const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const handlers={}, classNames=[];
let navigated='',focused=0;
const group={tagName:'DETAILS',open:false,parentElement:null};
const invalid={parentElement:group};
const error={parentElement:group};
const summary={focus(){focused++;}};
const form={querySelector:()=>summary,querySelectorAll:()=>[error],addEventListener:(name,fn)=>handlers[name]=fn};
const select={selectedOptions:[{value:'/customer/workspace/needs/'}],addEventListener:(name,fn)=>handlers.choose=fn};
const document={body:{classList:{add:value=>classNames.push(value)}},querySelectorAll:selector=>
  selector==='[data-section-chooser]'?[select]:selector==='[data-structured-form]'?[form]:[],getElementById:()=>invalid};
vm.runInNewContext(fs.readFileSync('static/shared/platform.js','utf8'),{document,window:{location:{assign:value=>navigated=value}}});
assert.equal(group.open,true,'server errors must reveal their containing disclosure');
assert.equal(focused,1,'error summary receives keyboard focus');
group.open=false;handlers.invalid({target:invalid});assert.equal(group.open,true,'native validation reveals collapsed groups');
handlers.choose();assert.equal(navigated,'/customer/workspace/needs/','native chooser uses its server-rendered destination');
assert.ok(classNames.includes('btw-platform-enhanced'),'no-JS navigation remains a progressive fallback');
console.log('Platform presentation interactions passed');

// Coordinated UX behavior tests: scope, focus, state and meaningful edit loss.
{
 const windowEvents={}, formEvents={}, selectEvents={}, leaveEvents={};
 let values=[['title','Original']], confirmResult=false, confirms=0, prevented=0, groupFocused=0;
 const link={hash:'#hiring-group-1',attrs:{},setAttribute(k,v){this.attrs[k]=v;},removeAttribute(k){delete this.attrs[k];}};
 const heading={tagName:'SUMMARY',parentElement:null,scrollIntoView(){},focus(){groupFocused++;}};
 const section={id:'hiring-group-1',tagName:'DETAILS',open:false,parentElement:null,querySelector(){return heading;},hasAttribute(){return true;},addEventListener(){}};
 heading.parentElement=section;
 const chooser={value:'',addEventListener(k,v){selectEvents[k]=v;}};
 const sectionForm={querySelector(){return chooser;},querySelectorAll(s){return s==='.customer-form-group'?[section]:[link];},addEventListener(){},contains(){return true;}};
 const guardedForm={addEventListener(k,v){formEvents[k]=v;},querySelectorAll(){return [{addEventListener(k,v){leaveEvents[k]=v;}}];}};
 const testDocument={body:{classList:{add(){}}},getElementById(){return section;},querySelectorAll(s){return s==='[data-sectioned-form]'?[sectionForm]:s==='form[data-unsaved-guard]'?[guardedForm]:[];}};
 class Data { [Symbol.iterator](){return values[Symbol.iterator]();} }
 const testWindow={addEventListener(k,v){windowEvents[k]=v;},confirm(){confirms++;return confirmResult;}};
 vm.runInNewContext(fs.readFileSync('static/shared/platform.js','utf8'),{document:testDocument,window:testWindow,FormData:Data});
 assert.equal(chooser.value,'#hiring-group-1');assert.equal(link.attrs['aria-current'],'step');
 selectEvents.change();assert.equal(section.open,true);assert.equal(groupFocused,1,'chooser opens and focuses its summary');
 chooser.value='https://outside.example/';selectEvents.change();assert.equal(groupFocused,1,'section chooser never navigates to external destinations');
 const event={preventDefault(){prevented++;}};
 leaveEvents.click(event);assert.equal(confirms,0,'unchanged forms leave freely');
 values=[['title','Edited']];leaveEvents.click(event);assert.equal(confirms,1);assert.equal(prevented,1,'cancelled leave preserves edited form');
 windowEvents.beforeunload(event);assert.equal(prevented,2,'browser back/close guards meaningful changes');
 confirmResult=true;leaveEvents.click(event);windowEvents.beforeunload(event);assert.equal(prevented,2,'confirmed leave avoids a second warning');
 windowEvents.pageshow();formEvents.submit();windowEvents.beforeunload(event);assert.equal(prevented,2,'successful submission bypasses loss guard');
 console.log('Coordinated UX chooser and unsaved-change behavior passed');
}
