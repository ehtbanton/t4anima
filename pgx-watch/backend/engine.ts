import {randomUUID} from 'node:crypto';
import {simulator} from './simulator.js';
import {rules,samplePatients} from './rules.js';
import {extract,review} from './agents.js';
import {get,put,list,audit,hash,locked} from './store.js';
import {isTest,isOwn,relevant,pgxPattern,matchRule,planActions,assertAllowed} from './policy.js';
import type {Finding,Patient,Resource} from './types.js';
export interface Run {id:string; trigger:string; scope:string; status:string; startedAt:string; finishedAt?:string; total:number; scanned:number; findings:number; errors:{patientId:string;message:string}[]; phase:string; execute:boolean; cursor?:number}
const fingerprint=(patient:Patient,records:Resource[])=>hash({patient:{...patient,conditions:patient.conditions.filter(c=>!c.includes('[PGx Watch]'))},records:records.filter(r=>relevant(r)&&!isOwn(r)).map(r=>({id:r.id,version:r.version,status:r.status,data:r.data,title:r.title})).sort((a,b)=>a.id.localeCompare(b.id))});
async function ensureWorld(){
 const team=await simulator.request('/api/team');const identity=hash(team.world);const bound=get('meta','world');
 if(bound&&bound.identity!==identity)throw new Error('This database belongs to a different simulator world. Use a separate DATA_DIR.');
 if(!bound)put('meta','world',{identity});
}
export async function scanPatient(id:string,run:Run){
 const [patient,records]=await Promise.all([simulator.patient(id),simulator.records(id)]);
 const currentHash=fingerprint(patient,records);put('patient',id,{...patient,lastScannedAt:new Date().toISOString()});
 const candidate=records.some(r=>!isOwn(r)&&!isTest(r)&&r.kind!=='genome-record'&&pgxPattern.test(JSON.stringify(r)));
 const cached=get('extraction',id);
 const extraction=cached?.extractorVersion===3&&cached?.snapshotHash===currentHash?cached:!candidate?{facts:[],warnings:['No interpreted PGx evidence; raw SNP panels are not assigned a diplotype.'],snapshotHash:currentHash}:await extract(patient,records).then(x=>({...x,snapshotHash:currentHash}));
 put('extraction',id,{...extraction,extractorVersion:3});
 const proposals:{rule:typeof rules[number];facts:any[];warnings:string[]}[]=[];
 for(const rule of rules){
  const facts=extraction.facts.filter((f:any)=>matchRule(f,rule));if(facts.length)proposals.push({rule,facts,warnings:extraction.warnings});
 }
 if(records.some(isTest))proposals.push({rule:rules.find(r=>r.id==='exclude-test')!,facts:[],warnings:['WRITE TEST evidence excluded; all other patient evidence evaluated normally.']});
 const foundIds=new Set<string>();
 for(const {rule,facts,warnings} of proposals){
  const episodeIds=[...new Set(facts.flatMap(f=>f.evidence).map((e:any)=>e.resourceId))].sort();
  const existingEpisode=list<Finding>('finding').find(f=>f.patientId===id&&f.ruleId===rule.id&&(rule.id==='exclude-test'||f.evidence.some(e=>episodeIds.includes(e.resourceId))));
  const findingId=existingEpisode?.id||hash({patient:id,rule:hash(rule),episodeIds}).slice(0,24);
  foundIds.add(findingId);
  const previous=get<Finding>('finding',findingId);
  if(previous?.snapshotHash===currentHash&&['executed','denied','deferred','excluded','superseded'].includes(previous.status))continue;
  if(previous?.status==='executed') {audit('finding.rechecked',findingId,{runId:run.id,snapshotHash:currentHash});continue;}
  const time=new Date().toISOString();
  const finding:Finding={id:findingId,patientId:id,patientName:patient.name,ruleId:rule.id,code:rule.code,title:`${rule.gene} · ${rule.result}`,action:rule.action,specialist:rule.specialist,severity:rule.severity,source:rule.source,evidence:facts.flatMap(f=>f.evidence),facts,snapshotHash:currentHash,status:rule.id==='exclude-test'?'excluded':'pending',createdAt:previous?.createdAt||time,updatedAt:time,runId:run.id,warnings};
  put('finding',findingId,finding);audit('finding.detected',findingId,{rule:rule.id,patientId:id,runId:run.id,evidence:finding.evidence});
  if(finding.status==='excluded')continue;
  if(run.scope==='sample-preview'){finding.status='preview';put('finding',findingId,finding);continue;}
  const clock=await simulator.request('/api/clock');
  finding.decision=await review(finding,patient,records,clock.now,planActions(finding));
  finding.status=({accept:'accepted',deny:'denied',defer:'deferred'} as const)[finding.decision.decision];
  finding.updatedAt=new Date().toISOString();put('finding',findingId,finding);audit('gp.decision',findingId,finding.decision);
  if(run.execute&&finding.status==='accepted')await executeFinding(findingId);
 }
 for(const old of list<Finding>('finding').filter(f=>f.patientId===id&&!foundIds.has(f.id)&&!['executed','superseded'].includes(f.status))){old.status='superseded';old.updatedAt=new Date().toISOString();put('finding',old.id,old);audit('finding.superseded',old.id,{runId:run.id});}
}
export async function executeFinding(id:string){return locked('execute:'+id,async()=>{
 await ensureWorld();
 const f=get<Finding>('finding',id);if(!f)throw new Error('Finding not found');if(f.status==='executed')return f;
 if(!['accepted','execution_failed','executing'].includes(f.status))throw new Error('Finding is not accepted for execution');
 const actions=planActions(f);assertAllowed(f,actions);
 const [patient,records,clock]=await Promise.all([simulator.patient(f.patientId),simulator.records(f.patientId),simulator.request('/api/clock')]);
 if(patient.death&&Date.parse(patient.death.date)<=clock.now)throw new Error('Patient is recorded deceased; rescan required');
 if(fingerprint(patient,records)!==f.snapshotHash){f.status='stale';put('finding',id,f);throw new Error('Clinical record changed after review; rescan required');}
 f.status='executing';put('finding',id,f);
 try{
  for(let i=0;i<actions.length;i++){
   const key=`${id}:${i}`;let entry=get('outbox',key);
   if(entry?.status==='completed')continue;
   if(!entry){entry={id:key,requestId:randomUUID(),findingId:id,body:actions[i],status:'pending',createdAt:new Date().toISOString()};put('outbox',key,entry);}
   if(JSON.stringify(entry.body)!==JSON.stringify(actions[i]))throw new Error('Persisted action plan differs; manual reconciliation required');
   audit('action.requested',id,{requestId:entry.requestId,body:entry.body});
   const response=await simulator.action(entry.body,entry.requestId);
   entry={...entry,status:'completed',response,completedAt:new Date().toISOString()};put('outbox',key,entry);audit('action.completed',id,{requestId:entry.requestId,response});
  }
  f.status='executed';f.updatedAt=new Date().toISOString();put('finding',id,f);return f;
 }catch(e){f.status='execution_failed';f.updatedAt=new Date().toISOString();put('finding',id,f);audit('action.failed',id,{error:(e as Error).message});throw e;}
});}
export async function runScan(scope:string,execute=false,patientIds?:string[],trigger='manual',resume?:Run){return locked('scan',async()=>{
 await ensureWorld();
 const run:Run=resume||{id:randomUUID(),trigger,scope,status:'running',startedAt:new Date().toISOString(),total:0,scanned:0,findings:0,errors:[],phase:'Discovering patients',execute};
 run.status='running';put('run',run.id,run);audit('scan.started',run.id,{scope,trigger,execute});
 try{
  if(scope==='all') {
   let offset=run.cursor||0;
   if(resume){const retries=[...new Set(run.errors.map(e=>e.patientId).filter(Boolean))];run.errors=[];run.scanned-=retries.length;for(const id of retries)await processPatient(id,run);}
   for(;;){
    const page=await simulator.request(`/api/sites/gp/patients?offset=${offset}`);run.total=page.total;
    if(!page.items.length&&offset<page.total)throw new Error('Incomplete patient directory pagination');
    for(const patient of page.items)await processPatient(patient.id,run);
    offset+=page.items.length;run.cursor=offset;put('run',run.id,run);
    if(offset>=page.total)break;
   }
  }else {
   let ids=patientIds;
   if(!ids&&scope.startsWith('sample'))ids=samplePatients;
   if(!ids){const docs=await simulator.request('/api/sites/gp/documents');ids=[...new Set<string>(docs.resources.filter((r:Resource)=>pgxPattern.test(JSON.stringify(r))).map((r:Resource)=>r.patientId!))];}
   run.total=ids.length;put('run',run.id,run);
   // One patient at a time keeps resource/API usage bounded and decisions auditable.
   for(const id of ids)await processPatient(id,run);
  }
  run.status=run.errors.length?'completed_with_errors':'completed';run.phase='Finished';
 }catch(e){run.status='failed';run.errors.push({patientId:'',message:(e as Error).message});}
 run.finishedAt=new Date().toISOString();run.findings=list<Finding>('finding').filter(f=>f.runId===run.id).length;put('run',run.id,run);audit('scan.finished',run.id,run);return run;
});}
async function processPatient(id:string,run:Run){
 run.phase=`Reviewing ${id}`;put('run',run.id,run);
 try{await scanPatient(id,run);}catch(e){run.errors.push({patientId:id,message:(e as Error).message});audit('patient.failed',id,{runId:run.id,error:(e as Error).message});}
 run.scanned++;run.findings=list<Finding>('finding').filter(f=>f.runId===run.id).length;put('run',run.id,run);
}
export async function retryFinding(id:string,execute:boolean){const f=get<Finding>('finding',id);if(!f)throw new Error('Finding not found');put('extraction',f.patientId,{});f.status='pending';put('finding',id,f);return runScan('patient',execute,[f.patientId]);}
