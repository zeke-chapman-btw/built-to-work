const assert=require('node:assert/strict');
const fs=require('node:fs');const vm=require('node:vm');const path=require('node:path');
const script=fs.readFileSync(path.join(__dirname,'../static/accounts/login.js'),'utf8');
const handlers={},win={},attrs={};let valid=true,focused=false;
const button={textContent:'Sign in',disabled:false};
const form={querySelector:()=>button,addEventListener:(n,f)=>handlers[n]=f,
checkValidity:()=>valid,setAttribute:(n,v)=>attrs[n]=v,removeAttribute:n=>delete attrs[n]};
vm.runInNewContext(script,{document:{querySelectorAll:()=>[form],querySelector:()=>({focus:()=>focused=true})},window:{addEventListener:(n,f)=>win[n]=f}});
assert.equal(focused,true);let prevented=false;
valid=false;handlers.submit({preventDefault:()=>prevented=true});assert.equal(button.disabled,false);
valid=true;handlers.submit({preventDefault:()=>prevented=true});assert.equal(button.disabled,true);assert.equal(attrs['aria-busy'],'true');
handlers.submit({preventDefault:()=>prevented=true});assert.equal(prevented,true);
win.pageshow();assert.equal(button.disabled,false);assert.equal(button.textContent,'Sign in');assert.equal(attrs['aria-busy'],undefined);
form.dataset={submittingLabel:'Sending request…'};button.textContent='Send recovery instructions';handlers.submit({preventDefault:()=>{}});assert.equal(button.textContent,'Sending request…');win.pageshow();assert.equal(button.disabled,false);
assert.equal(script.includes('.value'),false);console.log('PASS unified login loading, duplicate-submit, error focus and Back/Forward restoration: 11 assertions');
