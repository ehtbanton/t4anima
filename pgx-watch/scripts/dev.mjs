import {spawn} from 'node:child_process';
if(Number(process.versions.node.split('.')[0])<22){console.error('Node 22+ required. Try: npx --yes --package=node@22 -c "npm run dev"');process.exit(1);}
const children=[spawn(process.execPath,['--import','tsx','backend/server.ts'],{stdio:'inherit'}),spawn('npm',['run','dev'],{cwd:'frontend',stdio:'inherit'})];
let stopping=false;
function stop(code=0){if(stopping)return;stopping=true;for(const child of children)child.kill('SIGTERM');setTimeout(()=>process.exit(code),400);}
process.on('SIGINT',()=>stop());process.on('SIGTERM',()=>stop());for(const child of children)child.on('exit',code=>stop(code||0));
