(() => {
  const boxes = [...document.querySelectorAll('input[name=allocations]')];
  const all = document.querySelector('#select-page');
  const status = document.querySelector('#selection-status');
  let anchor = null;
  function update() {
    const count = boxes.filter(b => b.checked).length;
    all.checked = boxes.length > 0 && count === boxes.length;
    all.indeterminate = count > 0 && count < boxes.length;
    status.textContent = `현재 페이지 ${count}행 선택`;
  }
  boxes.forEach((box, index) => box.addEventListener('click', event => {
    if (event.shiftKey && anchor !== null) {
      const start = Math.min(anchor, index), end = Math.max(anchor, index);
      for (let i = start; i <= end; i++) boxes[i].checked = box.checked;
    }
    anchor = index;
    update();
  }));
  all.addEventListener('change', () => {boxes.forEach(b => b.checked = all.checked); anchor = null; update();});
  update();
})();
