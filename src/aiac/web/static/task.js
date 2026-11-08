(() => {
  const form = document.querySelector('[data-async-task]');
  if (!form || !window.fetch) return;

  const button = form.querySelector('button[type="submit"]');
  const progress = document.querySelector('[data-request-progress]');
  const status = document.querySelector('[data-request-status]');
  const result = document.getElementById('task-result');
  const buttonLabel = button.textContent;
  let pending = false;

  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (pending) return;
    pending = true;
    button.disabled = true;
    button.textContent = 'Ожидание ответа…';
    progress.hidden = false;
    result.setAttribute('aria-busy', 'true');
    status.textContent = 'Запрос отправляется. Можно продолжать просматривать страницу.';

    try {
      const response = await fetch(form.action, {
        method: 'POST',
        body: new FormData(form),
        headers: { Accept: 'text/html' },
        credentials: 'same-origin',
      });
      const html = await response.text();
      const page = new DOMParser().parseFromString(html, 'text/html');
      const updated = page.getElementById('task-result');
      if (!updated) {
        status.textContent = response.status === 422
          ? 'Проверьте запрос: нужен непустой текст до 4000 символов.'
          : 'Сервер не вернул результат. Повторная отправка автоматически не выполняется.';
        return;
      }
      const expanded = Array.from(result.querySelectorAll('details')).map(panel => panel.open);
      result.replaceChildren(...Array.from(updated.childNodes));
      result.querySelectorAll('details').forEach((panel, index) => {
        panel.open = expanded[index] || false;
      });
      status.textContent = response.ok ? 'Ответ получен.' : 'Запрос завершился с ошибкой. Подробности ниже.';
    } catch {
      status.textContent = 'Связь с сервером прервалась. Результат запроса неизвестен. Автоматического повтора не будет; проверьте соединение перед новой отправкой.';
    } finally {
      pending = false;
      button.disabled = false;
      button.textContent = buttonLabel;
      progress.hidden = true;
      result.setAttribute('aria-busy', 'false');
    }
  });
})();
