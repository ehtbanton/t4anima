import type {Resource,Fact,Rule,Finding,SimAction} from './types.js';
export const pgxPattern=/CYP2C19|DPYD|TPMT|HLA-B\s*\*?57:01|rs4244285/i;
export const isTest=(r:Resource)=>/\bWRITE TEST\b/i.test(r.title);
export const isOwn=(r:Resource)=>/\[PGx Watch\]/.test(r.title)||JSON.stringify(r.data).includes('[PGx Watch]');
export const relevant=(r:Resource)=>!isOwn(r)&&(['ehr-record','prescription','problem','allergy','discharge-summary','document','genome-record','consultation','hospital-note'].includes(r.kind)||pgxPattern.test(JSON.stringify(r.data)));
function strings(x:unknown):string[]{if(typeof x==='string')return [x];if(Array.isArray(x))return x.flatMap(strings);if(x&&typeof x==='object')return Object.values(x).flatMap(strings);return [];}
export function evidenceValid(fact:Pick<Fact,'evidence'>,records:Resource[]) {
 return fact.evidence.length>0&&fact.evidence.every(e=>{const r=records.find(r=>r.id===e.resourceId&&r.version===e.version);return r&&!isTest(r)&&e.quote.trim().length>0&&strings(r).some(s=>s.replace(/\s+/g,' ').includes(e.quote.replace(/\s+/g,' ')));});
}
const norm=(s:string)=>s.toLowerCase().replace(/\s/g,'');
export function matchRule(f:Fact,r:Rule):boolean {
 if(norm(f.gene)!==norm(r.gene))return false;
 if(r.id==='exclude-test')return false;
 if(r.id==='cyp-family')return f.subject==='family';
 if(f.subject!=='patient')return false;
 if(r.id==='cyp-pending')return f.status==='pending';
 if(f.status!=='confirmed'&&r.id!=='cyp-variant')return false;
 if(!norm(f.drug).includes(r.drug.split(' / ')[0])&&!(r.id==='dpyd-partial'&&/fluorouracil/i.test(f.drug)))return false;
 if(r.context==='stroke/TIA'||r.context==='TIA') {if(!/stroke|TIA|ischaemic attack/i.test(f.context))return false;}
 if(r.context==='ACS/PCI'&&!/ACS|PCI|NSTEMI|STEMI|coronary|stent/i.test(f.context))return false;
 if(r.id==='tpmt-poor'&&!/crohn/i.test(f.context))return false;
 const v=norm(f.result);
 if(r.id==='cyp-variant')return /rs4244285/i.test(f.result)&&/homozygous|A\/A/i.test(f.result);
 if(r.id==='dpyd-partial')return v.includes('c.2846a>t')&&v.includes('heterozygous');
 return v===norm(r.result);
}
export function planActions(f:Finding):SimAction[] {
 if(f.code==='EXCLUDE_TEST_EVIDENCE')return [];
 const text=`[PGx Watch] ${f.action}\nEvidence: ${f.evidence.map(e=>`${e.resourceId} v${e.version}: ${e.quote}`).join('\n')}\nFinding: ${f.id}`;
 const base={patientId:f.patientId,text};
 if(['NO_PGX_CHANGE','NO_PGX_RESTRICTION'].includes(f.code))return [{...base,type:'save_consultation',title:`[PGx Watch] ${f.code}`,consultationStatus:'saved'}];
 const actions:SimAction[]=[{...base,type:'create_task',title:`[PGx Watch] ${f.severity==='high'?'URGENT: ':''}${f.specialist} — ${f.code}`}];
 if(!['CHASE_RESULT','NO_PATIENT_GENOTYPE'].includes(f.code))actions.push({...base,type:'create_referral',target:'hospital',title:`[PGx Watch] ${f.specialist}: ${f.code}`});
 if(f.code==='AVOID_DRUG')actions.push({...base,type:'save_problem',title:'[PGx Watch] PGx prescribing alert: HLA-B*57:01 positive — avoid abacavir',problemStatus:'active'});
 return actions;
}
export function assertAllowed(f:Finding,actions:SimAction[]) {
 if(f.decision?.decision!=='accept')throw new Error('GP acceptance is required');
 if(JSON.stringify(actions)!==JSON.stringify(planActions(f)))throw new Error('Execution does not match accepted policy plan');
 if(actions.some(a=>!['create_task','create_referral','save_problem','save_consultation'].includes(a.type)))throw new Error('Action is outside the sample policy');
}
