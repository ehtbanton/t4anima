import {createReadStream} from 'node:fs';
import {readFile,writeFile,mkdir,readdir} from 'node:fs/promises';
import {createInterface} from 'node:readline';
import {gzipSync} from 'node:zlib';
const base='../nhs-sim-t4-2026-09-12T11-47-20-818Z';
async function* rows(f){for await(const l of createInterface({input:createReadStream(`${base}/${f}`),crlfDelay:Infinity}))if(l)yield JSON.parse(l);}
const people=new Map(),seen=new Set();
for await(const p of rows('patients.ndjson'))people.set(p.id,{...p,records:[],flags:[]});
for(const f of (await readdir(base)).filter(f=>/^resources-.*ndjson$/.test(f)).sort())for await(const r of rows(f)){
 if(seen.has(r.id))continue;seen.add(r.id);const p=people.get(r.patientId);if(!p)continue;
 p.records.push({id:r.id,kind:r.kind,title:r.title,status:r.status,priority:r.priority,owner:r.owner,visibleTo:r.visibleTo,createdAt:r.createdAt,dueAt:r.dueAt,version:r.version,data:r.data,author:r.provenance?.created?.actor?.name||'Unknown author'});
}
const clock=JSON.parse(await readFile(`${base}/clock-start.json`,'utf8'));const index=[],chunks=new Map();
for(const p of people.values()){
 const ehr=p.records.find(r=>r.kind==='ehr-record')?.data||{};
 const add=(id,title,detail,weight,category,evidence)=>p.flags.push({id:`${p.id}-${id}`,title,detail,weight,category,evidence});
 const terms=p.conditions.filter(c=>(ehr.problems||[]).some(x=>x.term.toLowerCase()===c.toLowerCase()&&x.status==='resolved')&&!(ehr.problems||[]).some(x=>x.term.toLowerCase()===c.toLowerCase()&&x.status==='active'));
 if(terms.length)add('condition','Condition status needs reconciliation',`${terms.join(', ')} appears in the patient summary but only as resolved in the problem history. This is a record disagreement, not a confirmed diagnostic error.`,20,'Record conflict',p.records.filter(r=>r.kind==='ehr-record').map(r=>r.id));
 for(const r of p.records.filter(r=>r.kind==='task'&&!['completed','cancelled'].includes(r.status)))add(r.id,r.title,r.dueAt<clock.now?'The recorded follow-up is overdue at the export time. Confirm whether it has been completed elsewhere.':'An open follow-up requires a review owner.',r.priority==='urgent'?30:20,'Follow-up',[r.id]);
 for(const r of p.records.filter(r=>r.kind==='referral'&&r.status==='rejected'))add(r.id,'Referral rejected — evidence missing',r.data.reason||r.title,30,'Care coordination',[r.id,...p.records.filter(x=>x.kind==='report'&&x.data.text).map(x=>x.id)]);
 const letters=p.records.filter(r=>r.kind==='discharge-summary'&&r.data.stage==='sent');
 if(letters.length)add('letter','Discharge correspondence awaits review',`${letters.length} sent letter${letters.length>1?'s':''} with no recorded review stage. Check requested actions and medicines reconciliation.`,20,'Correspondence',letters.map(r=>r.id));
 const scripts=p.records.filter(r=>r.kind==='prescription'&&r.status==='approved');
 if(scripts.length)add('medicine','Discharge medicine handover incomplete','A prescription is approved, but dispensing or collection is not recorded. Confirm the current medicines list and supply status.',25,'Medicines',scripts.map(r=>r.id));
 const care=p.records.filter(r=>['care-plan','care-package'].includes(r.kind)&&['open','waiting'].includes(r.status));
 if(care.length)add('care','Home support is not confirmed','Support remains open or awaiting allocation. Confirm access, capacity and the responsible team.',25,'Care coordination',care.map(r=>r.id));
 p.score=Math.min(100,p.flags.reduce((s,f)=>s+f.weight,0));p.age=Math.floor((clock.now-Date.parse(p.birthDate))/(365.2425*86400000));p.sources=[...new Set(p.records.map(r=>r.owner))];
 index.push({id:p.id,name:p.name,age:p.age,conditions:p.conditions,needs:p.needs,score:p.score,flags:p.flags,sourceCount:p.sources.length});
 const n=Math.floor((Number(p.id.slice(4))-1)/100);if(!chunks.has(n))chunks.set(n,{});chunks.get(n)[p.id]=p;
}
await mkdir('public/data/patients',{recursive:true});await writeFile('public/data/index.bin',gzipSync(JSON.stringify(index)));
await writeFile('public/data/meta.json',JSON.stringify({patients:people.size,resources:seen.size,capturedAt:'2026-09-12T11:47:20Z',simulationTime:clock.now,reviewPatients:index.filter(p=>p.score>0).length,highPriority:index.filter(p=>p.score>=50).length,flags:index.reduce((n,p)=>n+p.flags.length,0)}));
for(const [n,data]of chunks)await writeFile(`public/data/patients/${n}.bin`,gzipSync(JSON.stringify(data)));console.log('Prepared',index.length,'patients in',chunks.size,'compressed chunks');
