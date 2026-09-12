import {test} from 'node:test';
import assert from 'node:assert/strict';
import {runTest,user,model} from '@animahealth/adk/testing';
import {evidenceAgent,app} from './agents.js';
test('ADK evidence agent uses structured output and records model events without live API',async()=>{
 const expected={facts:[{gene:'CYP2C19',result:'pending',phenotype:'',subject:'patient',status:'pending',drug:'clopidogrel',context:'TIA',evidence:[{resourceId:'doc-1',version:1,field:'data.sections.results'}]}],warnings:[]};
 // ADK 0.6's test helper erases the state-schema generic; pass the matching schema explicitly.
 const result=await runTest(evidenceAgent as unknown as Parameters<typeof runTest>[0],[user('Synthetic pending result'),model({text:JSON.stringify(expected)})],{schema:app.schema});
 assert.equal(result.status,'completed');assert.ok(result.session.events.some(e=>e.type==='model_start'));assert.deepEqual(result.session.state.extraction,expected);
});
