const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const {modelSummary} = require('../../src/aiac/web/static/day_05.js');
const source = fs.readFileSync('src/aiac/web/static/day_05.js', 'utf8');
const reply = extra => ({status: 'completed', correct: true, text: '<script>x</script>',
  seconds: 2, cost_usd: 0.01, usage: {input_tokens: 10, output_tokens: 20, total_tokens: 30}, ...extra});
test('metrics sum cost and tokens; only replies contribute to mean time', () => {
  const summary = modelSummary([reply({}), reply({seconds: 4})]);
  assert.equal(summary.average, 3);
  assert.equal(summary.cost, 0.02);
  assert.deepEqual(summary.tokens, [20, 40, 60]);
  assert.equal(modelSummary([reply({cost_usd: null})]).cost, null);
  assert.equal(modelSummary([reply({}), {status: 'api_error', seconds: 9}]).average, 2);
  assert.equal(modelSummary([]).average, null);
});
class Element {
  constructor() {this.listeners = {}; this.children = []; this.value = ''; this.textContent = '';}
  addEventListener(k, fn) {this.listeners[k] = fn;}
  append(...items) {this.children.push(...items);}
  replaceChildren(...items) {this.children = items;}
  set innerHTML(_) {throw Error('unsafe HTML');}
}
function setup(options = {}) {
  const ids = ['form', 'fields', 'cost', 'stop', 'progress', 'status', 'summary', 'results',
    'findings', 'catalog-status', 'list', 'repeats'];
  for (const role of ['small', 'medium', 'flagship']) {
    ids.push(role, `${role}-info`);
  }
  const el = Object.fromEntries(ids.map(id => [`models-${id}`, new Element()]));
  el['models-form'].action = 'http://localhost/weeks/1/days/5';
  el['models-form'].reportValidity = () => true;
  el['models-repeats'].value = '1';
  const defaults = {small: 'gpt-6-luna', medium: 'gpt-6.1-sol', flagship: 'gpt-6-astra'};
  for (const [role, id] of Object.entries(defaults)) {
    el[`models-${role}`].value = id;
  }
  const requests = [];
  let resolve;
  vm.runInNewContext(source, {
    document: {getElementById: id => el[id], createElement: () => new Element()},
    fetch: async (url, request = {}) => {
      requests.push({url, body: request.body && JSON.parse(request.body)});
      if (url.endsWith('/models')) return {ok: !options.catalogError, json: async () => ({error: 'catalog failed',
        models: [...Object.values(defaults), 'older-model'].map(id => ({id, rates: null}))})};
      const body = JSON.parse(request.body);
      if (url.endsWith('/plan')) return {ok: true, json: async () => ({models: Object.fromEntries(['small', 'medium', 'flagship'].map(role => [role, {...body[role], effort: body[role].model === 'older-model' ? null : 'low'}])), calls: 3, repeats: 1,
        tasks: [{id: 'price', title: 'Покупка'}], runs: ['small', 'medium', 'flagship'].map(role => ({role, task: 'price', spec: body[role], repeat: 1}))})};
      if (options.wait) await new Promise(r => {resolve = r;});
      if (options.network) throw Error('offline');
      return {ok: true, json: async () => reply({...body, status: options.apiError ? 'api_error' : 'completed', model: body.spec.model})};
    },
  });
  return {el, requests, resolve: () => resolve(), submit: () => el['models-form'].listeners.submit({preventDefault() {}})};
}
const settle = () => new Promise(r => setImmediate(r));
test('loads dynamic catalog and accepts an older model without manual settings', async () => {
  const ui = setup(); await settle();
  ui.el['models-small'].value = 'older-model';
  ui.el['models-small'].listeners.change();
  await ui.submit();
  const plan = ui.requests.find(r => r.url.endsWith('/plan')).body;
  assert.equal(plan.small.model, 'older-model');
  assert.deepEqual(Object.keys(plan.small), ['model']);
  assert.match(ui.el['models-small-info'].textContent, /нет в справочнике/);
  assert.equal(ui.requests.filter(r => r.url.endsWith('/runs')).length, 3);
  assert.match(ui.el['models-findings'].textContent, /Настройки reasoning различаются/);
});
test('catalog failure does not substitute defaults or allow generation', async () => {
  const ui = setup({catalogError: true}); await settle(); await ui.submit();
  assert.equal(ui.requests.length, 1);
  assert.match(ui.el['models-catalog-status'].textContent, /Подмены/);
});
test('stop waits for current result, double submit is ignored', async () => {
  const ui = setup({wait: true}); await settle();
  const work = ui.submit(); await settle(); await ui.submit();
  ui.el['models-stop'].listeners.click(); ui.resolve(); await work;
  assert.equal(ui.requests.filter(r => r.url.endsWith('/runs')).length, 1);
  assert.equal(ui.el['models-fields'].disabled, false);
});
for (const option of ['network', 'apiError']) test(`${option} stops without retry`, async () => {
  const ui = setup({[option]: true}); await settle(); await ui.submit();
  assert.equal(ui.requests.filter(r => r.url.endsWith('/runs')).length, 1);
  assert.match(ui.el['models-status'].textContent, /прервана/);
});
