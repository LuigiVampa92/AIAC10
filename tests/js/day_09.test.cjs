const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync('src/aiac/web/static/week_02_day_04.js','utf8');
const settle=()=>new Promise(r=>setImmediate(r));
class Element{
  constructor(){this.listeners={};this.children=[];this.value='6';this.textContent='';}
  addEventListener(k,f){this.listeners[k]=f;}
  append(...items){this.children.push(...items);}
  replaceChildren(){this.children=[];}
  reportValidity(){return true;}
  set innerHTML(_){throw Error('Unsafe HTML');}
}
function state(exp={}){
  return {id:'x',status:'completed',history:[],turns:[],summary:'<script>summary</script>',
    summarized_messages:0,keep_last:6,compress_every:10,active_context:[],message_count:0,
    retained_messages:0,compression_log:[],last_operation:{},experiment:exp};
}
function setup(options={}){
  const ids=['form','keep','every','prompt','send','new','start','resume','stop','status','info',
    'summary','context','archive','messages','growth','experiment-status','experiment-cost',
    'results','facts','experiment-data','scenario','scenario-view'];
  const el=Object.fromEntries(ids.map(id=>['memory-'+id,new Element()]));
  el['memory-form'].action='/weeks/2/days/4';el['memory-every'].value='10';
  el['memory-prompt'].value='Запомни имя';el['memory-scenario'].textContent=JSON.stringify({
    facts:[{id:'owner',question:'Имя?',expected:'Марина'}],user_messages:['Привет'],
  });
  const calls=[];let release,step=0;
  vm.runInNewContext(source,{
    document:{getElementById:id=>el[id],createElement:()=>new Element()},
    fetch:async(url,config)=>{
      calls.push({url,body:config.body?JSON.parse(config.body):undefined,method:config.method});
      if(url.endsWith('/run')&&options.network)throw Error('offline');
      if(url.endsWith('/comparison/step')&&options.wait)await new Promise(r=>{release=r;});
      let data=state();
      if(url.endsWith('/comparison/start'))data=state({phase:'dialogue',index:0,results:{}});
      if(url.endsWith('/comparison/step')){
        step++;
        data=state({phase:options.wait?'dialogue':'finished',index:step,results:{}});
        if(options.failed)data.status='api_error';
      }
      if(url.endsWith('/comparison')&&options.stored)data=options.stored;
      return {ok:true,json:async()=>data};
    },
  });
  return {el,calls,release:()=>release(),submit:()=>el['memory-form'].listeners.submit({preventDefault(){}})};
}
test('loads manual and comparison state without launching generations; text stays text',async()=>{
  const ui=setup();await settle();
  assert.equal(ui.calls.length,2);assert.ok(ui.calls.every(c=>c.method==='GET'));
  assert.equal(ui.el['memory-summary'].textContent,'<script>summary</script>');
  assert.equal(ui.el['memory-send'].disabled,false);
});
test('manual request sends settings and no tools or configurable budget',async()=>{
  const ui=setup();await settle();await ui.submit();
  assert.deepEqual(ui.calls[2].body,{prompt:'Запомни имя',keep_last:6,compress_every:10});
  assert.match(ui.calls[2].url,/\/run$/);
});
test('stop waits for the current step; duplicate start and resume do not send requests',async()=>{
  const ui=setup({wait:true});await settle();
  const work=ui.el['memory-start'].listeners.click();await settle();
  await ui.el['memory-start'].listeners.click();
  ui.el['memory-stop'].listeners.click();
  assert.equal(ui.calls.length,4);
  ui.release();await work;
  assert.equal(ui.calls.length,4);
  assert.equal(ui.el['memory-resume'].disabled,false);
  assert.match(ui.el['memory-experiment-status'].textContent,/остановлено/);
});
test('failed comparison step is kept and never automatically repeated',async()=>{
  const ui=setup({failed:true});await settle();await ui.el['memory-start'].listeners.click();
  assert.equal(ui.calls.length,4);
  assert.equal(ui.el['memory-resume'].disabled,true);
  assert.match(ui.el['memory-experiment-status'].textContent,/Автоповтора нет/);
});
test('manual network failure preserves text and blocks stale continuation',async()=>{
  const ui=setup({network:true});await settle();await ui.submit();
  assert.equal(ui.calls.length,3);
  assert.equal(ui.el['memory-prompt'].value,'Запомни имя');
  assert.equal(ui.el['memory-send'].disabled,true);
});
test('unknown summary cost is not replaced by the answer-only cost',async()=>{
  const result={sent_input_tokens:100,cost_usd:0.01,usage:{total_tokens:120},
    evaluation:{valid_json_object:true,remembered:1,details:[{id:'owner',answer:'Марина',remembered:true}]}};
  const ui=setup({stored:state({
    phase:'finished',index:12,results:{full_recall:result,compressed_recall:result},
    full_test_resources:{total_tokens:120,cost_usd:0.01},
    compressed_test_resources:{total_tokens:null,cost_usd:null},
  })});
  await settle();
  assert.equal(ui.el['memory-results'].children[1].children[4].textContent,'нет данных');
  assert.equal(ui.el['memory-resume'].disabled,true);
});
