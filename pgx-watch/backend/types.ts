export interface Patient { id:string; name:string; birthDate:string; death?:{date:string}; conditions:string[]; [key:string]:unknown }
export interface Resource {id:string; patientId?:string; title:string; kind:string; status:string; version:number; createdAt:number; data:Record<string,any>; [key:string]:any}
export interface Evidence {resourceId:string; version:number; quote:string}
export interface Fact {gene:string; result:string; phenotype:string; subject:'patient'|'family'|'unknown'; status:'confirmed'|'pending'|'uncertain'; drug:string; context:string; evidence:Evidence[]}
export interface Finding {id:string; patientId:string; patientName:string; ruleId:string; code:string; title:string; action:string; specialist:string; severity:string; evidence:Evidence[]; facts:Fact[]; snapshotHash:string; status:string; createdAt:string; updatedAt:string; decision?:Decision; source?:string; runId?:string; warnings:string[]}
export interface Decision {decision:'accept'|'deny'|'defer'; rationale:string; evidence:Evidence[]}
export interface Rule {id:string; gene:string; result:string; drug:string; context:string; code:string; action:string; specialist:string; severity:string; source:string; samplePatients:string[]}
export interface SimAction {type:string; patientId?:string; title?:string; text?:string; target?:string; [key:string]:unknown}
