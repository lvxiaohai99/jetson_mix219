#!/usr/bin/env python3
"""Jetson IMX219 网页摄像头：预览/控制/OSD/回放。"""

from __future__ import annotations

import json
import logging
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
from flask import Flask, Response, jsonify, render_template_string, request, send_file

HOST = "0.0.0.0"
PORT = 8080
BASE_DIR = Path(__file__).resolve().parent
RECORD_DIR = BASE_DIR / "recordings"
SNAP_DIR = BASE_DIR / "snapshots"
SETTINGS_PATH = BASE_DIR / "settings.json"

GST_PIPELINE = (
    "nvarguscamerasrc sensor-id=0 ! "
    "video/x-raw(memory:NVMM),width=1280,height=720,framerate=30/1 ! "
    "nvvidconv ! video/x-raw,format=BGRx ! "
    "videoconvert ! video/x-raw,format=BGR ! "
    "appsink drop=1"
)

JPEG_QUALITY = 70
DEFAULT_SETTINGS = {
    "rotate": 0,
    "scale": 1.0,
    "de_red": 55,
    "osd": True,
}
LOG = logging.getLogger("webcam")

app = Flask(__name__)

INDEX_HTML = r"""
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>摄像头预览</title>
  <style>
    :root { color-scheme: dark; }
    * { box-sizing: border-box; }
    body {
      margin: 0; min-height: 100vh;
      font-family: "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
      background: radial-gradient(circle at top, #1f2937, #0b1220 60%);
      color: #e5e7eb;
    }
    main { width: min(96vw, 1100px); margin: 0 auto; padding: 1.2rem 0 2rem; text-align: center; }
    h1 { margin: 0 0 0.35rem; font-size: 1.4rem; font-weight: 600; }
    .sub { margin: 0 0 0.9rem; color: #9ca3af; font-size: 0.92rem; }
    .nav a { color: #93c5fd; margin: 0 .4rem; }
    .stage-wrap { display: flex; justify-content: center; }
    .stage {
      position: relative;
      display: inline-block;
      max-width: 100%;
      overflow: hidden; background: #000; border-radius: 12px;
      box-shadow: 0 12px 40px rgba(0,0,0,.45); line-height: 0;
    }
    #view {
      display: block;
      width: auto; height: auto;
      max-width: min(96vw, 1100px);
      max-height: 68vh;
      transform-origin: center center;
      transition: transform .15s ease;
      background: #000;
    }
    #osd-clock {
      position: absolute; left: 12px; top: 10px; z-index: 2;
      margin: 0; padding: .15rem .45rem; border-radius: 6px;
      font-size: 1rem; font-weight: 600; letter-spacing: .02em;
      color: #fff; background: rgba(0,0,0,.45);
      text-shadow: 0 1px 2px rgba(0,0,0,.8);
      pointer-events: none; user-select: none;
    }
    #osd-clock.hide { display: none; }
    .toolbar, .controls {
      display: flex; flex-wrap: wrap; gap: .5rem; justify-content: center; align-items: center;
      margin: .8rem auto;
    }
    .controls {
      gap: .8rem 1.2rem; max-width: 820px; background: rgba(15,23,42,.45);
      border: 1px solid #334155; border-radius: 12px; padding: .7rem 1rem;
    }
    button {
      border: 0; border-radius: 10px; padding: .55rem .9rem;
      background: #334155; color: #f8fafc; font-size: .92rem; cursor: pointer;
    }
    button:hover { background: #475569; }
    button.primary { background: #2563eb; }
    button.primary:hover { background: #1d4ed8; }
    button.danger { background: #b91c1c; }
    button.danger:hover { background: #991b1b; }
    button:disabled { opacity: .5; cursor: not-allowed; }
    .controls label { font-size: .9rem; color: #cbd5e1; }
    .controls input[type=range] { width: min(220px, 45vw); vertical-align: middle; }
    .status { min-height: 1.4rem; color: #93c5fd; font-size: .9rem; margin-bottom: .8rem; }
    .files {
      text-align: left; background: rgba(15,23,42,.55);
      border: 1px solid #334155; border-radius: 12px; padding: .8rem 1rem;
    }
    .files h2 { margin: 0 0 .5rem; font-size: 1rem; }
    .files ul { margin: 0; padding-left: 1.1rem; }
    .files li { margin: .25rem 0; }
    .files a { color: #93c5fd; }
    .empty { color: #94a3b8; }
  </style>
</head>
<body>
  <main>
    <h1>IMX219 实时预览</h1>
    <p class="sub">旋转在服务器完成，避免黑边；时间固定正向显示 · <span class="nav"><a href="/playback">打开回放</a></span></p>
    <div class="stage-wrap">
      <div class="stage">
        <div id="osd-clock">--:--:--</div>
        <img id="view" src="/video_feed" alt="live camera" />
      </div>
    </div>

    <div class="toolbar">
      <button type="button" id="btn-rot-l">左转</button>
      <button type="button" id="btn-rot-r">右转</button>
      <button type="button" id="btn-zoom-in">放大</button>
      <button type="button" id="btn-zoom-out">缩小</button>
      <button type="button" id="btn-reset">复位</button>
      <button type="button" class="primary" id="btn-lock">锁定当前设置</button>
      <button type="button" class="primary" id="btn-shot">截图</button>
      <button type="button" class="primary" id="btn-rec-start">开始录制</button>
      <button type="button" class="danger" id="btn-rec-stop" disabled>停止录制</button>
    </div>

    <div class="controls">
      <label for="de-red">去红边 <span id="de-red-val">55</span></label>
      <input id="de-red" type="range" min="0" max="100" value="55" />
      <button type="button" id="btn-dered-off">关闭</button>
      <button type="button" id="btn-dered-mid">适中</button>
      <button type="button" id="btn-dered-strong">强力</button>
      <label><input id="osd" type="checkbox" checked /> 显示时间</label>
    </div>
    <div class="status" id="status">就绪</div>

    <div class="files">
      <h2>最近文件</h2>
      <ul id="file-list"><li class="empty">暂无</li></ul>
    </div>
  </main>

  <script>
    const view = document.getElementById('view');
    const statusEl = document.getElementById('status');
    const deRed = document.getElementById('de-red');
    const deRedVal = document.getElementById('de-red-val');
    const osdBox = document.getElementById('osd');
    const osdClock = document.getElementById('osd-clock');
    let rotate = 0, scale = 1, deRedTimer = null;

    function setStatus(t){ statusEl.textContent = t; }
    function pad2(n){ return String(n).padStart(2,'0'); }
    function tickOsd(){
      const d = new Date();
      osdClock.textContent =
        d.getFullYear() + '-' + pad2(d.getMonth()+1) + '-' + pad2(d.getDate()) + ' ' +
        pad2(d.getHours()) + ':' + pad2(d.getMinutes()) + ':' + pad2(d.getSeconds());
    }
    function applyOsdVisible(){
      osdClock.classList.toggle('hide', !osdBox.checked);
    }
    function applyTransform(){
      // 旋转改由服务器处理，网页只缩放，这样不会出现大黑边
      view.style.transform = `scale(${scale})`;
      setStatus(`旋转 ${rotate}° · 缩放 ${scale.toFixed(1)}x · 去红边 ${deRed.value}`);
    }
    async function setRotate(next, resetScale=false){
      rotate = ((next % 360) + 360) % 360;
      if(resetScale) scale = 1;
      applyTransform();
      try{
        await postJSON('/api/settings', {rotate});
        // 刷新视频流，立刻按新方向显示
        view.src = '/video_feed?t=' + Date.now();
        setStatus(`已旋转到 ${rotate}°`);
      }catch(e){ setStatus(String(e)); }
    }
    async function postJSON(url, body){
      const res = await fetch(url, {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body||{})});
      const data = await res.json();
      if(!res.ok) throw new Error(data.error || '请求失败');
      return data;
    }
    async function setDeRed(value, immediate=false){
      deRed.value = String(value); deRedVal.textContent = String(value);
      const send = async ()=>{ try { await postJSON('/api/color', {de_red:Number(value)}); setStatus('去红边：'+value);} catch(e){ setStatus(String(e)); } };
      if(immediate) return send();
      clearTimeout(deRedTimer); deRedTimer = setTimeout(send, 120);
    }
    async function setOsd(on){
      applyOsdVisible();
      try { await postJSON('/api/settings', {osd: !!on}); setStatus(on?'已打开时间显示':'已关闭时间显示'); }
      catch(e){ setStatus(String(e)); }
    }

    document.getElementById('btn-rot-l').onclick = ()=>setRotate(rotate+270);
    document.getElementById('btn-rot-r').onclick = ()=>setRotate(rotate+90);
    document.getElementById('btn-zoom-in').onclick = ()=>{ scale=Math.min(3,+(scale+0.2).toFixed(1)); applyTransform(); };
    document.getElementById('btn-zoom-out').onclick = ()=>{ scale=Math.max(0.4,+(scale-0.2).toFixed(1)); applyTransform(); };
    document.getElementById('btn-reset').onclick = ()=>setRotate(0, true);
    document.getElementById('btn-lock').onclick = async ()=>{
      try{
        await postJSON('/api/settings', {rotate, scale:Number(scale), de_red:Number(deRed.value), osd: !!osdBox.checked, lock:true});
        setStatus('已锁定：下次打开自动恢复这些设置');
      }catch(e){ setStatus(String(e)); }
    };
    deRed.oninput = ()=>{ deRedVal.textContent=deRed.value; setDeRed(deRed.value); };
    document.getElementById('btn-dered-off').onclick = ()=>setDeRed(0,true);
    document.getElementById('btn-dered-mid').onclick = ()=>setDeRed(55,true);
    document.getElementById('btn-dered-strong').onclick = ()=>setDeRed(85,true);
    osdBox.onchange = ()=>setOsd(osdBox.checked);

    document.getElementById('btn-shot').onclick = async ()=>{
      try{
        setStatus('正在截图...');
        // 画面已在服务器按当前方向旋转，这里不再二次旋转
        const res = await fetch('/api/snapshot');
        if(!res.ok) throw new Error('截图失败');
        const blob = await res.blob();
        const name = res.headers.get('X-Filename') || `snapshot_${Date.now()}.jpg`;
        const a=document.createElement('a'); a.href=URL.createObjectURL(blob); a.download=name; a.click();
        URL.revokeObjectURL(a.href); setStatus('截图完成：'+name); refreshFiles();
      }catch(e){ setStatus(String(e)); }
    };

    const btnStart=document.getElementById('btn-rec-start');
    const btnStop=document.getElementById('btn-rec-stop');
    btnStart.onclick = async ()=>{
      try{
        const data = await postJSON('/api/record/start', {});
        btnStart.disabled=true; btnStop.disabled=false; setStatus('录制中：'+data.file);
      }catch(e){ setStatus(String(e)); }
    };
    btnStop.onclick = async ()=>{
      try{
        const data = await postJSON('/api/record/stop', {});
        btnStart.disabled=false; btnStop.disabled=true; setStatus('录制完成：'+(data.file||'')); refreshFiles();
      }catch(e){ setStatus(String(e)); }
    };

    async function refreshFiles(){
      try{
        const res=await fetch('/api/files'); const data=await res.json();
        const ul=document.getElementById('file-list');
        const items=[...(data.snapshots||[]), ...(data.recordings||[])];
        if(!items.length){ ul.innerHTML='<li class="empty">暂无</li>'; return; }
        ul.innerHTML = items.map(f=>`<li><a href="${f.url}" download>${f.name}</a> <span class="empty">(${f.kind})</span></li>`).join('');
      }catch(_){}
    }
    async function syncState(){
      try{
        const res=await fetch('/api/settings'); const s=await res.json();
        rotate = Number(s.rotate||0); scale = Number(s.scale||1);
        deRed.value = String(s.de_red ?? 55); deRedVal.textContent = deRed.value;
        osdBox.checked = !!s.osd; applyOsdVisible(); applyTransform();
      }catch(_){ applyTransform(); }
      try{
        const res=await fetch('/api/record/status'); const data=await res.json();
        btnStart.disabled=!!data.recording; btnStop.disabled=!data.recording;
        if(data.recording) setStatus('录制中：'+data.file);
      }catch(_){}
    }
    tickOsd(); setInterval(tickOsd, 1000); applyOsdVisible();
    refreshFiles(); syncState();
  </script>
</body>
</html>
"""

PLAYBACK_HTML = r"""
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>录像回放</title>
  <style>
    :root { color-scheme: dark; }
    * { box-sizing: border-box; }
    body {
      margin: 0; min-height: 100vh;
      font-family: "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
      background: radial-gradient(circle at top, #1f2937, #0b1220 60%);
      color: #e5e7eb;
    }
    main { width: min(96vw, 1100px); margin: 0 auto; padding: 1.2rem 0 2rem; }
    h1 { margin: 0 0 .35rem; font-size: 1.4rem; }
    .sub { color:#9ca3af; margin: 0 0 1rem; }
    .sub a { color:#93c5fd; }
    .layout { display:grid; grid-template-columns: 280px 1fr; gap: 1rem; }
    @media (max-width: 860px){ .layout { grid-template-columns: 1fr; } }
    .panel, .player {
      background: rgba(15,23,42,.55); border:1px solid #334155; border-radius:12px; padding:.8rem;
    }
    .panel h2, .player h2 { margin:0 0 .6rem; font-size:1rem; }
    .list { max-height: 70vh; overflow:auto; }
    .item {
      width:100%; text-align:left; border:0; background:#1e293b; color:#e2e8f0;
      border-radius:8px; padding:.55rem .7rem; margin:0 0 .45rem; cursor:pointer;
    }
    .item:hover, .item.active { background:#2563eb; }
    .meta { display:block; color:#94a3b8; font-size:.8rem; margin-top:.2rem; }
    video { width:100%; background:#000; border-radius:10px; max-height:62vh; }
    .timeline { margin-top:.7rem; }
    .timeline input[type=range]{ width:100%; }
    .row { display:flex; justify-content:space-between; color:#94a3b8; font-size:.85rem; margin-top:.25rem; }
    .status { min-height:1.2rem; color:#93c5fd; margin-top:.5rem; }
  </style>
</head>
<body>
  <main>
    <h1>录像回放</h1>
    <p class="sub"><a href="/">返回实时预览</a> · 点左侧录像，用时间轴拖动回放</p>
    <div class="layout">
      <div class="panel">
        <h2>录像列表</h2>
        <div class="list" id="list"><div class="meta">加载中...</div></div>
      </div>
      <div class="player">
        <h2 id="title">未选择</h2>
        <video id="video" controls playsinline></video>
        <div class="timeline">
          <input id="seek" type="range" min="0" max="1000" value="0" />
          <div class="row"><span id="cur">00:00</span><span id="dur">00:00</span></div>
        </div>
        <div class="status" id="status"></div>
      </div>
    </div>
  </main>
  <script>
    const listEl=document.getElementById('list');
    const video=document.getElementById('video');
    const seek=document.getElementById('seek');
    const cur=document.getElementById('cur');
    const dur=document.getElementById('dur');
    const title=document.getElementById('title');
    const statusEl=document.getElementById('status');
    let dragging=false;

    function fmt(sec){
      sec=Math.max(0, Math.floor(sec||0));
      const m=Math.floor(sec/60), s=sec%60;
      return String(m).padStart(2,'0')+':'+String(s).padStart(2,'0');
    }
    function setStatus(t){ statusEl.textContent=t; }

    async function loadList(){
      const res=await fetch('/api/playback/list');
      const data=await res.json();
      const items=data.items||[];
      if(!items.length){ listEl.innerHTML='<div class="meta">暂无录像</div>'; return; }
      listEl.innerHTML = items.map(it => `
        <button class="item" data-id="${it.id}">
          ${it.title}
          <span class="meta">${it.start_text} · ${it.duration_text} · ${it.size_mb}MB ${it.playable?'':'· 需转码'}</span>
        </button>`).join('');
      [...listEl.querySelectorAll('.item')].forEach(btn=>{
        btn.onclick = ()=> openItem(btn.dataset.id, btn);
      });
    }

    async function openItem(id, btn){
      [...listEl.querySelectorAll('.item')].forEach(x=>x.classList.remove('active'));
      if(btn) btn.classList.add('active');
      setStatus('准备播放...');
      const res=await fetch('/api/playback/open/'+id, {method:'POST'});
      const data=await res.json();
      if(!res.ok){ setStatus(data.error||'打开失败'); return; }
      title.textContent = data.title;
      video.src = data.url + '?t=' + Date.now();
      video.load();
      setStatus(data.converted ? '已转码，可拖动时间轴回放' : '可拖动时间轴回放');
    }

    video.ontimeupdate = ()=>{
      if(dragging || !video.duration) return;
      seek.value = String(Math.floor((video.currentTime/video.duration)*1000));
      cur.textContent = fmt(video.currentTime);
      dur.textContent = fmt(video.duration);
    };
    video.onloadedmetadata = ()=>{ dur.textContent=fmt(video.duration); };
    seek.oninput = ()=>{ dragging=true; };
    seek.onchange = ()=>{
      if(video.duration){ video.currentTime = (Number(seek.value)/1000)*video.duration; }
      dragging=false;
    };
    loadList();
  </script>
</body>
</html>
"""


def _default_settings() -> dict:
    return dict(DEFAULT_SETTINGS)


def load_settings() -> dict:
    data = _default_settings()
    if SETTINGS_PATH.exists():
        try:
            saved = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
            if isinstance(saved, dict):
                data.update(saved)
        except Exception as e:
            LOG.warning("读取设置失败: %s", e)
    data["rotate"] = int(data.get("rotate", 0)) % 360
    data["scale"] = float(data.get("scale", 1.0))
    data["de_red"] = int(max(0, min(100, int(data.get("de_red", 55)))))
    data["osd"] = bool(data.get("osd", True))
    return data


def save_settings(patch: dict) -> dict:
    data = load_settings()
    if "rotate" in patch:
        data["rotate"] = int(patch["rotate"]) % 360
    if "scale" in patch:
        data["scale"] = float(patch["scale"])
    if "de_red" in patch:
        data["de_red"] = int(max(0, min(100, int(patch["de_red"]))))
    if "osd" in patch:
        data["osd"] = bool(patch["osd"])
    SETTINGS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


def rotate_frame(frame: np.ndarray, degrees: int) -> np.ndarray:
    degrees = int(degrees) % 360
    if degrees == 90:
        return cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)
    if degrees == 180:
        return cv2.rotate(frame, cv2.ROTATE_180)
    if degrees == 270:
        return cv2.rotate(frame, cv2.ROTATE_90_COUNTERCLOCKWISE)
    return frame


def build_edge_weight(h: int, w: int) -> np.ndarray:
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    cy = (h - 1) / 2.0
    cx = (w - 1) / 2.0
    ry = np.abs(yy - cy) / max(cy, 1.0)
    rx = np.abs(xx - cx) / max(cx, 1.0)
    r = np.clip(np.sqrt(rx * rx + ry * ry) / 1.35, 0.0, 1.0)
    return (r * r).astype(np.float32)


def correct_red_edge(frame: np.ndarray, strength: float, weight: np.ndarray) -> np.ndarray:
    if strength <= 0.001:
        return frame
    out = frame.astype(np.float32)
    out[:, :, 2] *= 1.0 - weight * strength * 0.42
    out[:, :, 1] *= 1.0 + weight * strength * 0.10
    out[:, :, 0] *= 1.0 + weight * strength * 0.06
    return np.clip(out, 0, 255).astype(np.uint8)


def draw_osd(frame: np.ndarray) -> np.ndarray:
    text = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    origin = (16, 40)
    cv2.putText(frame, text, origin, cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 0), 3, cv2.LINE_AA)
    cv2.putText(frame, text, origin, cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 2, cv2.LINE_AA)
    return frame


def _probe_duration(path: Path) -> float:
    try:
        out = subprocess.check_output(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            text=True,
            timeout=10,
        ).strip()
        return float(out)
    except Exception:
        return 0.0


def _ensure_mp4(src_path: Path) -> tuple[Path, bool]:
    """确保有可播放 mp4。新录制已是 mp4；旧 avi 会转码一次。"""
    if src_path.suffix.lower() == ".mp4" and src_path.exists() and src_path.stat().st_size > 0:
        return src_path, False
    mp4_path = src_path.with_suffix(".mp4")
    if mp4_path.exists() and mp4_path.stat().st_size > 0:
        return mp4_path, False
    if not src_path.exists():
        raise FileNotFoundError(f"找不到录像: {src_path.name}")
    cmd = [
        "ffmpeg", "-y", "-i", str(src_path),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-an", "-movflags", "+faststart",
        str(mp4_path),
    ]
    LOG.info("转码回放文件: %s", " ".join(cmd))
    subprocess.check_call(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return mp4_path, True


class FFmpegH264Writer:
    """用 ffmpeg/libx264 边录边压缩，体积远小于 MJPG。"""

    def __init__(self, path: Path, width: int, height: int, fps: float = 20.0, crf: int = 23) -> None:
        # H.264 要求宽高为偶数
        self.width = width - (width % 2)
        self.height = height - (height % 2)
        self.path = path
        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "rawvideo",
            "-pix_fmt", "bgr24",
            "-s", f"{self.width}x{self.height}",
            "-r", str(fps),
            "-i", "pipe:0",
            "-an",
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", str(crf),
            "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            str(path),
        ]
        self.proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )

    def write(self, frame: np.ndarray) -> None:
        if self.proc.stdin is None:
            raise RuntimeError("录像管道已关闭")
        if frame.shape[1] != self.width or frame.shape[0] != self.height:
            frame = cv2.resize(frame, (self.width, self.height), interpolation=cv2.INTER_AREA)
        self.proc.stdin.write(frame.tobytes())

    def release(self) -> None:
        try:
            if self.proc.stdin is not None:
                self.proc.stdin.close()
        except Exception:
            pass
        try:
            self.proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()
        err = b""
        if self.proc.stderr is not None:
            try:
                err = self.proc.stderr.read() or b""
            except Exception:
                pass
        if self.proc.returncode not in (0, None) and err:
            LOG.warning("ffmpeg 结束码=%s: %s", self.proc.returncode, err.decode(errors="ignore")[:300])


class CameraStream:
    def __init__(self, pipeline: str) -> None:
        self.pipeline = pipeline
        self._lock = threading.Lock()
        self._frame: np.ndarray | None = None
        self._jpeg: bytes | None = None
        self._running = False
        self._thread: threading.Thread | None = None

        self._recording = False
        self._writer: FFmpegH264Writer | None = None
        self._record_path: Path | None = None
        self._record_rotate = 0

        settings = load_settings()
        self._de_red = int(settings["de_red"])
        self._osd = bool(settings["osd"])
        self._view_rotate = int(settings.get("rotate", 0)) % 360
        self._weight: np.ndarray | None = None
        self._weight_shape: tuple[int, int] | None = None

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, name="camera-loop", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=3)
            self._thread = None
        self.stop_recording()

    def get_jpeg(self) -> bytes | None:
        with self._lock:
            return self._jpeg

    def get_frame_copy(self) -> np.ndarray | None:
        with self._lock:
            if self._frame is None:
                return None
            return self._frame.copy()

    def get_de_red(self) -> int:
        with self._lock:
            return self._de_red

    def set_de_red(self, value: int) -> int:
        value = int(max(0, min(100, value)))
        with self._lock:
            self._de_red = value
        return value

    def set_osd(self, enabled: bool) -> bool:
        with self._lock:
            self._osd = bool(enabled)
            return self._osd

    def get_osd(self) -> bool:
        with self._lock:
            return self._osd

    def set_view_rotate(self, degrees: int) -> int:
        degrees = int(degrees) % 360
        with self._lock:
            self._view_rotate = degrees
        return degrees

    def get_view_rotate(self) -> int:
        with self._lock:
            return self._view_rotate

    def record_info(self) -> dict:
        with self._lock:
            return {
                "recording": self._recording,
                "file": self._record_path.name if self._record_path else None,
            }

    def start_recording(self, rotate: int = 0) -> Path:
        with self._lock:
            if self._recording:
                raise RuntimeError("已经在录制中")
            if self._frame is None:
                raise RuntimeError("摄像头还没有画面")
            RECORD_DIR.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            path = RECORD_DIR / f"record_{stamp}.mp4"
            sample = self._frame
            h, w = sample.shape[:2]
            writer = FFmpegH264Writer(path, width=w, height=h, fps=20.0, crf=23)
            self._writer = writer
            self._record_path = path
            self._record_rotate = 0
            self._recording = True
            LOG.info("开始录制(H.264): %s", path)
            return path

    def stop_recording(self) -> Path | None:
        with self._lock:
            if not self._recording:
                return None
            path = self._record_path
            writer = self._writer
            self._writer = None
            self._recording = False
            self._record_path = None
        if writer is not None:
            writer.release()
        LOG.info("停止录制: %s", path)
        return path

    def _get_weight(self, h: int, w: int) -> np.ndarray:
        if self._weight is None or self._weight_shape != (h, w):
            self._weight = build_edge_weight(h, w)
            self._weight_shape = (h, w)
        return self._weight

    def _loop(self) -> None:
        while self._running:
            cap = cv2.VideoCapture(self.pipeline, cv2.CAP_GSTREAMER)
            if not cap.isOpened():
                LOG.error("打不开摄像头，3 秒后重试")
                time.sleep(3)
                continue
            LOG.info("摄像头已打开")
            fail = 0
            try:
                while self._running:
                    ok, frame = cap.read()
                    if not ok or frame is None:
                        fail += 1
                        if fail >= 30:
                            LOG.warning("连续读帧失败，准备重开摄像头")
                            break
                        time.sleep(0.05)
                        continue
                    fail = 0

                    with self._lock:
                        strength = self._de_red / 100.0
                        view_rotate = self._view_rotate
                    if strength > 0:
                        h, w = frame.shape[:2]
                        frame = correct_red_edge(frame, strength, self._get_weight(h, w))
                    # 服务器端旋转，输出宽高跟着变，网页不再 CSS 旋转，避免黑边
                    frame = rotate_frame(frame, view_rotate)

                    # 预览流不烧录时间：网页用固定叠加层
                    ok, buf = cv2.imencode(
                        ".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY]
                    )
                    if not ok:
                        continue
                    jpeg = buf.tobytes()
                    with self._lock:
                        self._frame = frame
                        self._jpeg = jpeg
                        if self._recording and self._writer is not None:
                            out = frame
                            if self._osd:
                                out = draw_osd(frame.copy())
                            self._writer.write(out)
            finally:
                cap.release()
                LOG.info("摄像头已关闭")
            if self._running:
                time.sleep(1)


camera = CameraStream(GST_PIPELINE)


def _list_files() -> dict:
    SNAP_DIR.mkdir(parents=True, exist_ok=True)
    RECORD_DIR.mkdir(parents=True, exist_ok=True)

    def collect(folder: Path, kind: str, url_prefix: str, patterns: list[str]):
        items = []
        files: list[Path] = []
        for pat in patterns:
            files.extend(folder.glob(pat))
        for p in sorted(set(files), key=lambda x: x.stat().st_mtime, reverse=True):
            if not p.is_file():
                continue
            # 有同名 avi+mp4 时只显示 mp4
            if p.suffix.lower() == ".avi" and p.with_suffix(".mp4").exists():
                continue
            items.append({"name": p.name, "kind": kind, "url": f"{url_prefix}/{p.name}"})
        return items[:20]

    return {
        "snapshots": collect(SNAP_DIR, "截图", "/files/snapshots", ["*.jpg"]),
        "recordings": collect(RECORD_DIR, "录像", "/files/recordings", ["*.mp4", "*.avi"]),
    }


def _playback_items() -> list[dict]:
    RECORD_DIR.mkdir(parents=True, exist_ok=True)
    items = []
    seen: set[str] = set()
    files = list(RECORD_DIR.glob("record_*.mp4")) + list(RECORD_DIR.glob("record_*.avi"))
    files = sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)
    for path in files:
        if path.stem in seen:
            continue
        # 优先 mp4
        mp4 = path if path.suffix.lower() == ".mp4" else path.with_suffix(".mp4")
        avi = path if path.suffix.lower() == ".avi" else path.with_suffix(".avi")
        if mp4.exists():
            main = mp4
        elif avi.exists():
            main = avi
        else:
            continue
        seen.add(path.stem)
        start_text = path.stem.replace("record_", "")
        try:
            dt = datetime.strptime(start_text, "%Y%m%d_%H%M%S")
            start_text = dt.strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            pass
        duration = _probe_duration(main)
        size_mb = round(main.stat().st_size / 1024 / 1024, 2)
        items.append(
            {
                "id": path.stem,
                "title": main.name,
                "start_text": start_text,
                "duration": duration,
                "duration_text": f"{int(duration // 60):02d}:{int(duration % 60):02d}",
                "size_mb": size_mb,
                "playable": main.suffix.lower() == ".mp4",
            }
        )
    return items


@app.route("/")
def index():
    return render_template_string(INDEX_HTML)


@app.route("/playback")
def playback_page():
    return render_template_string(PLAYBACK_HTML)


@app.route("/video_feed")
def video_feed():
    def generate():
        while True:
            jpeg = camera.get_jpeg()
            if jpeg is None:
                time.sleep(0.05)
                continue
            yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + jpeg + b"\r\n"
            time.sleep(0.03)

    return Response(generate(), mimetype="multipart/x-mixed-replace; boundary=frame")


@app.route("/health")
def health():
    ready = camera.get_jpeg() is not None
    info = camera.record_info()
    return {
        "ok": ready,
        "camera": "ready" if ready else "starting",
        "recording": info["recording"],
        "de_red": camera.get_de_red(),
        "osd": camera.get_osd(),
    }, 200 if ready else 503


@app.route("/api/settings", methods=["GET", "POST"])
def api_settings():
    if request.method == "GET":
        return jsonify(load_settings())
    data = request.get_json(silent=True) or {}
    saved = save_settings(data)
    if "de_red" in data:
        camera.set_de_red(saved["de_red"])
    if "osd" in data:
        camera.set_osd(saved["osd"])
    if "rotate" in data:
        camera.set_view_rotate(saved["rotate"])
    return jsonify({"ok": True, **saved})


@app.route("/api/color", methods=["GET", "POST"])
def api_color():
    if request.method == "GET":
        return jsonify({"de_red": camera.get_de_red()})
    data = request.get_json(silent=True) or {}
    try:
        value = int(data.get("de_red", camera.get_de_red()))
    except (TypeError, ValueError):
        return jsonify({"error": "de_red 必须是 0~100"}), 400
    value = camera.set_de_red(value)
    save_settings({"de_red": value})
    return jsonify({"ok": True, "de_red": value})


@app.route("/api/snapshot")
def api_snapshot():
    frame = camera.get_frame_copy()
    if frame is None:
        return jsonify({"error": "还没有画面"}), 503
    # 预览帧已按当前方向旋转，避免二次旋转导致方向错误
    out = frame
    if camera.get_osd():
        out = draw_osd(out.copy())
    ok, buf = cv2.imencode(".jpg", out, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
    if not ok:
        return jsonify({"error": "编码失败"}), 500
    SNAP_DIR.mkdir(parents=True, exist_ok=True)
    name = f"snapshot_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
    path = SNAP_DIR / name
    path.write_bytes(buf.tobytes())
    resp = send_file(path, mimetype="image/jpeg", as_attachment=True, download_name=name)
    resp.headers["X-Filename"] = name
    return resp


@app.route("/api/record/start", methods=["POST"])
def api_record_start():
    try:
        path = camera.start_recording()
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"ok": True, "file": path.name})


@app.route("/api/record/stop", methods=["POST"])
def api_record_stop():
    path = camera.stop_recording()
    if path is None:
        return jsonify({"error": "当前没有在录制"}), 400
    return jsonify({"ok": True, "file": path.name})


@app.route("/api/record/status")
def api_record_status():
    return jsonify(camera.record_info())


@app.route("/api/files")
def api_files():
    return jsonify(_list_files())


@app.route("/api/playback/list")
def api_playback_list():
    return jsonify({"items": _playback_items()})


@app.route("/api/playback/open/<rec_id>", methods=["POST"])
def api_playback_open(rec_id: str):
    if "/" in rec_id or ".." in rec_id:
        return jsonify({"error": "非法编号"}), 400
    mp4 = RECORD_DIR / f"{rec_id}.mp4"
    avi = RECORD_DIR / f"{rec_id}.avi"
    src = mp4 if mp4.exists() else avi
    if not src.exists():
        return jsonify({"error": "录像不存在"}), 404
    try:
        playable, converted = _ensure_mp4(src)
    except Exception as e:
        return jsonify({"error": f"转码失败: {e}"}), 500
    return jsonify(
        {
            "ok": True,
            "title": playable.name,
            "url": f"/files/recordings/{playable.name}",
            "converted": converted,
            "duration": _probe_duration(playable),
        }
    )


@app.route("/files/snapshots/<path:name>")
def file_snapshot(name: str):
    path = (SNAP_DIR / name).resolve()
    if not str(path).startswith(str(SNAP_DIR.resolve())) or not path.is_file():
        return jsonify({"error": "not found"}), 404
    return send_file(path, as_attachment=True, download_name=path.name)


@app.route("/files/recordings/<path:name>")
def file_recording(name: str):
    path = (RECORD_DIR / name).resolve()
    if not str(path).startswith(str(RECORD_DIR.resolve())) or not path.is_file():
        return jsonify({"error": "not found"}), 404
    # mp4 给网页播放，不要强制 attachment
    as_attachment = path.suffix.lower() != ".mp4"
    return send_file(path, as_attachment=as_attachment, download_name=path.name)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    SNAP_DIR.mkdir(parents=True, exist_ok=True)
    RECORD_DIR.mkdir(parents=True, exist_ok=True)
    settings = load_settings()
    camera.set_de_red(settings["de_red"])
    camera.set_osd(settings["osd"])
    camera.set_view_rotate(settings["rotate"])
    camera.start()
    for _ in range(50):
        if camera.get_jpeg() is not None:
            break
        time.sleep(0.1)
    if camera.get_jpeg() is None:
        LOG.warning("启动时还没拿到画面，请稍后刷新网页")
    else:
        LOG.info("首帧已就绪，开始提供网页服务")
    try:
        app.run(host=HOST, port=PORT, threaded=True, use_reloader=False)
    finally:
        camera.stop()


if __name__ == "__main__":
    main()
