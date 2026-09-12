import Database from 'better-sqlite3';
import {createHash,randomUUID} from 'node:crypto';
import path from 'node:path';
import {config} from './config.js';
export const hash=(x:unknown)=>createHash('sha256').update(JSON.stringify(x)).digest('hex');
export const db = new Database(path.join(config.data,'watch.sqlite'));
db.pragma('journal_mode = WAL');db.pragma('busy_timeout = 5000');
db.exec(`CREATE TABLE IF NOT EXISTS objects (kind TEXT NOT NULL,id TEXT NOT NULL,json TEXT NOT NULL,PRIMARY KEY(kind,id));
CREATE TABLE IF NOT EXISTS audit (seq INTEGER PRIMARY KEY AUTOINCREMENT,time TEXT NOT NULL,type TEXT NOT NULL,entity TEXT NOT NULL,json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS locks (name TEXT PRIMARY KEY,owner TEXT NOT NULL,expires INTEGER NOT NULL);`);
export function put(kind:string,id:string,value:unknown){db.prepare('INSERT INTO objects VALUES (?,?,?) ON CONFLICT(kind,id) DO UPDATE SET json=excluded.json').run(kind,id,JSON.stringify(value));}
export function get<T=any>(kind:string,id:string):T|undefined {const r=db.prepare('SELECT json FROM objects WHERE kind=? AND id=?').get(kind,id) as any;return r?JSON.parse(r.json):undefined;}
export function list<T=any>(kind:string):T[]{return (db.prepare('SELECT json FROM objects WHERE kind=? ORDER BY rowid DESC').all(kind) as any[]).map(r=>JSON.parse(r.json));}
export function audit(type:string,entity:string,value:unknown){db.prepare('INSERT INTO audit(time,type,entity,json) VALUES (?,?,?,?)').run(new Date().toISOString(),type,entity,JSON.stringify(value));}
export function events(entity?:string){return (entity?db.prepare('SELECT * FROM audit WHERE entity=? ORDER BY seq DESC LIMIT 300').all(entity):db.prepare('SELECT * FROM audit ORDER BY seq DESC LIMIT 300').all()).map((r:any)=>({...r,data:JSON.parse(r.json),json:undefined}));}
export async function locked<T>(name:string,fn:()=>Promise<T>):Promise<T>{
 const owner=randomUUID();const take=db.transaction(()=>{db.prepare('DELETE FROM locks WHERE expires<?').run(Date.now());return db.prepare('INSERT OR IGNORE INTO locks VALUES (?,?,?)').run(name,owner,Date.now()+120000).changes;});
 if(!take())throw new Error('A workflow is already running');
 const heartbeat=setInterval(()=>db.prepare('UPDATE locks SET expires=? WHERE name=? AND owner=?').run(Date.now()+120000,name,owner),20000);
 try{return await fn();}finally{clearInterval(heartbeat);db.prepare('DELETE FROM locks WHERE name=? AND owner=?').run(name,owner);}
}
