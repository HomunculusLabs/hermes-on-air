// Exercise the shipped event handler and default character with geometry-only fakes.
const fs=require('node:fs'), vm=require('node:vm');
const path=require('node:path');
const root=path.join(__dirname,'../../onair/scene_starter');
class Part {
  constructor() {this.children=[];this.position={x:0,y:0,z:0,set(x,y,z){Object.assign(this,{x,y,z})}};this.scale={x:1,y:1,z:1,setScalar(v){this.x=this.y=this.z=v}};this.rotation={x:0,y:0,z:0};this.visible=true;}
  add(p){this.children.push(p);}
}
const THREE={Group:Part,Mesh:Part,Vector3:class {},QuadraticBezierCurve3:class {},TubeGeometry:class {}};
const sphere=(parent,material,radius,position,scale)=>{const p=new Part();[p.position.x,p.position.y,p.position.z]=position;[p.scale.x,p.scale.y,p.scale.z]=scale;parent.add(p);return p;};
let source;
const document={body:{dataset:{}}}, window={};
class EventSource {constructor(){source=this;} close(){}}
const sandbox={THREE,document,window,EventSource,fetch:async()=>({json:async()=>({avatar:false})}),mat:()=>({}),sphere,textureMaterial:()=>({}),performance,Date,setTimeout,clearTimeout,console,moodForLine:()=> 'neutral',DataView,Float32Array};
vm.createContext(sandbox);
let character=fs.readFileSync(path.join(root,'character.js'),'utf8').replace(/^import .*;\n/gm,'').replace('export async function createCharacter','async function createCharacter');
let events=fs.readFileSync(path.join(root,'events.js'),'utf8').replace(/^import .*;\n/gm,'').replace(/export function /g,'function ');
vm.runInContext(character+'\n'+events+'\nglobalThis.run = async function(events) { const c=await createCharacter(new THREE.Group(), {accent:"#fff"}); connectEvents(c); for(const event of events.before) sourceSend(event); c.update(1,.1); const before={speaking:document.body.dataset.speaking,mouth:c.status().mouthScale}; for(const event of events.cut) sourceSend(event); return {before,after:{speaking:document.body.dataset.speaking,mouth:c.status().mouthScale,dataset:document.body.dataset.mouth}}; }; function sourceSend(ev) { globalThis.__source.onmessage({data:JSON.stringify(ev)}); }',sandbox);
// Expose the EventSource created inside the sandbox to its helper.
Object.defineProperty(sandbox,'__source',{get:()=>source});
let input=''; process.stdin.on('data',s=>input+=s); process.stdin.on('end',async()=>{try {let r=await sandbox.run(JSON.parse(input)); console.log(JSON.stringify(r)); process.exitCode=0;}catch(e){console.error(e);process.exitCode=1;}});
