(() => {
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
})();
