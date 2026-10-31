(() => {
  const font = document.querySelector('#print-font-size');
  const density = document.querySelector('#print-density');
  if (!font || !density) return;
  const key = 'sunil.picking-print.v1';
  const sizes = ['7','8','9','10','11','12'];
  function validated(value) {
    return {font: sizes.includes(String(value?.font)) ? String(value.font) : '8',
      density: value?.density === 'normal' ? 'normal' : 'compact'};
  }
  function apply(value, save = true) {
    const settings = validated(value);
    font.value = settings.font;
    density.value = settings.density;
    const style = document.documentElement.style;
    style.setProperty('--picking-font-size', `${settings.font}pt`);
    style.setProperty('--picking-line-height', settings.density === 'compact' ? '1.15' : '1.3');
    style.setProperty('--picking-pad-y', settings.density === 'compact' ? '2px' : '4px');
    if (save) {
      try { localStorage.setItem(key, JSON.stringify(settings)); } catch (_) { /* Still usable without storage. */ }
    }
    window.dispatchEvent(new Event('picking-print-settings-change'));
  }
  let saved = {};
  try { saved = JSON.parse(localStorage.getItem(key)) || {}; } catch (_) { /* Use defaults. */ }
  apply(saved, false);
  function change() {apply({font: font.value, density: density.value});}
  font.addEventListener('change', change);
  density.addEventListener('change', change);
  document.querySelector('#reset-print-settings').addEventListener('click', () => apply({font:'8',density:'compact'}));
  window.addEventListener('storage', event => {
    if (event.key === key) {
      try {apply(JSON.parse(event.newValue) || {}, false);} catch (_) {apply({}, false);}
    }
  });
})();
