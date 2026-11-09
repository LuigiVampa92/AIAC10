const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('src/aiac/web/static/reports.js', 'utf8');
test('report button downloads Markdown snapshot without a model call', async () => {
  let listener, saved, artifact;
  const button = {disabled: true, dataset: {day: '3'}};
  const status = {};
  const context = {
    document: {
      querySelector: selector => selector === '[data-download-report]' ? button : status,
      getElementById: () => null,
      addEventListener: (_, fn) => {listener = fn;},
      createElement: () => ({click() {}, remove() {}}), body: {append() {}},
    },
    fetch: async (url, request) => {saved = {url, body: JSON.parse(request.body)};
      return {ok: true, text: async () => '# Test'};},
    URL: {createObjectURL: blob => {artifact = blob; return 'blob:test';}, revokeObjectURL() {}},
    Blob, setTimeout: fn => fn(),
  };
  vm.runInNewContext(source, context);
  context.AiacReport.set({day: 3, state: 'stopped', plan: {}, runs: []});
  assert.equal(button.disabled, false);
  await listener({target: {closest: () => button}});
  assert.equal(saved.url, '/weeks/1/days/3/report');
  assert.equal(saved.body.state, 'stopped');
  assert.equal(await artifact.text(), '# Test');
  assert.equal(button.disabled, false);
});
