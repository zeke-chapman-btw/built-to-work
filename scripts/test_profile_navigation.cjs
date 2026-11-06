'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('static/crm/profile_navigation.js','utf8');
let checks=0;
function run(hash, href, ready='complete') {
 const replaced=[], handlers={};
 const window={location:{hash,href:'https://review.example/participants/staff/directory/1/',origin:'https://review.example',replace:x=>replaced.push(x)},addEventListener:(k,f)=>handlers[k]=f};
 const document={readyState:ready,getElementById:id=>id===hash.slice(1)&&href?{querySelector:()=>({href})}:null,addEventListener:(k,f)=>handlers[k]=f};
 vm.runInNewContext(source,{window,document,URL});return {replaced,handlers};
}
for(const [hash,area,view]of [['btw-interactions','relationship',''],['employer-interactions','relationship','&crm_view=employer'],['relationship-followups','relationship','&crm_view=followups'],['profile-recruiting','recruiting','']]){
 const href='https://review.example/participants/staff/directory/1/?section='+area+view+'#'+hash;
 assert.deepEqual(run('#'+hash,href).replaced,[href]);checks++;
 assert.equal(run('#'+hash,null).replaced.length,0);checks++;
}
assert.equal(run('#profile-identity','https://review.example/x').replaced.length,0);checks++;
assert.equal(run('#btw-interactions','https://other.example/steal').replaced.length,0);checks++;
const late=run('#btw-interactions','https://review.example/participants/staff/directory/1/?section=relationship','loading');assert.equal(late.replaced.length,0);late.handlers.DOMContentLoaded();assert.equal(late.replaced.length,1);checks++;
console.log(checks+' legacy Profile navigation checks passed');
