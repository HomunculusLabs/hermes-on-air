// Visual harness: renders the real desktop/plugin.js in headless Chromium and captures evidence PNGs.
// No network, no webfonts; system-ui only. Usage: node visual_harness.cjs <outdir>
// Requires ONAIR_HERMES_SDK (SDK with node_modules) and ONAIR_CHROME (chromium executable).
// Optional ONAIR_PLAYWRIGHT: directory containing a playwright install (defaults to SDK node_modules).
const fs = require('node:fs'); const path = require('node:path');
const SDK = process.env.ONAIR_HERMES_SDK;
if (!SDK) { console.error('ONAIR_HERMES_SDK is required'); process.exit(2); }
let chromium; try { ({chromium} = require(path.join(process.env.ONAIR_PLAYWRIGHT || path.join(SDK,'node_modules'), 'playwright'))); }
catch { console.error('playwright not found; set ONAIR_PLAYWRIGHT'); process.exit(2); }
const CHROME = process.env.ONAIR_CHROME;
if (!CHROME || !fs.existsSync(CHROME)) { console.error('ONAIR_CHROME chromium executable not found'); process.exit(2); }
const esbuild = require(path.join(SDK, 'node_modules', 'esbuild'));
const OUT = process.argv[2] || (path.join(require('node:os').tmpdir(), 'onair-shots'));
const ESB = {absWorkingDir: SDK};
fs.mkdirSync(OUT,{recursive:true});
const sdkShim = path.join(OUT,'onair-sdk-shim.mjs'); fs.writeFileSync(sdkShim, 'export const ROUTES_AREA="routes";export const SIDEBAR_NAV_AREA="nav";\n');
const entry = `
import plugin from ${JSON.stringify(path.resolve(__dirname,'../../desktop/plugin.js'))};
import { createRoot } from 'react-dom/client';
import React from 'react';window.__mount = function(el, cfg) {
  const state = cfg.state;
  const rest = async (p, o = {}) => {
    if (p === '/state') { if (cfg.offline) throw Error('Show state is unavailable.'); return state; }
    if (p === '/pending') return { epoch: state.epoch, pending: cfg.pending };
    if (p === '/views/state') { if (cfg.viewsOff) return { ok:false, connected:false, error:'OBS is not reachable. Check that OBS is running and its websocket is enabled.' }; return cfg.views; }
    if (o.method === 'POST') { if (cfg.deny) return { ok:false, error:'The show refused: this line was already handled.' }; if (cfg.slowPost) await new Promise(r => setTimeout(r, 1400)); return { ok:true, queued:true, note:'Request accepted; state refreshed.' }; }
    return {};
  };
  const items = [];
  plugin.register({ rest, registerMany: x => items.push(...x), register: i => items.push(i) });
  const item = cfg.compact ? items.find(i => i.id === 'onair-pane') : items.find(i => i.id === 'onair-page');
  const root = createRoot(el);
  root.render(React.createElement(item.render));
  return () => root.unmount();
};
`;
const NM = path.join(SDK, 'node_modules');
const bundle = esbuild.buildSync({...ESB, stdin:{contents:entry, resolveDir: SDK}, bundle:true, format:'iife', alias:{'@hermes/plugin-sdk':sdkShim, 'react':path.join(NM,'react','index.js'), 'react-dom/client':path.join(NM,'react-dom','client.js')}, define:{'process.env.NODE_ENV':'"production"'}, write:false}).outputFiles[0].text;
const LINES = [
  {line_id:'l1', epoch:7, text:'Welcome back — tonight we are building a small synth live on camera.', source:'host'},
  {line_id:'l2', epoch:7, text:'Chat asks: what sample rate are you recording at?', source:'chat'},
  {line_id:'l3', epoch:7, text:'Beat idea: fade the pads under the next answer.', source:'beat'},
];
const VIEWS = {ok:true, connected:true, program_scene:'On Air: Boxes', boxes:{slots:{main:'scene',sub1:'slides',sub2:'window'},arrangement:'stack',inset_corner:'bottom-right',arrangement_source:'readback',available:{scene:{available:true},slides:{available:true},window:{available:true}}}, window:{ready:true}, window_state:'shown', layout:{presets:{presentation:{ready:true},window:{ready:true},window_pip:{ready:true},side_by_side:{ready:true}}}};
const THEMES = {
  light: {'--background':'#f4f5f9','--foreground':'#1b1f27','--ui-bg-editor':'#fbfbfe','--ui-bg-card':'#ffffff','--ui-text-secondary':'#545e70','--ui-stroke-secondary':'#ccd2df','--ui-accent':'#4d43a8'},
  dark: {'--background':'#14161c','--foreground':'#e8eaf0','--ui-bg-editor':'#181b22','--ui-bg-card':'#1f232d','--ui-text-secondary':'#9aa3b5','--ui-stroke-secondary':'#3a4150','--ui-accent':'#9c93ec'},
};
(async()=>{
  const browser = await chromium.launch({executablePath: CHROME});
  const records = [];
  async function capture(name, {theme='light', width=1440, height=980, compact=false, compactW, over={}, click, type, during, scroll}={}) {
    const cfg = {compact, state:{epoch:7, voice_boot:'a'.repeat(32), mode:'approval', privacy:false, muted:false, rundown:'Show and tell: mini synth', host_turns:{enabled:true}, ...(over.state||{})}, views: over.views || VIEWS, pending: over.pending===undefined ? LINES : over.pending, offline: !!over.offline, viewsOff: !!over.viewsOff, deny: !!over.deny, slowPost: !!over.slowPost};
    const page = await browser.newPage({viewport:{width: compact?(compactW||360):width, height}});
    await page.setContent('<!doctype html><meta charset="utf-8"><style>html,body,#host{margin:0;min-height:100%;height:100%}body{background:#f4f5f9}</style><div id="host"></div>');
    await page.addScriptTag({content: bundle});
    const styleDecl = Object.entries(THEMES[theme]).map(([k,v])=>`${k}:${v}`).join(';') + ';--page-bg:' + THEMES[theme]['--background'];
    await page.evaluate(({cfg, styleDecl})=>{
      document.documentElement.setAttribute('style', styleDecl);
      document.body.style.background = 'var(--page-bg)';
      window.__mount(document.getElementById('host'), cfg);
    }, {cfg, styleDecl});
    await page.waitForTimeout(300);
    if (click) { const loc = page.getByRole('button', {name:click, exact:true}); await (click==='Approve'?loc.first():loc).click(); await page.waitForTimeout(during ? 300 : 150); }
    if (type) { await page.locator('textarea').fill(type); await page.waitForTimeout(100); }
    if (scroll) await page.mouse.wheel(0, 900);
    const file = path.join(OUT, name+'.png');
    await page.screenshot({path:file, fullPage: !compact});
    const m = await page.evaluate(()=>{const root=document.querySelector('.onair-desk');const stage=root.querySelector('.stage');const rect=e=>{const r=e.getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height}};return {w:innerWidth,h:innerHeight,dpr:devicePixelRatio,scroll:document.documentElement.scrollWidth,root:rect(root),swaps:stage?[...stage.querySelectorAll('.swap')].map(rect):[],boxes:stage?[...stage.querySelectorAll('.box')].map(rect):[],queue:[...root.querySelectorAll('.queue .who')].map(rect)}});
    records.push({name, theme, compact, width:m.w, scrollW:m.scroll, metrics:m, file});
    await page.close();
    return file;
  }
  for (const theme of ['light','dark']) {
    await capture(`${theme}-wide-show`, {theme});
    await capture(`${theme}-wide-views`, {theme, click:'Views'});
    await capture(`${theme}-wide-host`, {theme, click:'Host'});
    await capture(`${theme}-narrow-show`, {theme, width:900, height:900});
    await capture(`${theme}-narrow-views`, {theme, width:900, click:'Views'});
    await capture(`${theme}-narrow-host`, {theme, width:900, click:'Host', type:'Testing a longer host line so the composer shows real content.'});
    await capture(`${theme}-pane`, {theme, compact:true, height:760});
    await capture(`${theme}-narrow-pane`, {theme, compact:true, compactW:320, height:720});
    await capture(`${theme}-pane-scrolled`, {theme, compact:true, height:720, scroll:true});
    await capture(`${theme}-wide-privacy`, {theme, over:{state:{privacy:true}}});
    await capture(`${theme}-wide-empty`, {theme, over:{pending:[]}});
    await capture(`${theme}-wide-offline`, {theme, over:{offline:true}});
    await capture(`${theme}-wide-obs`, {theme, click:'Views', over:{viewsOff:true}});
  }
  await capture('dark-wide-refusal', {theme:'dark', over:{deny:true}, click:'Approve', during:true});
  await capture('light-wide-busy', {theme:'light', over:{slowPost:true}, click:'Stop (Esc)', during:true});
  // Exercise every reported corner at page and pane widths without altering the capture matrix.
  const checks=[];
  for (const compact of [false,true]) for (const corner of ['top-left','top-right','bottom-left','bottom-right','side']) {
    const page=await browser.newPage({viewport:{width:compact?320:1440,height:980}});
    await page.setContent('<!doctype html><meta charset="utf-8"><style>html,body,#host{margin:0;height:100%}</style><div id="host"></div>');
    await page.addScriptTag({content:bundle});
    const views=JSON.parse(JSON.stringify(VIEWS));if(corner==='side') views.boxes.arrangement='side';else views.boxes.inset_corner=corner;
    await page.evaluate(({compact,views})=>window.__mount(document.getElementById('host'),{compact,views,pending:[],state:{epoch:7,voice_boot:'a'.repeat(32),mode:'approval',privacy:false,muted:false,host_turns:{enabled:true}}}),{compact,views});
    await page.waitForTimeout(100);
    if(!compact) await page.getByRole('button',{name:'Views',exact:true}).click();
    const g=await page.evaluate(()=>{const stage=document.querySelector('.stage');const root=document.querySelector('.onair-desk');const rect=e=>{const r=e.getBoundingClientRect();return {x:r.x,y:r.y,w:r.width,h:r.height}};return {stage:rect(stage),root:rect(root),boxes:[...stage.querySelectorAll('.box')].map(rect),swaps:[...stage.querySelectorAll('.swap')].map(rect),boxOverflow:[...stage.querySelectorAll('.box')].map(e=>({h:e.scrollHeight,client:e.clientHeight})),scroll:document.documentElement.scrollWidth,width:innerWidth}});
    const assert=require('node:assert/strict');
    assert.ok(g.stage.x>=g.root.x&&g.stage.x+g.stage.w<=g.root.x+g.root.w+1,JSON.stringify({compact,corner,g}));
    assert.ok(g.scroll<=g.width,JSON.stringify({compact,corner,g}));
    const [main,sub1,sub2]=g.boxes, [a,b,c]=g.swaps, cy=r=>r.y+r.h/2;
    if(corner==='side') {
      assert.ok(main.x+main.w<sub1.x&&sub1.y<sub2.y);
      assert.ok(a.x>main.x+main.w&&a.x+a.w<sub1.x+1,JSON.stringify({compact,corner,g}));
    } else {
      assert.ok(corner.endsWith('left')?sub1.x<main.x+main.w/2:sub1.x>main.x+main.w/2);
      assert.ok(corner.startsWith('top')?sub1.y<sub2.y:sub1.y>sub2.y);
    }
    assert.ok(Math.abs((a.x+a.w/2)-(b.x+b.w/2))<1&&Math.abs((b.x+b.w/2)-(c.x+c.w/2))<1);
    assert.ok(Math.abs(cy(a)-cy(sub1))<2&&Math.abs(cy(b)-cy(sub2))<2&&Math.abs(cy(c)-(cy(sub1)+cy(sub2))/2)<2,JSON.stringify({compact,corner,g}));
    assert.ok(g.swaps.every(s=>s.w>=32&&s.h>=32));
    assert.ok(Math.abs(cy(a)-cy(c))>=(a.h+c.h)/2-1&&Math.abs(cy(b)-cy(c))>=(b.h+c.h)/2-1);
    assert.ok(g.boxOverflow.every(x=>x.h<=x.client+1),JSON.stringify({compact,corner,g}));
    checks.push({compact,corner,geometry:g});
    await page.close();
  }
  await browser.close();
  fs.writeFileSync(path.join(OUT,'report.json'), JSON.stringify({records,checks},null,2));
  console.log('captured', records.length, '->', OUT);
})().catch(e=>{console.error(e);process.exit(1)});
