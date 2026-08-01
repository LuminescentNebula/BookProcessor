const canEdit = document.body.dataset.role === 'admin';
const editable = {box:'Коробка',author:'Автор',title:'Название',publication_year:'Год',publisher:'Издательство',print_run:'Тираж',language:'Язык',isbn:'ISBN',genre:'Жанр'};
let currentBook = null;

document.querySelectorAll('.scene').forEach((scene) => {
  const open = () => openBook(JSON.parse(scene.dataset.book));
  scene.addEventListener('click', open);
  scene.addEventListener('keydown', (event) => { if (event.key === 'Enter') open(); });
});
function openBook(book) {
  currentBook = book;
  document.querySelector('#modal-cover').src = `/api/books/${book.id}/image/cover`;
  document.querySelector('#modal-info').src = `/api/books/${book.id}/image/info`;
  document.querySelector('#modal-fields').replaceChildren(...Object.entries(editable).map(([name, label]) => {
    const wrapper = document.createElement('label');
    const input = document.createElement('input');
    wrapper.textContent = label; input.name = name; input.value = book[name] || ''; input.disabled = true;
    wrapper.append(input); return wrapper;
  }));
  document.querySelector('#book-modal').classList.add('open');
  document.body.style.overflow = 'hidden';
  setEdit(false);
}
function setEdit(enabled) {
  document.querySelectorAll('#modal-fields input').forEach((input) => { input.disabled = !enabled; });
  document.querySelector('#edit-modal').hidden = enabled || !canEdit;
  document.querySelector('#save-modal').hidden = !enabled;
  document.querySelector('#modal-message').textContent = '';
}
document.querySelector('#edit-modal').onclick = () => setEdit(true);
document.querySelector('#close-modal').onclick = () => {
  document.querySelector('#book-modal').classList.remove('open'); document.body.style.overflow = '';
};
document.querySelector('#save-modal').onclick = async () => {
  const changes = Object.fromEntries(new FormData(document.querySelector('#modal-fields')));
  try {
    const response = await fetch(`/api/books/${currentBook.id}`, {
      method:'PATCH', headers:{'Content-Type':'application/json'}, body:JSON.stringify(changes),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error);
    Object.assign(currentBook, changes); setEdit(false); document.querySelector('#modal-message').textContent = 'Сохранено';
  } catch (error) { document.querySelector('#modal-message').textContent = `Ошибка: ${error.message}`; }
};
document.addEventListener('keydown', (event) => { if (event.key === 'Escape') document.querySelector('#close-modal').click(); });
