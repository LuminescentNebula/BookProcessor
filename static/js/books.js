const selections = [...document.querySelectorAll('.book-select')];
const normalizeButton = document.querySelector('#normalize-selected');
const selectionStatus = document.querySelector('#selection-status');

function refreshSelection() {
  normalizeButton.disabled = !selections.some((value) => value.checked);
}
selections.forEach((value) => value.addEventListener('change', refreshSelection));
document.querySelector('#select-all').addEventListener('change', (event) => {
  selections.forEach((value) => { value.checked = event.target.checked; });
  refreshSelection();
});
normalizeButton.addEventListener('click', async () => {
  const bookIds = selections.filter((value) => value.checked).map((value) => Number(value.value));
  normalizeButton.disabled = true;
  selectionStatus.textContent = 'Запуск…';
  try {
    const response = await fetch('/api/books/normalize', {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({book_ids: bookIds}),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error);
    await watchNormalization(data.job_id);
  } catch (error) {
    selectionStatus.textContent = `Ошибка: ${error.message}`;
    refreshSelection();
  }
});
async function watchNormalization(jobId) {
  while (true) {
    const response = await fetch(`/api/jobs/${jobId}`);
    const job = await response.json();
    selectionStatus.textContent = `Обработано ${job.completed} из ${job.total}`;
    if (job.status === 'failed') throw new Error(job.error);
    if (job.status === 'completed') {
      selectionStatus.textContent = 'Готово';
      setTimeout(() => location.reload(), 700);
      return;
    }
    await new Promise((resolve) => setTimeout(resolve, 1000));
  }
}

document.querySelectorAll('td[data-field]').forEach((cell) => cell.addEventListener('dblclick', () => editCell(cell)));
function editCell(cell) {
  if (cell.isContentEditable) return;
  const before = cell.textContent;
  cell.contentEditable = 'true';
  cell.classList.add('editing');
  cell.focus();
  cell.onkeydown = async (event) => {
    if (event.key === 'Escape') { event.preventDefault(); finish(before); return; }
    if (event.key !== 'Enter') return;
    event.preventDefault();
    const value = cell.textContent.trim();
    try {
      const response = await fetch(`/api/books/${cell.closest('tr').dataset.bookId}`, {
        method: 'PATCH', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({[cell.dataset.field]: value}),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error);
      finish(value); notice('Сохранено');
    } catch (error) { finish(before); notice(`Ошибка: ${error.message}`); }
  };
  function finish(value) {
    cell.textContent = value; cell.contentEditable = 'false'; cell.classList.remove('editing'); cell.onkeydown = null;
  }
}
function notice(text) {
  const box = document.querySelector('#save-state');
  box.textContent = text; box.style.display = 'block';
  setTimeout(() => { box.style.display = 'none'; }, 2500);
}
