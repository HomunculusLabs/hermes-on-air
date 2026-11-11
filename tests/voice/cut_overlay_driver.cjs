// Drive the shipped program overlay with real stage events and a fake player.
const fs = require('node:fs');
const vm = require('node:vm');
const readline = require('node:readline');
const elements = new Map(), players = [];
function element() { return {hidden:false,textContent:'',style:{},dataset:{},offsetWidth:0,children:[],
  classList:{add(){},remove(){},toggle(){}},addEventListener(){},appendChild(){},querySelector(){return null}}; }
const document = {getElementById(id){if(!elements.has(id)) elements.set(id,element()); return elements.get(id);},
  body:element(),documentElement:{style:{setProperty(){}}},createElement:element};
let source;
class EventSource {constructor(){source=this} close(){}}
class Audio {constructor(url){this.src=url;this.paused=false;this.plays=0;players.push(this)}
  addEventListener(){} play(){this.plays++;this.paused=false;return Promise.resolve()}
  pause(){this.paused=true} removeAttribute(name){if(name==='src') this.src=''} load(){} }
const context = {document,EventSource,Audio,URLSearchParams,location:{search:''},
  window:{addEventListener(){}},setTimeout:()=>1,clearTimeout(){},getComputedStyle:()=>({fontSize:'40',lineHeight:'40'})};
vm.runInNewContext(fs.readFileSync('onair/overlay/web/overlay.js','utf8'),context);
readline.createInterface({input:process.stdin}).on('line', line=>{
  for(const event of JSON.parse(line)) source.onmessage({data:JSON.stringify(event)});
  process.stdout.write(JSON.stringify({players:players.map(p=>({src:p.src,paused:p.paused,plays:p.plays})),
    caption:elements.get('caption-text').textContent,hidden:elements.get('caption').hidden})+'\n');
});
