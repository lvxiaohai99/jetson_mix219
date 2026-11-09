(() => {
  const view = document.getElementById('view');
  const viewVideo = document.getElementById('viewVideo');
  const statusEl = document.getElementById('status');
  const deRed = document.getElementById('de-red');
  const deRedVal = document.getElementById('de-red-val');
  const osdBox = document.getElementById('osd');
  const osdClock = document.getElementById('osd-clock');
  const modeSel = document.getElementById('preview-mode');
  const benchResult = document.getElementById('bench-result');
  const fpsInput = document.getElementById('preview-fps');
  const fpsVal = document.getElementById('fps-val');
  const autoRecord = document.getElementById('auto-record');
  const retention = document.getElementById('retention-days');
  const retentionVal = document.getElementById('retention-val');
  const storageHint = document.getElementById('storage-hint');

  let rotate = 0, scale = 1, deRedTimer = null, previewMode = 'mjpeg_hw';
  let fpsTimer = null, retentionTimer = null;
  let mpegtsPlayer = null;

  function setStatus(t){ statusEl.textContent = t; }
  function pad2(n){ return String(n).padStart(2,'0'); }
  function tickOsd(){
    const d = new Date();
    osdClock.textContent =
      d.getFullYear() + '-' + pad2(d.getMonth()+1) + '-' + pad2(d.getDate()) + ' ' +
      pad2(d.getHours()) + ':' + pad2(d.getMinutes()) + ':' + pad2(d.getSeconds());
  }
  function applyOsdVisible(){ osdClock.classList.toggle('hide', !osdBox.checked); }
  function applyTransform(){
    const target = previewMode === 'h264_soft' ? viewVideo : view;
    target.style.transform = `scale(${scale})`;
    setStatus(`模式 ${previewMode} · ${fpsInput.value}fps · 旋转 ${rotate}° · 缩放 ${scale.toFixed(1)}x · 去红边 ${deRed.value}`);
  }
  function showStorage(s){
    if (!s) return;
    storageHint.textContent =
      `已用 ${s.used_mb}MB · 预计滚存上限约 ${s.estimate_total_gb}GB` +
      `（${s.estimate_mb_per_day}MB/天 × ${s.retention_days}天）`;
  }

  async function postJSON(url, body){
    const res = await fetch(url, {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify(body||{})
    });
    const data = await res.json();
    if(!res.ok) throw new Error(data.error || '请求失败');
    return data;
  }

  function stopH264(){
    if (mpegtsPlayer) {
      try { mpegtsPlayer.destroy(); } catch (_) {}
      mpegtsPlayer = null;
    }
  }

  function startMjpeg(){
    stopH264();
    view.classList.remove('hide');
    viewVideo.classList.add('hide');
    view.src = '/api/preview/mjpeg?t=' + Date.now();
  }

  function startH264(){
    stopH264();
    view.classList.add('hide');
    viewVideo.classList.remove('hide');
    if (!window.mpegts || !mpegts.getFeatureList().mseLivePlayback) {
      setStatus('当前浏览器不支持 H.264 MSE 播放，请改用 MJPEG');
      return;
    }
    const wsProto = location.protocol === 'https:' ? 'wss' : 'ws';
    const url = `${wsProto}://${location.host}/api/preview/h264`;
    mpegtsPlayer = mpegts.createPlayer({ type: 'mpegts', isLive: true, url }, { enableWorker: true, liveBufferLatencyChasing: true });
    mpegtsPlayer.attachMediaElement(viewVideo);
    mpegtsPlayer.load();
    mpegtsPlayer.play().catch(()=>{});
  }

  async function applyPreviewMode(mode, persist=true){
    previewMode = mode;
    modeSel.value = mode;
    if (persist) {
      await postJSON('/api/settings', { preview_mode: mode });
    }
    if (mode === 'h264_soft') startH264();
    else startMjpeg();
    applyTransform();
  }

  async function setDeRed(value, immediate=false){
    deRed.value = String(value); deRedVal.textContent = String(value);
    const send = async ()=>{
      try { await postJSON('/api/settings', { de_red: Number(value) }); setStatus('去红边：'+value); }
      catch(e){ setStatus(String(e)); }
    };
    if(immediate) return send();
    clearTimeout(deRedTimer); deRedTimer = setTimeout(send, 120);
  }

  async function setRotate(next, resetScale=false){
    rotate = ((next % 360) + 360) % 360;
    if(resetScale) scale = 1;
    applyTransform();
    try{
      await postJSON('/api/settings', { rotate });
      if (previewMode === 'h264_soft') startH264();
      else startMjpeg();
      setStatus(`已旋转到 ${rotate}°`);
    }catch(e){ setStatus(String(e)); }
  }

  async function saveFps(v){
    fpsVal.textContent = String(v);
    try{
      const data = await postJSON('/api/settings', { preview_fps: Number(v) });
      showStorage(data.storage);
      setStatus('预览帧数已改为 ' + v + '（摄像头会短暂重开）');
      // 帧率变更后刷新播放器
      if (previewMode === 'h264_soft') startH264();
      else startMjpeg();
      applyTransform();
    }catch(e){ setStatus(String(e)); }
  }

  document.getElementById('btn-rot-l').onclick = ()=>setRotate(rotate+270);
  document.getElementById('btn-rot-r').onclick = ()=>setRotate(rotate+90);
  document.getElementById('btn-zoom-in').onclick = ()=>{ scale=Math.min(3,+(scale+0.2).toFixed(1)); applyTransform(); };
  document.getElementById('btn-zoom-out').onclick = ()=>{ scale=Math.max(0.4,+(scale-0.2).toFixed(1)); applyTransform(); };
  document.getElementById('btn-reset').onclick = ()=>setRotate(0, true);
  document.getElementById('btn-lock').onclick = async ()=>{
    try{
      const data = await postJSON('/api/settings', {
        rotate, scale:Number(scale), de_red:Number(deRed.value),
        osd: !!osdBox.checked, preview_mode: previewMode,
        preview_fps: Number(fpsInput.value),
        auto_record: !!autoRecord.checked,
        retention_days: Number(retention.value),
      });
      showStorage(data.storage);
      setStatus('已锁定当前设置');
    }catch(e){ setStatus(String(e)); }
  };
  deRed.oninput = ()=>{ deRedVal.textContent=deRed.value; setDeRed(deRed.value); };
  document.getElementById('btn-dered-off').onclick = ()=>setDeRed(0,true);
  document.getElementById('btn-dered-mid').onclick = ()=>setDeRed(55,true);
  document.getElementById('btn-dered-strong').onclick = ()=>setDeRed(85,true);
  osdBox.onchange = async ()=>{
    applyOsdVisible();
    try { await postJSON('/api/settings', { osd: !!osdBox.checked }); }
    catch(e){ setStatus(String(e)); }
  };
  modeSel.onchange = ()=>applyPreviewMode(modeSel.value, true);
  fpsInput.oninput = ()=>{
    fpsVal.textContent = fpsInput.value;
    clearTimeout(fpsTimer);
    fpsTimer = setTimeout(()=>saveFps(fpsInput.value), 280);
  };

  autoRecord.onchange = async ()=>{
    try{
      const data = await postJSON('/api/settings', { auto_record: !!autoRecord.checked });
      showStorage(data.storage);
      setStatus(autoRecord.checked ? '已开启默认自动录制' : '已关闭自动录制');
      syncRecordButtons();
    }catch(e){ setStatus(String(e)); }
  };
  retention.oninput = ()=>{
    retentionVal.textContent = retention.value;
    clearTimeout(retentionTimer);
    retentionTimer = setTimeout(async ()=>{
      try{
        const data = await postJSON('/api/settings', { retention_days: Number(retention.value) });
        showStorage(data.storage);
        setStatus('滚存天数已改为 ' + retention.value + ' 天');
      }catch(e){ setStatus(String(e)); }
    }, 280);
  };

  document.getElementById('btn-shot').onclick = async ()=>{
    try{
      setStatus('正在截图...');
      const res = await fetch('/api/snapshot');
      if(!res.ok) throw new Error('截图失败');
      const blob = await res.blob();
      const name = res.headers.get('X-Filename') || `snapshot_${Date.now()}.jpg`;
      const a=document.createElement('a'); a.href=URL.createObjectURL(blob); a.download=name; a.click();
      URL.revokeObjectURL(a.href); setStatus('截图完成：'+name); refreshCalendar();
    }catch(e){ setStatus(String(e)); }
  };

  const btnStart=document.getElementById('btn-rec-start');
  const btnStop=document.getElementById('btn-rec-stop');
  async function syncRecordButtons(){
    try{
      const res=await fetch('/api/record/status'); const data=await res.json();
      btnStart.disabled=!!data.recording; btnStop.disabled=!data.recording;
      if(data.recording) setStatus('录制中：'+(data.file||'')+(data.auto?'（自动）':''));
    }catch(_){}
  }
  btnStart.onclick = async ()=>{
    try{
      const data = await postJSON('/api/record/start', {});
      btnStart.disabled=true; btnStop.disabled=false; setStatus('录制中：'+data.file);
    }catch(e){ setStatus(String(e)); }
  };
  btnStop.onclick = async ()=>{
    try{
      const data = await postJSON('/api/record/stop', {});
      btnStart.disabled=false; btnStop.disabled=true; setStatus('录制完成：'+(data.file||'')); refreshCalendar();
    }catch(e){ setStatus(String(e)); }
  };

  document.getElementById('btn-bench').onclick = async ()=>{
    try{
      setStatus('正在实测当前预览模式约 10 秒，请稍候...');
      benchResult.textContent = '实测中...';
      const data = await postJSON('/api/metrics/preview-bench', { seconds: 10 });
      const text = `模式 ${data.preview_mode} · CPU均值 ${data.cpu_avg}% · 峰值 ${data.cpu_max}% · 采样 ${data.samples}`;
      benchResult.textContent = text;
      setStatus(text);
    }catch(e){ setStatus(String(e)); benchResult.textContent = String(e); }
  };
  document.getElementById('btn-bench-save').onclick = async ()=>{
    try{
      await postJSON('/api/settings', { preview_mode: previewMode, preview_fps: Number(fpsInput.value) });
      setStatus('已将当前预览模式/帧数设为默认');
    }catch(e){ setStatus(String(e)); }
  };

  function refreshCalendar(){
    if (window.MediaCalendar && typeof window.MediaCalendar.refresh === 'function') {
      window.MediaCalendar.refresh().catch(()=>{});
    }
  }

  async function syncState(){
    try{
      const res=await fetch('/api/settings'); const s=await res.json();
      rotate = Number(s.rotate||0); scale = Number(s.scale||1);
      deRed.value = String(s.de_red ?? 55); deRedVal.textContent = deRed.value;
      osdBox.checked = !!s.osd; applyOsdVisible();
      fpsInput.value = String(s.preview_fps ?? 15); fpsVal.textContent = fpsInput.value;
      autoRecord.checked = !!s.auto_record;
      retention.value = String(s.retention_days ?? 7); retentionVal.textContent = retention.value;
      showStorage(s.storage);
      const modes = s.preview_modes || {};
      modeSel.innerHTML = Object.entries(modes).map(([k,v]) =>
        `<option value="${k}">${v.label || k}</option>`
      ).join('');
      await applyPreviewMode(s.preview_mode || 'mjpeg_hw', false);
    }catch(_){ startMjpeg(); applyTransform(); }
    await syncRecordButtons();
  }

  tickOsd(); setInterval(tickOsd, 1000); applyOsdVisible();
  syncState();
  setInterval(syncRecordButtons, 5000);
  setInterval(refreshCalendar, 30000);
})();
