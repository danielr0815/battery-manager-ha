// Invoked by pytest with real backend payloads on stdin; no browser or device access.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
const input = JSON.parse(readFileSync(0, 'utf8'));
const definitions = new Map();
const context = vm.createContext({URL, Intl, Date, console,
  HTMLElement: class {attachShadow() {this.shadowRoot = {innerHTML:'', querySelectorAll:()=>[], getElementById:()=>null};}},
  ResizeObserver: class {observe() {} disconnect() {}},
  customElements: {get:k=>definitions.get(k), define:(k,v)=>definitions.set(k,v)}, window:{},
});
vm.runInContext(readFileSync(new URL('../../custom_components/battery_manager/frontend/battery-manager-forecast-card.js', import.meta.url), 'utf8').replaceAll('import.meta.url', '"https://example.test/card.js"'), context);
if (input.consumption) {
  const Card = definitions.get('battery-manager-consumption-card');
  const card = new Card();
  card._config = {hours:6}; card._width = 800;
  card._hass = {language:'de', config:{time_zone: input.time_zone}};
  card._renderChart({attributes:{consumption_forecast:input.consumption}}, key=>key);
  const meta = card._chartMeta;
  assert.equal(meta.t1 - meta.t0, 6 * 3600000);
  const energy = meta.totals.reduce((sum, watts, i)=>sum + watts * meta.durs[i], 0);
  assert.ok(Math.abs(energy - input.expected_wh) < 0.001, `${energy} Wh versus ${input.expected_wh} Wh`);
  assert.ok(meta.points.every(p=>p.time < meta.t1));
}
if (input.cascade) {
  const Card = definitions.get('battery-manager-cascade-card');
  const card = new Card();
  card._config={hours:48}; card._hass={language:'de',config:{time_zone:'Europe/Berlin'}};
  assert.equal(input.cascade.member_details[0].soc_percent, null);
  assert.equal(card._series(input.cascade,'soc','b1','all').points.length, 0);
}
if (input.timed_cascade) {
  const Card = definitions.get('battery-manager-cascade-card'), card = new Card();
  card._config={hours:48}; card._hass={language:'de',config:{time_zone:'Europe/Berlin'}};
  const cascade=input.timed_cascade, start=Date.parse(cascade.schedule[0].start);
  const value=(kind,id,minute,mode='power')=>card._valueAt(card._series(cascade,kind,id,'all',mode),start+minute*60000);
  assert.equal(value('soc','b1',20),60);
  assert.equal(value('soc','b1',30),60);
  assert.equal(value('soc','b1',37.5),56);
  assert.equal(value('soc','b1',50),52);
  assert.equal(value('soc','b2',7.5),54.5);
  assert.equal(value('soc','b2',50),59);
  assert.equal(value('soc','b2',56.25),57);
  assert.equal(value('discharge','b1',20),0);
  assert.equal(value('discharge','b1',30),320);
  const power=card._series(cascade,'discharge','b1','all','power');
  assert.deepEqual(Array.from(power.points.filter(p=>p.time===start+30*60000),p=>p.value),[0,320]);
  assert.deepEqual(Array.from(power.points.filter(p=>p.time===start+45*60000),p=>p.value),[320,0]);
  assert.equal(value('discharge','b1',45),0);
  assert.equal(value('charge','b2',10),400);
  assert.equal(value('charge','b2',15),0);
  assert.equal(value('root',null,10),630);
  assert.equal(value('root',null,20),0);
  assert.equal(value('terminal',null,20),0);
  assert.equal(value('terminal',null,37.5),300);
  assert.equal(value('terminal',null,50),0);
  assert.equal(value('terminal',null,55),280);
  assert.equal(value('discharge','b1',20,'energy'),0);
  assert.equal(value('discharge','b1',37.5,'energy'),.04);
  assert.equal(value('discharge','b1',60,'energy'),.08);
  assert.equal(value('root',null,60,'energy'),.1575);
  assert.equal(value('terminal',null,60,'energy'),.16);
  // Clipping inside a pause must neither prorate full-slot Wh nor move a ramp.
  card._window=()=>[start+20*60000,start+40*60000];
  assert.equal(value('discharge','b1',20,'energy'),0);
  assert.ok(Math.abs(value('discharge','b1',40,'energy')-320/6/1000)<1e-9);
  assert.equal(value('soc','b1',20),60);
  assert.ok(Math.abs(value('soc','b1',40)-(60-8*2/3))<1e-9);
}
if (input.loads_attributes) {
  const Card=definitions.get('battery-manager-loads-card'), card=new Card();
  card.setConfig({entity:'sensor.forecast',hours:96});
  card.hass={language:'de',config:{time_zone:input.time_zone},states:{'sensor.forecast':{attributes:input.loads_attributes}}};
  const load=card._cascades()[0];
  assert.equal(load.load_id,input.expected_load_id);
  const energy=card._total(card._blocks(load),'root');
  assert.ok(Math.abs(energy-input.expected_wh)<.001, `${energy} versus ${input.expected_wh}`);
  assert.match(card.shadowRoot.innerHTML,/Planungsleistung/);
  assert.match(card.shadowRoot.innerHTML,/Ladeziel/);
}
