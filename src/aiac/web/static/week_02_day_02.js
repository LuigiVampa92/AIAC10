(() => {
  const el = id => document.getElementById('chat-' + id);
  const form = el('form');
  if (!form) return;
  const tools = ['fixed_exchange', 'calculator', 'current_time'];
  const metric = v => v === null || v === undefined ? 'нет данных' : v;
  const node = (tag, text) => {
    const n = document.createElement(tag);
    if (text !== undefined) n.textContent = String(text);
    return n;
  };
  let busy = false, state = null;
  function controls() {
    el('submit').disabled = busy || !state || !['empty', 'completed'].includes(state.status);
    el('new').disabled = busy || !state;
    tools.forEach(name => { el('tool-' + name).disabled = busy; });
  }
  function show(data) {
    state = data;
    tools.forEach(name => { el('tool-' + name).checked = (data.tools || tools).includes(name); });
    el('info').textContent = 'Диалог ' + data.id + ' · ' + data.turns.length + ' ходов · ' + data.status;
    el('messages').replaceChildren();
    for (const turn of data.turns) {
      const card = node('article');
      card.append(node('h3', 'Вы'), node('p', turn.prompt), node('h3', 'Агент'),
        node('pre', turn.text || turn.error || 'Ход не завершён.'));
      card.append(node('p', turn.status + ' · ' + turn.model + ' · ' + metric(turn.seconds) +
        ' с · API: ' + metric(turn.api_calls) + ' · токены: вход ' + metric(turn.usage?.input_tokens) +
        ' / выход ' + metric(turn.usage?.output_tokens) + ' / всего ' + metric(turn.usage?.total_tokens)));
      for (const tool of turn.tools || []) {
        const detail = node('details');
        detail.append(node('summary', 'Инструмент: ' + tool.name),
          node('pre', tool.arguments), node('pre', JSON.stringify(tool.output, null, 2)));
        card.append(detail);
      }
      el('messages').append(card);
    }
    el('history').textContent = JSON.stringify(data.history, null, 2);
    if (!['empty', 'completed'].includes(data.status)) {
      el('status').textContent = 'Последний ход не завершён. Продолжение заблокировано, чтобы не повторять вызовы. Новый диалог сохранит старую историю.';
    }
    controls();
  }
  async function request(path, body) {
    const response = await fetch(form.action + path, {
      method: body === undefined ? 'GET' : 'POST', credentials: 'same-origin',
      cache: 'no-store', headers: {'Content-Type': 'application/json'},
      ...(body === undefined ? {} : {body: JSON.stringify(body)}),
    });
    const data = await response.json();
    if (!response.ok) throw Error(data.error || 'Не удалось выполнить запрос.');
    return data;
  }
  async function act(path, body) {
    if (busy) return;
    busy = true; controls();
    el('status').textContent = path === '/run' ? 'Агент отвечает и сохраняет историю…' : 'Загружаем диалог…';
    try {
      show(await request(path, body));
      if (path === '/run') el('prompt').value = '';
      if (['empty', 'completed'].includes(state.status)) el('status').textContent = 'История восстановлена с сервера и сохранена локально.';
    } catch (error) {
      // The server may still be completing a paid call. Never retry automatically.
      state = null;
      el('status').textContent = error.message + ' Автоповтора нет. Обновите страницу, чтобы загрузить фактическое состояние истории.';
    } finally {
      busy = false; controls();
    }
  }
  form.addEventListener('submit', event => {
    event.preventDefault();
    if (busy || !state || !['empty', 'completed'].includes(state.status) || !form.reportValidity()) return;
    return act('/run', {prompt: el('prompt').value, tools: tools.filter(name => el('tool-' + name).checked)});
  });
  el('new').addEventListener('click', () => {
    if (!busy && state) return act('/new', {});
  });
  act('/history');
})();
