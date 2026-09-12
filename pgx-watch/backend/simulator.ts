import {config} from './config.js';
import type {Patient,Resource,SimAction} from './types.js';
export class Simulator {
  constructor(public base=config.simulator, private key=config.simulatorKey) {}
  async request<T=any>(path:string, body?:unknown, key?:string):Promise<T> {
    if(!this.key) throw new Error('Simulator API key is missing');
    for(let attempt=0;;attempt++) {
      let response:Response;
      try { response=await fetch(this.base+path,{method:body?'POST':'GET',headers:{Authorization:`Bearer ${this.key}`,'Content-Type':'application/json',...(key?{'Idempotency-Key':key}:{})},body:body?JSON.stringify(body):undefined,signal:AbortSignal.timeout(45000)}); }
      catch { if(attempt<2 && (!body||key)) {await new Promise(r=>setTimeout(r,500*2**attempt));continue;} throw new Error(`Simulator network timeout: ${path}`); }
      if((response.status===429||response.status>=500)&&attempt<2&&(!body||key)){await new Promise(r=>setTimeout(r,500*2**attempt));continue;}
      if(!response.ok) throw new Error(`Simulator ${response.status} ${path}: ${(await response.text()).slice(0,300)}`);
      return response.json() as Promise<T>;
    }
  }
  async patient(id:string):Promise<Patient> {
    const page=await this.request<{items:Patient[]}>(`/api/sites/gp/patients?q=${encodeURIComponent(id)}`);
    const p=page.items.find(p=>p.id===id); if(!p)throw new Error(`Patient ${id} not found`);return p;
  }
  async records(id:string):Promise<Resource[]> {
    const all=await Promise.all(['gp','hospital'].map(async site=>{
      let rows:Resource[]=[];
      for(let offset=0;;) {
        const d=await this.request(`/api/sites/${site}/view?patient=${encodeURIComponent(id)}&limit=500&offset=${offset}`);
        rows.push(...d.resources.filter((r:Resource)=>r.patientId===id));offset+=d.resources.length;
        if(offset>=d.resourceTotal)break;if(!d.resources.length)throw new Error('Incomplete resource pagination');
      }return rows;
    }));return [...new Map(all.flat().map(r=>[r.id,r])).values()];
  }
  action(body:SimAction,key:string) {return this.request('/api/sites/gp/actions',body,key);}
}
export const simulator=new Simulator();
