const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('src/aiac/web/static/week_02_day_02.js', 'utf8');
const settle = () => new Promise(resolve => setImmediate(resolve));
class Element {
  constructor() { this.listeners = {}; this.children = []; this.value = 'Помнишь имя?'; }
  addEventListener(name, fn) {this.listeners[name] = fn;}
  append(...items) {this.children.push(...items);}
  replaceChildren() {this.children = [];}
  set innerHTML(_) {throw Error('Unsafe HTML');}
}
function state(status = 'completed') {
  return {id: 'saved', status, tools: ['calculator'], history: [{role: 'user', content: 'Я Павел'}],
    turns: [{prompt: 'Я Павел', text: '<script>x</script>', status, model: 'test', api_calls: 2,
      seconds: 1, usage: {input_tokens: 80, output_tokens: 20, total_tokens: 100},
      tools: [{name: 'calculator', arguments: '{"expression":"20+22"}', output: {value:'42'}}]}]};
}
function setup(options = {}) {
  const ids = ['form','info','messages','prompt','submit','new','status','history',
    'tool-fixed_exchange','tool-calculator','tool-current_time'];
  const el = Object.fromEntries(ids.map(id => ['chat-' + id, new Element()]));
  el['chat-form'].action = '/weeks/2/days/2';
  el['chat-form'].reportValidity = () => true;
  const requests = []; let release;
  vm.runInNewContext(source, {
    document: {getElementById: id => el[id], createElement: () => new Element()},
    fetch: async (url, config) => {
      requests.push({url, config});
      if (url.endsWith('/run') && options.wait) await new Promise(r => {release = r;});
      if (url.endsWith('/run') && options.fail) throw Error('offline');
      return {ok: true, json: async () => state(options.interrupted ? 'running' : 'completed')};
    },
  });
  return {el, requests, release: () => release(), submit: () => el['chat-form'].listeners.submit({preventDefault() {}})};
}
test('page restores server history without paid call and renders as text', async () => {
  const ui = setup(); await settle();
  assert.equal(ui.requests.length, 1);
  assert.match(ui.requests[0].url, /history$/);
  assert.equal(ui.requests[0].config.method, 'GET');
  assert.match(ui.el['chat-history'].textContent, /Я Павел/);
  assert.equal(ui.el['chat-messages'].children[0].children[3].textContent, '<script>x</script>');
  assert.equal(ui.el['chat-tool-calculator'].checked, true);
  assert.equal(ui.el['chat-tool-current_time'].checked, false);
  assert.equal(ui.el['chat-submit'].disabled, false);
});
test('one submission sends selected tools; duplicate and reset while running do nothing', async () => {
  const ui = setup({wait:true}); await settle();
  const work = ui.submit(); await settle();
  await ui.submit(); await ui.el['chat-new'].listeners.click();
  assert.equal(ui.requests.length, 2);
  assert.deepEqual(JSON.parse(ui.requests[1].config.body), {prompt:'Помнишь имя?', tools:['calculator']});
  ui.release(); await work;
  assert.equal(ui.el['chat-submit'].disabled, false);
  assert.equal(ui.el['chat-prompt'].value, '');
});
test('network error keeps entered text and does not retry or allow stale continuation', async () => {
  const ui = setup({fail:true}); await settle(); await ui.submit();
  assert.equal(ui.requests.length, 2);
  assert.equal(ui.el['chat-submit'].disabled, true);
  assert.equal(ui.el['chat-prompt'].value, 'Помнишь имя?');
  assert.match(ui.el['chat-status'].textContent, /Автоповтора нет/);
  assert.equal(ui.el['chat-messages'].children.length, 1);
});
test('interrupted stored turn cannot be submitted; new dialogue is explicit', async () => {
  const ui = setup({interrupted:true}); await settle(); await ui.submit();
  assert.equal(ui.requests.length, 1);
  assert.equal(ui.el['chat-submit'].disabled, true);
  assert.equal(ui.el['chat-new'].disabled, false);
  await ui.el['chat-new'].listeners.click();
  assert.match(ui.requests[1].url, /new$/);
});
