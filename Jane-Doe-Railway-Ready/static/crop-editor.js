(function () {
  'use strict';

  const state = { input: null, image: null, url: '', rect: null, action: null, start: null };
  const originals = new WeakMap();
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

  function toast(message) {
    if (typeof window.toast === 'function') return window.toast(message);
    const node = $('#toast');
    if (node) { node.textContent = message; node.classList.add('show'); setTimeout(() => node.classList.remove('show'), 2500); }
  }

  function buildUI() {
    $('#image-editor-modal')?.remove();
    if ($('#jd-crop-modal')) return;
    const style = document.createElement('style');
    style.textContent = `
      #jd-crop-modal[hidden]{display:none!important}#jd-crop-modal{position:fixed;inset:0;z-index:1000;background:#000d;display:grid;place-items:center;padding:18px}
      .jd-crop-dialog{width:min(920px,96vw);max-height:96vh;overflow:auto;background:#0e0e0e;border:1px solid #8d3039;padding:20px;color:#f5f5f5}
      .jd-crop-dialog h2{margin:3px 0 4px}.jd-crop-help{color:#aaa;font-size:12px;margin:0 0 12px}.jd-crop-presets,.jd-crop-actions{display:flex;flex-wrap:wrap;gap:8px;margin:10px 0}
      .jd-crop-presets button,.jd-crop-actions button{padding:8px 12px}.jd-crop-stage{position:relative;width:100%;height:min(58vh,560px);min-height:320px;background:#050505;border:1px solid #4b2529;overflow:hidden;touch-action:none;user-select:none}
      .jd-crop-stage>img{width:100%;height:100%;display:block;object-fit:contain;pointer-events:none}.jd-crop-box{position:absolute;border:2px solid #fff;box-shadow:0 0 0 9999px #000a;cursor:move;touch-action:none}
      .jd-crop-box:after{content:'Drag to move · drag edges or corners to resize';position:absolute;left:50%;top:50%;transform:translate(-50%,-50%);background:#000c;padding:6px 9px;font:11px sans-serif;white-space:nowrap;pointer-events:none}
      .jd-crop-handle{position:absolute;width:18px;height:18px;border-radius:50%;background:#fff;border:3px solid #c53b47}.jd-crop-handle[data-h=nw]{left:-10px;top:-10px;cursor:nwse-resize}.jd-crop-handle[data-h=ne]{right:-10px;top:-10px;cursor:nesw-resize}.jd-crop-handle[data-h=sw]{left:-10px;bottom:-10px;cursor:nesw-resize}.jd-crop-handle[data-h=se]{right:-10px;bottom:-10px;cursor:nwse-resize}
      .jd-crop-handle[data-h=n]{left:50%;top:-10px;transform:translateX(-50%);cursor:ns-resize}.jd-crop-handle[data-h=s]{left:50%;bottom:-10px;transform:translateX(-50%);cursor:ns-resize}.jd-crop-handle[data-h=e]{right:-10px;top:50%;transform:translateY(-50%);cursor:ew-resize}.jd-crop-handle[data-h=w]{left:-10px;top:50%;transform:translateY(-50%);cursor:ew-resize}
      .jd-crop-options{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:12px}.jd-crop-options label{margin:0}.jd-crop-actions{justify-content:flex-end}.jd-crop-primary{background:#f1f1f1!important;color:#080808!important}.jd-crop-secondary{background:#171717!important;color:#fff!important}
      .image-edit-button{display:block!important;width:100%!important;margin-top:8px!important}.image-edit-button[disabled]{opacity:.5!important;cursor:not-allowed!important}@media(max-width:600px){.jd-crop-stage{height:48vh;min-height:260px}.jd-crop-options{grid-template-columns:1fr}.jd-crop-box:after{content:'Move · resize handles'}}`;
    document.head.append(style);
    document.body.insertAdjacentHTML('beforeend', `
      <div id="jd-crop-modal" hidden>
        <div class="jd-crop-dialog" role="dialog" aria-modal="true" aria-labelledby="jd-crop-title">
          <p class="eyebrow">VISUAL IMAGE EDITOR</p><h2 id="jd-crop-title">Drag to crop or fit image</h2>
          <p class="jd-crop-help">Draw a box anywhere on the image, move it, or drag any edge or corner exactly like a screenshot crop.</p>
          <div class="jd-crop-presets"><button type="button" data-shape="banner">Banner</button><button type="button" data-shape="square">Square</button><button type="button" data-shape="full">Full image</button></div>
          <div class="jd-crop-stage"><img alt="Image being cropped"><div class="jd-crop-box">${['nw','n','ne','e','se','s','sw','w'].map(h=>`<i class="jd-crop-handle" data-h="${h}"></i>`).join('')}</div></div>
          <div class="jd-crop-options"><label>Result<select id="jd-crop-mode"><option value="crop">Crop to my box</option><option value="fit">Fit full image inside my box</option></select></label><label>Fit background<input id="jd-crop-bg" type="color" value="#2b2d31"></label></div>
          <div class="jd-crop-actions"><button type="button" class="jd-crop-secondary" data-action="original">Back to original</button><button type="button" class="jd-crop-secondary" data-action="cancel">Cancel</button><button type="button" class="jd-crop-primary" data-action="apply">Use edited image</button></div>
        </div>
      </div>`);
    const stage = $('.jd-crop-stage');
    stage.addEventListener('pointerdown', pointerDown);
    window.addEventListener('pointermove', pointerMove);
    window.addEventListener('pointerup', () => state.action = null);
    $$('[data-shape]', $('#jd-crop-modal')).forEach(b => b.onclick = () => preset(b.dataset.shape));
    $('[data-action="original"]').onclick = resetOriginal;
    $('[data-action="cancel"]').onclick = close;
    $('[data-action="apply"]').onclick = apply;
    $('#jd-crop-modal').addEventListener('pointerdown', e => { if (e.target.id === 'jd-crop-modal') close(); });
  }

  function imageBounds() {
    const stage = $('.jd-crop-stage'), sw = stage.clientWidth, sh = stage.clientHeight;
    const scale = Math.min(sw / state.image.naturalWidth, sh / state.image.naturalHeight);
    const w = state.image.naturalWidth * scale, h = state.image.naturalHeight * scale;
    return { x: (sw - w) / 2, y: (sh - h) / 2, w, h, scale };
  }

  function paint() {
    const r = state.rect, box = $('.jd-crop-box');
    if (!r || !box) return;
    Object.assign(box.style, { left: r.x + 'px', top: r.y + 'px', width: r.w + 'px', height: r.h + 'px' });
  }

  function preset(shape) {
    if (!state.image) return;
    const b = imageBounds();
    if (shape === 'full') { state.rect = { x:b.x, y:b.y, w:b.w, h:b.h }; return paint(); }
    const ratio = shape === 'banner' ? 16 / 5 : 1;
    let w = b.w * .82, h = w / ratio;
    if (h > b.h * .82) { h = b.h * .82; w = h * ratio; }
    state.rect = { x:b.x + (b.w-w)/2, y:b.y + (b.h-h)/2, w, h }; paint();
  }

  function pointerDown(e) {
    if (!state.image) return;
    const stage = $('.jd-crop-stage'), sr = stage.getBoundingClientRect(), b = imageBounds();
    const box = e.target.closest('.jd-crop-box');
    state.start = { cx:e.clientX, cy:e.clientY, rect:{...state.rect} };
    if (box) state.action = e.target.dataset.h || 'move';
    else {
      const x = Math.max(b.x, Math.min(b.x+b.w, e.clientX-sr.left)), y = Math.max(b.y, Math.min(b.y+b.h, e.clientY-sr.top));
      state.action = 'draw'; state.start.rect = {x,y,w:1,h:1}; state.rect = {...state.start.rect}; paint();
    }
    stage.setPointerCapture?.(e.pointerId); e.preventDefault();
  }

  function pointerMove(e) {
    if (!state.action || !state.image) return;
    const stage = $('.jd-crop-stage'), sr = stage.getBoundingClientRect(), b = imageBounds(), o = state.start.rect;
    const dx=e.clientX-state.start.cx, dy=e.clientY-state.start.cy, min=28; let {x,y,w,h}=o;
    if (state.action === 'draw') {
      const px=Math.max(b.x,Math.min(b.x+b.w,e.clientX-sr.left)), py=Math.max(b.y,Math.min(b.y+b.h,e.clientY-sr.top));
      x=Math.min(o.x,px); y=Math.min(o.y,py); w=Math.max(2,Math.abs(px-o.x)); h=Math.max(2,Math.abs(py-o.y));
    } else if (state.action === 'move') {
      x=Math.max(b.x,Math.min(b.x+b.w-w,o.x+dx)); y=Math.max(b.y,Math.min(b.y+b.h-h,o.y+dy));
    } else {
      if(state.action.includes('e'))w=Math.max(min,Math.min(b.x+b.w-o.x,o.w+dx));
      if(state.action.includes('s'))h=Math.max(min,Math.min(b.y+b.h-o.y,o.h+dy));
      if(state.action.includes('w')){x=Math.max(b.x,Math.min(o.x+o.w-min,o.x+dx));w=o.w+(o.x-x)}
      if(state.action.includes('n')){y=Math.max(b.y,Math.min(o.y+o.h-min,o.y+dy));h=o.h+(o.y-y)}
    }
    state.rect={x,y,w,h}; paint(); e.preventDefault();
  }

  function open(input) {
    const file = input.files?.[0];
    if (!file) return toast('Choose an image first');
    if (file.type === 'image/gif') return toast('Animated GIF cropping is unavailable. Use PNG, JPG, or WEBP.');
    buildUI(); state.input=input;
    if (!originals.has(input)) originals.set(input,file);
    if (state.url) URL.revokeObjectURL(state.url);
    state.url=URL.createObjectURL(file); const img=$('.jd-crop-stage>img'); state.image=img;
    img.onload=()=>requestAnimationFrame(()=>preset('full')); img.src=state.url; $('#jd-crop-modal').hidden=false;
  }

  function resetOriginal() {
    const original=state.input&&originals.get(state.input);
    if (!original) return preset('full');
    const dt=new DataTransfer();dt.items.add(original);state.input.files=dt.files;
    if(state.url)URL.revokeObjectURL(state.url);state.url=URL.createObjectURL(original);state.image=$('.jd-crop-stage>img');state.image.onload=()=>requestAnimationFrame(()=>preset('full'));state.image.src=state.url;$('#jd-crop-mode').value='crop';
  }

  function apply() {
    if (!state.image || !state.rect || !state.input) return;
    const b=imageBounds(), r=state.rect, mode=$('#jd-crop-mode').value, canvas=document.createElement('canvas'), ratio=r.w/r.h;
    canvas.width=Math.min(1400,Math.max(1,Math.round(mode==='crop'?r.w/b.scale:1200)));canvas.height=Math.max(1,Math.round(canvas.width/ratio));
    const c=canvas.getContext('2d');c.fillStyle=$('#jd-crop-bg').value;c.fillRect(0,0,canvas.width,canvas.height);
    if(mode==='crop')c.drawImage(state.image,(r.x-b.x)/b.scale,(r.y-b.y)/b.scale,r.w/b.scale,r.h/b.scale,0,0,canvas.width,canvas.height);
    else{const s=Math.min(canvas.width/state.image.naturalWidth,canvas.height/state.image.naturalHeight),w=state.image.naturalWidth*s,h=state.image.naturalHeight*s;c.drawImage(state.image,(canvas.width-w)/2,(canvas.height-h)/2,w,h)}
    canvas.toBlob(blob=>{if(!blob)return toast('Could not create the edited image');const dt=new DataTransfer(),name=(state.input.files[0]?.name||'image').replace(/\.[^.]+$/,'.png');dt.items.add(new File([blob],name,{type:'image/png'}));state.input.files=dt.files;state.input.dispatchEvent(new Event('change',{bubbles:true}));close();toast('Edited image applied to the preview')},'image/png');
  }

  function close(){const modal=$('#jd-crop-modal');if(modal)modal.hidden=true;state.action=null}

  function install() {
    buildUI();
    $$('input[type="file"][accept*="image"]').forEach(input=>{
      let button=input.nextElementSibling;
      if(!button?.classList.contains('image-edit-button')){button=document.createElement('button');button.type='button';button.className='secondary image-edit-button';input.after(button)}
      button.hidden=false;button.textContent='Open visual drag crop';button.disabled=!input.files?.[0]||input.files[0].type==='image/gif';button.onclick=e=>{e.preventDefault();open(input)};
      if(!input.dataset.jdCropBound){input.dataset.jdCropBound='1';input.addEventListener('change',()=>{button.disabled=!input.files?.[0]||input.files[0].type==='image/gif'})}
    });
  }

  const observer=new MutationObserver(()=>{observer.disconnect();install();observer.observe(document.body,{childList:true,subtree:true})});
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',()=>{install();observer.observe(document.body,{childList:true,subtree:true})});else{install();observer.observe(document.body,{childList:true,subtree:true})}
  window.JaneDoeCropEditor={open,install};
})();
