/* Keep the complete ERP code on one line; shrink only when it needs to fit. */
(() => {
  const table = document.querySelector('table.customer');
  if (!table) return;
  const canvas = document.createElement('canvas');
  const context = canvas.getContext('2d');
  if (!context) return;
  const codes = [...table.querySelectorAll('.erp-code-value')];
  function font(style, size) {
    return `${style.fontStyle} ${style.fontWeight} ${size}px ${style.fontFamily}`;
  }
  function fitted(size, available, width) {
    return `${Math.min(size, size * Math.max(available - 1, 1) / Math.max(width, 1))}px`;
  }
  function fit() {
    codes.forEach(code => {
      const cell = code.parentElement;
      const style = getComputedStyle(cell);
      const size = parseFloat(style.fontSize);
      const available = cell.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight);
      context.font = font(style, size);
      code.style.setProperty('--code-screen-size', fitted(size, available, context.measureText(code.textContent).width));
      // The customer print template is A4 portrait, 6.35mm margins and an
      // 18% ERP-code column. Reserve its padding/border as well. Calculate
      // independently of the preview viewport, which may still be screen-sized
      // when Chromium dispatches beforeprint.
      const selected = parseFloat(getComputedStyle(table).getPropertyValue('--picking-font-size')) || 8;
      const printSize = selected * 96 / 72;
      const printAvailable = (210 - 2 * 6.35) * 96 / 25.4 * 0.18 - 10;
      context.font = font(style, printSize);
      code.style.setProperty('--code-print-size', fitted(printSize, printAvailable, context.measureText(code.textContent).width));
    });
  }
  fit();
  if (document.fonts) document.fonts.ready.then(fit);
  window.addEventListener('beforeprint', fit);
  window.addEventListener('picking-print-settings-change', fit);
  window.addEventListener('afterprint', fit);
  if (window.ResizeObserver) new ResizeObserver(fit).observe(table);
  else window.addEventListener('resize', fit);
})();
