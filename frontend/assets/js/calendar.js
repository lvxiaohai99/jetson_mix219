(() => {
  const titleEl = document.getElementById('cal-title');
  const bodyEl = document.getElementById('cal-body');
  const dayTitle = document.getElementById('day-title');
  const dayList = document.getElementById('day-list');
  const dayPreview = document.getElementById('day-preview');
  if (!titleEl || !bodyEl) return;

  const now = new Date();
  let year = now.getFullYear();
  let month = now.getMonth() + 1; // 1-12
  let selected = null;

  function pad2(n){ return String(n).padStart(2, '0'); }
  function dateKey(y, m, d){ return `${y}-${pad2(m)}-${pad2(d)}`; }

  async function loadMonth(){
    titleEl.textContent = `${year}年${pad2(month)}月`;
    bodyEl.innerHTML = '<div class="empty" style="grid-column:1/-1;padding:.5rem">加载中...</div>';
    const res = await fetch(`/api/calendar?year=${year}&month=${month}`);
    const data = await res.json();
    const days = data.days || [];
    const first = Number(data.first_weekday || 0); // Mon=0
    const cells = [];
    for (let i = 0; i < first; i++) cells.push('<div class="cal-cell empty-cell"></div>');
    for (const d of days) {
      const cls = [
        'cal-cell',
        d.has_media ? 'has-media' : 'no-media',
        selected === d.date ? 'selected' : '',
      ].filter(Boolean).join(' ');
      const tip = d.has_media
        ? `${d.snapshots}张截图 · ${d.recordings}段录像`
        : '无文件';
      cells.push(`
        <button type="button" class="${cls}" data-date="${d.date}" title="${tip}">
          <span class="cal-day">${d.day}</span>
          <span class="cal-dot ${d.has_media ? 'green' : 'gray'}"></span>
        </button>`);
    }
    bodyEl.innerHTML = cells.join('');
    [...bodyEl.querySelectorAll('.cal-cell[data-date]')].forEach(btn => {
      btn.onclick = () => openDay(btn.dataset.date);
    });
  }

  async function openDay(dateStr){
    selected = dateStr;
    [...bodyEl.querySelectorAll('.cal-cell')].forEach(el => {
      el.classList.toggle('selected', el.dataset.date === dateStr);
    });
    if (dayTitle) dayTitle.textContent = dateStr + ' 的文件';
    if (dayList) dayList.innerHTML = '<li class="empty">加载中...</li>';
    if (dayPreview) {
      dayPreview.classList.add('hide');
      dayPreview.innerHTML = '';
    }
    const res = await fetch('/api/calendar/day?date=' + encodeURIComponent(dateStr));
    const data = await res.json();
    const items = data.items || [];
    if (!dayList) return;
    if (!items.length) {
      dayList.innerHTML = '<li class="empty">这一天没有截图或录像</li>';
      return;
    }
    dayList.innerHTML = items.map((it, idx) => `
      <li>
        <button type="button" class="day-item" data-idx="${idx}">
          ${it.time} · ${it.kind} · ${it.size_mb}MB
        </button>
      </li>`).join('');
    [...dayList.querySelectorAll('.day-item')].forEach(btn => {
      btn.onclick = () => showItem(items[Number(btn.dataset.idx)]);
    });
  }

  function showItem(it){
    if (!dayPreview) return;
    dayPreview.classList.remove('hide');
    if (it.kind_id === 'snapshot') {
      dayPreview.innerHTML = `
        <p><a href="${it.url}" download>${it.name}</a></p>
        <img src="${it.url}" alt="${it.name}" />`;
      return;
    }
    const tip = it.recording
      ? '<p class="empty">正在录制中，可试播当前片段；停止后会生成完整文件。</p>'
      : (it.playable === false
        ? '<p class="empty">文件未完成封装，无法预览。请停止录制后重试，或删掉损坏文件重新录。</p>'
        : '');
    if (it.playable === false) {
      dayPreview.innerHTML = `<p>${it.name}</p>${tip}`;
      return;
    }
    dayPreview.innerHTML = `
      <p><a href="${it.url}" download>${it.name}</a></p>
      ${tip}
      <video class="playback" controls playsinline preload="metadata" src="${it.url}?t=${Date.now()}"></video>`;
  }

  async function refresh(){
    await loadMonth();
    if (selected) await openDay(selected);
  }

  const btnPrev = document.getElementById('btn-prev');
  const btnNext = document.getElementById('btn-next');
  const btnToday = document.getElementById('btn-today');
  if (btnPrev) btnPrev.onclick = () => {
    month -= 1;
    if (month < 1) { month = 12; year -= 1; }
    loadMonth();
  };
  if (btnNext) btnNext.onclick = () => {
    month += 1;
    if (month > 12) { month = 1; year += 1; }
    loadMonth();
  };
  if (btnToday) btnToday.onclick = () => {
    const t = new Date();
    year = t.getFullYear();
    month = t.getMonth() + 1;
    loadMonth().then(() => openDay(dateKey(year, month, t.getDate())));
  };

  window.MediaCalendar = { refresh, openDay, loadMonth };

  loadMonth().then(() => openDay(dateKey(now.getFullYear(), now.getMonth() + 1, now.getDate())));
})();
