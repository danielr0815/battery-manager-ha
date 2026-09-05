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
 assert.ok(html.includes('Root → Speicher &lt;script&gt;'));
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
