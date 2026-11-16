const {test}=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync('src/aiac/web/static/week_02_day_05.js','utf8');
const settle=()=>new Promise(r=>setImmediate(r));
class Element{
  constructor(){this.listeners={};this.children=[];this.value='';this.textContent='';}
  addEventListener(k,f){this.listeners[k]=f;}
  append(...items){this.children.push(...items);}
  replaceChildren(){this.children=[];}
  reportValidity(){return true;}
  set innerHTML(_){throw Error('unsafe HTML');}
}
const branch=(name='Основная')=>({name,strategy:'summary',keep_last:6,compress_every:10,status:'completed',facts:{owner:'<script>x</script>'},summary:'memo',turns:[],history:[]});
function workspace(active='main',withFork=false){
  const branches={main:branch()};if(withFork){branches.a=branch('A');branches.b=branch('B');}
  return {active_branch:active,branches,current:branches[active],checkpoints:{},working_messages:0,active_context:[]};
}
function experiment(phase='dialogue',status='completed'){
  return {phase,status,index:1,replay_index:0,recall_index:0,states:phase==='empty'?{}:{window:{}},results:{},resources:{},recommendations:['guidance'],complexity:{window:{nonblank_lines:2},facts:{nonblank_lines:40},summary:{nonblank_lines:60}}};
}
function setup(options={}){
  const ids=['form','scenario','mode','keep','every','apply','new','prompt','send','status','info','facts','summary','context','messages','branches','checkpoint-name','checkpoint','checkpoints','fork','start','resume','stop','progress','total','results','details','conclusions','data','scenario-view'];
  const el=Object.fromEntries(ids.map(id=>['strategy-'+id,new Element()]));
  el['strategy-form'].action='/weeks/2/days/5';el['strategy-prompt'].value='Новый бюджет';
  el['strategy-keep'].value='6';el['strategy-every'].value='10';el['strategy-checkpoint-name'].value='Развилка';
  el['strategy-scenario'].textContent=JSON.stringify({facts:[{id:'owner',question:'Имя?',expected:'Марина'}]});
  const calls=[];let release;
  vm.runInNewContext(source,{
    document:{getElementById:id=>el[id],createElement:()=>new Element()},
    fetch:async(url,config)=>{
      const body=config.body?JSON.parse(config.body):undefined;calls.push({url,body,method:config.method});
      if(url.endsWith('/run')&&options.network)throw Error('offline');
      if(url.endsWith('/comparison/step')&&options.wait)await new Promise(r=>{release=r;});
      let data=workspace();
      if(url.endsWith('/comparison'))data=options.stored||experiment('empty');
      if(url.endsWith('/comparison/start'))data=experiment();
      if(url.endsWith('/comparison/step'))data=experiment(options.wait?'replay':'finished',options.failed?'api_error':'completed');
      if(url.endsWith('/checkpoint')){data.checkpoints.c1={name:'Развилка'};}
      if(url.endsWith('/fork')){data=workspace('a',true);data.checkpoints.c1={name:'Развилка'};}
      if(url.endsWith('/branch')){data=workspace(body.branch_id,true);}
      return {ok:true,json:async()=>data};
    },
  });
  return {el,calls,release:()=>release(),submit:()=>el['strategy-form'].listeners.submit({preventDefault(){}})};
}
test('initial read restores workspace without paid calls; memory is rendered as text',async()=>{
  const ui=setup();await settle();assert.equal(ui.calls.length,2);assert.ok(ui.calls.every(c=>c.method==='GET'));
  assert.match(ui.el['strategy-facts'].textContent,/<script>x<\/script>/);assert.equal(ui.el['strategy-send'].disabled,false);
  assert.equal(ui.el['strategy-resume'].disabled,true);
});
test('checkpoint, two forks and branch selection use explicit metadata actions',async()=>{
  const ui=setup();await settle();await ui.el['strategy-checkpoint'].listeners.click();
  await ui.el['strategy-fork'].listeners.click();ui.el['strategy-branches'].value='b';await ui.el['strategy-branches'].listeners.change();
  assert.deepEqual(ui.calls.slice(2).map(c=>c.body),[{name:'Развилка'},{checkpoint_id:'c1'},{branch_id:'b'}]);
  assert.match(ui.el['strategy-info'].textContent,/Ветка: B/);
});
test('manual send contains only prompt; strategy change is separate and explicit',async()=>{
  const ui=setup();await settle();ui.el['strategy-mode'].value='facts';await ui.el['strategy-apply'].listeners.click();
  assert.deepEqual(ui.calls[2].body,{strategy:'facts',keep_last:6,compress_every:10});
  await ui.submit();assert.deepEqual(ui.calls[3].body,{prompt:'Новый бюджет'});
});
test('stop waits for in-flight step and blocks double start',async()=>{
  const ui=setup({wait:true});await settle();const work=ui.el['strategy-start'].listeners.click();await settle();
  await ui.el['strategy-start'].listeners.click();ui.el['strategy-stop'].listeners.click();assert.equal(ui.calls.length,4);
  ui.release();await work;assert.equal(ui.calls.length,4);assert.equal(ui.el['strategy-resume'].disabled,false);
  assert.match(ui.el['strategy-progress'].textContent,/остановлено/);
});
test('failed comparison is not automatically retried',async()=>{
  const ui=setup({failed:true});await settle();await ui.el['strategy-start'].listeners.click();
  assert.equal(ui.calls.length,4);assert.equal(ui.el['strategy-resume'].disabled,true);
});
test('manual network failure preserves prompt and blocks stale state',async()=>{
  const ui=setup({network:true});await settle();await ui.submit();assert.equal(ui.calls.length,3);
  assert.equal(ui.el['strategy-prompt'].value,'Новый бюджет');assert.equal(ui.el['strategy-send'].disabled,true);
});
test('unknown maintenance cost and invalid JSON are not treated as zero',async()=>{
  const stored=experiment('finished');stored.results.facts={evaluation:{valid_json_object:false,remembered:null,details:[]},resources:{total_tokens:null,cost_usd:null},maintenance_calls:13};
  const ui=setup({stored});await settle();const cells=ui.el['strategy-results'].children[1].children;
  assert.equal(cells[1].textContent,'не оценено');assert.equal(cells[4].textContent,'нет данных');
});
