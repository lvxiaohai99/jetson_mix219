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
  let dayItems = [];
  let previewName = null;
  let liveLoading = false;
  let liveSrc = '';
  let liveItem = null;

  function pad2(n){ return String(n).padStart(2, '0'); }
  function dateKey(y, m, d){ return `${y}-${pad2(m)}-${pad2(d)}`; }

  function previewVideo(){
    return dayPreview ? dayPreview.querySelector('video') : null;
  }

  function isFullscreenFor(video){
    const fs = document.fullscreenElement || document.webkitFullscreenElement;
    if (!fs || !video) return false;
    return fs === video || (typeof fs.contains === 'function' && fs.contains(video));
  }

  function isWatching(){
    const video = previewVideo();
    if (!video) return false;
    if (isFullscreenFor(video)) return true;
    if (!video.paused && !video.ended) return true;
    return false;
  }

  async function loadMonth(){
    titleEl.textContent = `${year}年${pad2(month)}月`;
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
        </button>`);
    }
    bodyEl.innerHTML = cells.join('');
    [...bodyEl.querySelectorAll('.cal-cell[data-date]')].forEach(btn => {
      btn.onclick = () => openDay(btn.dataset.date);
    });
  }

  async function fetchDayItems(dateStr){
    const res = await fetch('/api/calendar/day?date=' + encodeURIComponent(dateStr));
    const data = await res.json();
    return data.items || [];
  }

  async function openDay(dateStr, preferName, opts){
    const keepPreview = !!(opts && opts.keepPreview);
    selected = dateStr;
    [...bodyEl.querySelectorAll('.cal-cell')].forEach(el => {
      el.classList.toggle('selected', el.dataset.date === dateStr);
    });
    if (dayTitle) dayTitle.textContent = dateStr + ' 的文件';
    if (dayList) dayList.innerHTML = '<li class="empty">加载中...</li>';

    if (dayPreview && !preferName && !keepPreview) {
      dayPreview.classList.add('hide');
      dayPreview.innerHTML = '';
      previewName = null;
      liveItem = null;
      liveSrc = '';
    }

    dayItems = await fetchDayItems(dateStr);
    renderDayList();

    if (keepPreview) return; // 正在看/全屏：只刷新列表，绝不重建播放器

    const want = preferName || previewName;
    if (want) {
      const hit = dayItems.find(x => x.name === want || x.file === want);
      if (hit) showItem(hit);
    }
  }

  function renderDayList(){
    if (!dayList) return;
    if (!dayItems.length) {
      dayList.innerHTML = '<li class="empty">这一天没有截图或录像</li>';
      return;
    }
    dayList.innerHTML = dayItems.map((it, idx) => `
      <li class="day-row">
        <button type="button" class="day-item" data-idx="${idx}">
          ${it.time} · ${it.kind} · ${it.size_mb}MB
        </button>
        <button type="button" class="btn-del" data-idx="${idx}" title="删除">删</button>
      </li>`).join('');
    [...dayList.querySelectorAll('.day-item')].forEach(btn => {
      btn.onclick = () => showItem(dayItems[Number(btn.dataset.idx)]);
    });
    [...dayList.querySelectorAll('.btn-del')].forEach(btn => {
      btn.onclick = (e) => {
        e.stopPropagation();
        deleteItem(dayItems[Number(btn.dataset.idx)]);
      };
    });
  }

  function showItem(it){
    if (!dayPreview || !it) return;
    // 同一个正在播的文件，不要重复重建（避免全屏被干掉）
    if (
      previewName === it.name &&
      dayPreview.querySelector('video') &&
      isWatching()
    ) {
      return;
    }

    previewName = it.name;
    liveItem = it;
    dayPreview.classList.remove('hide');

    if (it.kind_id === 'snapshot') {
      liveSrc = '';
      dayPreview.innerHTML = `
        <p class="preview-actions"><a href="${it.url}" download>${it.name}</a></p>
        <img src="${it.url}" alt="${it.name}" />`;
      return;
    }

    if (it.recording) {
      liveSrc = '';
      dayPreview.innerHTML = `
        <p class="preview-actions">${it.name}</p>
        <p class="empty" id="live-hint">边录边播（有延迟）。可拖进度条回看；播到末尾再接新内容。</p>
        <div class="live-actions">
          <button type="button" id="btn-refresh-live">刷新到最新</button>
        </div>
        <video id="live-rec-video" class="playback" controls playsinline preload="metadata"></video>`;
      document.getElementById('btn-refresh-live').onclick = () => loadLivePreview({ manual: true });
      const video = document.getElementById('live-rec-video');
      // 不要用 timeupdate 提前重载：全屏时改 src 会直接退出全屏，看起来像“播一会没了”
      video.onended = () => {
        if (isFullscreenFor(video)) {
          const hint = document.getElementById('live-hint');
          if (hint) hint.textContent = '已到当前预览末尾。请退出全屏后点「刷新到最新」继续。';
          const onFs = () => {
            if (isFullscreenFor(video)) return;
            document.removeEventListener('fullscreenchange', onFs);
            document.removeEventListener('webkitfullscreenchange', onFs);
            loadLivePreview({ nearEnd: true });
          };
          document.addEventListener('fullscreenchange', onFs);
          document.addEventListener('webkitfullscreenchange', onFs);
          return;
        }
        loadLivePreview({ nearEnd: true });
      };
      loadLivePreview({ init: true });
      return;
    }

    if (it.playable === false) {
      liveSrc = '';
      dayPreview.innerHTML = `
        <p class="preview-actions">${it.name}</p>
        <p class="empty">文件未完成封装，无法预览。请用右侧「删」清理。</p>`;
      return;
    }

    liveSrc = '';
    dayPreview.innerHTML = `
      <p class="preview-actions"><a href="${it.url}" download>${it.name}</a></p>
      <video class="playback" controls playsinline preload="metadata" src="${it.url}?t=${Date.now()}"></video>`;
  }

  async function loadLivePreview(opts){
    const video = document.getElementById('live-rec-video');
    const hint = document.getElementById('live-hint');
    if (!video || liveLoading) return;

    const manual = !!(opts && opts.manual);
    const nearEnd = !!(opts && opts.nearEnd);
    const init = !!(opts && opts.init);

    // 全屏中禁止自动改 src（会退出全屏并黑屏）
    if (!manual && !init && isFullscreenFor(video)) {
      if (hint) hint.textContent = '全屏播放中，不会自动刷新。退出全屏或点「刷新到最新」再接新内容。';
      return;
    }

    liveLoading = true;
    const keep = video.currentTime || 0;
    const wasPlaying = !video.paused && keep > 0;
    const wasFs = isFullscreenFor(video);
    try {
      const res = await fetch('/api/record/live-preview', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ force: true }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || '预览失败');
      const durTxt = data.duration
        ? `已录约 ${Math.floor(data.duration / 60)}分${String(Math.floor(data.duration % 60)).padStart(2, '0')}秒`
        : '';
      if (hint) {
        hint.textContent =
          `边录边播 · 预览截至 ${data.as_of || '--'} · ${durTxt} · 全屏时不会自动打断`;
      }
      const oldDur = video.duration || 0;
      const newDur = Number(data.duration || 0);
      const minGrow = nearEnd ? 2.0 : (manual ? 0.8 : 0);
      if (!init && liveSrc && oldDur > 0 && newDur > 0 && (newDur - oldDur) < minGrow) {
        return;
      }

      const next = data.url + '?t=' + Date.now();
      await new Promise((resolve) => {
        const onReady = () => {
          video.removeEventListener('loadedmetadata', onReady);
          if (keep > 0.3 && keep < (video.duration || 0)) {
            try { video.currentTime = keep; } catch (_) {}
          } else if (nearEnd && (video.duration || 0) > 1) {
            try { video.currentTime = Math.max(0, Math.min(keep, video.duration - 0.5)); } catch (_) {}
          }
          const playP = (wasPlaying || nearEnd || keep > 0.3) ? video.play().catch(()=>{}) : Promise.resolve();
          Promise.resolve(playP).finally(() => {
            // 手动刷新若刚才在全屏，尽量恢复全屏
            if (wasFs && manual && video.requestFullscreen) {
              video.requestFullscreen().catch(()=>{});
            }
            resolve();
          });
        };
        video.addEventListener('loadedmetadata', onReady);
        liveSrc = next;
        video.src = next;
        video.load();
      });
    } catch (e) {
      if (hint) hint.textContent = String(e.message || e) + '（录制开始几秒后再试）';
    } finally {
      liveLoading = false;
    }
  }

  async function deleteItem(it){
    if (!it) return;
    const fileName = it.file || it.name;
    const msg = it.recording
      ? `「${it.name}」正在录制中。\n删除会先停止并丢掉这一段，然后按设置重新开始录制。\n确定删除吗？`
      : `确定删除「${it.name}」吗？此操作不可恢复。`;
    if (!window.confirm(msg)) return;
    try {
      const res = await fetch('/api/media/delete', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: fileName }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || '删除失败');
      if (dayPreview) {
        dayPreview.classList.add('hide');
        dayPreview.innerHTML = '';
      }
      previewName = null;
      liveItem = null;
      liveSrc = '';
      await refresh();
      if (window.setStatus) {
        window.setStatus(data.message || ('已删除 ' + it.name));
      }
    } catch (e) {
      alert(String(e.message || e));
    }
  }

  async function refresh(preferName){
    await loadMonth();
    if (selected) {
      await openDay(selected, preferName, { keepPreview: isWatching() });
    }
  }

  async function softRefresh(){
    // 定时任务专用：只更新绿点和列表，播放中绝不碰预览区
    await loadMonth();
    if (!selected) return;
    dayItems = await fetchDayItems(selected);
    renderDayList();
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

  window.MediaCalendar = { refresh, softRefresh, openDay, loadMonth };

  loadMonth().then(() => openDay(dateKey(now.getFullYear(), now.getMonth() + 1, now.getDate())));
})();
