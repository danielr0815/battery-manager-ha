import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
const sourcePath = process.env.CASCADE_CARD_SOURCE || new URL('../../custom_components/battery_manager/frontend/battery-manager-forecast-card.js', import.meta.url);
const definitions = new Map();
const context = vm.createContext({ URL, Intl, Date, console, CustomEvent: class {constructor(type, options) {this.type=type;Object.assign(this, options);}},
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

test('consumption horizon clips boundary and partial slots without adding an hour',()=>{
 const C=definitions.get('battery-manager-consumption-card');
 for (const hours of [0.25,6]) {
  const c=new C();c._config={hours};c._width=800;c._hass={language:'en',config:{time_zone:'UTC'}};
  const base=Date.parse('2026-09-05T08:30:00Z');
  const points=Array.from({length:12},(_,i)=>({t:new Date(base+(i===0?0:i-.5)*3600000).toISOString(),ac_w:1000,duration_h:i===0?.5:1}));
  c._renderChart({attributes:{consumption_forecast:points}},key=>key);
  assert.equal((c._chartMeta.t1-c._chartMeta.t0)/3600000,hours);
  assert.ok(c._statsText.includes(`${hours.toFixed(1)}/0.0`));
 }
});

test('consumption day totals follow HA timezone across the repeated autumn hour',()=>{
 const C=definitions.get('battery-manager-consumption-card');const c=new C();
 c._config={hours:48};c._width=800;c._hass={language:'en',config:{time_zone:'Europe/Berlin'}};
 const base=Date.parse('2026-10-24T22:00:00Z');
 const points=Array.from({length:48},(_,i)=>({t:new Date(base+i*3600000).toISOString(),ac_w:1000,duration_h:1}));
 c._renderChart({attributes:{consumption_forecast:points}},key=>key);
 assert.ok(c._statsText.includes('25.0/23.0'));
});


test('rounded partial slots merge without hiding actual power changes or gaps',()=>{
 const c=card();
 // 492 W / 450 W, first two minutes rounded independently to 0.1 Wh.
 const cascade={schedule:[block(0,1/30,16.4,[activity('terminal',15,{source:'root'})]),
 block(1/30,31/30,491.7,[activity('terminal',450,{source:'root'})]),
 block(31/30,61/30,510,[activity('terminal',468,{source:'root'})]),
 block(3,4,510,[activity('terminal',468,{source:'root'})])]};
 const groups=c._groups(c._blocks(cascade));
 assert.equal(groups.length,3);assert.equal(groups[0].blocks.length,2);
 assert.equal(c._total(groups[0].blocks,'terminal'),465);
 const unknown=c._blocks({schedule:[block(0,1,10),block(1,2,10)]});
 unknown[0].root_input_wh=null;assert.equal(c._groups(unknown).length,2);
});

test('history uses the configured entity and never opens an invented entity',()=>{
 const c=card();c._hass.states={'sensor.soc':{attributes:{friendly_name:'Battery SOC'}},'sensor.out':{attributes:{}}};
 const cascade={member_details:[{load_id:'b1',history_entities:{soc:'sensor.soc',output:'sensor.out'}}]};
 assert.equal(c._historyEntity(cascade,'soc','b1'),'sensor.soc');
 assert.equal(c._historyEntity(cascade,'discharge','b1'),'sensor.out');
 assert.equal(c._historyEntity(cascade,'root'),null);
 const events=[];c.dispatchEvent=e=>events.push(e);
 c._openHistory('sensor.soc');c._openHistory('sensor.missing');
 assert.equal(events.length,1);assert.equal(events[0].type,'hass-more-info');
 assert.equal(events[0].detail.entityId,'sensor.soc');assert.equal(events[0].composed,true);
 assert.match(c._historyButton('sensor.soc','48 %'),/data-history="sensor.soc"/);
});

test('cursor outside a new forecast shows its valid period rather than a missing SOC',()=>{
 const c=card(), marker={innerHTML:'old'}, readout={textContent:''};
 c._plot({points:[{time:start,value:48},{time:start+3600000,value:60}],unit:'%',label:'SOC'},'B1','blue');
 c.shadowRoot.getElementById=id=>id.startsWith('marker')?marker:readout;
 c._showTime(start-1);assert.equal(marker.innerHTML,'');assert.ok(!readout.textContent.includes('—'));
 c._showTime(start+1800000);assert.match(readout.textContent,/54,0/);
});

test('activity tracks preserve partial intervals, merge contiguous bars and clip to the selected period',()=>{
 const c=card(), cascade={schedule:[block(0,2)],activity_intervals:[
 {kind:'charge',load_id:'b1',start:new Date(start).toISOString(),end:new Date(start+15*60000).toISOString(),exact:true},
 {kind:'charge',load_id:'b1',start:new Date(start+15*60000).toISOString(),end:new Date(start+30*60000).toISOString(),exact:true},
 {kind:'output',load_id:'b1',start:new Date(start+3600000).toISOString(),end:new Date(start+7200000).toISOString(),exact:false}]};
 const html=c._activityTracks(cascade,'b1','all');
 assert.match(html,/width:25%/);assert.match(html,/switching|Schaltzeiten unbekannt/);
 assert.equal((html.match(/class="activity-bar /g)||[]).length,2);
 c._window=()=>[start+3600000,start+7200000];
 assert.equal((c._activityTracks(cascade,'b1','today').match(/class="activity-bar /g)||[]).length,1);
});

test('small residuals use Wh and member details exclude unrelated energy flows',()=>{
 const c=card(),cascade={schedule:[block(0,1,105,[activity('charge',50,{stored_energy_wh:45}),activity('charge',50,{load_id:'b2',name:'Other',stored_energy_wh:45})])]};
 const all=c._flowList(c._blocks(cascade),cascade);
 assert.match(all,/5,0 Wh/);assert.match(all,/<details>/);
 const member=c._flowList(c._blocks(cascade),cascade,'b1');
 assert.ok(!member.includes('Other'));assert.ok(!member.includes('Bilanzrest'));
});


test('diagram click and Enter open history while arrow keys keep selecting forecast time',()=>{
 const c=card(),listeners={};c._hass.states['sensor.soc']={attributes:{}};
 const cascade={member_details:[{load_id:'b1',history_entities:{soc:'sensor.soc'},soc_forecast:[{t:new Date(start).toISOString(),soc:48},{t:new Date(start+3600000).toISOString(),soc:60}]}]};
 c._plot(c._series(cascade,'soc','b1','all'),'B1','blue');
 c.shadowRoot.getElementById=()=>({getBoundingClientRect:()=>({width:600,left:0}),addEventListener:(name,handler)=>listeners[name]=handler});
 const opened=[];let selected;c._openHistory=id=>opened.push(id);c._showTime=t=>selected=t;
 c._bindCharts();listeners.click();listeners.keydown({key:'Enter',preventDefault(){}});
 assert.deepEqual(opened,['sensor.soc','sensor.soc']);
 listeners.keydown({key:'ArrowRight',preventDefault(){}});assert.equal(selected,start+3600000);
 assert.equal(opened.length,2);
});


test('narrow grid charts keep twelve physical pixels for axis text',()=>{
 const c=card();c._charts=[{width:600}];const label={style:{}};
 c.shadowRoot.getElementById=()=>({getBoundingClientRect:()=>({width:300}),querySelectorAll:()=>[label]});
 c._sizeAxes();assert.equal(label.style.fontSize,'24px');
});

test('forecast explains a rejected start and pending confirmation without injecting HTML',()=>{
 const c=new (definitions.get('battery-manager-forecast-card'))();
 c.setConfig({entity:'sensor.test'});
 c.hass={language:'de',config:{time_zone:'Europe/Berlin'},states:{'sensor.test':{attributes:{
  forecast:[{t:new Date(start).toISOString(),soc:50},{t:new Date(start+3600000).toISOString(),soc:60}],
  loads:[{name:'Entfeuchter <script>',feedin_waiting_for_confirmation:true,not_before:new Date(start+2700000).toISOString(),
   rejected_candidates:[{start:new Date(start).toISOString(),reason:'daily_peak'}],schedule:[]}],
 }}}};
 assert.ok(c.shadowRoot.innerHTML.includes('Einspeisung wartet auf bestätigten Laststart'));
 assert.ok(c.shadowRoot.innerHTML.includes('Frühester Start nach Mindestpause'));
 assert.ok(c.shadowRoot.innerHTML.includes('Batterie-Tagesziel'));
 assert.ok(c.shadowRoot.innerHTML.includes('geprüfter Start verworfen'));
 assert.ok(!c.shadowRoot.innerHTML.includes('<script>'));
});

test('each activity hint shares the full axis and preserves its accessible description',()=>{
 const c=card(), cascade={schedule:[block(0,2)],activity_intervals:[
  {kind:'output',load_id:'b1',start:new Date(start).toISOString(),end:new Date(start+600000).toISOString(),exact:true},
  {kind:'output',load_id:'b1',start:new Date(start+6600000).toISOString(),end:new Date(start+7200000).toISOString(),exact:false},
 ]};
 const html=c._activityTracks(cascade,'b1','all');
 const pairs=[...html.matchAll(/aria-label="([^"]+)"><\/span><span class="activity-tip" aria-hidden="true">([^<]+)<\/span>/g)];
 assert.equal(pairs.length,2);
 for(const [,label,hint] of pairs) {assert.equal(hint,label);assert.match(label,/AC-Ausgang/);}
 assert.notEqual(pairs[0][1],pairs[1][1]);
});


test('cascade decisions include only own loads, translate reasons and escape text',()=>{
 const c=card();c._config.entity='sensor.test';
 c._hass.states['sensor.test']={attributes:{load_decisions:{
  b1:{name:'Akku <script>',rejected_candidates:[{start:new Date(start).toISOString(),reason:'daily_peak'}]},
  leaf:{name:'Endlast',waiting_for_confirmation:true,rejected_candidates:[{start:'invalid',reason:'additional_import'}]},
  unrelated:{name:'Fremd',waiting_for_confirmation:true},
 }}};
 const html=c._decisions({member_details:[{load_id:'b1'}],terminal_load_id:'leaf'});
 assert.match(html,/Planungsgründe/);assert.match(html,/Batterie-Tagesziel/);
 assert.match(html,/Einspeisung wartet auf bestätigten Laststart/);
 assert.match(html,/Akku &lt;script&gt;/);assert.ok(!html.includes('<script>'));
 assert.ok(!html.includes('Fremd'));assert.ok(!html.includes('Invalid Date'));
 assert.equal(c._decisions({terminal_load_id:'missing'}),'');
});

test('slot-average discharge can precede switching; shared cursor shows the actual planned state',()=>{
 const c=card(), cascade={schedule:[block(0,1,0,[activity('discharge',230)])],activity_intervals:[
  {kind:'discharge',load_id:'b1',start:new Date(start+30*60000).toISOString(),end:new Date(start+3600000).toISOString(),exact:true},
  {kind:'output',load_id:'b1',start:new Date(start).toISOString(),end:new Date(start+3600000).toISOString(),exact:false}]};
 const series=c._series(cascade,'discharge','b1','all','power');
 c._plot(series,'Battery','orange');
 const html=c._activityTracks(cascade,'b1','all');
 assert.equal((html.match(/class="activity-marker"/g)||[]).length,3);
 const elements=new Map();
 c.shadowRoot.getElementById=id=>{if(!elements.has(id))elements.set(id,{style:{},textContent:'',innerHTML:''});return elements.get(id);};
 c._showTime(start+15*60000);
 assert.equal(c._valueAt(series,start+15*60000),230);
 for(let i=0;i<3;i++)assert.equal(elements.get(`activity-marker-${i}`).style.left,'25%');
 assert.match(elements.get('activity-readout-0').textContent,/aus/);
 assert.match(elements.get('activity-readout-1').textContent,/aus/);
 assert.match(elements.get('activity-readout-2').textContent,/unbekannt/);
 assert.match(elements.get('readout-0').textContent,/230 W/);
 c._showTime(start+30*60000);
 assert.match(elements.get('activity-readout-1').textContent,/ein/);
 c._showTime(start+3600000);
 assert.equal(elements.get('activity-marker-1').hidden,true);
 assert.equal(elements.get('activity-readout-1').textContent,'');
 c._showTime(start-1);
 for(let i=0;i<3;i++)assert.equal(elements.get(`activity-marker-${i}`).hidden,true);
});

test('activity axes drive the shared cursor with scaled pointer positions and keyboard boundaries',()=>{
 for(const [width,left] of [[536,48],[268,-50]]) {
  const c=card(),cascade={schedule:[block(0,1)],activity_intervals:[
   {kind:'discharge',load_id:'b1',start:new Date(start+30*60000).toISOString(),end:new Date(start+45*60000).toISOString(),exact:true}]};
  c._activityTracks(cascade,'b1','all');
  const listeners={};
  c.shadowRoot.getElementById=id=>id==='activity-axis-1'?{getBoundingClientRect:()=>({width,left}),addEventListener:(name,fn)=>{listeners[name]=fn;}}:null;
  c._bindCharts();
  listeners.pointermove({clientX:left+width/2});
  assert.equal(c._cursorTime,start+30*60000);
  listeners.keydown({key:'ArrowRight',preventDefault(){}});
  assert.equal(c._cursorTime,start+45*60000);
  listeners.keydown({key:'ArrowLeft',preventDefault(){}});
  assert.equal(c._cursorTime,start+30*60000);
  listeners.keydown({key:'Home',preventDefault(){}});
  assert.equal(c._cursorTime,start);
  listeners.keydown({key:'End',preventDefault(){}});
  assert.equal(c._cursorTime,start+3600000);
  listeners.pointerdown({clientX:left-10});
  assert.equal(c._cursorTime,start);
  listeners.pointerdown({clientX:left+width+10});
  assert.equal(c._cursorTime,start+3600000);
 }
});

test('fine chart schedules retain more than 100 events and show zero power in leading and trailing pauses',()=>{
 const c=card(),cascade={schedule:[block(0,6,1200)],chart_resolution:'activity',
  chart_schedule:Array.from({length:120},(_,i)=>block(.5+i/30,.5+(i+1)/30,10)),
  member_details:[{load_id:'b1',soc_forecast:[{t:new Date(start).toISOString(),soc:50},{t:new Date(start+6*3600000).toISOString(),soc:50}]}]};
 const power=c._series(cascade,'root',null,'all','power');
 const energy=c._series(cascade,'root',null,'all','energy');
 assert.equal(power.blocks.length,120);
 assert.equal(c._valueAt(power,start),0);
 assert.equal(c._valueAt(power,start+5*3600000),0);
 assert.equal(c._valueAt(power,start+6*3600000),0);
 assert.equal(c._valueAt(energy,start+6*3600000),1.2);
 assert.equal(power.points[0].time,start);
 assert.equal(power.points.at(-1).time,start+6*3600000);
 assert.equal(power.points.at(-1).value,0);
 assert.equal(power.label,'Geplante Leistung');
});

test('cascade decisions show precise release time and translated minimum pause',()=>{
 const c=card();c._config={entity:'sensor.test'};
 c._hass.states['sensor.test']={attributes:{load_decisions:{leaf:{name:'Endlast',not_before:'2026-09-08T07:45:12Z',rejected_candidates:[{start:'2026-09-08T07:00:00Z',reason:'waiting for runtime release'}]}}}};
 const html=c._decisions({terminal_load_id:'leaf'});
 assert.match(html,/Frühester Start nach Mindestpause/);
 assert.match(html,/09:45:12/);
 assert.match(html,/Mindestpause noch nicht abgelaufen/);
});

test('daily report keeps missing data, boundaries and runtime coverage visible in both cards',()=>{
 for (const type of ['battery-manager-forecast-card','battery-manager-cascade-card']) {
  const c=new (definitions.get(type))(); c.setConfig({entity:'sensor.test'});
  const report={days:[null,{day:'2026-09-08',switch_requests:3,state_changes:2,gap_hours:.5,metrics:{pv:{planned_wh:1000,actual_wh:900,error_wh:-100,coverage_hours:2},'cascade_input:b1':{planned_wh:500,actual_wh:400,coverage_hours:1}},loads:{b1:{actual_run_hours:2,planned_run_hours:1,runtime_coverage_hours:3}},storage_soc:{b1:{soc_min_percent:50,soc_max_percent:70}}}],load_names:{b1:'B1 <script>'},dropped_events:2,last_error:'ValueError'};
  c.hass={language:'de',config:{time_zone:'Europe/Berlin'},states:{'sensor.test':{attributes:{operation_report:report,cascades:[],forecast:[]}}}};
  assert.ok(c.shadowRoot.innerHTML.includes('Tagesvergleich'),type);
  assert.ok(c.shadowRoot.innerHTML.includes('Durchleitung'),type);
  assert.ok(c.shadowRoot.innerHTML.includes('B1 &lt;script&gt;'),type);
  assert.ok(!c.shadowRoot.innerHTML.includes('<script>'),type);
  assert.ok(c.shadowRoot.innerHTML.includes('—'),type);
  assert.ok(c.shadowRoot.innerHTML.includes('Abdeckung Aktorzeit/Energie'),type);
  assert.ok(c.shadowRoot.innerHTML.includes('Aufzeichnungsfehler aufgetreten'),type);
  c.hass={...c._hass,language:'en'};
  assert.ok(c.shadowRoot.innerHTML.includes('Daily comparison'),type);
 }
});
