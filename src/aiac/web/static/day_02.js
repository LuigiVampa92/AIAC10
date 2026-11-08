(() => {
  const form = document.getElementById('experiment-form');
  if (!form) return;
  const fields = document.getElementById('experiment-fields');
  const status = document.getElementById('experiment-status');
  const cost = document.getElementById('experiment-cost');
  const progress = document.getElementById('experiment-progress');
  const meter = document.getElementById('experiment-meter');
  const count = document.getElementById('experiment-count');
  const stop = document.getElementById('stop-experiment');
  const download = document.getElementById('download-experiment');
  const results = document.getElementById('experiment-results');
  const summary = document.getElementById('comparison-body');
  const findings = document.getElementById('experiment-findings');
  const conclusion = document.getElementById('experiment-conclusion');
  let running = false;
  let stopping = false;
  let report = null;

  const node = (tag, text, className) => {
    const element = document.createElement(tag);
    if (text !== undefined) element.textContent = text;
    if (className) element.className = className;
    return element;
  };
  const json = value => JSON.stringify(value, null, 2);
  const validText = value => value === null || value === undefined ? '—' : value ? 'да' : 'нет';
  const preview = () => {
    const total = Number(form.elements.repeats.value) * (form.elements.selection.value === 'all' ? 5 : 1);
    cost.textContent = `Будет выполнено до ${total} реальных запросов. Лимит каждого: до 512 выходных токенов; автоматических повторов нет.`;
    document.getElementById('start-experiment').textContent = form.elements.selection.value === 'all'
      ? 'Сравнить все варианты' : 'Запустить выбранный вариант';
  };
  form.addEventListener('input', preview);
  preview();

  async function post(path, body) {
    const response = await fetch(`${form.action}/${path}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      credentials: 'same-origin', body: JSON.stringify(body),
    });
    let data;
    try { data = await response.json(); }
    catch { throw new Error('Сервер не вернул JSON. Результат текущего вызова может быть неизвестен.'); }
    if (!response.ok) throw new Error(data.error || 'Проверьте поля формы: запрос отклонён сервером.');
    return data;
  }

  function refreshSummary() {
    summary.replaceChildren();
    const observations = [];
    for (const variant of report.plan.variants) {
      const rows = report.runs.filter(run => run.variant === variant.id);
      const attempts = rows.filter(run => run.status !== 'skipped');
      const replies = attempts.filter(run => run.response !== null && run.response !== undefined);
      const parsed = replies.filter(run => run.json_valid === true).length;
      const matched = replies.filter(run => run.schema_valid === true).length;
      const errors = attempts.filter(run => run.status === 'api_error').length;
      const incomplete = replies.filter(run => run.status !== 'completed').length;
      const skipped = rows.length - attempts.length;
      const unknown = attempts.filter(run => run.status === 'unknown').length;
      const usage = replies.map(run => run.usage?.total_tokens);
      const tokens = usage.length && usage.every(value => Number.isFinite(value))
        ? usage.reduce((a, b) => a + b, 0) : '—';
      const seconds = attempts.reduce((total, run) => total + (run.seconds || 0), 0).toFixed(3);
      const row = node('tr');
      [variant.label, attempts.length, `${parsed}/${replies.length}`, `${matched}/${replies.length}`,
        incomplete, errors, unknown, skipped, tokens, seconds].forEach(value => row.append(node('td', String(value))));
      summary.append(row);
      if (replies.length || errors) observations.push(
        `${variant.label}: JSON разобран ${parsed}/${replies.length}, схема соблюдена ${matched}/${replies.length}, неполных/отказов ${incomplete}, ошибок API ${errors}.`
      );
    }
    findings.textContent = observations.join(' ') + ' Это наблюдения данной серии, а не гарантия поведения модели. Ошибка API не считается ответом модели; причина finish_reason=stop не доказывает срабатывание заданного маркера.';
  }

  function showRun(run) {
    const card = node('article', undefined, 'run-card');
    card.append(node('h3', `${run.label} · прогон ${run.repeat}`));
    card.append(node('p', `Статус: ${run.status} · JSON: ${validText(run.json_valid)} · Схема: ${validText(run.schema_valid)} · Завершение: ${run.finish_reason || '—'} · ${run.seconds ?? '—'} с`, 'debug-caption'));
    if (run.error) card.append(node('p', run.error, 'notice'));
    if (run.text) card.append(node('pre', run.text));
    if (run.validation_errors?.length) card.append(node('p', run.validation_errors.join(' '), 'notice'));
    for (const [label, data] of [['Запрос к API · JSON', run.request], ['Ответ API · JSON', run.response]]) {
      if (data === null || data === undefined) continue;
      const panel = node('details', undefined, 'debug-panel');
      panel.append(node('summary', label), node('pre', json(data)));
      card.append(panel);
    }
    results.append(card);
    download.disabled = false;
  }

  stop.addEventListener('click', () => {
    stopping = true;
    stop.disabled = true;
    status.textContent = 'Останавливаем серию после текущего запроса. Его ответ будет сохранён.';
  });

  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (running || !form.reportValidity()) return;
    const data = Object.fromEntries(new FormData(form));
    const config = { ...data, token_limit: Number(data.token_limit), repeats: Number(data.repeats) };
    running = true;
    stopping = false;
    fields.disabled = true;
    progress.hidden = false;
    meter.removeAttribute('value');
    stop.hidden = true;
    status.textContent = 'Проверяем настройки перед запуском…';
    let completed = 0;
    let activeReport = null;
    count.textContent = 'Проверка настроек…';
    try {
      const plan = await post('plan', config);
      report = { version: 1, task: 'week_01/day_02', plan, runs: [], state: 'running' };
      activeReport = report;
      results.replaceChildren();
      conclusion.value = '';
      download.disabled = true;
      meter.max = plan.total;
      meter.value = 0;
      stop.hidden = false;
      stop.disabled = false;
      refreshSummary();
      let fatal = false;
      for (const variant of plan.variants) {
        let blocked = false;
        for (let repeat = 1; repeat <= config.repeats; repeat++) {
          if (stopping || fatal) break;
          let run;
          if (blocked) {
            run = {variant: variant.id, label: variant.label, repeat, status: 'skipped',
              error: 'Параметры этого варианта отклонены API. Повторный запрос не отправлялся.'};
          } else {
            count.textContent = `Готово ${completed} из ${plan.total} · ${variant.label}, прогон ${repeat}`;
            status.textContent = 'Ожидаем ответ. Можно просматривать предыдущие результаты.';
            try {
              run = await post('runs', { ...config, variant: variant.id, repeat, model: plan.model, provider: plan.provider });
            } catch (error) {
              report.state = 'interrupted';
              report.interruption = String(error.message);
              const unknown = {variant: variant.id, label: variant.label, repeat,
                status: 'unknown', response: null,
                error: 'Результат этого вызова неизвестен. Повтор автоматически не выполняется.'};
              report.runs.push(unknown);
              showRun(unknown);
              refreshSummary();
              throw error;
            }
            if (run.status === 'api_error') {
              fatal = run.stop_batch;
              blocked = !fatal;
            }
          }
          report.runs.push(run);
          showRun(run);
          refreshSummary();
          completed++;
          meter.value = completed;
          count.textContent = `Обработано ${completed} из ${plan.total} (включая пропущенные)`;
        }
        if (stopping || fatal) break;
      }
      report.state = stopping ? 'stopped' : fatal ? 'api_error' : 'finished';
      status.textContent = report.state === 'finished'
        ? 'Серия завершена. Сравните результаты и сохраните JSON.'
        : 'Серия остановлена. Частичные результаты доступны для скачивания; автоматического продолжения нет.';
    } catch (error) {
      status.textContent = `${error.message} Автоматического повтора нет.`;
    } finally {
      if (activeReport) activeReport.finished_at = new Date().toISOString();
      running = false;
      fields.disabled = false;
      stop.hidden = true;
      progress.hidden = true;
    }
  });

  download.addEventListener('click', () => {
    if (!report) return;
    const artifact = { ...report, conclusion: conclusion.value };
    const url = URL.createObjectURL(new Blob([json(artifact)], {type: 'application/json'}));
    const link = node('a');
    link.href = url;
    link.download = `day-02-${report.plan.created_at.replace(/[:.]/g, '-')}.json`;
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
})();
