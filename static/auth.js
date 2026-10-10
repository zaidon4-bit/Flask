(() => {
  'use strict';
  document.querySelectorAll('[data-password-toggle]').forEach((button) => {
    const targetId = button.getAttribute('data-password-toggle');
    const input = document.getElementById(targetId);
    if (!input) return;
    button.addEventListener('click', () => {
      const reveal = input.type === 'password';
      input.type = reveal ? 'text' : 'password';
      button.setAttribute('aria-pressed', String(reveal));
      button.setAttribute('aria-label', reveal ? 'إخفاء كلمة المرور' : 'إظهار كلمة المرور');
      button.textContent = reveal ? '◉' : '◎';
      input.focus({ preventScroll: true });
    });
  });
})();
