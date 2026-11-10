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
  const algoMode = document.getElementById('algo-mode');
  const algoModel = document.getElementById('algo-model');
  const faceStatus = document.getElementById('face-status');
  const btnPreviewToggle = document.getElementById('btn-preview-toggle');
  const stageEl = document.getElementById('stage');
  const previewOffEl = document.getElementById('preview-off');
  const cameraBanner = document.getElementById('camera-banner');
  const meterCpu = document.getElementById('meter-cpu');
  const meterMem = document.getElementById('meter-mem');
  const meterCma = document.getElementById('meter-cma');
  const barCpu = document.getElementById('bar-cpu');
  const barMem = document.getElementById('bar-mem');
  const barCma = document.getElementById('bar-cma');
  const deviceMeta = document.getElementById('device-meta');
  const deviceHint = document.getElementById('device-hint');
  const deviceTopList = document.getElementById('device-top-list');
  const deviceUpdated = document.getElementById('device-updated');
  const btnRecoverCam = document.getElementById('btn-recover-cam');
  const recoverStatus = document.getElementById('recover-status');
  let recoverReady = false;
  let recovering = false;

  let rotate = 0, scale = 1, deRedTimer = null, previewMode = 'mjpeg_hw';
  let previewEnabled = true;
  let fpsTimer = null, retentionTimer = null;
  let mpegtsPlayer = null;

  const CAM_LABEL = {
    ready: '正常',
    starting: '启动中',
    paused: '已暂停',
    open_failed: '打开失败',
    no_frame: '无画面',
    argus_busy: 'Argus占用',
    cma_low: 'CMA不足',
  };

  function setStatus(t){ statusEl.textContent = t; }

  function setBar(el, pct){
    if (!el) return;
    const v = Math.max(0, Math.min(100, Number(pct) || 0));
    el.style.width = v + '%';
    el.classList.toggle('hot', v >= 90);
  }

  function showCameraBanner(cam){
    if (!cameraBanner) return;
    const st = (cam && cam.state) || '';
    const bad = ['open_failed', 'no_frame', 'argus_busy', 'cma_low'].includes(st);
    if (!bad || !previewEnabled) {
      cameraBanner.classList.add('hide');
      cameraBanner.textContent = '';
      return;
    }
    const hint = (cam && (cam.hint || cam.detail)) || CAM_LABEL[st] || st;
    cameraBanner.textContent = '摄像头异常：' + hint;
    cameraBanner.classList.toggle('warn', st === 'starting' || st === 'no_frame');
    cameraBanner.classList.remove('hide');
  }

  function renderDeviceStatus(data){
    if (!data) return;
    const cpu = data.cpu_percent ?? 0;
    const mem = data.memory || {};
    const cma = data.cma || {};
    const cam = data.camera || {};
    if (meterCpu) meterCpu.textContent = cpu.toFixed(1) + '%';
    setBar(barCpu, cpu);
    if (meterMem) {
      meterMem.textContent = `${mem.used_mb ?? '-'} / ${mem.total_mb ?? '-'} MB (${mem.percent ?? '-'}%)`;
    }
    setBar(barMem, mem.percent);
    if (meterCma) {
      const total = cma.total_mb ?? 256;
      meterCma.textContent = `剩余 ${cma.free_mb ?? '-'} / ${total} MB`;
    }
    setBar(barCma, cma.used_percent);
    if (barCma) barCma.classList.toggle('hot', !!cma.low);

    recoverReady = !!(data.recover && data.recover.ready);
    if (btnRecoverCam && !recovering) {
      btnRecoverCam.title = recoverReady
        ? '释放摄像头并重启 Argus，回收 CMA'
        : '需先在设备上执行一次免密配置（见下方提示）';
    }

    const load = (data.loadavg || []).map(x => Number(x).toFixed(2)).join(' / ') || '--';
    const camState = cam.state || '--';
    const camText = CAM_LABEL[camState] || camState;
    const argus = data.nvargus_active === true ? '运行中' : (data.nvargus_active === false ? '未运行' : '未知');
    if (deviceMeta) {
      deviceMeta.textContent =
        `摄像头：${camText}` +
        (cam.detail ? `（${cam.detail}）` : '') +
        ` · Argus：${argus} · Load：${load}` +
        (mem.swap_used_mb ? ` · Swap：${mem.swap_used_mb}MB` : '');
    }

    const hints = [];
    if (cam.hint) hints.push(cam.hint);
    (data.hints || []).forEach(h => { if (h && !hints.includes(h)) hints.push(h); });
    if (deviceHint) {
      if (hints.length) {
        deviceHint.textContent = hints.join('；');
        deviceHint.classList.remove('hide');
      } else {
        deviceHint.textContent = '';
        deviceHint.classList.add('hide');
      }
    }

    if (deviceTopList) {
      const rows = data.top_processes || [];
      if (!rows.length) {
        deviceTopList.innerHTML = '<li class="empty">暂无进程数据</li>';
      } else {
        deviceTopList.innerHTML = rows.map((p, i) =>
          `<li><span class="rank">${i + 1}</span>` +
          `<span class="pname" title="pid ${p.pid}">${p.name || p.pid}</span>` +
          `<span class="pstat">${Number(p.cpu).toFixed(1)}% · ${Number(p.rss_mb).toFixed(1)}MB</span></li>`
        ).join('');
      }
    }
    if (deviceUpdated) {
      const d = new Date();
      deviceUpdated.textContent =
        '更新 ' + String(d.getHours()).padStart(2,'0') + ':' +
        String(d.getMinutes()).padStart(2,'0') + ':' +
        String(d.getSeconds()).padStart(2,'0');
    }
    showCameraBanner(cam);
  }

  async function pollDeviceStatus(){
    try{
      const res = await fetch('/api/system/status?top=10');
      if (!res.ok) return;
      const data = await res.json();
      renderDeviceStatus(data);
    }catch(_){}
  }

  function showRecoverMsg(text, isErr){
    if (!recoverStatus) return;
    recoverStatus.textContent = text || '';
    recoverStatus.classList.toggle('err', !!isErr);
    recoverStatus.classList.toggle('hide', !text);
  }

  function sleep(ms){ return new Promise(r => setTimeout(r, ms)); }

  async function waitPreviewBack(timeoutMs){
    const t0 = Date.now();
    while (Date.now() - t0 < timeoutMs) {
      await sleep(1000);
      try{
        const res = await fetch('/api/health');
        const data = await res.json().catch(() => null);
        if (data && (data.ok || data.camera === 'ready')) return data;
      }catch(_){
        // 服务重启瞬间会连不上，继续等
      }
    }
    return null;
  }

  async function recoverCamera(){
    if (recovering) return;
    recovering = true;
    if (btnRecoverCam) btnRecoverCam.disabled = true;
    showRecoverMsg('正在释放摄像头并重启 Argus（约需数秒）…', false);
    setStatus('正在恢复摄像头…');
    try{
      const res = await fetch('/api/system/recover-camera', {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: '{}',
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        const setup = data.setup_cmd || '';
        const tip = data.need_setup && setup
          ? `需先配置免密（只需一次）。请在设备终端执行：${setup}`
          : (`恢复失败：` + (data.error || res.statusText || '未知错误'));
        showRecoverMsg(tip, true);
        setStatus(tip);
        await pollDeviceStatus();
        return;
      }

      if (data.restarting_service) {
        showRecoverMsg(data.message || '正在重启预览服务，请稍候…', false);
        setStatus('预览服务重启中，等待重连…');
        const back = await waitPreviewBack(20000);
        if (!back) {
          const tip = '服务已重启但尚未出画，请手动刷新页面；仍不行再重启整机';
          showRecoverMsg(tip, true);
          setStatus(tip);
          return;
        }
        showRecoverMsg('服务已恢复，正在重新拉流…', false);
      } else if (!data.frame_ready) {
        showRecoverMsg((data.message || '仍无首帧') + '，继续等待…', false);
        await waitPreviewBack(8000);
      }

      const cma = data.cma || {};
      const msg = (data.message || '恢复完成') +
        ` · CMA 剩余 ${cma.free_mb ?? '-'} / ${cma.total_mb ?? 256} MB`;
      showRecoverMsg(msg, false);
      setStatus(msg);
      // 重新同步设置并拉流（服务重启后内存状态会丢）
      try { await syncState(); } catch(_) {}
      if (previewEnabled) {
        if (previewMode === 'h264_soft') startH264();
        else startMjpeg();
      }
      await pollDeviceStatus();
    }catch(e){
      // 服务重启时请求可能被掐断，改为轮询等待
      showRecoverMsg('连接中断，正在等待服务重启…', false);
      const back = await waitPreviewBack(20000);
      if (back) {
        showRecoverMsg('服务已恢复', false);
        try { await syncState(); } catch(_) {}
        if (previewEnabled) {
          if (previewMode === 'h264_soft') startH264();
          else startMjpeg();
        }
        await pollDeviceStatus();
      } else {
        const tip = '恢复失败：' + String(e) + '。可手动刷新页面，或重启整机';
        showRecoverMsg(tip, true);
        setStatus(tip);
      }
    }finally{
      recovering = false;
      if (btnRecoverCam) btnRecoverCam.disabled = false;
    }
  }

  if (btnRecoverCam) {
    btnRecoverCam.onclick = () => recoverCamera();
  }
  window.setStatus = setStatus;
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

  function syncPreviewToggleUi(){
    if (btnPreviewToggle) {
      btnPreviewToggle.textContent = previewEnabled ? '关闭预览' : '开启预览';
      btnPreviewToggle.classList.toggle('is-active', !previewEnabled);
    }
    if (stageEl) stageEl.classList.toggle('preview-disabled', !previewEnabled);
    if (previewOffEl) previewOffEl.classList.toggle('hide', previewEnabled);
  }

  function stopPreviewStream(){
    stopH264();
    try { view.removeAttribute('src'); view.src = ''; } catch (_) {}
    view.classList.add('hide');
    viewVideo.classList.add('hide');
  }

  function startMjpeg(){
    if (!previewEnabled) { stopPreviewStream(); return; }
    stopH264();
    view.classList.remove('hide');
    viewVideo.classList.add('hide');
    view.src = '/api/preview/mjpeg?t=' + Date.now();
  }

  function startH264(){
    if (!previewEnabled) { stopPreviewStream(); return; }
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

  async function setPreviewEnabled(enabled, persist=true){
    previewEnabled = !!enabled;
    syncPreviewToggleUi();
    if (!previewEnabled && algoMode) {
      algoMode.value = 'off';
      updateFaceStatus({ mode: 'off', enabled: false });
    }
    syncAlgoModelEnabled();
    if (persist) {
      try {
        // 关预览时后端会同步把 algo_mode 置 off
        await postJSON('/api/settings', { preview_enabled: previewEnabled });
      } catch (e) { setStatus(String(e)); }
    }
    if (previewEnabled) {
      if (previewMode === 'h264_soft') startH264();
      else startMjpeg();
      applyTransform();
      setStatus('预览已开启');
    } else {
      stopPreviewStream();
      setStatus('预览已关闭（已停采）。若再打开失败，请点「恢复摄像头」');
      setTimeout(() => { pollDeviceStatus(); }, 800);
    }
  }

  async function applyPreviewMode(mode, persist=true){
    previewMode = mode;
    modeSel.value = mode;
    if (persist) {
      await postJSON('/api/settings', { preview_mode: mode });
    }
    if (!previewEnabled) {
      stopPreviewStream();
      syncPreviewToggleUi();
      return;
    }
    if (mode === 'h264_soft') startH264();
    else startMjpeg();
    applyTransform();
  }

  function syncDeredButtons(value){
    const v = Number(value);
    // 0=关闭；1~69≈适中；70~100≈强力（拖动滑条时也能看出档位）
    let key = 'off';
    if (v <= 0) key = 'off';
    else if (v >= 70) key = 'strong';
    else key = 'mid';
    const map = {
      off: document.getElementById('btn-dered-off'),
      mid: document.getElementById('btn-dered-mid'),
      strong: document.getElementById('btn-dered-strong'),
    };
    Object.entries(map).forEach(([k, btn]) => {
      if (!btn) return;
      btn.classList.toggle('is-active', k === key);
      btn.setAttribute('aria-pressed', k === key ? 'true' : 'false');
    });
  }

  async function setDeRed(value, immediate=false){
    deRed.value = String(value); deRedVal.textContent = String(value);
    syncDeredButtons(value);
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
  if (btnPreviewToggle) {
    btnPreviewToggle.onclick = () => setPreviewEnabled(!previewEnabled, true);
  }
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

  function fillAlgoModels(models, selected){
    if (!algoModel) return;
    const entries = Object.entries(models || {});
    if (!entries.length) return;
    algoModel.innerHTML = entries.map(([k,v]) => {
      const ready = v.ready !== false;
      const tip = ready ? (v.label || k) : `${v.label || k}（未下载）`;
      return `<option value="${k}" ${ready ? '' : 'disabled'}>${tip}</option>`;
    }).join('');
    const want = selected || 'auto';
    if ([...algoModel.options].some(o => o.value === want && !o.disabled)) algoModel.value = want;
    else algoModel.value = 'auto';
  }
  function syncAlgoModelEnabled(){
    if (algoMode) algoMode.disabled = !previewEnabled;
    if (!algoModel) return;
    algoModel.disabled = !(previewEnabled && algoMode && algoMode.value === 'object');
  }
  if (algoMode) {
    algoMode.onchange = async ()=>{
      try{
        const mode = algoMode.value || 'off';
        // 开算法时若预览关着，先打开预览
        if (mode !== 'off' && !previewEnabled) {
          await setPreviewEnabled(true, true);
        }
        syncAlgoModelEnabled();
        const data = await postJSON('/api/algo/mode', { mode });
        if (data.preview_enabled) {
          previewEnabled = true;
          syncPreviewToggleUi();
          if (previewMode === 'h264_soft') startH264();
          else startMjpeg();
        }
        const tip = mode === 'object' ? '已开启物体识别' : (mode === 'face' ? '已开启人脸检测' : '已关闭算法');
        setStatus(tip + '（AlgoSink）');
        updateFaceStatus(data);
        syncAlgoModelEnabled();
      }catch(e){ setStatus(String(e)); }
    };
  }
  if (algoModel) {
    algoModel.onchange = async ()=>{
      try{
        const model = algoModel.value || 'auto';
        const data = await postJSON('/api/algo/model', { model });
        setStatus('识别模型已切换：' + model);
        updateFaceStatus(data);
      }catch(e){ setStatus(String(e)); }
    };
  }
  function updateFaceStatus(s){
    if (!faceStatus) return;
    if (!s || s.mode === 'off' || !s.enabled) { faceStatus.textContent = '未开启'; return; }
    const ms = s.last_infer_ms ?? '-';
    if (s.mode === 'object') {
      const m = s.model_loaded || s.model || '-';
      faceStatus.textContent = `${m} · 物体 ${s.object_count ?? s.det_count ?? 0} 个 · ${ms}ms`;
      if (s.object_load_error) faceStatus.textContent = '模型加载失败';
    } else {
      faceStatus.textContent = `人脸 ${s.face_count ?? 0} 张 · 推理 ${ms}ms`;
    }
  }
  async function pollAlgo(){
    if (!algoMode || algoMode.value === 'off') return;
    try{
      const res = await fetch('/api/algo/status');
      const data = await res.json();
      updateFaceStatus(data);
    }catch(_){}
  }

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
      syncDeredButtons(deRed.value);
      osdBox.checked = !!s.osd; applyOsdVisible();
      fpsInput.value = String(s.preview_fps ?? 15); fpsVal.textContent = fpsInput.value;
      autoRecord.checked = !!s.auto_record;
      retention.value = String(s.retention_days ?? 7); retentionVal.textContent = retention.value;
      if (algoMode) algoMode.value = s.algo_mode || (s.face_detect ? 'face' : 'off');
      fillAlgoModels(s.algo_models, s.algo_model || 'auto');
      syncAlgoModelEnabled();
      showStorage(s.storage);
      const modes = s.preview_modes || {};
      modeSel.innerHTML = Object.entries(modes).map(([k,v]) =>
        `<option value="${k}">${v.label || k}</option>`
      ).join('');
      previewEnabled = s.preview_enabled !== false;
      syncPreviewToggleUi();
      await applyPreviewMode(s.preview_mode || 'mjpeg_hw', false);
      if (!previewEnabled) stopPreviewStream();
      if (algoMode && algoMode.value !== 'off') pollAlgo();
      else updateFaceStatus({ mode: 'off', enabled: false });
    }catch(_){ startMjpeg(); applyTransform(); }
    await syncRecordButtons();
  }

  tickOsd(); setInterval(tickOsd, 1000); applyOsdVisible();
  syncState();
  pollDeviceStatus();
  setInterval(syncRecordButtons, 5000);
  setInterval(pollAlgo, 1000);
  setInterval(pollDeviceStatus, 3000);
  // 日历绿点轻量刷新；正在看视频/全屏时不会拆掉播放器
  setInterval(() => {
    if (window.MediaCalendar && typeof window.MediaCalendar.softRefresh === 'function') {
      window.MediaCalendar.softRefresh().catch(()=>{});
    } else {
      refreshCalendar();
    }
  }, 30000);
})();
