/* Download-only: report generation is offline and never invokes a model. */
(() => {
  let current = null;
  globalThis.AiacReport = {
    set(snapshot) {
      current = snapshot;
      const button = document.querySelector('[data-download-report]');
      if (button) button.disabled = false;
    },
  };
  document.addEventListener('click', async event => {
    const button = event.target.closest('[data-download-report]');
    if (!button) return;
    const embedded = document.getElementById('day-01-report');
    const report = Number(button.dataset.day) === 1 && embedded
      ? JSON.parse(embedded.textContent) : current;
    if (!report) return;
    button.disabled = true;
    const status = document.querySelector('[data-report-status]');
    if (status) status.textContent = 'Формируем отчёт…';
    try {
      const response = await fetch(`/weeks/1/days/${button.dataset.day}/report`, {
        method: 'POST', credentials: 'same-origin', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(report),
      });
      if (!response.ok) throw new Error('Не удалось сформировать отчёт. Текущие результаты остаются на странице.');
      const url = URL.createObjectURL(new Blob([await response.text()], {type: 'text/markdown;charset=utf-8'}));
      const link = document.createElement('a');
      link.href = url;
      link.download = `week-01-day-${button.dataset.day}-${new Date().toISOString().replace(/[:.]/g, '-')}.md`;
      document.body.append(link); link.click(); link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      if (status) status.textContent = 'Отчёт скачан. Он описывает текущую серию, включая частичные результаты.';
    } catch (error) {if (status) status.textContent = error.message;}
    finally {button.disabled = false;}
  });
})();
