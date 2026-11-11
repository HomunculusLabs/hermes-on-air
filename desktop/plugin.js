import React from 'react';
import { ROUTES_AREA, SIDEBAR_NAV_AREA } from '@hermes/plugin-sdk';

const h = React.createElement;
// Design system: spacing/type scale, control heights, radii and semantic colours ride on the
// app's theme tokens (--ui-*, --foreground) with offline fallbacks for light and dark themes.
const styles = `
.onair-desk{--oa-s1:4px;--oa-s2:8px;--oa-s3:12px;--oa-s4:16px;--oa-s5:24px;--oa-r:12px;--oa-r2:8px;
 --oa-text:var(--foreground,#22242c);--oa-sub:var(--ui-text-secondary,#57606f);--oa-border:var(--ui-stroke-secondary,#c9cfdd);
 --oa-accent:var(--ui-accent,#4d43a8);--oa-bg:var(--ui-bg-editor,var(--background,#f5f6fa));--oa-card:var(--ui-bg-card,var(--background,#fff));
 --oa-ok:color-mix(in srgb,#2fa06b 65%,var(--foreground));--oa-warn:#9a6212;--oa-warn-t:color-mix(in srgb,#c98a2a 65%,var(--foreground));--oa-danger:#a02b30;--oa-danger-t:color-mix(in srgb,#e0564f 65%,var(--foreground));
 box-sizing:border-box;min-width:0;min-height:100%;overflow-wrap:anywhere;padding:var(--oa-s5);color:var(--oa-text);background:var(--oa-bg);
 font:15px/1.55 system-ui,sans-serif;container-type:inline-size}
.onair-desk *{box-sizing:border-box}
.onair-desk h1{font-size:23px;line-height:1.15;margin:0;letter-spacing:-.01em}
.onair-desk h2{font-size:17px;line-height:1.3;margin:0 0 var(--oa-s2)}
.onair-desk h3{font-size:13px;margin:var(--oa-s4) 0 var(--oa-s2);color:var(--oa-sub);text-transform:uppercase;letter-spacing:.05em;font-weight:700}
.onair-desk p{margin:6px 0}
.onair-desk .helper{color:var(--oa-sub);font-size:13px;margin:4px 0}
.onair-desk .card{border:1px solid var(--oa-border);border-radius:var(--oa-r);padding:var(--oa-s4);background:var(--oa-card);min-width:0}
.onair-desk .row{display:flex;gap:var(--oa-s2);flex-wrap:wrap;align-items:center}
.onair-desk button,.onair-desk select,.onair-desk textarea{font:inherit;color:inherit;background:var(--oa-card);border:1px solid var(--oa-border);border-radius:var(--oa-r2);padding:9px 13px;min-height:36px;max-width:100%}
.onair-desk button{cursor:pointer;font-weight:600}
.onair-desk button:hover:not(:disabled):not([aria-pressed=true]){border-color:var(--oa-accent)}
.onair-desk button:active:not(:disabled){transform:translateY(1px)}
.onair-desk button[aria-pressed=true]{border-color:var(--oa-accent);color:var(--oa-accent);background:color-mix(in srgb,var(--oa-accent) 12%,var(--oa-card))}
.onair-desk button:disabled,.onair-desk select:disabled,.onair-desk textarea:disabled{opacity:.55;cursor:not-allowed}
.onair-desk .primary{background:#4d43a8;color:#fff;border-color:#4d43a8}
.onair-desk .primary:hover:not(:disabled){filter:brightness(1.12)}
.onair-desk .danger{background:var(--oa-danger);color:#fff;border-color:var(--oa-danger)}
.onair-desk .quiet{background:transparent}
.onair-desk :focus-visible{outline:3px solid var(--oa-accent);outline-offset:2px}
.onair-desk .statusbar{display:flex;justify-content:space-between;align-items:flex-start;gap:var(--oa-s4);margin:0 0 var(--oa-s4)}
.onair-desk .status-lead{display:flex;align-items:baseline;gap:var(--oa-s3);flex-wrap:wrap}
.onair-desk .status-lead h1{font-size:13px;text-transform:uppercase;letter-spacing:.09em;color:var(--oa-sub);font-weight:800}
.onair-desk .live-state{font-size:24px;font-weight:800;padding-left:var(--oa-s3);border-left:5px solid var(--oa-accent);line-height:1.1}
.onair-desk .live-state.st-off{border-color:var(--oa-border)}
.onair-desk .live-state.st-autonomous{border-color:var(--oa-ok)}
.onair-desk .live-state.st-paused{border-color:var(--oa-warn)}
.onair-desk .live-state.st-override{border-color:var(--oa-danger)}
.onair-desk .connection{font-size:13px;color:var(--oa-sub);display:inline-flex;align-items:center;gap:6px}
.onair-desk .connection::before{content:'';width:9px;height:9px;border-radius:50%;background:var(--oa-ok);flex:none}
.onair-desk .connection.off{color:var(--oa-danger-t);font-weight:700}
.onair-desk .connection.off::before{background:var(--oa-danger)}
.onair-desk .safety{display:flex;gap:var(--oa-s2);flex-wrap:wrap}
.onair-desk .safety button{min-height:40px}
.onair-desk .safety button[aria-pressed=true]{background:var(--oa-warn);border-color:var(--oa-warn);color:#fff}
.onair-desk .banner{border-radius:var(--oa-r2);padding:10px 14px;font-weight:700;margin:0 0 var(--oa-s3)}
.onair-desk .banner-privacy{background:var(--oa-danger);color:#fff}
.onair-desk .banner-muted{background:var(--oa-warn);color:#fff}
.onair-desk .notice{margin:0 0 var(--oa-s3);padding:10px 12px;border-radius:var(--oa-r2);border:1px solid color-mix(in srgb,var(--oa-warn) 45%,transparent);border-left:4px solid var(--oa-warn);background:color-mix(in srgb,var(--oa-warn) 18%,var(--oa-card));font-weight:600;display:flex;gap:9px;align-items:center}
.onair-desk .notice:empty{display:none}
.onair-desk .notice.busy::before{content:'';width:16px;height:16px;border-radius:50%;border:2.5px solid var(--oa-sub);border-top-color:transparent;flex:none;animation:oa-spin .9s linear infinite}
@keyframes oa-spin{to{transform:rotate(360deg)}}
.onair-desk nav{display:flex;gap:var(--oa-s1);border-bottom:1px solid var(--oa-border);margin:0 0 var(--oa-s4)}
.onair-desk nav button{background:transparent;border:1px solid transparent;border-bottom:0;border-radius:var(--oa-r2) var(--oa-r2) 0 0;padding:9px 16px;color:var(--oa-sub)}
.onair-desk nav button[aria-pressed=true]{color:var(--oa-text);box-shadow:inset 0 -3px 0 var(--oa-accent);font-weight:700}
.onair-desk .columns{display:grid;grid-template-columns:minmax(0,1fr) 340px;gap:var(--oa-s4);align-items:start}
.onair-desk .stack{display:grid;gap:var(--oa-s4)}
.onair-desk .queue{list-style:none;margin:var(--oa-s2) 0 0;padding:0;display:grid;gap:10px}
.onair-desk .queue li{border:0;border-radius:var(--oa-r2);padding:var(--oa-s3) var(--oa-s4);background:var(--oa-bg)}
.onair-desk .queue li:first-child{border-left:4px solid var(--oa-accent);padding-left:calc(var(--oa-s4) - 4px)}
.onair-desk .queue .who{font-size:12px;font-weight:700;letter-spacing:.05em;text-transform:uppercase;color:var(--oa-sub)}
.onair-desk .queue li:first-child .who{color:var(--oa-accent)}
.onair-desk .queue .line{font-size:16px;line-height:1.45;margin:5px 0 10px}
.onair-desk .queue .row{margin:0}
.onair-desk .empty{padding:var(--oa-s5);text-align:center;color:var(--oa-sub);font-size:14.5px}
.onair-desk .segments{display:inline-flex;gap:3px;padding:3px;border:1px solid var(--oa-border);border-radius:10px;background:var(--oa-bg);flex-wrap:wrap}
.onair-desk .segments.grid2{display:grid;grid-template-columns:1fr 1fr;width:100%}
.onair-desk .segments button{min-height:32px;border-color:transparent;background:transparent;border-radius:var(--oa-r2);padding:5px 11px;color:var(--oa-sub);display:inline-flex;align-items:center;gap:7px}
.onair-desk .segments button[aria-pressed=true]{background:var(--oa-card);color:var(--oa-text);border-color:var(--oa-accent);font-weight:700}
.onair-desk .mini{display:inline-block;width:26px;height:16px;border:1px solid currentColor;border-radius:3px;position:relative;vertical-align:-3px;flex:none}
.onair-desk .mini i{position:absolute;background:currentColor;opacity:.7;border-radius:1px}
.onair-desk .mini.m-stack i.a{left:58%;top:12%;width:36%;height:34%}.onair-desk .mini.m-stack i.b{left:58%;top:54%;width:36%;height:34%}
.onair-desk .mini.m-side i.a{left:8%;top:12%;width:38%;height:76%}.onair-desk .mini.m-side i.b{left:54%;top:12%;width:38%;height:34%}
.onair-desk .mini.m-full i.a{left:8%;top:12%;width:84%;height:76%}
.onair-desk .mini.m-pip i.a{left:8%;top:12%;width:84%;height:76%}.onair-desk .mini.m-pip i.b{left:60%;top:56%;width:32%;height:30%}
.onair-desk .mini.m-duo i.a{left:6%;top:12%;width:42%;height:76%}.onair-desk .mini.m-duo i.b{left:52%;top:12%;width:42%;height:76%}
.onair-desk .mini.m-win i.a{left:8%;top:26%;width:84%;height:62%}.onair-desk .mini.m-win i.b{left:8%;top:10%;width:84%;height:9%}
.onair-desk .mini.m-corner i{width:25%;height:27%}.onair-desk .mini.m-corner.top-left i,.onair-desk .mini.m-corner.bottom-left i{left:8%}.onair-desk .mini.m-corner.top-right i,.onair-desk .mini.m-corner.bottom-right i{right:8%}
.onair-desk .mini.m-corner.top-left i.a,.onair-desk .mini.m-corner.top-right i.a{top:8%}.onair-desk .mini.m-corner.top-left i.b,.onair-desk .mini.m-corner.top-right i.b{top:40%}
.onair-desk .mini.m-corner.bottom-left i.a,.onair-desk .mini.m-corner.bottom-right i.a{bottom:8%}.onair-desk .mini.m-corner.bottom-left i.b,.onair-desk .mini.m-corner.bottom-right i.b{bottom:40%}
.onair-desk .mode-note{margin:10px 2px 0}
.onair-desk .kicker{font-size:13px;color:var(--oa-sub);margin:2px 0 6px}
.onair-desk textarea{display:block;width:100%;min-height:132px;margin:8px 0;resize:vertical;line-height:1.5}
.onair-desk .composer-foot{display:flex;justify-content:space-between;align-items:center;gap:var(--oa-s3)}
.onair-desk .count{font-size:13px;color:var(--oa-sub)}
.onair-desk .voice{margin-top:var(--oa-s4);padding:8px 10px;border:1.5px dashed var(--oa-border);border-radius:var(--oa-r2);color:var(--oa-sub);font-size:12.5px}
.onair-desk .card.composer{max-width:760px}
.onair-desk .views-grid{display:grid;grid-template-columns:minmax(0,1.15fr) minmax(300px,.85fr);gap:var(--oa-s4);align-items:start}
.onair-desk .views-grid>.stack{gap:22px}
.onair-desk .stage{position:relative;width:100%;min-width:0;aspect-ratio:16/9;min-height:280px;max-width:640px;border:1.5px solid var(--oa-border);border-radius:var(--oa-r);padding:8px;display:grid;gap:8px;grid-template-columns:minmax(0,2.2fr) minmax(0,1fr);grid-template-rows:1fr 1fr;background:color-mix(in srgb,var(--oa-accent) 7%,var(--oa-bg))}
.onair-desk .stage .box{display:flex;flex-direction:column;align-items:flex-start;gap:2px;min-width:0;border:1px solid var(--oa-border);border-radius:var(--oa-r2);padding:8px;background:var(--oa-card)}
.onair-desk .stage .box .tag{font-size:11.5px;font-weight:700;letter-spacing:.05em;text-transform:uppercase;color:var(--oa-sub)}
.onair-desk .stage .box .srclabel{font-size:13px;font-weight:800;color:var(--oa-accent);line-height:1.25;overflow-wrap:anywhere}
.onair-desk .stage .box:first-child{grid-row:1/3}
.onair-desk .stage.stack .box:first-child{grid-column:1/3;grid-row:1/3}
.onair-desk .stage.stack .box:nth-child(2),.onair-desk .stage.stack .box:nth-child(3){position:absolute;width:20%;height:20%;grid-column:auto;grid-row:auto}
.onair-desk .stage.stack.top-left .box:nth-child(2),.onair-desk .stage.stack.top-left .box:nth-child(3),.onair-desk .stage.stack.bottom-left .box:nth-child(2),.onair-desk .stage.stack.bottom-left .box:nth-child(3){left:2.5%}
.onair-desk .stage.stack.top-right .box:nth-child(2),.onair-desk .stage.stack.top-right .box:nth-child(3),.onair-desk .stage.stack.bottom-right .box:nth-child(2),.onair-desk .stage.stack.bottom-right .box:nth-child(3){right:2.5%}
.onair-desk .stage.stack.top-left .box:nth-child(2),.onair-desk .stage.stack.top-right .box:nth-child(2){top:4.4%}
.onair-desk .stage.stack.top-left .box:nth-child(3),.onair-desk .stage.stack.top-right .box:nth-child(3){top:28.8%}
.onair-desk .stage.stack.bottom-left .box:nth-child(2),.onair-desk .stage.stack.bottom-right .box:nth-child(2){bottom:27.6%}
.onair-desk .stage.stack.bottom-left .box:nth-child(3),.onair-desk .stage.stack.bottom-right .box:nth-child(3){bottom:52%}
.onair-desk .stage.stack.corner-unknown .box:nth-child(2),.onair-desk .stage.stack.corner-unknown .box:nth-child(3){visibility:hidden}
.onair-desk .stage select{width:100%;min-width:0;padding:5px 7px;min-height:32px;font-size:13.5px}
.onair-desk .sources{display:grid;grid-template-columns:2.2fr 1fr 1fr;gap:8px;margin-top:10px}
.onair-desk .sources>div{display:flex;flex-direction:column;gap:4px;min-width:0}
.onair-desk .sources .tag{font-size:11.5px;font-weight:700;letter-spacing:.05em;text-transform:uppercase;color:var(--oa-sub)}
.onair-desk .stage.side{grid-template-columns:minmax(0,2.2fr) 42px minmax(0,1fr);column-gap:0}
.onair-desk .stage.side .box:first-child{grid-column:1}.onair-desk .stage.side .box:nth-child(2),.onair-desk .stage.side .box:nth-child(3){grid-column:3}
.onair-desk .stage .swap{position:absolute;width:34px;height:34px;min-height:34px;padding:0;border-radius:50%;background:var(--oa-card);border:1.5px solid var(--oa-accent);color:var(--oa-accent);font-size:15px;line-height:1;display:grid;place-items:center;z-index:2;transform:translate(-50%,-50%)}
.onair-desk .stage .swap:active:not(:disabled){transform:translate(-50%,-50%)}
.onair-desk .stage .swap:hover:not(:disabled){background:var(--oa-accent);color:#fff}
.onair-desk .stage.side .swap{left:calc(8px + (100% - 58px)*.6875 + 21px)}
.onair-desk .stage.side .swap-ms1{top:calc(25% + 2.5px)}.onair-desk .stage.side .swap-s1s2{top:50%}.onair-desk .stage.side .swap-ms2{top:calc(75% - 2.5px)}
.onair-desk .stage.stack.top-left .swap,.onair-desk .stage.stack.bottom-left .swap{left:calc(22.5% + 21px)}
.onair-desk .stage.stack.top-right .swap,.onair-desk .stage.stack.bottom-right .swap{left:calc(77.5% - 21px)}
.onair-desk .stage.stack.top-left .swap-ms1,.onair-desk .stage.stack.top-right .swap-ms1{top:14.4%}
.onair-desk .stage.stack.top-left .swap-s1s2,.onair-desk .stage.stack.top-right .swap-s1s2{top:26.6%}
.onair-desk .stage.stack.top-left .swap-ms2,.onair-desk .stage.stack.top-right .swap-ms2{top:38.8%}
.onair-desk .stage.stack.bottom-left .swap-ms1,.onair-desk .stage.stack.bottom-right .swap-ms1{top:62.4%}
.onair-desk .stage.stack.bottom-left .swap-s1s2,.onair-desk .stage.stack.bottom-right .swap-s1s2{top:50.2%}
.onair-desk .stage.stack.bottom-left .swap-ms2,.onair-desk .stage.stack.bottom-right .swap-ms2{top:38%}
.onair-desk .stage.stack.corner-unknown .swap{visibility:hidden}
.onair-desk .stage-note{margin:8px 2px 0}
.onair-desk .look-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px;margin-top:10px}
.onair-desk .look-grid button{display:flex;align-items:center;gap:9px;justify-content:flex-start;text-align:left;min-height:46px;font-size:13.5px}
.onair-desk .look-grid button[aria-pressed=true]{color:var(--oa-accent)}
.onair-desk .look-grid .mini{width:34px;height:22px}
.onair-desk .steps{display:flex;gap:8px;list-style:none;padding:0;margin:10px 0 6px}
.onair-desk .steps li{flex:1;border-top:3px solid var(--oa-border);padding-top:6px;font-size:13px;color:var(--oa-sub)}
.onair-desk .steps li.done{border-color:var(--oa-ok);color:var(--oa-ok)}
.onair-desk .steps li.done::after{content:' ✓';font-weight:800}
.onair-desk .steps li.active{border-color:var(--oa-accent);color:var(--oa-text);font-weight:800}
.onair-desk .chip{display:inline-block;font-size:12.5px;font-weight:700;padding:3px 10px;border-radius:99px;border:1px solid var(--oa-ok);color:var(--oa-ok)}
.onair-desk .scroll-hint{color:var(--oa-sub);font-size:12.5px;margin:0}
.onair-desk.compact{padding:var(--oa-s3);max-height:100%;overflow:auto;display:grid;gap:var(--oa-s2)}
.onair-desk.compact h1{font-size:17px}
.onair-desk.compact .statusbar{display:block;margin:0}
.onair-desk.compact .status-lead{gap:8px}
.onair-desk.compact .live-state{font-size:15px}
.onair-desk.compact .safety{display:grid;grid-template-columns:repeat(3,minmax(0,1fr))}
.onair-desk.compact .safety button{font-size:12.5px;padding:8px 2px}
.onair-desk.compact .banner{padding:7px 10px;font-size:13px;margin:0}
.onair-desk.compact .notice{padding:6px 9px;font-size:13px}
.onair-desk.compact .card{padding:var(--oa-s3)}
.onair-desk.compact .queue li{background:transparent;padding:10px 12px}
.onair-desk.compact .queue li:first-child{padding-left:8px}
.onair-desk.compact .stage{min-height:280px;padding:5px;gap:5px;max-width:none;border:0;background:transparent}
.onair-desk.compact .stage.stack .box:first-child{border:0;background:var(--oa-bg)}
.onair-desk.compact .stage .box{padding:3px}
.onair-desk.compact .stage .box .tag{font-size:10px;letter-spacing:0}
.onair-desk.compact .stage .box .srclabel{font-size:12px}
.onair-desk.compact .sources{display:none}
.onair-desk.compact .stage .swap{width:32px;height:32px;min-height:32px;font-size:13px}
@container (max-width:1000px){.onair-desk .columns,.onair-desk .views-grid{grid-template-columns:1fr}.onair-desk .statusbar{display:block}.onair-desk .safety{margin-top:10px}}
@media (prefers-reduced-motion:reduce){.onair-desk *{animation:none!important;transition:none!important}.onair-desk button:active{transform:none!important}}
`;

// One owner is shared by every mounted surface; in-flight SDK calls cannot be aborted.
export function createOwner(ctx) {
  const listeners = new Set();
  let value = { online:false, state:null, queue:[], views:null, busy:false, notice:'' };
  let generation = 0, lifetime = 0, timer = null, failures = 0, running = false, requests = 0, fullConsumers = 0;
  const emit = patch => { value = {...value, ...patch}; listeners.forEach(fn => fn()); };
  function schedule(delay) { clearTimeout(timer); if (running) timer = setTimeout(poll, delay); }
  async function poll() {
    const run = generation;
    try {
      const [state, pending, views] = await Promise.all([
        ctx.rest('/state', {timeoutMs:4000}), ctx.rest('/pending', {timeoutMs:4000}),
        ctx.rest('/views/state', {timeoutMs:4000}).catch(() => null)
      ]);
      if (!running || run !== generation) return;
      if (!state || !pending || !Array.isArray(pending.pending) || state.epoch !== pending.epoch) throw Error('Show state is unavailable or inconsistent.');
      failures = 0; emit({online:true, state, queue:pending.pending, views, ...(value.notice==='Request accepted; checking readback.'?{notice:'Request accepted; state refreshed.'}:{})});
    } catch (e) {
      if (!running || run !== generation) return;
      failures++; emit({online:false, state:null, queue:[], views:null, notice:e.message || 'Show unavailable.'});
    }
    if (running && run === generation) schedule(Math.min(30000, 2000 * 2 ** failures));
  }
  async function action(path, body, safety = false) {
    if (!running || !value.online || (!safety && requests)) return null;
    generation++; clearTimeout(timer); requests++;
    emit({busy:true, notice:'Request in progress…', ...(safety ? {queue:[]} : {}), ...((path==='/stop'||(path==='/privacy'&&body.on)) ? {draftRevision:(value.draftRevision||0)+1} : {})});
    const run = generation, life = lifetime;
    try {
      const result = await ctx.rest(path, {method:'POST',body,timeoutMs:8000});
      if (!running || life !== lifetime) return null;
      if (!result || result.ok !== true) throw Error(result && (result.next_human_action || result.error || result.message) || 'No confirmation from the show.');
      emit({notice:result.note || result.message || 'Request accepted; checking readback.'});
      return result;
    } catch(e) { if (running && life === lifetime) emit({notice:e.message || 'Request not confirmed.'}); return null; }
    finally { if (life === lifetime) { requests--; if (running) { emit({busy:requests>0}); if (!requests) { generation = Math.max(generation,run); schedule(0); } } } }
  }
  return {
    snapshot:()=>value, active:()=>running, hasFull:()=>fullConsumers>0, action,
    subscribe(fn, full=false) {
      listeners.add(fn); if (full) fullConsumers++;
      if (!running) { running=true; generation++; poll(); }
      return () => { if (!listeners.delete(fn)) return; if (full) fullConsumers--; if (!listeners.size) { running=false; generation++; lifetime++; requests=0; clearTimeout(timer); timer=null; value={...value,online:false,state:null,queue:[],views:null,busy:false}; } };
    }
  };
}
function mini(kind){ return h('span',{className:'mini m-'+kind,'aria-hidden':'true'},h('i',{className:'a'}),kind!=='full'?h('i',{className:'b'}):null); }
function Views({owner, view, compact}) {
  const v=view.views, connected=!!(v&&v.ok&&v.connected), boxes=connected&&v.boxes||{}, slots=boxes.slots, available=boxes.available||{};
  const [moveMessage,setMoveMessage]=React.useState('');
  const corners=['top-left','top-right','bottom-left','bottom-right'];
  const corner=corners.includes(boxes.inset_corner)?boxes.inset_corner:null;
  async function put(slot,source) {
    setMoveMessage('');
    const result=await owner.action('/views/boxes',{op:'put',slot,source});
    if (result&&result.move&&result.message) setMoveMessage(result.message);
  }
  const names={main:'Main',sub1:'Sub 1',sub2:'Sub 2'}, labels={scene:'Scene',slides:'Slides',window:'Window',empty:'Empty'};
  const blocked=!view.online||!connected||view.busy||(v&&v.stage_profile);
  const bad=!!(slots&&Object.values(slots).some(s=>s!=='empty'&&!(available[s]&&available[s].available)));
  const current=connected&&v.layout&&v.layout.current||{}, scenes=connected&&v.layout&&v.layout.scenes||{};
  const activeLook=connected&&(scenes.presentation&&v.program_scene===scenes.presentation.name?current.presentation:scenes.window&&v.program_scene===scenes.window.name?current.window:null);
  const liveBoxes=connected&&v.program_scene==='On Air: Boxes';
  async function look(preset) {
    const result=await owner.action('/views/layout',{preset});
    if (result && result.scene) await owner.action('/views/scene',{scene:result.scene});
  }
  const step=v&&v.window&&v.window.ready?2:0;
  const picker=slot=>compact?h('span',{className:'src'},slots?labels[slots[slot]]:'Unknown'):h('select',{'aria-label':names[slot]+' source',value:slots?slots[slot]:'',disabled:blocked||bad||!slots,onChange:e=>put(slot,e.target.value)},
    !slots&&h('option',{value:''},'Unavailable'),...['scene','slides','window','empty'].map(source=>h('option',{key:source,value:source,disabled:(source==='empty'&&slot==='main')||(source!=='empty'&&!(available[source]&&available[source].available))},labels[source])));
  const swap=(a,b,cls)=>h('button',{type:'button',className:'swap '+cls,'aria-label':'Swap '+names[a]+' and '+names[b],title:'Swap '+names[a]+' and '+names[b],disabled:blocked||bad||!slots||(a==='main'&&slots[b]==='empty'),onClick:()=>owner.action('/views/boxes',{op:'swap',a,b})},'⇄');
  const stage=h('div',{className:'stage '+(boxes.arrangement==='side'?'side':'stack')+' '+(corner||'corner-unknown'),'aria-label':'Box arrangement'},
    ...['main','sub1','sub2'].map(slot=>h('div',{key:slot,className:'box'},h('span',{className:'tag'},names[slot]),slots?h('span',{className:'srclabel'},labels[slots[slot]]):null)),
    swap('main','sub1','swap-ms1'),swap('main','sub2','swap-ms2'),swap('sub1','sub2','swap-s1s2'));
  const sources=h('div',{className:'sources'},...['main','sub1','sub2'].map(slot=>h('div',{key:slot},h('span',{className:'tag'},names[slot]),picker(slot))));
  const notes=(bad||Object.entries(available).some(([name,x])=>name!=='empty'&&!x.available))?h('div',null,
    bad&&h('p',{className:'helper stage-note'},'Swaps and sources are locked because a chosen source is not available right now.'),
    ...Object.entries(available).filter(([name,x])=>name!=='empty'&&!x.available).map(([name,x])=>h('p',{key:name,className:'helper stage-note'},(labels[name]||name)+' is unavailable: '+(x.reason||'not ready yet')+'.'))):null;
  return h('section',{className:'card'},h('h2',null,compact?'Stage':'Views'),
    connected?h('p',{className:'kicker'},'Scene on stream: '+(v.program_scene||'unknown scene')):h('p',{className:'notice'},v&&v.error||'The stream view is unavailable.'),
    connected&&boxes.arrangement==='stack'&&h('p',{className:'helper'},'Insets leave room for captions below.'),
    !slots&&!blocked&&h('div',{className:'row'},h('button',{type:'button',className:'primary',disabled:!(available.scene&&available.scene.available),onClick:()=>owner.action('/views/boxes',{op:'set',main:'scene',sub1:'empty',sub2:'empty',arrangement:'stack'})},'Set up boxes with Scene')),
    h('div',{className:compact?'stack':'views-grid'},
      compact?stage:h('div',{className:'stack'},stage,sources,moveMessage&&h('p',{className:'helper',role:'status'},moveMessage),notes),
      !compact&&h('div',{className:'stack'},
        h('div',null,h('h3',null,'Arrangement'),h('div',{className:'segments',role:'group','aria-label':'Arrangement'},
          h('button',{type:'button',disabled:blocked||bad||!slots,'aria-label':'Stack','aria-pressed':boxes.arrangement==='stack',onClick:()=>owner.action('/views/boxes',{op:'arrangement',arrangement:'stack'}),title:'Both sub boxes sit as insets over main'},mini('stack'),'Stack · insets'),
          h('button',{type:'button',disabled:blocked||bad||!slots,'aria-label':'Side','aria-pressed':boxes.arrangement==='side',onClick:()=>owner.action('/views/boxes',{op:'arrangement',arrangement:'side'}),title:'Main on the left, sub boxes in a column on the right'},mini('side'),'Side · column')),
        boxes.arrangement_source==='stored'&&h('p',{className:'helper'},'Arrangement remembered from the last confirmed layout.'),
        boxes.arrangement==='stack'&&h('div',null,h('h3',null,'Inset corner'),h('div',{className:'segments corners',role:'group','aria-label':'Inset corner'},
          ...corners.map(c=>h('button',{key:c,type:'button',disabled:blocked||bad||!slots,'aria-label':c.replace('-', ' ').replace(/^./,x=>x.toUpperCase()),'aria-pressed':corner===c,title:c.replace('-', ' '),onClick:()=>owner.action('/views/boxes',{op:'arrangement',arrangement:'stack',inset_corner:c})},mini('corner '+c))))),
        boxes.arrangement==='stack'&&!corner&&h('p',{className:'helper'},'Inset corner is unknown until readback.'),
        h('div',{className:'row',style:{marginTop:'12px'}},
          !liveBoxes&&h('button',{type:'button',className:'primary',disabled:blocked,onClick:()=>owner.action('/views/scene',{scene:'On Air: Boxes'})},'Show Boxes on stream'),
          liveBoxes&&h('span',{className:'chip'},'On stream now'))),
        h('div',null,h('h3',null,'Simple looks'),h('div',{className:'look-grid'},...[[ 'presentation','Slides','full'],['window','Shared window','win'],['window_pip','Window + slides inset','pip'],['side_by_side','Side by side','duo']].map(([preset,label,m])=>h('button',{key:preset,type:'button',disabled:blocked||!(v.layout&&v.layout.presets&&v.layout.presets[preset]&&v.layout.presets[preset].ready),'aria-pressed':!!(activeLook&&activeLook.preset===preset),onClick:()=>look(preset)},mini(m),label)))),
        h('div',null,h('h3',null,'Window sharing'),
          h('ol',{className:'steps'},h('li',{className:step>0?'done':step===0?'active':''},'1 · Choose a window'),h('li',{className:step>1?'done':step===1?'active':''},'2 · Confirm in OBS'),h('li',{className:step===2?'active':''},'3 · Show it')),
          h('p',{className:'helper'},connected?(v.window&&v.window.ready?'The chosen window is ready to show on stream.':'Choose and confirm a window in the owner session first; this desk cannot confirm it for you.'):'Connect to the show to share a window.'),
          h('div',{className:'row'},...[['discover','Find windows'],['show','Show window'],['hide','Hide window']].map(([action,label])=>h('button',{key:action,type:'button',className:(action==='show'&&step===2)||((action==='discover')&&step<2&&connected)?'primary':'',disabled:blocked||(action==='show'&&!(v.window&&v.window.ready)),onClick:()=>owner.action('/views/window',{action})},label)))))));
}
function offReason(state){ if(!state) return ' Sending is off because the show state was lost.'; if(state.privacy) return ' Sending is off while privacy is on.'; if(state.mode==='off_air') return ' Sending is off while the show is off air.'; return ' The show is not taking host lines right now.'; }
function CutIn({owner,view}) {
  const s=view.online&&view.state;
  const ready=!!(s && s.live===true && ['approval','autonomous'].includes(s.mode) && s.privacy===false && Number.isSafeInteger(s.epoch) && s.epoch>=0 && /^[0-9a-f]{32}$/.test(s.voice_boot));
  function cut() {
    const current=owner.snapshot(), state=current.online&&current.state;
    if (current.busy || !state || state.live!==true || !['approval','autonomous'].includes(state.mode) || state.privacy!==false || !Number.isSafeInteger(state.epoch) || state.epoch<0 || !/^[0-9a-f]{32}$/.test(state.voice_boot)) return;
    owner.action('/cut-in',{epoch:state.epoch,boot:state.voice_boot});
  }
  return h('div',null,h('button',{type:'button',disabled:!ready||view.busy,onClick:cut},'Cut in'),
    !ready&&h('p',{className:'helper'},'Cut in is unavailable while the show is private, stopped, paused, or disconnected.'));
}
function Host({owner,view}) {
  const [draft,setDraft]=React.useState({text:'',epoch:null,revision:0});
  const [message,setMessage]=React.useState('');
  const state=view.online&&view.state;
  const reason=!state?'show state was lost':state.privacy?'privacy is on':state.mode==='off_air'?'the show stopped':draft.epoch!==null&&(draft.epoch!==state.epoch||draft.boot!==state.voice_boot)?'the show epoch changed':draft.revision!==(view.draftRevision||0)?'privacy or stop was requested':'';
  const clears={'show state was lost':'The desk lost track of the show','privacy is on':'Privacy was turned on','the show stopped':'The show stopped','the show epoch changed':'The show changed since you started typing','privacy or stop was requested':'A safety action was used'};
  React.useEffect(()=>{if (draft.text&&reason) {setDraft({text:'',epoch:null,revision:view.draftRevision||0});setMessage((clears[reason]||'The show changed')+', so this line was cleared. Type it again.');}},[draft.text,reason,view.draftRevision]);
  const off=!state||state.privacy||state.mode==='off_air'||!Number.isSafeInteger(state.epoch)||!/^[0-9a-f]{32}$/.test(state.voice_boot)||!(state.host_turns&&state.host_turns.enabled);
  async function send() {
    const current=owner.snapshot();
    if (!draft.text.trim()||!current.online||!current.state||current.state.epoch!==draft.epoch||current.state.voice_boot!==draft.boot||!/^[0-9a-f]{32}$/.test(draft.boot)||current.state.privacy||current.state.mode==='off_air'||draft.revision!==(current.draftRevision||0)||!(current.state.host_turns&&current.state.host_turns.enabled)) return;
    const result=await owner.action('/host/turn',{text:draft.text,epoch:draft.epoch,boot:draft.boot,id:draft.id});
    if (result&&result.queued) {setDraft(d=>d.id===draft.id?{text:'',epoch:null,revision:current.draftRevision||0}:d);setMessage(result.message||'Sent. The co-host will work it in.');}
  }
  return h('section',{className:'card composer'},h('h2',null,'Your line'),h('p',{className:'kicker'},'Type a line and the co-host will work it into the show.'),
    h('label',null,'Line for the co-host',h('textarea',{value:reason?'':draft.text,maxLength:2000,disabled:off,onChange:e=>{const text=e.target.value;setDraft(d=>({text,epoch:d.text?d.epoch:state.epoch,boot:d.text?d.boot:state.voice_boot,revision:d.text?d.revision:(view.draftRevision||0),id:d.text?d.id:Array.from(window.crypto.getRandomValues(new Uint8Array(16)),b=>b.toString(16).padStart(2,'0')).join('')}));setMessage('');}})),
    h('div',{className:'composer-foot'},h('span',{className:'count'},draft.text.length+' / 2000 characters'),h('button',{type:'button',className:'primary',disabled:!!reason||!draft.text.trim()||view.busy||!(state&&state.host_turns&&state.host_turns.enabled===true),onClick:send},'Send to co-host')),
    h('p',{role:'status',className:'helper'},message,off&&h('span',null,offReason(state))),
    h('div',{'data-onair-slot':'host-voice',className:'voice'},'Hold-to-talk is in the dashboard’s On Air page, Talk section.'),
    h(CutIn,{owner,view}));
}
function Desk({owner, compact}) {
  const [view,setView] = React.useState(owner.snapshot());
  const [tab,setTab] = React.useState('Show');
  const [selected,setSelected] = React.useState(0);
  React.useEffect(() => owner.subscribe(() => setView(owner.snapshot()),!compact), [owner,compact]);
  const state = view.online && view.state, queue = view.online ? view.queue : [];
  function decide(kind,line) {
    const current=owner.snapshot(), s=current.online&&current.state;
    if (!s || current.busy || !line || line.epoch !== s.epoch || !current.queue.some(x=>x.line_id===line.line_id && x.epoch===line.epoch)) return;
    owner.action('/'+kind,{line_id:line.line_id,epoch:line.epoch});
  }
  function safety(kind) { const current=owner.snapshot(), s=current.online&&current.state; if (!s) return; owner.action('/'+kind,kind==='stop'?{}:{on:kind==='privacy'?!s.privacy:!s.muted},true); }
  function mode(next) {
    if (!state || state.mode===next) return;
    if (next==='autonomous' && (state.mode!=='approval' || !window.confirm('Autonomous mode releases guarded lines without desk approval. Continue?'))) return;
    owner.action('/mode',{mode:next});
  }
  React.useEffect(() => {
    function key(e) {
      if ((compact && owner.hasFull()) || e.defaultPrevented || e.repeat || e.ctrlKey || e.metaKey || e.altKey || (e.target && (e.target.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName)))) return;
      const k=e.key.toLowerCase();
      if (['p','m','escape'].includes(k)) { e.preventDefault(); safety(({p:'privacy',m:'mute',escape:'stop'})[k]); return; }
      if (!state || view.busy || !queue.length) return;
      if (compact) { if (k==='a') {e.preventDefault();decide('approve',queue[0]);} return; }
      if (k==='arrowdown'||k==='j') {e.preventDefault();setSelected(n=>Math.min(queue.length-1,n+1));}
      if (k==='arrowup'||k==='k') {e.preventDefault();setSelected(n=>Math.max(0,n-1));}
      if (k==='a'||k==='r') {e.preventDefault();decide(k==='a'?'approve':'reject',queue[Math.min(selected,queue.length-1)]);}
    }
    window.addEventListener('keydown',key);
    return ()=>window.removeEventListener('keydown',key);
  },[view,selected,compact]);
  const button=(label,onClick,disabled,extra={})=>h('button',{type:'button',onClick,disabled,...extra},label);
  const modeLabels={off_air:'Off air',approval:'Approval',autonomous:'Autonomous',paused:'Paused'};
  const modeHelp={off_air:'The show is stopped. Nothing goes out.',approval:'Each line waits here for your OK before it goes out.',autonomous:'Guarded lines can go out without waiting for your OK.',paused:'The co-host is holding. Nothing new goes out.'};
  const stateLabel=state?(state.privacy?'Privacy on':state.muted?'Muted':modeLabels[state.mode]||'Unknown'):'Desk unreachable';
  const stateClass=!state?'st-off':state.privacy||state.muted?'st-override':'st-'+state.mode;
  const waiting=h('section',{className:'card'},h('h2',null,(compact?'Next line':'Waiting lines')+(queue.length&&!compact?' · '+queue.length:'')),
    !queue.length?h('p',{className:'empty'},state?'Nothing waiting. Lines the co-host drafts will appear here for your OK.':'The show is unavailable, so nothing can be approved from here.'):
    h('ol',{className:'queue'},...(compact?queue.slice(0,1):queue).map((line,i)=>h('li',{key:line.line_id,'aria-current':i===selected?'true':undefined},
      h('span',{className:'who'},(i===0&&!compact?'Next · ':'')+({host:'Answers the host',chat:'Answers chat',beat:'On the beat'}[line.source]||'Co-host line')),
      h('p',{className:'line'},line.text),
      h('div',{className:'row'},button('Approve',()=>decide('approve',line),view.busy||line.epoch!==state.epoch,{className:i===0?'primary':''}),!compact&&button('Skip',()=>decide('reject',line),view.busy||line.epoch!==state.epoch,{className:'quiet'}))))));
  const modeCard=!compact&&h('section',{className:'card'},h('h2',null,'Mode'),
    h('div',{className:'segments grid2',role:'group','aria-label':'Mode'},...['off_air','approval','autonomous','paused'].map(m=>button(modeLabels[m],()=>mode(m),!state||view.busy||(m==='autonomous'&&state.mode!=='approval'),{key:m,'aria-pressed':!!state&&state.mode===m}))),
    h('p',{className:'helper mode-note'},state?modeHelp[state.mode]:'Connect to the show to pick a mode.'));
  const rundownCard=!compact&&h('section',{className:'card'},h('h2',null,'Rundown'),h('p',null,state&&state.rundown||'No rundown selected for this show.'));
  return h('section',{className:'onair-desk'+(compact?' compact':'')},h('style',null,styles),
    h('header',{className:'statusbar'},
      h('div',null,h('div',{className:'status-lead'},h('h1',null,state?'On Air':'Stream desk'),h('span',{className:'live-state '+stateClass},stateLabel)),
        h('p',{className:'helper'},h('span',{className:'connection'+(state?'':' off')},state?'Connected':'Connection: Unavailable'),' · Rundown: '+(state?(state.rundown||'None'):'Unknown'))),
      h('div',{className:'safety'},button(state&&state.privacy?'Privacy off (P)':'Privacy on (P)',()=>safety('privacy'),!state,{'aria-pressed':!!(state&&state.privacy)}),button(state&&state.muted?'Unmute (M)':'Mute (M)',()=>safety('mute'),!state,{'aria-pressed':!!(state&&state.muted)}),button('Stop (Esc)',()=>safety('stop'),!state,{className:'danger'}))),
    state&&state.model_failure_paused&&h('p',{role:'alert',className:'banner'},'Paused: the model is not answering'),
    state&&state.model_status&&h('p',{role:'status',className:'banner'},state.model_status),
    state&&state.privacy&&h('p',{className:'banner banner-privacy'},'Privacy is on — viewers see the cover'),
    state&&state.muted&&!state.privacy&&h('p',{className:'banner banner-muted'},'Muted — the co-host cannot be heard'),
    h('p',{role:'status',className:'notice'+(view.busy?' busy':'')},view.notice),
    compact&&h(CutIn,{owner,view}),
    compact&&h('p',{className:'scroll-hint'},'Stage below ↓'),
    !compact&&h('nav',{'aria-label':'Desk sections'},...['Show','Views','Host'].map(name=>button(name,()=>setTab(name),false,{key:name,'aria-pressed':tab===name}))),
    !compact&&tab==='Views'&&h(Views,{owner,view}),
    !compact&&tab==='Host'&&h(Host,{owner,view}),
    (compact||tab==='Show')&&h('div',{className:compact?'stack':'columns'},waiting,compact?h(Views,{owner,view,compact:true}):h('div',{className:'stack'},modeCard,rundownCard)));
}
const registrations = new WeakMap();
export default { id:'on-air', name:'On Air', defaultEnabled:false, register(ctx) {
  if (registrations.has(ctx)) return;
  const owner=createOwner(ctx);
  const items=[
    {id:'onair-page',area:ROUTES_AREA,data:{path:'/on-air'},render:()=>h(Desk,{owner})},
    {id:'onair-nav',area:SIDEBAR_NAV_AREA,data:{path:'/on-air',label:'On Air',codicon:'broadcast'}},
    {id:'onair-pane',area:'panes',title:'On Air',data:{placement:'right',width:'360px'},render:()=>h(Desk,{owner,compact:true})}
  ];
  if (typeof ctx.registerMany==='function') ctx.registerMany(items); else items.forEach(item=>ctx.register(item));
  registrations.set(ctx,owner); if (ctx.onDispose) ctx.onDispose(()=>registrations.delete(ctx));
}};
