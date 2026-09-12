'use strict';
let state, selected = 'SIM-000011', tab = 'evidence', filter = 'all', busy = false, decisionAction;
const $ = s => document.querySelector(s);
const esc = value => String(value ?? '').replace(/[&<>"']/g, x => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[x]));
const labels = {unscreened:'Not screened', ready:'Ready for review', blocked:'Specialist review', no_alert:'No alert', applied:'Drafts created', rejected:'Rejected', escalated:'Escalated', incomplete:'Incomplete', needs_review:'Reconcile record'};
const badge = (status, label) => `<span class="badge ${esc(status)}">${esc(label || labels[status] || status)}</span>`;
const date = ms => new Date(ms).toLocaleDateString('en-GB',{day:'numeric',month:'short',year:'numeric',timeZone:'UTC'});
const current = () => state.patients.find(p => p.id === selected);
let toastTimer;
function toast(message, error=false) { clearTimeout(toastTimer); $('#toast').textContent=message; $('#toast').className=`show${error?' error':''}`; toastTimer=setTimeout(()=>$('#toast').className='',6500); }
async function action(name, extra={}) {
  if (busy) return false;
  busy=true; document.body.setAttribute('aria-busy','true');
  const controls = [...document.querySelectorAll('button')].filter(b=>!b.disabled);
  controls.forEach(b=>b.disabled=true);
  try {
    const response=await fetch('/api/action',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:name,patient_id:selected,...extra})});
    const result=await response.json();
    if (!response.ok) throw new Error(result.error || 'The action could not be completed.');
    state=result.state;
    if (name==='scan') tab='decision';
    if (['approve','reject','escalate'].includes(name)) tab='record';
    if (name==='reset') {selected='SIM-000011';tab='evidence';filter='all';$('#search').value='';}
    render();
    if(name==='scan') $('#patient-header').scrollIntoView({behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'auto':'smooth',block:'start'});
    toast(result.message); return true;
  } catch (error) {toast(error.message,true); return false;}
  finally {busy=false;document.body.removeAttribute('aria-busy');controls.filter(b=>b.isConnected).forEach(b=>b.disabled=false); updateControls();}
}
function updateControls(){
  if(!state) return;
  $('#scan').disabled=state.scanned || busy; $('#advance').disabled=!state.scanned || busy; $('#chase').disabled=!state.scanned || busy;
}
function render(){
  $('#scan').innerHTML=state.scanned?'✓ Cohort screened':'Screen 12 patient records <span>→</span>';
  $('#screen-note').textContent=state.scanned?'8 alerts raised. You decide what happens next.':'Real extraction & rules. Local demo records.';
  const pending=state.patients.filter(p=>['ready','blocked','incomplete','needs_review'].includes(p.status)).length;
  const completed=state.patients.filter(p=>['applied','rejected','escalated'].includes(p.status)).length;
  $('#metrics').innerHTML=[['12','Patient records','Synthetic demo cohort'],[state.scanned?'8':'—','Alerts identified','4 switches · 4 escalations'],[state.scanned?pending:'—','Needs attention','Named reviewer required'],[state.scanned?completed:'0','Decisions recorded','Every action leaves a trail']].map(([v,k,s])=>`<div class="metric"><b>${v}</b><span>${k}<small>${s}</small></span></div>`).join('');
  renderList();renderPatient();
  $('#hours').textContent=state.elapsed_hours;$('#sim-date').textContent=date(state.now)+' · demo time';
  $('#timeline').innerHTML=state.timeline.length?[...state.timeline].reverse().map(e=>`<div class="event"><p>${esc(e.text)}</p><small>${date(e.at)}${e.patient_id?' · '+esc(e.patient_id):''}</small></div>`).join(''):'<div class="event"><p>Your decisions will appear here.</p><small>Start by screening the patient cohort.</small></div>';
  updateControls();
}
function renderList(){
  const query=$('#search').value.toLowerCase();
  const list=state.patients.filter(p=>{
    const text=`${p.name} ${p.id} ${p.label} ${p.original.drug}`.toLowerCase();
    return text.includes(query) && (filter==='all'||filter==='action'&&['ready','blocked','incomplete','needs_review'].includes(p.status)||filter==='control'&&p.label.startsWith('CONTROL'));
  });
  document.querySelectorAll('[data-filter]').forEach(b=>{b.classList.toggle('active',b.dataset.filter===filter);b.setAttribute('aria-pressed',b.dataset.filter===filter);});
  $('#patients').innerHTML=list.length?list.map(p=>`<button class="patient-item ${p.id===selected?'selected':''}" data-patient="${p.id}" aria-pressed="${p.id===selected}"><div class="row"><strong>${esc(p.name)}</strong><span class="gene">${esc(p.id.slice(-3))}</span></div><div class="subtitle">${esc(p.label.replace('CONTROL ',''))}</div>${badge(p.status)}</button>`).join(''):'<p class="no-matches">No patients match this view.</p>';
}
function renderPatient(){
  const p=current();
  $('#patient-header').innerHTML=`<div class="patient-title"><div class="avatar" aria-hidden="true">${esc(p.name.split(' ').map(n=>n[0]).join(''))}</div><div><h2>${esc(p.name)}</h2><div class="id">${esc(p.id)} · Synthetic patient</div></div>${badge(p.status)}</div><p class="story">${esc(p.story)}</p>`;
  document.querySelectorAll('[data-tab]').forEach(b=>{b.setAttribute('aria-selected',b.dataset.tab===tab);b.tabIndex=b.dataset.tab===tab?0:-1;});
  $('#detail').setAttribute('aria-labelledby','tab-'+tab);
  $('#detail').innerHTML=tab==='evidence'?evidenceView(p):tab==='decision'?decisionView(p):recordView(p);
}
function highlighted(text,p){
  text=text.replace(/\s+/g,' ');
  const quotes=p.evidence.map(f=>f.evidence?.replace(/\s+/g,' ')).filter(Boolean).sort((a,b)=>b.length-a.length);
  const escaped=esc(text);
  const exact=quotes.find(q=>text.includes(q));
  if(exact) {
    const start=exact.search(/CYP\s*-?\s*2\s*[CD]\s*(?:19|6)|TPMT|DPYD|HLA\s*-?\s*B|rs\d+|c\.\d+/i);
    const focus=(start>=0?exact.slice(start):exact).split(/(?<=[.;])\s+(?=[A-Z])/).slice(0,2).join(' ');
    return escaped.replace(esc(focus),`<mark>${esc(focus)}</mark>`);
  }
  // Highlight only a text span that was actually returned by the extractor.
  return escaped;
}
function evidenceView(p){
  const mainKeys=p.id==='SIM-000012'?['course','medicationChanges']:['SIM-000013','SIM-000019'].includes(p.id)?['gpActions','medicationChanges']:['results','medicationChanges'];
  const sections=mainKeys.filter(k=>p.sections[k]);
  const names={results:'Investigations & results',medicationChanges:'Medication on discharge',course:'Clinical course',gpActions:'Actions for the GP',reason:'Reason for admission',diagnoses:'Diagnoses',followUp:'Follow-up'};
  const body=keys=>keys.map(k=>`<h4>${esc(names[k]||k)}</h4><p>${highlighted(p.sections[k],p)}</p>`).join('');
  const ph=p.phenotypes[0];
  let finding=ph?`<div class="evidence-strip"><span class="symbol">↳</span><div><strong>${esc(ph.display)}</strong><p>${esc(ph.diplotype||'Phenotype stated in narrative')} · ${esc(ph.derivation)}</p><p>Source: ${esc(p.evidence[0]?.source.section||'report')} · Rule-based extraction</p></div>${badge('applied','Extracted')}</div>`:state.scanned?`<div class="evidence-strip"><span class="symbol">✓</span><div><strong>No patient genotype asserted</strong><p>${p.id==='SIM-000018'?'The test is pending. No phenotype is coded.':'This is family history, not a result for this patient. No phenotype is coded.'}</p></div></div>`:'';
  return `<div class="source-meta"><span>HOSPITAL → PRIMARY CARE</span><span>Filed ${date(p.source.createdAt)} · 1 year before review</span></div><div class="letter"><div class="letter-top"><span class="doc-icon" aria-hidden="true">≡</span><strong>${esc(p.source.title)}</strong></div>${body(sections)}<details><summary>Read the full discharge summary</summary>${body(Object.keys(p.sections).filter(k=>!sections.includes(k)))}</details></div>${finding}<p class="source-provenance">Demo provenance: this letter and its genotype were authored for this scenario. It is not a discovered result from the original simulator dataset.</p><div class="detail-bottom"><p>${state.scanned?'The highlighted evidence is read by the same extractor used in the command-line pipeline.':'Screen the cohort to extract findings and check them against current medicines.'}</p><button class="primary" data-command="${state.scanned?'decision':'scan'}">${state.scanned?'Review the decision':'Screen patient cohort'} <span>→</span></button></div>`;
}
function checkRows(checks){return checks.map(c=>`<div class="check ${c.passed?'':'fail'}"><span class="check-icon">${c.passed?'✓':'!'}</span><div><strong>${esc(c.check)}${!c.passed?(c.blocking?' · blocking':' · caution'):''}</strong><p>${esc(c.detail)}</p></div></div>`).join('');}
function decisionView(p){
  if(!state.scanned) return `<div class="empty-state"><div class="empty-icon">⌕</div><h3>Start with the evidence.</h3><p>Screen the cohort to extract the reported results, store phenotypes, and run the prescribing rules. Nothing is approved automatically.</p><button class="primary" data-command="scan">Screen 12 patient records →</button></div>`;
  if(!p.proposal) return `<div class="empty-state"><div class="empty-icon">✓</div><h3>The right action is no alert.</h3><p>${esc(p.story)} ${p.phenotypes.length?'The result is stored, but no prescribing rule calls for a change.':'No phenotype has been coded and no prescribing decision is created.'}</p><button class="secondary" data-command="evidence">Inspect the source evidence</button></div>`;
  const prop=p.proposal, fresh=p.fresh_proposal, active=prop.status==='routed';
  if(!active) return `<div class="alert-banner ${['incomplete','needs_review'].includes(prop.status)?'blocked':''}"><h3>${esc(labels[prop.status]||prop.status)}</h3><p>${prop.status==='applied'?'Replacement drafts were created and the original prescription was cancelled in this local sandbox. The drafts still need an issue process.':prop.status==='rejected'?'The reviewer declined the proposed change. Medicines were left unchanged.':prop.status==='escalated'?'A specialist review task was created. Medicines were left unchanged.':'The change needs manual reconciliation. Review any drafts and the original medicine before proceeding.'}</p><p>Recorded by ${esc(prop.authorised_by||'the reviewer')}${prop.decision_note?' · '+esc(prop.decision_note):''}</p></div><button class="primary" data-command="record">Inspect the resulting record →</button>`;
  const shown=fresh||prop, checks=shown.safety||[], blocked=!!shown.blocked_by.length, alt=shown.proposed_regimen;
  const stale=p.whatif_allergy;
  return `<div class="alert-banner ${blocked?'blocked':''}"><h3>${blocked?'A human review is required.':'An actionable result meets a current medicine.'}</h3><p>${esc(shown.summary)}</p>${blocked?`<p><b>${esc(shown.blocked_by.join('; '))}</b></p>`:''}</div><div class="regimen"><div><div class="label">CURRENT PRESCRIPTION</div><strong>${esc(p.original.drug)}</strong><p>${esc(p.original.frequency)} · ${esc(p.original.indication)}</p></div><div><div class="label">${blocked?'REVIEW DIRECTION':'PROPOSED REPLACEMENT DRAFTS'}</div>${alt?alt.components.length?alt.components.map(c=>`<div class="component"><strong>${esc(c.drug)}</strong><p>${esc(c.frequency)} · ${esc(c.duration)} · Qty ${c.quantity}</p></div>`).join(''):`<strong>${esc(alt.label)}</strong><p>Specialist decision only. This cannot be applied as a switch.</p>`:'<strong>No automatic replacement</strong><p>The original prescription remains unchanged until the responsible team reviews it.</p>'}</div></div>${checks.length?`<div class="checks-heading"><h3>Checks against the current record</h3><span>${stale?'Updated after what-if change':'Rechecked at approval'}</span></div>${checkRows(checks)}`:''}<div class="rule-stamp">${esc(prop.rule_id)} · v${esc(prop.rule_version)} · Phenotype: ${esc(prop.phenotype.display)}</div><div class="decision-actions">${!blocked?'<button class="primary" data-decision="approve">Approve demo change <span>→</span></button>':''}<button class="secondary" data-decision="escalate">Request specialist review</button><button class="secondary" data-decision="reject">Reject proposal</button></div><p class="action-note">${blocked?'Approval is unavailable because this proposal cannot be applied safely as a switch.':'Approval creates drafts, cancels the original in the sandbox, and saves a message preview. Nothing is issued or sent externally.'}</p>${p.id==='SIM-000011'?`<details class="what-if"><summary>Try a safety scenario</summary><div><button class="secondary" data-command="allergy">${p.whatif_allergy?'Remove':'Add'} aspirin allergy</button><button class="secondary" data-command="failure">${state.fail_next?'Restore prescription service':'Fail next prescription write'}</button></div><p>${state.fail_next?'Failure is armed: the next draft creation will fail. Approval must leave the original active and produce no patient message.':'Change the record after the proposal was drafted to see why safety must be checked again.'}</p></details>`:''}`;
}
function recordView(p){
  const records=p.records, scripts=records.filter(r=>r.kind==='prescription'), problems=records.filter(r=>r.kind==='problem'), messages=records.filter(r=>r.kind==='message-preview'), allergies=records.filter(r=>r.kind==='allergy');
  return `<div class="record-section"><div class="section-label"><h3>Medication record</h3><small>Local sandbox · current state</small></div>${scripts.map(r=>{const cancelled=['rejected','cancelled'].includes(r.status);return `<div class="record-row ${cancelled?'cancelled':esc(r.status)}"><div><strong>${esc(r.title)}</strong><p>${esc(r.data.medicationOrder.frequency)} · ${esc(r.id)}</p></div>${badge(cancelled?'cancelled':r.status,cancelled?'Cancelled':r.status==='draft'?'Draft · not issued':'Active original')}</div>`;}).join('')}</div><div class="record-section"><div class="section-label"><h3>Stored pharmacogenomic result</h3><small>SNOMED identifiers are placeholders</small></div>${problems.length?problems.map(r=>`<div class="record-row"><div><strong>${esc(r.title)}</strong><p>Demo code ${esc(r.data.code)} · ${esc(r.id)}</p></div>${badge('applied','Coded')}</div>`).join(''):'<p class="intro">No phenotype coded'+(!state.scanned?' yet.':'.')+'</p>'}</div>${allergies.length?`<div class="record-section"><h3>Recorded allergies</h3>${allergies.map(r=>`<div class="record-row"><strong>${esc(r.title)}</strong>${badge('blocked','Recorded')}</div>`).join('')}</div>`:''}<div class="record-section"><h3>Patient communication</h3>${messages.length?messages.map(r=>`<div class="message-preview"><strong>${esc(r.title)} · preview only</strong><p>${esc(r.data.text)}</p></div>`).join(''):'<p class="intro">No message preview has been created. Nothing has been sent.</p>'}</div>${p.proposal?.authorised_by?`<div class="message-preview"><strong>Decision recorded by ${esc(p.proposal.authorised_by)}</strong>${esc(p.proposal.decision_note||'No additional note.')}<p>Status: ${esc(labels[p.proposal.status]||p.proposal.status)}</p></div>`:''}<p class="source-provenance">Reset the sandbox to replay the same patient from the original record.</p>`;
}
function showDecision(actionName){
  decisionAction=actionName;const p=current();
  $('#dialog-title').textContent=actionName==='approve'?'Approve this demo change?':actionName==='reject'?'Reject the proposal?':'Request specialist review?';
  $('#dialog-copy').textContent=`${p.name} · ${p.id}`;
  $('#dialog-summary').innerHTML=actionName==='approve'?`<b>On confirmation, the local sandbox will:</b><ul><li>Create ${p.proposal.proposed_regimen.components.length} replacement prescription drafts.</li><li>Cancel ${esc(p.original.drug)}.</li><li>Save a simulated patient-message preview.</li></ul>`:`<b>Medicines will remain unchanged.</b><p>${actionName==='reject'?'Close this proposed change with your reason.':'Record your reason and create a specialist review task in the demo.'}</p>`;
  $('#note').required=actionName!=='approve';$('#note-optional').textContent=actionName==='approve'?'(optional)':'(required)';$('#note').value='';
  $('#confirm-decision').textContent=actionName==='approve'?'Confirm demo approval':actionName==='reject'?'Record rejection':'Record escalation';
  $('#decision-dialog').showModal();$('#clinician').focus();
}
document.addEventListener('click',event=>{
  const button=event.target.closest('button');if(!button||busy)return;
  if(button.dataset.patient){selected=button.dataset.patient;renderList();renderPatient();if(matchMedia('(max-width:760px)').matches)$('#patient-header').scrollIntoView({block:'start'});}
  if(button.dataset.filter){filter=button.dataset.filter;renderList();}
  if(button.dataset.tab){tab=button.dataset.tab;renderPatient();}
  if(button.dataset.command){const command=button.dataset.command;if(['evidence','decision','record'].includes(command)){tab=command;renderPatient();}else action(command);}
  if(button.dataset.decision)showDecision(button.dataset.decision);
  if(button.classList.contains('dialog-close'))$('#decision-dialog').close();
});
$('.tabs').addEventListener('keydown',event=>{
  const keys=['ArrowLeft','ArrowRight','Home','End'];if(!keys.includes(event.key))return;
  event.preventDefault();const tabs=['evidence','decision','record'];let index=tabs.indexOf(tab);index=event.key==='Home'?0:event.key==='End'?2:(index+(event.key==='ArrowRight'?1:2))%3;tab=tabs[index];renderPatient();$('#tab-'+tab).focus();
});
$('#decision-form').addEventListener('submit',async event=>{event.preventDefault();if(await action(decisionAction,{clinician:$('#clinician').value,note:$('#note').value}))$('#decision-dialog').close();});
$('#search').addEventListener('input',renderList);
$('#scan').addEventListener('click',()=>action('scan'));
$('#advance').addEventListener('click',()=>action('advance'));
$('#chase').addEventListener('click',()=>action('chase'));
$('#reset').addEventListener('click',()=>$('#reset-dialog').showModal());
$('#confirm-reset').addEventListener('click',async()=>{if(await action('reset'))$('#reset-dialog').close();});
$('#export').addEventListener('click',()=>{
  $('#audit-json').value=JSON.stringify({mode:'Synthetic local sandbox',...state},null,2);
  $('#copy-status').textContent='Copy this snapshot to save or inspect it outside the demo.';
  $('#audit-dialog').showModal();
});
$('#copy-audit').addEventListener('click',async()=>{
  try {await navigator.clipboard.writeText($('#audit-json').value);$('#copy-status').textContent='Audit JSON copied to the clipboard.';}
  catch {$('#audit-json').focus();$('#audit-json').select();$('#copy-status').textContent='Audit selected. Press Command+C (Mac) or Ctrl+C to copy.';}
});
fetch('/api/state').then(async response=>{if(!response.ok)throw new Error('Could not load the local demo session.');state=await response.json();render();}).catch(error=>{$('#detail').innerHTML=`<div class="error-box">${esc(error.message)} Refresh the page to retry.</div>`;toast(error.message,true);});
