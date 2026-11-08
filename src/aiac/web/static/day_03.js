/* Sequential calls; no retries, background jobs or persistent storage. */
(() => {
  const form = document.getElementById('reasoning-form');
  if (!form) return;
  const el = id => document.getElementById(`reasoning-${id}`);
  const labels = {direct: 'Прямой ответ', steps: 'Решай пошагово', meta: 'Создание промпта', experts: 'Группа экспертов'};
  const node = (tag, text) => {
    const n = document.createElement(tag);
    if (text !== undefined) n.textContent = String(text);
    return n;
  };
  let running = false;
  let stopping = false;
  let plan = null;
  let rows = [];
  const metric = values => values.length && values.every(Number.isFinite)
    ? values.reduce((a, b) => a + b, 0) : null;
  const display = value => value === null || value === undefined ? 'нет данных' : value;
  function preview() {
    const tasks = form.elements.task.value === 'all' ? 5 : 1;
    const efforts = form.elements.effort.value === 'all' ? 2 : 1;
    const method = form.elements.method.value;
    const calls = tasks * efforts * (method === 'all' ? 5 : method === 'meta' ? 2 : 1);
    el('cost').textContent = `До ${calls} реальных API-вызовов. Каждый: до 16000 выходных токенов, включая рассуждение; тайм-аут 120 с, без повторов.`;
  }
  form.addEventListener('change', preview);
  preview();
  async function post(path, body) {
    const response = await fetch(`${form.action}/${path}`, {
      method: 'POST', credentials: 'same-origin',
      headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Сервер отклонил запрос.');
    return data;
  }
  function summary() {
    el('summary').replaceChildren();
    for (const method of Object.keys(labels)) for (const effort of ['none', 'max']) {
      const expected = plan.runs.filter(r => r.method === method && r.effort === effort);
      if (!expected.length) continue;
      const group = rows.filter(r => r.method === method && r.effort === effort);
      const calls = group.flatMap(r => r.calls);
      const usage = ['input_tokens', 'output_tokens', 'total_tokens'].map(key =>
        display(metric(calls.map(c => c.usage?.[key]))));
      const seconds = metric(calls.map(c => c.seconds));
      const tr = node('tr');
      [labels[method], effort, `${group.filter(r => r.correct).length}/${expected.length}`,
        group.length, usage.join(' / '), seconds === null ? 'нет данных' : seconds.toFixed(3)]
        .forEach(value => tr.append(node('td', value)));
      el('summary').append(tr);
    }
  }
  function showCall(call) {
    const card = node('article');
    card.className = 'run-card';
    card.append(node('h3', `${call.task} · ${labels[call.method]} · ${call.effort} · ${call.stage === 'prepare' ? 'Создание промпта' : 'Решение'}`));
    card.append(node('p', `Статус: ${call.status} · модель: ${call.model || 'нет данных'} · ${display(call.seconds)} с · Токены: вход ${display(call.usage?.input_tokens)} / выход ${display(call.usage?.output_tokens)} / всего ${display(call.usage?.total_tokens)} · reasoning ${display(call.usage?.output_tokens_details?.reasoning_tokens)}`));
    if (call.error || call.evaluation) card.append(node('p', call.error || call.evaluation));
    if (call.text) card.append(node('pre', call.text));
    for (const [label, value] of [['Запрос API', call.request], ['Ответ API', call.response]]) {
      if (!value) continue;
      const details = node('details');
      details.append(node('summary', label), node('pre', JSON.stringify(value, null, 2)));
      card.append(details);
    }
    el('results').append(card);
  }
  el('stop').addEventListener('click', () => {
    stopping = true;
    el('stop').disabled = true;
    el('status').textContent = 'Ожидаем текущий вызов. Следующий отправлен не будет.';
  });
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (running) return;
    const selection = Object.fromEntries(new FormData(form));
    running = true;
    stopping = false;
    el('fields').disabled = true;
    el('status').textContent = 'Проверяем план…';
    let callsDone = 0;
    let fatal = false;
    try {
      plan = await post('plan', selection);
      rows = [];
      el('results').replaceChildren();
      summary();
      el('stop').hidden = false;
      el('stop').disabled = false;
      el('progress').hidden = false;
      el('progress').max = plan.calls;
      el('progress').value = 0;
      for (const run of plan.runs) {
        if (stopping || fatal) break;
        const row = {...run, correct: false, calls: []};
        rows.push(row);
        let generated = '';
        const stages = run.method === 'meta' ? ['prepare', 'solve'] : ['solve'];
        for (const stage of stages) {
          if (stopping || fatal) break;
          el('status').textContent = `Готово вызовов ${callsDone}/${plan.calls}. ${labels[run.method]} · ${run.effort} · ${run.task}. Ожидаем ответ…`;
          let call;
          try {
            call = await post('runs', {...run, stage, generated_prompt: generated});
          } catch (error) {
            call = {...run, stage, status: 'unknown', error: `${error.message} Результат вызова неизвестен.`, seconds: null};
            fatal = true;
          }
          row.calls.push(call);
          showCall(call);
          callsDone++;
          el('progress').value = callsDone;
          if (call.status === 'api_error') fatal = true;
          if (stage === 'solve') row.correct = call.correct === true;
          summary();
          if (stage === 'prepare') {
            if (!call.usable_prompt) break;
            generated = call.text;
          }
        }
      }
      el('status').textContent = fatal ? 'Серия прервана. Автоматического повтора нет.'
        : stopping ? 'Серия остановлена. Частичные ответы остаются на странице.'
        : 'Серия завершена. Сравните точность, токены и время.';
    } catch (error) {
      el('status').textContent = `${error.message} Автоматического повтора нет.`;
    } finally {
      running = false;
      el('fields').disabled = false;
      el('stop').hidden = true;
      el('progress').hidden = true;
    }
  });
})();
