const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('src/aiac/web/static/week_02_day_03.js','utf8');
const settle = () => new Promise(r => setImmediate(r));
class Element {
  constructor() {this.listeners={}; this.children=[]; this.value='0.5'; this.textContent='';}
  addEventListener(k,fn) {this.listeners[k]=fn;}
  append(...items) {this.children.push(...items);}
  replaceChildren() {this.children=[];}
  reportValidity() {return true;}
  set innerHTML(_) {throw Error('unsafe HTML');}
}
const turn = (total=120,cost=0.00002) => ({prompt:'hello',text:'<script>x</script>',model:'gpt-6-luna',
  status:'completed',outcome:'completed',api_calls:1,counting_calls:3,seconds:1,
  usage:{input_tokens:100,output_tokens:20,total_tokens:total}, cost_usd:cost,
  measurements:[{full_input_tokens:100,context_percent:0.01}],current_message_tokens:10,
  history_before_turn_tokens:80});
function setup(options={}) {
  const ids=['form','profile','prompt','budget','submit','new','compare','overflow','status','total','messages','growth','probes',
    'tool-fixed_exchange','tool-calculator','tool-current_time'];
  const el=Object.fromEntries(ids.map(id=>['tokens-'+id,new Element()]));
  el['tokens-form'].action='/weeks/2/days/3'; el['tokens-prompt'].value='Сложи 2+2';
  const requests=[]; let release;
  vm.runInNewContext(source,{
    document:{getElementById:id=>el[id],createElement:()=>new Element()},
    fetch:async(url,config)=>{
      const body=config.body ? JSON.parse(config.body) : undefined;
      requests.push({url,config,body});
      if(url.endsWith('/run') && options.manualNetwork) throw Error('offline');
      if(url.endsWith('/probe') && options.wait) await new Promise(r=>{release=r;});
      if(url.endsWith('/probe') && options.network) throw Error('offline');
      const data=url.endsWith('/probe') ? {...turn(),...(options.failed?{status:'api_error',outcome:'other_error'}:{})} :
        {id:'test',status:'completed',tools:['calculator'],profile:{model:'gpt-6-luna',context_window:1050000,checked:'date',source:'official'},
          total_cost_usd:null,known_cost_usd:0.00002,turns:options.holes?[turn(),turn(null,null)]:[turn()]};
      return {ok:true,json:async()=>data};
    },
  });
  return {el,requests,release:()=>release()};
}
test('initial load does not generate; usage growth is text-safe and missing cost is not zero',async()=>{
  const ui=setup({holes:true});await settle();
  assert.equal(ui.requests.length,1);assert.equal(ui.requests[0].config.method,'GET');
  assert.equal(ui.el['tokens-growth'].children.length,2);
  const cells=ui.el['tokens-growth'].children[1].children;
  assert.equal(cells[6].textContent,'нет данных');assert.equal(cells[8].textContent,'нет данных');
  assert.equal(ui.el['tokens-messages'].children[0].children[2].textContent,'<script>x</script>');
});
test('manual send does not include tools',async()=>{
  const ui=setup();await settle();
  await ui.el['tokens-form'].listeners.submit({preventDefault(){}});
  assert.match(ui.requests[1].url,/\/run$/);
  assert.deepEqual(ui.requests[1].body,{prompt:'Сложи 2+2'});
});


test('manual network failure does not retry or allow a stale continuation',async()=>{
  const ui=setup({manualNetwork:true}); await settle();
  await ui.el['tokens-form'].listeners.submit({preventDefault(){}});
  assert.equal(ui.requests.length,2);
  assert.equal(ui.el['tokens-submit'].disabled,true);
  assert.match(ui.el['tokens-status'].textContent,/Автоповтора нет/);
});
