function modelSummary(rows) {
  const sum = xs => xs.length && xs.every(Number.isFinite) ? xs.reduce((a, b) => a + b, 0) : null;
  const replies = rows.filter(r => ['completed', 'incomplete', 'refusal', 'empty'].includes(r.status));
  const seconds = sum(replies.map(r => r.seconds));
  return {correct: rows.filter(r => r.correct === true).length,
    tokens: ['input_tokens', 'output_tokens', 'total_tokens'].map(k => sum(rows.map(r => r.usage?.[k]))),
    cost: sum(rows.map(r => r.cost_usd)),
    average: seconds === null ? null : seconds / replies.length,
    failures: rows.filter(r => r.status !== 'completed').length};
}
if (typeof module !== 'undefined') module.exports = {modelSummary};
(() => {
  if (typeof document === 'undefined') return;
  const form = document.getElementById('models-form');
  if (!form) return;
  const el = id => document.getElementById(`models-${id}`);
  const roles = {small: 'Маленькая', medium: 'Средняя', flagship: 'Флагманская'};
  const node = (tag, text) => {const n = document.createElement(tag); if (text !== undefined) n.textContent = String(text); return n;};
  const display = (value, digits = 3) => value === null || value === undefined ? 'нет данных' : Number(value).toFixed(digits);
  let catalog = new Map();
  let running = false;
  let loading = false;
  let stopping = false;
  let rows = [];
  let plan = null;
  const containers = new Map();
  function preview() {
    const n = Number(el('repeats').value);
    const rates = Object.keys(roles).map(role => catalog.get(el(role).value)?.rates?.output);
    const cap = rates.every(Number.isFinite)
      ? (rates.reduce((sum, v) => sum + Number(v), 0) * 5 * n * 16000 / 1e6).toFixed(4) : null;
    el('cost').textContent = `До ${15 * n} реальных вызовов. Каждый: до 16000 выходных токенов и 120 с, без автоматических повторов.`
      + (cap === null ? ' Полная стоимость неизвестна без всех тарифов.'
        : ` При достижении лимита всеми ответами выход стоит до $${cap}, плюс вход. Это предел по токенам, не прогноз расхода.`);
  }
  function fillRates(role) {
    const rates = catalog.get(el(role).value)?.rates;
    el(role + '-info').textContent = rates
      ? 'Тарифы подставлены автоматически · USD / 1 млн: вход ' + rates.input + ', кэш ' + rates.cached + ', выход ' + rates.output + '. Проверено ' + rates.date + '.'
      : 'Тарифа для этого ID пока нет в справочнике: стоимость будет показана как «нет данных».';
    preview();
  }
  async function load() {
    if (loading || running) return;
    loading = true;
    el('catalog-status').textContent = 'Загружаем список моделей…';
    try {
      const response = await fetch(`${form.action}/models`, {credentials: 'same-origin', cache: 'no-store'});
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || 'Не удалось загрузить список.');
      catalog = new Map(data.models.map(model => [model.id, model]));
      el('list').replaceChildren();
      for (const id of catalog.keys()) {const option = node('option'); option.value = id; el('list').append(option);}
      for (const role of Object.keys(roles)) fillRates(role);
      const absent = Object.keys(roles).filter(role => !catalog.has(el(role).value));
      el('catalog-status').textContent = `Получено моделей: ${catalog.size}.`
        + (absent.length ? ` Выбранные ID отсутствуют в списке: ${absent.map(role => el(role).value).join(', ')}. Выберите доступные модели.` : ' Можно запускать сравнение.');
    } catch (error) {el('catalog-status').textContent = `${error.message} Подмены списком по умолчанию нет. Обновите страницу, чтобы повторить загрузку.`;}
    finally {loading = false; }
  }
  for (const role of Object.keys(roles)) el(role).addEventListener('change', () => fillRates(role));
  form.addEventListener('input', preview);
  preview();
  void load();
  async function post(path, config) {
    const response = await fetch(`${form.action}/${path}`, {method: 'POST', credentials: 'same-origin',
      headers: {'Content-Type': 'application/json'}, body: JSON.stringify(config)});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Проверьте параметры: сервер отклонил запрос.');
    return data;
  }
  function configuration() {
    const config = {repeats: Number(el('repeats').value)};
    for (const role of Object.keys(roles)) {
      const model = el(role).value;
      if (!catalog.has(model)) throw new Error(`Модель ${model} отсутствует в загруженном списке. Обновите страницу или выберите другую.`);
      const spec = {model};
      config[role] = spec;
    }
    return config;
  }
  function summary() {
    el('summary').replaceChildren();
    const stats = {};
    for (const role of Object.keys(roles)) {
      const group = rows.filter(row => row.role === role);
      const expected = plan.runs.filter(row => row.role === role).length;
      const stat = modelSummary(group);
      stats[role] = {stat, full: group.length === expected && group.every(r => r.status === 'completed'), expected};
      const tr = node('tr');
      [`${roles[role]} · ${plan.models[role].model} · reasoning ${plan.models[role].effort ?? 'параметр не отправлен'}`,
        `${stat.correct}/${expected}`, `${group.length}/${expected}`, display(stat.average),
        stat.tokens.map(v => v === null ? 'нет данных' : v).join(' / '), display(stat.cost, 6), stat.failures]
        .forEach(value => tr.append(node('td', value)));
      el('summary').append(tr);
    }
    const small = stats.small, flagship = stats.flagship;
    const messages = [];
    if (small.full && flagship.full) {
      const delta = (flagship.stat.correct / flagship.expected - small.stat.correct / small.expected) * 100;
      messages.push(`Разница точности флагмана и маленькой: ${delta.toFixed(1)} п.п.`);
      if (small.stat.cost !== null && flagship.stat.cost !== null && small.stat.cost > 0) {
        messages.push(`Отношение расчётных расходов флагман / маленькая: ${(flagship.stat.cost / small.stat.cost).toFixed(2)}×.`);
      } else messages.push('Отношение расходов неприменимо: неизвестная стоимость или нулевая база.');
    } else messages.push('Для сопоставления расходов и точности завершите обе серии без ошибок и неполных ответов.');
    const solved = plan.tasks.filter(task => {
      const group = rows.filter(row => row.role === 'small' && row.task === task.id);
      return group.length === plan.repeats && group.every(row => row.correct === true);
    }).map(task => task.title);
    if (solved.length) messages.push(`Маленькая модель решила во всех запланированных повторах: ${solved.join(', ')}.`);
    if (new Set(Object.values(plan.models).map(spec => spec.effort)).size > 1) messages.push('Настройки reasoning различаются: учитывайте это при сравнении.');
    el('findings').textContent = messages.join(' ') + ' Это наблюдение на пяти задачах, а не универсальный рейтинг моделей.';
  }
  function showRun(run) {
    const card = node('article'); card.className = 'run-card';
    card.append(node('h4', `${roles[run.role]} · ${run.requested_model || run.spec.model} · повтор ${run.repeat}`));
    card.append(node('p', `${run.status} · фактическая модель ${run.model || 'нет данных'} · ${display(run.seconds)} с · токены вход ${run.usage?.input_tokens ?? 'нет данных'} / выход ${run.usage?.output_tokens ?? 'нет данных'} / всего ${run.usage?.total_tokens ?? 'нет данных'} · $${display(run.cost_usd, 6)}`));
    if (run.evaluation || run.error) card.append(node('p', run.evaluation || run.error));
    if (run.text) card.append(node('pre', run.text));
    for (const [label, value] of [['Запрос API', run.request], ['Ответ API', run.response], ['Тарифы и источник', run.rates]]) {
      if (!value) continue;
      const panel = node('details'); panel.append(node('summary', label), node('pre', JSON.stringify(value, null, 2))); card.append(panel);
    }
    containers.get(run.task).append(card);
  }
  el('stop').addEventListener('click', () => {stopping = true; el('stop').disabled = true; el('status').textContent = 'Остановим после текущего вызова.';});
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (running || loading || !form.reportValidity()) return;
    let config;
    try {config = configuration();} catch (error) {el('status').textContent = error.message; return;}
    running = true; stopping = false;
    el('fields').disabled = true;
    el('status').textContent = 'Проверяем план…';
    let fatal = false;
    try {
      plan = await post('plan', config);
      rows = []; containers.clear(); el('results').replaceChildren();
      for (const task of plan.tasks) {
        const section = node('section'), cards = node('div'); cards.className = 'temperature-cards';
        section.append(node('h3', task.title), cards); el('results').append(section); containers.set(task.id, cards);
      }
      summary(); el('stop').hidden = false; el('stop').disabled = false;
      el('progress').hidden = false; el('progress').max = plan.calls; el('progress').value = 0;
      for (const run of plan.runs) {
        if (stopping || fatal) break;
        el('status').textContent = `Готово ${rows.length}/${plan.calls}. ${run.spec.model} · ${run.task}, повтор ${run.repeat}. Ожидаем ответ…`;
        let result;
        try {result = await post('runs', run);} catch (error) {
          result = {...run, status: 'unknown', seconds: null, correct: false,
            error: `${error.message} Исход вызова неизвестен; автоматического повтора нет.`}; fatal = true;
        }
        rows.push(result); showRun(result); summary(); el('progress').value = rows.length;
        if (result.status === 'api_error') fatal = true;
      }
      el('status').textContent = fatal ? 'Серия прервана. Автоматического повтора нет.' : stopping ? 'Серия остановлена. Частичные результаты доступны ниже.' : `Серия завершена: ${rows.length} вызовов.`;
    } catch (error) {el('status').textContent = `${error.message} Автоматического повтора нет.`;}
    finally {running = false; el('fields').disabled = false;  el('stop').hidden = true; el('progress').hidden = true;}
  });
})();
