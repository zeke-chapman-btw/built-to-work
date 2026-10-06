const assert=require('node:assert/strict');
const {Round,position,pick}=require('./logic.js');
const rules={width:1920,height:1080,duration_ms:45000};
const base={id:0,start:0,end:7000,flight:7000,size:160,direction:1,y0:.4,y1:.4,wave:0,points:25};
const snapshot={rules,targets:[base,{...base,id:1}]};
const p=position(base,3500,rules),round=new Round(snapshot);
assert.equal(pick(snapshot.targets,3500,p.x,p.y,new Set(),rules).id,1);
round.trigger(3500,p.x,p.y);assert.equal(round.kills,1);assert.equal(round.score,25);
round.trigger(3520,p.x,p.y);assert.equal(round.kills,2);
round.trigger(3540,p.x,p.y);assert.equal(round.kills,2);assert.equal(round.shots.length,3);
round.trigger(45000,p.x,p.y);assert.equal(round.shots.length,3);
round.trigger(-1,p.x,p.y);assert.equal(round.shots.length,3);
const miss=new Round(snapshot);miss.trigger(4000,5,1050);assert.equal(miss.score,0);assert.equal(miss.shots.length,1);
for(const direction of [-1,1]){const t={...base,direction};const pos=position(t,2600,rules);assert.equal(pick([t],2600,pos.x,pos.y,new Set(),rules).id,0);}
console.log('Duck Hunt deterministic JavaScript checks passed: overlap, single-hit, duplicates, timing, misses, direction.');

const rapid=new Round({rules,targets:[]});
rapid.trigger(1,1,1);rapid.trigger(2,1,1);assert.equal(rapid.shots.length,2);
rapid.trigger(44999.9999,1,1);assert.ok(rapid.shots[2].t<45000);
rapid.trigger(NaN,1,1);assert.equal(rapid.shots.length,3);
console.log('Rapid primary triggers, deadline rounding and invalid-input checks passed.');
