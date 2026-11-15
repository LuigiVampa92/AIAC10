(() => {
  const el = id => document.getElementById('tokens-' + id);
  const form = el('form');
  if (!form) return;
  const metric = x => x === null || x === undefined ? 'нет данных' : x;
  const money = x => Number.isFinite(x) ? x.toFixed(7) : 'нет данных';
  const node = (tag, text) => {
    const n = document.createElement(tag);
    if (text !== undefined) n.textContent = String(text);
    return n;
  };
  let busy = false, state = null;
  function controls() {
    el('submit').disabled = busy || !state || !['empty', 'completed'].includes(state.status);
    el('new').disabled = busy || !state;
  }
  function row(parent, values) {
    const tr = node('tr');
    values.forEach(v => tr.append(node('td', metric(v))));
    el(parent).append(tr);
    return tr;
  }
  function details(parent, data) {
    const d = node('details');
    d.append(node('summary', 'Ответ, токены и ошибки'), node('pre', JSON.stringify(data, null, 2)));
    parent.append(d);
  }
  function show(data) {
    state = data;
    el('profile').textContent = data.profile ?
      data.profile.model + ' · окно ' + data.profile.context_window + ' токенов · тарифы проверены ' +
      data.profile.checked + ' · ' + data.profile.source :
      'Для выбранной модели нет проверенного лимита и тарифа. Поддержан gpt-6-luna.';
    el('total').textContent = 'Всего ходов: ' + data.turns.length + ' · накопленная стоимость USD ≈ ' +
      money(data.total_cost_usd) + ' · известная часть USD ≈ ' + money(data.known_cost_usd);
    el('messages').replaceChildren(); el('growth').replaceChildren();
    let totalTokens = 0, totalCost = 0, knownTokens = true, knownCost = true;
    data.turns.forEach((turn, index) => {
      const article = node('article');
      article.append(node('h3', 'Ход ' + (index + 1)), node('p', turn.prompt),
        node('pre', turn.text || turn.error || 'Ход не завершён.'));
      details(article, turn);
      el('messages').append(article);
      const first = turn.measurements?.[0];
      if (Number.isFinite(turn.usage?.total_tokens)) totalTokens += turn.usage.total_tokens;
      else knownTokens = false;
      if (Number.isFinite(turn.cost_usd)) totalCost += turn.cost_usd;
      else knownCost = false;
      row('growth', [index + 1, turn.current_message_tokens, turn.history_before_turn_tokens,
        first?.full_input_tokens, turn.usage?.input_tokens, turn.usage?.output_tokens,
        knownTokens ? totalTokens : null, money(turn.cost_usd), knownCost ? money(totalCost) : 'нет данных']);
    });
    controls();
  }
  async function request(path, body) {
    const response = await fetch(form.action + path, {
      method: body === undefined ? 'GET' : 'POST', credentials: 'same-origin', cache: 'no-store',
      headers: {'Content-Type': 'application/json'},
      ...(body === undefined ? {} : {body: JSON.stringify(body)}),
    });
    const data = await response.json();
    if (!response.ok) throw Error(data.error || 'Ошибка запроса.');
    return data;
  }
  async function work(action) {
    if (busy) return;
    busy = true; controls();
    el('status').textContent = 'Выполняется подсчёт / запрос к модели…';
    try { await action(); }
    catch (error) {
      el('status').textContent = error.message + ' Автоповтора нет. Исход сетевого вызова может быть неизвестен. Для диалога обновите страницу.';
    } finally { busy = false; controls(); }
  }
  form.addEventListener('submit', event => {
    event.preventDefault();
    if (!state || !['empty', 'completed'].includes(state.status) || !form.reportValidity()) return;
    const body = {prompt: el('prompt').value};
    return work(async () => {
      try { show(await request('/run', body)); }
      catch (error) { state = null; throw error; }
      el('prompt').value = '';
      el('status').textContent = state.status === 'completed' ? 'Ход сохранён.' : 'Ход остановлен. Подробности в записи хода; новый диалог сохраняет старый в архиве.';
    });
  });
  el('new').addEventListener('click', () => work(async () => {
    show(await request('/new', {})); el('status').textContent = 'Новый диалог создан; предыдущий сохранён.';
  }));
  work(async () => { show(await request('/history')); el('status').textContent = 'История загружена без обращения к модели.'; });
})();
