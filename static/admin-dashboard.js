(() => {
  const search = document.getElementById('adminSearch');
  if (!search) return;
  const normalise = (value) => String(value || '').normalize('NFKC').toLocaleLowerCase('ar').trim();
  const searchableRows = Array.from(document.querySelectorAll('.admin-searchable tbody tr'));
  const courseRows = Array.from(document.querySelectorAll('.admin-course-row'));
  const dbCards = Array.from(document.querySelectorAll('.admin-db-card'));
  document.querySelectorAll('form[data-confirm]').forEach((form) => {
    form.addEventListener('submit', (event) => {
      if (!window.confirm(form.dataset.confirm || 'هل تريد المتابعة؟')) event.preventDefault();
    });
  });
  search.addEventListener('input', () => {
    const query = normalise(search.value);
    searchableRows.forEach((row) => {
      const emptyRow = row.querySelector('td[colspan]');
      if (emptyRow) return;
      row.hidden = Boolean(query) && !normalise(row.innerText).includes(query);
    });
    courseRows.forEach((row) => {
      row.hidden = Boolean(query) && !normalise(row.innerText).includes(query);
    });
    dbCards.forEach((card) => {
      card.hidden = Boolean(query) && !normalise(card.innerText).includes(query);
    });
  });
})();
