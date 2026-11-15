(() => {
  const form = document.getElementById('agent-form');
  if (!form) return;
  const el = id => document.getElementById(`agent-${id}`);
  const node = (tag, text) => {
    const n = document.createElement(tag);
    if (text !== undefined) n.textContent = String(text);
    return n;
  };
  const metric = value => value === null || value === undefined ? 'нет данных' : value;
  let running = false;
  const toolNames = ['fixed_exchange', 'calculator', 'current_time'];
  const selected = () => toolNames.filter(name => el('tool-' + name).checked);
  function addRow(result) {
    const row = node('tr');
    const check = result.check;
    const values = [
      result.tools?.join(', ') || 'Без инструментов',
      result.status + ' / ' + result.model, metric(result.seconds),
      metric(result.usage?.input_tokens), metric(result.usage?.output_tokens),
      metric(result.usage?.total_tokens),
      result.steps.length, metric(result.tool_calls),
      check ? (check.json ? '✓' : '✗') : 'не проверялось',
    ];
    values.forEach(value => row.append(node('td', value)));
    const detail = node('details');
    detail.append(node('summary', 'Ответ и шаги'), node('pre', JSON.stringify(result, null, 2)));
    const cell = node('td'); cell.append(detail); row.append(cell);
    el('comparison').append(row);
  }
  function compareResults(results) {
    const target = el('comparison-summary');
    if (results.length !== 2 || results.some(r => r.status !== 'completed')) {
      target.textContent = 'Сравнение не завершено: полученные результаты сохранены в таблице.';
      return;
    }
    const [tools, baseline] = results;
    if (tools.model !== baseline.model) {
      target.textContent = 'Модели ответов различаются: прямое сравнение ресурсов не рассчитывается.';
      return;
    }
    const delta = (value, base, unit) => {
      if (!Number.isFinite(value) || !Number.isFinite(base) || base <= 0 || value < 0)
        return 'нет данных';
      const difference = value - base;
      return (value / base).toFixed(2) + '× (' + (difference >= 0 ? '+' : '') +
        Number(difference.toFixed(3)) + ' ' + unit + ')';
    };
    target.textContent = 'С инструментами относительно варианта без инструментов: время ' +
      delta(tools.seconds, baseline.seconds, 'с') + '; всего токенов ' +
      delta(tools.usage?.total_tokens, baseline.usage?.total_tokens, 'токенов') +
      '; запросы к API ' + tools.steps.length + ' против ' + baseline.steps.length +
      '. Это одна пара запусков, без оценки правильности суммы и временной метки.';
  }
  function show(result) {
    el('answer').textContent = result.text || 'Финальный ответ не получен.';
    el('result-info').textContent = `Статус: ${result.status} · модель: ${result.model} · ${metric(result.seconds)} с · обращений к модели: ${result.steps.length} · токены: вход ${metric(result.usage?.input_tokens)} / выход ${metric(result.usage?.output_tokens)} / всего ${metric(result.usage?.total_tokens)}`;
    el('steps').replaceChildren();
    for (const step of result.steps) {
      const card = node('article'); card.className = 'run-card';
      card.append(node('h3', `Обращение ${step.number} · ${step.status}`));
      card.append(node('p', `${metric(step.seconds)} с · токены: вход ${metric(step.usage?.input_tokens)} / выход ${metric(step.usage?.output_tokens)} / всего ${metric(step.usage?.total_tokens)}`));
      for (const tool of step.tools) {
        card.append(node('h4', `Инструмент: ${tool.name}`));
        card.append(node('pre', tool.arguments));
        card.append(node('pre', JSON.stringify(tool.output, null, 2)));
      }
      if (step.text) card.append(node('pre', step.text));
      for (const [label, value] of [['Запрос API', step.request], ['Ответ API', step.response]]) {
        if (!value) continue;
        const details = node('details');
        details.append(node('summary', label), node('pre', JSON.stringify(value, null, 2)));
        card.append(details);
      }
      el('steps').append(card);
    }
  }
  async function run(compare) {
    if (running || !form.reportValidity()) return;
    const prompt = el('prompt').value;
    const variants = compare ? [toolNames, []] : [selected()];
    const results = [];
    if (compare) el('comparison-summary').textContent = 'Выполняется новая пара запусков…';
    running = true;
    el('submit').disabled = true;
    el('compare').disabled = true;
    el('progress').hidden = false;
    try {
      for (let index = 0; index < variants.length; index++) {
        el('status').textContent = 'Агент работает · запуск ' + (index + 1) + ' / ' + variants.length;
        const response = await fetch(form.action + '/run', {
          method: 'POST', credentials: 'same-origin', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({prompt, tools: variants[index]}),
        });
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || 'Проверьте запрос и настройки агента.');
        if (compare) { results.push(result); addRow(result); }
        else show(result);
        el('status').textContent = result.status === 'completed' ? 'Запрос выполнен.' : result.error || 'Цикл остановлен.';
        // Keep the partial result, but do not spend more after a failed attempt.
        if (result.status !== 'completed') break;
      }
    } catch (error) {
      el('status').textContent = error.message + ' Автоматического повтора нет. При сетевом сбое исход вызова может быть неизвестен. Предыдущий результат, если он показан, не относится к новой попытке.';
    } finally {
      if (compare) compareResults(results);
      running = false;
      el('submit').disabled = false;
      el('compare').disabled = false;
      el('progress').hidden = true;
    }
  }
  form.addEventListener('submit', event => { event.preventDefault(); return run(false); });
  el('compare').addEventListener('click', () => run(true));
})();
