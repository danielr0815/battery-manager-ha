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
