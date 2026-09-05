import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
const sourcePath = process.env.CASCADE_CARD_SOURCE || new URL('../../custom_components/battery_manager/frontend/battery-manager-forecast-card.js', import.meta.url);
const definitions = new Map();
const context = vm.createContext({ URL, Intl, Date, console,
 HTMLElement: class { attachShadow() { this.shadowRoot = { innerHTML:'', querySelectorAll:()=>[], getElementById:()=>null }; } },
 ResizeObserver:class {observe(){} disconnect(){}},
 customElements:{get:(k)=>definitions.get(k),define:(k,v)=>definitions.set(k,v)},window:{},
});
vm.runInContext(readFileSync(sourcePath,'utf8').replaceAll('import.meta.url', '"https://example.test/card.js"'),context);
const Card = definitions.get('battery-manager-cascade-card');
const card = () => {const c=new Card();c._config={hours:48};c._hass={language:'de',config:{time_zone:'Europe/Berlin'},states:{}};return c;};
const start=Date.parse('2026-09-05T22:00:00Z');
const block=(a,b,root=100,activities=[])=>({start:new Date(start+a*3600000).toISOString(),end:new Date(start+b*3600000).toISOString(),root_input_wh:root,activities});
const activity=(kind,energy,extra={})=>({kind,load_id:'b1',name:'Speicher <script>',energy_wh:energy,...extra});

test('partial slots produce watts, exact cumulative energy and correct boundary values',()=>{
 const c=card(),cascade={schedule:[block(0,.5,150),block(1,2,200)]};
 const power=c._series(cascade,'root',null,'all','power');
 assert.equal(c._valueAt(power,start+15*60000),300);
 assert.equal(c._valueAt(power,start+30*60000),0);
 assert.equal(c._valueAt(power,start+60*60000),200);
 const energy=c._series(cascade,'root',null,'all','energy');
 assert.equal(c._valueAt(energy,start+15*60000),.075);
 assert.equal(c._valueAt(energy,start+45*60000),.15);
 assert.equal(c._valueAt(energy,start+120*60000),.35);
});

test('HA timezone day boundaries include both DST transitions',()=>{
 const c=card();
 assert.equal(c._dayStart('2026-03-30')-c._dayStart('2026-03-29'),23*3600000);
 assert.equal(c._dayStart('2026-10-26')-c._dayStart('2026-10-25'),25*3600000);
 assert.equal(c._day(Date.parse('2026-09-05T22:30:00Z')),'2026-09-06');
});

test('day clipping prorates energy without changing power',()=>{
 const c=card();c._window=()=>[start+15*60000,start+45*60000];
 const cascade={schedule:[block(0,1,400)]};
 assert.equal(c._total(c._blocks(cascade,'today'),'root'),200);
 const power=c._series(cascade,'root',null,'today','power');
 assert.equal(c._valueAt(power,start+30*60000),400);
});

test('configured horizon limits graphs and energy totals',()=>{
 const c=card();c._config.hours=6;
 const cascade={schedule:[block(0,12,1200)]};
 assert.equal(c._total(c._blocks(cascade),'root'),600);
 assert.equal(c._series(cascade,'root',null,'all','energy').points.at(-1).value,.6);
});

test('charge, stored energy, withdrawal and terminal delivery remain separate',()=>{
 const c=card(),cascade={schedule:[block(0,1,120,[activity('charge',100,{stored_energy_wh:90}),activity('discharge',60),activity('terminal',40,{source:'aux',source_load_id:'b1',source_name:'Speicher <script>',name:'Endlast'}),activity('terminal',20,{source:'root',name:'Endlast'}),activity('output',undefined)])]};
 const blocks=c._blocks(cascade);
 assert.equal(c._total(blocks,'charge','b1'),100);
 assert.equal(c._total(blocks,'charge','b1','stored_energy_wh'),90);
 assert.equal(c._total(blocks,'discharge','b1'),60);
 assert.equal(c._total(blocks,'terminal'),60);
 assert.equal(c._total(blocks,'aux'),40);
 const html=c._flowList(blocks,cascade);
 assert.ok(html.includes('Eingang → Speicher &lt;script&gt;'));
 assert.ok(html.includes('Speicher &lt;script&gt; → Endlast'));
 assert.ok(!html.includes('<script>'));
});

test('missing energies and missing SOC stay unknown',()=>{
 const c=card(),cascade={member_details:[{load_id:'b1',soc_percent:30}],schedule:[block(0,1,undefined,[activity('charge',100)])]};
 delete cascade.schedule[0].root_input_wh;
 assert.equal(c._total(c._blocks(cascade),'root'),null);
 assert.equal(c._kwh(null),null);
 assert.equal(c._total(c._blocks(cascade),'charge','b1','stored_energy_wh'),null);
 assert.equal(c._series(cascade,'soc','b1','all').points.length,0);
 assert.equal(c._series(cascade,'root',null,'all','power').points.length,0);
});

test('SOC interpolation agrees with the drawn curve and never extrapolates',()=>{
 const c=card();const points=[{time:start,value:20},{time:start+3600000,value:40}];
 assert.equal(c._socAt(points,start+1800000),30);
 assert.equal(c._socAt(points,start-1),null);
 assert.equal(c._socAt(points,start+3600001),null);
});

test('render supports empty, malformed and escaped inputs without creating fake forecasts',()=>{
 const c=card();c._config.entity='sensor.test';
 c._hass.states['sensor.test']={attributes:{cascades:[{name:'<img src=x onerror=alert(1)>',schedule:[null,{start:'bad',end:'bad'}],member_details:[{load_id:'x',name:'<script>'}]}]}};
 c._render();assert.ok(c.shadowRoot.innerHTML.includes('&lt;img'));
 assert.ok(!c.shadowRoot.innerHTML.includes('<img src=x'));
 assert.ok(c.shadowRoot.innerHTML.includes('Keine Prognose'));
});

test('naive backend times use HA timezone instead of the browser timezone',()=>{
 const c=card();
 assert.equal(c._timestamp('2026-09-06T00:00:00'),start);
 assert.equal(c._timestamp('2026-09-06T00:00:00+02:00'),start);
 assert.equal(c._timestamp(null),NaN);
});

test('agenda merges identical phases but preserves changing source and power',()=>{
 const c=card(), cascade={schedule:[block(0,1,100,[activity('terminal',100,{source:'root'})]),block(1,2,100,[activity('terminal',100,{source:'root'})]),block(2,3,200,[activity('terminal',200,{source:'root'})]),block(3,4,0,[activity('terminal',200,{source:'aux',source_load_id:'b1'})])]};
 const groups=c._groups(c._blocks(cascade));
 assert.equal(groups.length,3);
 assert.equal(groups[0].end,start+2*3600000);
 assert.equal(c._total(groups[0].blocks,'terminal'),200);
});

test('SOC cannot leak beyond configured horizon into a later day',()=>{
 const c=card();c._config.hours=6;c._window=()=>[start+24*3600000,start+48*3600000];
 const cascade={member_details:[{load_id:'b1',soc_forecast:[{t:new Date(start).toISOString(),soc:20},{t:new Date(start+48*3600000).toISOString(),soc:80}]}]};
 assert.equal(c._series(cascade,'soc','b1','tomorrow').points.length,0);
});

test('fractional seconds survive HA-local conversion',()=>{
 const c=card();assert.equal(c._timestamp('2026-09-06T00:00:00.123'),start+123);
});

test('unknown terminal energy cannot be relabelled as AC overhead',()=>{
 const c=card(),cascade={schedule:[block(0,1,300,[activity('terminal',undefined,{source:'root'})])]};
 assert.ok(!c._flowList(c._blocks(cascade),cascade).includes('Rundungsrest'));
});

test('pointer selection stays aligned when charts are scaled or horizontally scrolled',()=>{
 for (const [width,left] of [[894,30],[594,30],[300,-70]]) {
  const c=card(),listeners={};
  const series=c._series({schedule:[block(0,1,100),block(1,2,200)]},'root',null,'all','power');
  c._plot(series,'Root','blue');
  c.shadowRoot.getElementById=()=>({
   getBoundingClientRect:()=>({width,left}),
   addEventListener:(name,handler)=>{listeners[name]=handler;},
  });
  let selected;
  c._showTime=time=>{selected=time;};
  c._bindCharts();
  // The time-axis midpoint must select the slot boundary at every CSS
  // size, including an SVG shifted by horizontal scrolling.
  listeners.pointermove({clientX:left+width*(48+(600-48-16)/2)/600});
  assert.equal(selected,start+3600000);
  assert.equal(c._valueAt(series,selected),200);
  listeners.pointerdown({clientX:left});
  assert.equal(selected,start);
  listeners.pointerdown({clientX:left+width});
  assert.equal(selected,start+7200000);
 }
});

test('shared period selector precedes every chart and the planned sequence',()=>{
 const c=card(),cascade={name:'Test',schedule:[block(0,1,100)]};
 const view=c._ui(cascade,0);
 view.period='all';view.detail={kind:'root'};
 const html=c._renderCascade(cascade,0);
 const selector=html.indexOf('<div class="period">');
 assert.ok(selector>=0);
 for(const section of ['<div class="members">','<article class="terminal">','<section class="details"','<ol class="agenda">']) {
  assert.ok(html.indexOf(section)>selector,section);
 }
 assert.equal((html.match(/data-action="period"/g)||[]).length,3);
 assert.match(html,/data-action="period" data-period="all" aria-pressed="true"/);
});

test('all cards react to a UI language change without a sensor update',()=>{
 for(const [type,de,en] of [
  ['battery-manager-forecast-card','Warte auf den ersten Planungslauf','Waiting for the first planning run'],
  ['battery-manager-consumption-card','Keine Verbrauchsprognose','No consumption forecast'],
  ['battery-manager-cascade-card','Wiederaufladung ausstehend','recharge pending'],
 ]) {
  const c=new (definitions.get(type))();
  c.setConfig({entity:'sensor.test'});
  const states={'sensor.test':{attributes:{cascades:[{phase:'recovering',schedule:[block(0,1,100)]}]}}};
  const hass={language:'de-DE',config:{time_zone:'Europe/Berlin'},states};
  c.hass=hass;
  assert.ok(c.shadowRoot.innerHTML.includes(de),type);
  c.hass={...hass,language:'en-GB'};
  assert.ok(c.shadowRoot.innerHTML.includes(en),type);
  assert.ok(!c.shadowRoot.innerHTML.includes(de),type);
  c.hass={...hass,language:'de'};
  assert.ok(c.shadowRoot.innerHTML.includes(de),type);
 }
});

test('cascade title, root details and every backend phase are bilingual',()=>{
 const translations=Object.fromEntries(['de','en'].map(lang=>[lang,JSON.parse(readFileSync(new URL(`../../custom_components/battery_manager/translations/${lang}.json`,import.meta.url),'utf8'))]));
 for(const language of ['de','en']){
  const c=card();c._config.entity='sensor.test';c._hass.language=language;
  const cascade={phase:'recovering',schedule:[block(0,1,100)],member_details:[]};
  c._hass.states['sensor.test']={attributes:{cascades:[cascade]}};
  const view=c._ui(cascade,0);view.period='all';view.detail={kind:'root'};
  c._render();
  assert.ok(c.shadowRoot.innerHTML.includes(language==='de'?'Battery Manager Kaskaden':'Battery Manager Cascades'));
  assert.ok(c.shadowRoot.innerHTML.includes(language==='de'?'Eingang → Kaskade':'Root → cascade'));
  assert.ok(!c.shadowRoot.innerHTML.includes(language==='de'?'charging storage':'Root → Kaskade'));
  for(const phase of Object.keys(translations[language].entity.sensor.cascade_mode.state)){
   cascade.phase=phase;c._render();
   assert.ok(!c.shadowRoot.innerHTML.includes('cascade_phase_'),phase);
   assert.ok(!c.shadowRoot.innerHTML.includes(language==='de'?'Status unbekannt':'unknown status'),phase);
  }
  cascade.fault='safe_off_failed:some technical reason';c._render();
  assert.ok(c.shadowRoot.innerHTML.includes(language==='de'?'Sicherheitsabschaltung fehlgeschlagen':'Safety shutdown failed'));
  assert.ok(!c.shadowRoot.innerHTML.includes('some technical reason'));
  c._config.title='My own title';c._render();
  assert.ok(c.shadowRoot.innerHTML.includes('header="My own title"'));
 }
});

test('picker and editor labels follow HA language rather than browser language',()=>{
 context.navigator={language:'en-US'};
 for(const language of ['de','en']) {
  context.document={querySelector:()=>({hass:{language}})};
  const names=context.window.customCards.map(c=>c.name);
  assert.ok(names.includes(language==='de'?'Battery Manager Kaskaden':'Battery Manager Cascades'));
  for(const Card of definitions.values()) {
   const form=Card.getConfigForm();
   assert.equal(form.computeLabel({name:'hours'}),language==='de'?'Prognosezeitraum (Stunden)':'Forecast horizon (hours)');
  }
 }
 delete context.document;delete context.navigator;
});

test('frontend dictionaries have matching keys and unsupported languages use English',()=>{
 const strings=vm.runInContext('STRINGS',context);
 assert.deepEqual(Object.keys(strings.de).sort(),Object.keys(strings.en).sort());
 assert.equal(vm.runInContext('localize({language:"fr"},"card_cascade")',context),'Battery Manager Cascades');
});
