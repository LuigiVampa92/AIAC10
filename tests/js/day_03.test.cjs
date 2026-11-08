const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('src/aiac/web/static/day_03.js', 'utf8');
class Element {
  constructor() { this.children = []; this.listeners = {}; this.textContent = ''; }
  addEventListener(k, fn) { this.listeners[k] = fn; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  set innerHTML(_) { throw Error('unsafe HTML'); }
}
function setup(options = {}) {
  const ids = ['form', 'fields', 'cost', 'stop', 'progress', 'status', 'summary', 'results'];
  const elements = Object.fromEntries(ids.map(id => [`reasoning-${id}`, new Element()]));
  const form = elements['reasoning-form'];
  form.action = 'http://localhost/weeks/1/days/3';
  form.elements = {task: {value: 'logic'}, method: {value: 'meta'}, effort: {value: 'max'}};
  const requests = [];
  let resolve;
  vm.runInNewContext(source, {
    document: {getElementById: id => elements[id], createElement: () => new Element()},
    FormData: class {
      constructor(form) {
        assert(!elements['reasoning-fields'].disabled, 'read form before disabling');
        this.entries = Object.entries(form.elements).map(([k, v]) => [k, v.value]);
      }
      [Symbol.iterator]() { return this.entries[Symbol.iterator](); }
    },
    fetch: async (url, request) => {
      const body = JSON.parse(request.body);
      requests.push(body);
      if (url.endsWith('/plan')) return {ok: true, json: async () => ({
        calls: 2, runs: [{task: 'logic', method: 'meta', effort: 'max'}]})};
      if (options.network) throw Error('offline');
      if (options.wait) await new Promise(r => {resolve = r;});
      return {ok: true, json: async () => ({...body,
        status: options.apiError ? 'api_error' : 'completed',
        text: body.stage === 'prepare' ? '<script>prompt</script>' : '{"answer":"Анна"}',
        usable_prompt: !options.badPrompt, correct: body.stage === 'solve',
        seconds: 1.5, usage: options.missingUsage ? null : {
          input_tokens: 10, output_tokens: 20, total_tokens: 30,
          output_tokens_details: {reasoning_tokens: 15}},
      })};
    },
  });
  return {elements, requests, resolve: () => resolve(),
    submit: () => form.listeners.submit({preventDefault() {}})};
}
test('meta uses generated prompt and sums both calls without adding reasoning twice', async () => {
  const ui = setup();
  await ui.submit();
  assert.equal(ui.requests.length, 3);
  assert.equal(ui.requests[2].generated_prompt, '<script>prompt</script>');
  const cells = ui.elements['reasoning-summary'].children[0].children;
  assert.equal(cells[2].textContent, '1/1');
  assert.equal(cells[4].textContent, '20 / 40 / 60');
  assert.equal(cells[5].textContent, '3.000');
});
test('stop during preparation prevents solve and double submit', async () => {
  const ui = setup({wait: true});
  const work = ui.submit();
  await new Promise(r => setImmediate(r));
  await ui.submit();
  ui.elements['reasoning-stop'].listeners.click();
  ui.resolve();
  await work;
  assert.equal(ui.requests.length, 2);
  assert.equal(ui.elements['reasoning-fields'].disabled, false);
  assert.equal(ui.elements['reasoning-summary'].children[0].children[2].textContent, '0/1');
});
for (const option of ['badPrompt', 'apiError', 'network']) {
  test(`${option} prevents second stage without retry`, async () => {
    const ui = setup({[option]: true});
    await ui.submit();
    assert.equal(ui.requests.length, 2);
    assert.equal(ui.elements['reasoning-results'].children.length, 1);
  });
}
test('missing usage stays unknown', async () => {
  const ui = setup({missingUsage: true});
  await ui.submit();
  assert.equal(ui.elements['reasoning-summary'].children[0].children[4].textContent,
    'нет данных / нет данных / нет данных');
});
