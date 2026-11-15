const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('src/aiac/web/static/week_02_day_01.js', 'utf8');
class Element {
  constructor() {this.listeners = {}; this.children = []; this.value = 'initial'; this.textContent = '';}
  addEventListener(k, fn) {this.listeners[k] = fn;}
  append(...items) {this.children.push(...items);}
  replaceChildren(...items) {this.children = items;}
  set innerHTML(_) {throw Error('unsafe HTML');}
}
function setup(options = {}) {
  const ids = ['form', 'prompt', 'examples', 'submit', 'progress', 'status', 'answer', 'result-info', 'steps', 'compare', 'comparison', 'comparison-summary', 'tool-fixed_exchange', 'tool-calculator', 'tool-current_time'];
  const el = Object.fromEntries(ids.map(id => [`agent-${id}`, new Element()]));
  for (const name of ['fixed_exchange', 'calculator', 'current_time']) el['agent-tool-' + name].checked = true;
  el['agent-form'].action = 'http://localhost/weeks/2/days/1';
  el['agent-form'].reportValidity = () => true;
  el['agent-examples'].textContent = JSON.stringify({none: 'no tools', one: 'one tool', two: 'two tools'});
  const example = new Element(); example.dataset = {agentExample: 'two'};
  const requests = []; let resolve;
  vm.runInNewContext(source, {
    document: {getElementById: id => el[id], querySelectorAll: () => [example], createElement: () => new Element()},
    fetch: async (url, request) => {
      requests.push({url, body: JSON.parse(request.body)});
      if (options.wait) await new Promise(r => {resolve = r;});
      if (options.network) throw Error('offline');
      return {ok: !options.rejected, json: async () => options.results ? options.results[requests.length - 1] : ({error: 'configuration error',
        status: 'completed', text: '<script>answer</script>', model: 'test', seconds: 1,
        steps: [{number: 1, status: 'completed', seconds: 1, usage: null,
          tools: [{name: 'calculator', arguments: '{"expression":"2+2"}', output: {ok: true, result: {value: '4'}}}],
          request: {model: 'test'}, response: {output: []}}]})};
    },
  });
  return {el, example, requests, resolve: () => resolve(), submit: () => el['agent-form'].listeners.submit({preventDefault() {}})};
}
test('manual run renders answer and tool trace without adding comparison rows', async () => {
  const ui = setup();
  await ui.submit();
  assert.equal(ui.requests.length, 1);
  assert.equal(ui.el['agent-answer'].textContent, '<script>answer</script>');
  assert.equal(ui.el['agent-steps'].children.length, 1);
  assert.equal(ui.el['agent-comparison'].children.length, 0);
});
test('double submit is ignored and controls restore after completion', async () => {
  const ui = setup({wait: true}); const work = ui.submit();
  await new Promise(r => setImmediate(r)); await ui.submit();
  assert.equal(ui.requests.length, 1);
  assert.equal(ui.el['agent-submit'].disabled, true);
  ui.resolve(); await work;
  assert.equal(ui.el['agent-submit'].disabled, false);
  assert.equal(ui.el['agent-progress'].hidden, true);
});
for (const option of ['network', 'rejected']) test(`${option} does not retry or replace old answer`, async () => {
  const ui = setup({[option]: true}); ui.el['agent-answer'].textContent = 'previous';
  await ui.submit();
  assert.equal(ui.requests.length, 1);
  assert.equal(ui.el['agent-answer'].textContent, 'previous');
  assert.match(ui.el['agent-status'].textContent, /Автоматического повтора нет/);
});

test('manual subset and auto all/none use the same prompt and preserve both results', async () => {
  const ui = setup();
  ui.el['agent-tool-current_time'].checked = false;
  await ui.submit();
  assert.deepEqual(ui.requests[0].body.tools, ['fixed_exchange', 'calculator']);
  await ui.el['agent-compare'].listeners.click();
  assert.deepEqual(ui.requests.slice(1).map(r => r.body.tools),
    [['fixed_exchange', 'calculator', 'current_time'], []]);
  assert.equal(new Set(ui.requests.map(r => r.body.prompt)).size, 1);
  assert.equal(ui.el['agent-comparison'].children.length, 2);
});

test('comparison shows only JSON validation and calculates resource ratios', async () => {
  const result = (tools, seconds, tokens, count) => ({
    tools, seconds, usage: {input_tokens: tokens - 10, output_tokens: 10, total_tokens: tokens},
    status: 'completed', model: 'same', tool_calls: count - 1,
    check: {json: true}, steps: Array.from({length: count}, () => ({})), text: '{}',
  });
  const ui = setup({results: [
    result(['calculator'], 6, 1000, 5), result([], 2, 200, 1),
  ]});
  await ui.el['agent-compare'].listeners.click();
  assert.match(ui.el['agent-comparison-summary'].textContent, /3.00×/);
  assert.match(ui.el['agent-comparison-summary'].textContent, /5.00×/);
  const cells = ui.el['agent-comparison'].children[0].children;
  assert.equal(cells[6].textContent, '5');
  assert.equal(cells[7].textContent, '4');
  assert.equal(cells[8].textContent, '✓');
});
test('missing metrics are not zero and failed pairs have no ratios', async () => {
  const ui = setup();
  await ui.el['agent-compare'].listeners.click();
  assert.match(ui.el['agent-comparison-summary'].textContent, /нет данных/);
  const failed = setup({results: [{status: 'api_error', model: 'same', steps: [], tools: []}]});
  await failed.el['agent-compare'].listeners.click();
  assert.equal(failed.requests.length, 1);
  assert.match(failed.el['agent-comparison-summary'].textContent, /не завершено/);
});
