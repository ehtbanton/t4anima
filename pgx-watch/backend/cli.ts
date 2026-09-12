import {runScan} from './engine.js';
const args=process.argv.slice(2);
const scope=args.find(x=>['sample','documents','all'].includes(x))||'documents';
const run=await runScan(scope,args.includes('--execute'),undefined,'cli');
console.log(JSON.stringify(run,null,2));process.exit(run.status==='completed'?0:1);
