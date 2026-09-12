import {test} from 'node:test';
import assert from 'node:assert/strict';
import {Simulator} from './simulator.js';
test('uncertain action retries preserve idempotency key and exact body',async()=>{
 const original=globalThis.fetch;const calls:RequestInit[]=[];
 globalThis.fetch=(async(_url:unknown,init:RequestInit)=>{calls.push(init);if(calls.length===1)throw Error('Connection lost after write');return Response.json({resource:{id:'r-created'}});}) as typeof fetch;
 try{const sim=new Simulator('https://example.invalid','test-key');const result=await sim.action({type:'create_task',patientId:'SIM-000001'},'same-key');assert.equal(result.resource.id,'r-created');assert.equal(calls.length,2);assert.deepEqual(calls[0].headers,calls[1].headers);assert.equal(calls[0].body,calls[1].body);}finally{globalThis.fetch=original;}
});
test('patient resources paginate both scopes and exclude shared/unrelated records',async()=>{
 const original=globalThis.fetch;
 globalThis.fetch=(async(url:unknown)=>{const u=new URL(String(url)),offset=u.searchParams.get('offset');return Response.json({resourceTotal:3,resources:offset==='0'?[{id:'shared',version:1},{id:'r1',patientId:'SIM-000001',version:1}]:[{id:'r2',patientId:'SIM-000001',version:1}]});}) as typeof fetch;
 try{const records=await new Simulator('https://example.invalid','key').records('SIM-000001');assert.deepEqual(records.map(r=>r.id),['r1','r2']);}finally{globalThis.fetch=original;}
});
