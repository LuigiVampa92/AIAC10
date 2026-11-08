const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('src/aiac/web/static/day_02.js', 'utf8');
const variants = ['baseline', 'format', 'limit', 'stop', 'schema'].map(id => ({id, label: id}));

class Element {
  constructor(tag) {
    this.tag = tag; this.listeners = {}; this.children = []; this.value = '';
    this.disabled = false; this.hidden = false; this.textContent = '';
  }
  addEventListener(name, fn) { this.listeners[name] = fn; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  removeAttribute(name) { delete this[name]; }
  remove() {}
  click() { return this.listeners.click?.(); }
  set innerHTML(_) { throw new Error('Untrusted HTML must not be inserted'); }
}
function setup(options = {}) {
  const ids = ['experiment-form', 'experiment-fields', 'experiment-status', 'experiment-cost',
    'experiment-progress', 'experiment-meter', 'experiment-count', 'stop-experiment',
    'download-experiment', 'experiment-results', 'comparison-body', 'experiment-findings',
    'experiment-conclusion', 'start-experiment'];
  const elements = Object.fromEntries(ids.map(id => [id, new Element(id)]));
  const form = elements['experiment-form'];
  const config = {prompt: 'extract data', format_instruction: 'only JSON', token_limit: '32',
    stop_sequence: '}', repeats: '2', selection: 'all', schema_text: '{}'};
  form.elements = Object.fromEntries(Object.entries(config).map(([key, value]) => [key, {value}]));
  form.reportValidity = () => true;
  form.action = 'http://localhost/weeks/1/days/2';
  const requests = [];
  let artifact;
  let pendingResolve;
  const fetch = async (url, request) => {
    const body = JSON.parse(request.body);
    requests.push({url, body});
    if (url.endsWith('/plan')) {
      if (options.invalidPlan) return {ok: false, json: async () => ({error: 'Invalid schema'})};
      const selected = body.selection === 'all' ? variants : variants.filter(v => v.id === body.selection);
      return {ok: true, json: async () => ({variants: selected, total: selected.length * body.repeats,
        model: 'test-model', provider: 'openai', config: body, created_at: '2026-11-08T00:00:00Z'})};
    }
    if (options.network) throw new Error('offline');
    if (options.wait) await new Promise(resolve => { pendingResolve = resolve; });
    const row = {variant: body.variant, label: body.variant, repeat: body.repeat,
      model: 'test-model', text: '<script>unsafe</script>', response: {status: 'ok'},
      request: body, status: 'completed', finish_reason: 'stop', seconds: 0.5,
      usage: {total_tokens: 15}, json_valid: true, schema_valid: true};
    if (options.fatal || options.blockVariant && body.variant === 'stop') {
      Object.assign(row, {status: 'api_error', response: null, error: 'rejected',
        stop_batch: Boolean(options.fatal), json_valid: null, schema_valid: null});
    }
    return {ok: true, json: async () => row};
  };
  vm.runInNewContext(source, {
    document: {getElementById: id => elements[id], createElement: tag => new Element(tag), body: new Element('body')},
    FormData: class {
      constructor(target) { this.entries = Object.entries(target.elements).map(([k, v]) => [k, v.value]); }
      [Symbol.iterator]() { return this.entries[Symbol.iterator](); }
    },
    fetch, Blob, URL: {createObjectURL: blob => { artifact = blob; return 'blob:test'; }, revokeObjectURL() {}},
    setTimeout: fn => fn(),
  });
  return {
    elements, requests,
    submit: () => form.listeners.submit({preventDefault() {}}),
    resolve: () => pendingResolve(),
    export: async () => {
      elements['download-experiment'].click();
      return JSON.parse(await artifact.text());
    },
  };
}
const settle = () => new Promise(resolve => setImmediate(resolve));

test('all variants repeat sequentially, summarize and export raw data with conclusion', async () => {
  const ui = setup();
  await ui.submit();
  assert.equal(ui.requests.length, 11); // preflight + 5 x 2, no retries
  assert.equal(ui.elements['experiment-results'].children.length, 10);
  assert.equal(ui.elements['comparison-body'].children.length, 5);
  assert.equal(ui.elements['experiment-meter'].value, 10);
  assert.equal(ui.elements['experiment-fields'].disabled, false);
  assert.equal(ui.elements['experiment-progress'].hidden, true);
  ui.elements['experiment-conclusion'].value = 'My conclusion';
  const artifact = await ui.export();
  assert.equal(artifact.runs.length, 10);
  assert.equal(artifact.runs[0].text, '<script>unsafe</script>');
  assert.equal(artifact.conclusion, 'My conclusion');
  assert.equal(artifact.state, 'finished');
});

test('stop waits for the current result; double submission does not duplicate calls', async () => {
  const ui = setup({wait: true});
  const work = ui.submit();
  await settle();
  assert.equal(ui.elements['experiment-fields'].disabled, true);
  await ui.submit();
  assert.equal(ui.requests.length, 2);
  ui.elements['stop-experiment'].click();
  ui.resolve();
  await work;
  assert.equal(ui.requests.length, 2);
  const artifact = await ui.export();
  assert.equal(artifact.state, 'stopped');
  assert.equal(artifact.runs.length, 1);
  assert.equal(ui.elements['experiment-fields'].disabled, false);
});

test('unsupported variant is called once; remaining repeats skipped, other variants continue', async () => {
  const ui = setup({blockVariant: true});
  await ui.submit();
  const artifact = await ui.export();
  const stopRows = artifact.runs.filter(row => row.variant === 'stop');
  assert.deepEqual(stopRows.map(row => row.status), ['api_error', 'skipped']);
  assert.equal(artifact.runs.filter(row => row.variant === 'schema').length, 2);
  assert.equal(ui.requests.filter(request => request.body.variant === 'stop').length, 1);
});

test('quota/connection failure stops remaining paid requests', async () => {
  const ui = setup({fatal: true});
  await ui.submit();
  assert.equal(ui.requests.length, 2);
  assert.equal((await ui.export()).state, 'api_error');
});

test('network interruption records an unknown outcome and does not retry', async () => {
  const ui = setup({network: true});
  await ui.submit();
  assert.equal(ui.requests.length, 2);
  const artifact = await ui.export();
  assert.equal(artifact.state, 'interrupted');
  assert.equal(artifact.runs[0].status, 'unknown');
  assert.equal(ui.elements['experiment-progress'].hidden, true);
});

test('invalid preflight performs zero model calls and restores controls', async () => {
  const ui = setup({invalidPlan: true});
  await ui.submit();
  assert.equal(ui.requests.length, 1);
  assert.equal(ui.elements['experiment-results'].children.length, 0);
  assert.match(ui.elements['experiment-status'].textContent, /Invalid schema/);
  assert.equal(ui.elements['experiment-fields'].disabled, false);
});
