const fs=require('node:fs');const vm=require('node:vm');const assert=require('node:assert/strict');
const source=fs.readFileSync('static/trailer_control/display.js','utf8');
assert(!source.includes('innerHTML'),'Display updates must use text nodes');
assert(!source.includes('localStorage'),'No record persistence in TV storage');
assert(!source.includes('sessionStorage'),'No record persistence in TV storage');
assert(source.includes('response.status === 403'),'Revoked pairing clears stale rankings');
assert(source.includes('cache: "no-store"'),'Scores are fetched without an HTTP cache');
assert(source.includes('X-CSRFToken'),'Display heartbeats use CSRF protection');
assert(source.includes('Math.min(30000'),'Reconnect backoff is bounded');
assert(source.includes('video?.play()'),'Playback has a manual user-action retry');
new vm.Script(source);
new vm.Script(fs.readFileSync('static/trailer_control/control.js','utf8'));

class Element {
  constructor(tag) { this.tag=tag;this.children=[];this.textContent='';this.dataset={};this.classList={toggle(){}}; }
  append(x){this.children.push(x);}
  replaceChildren(){this.children=[];}
}
const nodes={board:new Element('section'),'board-group':new Element('p'),'display-status':new Element('p')};
const root=new Element('main');root.dataset={tv:'leaderboard',dataUrl:'/local/data',heartbeatUrl:'/local/heartbeat',event:'synthetic-event'};
const timers=[],requests=[];let mode='ok';
const malicious='<img src=x onerror=alert(1)>';
const document={querySelector(q){return q==='[data-tv]'?root:q==='[name=csrfmiddlewaretoken]'?{value:'synthetic-csrf'}:null;},getElementById(id){return nodes[id];},createElement(tag){return new Element(tag);}};
root.querySelector=()=>null;
const context=vm.createContext({document,crypto:{randomUUID:()=> 'synthetic-boot'},Date,Number,Math,console,
  fetch:async(url,options)=>{requests.push({url,options});return {ok:mode==='ok',status:mode==='revoked'?403:200,json:async()=>({groups:[{simulator:'A',profile:'v1',version:'1',rows:[{rank:1,alias:malicious,score:'90'}]}]})};},
  setTimeout:(fn,ms)=>{timers.push({fn,ms});},setInterval:()=>{}});
const flush=()=>new Promise(resolve=>setImmediate(resolve));
(async()=>{
  vm.runInContext(source,context);await flush();
  const body=nodes.board.children[0].children[1];
  assert.equal(body.children[0].children[1].textContent,malicious,'Alias must remain text, never parsed markup');
  assert.equal(requests.find(x=>x.url==='/local/data').options.cache,'no-store');
  const heartbeat=requests.find(x=>x.url==='/local/heartbeat');
  assert.equal(heartbeat.options.headers['X-CSRFToken'],'synthetic-csrf');
  assert.deepEqual(Object.keys(JSON.parse(heartbeat.options.body)).sort(),['boot_id','event_id','sequence','state']);
  mode='revoked';const next=timers.find(x=>x.ms===5000);await next.fn();await flush();
  assert(nodes.board.children.some(x=>x.textContent==='No published accepted scores yet.'),'Revoked session must clear prior scores');
  assert(nodes['display-status'].textContent.includes('Connection interrupted'));
  assert(timers.some(x=>x.ms===10000),'Reconnect delay increases after failure');
  console.log('PASS: display text rendering, restricted heartbeat, revocation clearing, reconnect behavior and source privacy guards');
})().catch(error=>{console.error(error);process.exitCode=1;});

async function verifyOfflineDisplay() {
  let clock=1000000,offline=false;
  class Clock extends Date { constructor(...args){super(...(args.length?args:[clock]));} static now(){return clock;} }
  const nodes={board:new Element('section'),'board-group':new Element('p'),'display-status':new Element('p')};
  const root=new Element('main');root.dataset={tv:'leaderboard',dataUrl:'/board',heartbeatUrl:'/heartbeat',event:'synthetic'};root.querySelector=()=>null;
  const timers=[];
  const document={querySelector:q=>q==='[data-tv]'?root:null,getElementById:id=>nodes[id],createElement:tag=>new Element(tag)};
  const context=vm.createContext({document,crypto:{randomUUID:()=> 'offline-boot'},Date:Clock,Number,Math,
    fetch:async url=>{if(offline&&url==='/board')throw new Error('local server unavailable');return {ok:true,json:async()=>({groups:[{simulator:'A',profile:'Compatible',version:'1',rows:[{rank:1,alias:'Player RANDOM',score:'91'}]}]})};},
    setTimeout:(fn,ms)=>timers.push({fn,ms}),setInterval:()=>{}});
  vm.runInContext(source,context);await flush();
  const poll=timers.find(x=>x.ms===5000).fn;
  offline=true;clock+=60000;await poll();
  assert.equal(nodes.board.children[0].tag,'table','Brief interruption retains the last anonymous frame');
  assert(nodes['display-status'].textContent.includes('Connection interrupted'));
  clock+=300001;await poll();
  assert.equal(nodes.board.children[0].textContent,'No published accepted scores yet.','Expired offline frame is cleared');
  await poll();assert(timers.some(x=>x.ms===30000),'Offline reconnect backoff is capped');
  offline=false;await poll();assert.equal(nodes.board.children[0].tag,'table','Successful local reconnect restores rankings');
  assert(nodes['display-status'].textContent.includes('Local update'));
  console.log('PASS: offline interruption, stale-frame expiry, bounded retry and local reconnect');
}

async function verifyHealthRefresh() {
  const live={};for(const key of ['application','central','storage','storage-state','worker','communication','unknown','sample','refresh'])live[key]=new Element('span');
  const cells={};for(const key of ['status','seen','application','playback','activity'])cells[key]=new Element('span');
  const row={dataset:{device:'tv'},querySelector:q=>cells[q.slice(13,-1)]};
  const root={dataset:{controlStatus:'/status'}};let unavailable=false;
  const payload={fresh:true,server:{application:'Responding',database:'Responding',storage:'Available',free_bytes:1073741824,central:'Unavailable'},worker:'Recent',sampled_at:'2026-10-31T12:00:00Z',devices:[{id:'tv',communication:'Recent',application:'playing',playback_advancing:true,last_seen:'2026-10-31T12:00:00Z'},{id:'kiosk',communication:'Unknown'}]};
  const document={querySelector:q=>q==='[data-control-status]'?root:live[q.slice(12,-2)],querySelectorAll:()=>[row]};
  const timers=[];
  const context=vm.createContext({document,Date,Array,fetch:async()=>{if(unavailable)throw new Error('lost connection');return {ok:true,json:async()=>payload};},setTimeout:(fn,ms)=>timers.push({fn,ms})});
  vm.runInContext(fs.readFileSync('static/trailer_control/control.js','utf8'),context);await flush();
  assert.equal(live.unknown.textContent,1,'Unknown count refreshes with communication totals');
  assert.equal(cells.application.textContent,'playing');assert.equal(cells.playback.textContent,'advancing');
  payload.devices[0].application='error';payload.devices[0].playback_advancing=false;payload.worker='Unknown / stale';
  await timers[0].fn();assert.equal(cells.application.textContent,'error');assert.equal(cells.playback.textContent,'unconfirmed');assert.equal(live.worker.textContent,'Unknown / stale');
  unavailable=true;await timers[0].fn();
  assert.equal(live.communication.textContent,'Unknown / stale');assert.equal(cells.status.textContent,'Unknown / stale');
  assert.equal(cells.application.textContent,'Previous report; refresh unavailable');assert.equal(live.storage.textContent,'Unknown / stale');
  console.log('PASS: live counts, reported state, playback evidence and failure warnings stay consistent');
}

Promise.all([verifyOfflineDisplay(),verifyHealthRefresh()]).catch(error=>{console.error(error);process.exitCode=1;});
