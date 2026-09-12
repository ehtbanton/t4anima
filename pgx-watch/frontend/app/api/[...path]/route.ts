import { env } from 'cloudflare:workers';
async function proxy(request:Request){
 const e=env as unknown as Record<string,string>;
 const url=new URL(request.url);
 const local=['localhost','127.0.0.1'].includes(url.hostname);
 const base=e.BACKEND_URL||(local?'http://127.0.0.1:3100':'');
 if(!base)return Response.json({error:'Connect this interface to the PGx Watch backend using BACKEND_URL and APP_TOKEN.'},{status:503});
 if(request.method!=='GET'){
  const origin=request.headers.get('origin');
  if(origin&&origin!==url.origin)return Response.json({error:'Origin not allowed'},{status:403});
 }
 try{
  const result=await fetch(base+url.pathname+url.search,{method:request.method,headers:{'Content-Type':'application/json',...(e.APP_TOKEN?{Authorization:`Bearer ${e.APP_TOKEN}`}:{})},body:request.method==='GET'?undefined:await request.text()});
  return new Response(result.body,{status:result.status,headers:{'Content-Type':'application/json','Cache-Control':'no-store'}});
 }catch{return Response.json({error:'The backend is offline. Start the PGx Watch service and reconnect.'},{status:503});}
}
export const GET=proxy;export const POST=proxy;
