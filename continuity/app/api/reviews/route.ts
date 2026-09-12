import {env} from 'cloudflare:workers';
async function db(){const d=env.DB;await d.prepare('CREATE TABLE IF NOT EXISTS reviews (id TEXT PRIMARY KEY, patient_id TEXT NOT NULL, status TEXT NOT NULL, note TEXT NOT NULL, assignee TEXT NOT NULL, due TEXT NOT NULL, updated_at TEXT NOT NULL)').run();return d;}
export async function GET(){try{const d=await db();const r=await d.prepare('SELECT * FROM reviews ORDER BY updated_at DESC').all();return Response.json(r.results);}catch{return Response.json({error:'Saved reviews are temporarily unavailable.'},{status:503});}}
export async function POST(request:Request){
 if(request.headers.get('origin')&&request.headers.get('origin')!==new URL(request.url).origin)return Response.json({error:'Invalid origin'},{status:403});
 try{const b:any=await request.json();if(typeof b.id!=='string'||b.id.length>150||!/^SIM-\d{6}$/.test(b.patientId)||!['open','reviewed','resolved','deferred'].includes(b.status)||typeof b.note!=='string'||b.note.length>5000||typeof b.assignee!=='string'||b.assignee.length>100||typeof b.due!=='string'||(b.due&&!/^\d{4}-\d{2}-\d{2}$/.test(b.due)))return Response.json({error:'Please check the review fields.'},{status:400});
 if(['resolved','deferred'].includes(b.status)&&!b.note.trim())return Response.json({error:'Add a reason before resolving or deferring.'},{status:400});
 const d=await db();await d.prepare('INSERT INTO reviews (id,patient_id,status,note,assignee,due,updated_at) VALUES (?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET status=excluded.status,note=excluded.note,assignee=excluded.assignee,due=excluded.due,updated_at=excluded.updated_at').bind(b.id,b.patientId,b.status,b.note,b.assignee,b.due,new Date().toISOString()).run();return Response.json({ok:true});
 }catch{return Response.json({error:'Could not save this review. Please retry.'},{status:503});}
}
