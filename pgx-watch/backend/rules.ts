import {readFileSync} from 'node:fs';
import path from 'node:path';
import {z} from 'zod';
import {root} from './config.js';
const schema=z.array(z.object({id:z.string(),gene:z.string(),result:z.string(),drug:z.string(),context:z.string(),code:z.string(),action:z.string(),specialist:z.string(),severity:z.enum(['high','moderate','info']),source:z.string(),samplePatients:z.array(z.string())}));
export const rules=schema.parse(JSON.parse(readFileSync(path.join(root,'rules/pgx.json'),'utf8')));
export const samplePatients=[...new Set(rules.flatMap(r=>r.samplePatients))];
