import {readFileSync, mkdirSync} from 'node:fs';
import path from 'node:path';
export const root = path.resolve(import.meta.dirname, '..');
let keys: Record<string,string> = {};
try { keys = JSON.parse(readFileSync(path.join(root,'api.json'),'utf8')); } catch {}
export const config = {
  simulator: process.env.SIMULATOR_URL || 'https://sim.animahacks.com',
  simulatorKey: process.env.SIMULATOR_API_KEY || keys.simulator || '',
  model: process.env.OPENAI_MODEL || 'gpt-4.1-mini',
  port: Number(process.env.PORT || 3100),
  data: process.env.DATA_DIR || path.join(root,'data'),
  token: process.env.APP_TOKEN || '',
};
process.env.OPENAI_API_KEY ||= keys.OPEN_AI || '';
mkdirSync(config.data,{recursive:true});
