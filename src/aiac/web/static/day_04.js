/* Pure metrics shared with Node tests; browser calls remain sequential. */
function temperatureSummary(rows) {
  const replies = rows.filter(r => ['completed', 'incomplete', 'refusal', 'empty'].includes(r.status));
  const completed = replies.filter(r => r.status === 'completed');
  const valid = completed.filter(r => r.format_valid === true);
  const sum = values => values.length && values.every(Number.isFinite)
    ? values.reduce((a, b) => a + b, 0) : null;
  return {
    replies: replies.length,
    correct: completed.filter(r => r.correct === true).length,
    wrong: valid.filter(r => r.correct === false).length,
    formatErrors: completed.filter(r => r.format_valid === false).length,
    incomplete: replies.length - completed.length,
    errors: rows.filter(r => r.status === 'api_error').length,
    unknown: rows.filter(r => r.status === 'unknown').length,
    uniqueTexts: new Set(replies.map(r => r.text)).size,
    uniqueAnswers: new Set(valid.map(r => JSON.stringify(r.normalized))).size,
    uniqueNames: new Set(valid.flatMap(r => r.names || [])).size,
    tokens: ['input_tokens', 'output_tokens', 'total_tokens'].map(k => sum(rows.map(r => r.usage?.[k]))),
    seconds: sum(rows.map(r => r.seconds)),
  };
}
if (typeof module !== 'undefined') module.exports = {temperatureSummary};
(() => {
  if (typeof document === 'undefined') return;
  const form = document.getElementById('temperature-form');
  if (!form) return;
  const el = id => document.getElementById(`temperature-${id}`);
  const titles = {price: 'Покупка', names: 'Названия приложения'};
  const node = (tag, text) => {
    const n = document.createElement(tag);
    if (text !== undefined) n.textContent = String(text);
    return n;
  };
  const display = value => value === null || value === undefined ? 'нет данных' : value;
  let running = false;
  let stopping = false;
  let plan = null;
  let rows = [];
  let groups = [];
  function preview() {
    const repeats = Number(form.elements.repeats.value);
    const calls = (form.elements.task.value === 'all' ? 2 : 1)
      * (form.elements.temperature.value === 'all' ? 3 : 1) * repeats;
    el('cost').textContent = Number.isInteger(repeats) && repeats >= 1 && repeats <= 10
      ? `До ${calls} реальных API-вызовов. Лимит 1024 выходных токена, тайм-аут 60 с. Без повторов при ошибке. Рассуждение отключено и одинаково для всех температур.`
      : 'Выберите целое число повторов от 1 до 10.';
  }
  form.addEventListener('input', preview);
  preview();
  async function post(path, body) {
    const response = await fetch(`${form.action}/${path}`, {
      method: 'POST', credentials: 'same-origin', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Сервер отклонил параметры запроса.');
    return data;
  }
  const groupRows = group => rows.filter(r => r.task === group.task && r.temperature === group.temperature);
  function summary() {
    el('summary').replaceChildren();
    const observations = [];
    for (const group of groups) {
      const current = groupRows(group);
      const stats = temperatureSummary(current);
      const tr = node('tr');
      [titles[group.task], group.temperature, `${current.length}/${group.expected}`, stats.replies,
        group.task === 'price' ? `${stats.correct}/${group.expected}` : '—',
        group.task === 'price' ? stats.wrong : '—', stats.formatErrors,
        stats.incomplete, `${stats.errors} / ${stats.unknown}`, stats.uniqueTexts,
        stats.uniqueAnswers, group.task === 'names' ? stats.uniqueNames : '—',
        stats.tokens.map(display).join(' / '), stats.seconds === null ? 'нет данных' : stats.seconds.toFixed(3)]
        .forEach(value => tr.append(node('td', value)));
      el('summary').append(tr);
      if (group.temperature === 0 && current.length) {
        const full = current.length === group.expected && stats.replies === group.expected
          && stats.incomplete === 0;
        observations.push(`${titles[group.task]}, temperature=0: ${stats.uniqueTexts} разных текстов среди ${stats.replies} ответов.`
          + (full && stats.replies >= 2 ? stats.uniqueTexts === 1
            ? ' В этой серии все тексты совпали.' : ' В этой серии тексты различались.'
            : ' Для сравнения повторяемости нужны минимум два полных ответа; серия может быть частичной.'));
      }
    }
    el('findings').textContent = observations.join(' ') + ' Совпадения в этой серии не гарантируют повторяемость будущих ответов. Полезность разнообразия оцените по примерам; число разных вариантов не измеряет качество.';
  }
  function showRun(run, group) {
    const card = node('article');
    card.className = 'run-card';
    card.append(node('h4', `Повтор ${run.repeat}`));
    card.append(node('p', `${run.status} · ${run.model || plan.model} · ${display(run.seconds)} с · Токены: вход ${display(run.usage?.input_tokens)} / выход ${display(run.usage?.output_tokens)} / всего ${display(run.usage?.total_tokens)}`));
    if (run.evaluation || run.error) card.append(node('p', run.evaluation || run.error));
    if (run.text) card.append(node('pre', run.text));
    for (const [label, value] of [['Запрос API', run.request], ['Ответ API', run.response]]) {
      if (!value) continue;
      const panel = node('details');
      panel.append(node('summary', label), node('pre', JSON.stringify(value, null, 2)));
      card.append(panel);
    }
    group.cards.append(card);
  }
  el('stop').addEventListener('click', () => {
    stopping = true;
    el('stop').disabled = true;
    el('status').textContent = 'Останавливаем после текущего вызова.';
  });
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (running || !form.reportValidity()) return;
    const config = Object.fromEntries(new FormData(form));
    config.repeats = Number(config.repeats);
    running = true;
    stopping = false;
    el('fields').disabled = true;
    el('status').textContent = 'Проверяем план…';
    let fatal = false;
    try {
      plan = await post('plan', config);
      rows = [];
      groups = [];
      el('results').replaceChildren();
      for (const run of plan.runs) {
        let group = groups.find(g => g.task === run.task && g.temperature === run.temperature);
        if (!group) {
          const section = node('section');
          const cards = node('div');
          cards.className = 'temperature-cards';
          section.append(node('h3', `${titles[run.task]} · temperature=${run.temperature}`), cards);
          el('results').append(section);
          group = {task: run.task, temperature: run.temperature, expected: 0, cards};
          groups.push(group);
        }
        group.expected++;
      }
      summary();
      el('stop').hidden = false;
      el('stop').disabled = false;
      el('progress').hidden = false;
      el('progress').max = plan.calls;
      el('progress').value = 0;
      for (const run of plan.runs) {
        if (stopping || fatal) break;
        el('status').textContent = `Готово ${rows.length}/${plan.calls}. ${titles[run.task]}, температура ${run.temperature}, повтор ${run.repeat}. Ожидаем ответ…`;
        let result;
        try { result = await post('runs', run); }
        catch (error) {
          result = {...run, status: 'unknown', seconds: null,
            error: `${error.message} Исход вызова неизвестен; автоматического повтора нет.`};
          fatal = true;
        }
        rows.push(result);
        showRun(result, groups.find(g => g.task === run.task && g.temperature === run.temperature));
        summary();
        el('progress').value = rows.length;
        if (result.status === 'api_error') fatal = true;
      }
      el('status').textContent = fatal ? 'Серия прервана. Частичные результаты доступны ниже.'
        : stopping ? 'Серия остановлена. Частичные результаты доступны ниже.'
        : `Серия завершена: ${rows.length} вызовов. Сравните ответы и показатели.`;
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
