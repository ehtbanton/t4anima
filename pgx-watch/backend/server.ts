import express from 'express';
import {z} from 'zod';
import {config} from './config.js';
import {list,get,put,events,locked,audit,db} from './store.js';
import {rules} from './rules.js';
import {simulator} from './simulator.js';
import {runScan,executeFinding,retryFinding,type Run} from './engine.js';
const app=express();app.use(express.json({limit:'64kb'}));
app.use((req,res,next)=>{
 const host=req.hostname;
 if(!['localhost','127.0.0.1','::1'].includes(host)&&!config.token)return res.status(403).json({error:'Remote access requires APP_TOKEN'});
 if(config.token&&req.headers.authorization!==`Bearer ${config.token}`)return res.status(401).json({error:'Authentication required'});
 const origin=req.headers.origin;
 if(origin&&!['http://localhost:3000','http://127.0.0.1:3000','http://localhost:3100'].includes(origin))return res.status(403).json({error:'Origin not allowed'});
 next();
});
let busy=false;
function launch(fn:()=>Promise<unknown>){if(busy)throw new Error('A workflow is already running');busy=true;void fn().catch(e=>audit('workflow.failed','system',{error:e.message})).finally(()=>{busy=false;});}
app.get('/api/state',async(_req,res)=>{
 const settings=get('settings','automation')||{pollEnabled:false,intervalMinutes:60,scheduledEnabled:false,scope:'documents',execute:false};
 res.json({findings:list('finding'),runs:list('run').slice(0,50),actions:list('outbox'),rules,events:events(),settings,busy,model:config.model,simulator:config.simulator,connection:get('meta','connection')||null,patientsScanned:list('patient').length});
});
app.get('/api/health',async(_req,res)=>{const [team,clock]=await Promise.all([simulator.request('/api/team'),simulator.request('/api/clock')]);const c={connected:true,team:team.team,scopes:team.scopes,now:clock.now,checkedAt:new Date().toISOString()};put('meta','connection',c);res.json(c);});
app.get('/api/findings/:id',async(req,res)=>{const finding=get('finding',req.params.id);if(!finding)return res.status(404).json({error:'Not found'});res.json({finding,patient:get('patient',finding.patientId),events:events(finding.id),actions:list('outbox').filter(x=>x.findingId===finding.id)});});
app.post('/api/scans',(req,res)=>{const body=z.object({scope:z.enum(['sample','documents','all','patient']).default('documents'),execute:z.boolean().default(false),patientId:z.string().regex(/^SIM-\d{6}$/).optional()}).parse(req.body);if(body.scope==='patient'&&!body.patientId)return res.status(400).json({error:'patientId required'});launch(()=>runScan(body.scope,body.execute,body.patientId?[body.patientId]:undefined));res.status(202).json({queued:true});});
app.post('/api/runs/:id/resume',(req,res)=>{const run=get<Run>('run',req.params.id);if(!run||run.scope!=='all')return res.status(400).json({error:'Only full scans can resume'});launch(()=>runScan('all',run.execute,undefined,'resume',run));res.status(202).json({queued:true});});
app.post('/api/findings/:id/execute',(req,res)=>{launch(()=>executeFinding(req.params.id));res.status(202).json({queued:true});});
app.post('/api/findings/:id/review',(req,res)=>{const body=z.object({execute:z.boolean().default(false)}).parse(req.body);launch(()=>retryFinding(req.params.id,body.execute));res.status(202).json({queued:true});});
app.post('/api/settings',(req,res)=>{const value=z.object({pollEnabled:z.boolean(),scheduledEnabled:z.boolean(),intervalMinutes:z.number().int().min(1).max(1440),scope:z.enum(['documents','all','sample']),execute:z.boolean()}).parse(req.body);put('settings','automation',value);audit('settings.changed','system',value);res.json(value);});
app.post('/api/events',(req,res)=>{const b=z.object({id:z.string().min(1).max(200),patientId:z.string().regex(/^SIM-\d{6}$/)}).parse(req.body);if(get('external-event',b.id))return res.json({duplicate:true});launch(async()=>{const run=await runScan('patient',get('settings','automation')?.execute||false,[b.patientId],'event');if(run.status==='completed')put('external-event',b.id,{runId:run.id});});res.status(202).json({queued:true});});
app.use((error:any,_req:express.Request,res:express.Response,_next:express.NextFunction)=>{res.status(error instanceof z.ZodError?400:409).json({error:error instanceof z.ZodError?'Invalid request':String(error.message)});});
// Recover interrupted jobs visibly. Full scans keep page cursors; uncertain writes keep their original request IDs.
const activeLease=db.prepare('SELECT 1 FROM locks WHERE name=? AND expires>?').get('scan',Date.now());
for(const run of list<Run>('run').filter(r=>r.status==='running'&&!activeLease)){run.status='interrupted';put('run',run.id,run);}
let ticking=false;
setInterval(async()=>{
 if(ticking||busy)return;ticking=true;
 try{
  const s=get('settings','automation');if(!s)return;
  if(s.pollEnabled){
   const c=await simulator.request('/api/clock');const unseen=c.events.filter((e:any)=>!get('sim-event',e.id));
   const previous=get('meta','last-event');
   if(previous&&!c.events.some((e:any)=>e.id===previous.id)){audit('events.possible_gap','system',{message:'Recent event window was exceeded; document reconciliation requested'});await runScan('documents',s.execute,undefined,'event-reconciliation');}
   const ownResourceIds=new Set(list('outbox').flatMap(x=>[x.response?.id,x.response?.resource?.id,x.response?.resourceId]).filter(Boolean));
   const ids=[...new Set<string>(unseen.filter((e:any)=>e.patientId&&!ownResourceIds.has(e.resourceId)).map((e:any)=>e.patientId))];
   if(ids.length){const run=await runScan('patient',s.execute,ids,'simulator-event');if(run.status!=='completed')throw new Error('Event processing incomplete; will retry');}
   for(const e of unseen)put('sim-event',e.id,{id:e.id});if(c.events[0])put('meta','last-event',{id:c.events[0].id});
  }
  const last=get('meta','last-schedule');
  if(s.scheduledEnabled&&Date.now()-(last?.time||0)>=s.intervalMinutes*60000){const run=await runScan(s.scope,s.execute,undefined,'schedule');if(run.status==='completed')put('meta','last-schedule',{time:Date.now()});}
 }catch(e){audit('automation.failed','system',{error:(e as Error).message});}finally{ticking=false;}
},30000).unref();
app.listen(config.port,'127.0.0.1',()=>console.log(`PGx Watch backend: http://localhost:${config.port}`));
