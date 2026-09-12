import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {gunzipSync} from 'node:zlib';
const patients=JSON.parse(gunzipSync(await readFile('public/data/index.bin')).toString());
assert.equal(patients.length,50000);
assert.equal(new Set(patients.map(p=>p.id)).size,50000);
for(const p of patients){assert.equal(p.score,Math.min(100,p.flags.reduce((n,f)=>n+f.weight,0)));}
const sample=JSON.parse(gunzipSync(await readFile('public/data/patients/0.bin')).toString());
for(const p of Object.values(sample)){for(const flag of p.flags)for(const id of flag.evidence)assert(p.records.some(r=>r.id===id),`Missing evidence ${id}`);}
assert(sample['SIM-000001'].flags.some(f=>f.category==='Medicines'));
assert(sample['SIM-000002'].flags.some(f=>f.category==='Care coordination'));
assert(sample['SIM-000006'].flags.some(f=>f.title==='Home support is not confirmed'));
const base='http://localhost:3000';
let r=await fetch(base+'/api/reviews');assert.equal(r.status,200);assert(Array.isArray(await r.json()));
r=await fetch(base+'/api/reviews',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:'qa-review',patientId:'SIM-000001',status:'resolved',note:'',assignee:'QA',due:''})});assert.equal(r.status,400);
r=await fetch(base+'/api/reviews',{method:'POST',headers:{'Content-Type':'application/json',Origin:'https://untrusted.example'},body:'{}'});assert.equal(r.status,403);
const payload={id:'qa-local-verification',patientId:'SIM-000001',status:'reviewed',note:'Local verification fixture — not a clinical decision.',assignee:'QA local test',due:'2026-09-20'};
r=await fetch(base+'/api/reviews',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});assert.equal(r.status,200);
const saved=await(await fetch(base+'/api/reviews')).json();assert.equal(saved.find(x=>x.id===payload.id).note,payload.note);
payload.status='resolved';payload.note='Local persistence verification complete.';
r=await fetch(base+'/api/reviews',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});assert.equal(r.status,200);
const updated=await(await fetch(base+'/api/reviews')).json();assert.equal(updated.filter(x=>x.id===payload.id).length,1);assert.equal(updated.find(x=>x.id===payload.id).status,'resolved');
console.log('PASS: 50,000 unique patients; priority arithmetic; source links; seed journeys; review validation; origin checks; durable create/update/read.');
