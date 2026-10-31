(() => {
  const form = document.querySelector('#stock-filter');
  const results = document.querySelector('#stock-results');
  const status = document.querySelector('#stock-search-status');
  let timer, controller, serial = 0, composing = false;
  async function refresh(url) {
    clearTimeout(timer);
    const version = ++serial;
    if (controller) controller.abort();
    controller = new AbortController();
    status.textContent = '조회 중…';
    results.setAttribute('aria-busy', 'true');
    const endpoint = new URL(url);
    endpoint.searchParams.set('partial', '1');
    try {
      const response = await fetch(endpoint, {signal: controller.signal});
      if (!response.ok || response.redirected) throw new Error('request failed');
      const html = await response.text();
      if (version !== serial) return;
      results.innerHTML = html;
      history.replaceState(null, '', url);
      const download = document.querySelector('#stock-export');
      if (download) download.search = new URL(url).search;
      status.textContent = '조회 완료';
    } catch (error) {
      if (error.name !== 'AbortError' && version === serial) status.textContent = '조회하지 못했습니다. 검색 버튼으로 다시 시도하세요.';
    } finally {
      if (version === serial) results.removeAttribute('aria-busy');
    }
  }
  function url() {
    const target = new URL(location.href);
    target.search = new URLSearchParams(new FormData(form)).toString();
    return target;
  }
  function schedule() {
    if (composing) return;
    clearTimeout(timer);
    // Invalidate and cancel in-flight work immediately, even during debounce.
    serial++;
    if (controller) controller.abort();
    timer = setTimeout(() => refresh(url()), 300);
  }
  form.addEventListener('compositionstart', () => { composing = true; clearTimeout(timer); serial++; if (controller) controller.abort(); });
  form.addEventListener('compositionend', () => { composing = false; schedule(); });
  form.addEventListener('input', schedule);
  form.addEventListener('change', schedule);
  form.addEventListener('submit', event => {event.preventDefault(); refresh(url());});
  results.addEventListener('click', event => {
    const link = event.target.closest('nav a');
    if (link && !event.ctrlKey && !event.metaKey && !event.shiftKey) {
      event.preventDefault(); refresh(new URL(link.href));
    }
  });
})();
