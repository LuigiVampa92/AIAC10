const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const {temperatureSummary} = require('../../src/aiac/web/static/day_04.js');
const source = fs.readFileSync('src/aiac/web/static/day_04.js', 'utf8');
const reply = extra => ({status: 'completed', text: 'raw', format_valid: true,
  correct: true, normalized: '4494', names: [], seconds: 1,
  usage: {input_tokens: 2, output_tokens: 3, total_tokens: 5}, ...extra});
test('text variation is independent of final answer; unavailable metrics remain unknown', () => {
  const stats = temperatureSummary([reply({text: 'first'}), reply({text: 'second'})]);
  assert.equal(stats.uniqueTexts, 2);
  assert.equal(stats.uniqueAnswers, 1);
  assert.equal(temperatureSummary([reply({correct: false})]).wrong, 1);
  assert.deepEqual(stats.tokens, [4, 6, 10]);
  assert.equal(stats.seconds, 2);
  assert.deepEqual(temperatureSummary([reply({usage: null})]).tokens, [null, null, null]);
  assert.equal(temperatureSummary([]).seconds, null);
});
test('API errors are not replies or format violations; incomplete and unknown stay separate', () => {
  const stats = temperatureSummary([reply({format_valid: false, correct: false}),
    {status: 'api_error'}, {status: 'unknown'}, reply({status: 'incomplete'})]);
  assert.equal(stats.replies, 2);
  assert.equal(stats.formatErrors, 1);
  assert.equal(stats.errors, 1);
  assert.equal(stats.unknown, 1);
  assert.equal(stats.incomplete, 1);
});
test('names are counted across valid responses only', () => {
  const stats = temperatureSummary([reply({names: ['a', 'b'], normalized: ['a', 'b']}),
    reply({names: ['b', 'c'], normalized: ['b', 'c']}),
    reply({format_valid: false, names: ['bad']})]);
  assert.equal(stats.uniqueNames, 3);
  assert.equal(stats.uniqueAnswers, 2);
});
class Element {
  constructor() {this.children = []; this.listeners = {}; this.textContent = '';}
  addEventListener(k, fn) {this.listeners[k] = fn;}
  append(...items) {this.children.push(...items);}
  replaceChildren(...items) {this.children = items;}
  set innerHTML(_) {throw Error('unsafe HTML');}
}
function setup(options = {}) {
  const ids = ['form', 'fields', 'cost', 'stop', 'progress', 'status', 'summary', 'results', 'findings'];
  const elements = Object.fromEntries(ids.map(id => [`temperature-${id}`, new Element()]));
  const form = elements['temperature-form'];
  form.action = 'http://localhost/weeks/1/days/4';
  form.reportValidity = () => true;
  form.elements = {task: {value: 'price'}, temperature: {value: '0'}, repeats: {value: '2'}};
  const requests = [];
  let resolve;
  vm.runInNewContext(source, {
    document: {getElementById: id => elements[id], createElement: () => new Element()},
    FormData: class {
      constructor(target) {
        assert(!elements['temperature-fields'].disabled);
        this.entries = Object.entries(target.elements).map(([k, v]) => [k, v.value]);
      }
      [Symbol.iterator]() {return this.entries[Symbol.iterator]();}
    },
    fetch: async (url, request) => {
      const body = JSON.parse(request.body);
      requests.push(body);
      if (url.endsWith('/plan')) return {ok: !options.invalidPlan, json: async () => ({
        error: 'invalid', model: 'test', calls: body.repeats,
        runs: Array.from({length: body.repeats}, (_, i) => ({task: 'price', temperature: 0, repeat: i + 1})),
      })};
      if (options.wait) await new Promise(r => {resolve = r;});
      if (options.network) throw Error('offline');
      return {ok: true, json: async () => reply({...body, status: options.apiError ? 'api_error' : 'completed', text: '<script>x</script>'})};
    },
  });
  return {elements, requests, resolve: () => resolve(), submit: () => form.listeners.submit({preventDefault() {}})};
}
test('configurable repeats are sent as number; responses grouped and zero-temperature observation shown', async () => {
  const ui = setup();
  await ui.submit();
  assert.equal(ui.requests[0].repeats, 2);
  assert.equal(ui.requests.length, 3);
  assert.equal(ui.elements['temperature-results'].children[0].children[1].children.length, 2);
  assert.match(ui.elements['temperature-findings'].textContent, /все тексты совпали/);
  assert.equal(ui.elements['temperature-fields'].disabled, false);
});
test('stop and double submit do not send another call', async () => {
  const ui = setup({wait: true});
  const work = ui.submit();
  await new Promise(r => setImmediate(r));
  await ui.submit();
  ui.elements['temperature-stop'].listeners.click();
  ui.resolve();
  await work;
  assert.equal(ui.requests.length, 2);
  assert.match(ui.elements['temperature-status'].textContent, /остановлена/);
});
for (const option of ['network', 'apiError', 'invalidPlan']) test(`${option} stops without retry`, async () => {
  const ui = setup({[option]: true});
  await ui.submit();
  assert.equal(ui.requests.length, option === 'invalidPlan' ? 1 : 2);
  assert.equal(ui.elements['temperature-fields'].disabled, false);
});
