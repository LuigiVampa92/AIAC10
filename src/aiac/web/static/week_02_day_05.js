(() => {
  const el=id=>document.getElementById('strategy-'+id),form=el('form');
  if(!form)return;
  const scenario=JSON.parse(el('scenario').textContent), modes=['window','facts','summary'];
  const titles={window:'Sliding Window',facts:'Sticky Facts',summary:'Summary'};
  const metric=x=>x===null||x===undefined?'нет данных':x;
  const money=x=>Number.isFinite(x)?x.toFixed(7):'нет данных';
  const node=(tag,text)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=String(text);return n;};
  let workspace=null,experiment=null,busy=false,comparing=false,stopped=false;
  const ready=s=>s&&['empty','completed'].includes(s.status);
  function controls(){
    el('send').disabled=busy||!ready(workspace?.current);
    for(const id of ['new','branches'])el(id).disabled=busy||!workspace;
    for(const id of ['apply','checkpoint'])el(id).disabled=busy||!ready(workspace?.current);
    el('fork').disabled=busy||!Object.keys(workspace?.checkpoints||{}).length;
    el('checkpoints').disabled=busy||!Object.keys(workspace?.checkpoints||{}).length;
    for(const id of ['mode','keep','every','checkpoint-name'])el(id).disabled=busy;
    el('start').disabled=busy||!experiment;
    el('resume').disabled=busy||!ready(experiment)||!Object.keys(experiment?.states||{}).length||experiment.phase==='finished';
    el('stop').disabled=!comparing||stopped;
  }
  function options(id,items,value){
    const target=el(id);target.replaceChildren();
    for(const item of items){const n=node('option',item.label);n.value=item.id;target.append(n);}
    target.value=items.some(i=>i.id===value)?value:(items[0]?.id||'');
  }
  function row(id,values){const tr=node('tr');values.forEach(v=>tr.append(node('td',metric(v))));el(id).append(tr);}
  function showWorkspace(data){
    workspace=data;const b=data.current;
    el('mode').value=b.strategy;el('keep').value=b.keep_last;el('every').value=b.compress_every;
    options('branches',Object.entries(data.branches).map(([id,s])=>({id,label:s.name+' · '+titles[s.strategy]})),data.active_branch);
    options('checkpoints',Object.entries(data.checkpoints).map(([id,c])=>({id,label:c.name})),el('checkpoints').value);
    el('info').textContent='Ветка: '+b.name+' · '+titles[b.strategy]+' · сообщений в рабочей памяти: '+data.working_messages+' · '+b.status;
    el('facts').textContent=JSON.stringify(b.facts,null,2);
    el('summary').textContent=b.summary||'Сводки нет.';
    el('context').textContent=JSON.stringify(data.active_context,null,2);
    el('messages').replaceChildren();
    for(const turn of b.turns){
      const card=node('article');card.append(node('h3',titles[turn.strategy]||'Унаследованный ход'),node('p',turn.prompt),node('pre',turn.text||turn.error||'Не завершён'));
      card.append(node('p','Токены с поддержкой памяти: '+metric(turn.resources?.total_tokens)+' · USD ≈ '+money(turn.resources?.cost_usd)));
      const d=node('details');d.append(node('summary','Ресурсы ответа и памяти'),node('pre',JSON.stringify(turn,null,2)));card.append(d);el('messages').append(card);
    }
    if(!ready(b))el('status').textContent='Последний ход не завершён. Автоповтора нет. Можно выбрать другую ветку или разветвить сохранённый checkpoint.';
    if(b.pending_prompt)el('status').textContent+=' Незавершённое сообщение: '+b.pending_prompt;
    controls();
  }
  const phases={dialogue:'Создание общего диалога',replay:'Воспроизведение для трёх стратегий',final_summary:'Финальное сжатие',recall_facts_update:'Обновление Facts перед вопросами',recall:'Проверка деталей',finished:'Сравнение завершено'};
  function showExperiment(data){
    experiment=data;
    el('progress').textContent=(phases[data.phase]||data.phase)+' · диалог '+data.index+'/12 · воспроизведение '+data.replay_index+'/12 · проверено '+data.recall_index+'/3 · '+data.status;
    if(!ready(data))el('progress').textContent+=' Автоповтора нет, начните новое сравнение.';
    el('total').textContent='Общий расход с созданием диалога: '+metric(data.resources?.total_tokens)+' токенов · генераций '+metric(data.resources?.api_calls)+' · подсчётов '+metric(data.resources?.counting_calls)+' · USD ≈ '+money(data.resources?.cost_usd);
    el('results').replaceChildren();el('details').replaceChildren();el('conclusions').replaceChildren();
    for(const mode of modes){
      const r=data.results[mode],c=data.complexity[mode];
      row('results',[titles[mode],r?(r.evaluation.valid_json_object?r.evaluation.remembered+' / '+scenario.facts.length:'не оценено'):'ещё не проверено',r?.sent_input_tokens,r?.resources?.total_tokens,money(r?.resources?.cost_usd),r?.maintenance_calls,c.nonblank_lines]);
    }
    const answer=(mode,id)=>{const d=data.results[mode]?.evaluation.details.find(d=>d.id===id);return d?String(d.answer??'нет ответа')+(d.remembered?' ✓':' ✗'):'не оценено';};
    scenario.facts.forEach(f=>row('details',[f.question,f.expected,...modes.map(m=>answer(m,f.id))]));
    data.recommendations.forEach(text=>el('conclusions').append(node('li',text)));
    el('data').textContent=JSON.stringify(data,null,2);controls();
  }
  const config=()=>({strategy:el('mode').value,keep_last:Number(el('keep').value),compress_every:Number(el('every').value)});
  const validConfig=()=>el('keep').reportValidity()&&el('every').reportValidity();
  async function request(path,body){
    const response=await fetch(form.action+path,{method:body===undefined?'GET':'POST',credentials:'same-origin',cache:'no-store',headers:{'Content-Type':'application/json'},...(body===undefined?{}:{body:JSON.stringify(body)})});
    const data=await response.json();if(!response.ok)throw Error(data.error||'Ошибка запроса.');return data;
  }
  async function work(action,target='status'){
    if(busy)return;busy=true;controls();el(target).textContent='Сохраняем состояние / ожидаем модель…';
    try{await action();}catch(error){el(target).textContent=error.message+' Автоповтора нет. Исход сетевого запроса может быть неизвестен; обновите страницу.';}
    finally{busy=false;comparing=false;controls();}
  }
  async function control(path,body){return work(async()=>{try{showWorkspace(await request(path,body));}catch(error){workspace=null;throw error;}if(ready(workspace.current))el('status').textContent='Состояние сохранено.';});}
  form.addEventListener('submit',event=>{
    event.preventDefault();if(busy||!ready(workspace?.current)||!form.reportValidity())return;
    const body={prompt:el('prompt').value};
    return work(async()=>{try{showWorkspace(await request('/run',body));}catch(error){workspace=null;throw error;}if(ready(workspace.current)){el('prompt').value='';el('status').textContent='Ход сохранён.';}});
  });
  el('new').addEventListener('click',()=>{if(validConfig())return control('/new',config());});
  el('apply').addEventListener('click',()=>{if(validConfig())return control('/strategy',config());});
  el('branches').addEventListener('change',()=>control('/branch',{branch_id:el('branches').value}));
  el('checkpoint').addEventListener('click',()=>{if(el('checkpoint-name').reportValidity())return control('/checkpoint',{name:el('checkpoint-name').value});});
  el('fork').addEventListener('click',()=>control('/fork',{checkpoint_id:el('checkpoints').value}));
  async function compare(start){
    if(busy||!validConfig())return;const cfg=config();stopped=false;
    return work(async()=>{
      comparing=true;controls();
      try{
        if(start)showExperiment(await request('/comparison/start',cfg));
        for(let i=0;i<50&&!stopped&&ready(experiment)&&experiment.phase!=='finished';i++)showExperiment(await request('/comparison/step',{}));
        if(stopped&&ready(experiment))el('progress').textContent+=' · остановлено пользователем';
      }catch(error){experiment=null;throw error;}
    },'progress');
  }
  el('start').addEventListener('click',()=>compare(true));el('resume').addEventListener('click',()=>compare(false));
  el('stop').addEventListener('click',()=>{stopped=true;controls();});
  el('scenario-view').textContent=JSON.stringify(scenario,null,2);
  work(async()=>{const [manual,comparison]=await Promise.all([request('/history'),request('/comparison')]);showWorkspace(manual);showExperiment(comparison);if(ready(manual.current))el('status').textContent='Состояние восстановлено. Платные вызовы не запускались.';});
})();
