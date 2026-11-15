(() => {
  const el = id => document.getElementById('memory-' + id);
  const form = el('form');
  if (!form) return;
  const scenario = JSON.parse(el('scenario').textContent);
  const metric = x => x === null || x === undefined ? 'нет данных' : x;
  const money = x => Number.isFinite(x) ? x.toFixed(7) : 'нет данных';
  const node = (tag, text) => {
    const n = document.createElement(tag);
    if (text !== undefined) n.textContent = String(text);
    return n;
  };
  let chat = null, experiment = null, busy = false, comparing = false, stopped = false;
  const ready = data => data && ['empty', 'completed'].includes(data.status);
  function controls() {
    el('send').disabled = busy || !ready(chat);
    el('new').disabled = busy || !chat;
    el('keep').disabled = busy || !!chat?.history?.length;
    el('every').disabled = busy || !!chat?.history?.length;
    el('start').disabled = busy || !experiment;
    el('resume').disabled = busy || !ready(experiment) || !experiment.experiment?.phase || experiment.experiment.phase === 'finished';
    el('stop').disabled = !comparing || stopped;
  }
  function row(id, values) {
    const tr = node('tr'); values.forEach(v => tr.append(node('td', metric(v))));
    el(id).append(tr); return tr;
  }
  function showChat(data) {
    chat = data;
    el('keep').value = data.keep_last; el('every').value = data.compress_every;
    el('summary').textContent = data.summary || 'Сводки пока нет: все сообщения отправляются без сжатия.';
    el('context').textContent = JSON.stringify(data.active_context, null, 2);
    el('archive').textContent = JSON.stringify(data.history, null, 2);
    el('info').textContent = 'Всего сообщений: ' + data.message_count + ' · в summary: ' + data.summarized_messages + ' · без изменений: ' + data.retained_messages;
    el('messages').replaceChildren(); el('growth').replaceChildren();
    data.turns.forEach((turn, i) => {
      const article = node('article');
      article.append(node('h3', 'Ход ' + (i + 1)), node('p', turn.prompt), node('pre', turn.text || turn.error || 'Не завершён'));
      const detail = node('details'); detail.append(node('summary', 'Токены и расходы сжатия / ответа'), node('pre', JSON.stringify(turn, null, 2)));
      article.append(detail); el('messages').append(article);
      row('growth', [i + 1, turn.status, turn.uncompressed_input_tokens, turn.sent_input_tokens,
        turn.input_saved_tokens, turn.resources?.total_tokens, money(turn.resources?.cost_usd)]);
    });
    if (!ready(data)) el('status').textContent = 'Операция не завершена. Автоповтора нет. Новый диалог сохранит старые данные.';
    if (data.pending_prompt) el('status').textContent += ' Незавершённое сообщение: ' + data.pending_prompt;
    controls();
  }
  const phases = {dialogue:'Создание диалога',full_recall:'Проверка полной истории',summary:'Создание summary',compressed_recall:'Проверка после сжатия',finished:'Сравнение завершено'};
  function showExperiment(data) {
    experiment = data;
    const exp = data.experiment || {};
    el('experiment-status').textContent = (phases[exp.phase] || 'Сравнение ещё не запускалось') + ' · сообщений пользователя: ' + (exp.index || 0) + ' / 12 · ' + data.status;
    if (!ready(data)) el('experiment-status').textContent += ' Автоповтора нет: начните новое сравнение.';
    el('experiment-cost').textContent = 'Общий расход (с созданием диалога): ' + metric(exp.resources?.total_tokens) + ' токенов · USD ≈ ' + money(exp.resources?.cost_usd);
    el('results').replaceChildren(); el('facts').replaceChildren();
    const full = exp.results?.full_recall, compressed = exp.results?.compressed_recall;
    for (const [title, result, resources] of [
      ['Полная история',full,exp.full_test_resources || {total_tokens:full?.usage?.total_tokens,cost_usd:full?.cost_usd}],
      ['Summary + последние сообщения',compressed,exp.compressed_test_resources],
    ]) {
      if (!result) continue;
      const score = result.evaluation?.valid_json_object ? result.evaluation.remembered + ' / ' + scenario.facts.length : 'Невалидный ответ, не оценено';
      row('results', [title,score,result.sent_input_tokens,resources?.total_tokens,money(resources?.cost_usd)]);
    }
    const answer = (result, id) => {
      const detail = result?.evaluation?.details?.find(d => d.id === id);
      return detail ? String(detail.answer ?? 'нет ответа') + (detail.remembered ? ' ✓' : ' ✗') : 'не оценено';
    };
    scenario.facts.forEach(f => row('facts',[f.question,f.expected,answer(full,f.id),answer(compressed,f.id)]));
    if (exp.comparable_models === false) el('experiment-status').textContent += ' Внимание: фактические модели различаются, прямое сравнение некорректно.';
    el('experiment-data').textContent = JSON.stringify({summary:data.summary,compression_log:data.compression_log,...exp,last_operation:data.last_operation},null,2);
    controls();
  }
  async function request(path, body) {
    const response = await fetch(form.action + path, {
      method: body === undefined ? 'GET' : 'POST', credentials:'same-origin',cache:'no-store',
      headers:{'Content-Type':'application/json'},...(body === undefined ? {} : {body:JSON.stringify(body)}),
    });
    const data = await response.json();
    if (!response.ok) throw Error(data.error || 'Ошибка запроса.');
    return data;
  }
  const settings = () => ({keep_last:Number(el('keep').value),compress_every:Number(el('every').value)});
  const validSettings = () => el('keep').reportValidity() && el('every').reportValidity();
  async function work(action, target='status') {
    if (busy) return;
    busy=true; controls();
    el(target).textContent='Ожидаем модель и сохраняем состояние…';
    try { await action(); }
    catch(error) {
      el(target).textContent=error.message + ' Автоповтора нет. При сбое сети исход запроса может быть неизвестен; обновите страницу.';
    } finally { busy=false; comparing=false; controls(); }
  }
  form.addEventListener('submit', event => {
    event.preventDefault();
    if (busy || !ready(chat) || !form.reportValidity()) return;
    const body={prompt:el('prompt').value,...settings()};
    return work(async()=>{
      try { showChat(await request('/run',body)); }
      catch(error) { chat=null; throw error; }
      if (ready(chat)) {el('prompt').value='';el('status').textContent='Ход сохранён.';}
    });
  });
  el('new').addEventListener('click',()=>{
    if (!validSettings()) return;
    const config=settings();
    return work(async()=>{showChat(await request('/new',config));el('status').textContent='Новый диалог создан. Предыдущий сохранён в архиве.';});
  });
  async function compare(start) {
    if (busy || !validSettings()) return;
    const config=settings(); stopped=false;
    return work(async()=>{
      comparing=true; controls();
      try {
        if (start) showExperiment(await request('/comparison/start',config));
        for(let i=0;i<16 && !stopped && ready(experiment) && experiment.experiment?.phase !== 'finished';i++) {
          showExperiment(await request('/comparison/step',{}));
        }
        if (stopped && ready(experiment)) el('experiment-status').textContent+=' · остановлено пользователем, прогресс сохранён';
      } catch(error) {experiment=null;throw error;}
    },'experiment-status');
  }
  el('start').addEventListener('click',()=>compare(true));
  el('resume').addEventListener('click',()=>compare(false));
  el('stop').addEventListener('click',()=>{stopped=true;controls();});
  el('scenario-view').textContent=JSON.stringify(scenario,null,2);
  work(async()=>{
    const [manual,comparison]=await Promise.all([request('/history'),request('/comparison')]);
    showChat(manual);showExperiment(comparison);
    if(ready(manual))el('status').textContent='Состояние восстановлено с сервера. Платные вызовы не запускались.';
  });
})();
