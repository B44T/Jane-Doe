(function () {
  'use strict';

  let authorAssetToken = '';
  let glueCache = [];
  let archiveCache = [];
  const announcementKeys = ['welcome', 'leave', 'boost', 'birthdays'];
  const announcementTitles = ['Welcome', 'Leave', 'Boost', 'Birthday'];

  const baseApi = api;
  api = async function (path, method = 'GET', body) {
    if (method === 'PUT' && /settings\/(welcome|leave|boost|birthdays)$/.test(path) && body?.embed) {
      const key = path.split('/').pop();
      const card = $$('.announcement-card')[announcementKeys.indexOf(key)];
      if (card) {
        body.embed.author_icon = card.querySelector('.ann-author-icon')?.value || '';
        body.embed.author_icon_asset = card.dataset.authorAsset || '';
        if (key === 'welcome') body.invite_tracking = !!card.querySelector('.ann-invite-tracking')?.checked;
      }
    }
    return baseApi(path, method, body);
  };

  const baseEmbed = embed;
  embed = function () {
    return { ...baseEmbed(), author: $('#embed-author')?.value || '', author_icon: $('#embed-author-icon')?.value || '' };
  };

  const basePreview = preview;
  preview = function () {
    basePreview();
    let author = $('#p-embed .preview-embed-author');
    if (!author) { author = document.createElement('div'); author.className = 'preview-embed-author'; $('#p-title').before(author); }
    const data = embed();
    author.innerHTML = data.author ? `${data.author_icon ? `<img src="${escapeHtml(data.author_icon)}" alt="">` : ''}<b>${escapeHtml(data.author)}</b>` : '';
  };

  function avatarMarkup() {
    const name = botPreviewName(), primary = ctx.bot_profile?.avatar, fallback = ctx.bot_profile?.avatar_fallback || '';
    const letter = escapeHtml(name.slice(0, 1).toUpperCase() || 'J');
    if (!primary) return letter;
    return `<img src="${escapeHtml(primary)}" data-fallback="${escapeHtml(fallback)}" alt="${escapeHtml(name)}" onerror="if(this.dataset.fallback){this.src=this.dataset.fallback;this.dataset.fallback=''}else{const p=this.parentElement;this.remove();p.textContent='${letter}'}">`;
  }
  botAvatarMarkup = avatarMarkup;

  saveBotProfile = async function () {
    const form = new FormData(); form.append('nickname', $('#bot-profile-name').value);
    const file = $('#bot-profile-file').files[0]; if (file) form.append('avatar', file);
    try {
      const response = await fetch(`/api/guild/${gid()}/bot-profile`, { method:'POST', body:form });
      const data = await response.json(); if (!response.ok) throw Error(data.error || 'Profile update failed');
      ctx.bot_profile = { name:data.name, avatar:data.avatar, avatar_fallback:data.avatar_fallback || '' };
      $('#bot-profile-name').value = data.name; $('#bot-profile-preview').src = data.avatar; $('#bot-profile-file').value = '';
      applyBotIdentity(); toast('Server bot profile updated');
    } catch (error) { toast(error.message); }
  };

  function installProfilePreview() {
    const input = $('#bot-profile-file'), image = $('#bot-profile-preview');
    if (input && !input.dataset.profilePreview) { input.dataset.profilePreview = '1'; input.addEventListener('change', () => { if (input.files[0]) image.src = URL.createObjectURL(input.files[0]); }); }
    if (image && !image.dataset.fallbackBound) { image.dataset.fallbackBound = '1'; image.onerror = () => { const fallback=ctx.bot_profile?.avatar_fallback; if (fallback && image.src !== fallback) image.src=fallback; else image.removeAttribute('src'); }; }
  }

  function installMessageAuthor() {
    const footer = $('#embed-footer');
    if (!footer || $('#embed-author')) return;
    footer.closest('label').insertAdjacentHTML('afterend', '<div class="fields two"><label>Author name<input id="embed-author" placeholder="Shown above the title"></label><label>Author icon URL<input id="embed-author-icon" placeholder="https://..."></label></div><label>Upload author icon<input id="embed-author-file" type="file" accept="image/png,image/jpeg,image/gif,image/webp"></label>');
    $('#embed-author').oninput = $('#embed-author-icon').oninput = preview;
    $('#embed-author-file').onchange = async event => { const result=await uploadSavedAsset(event.target.files[0], 'embedAuthor'); authorAssetToken=result.asset_token; $('#embed-author-icon').value=URL.createObjectURL(event.target.files[0]); preview(); };
    const button=document.createElement('button');button.type='button';button.className='secondary';button.textContent='Archive draft';button.onclick=archiveEmbed;$('#embeds .actions').prepend(button);
  }

  function installArchiveNavigation() {
    if (!$('.nav[data-page="archives"]')) { const before=$('.nav[data-page="abouts"]'), button=document.createElement('button');button.className='nav';button.dataset.page='archives';button.textContent='Archives';button.onclick=()=>openPage('archives');before?.before(button); }
  }

  async function archiveEmbed() {
    try { await api(`/api/guild/${gid()}/archives`, 'POST', { name:$('#embed-title').value || 'Untitled embed', content:$('#embed-content').value, embed:embed() }); toast('Draft archived'); await loadArchives(); }
    catch (error) { toast(error.message); }
  }
  async function loadArchives() {
    if (!$('#archive-list')) return;
    const data=await api(`/api/guild/${gid()}/archives`);archiveCache=data.archives || [];
    $('#archive-list').innerHTML=archiveCache.map((item,index)=>`<article class="bot-embed-card"><div><p class="eyebrow">${escapeHtml(item.name)} · ${new Date(item.updated_at).toLocaleString()}</p>${discordPreview(item.content,item.embed.title,item.embed.description,[],item.embed)}</div><div class="actions"><button type="button" onclick="editArchive(${index})">Edit draft</button><button type="button" class="danger-action" onclick="deleteArchive(${item.id})">Delete</button></div></article>`).join('') || '<p class="muted">No archived drafts yet.</p>';
  }
  window.editArchive = function (index) { const item=archiveCache[index];if(!item)return;openPage('embeds');$('#embed-content').value=item.content||'';for(const [id,key] of [['embed-title','title'],['embed-description','description'],['embed-color','color'],['embed-image','image'],['embed-thumb','thumbnail'],['embed-footer','footer'],['embed-author','author'],['embed-author-icon','author_icon']])if($('#'+id))$('#'+id).value=item.embed[key]||($('#'+id).type==='color'?'#5865f2':'');preview(); };
  window.deleteArchive = async function (id) { if (!await confirmAction('Delete this archived draft?')) return; await api(`/api/guild/${gid()}/archives/${id}`, 'DELETE'); await loadArchives(); };

  const baseOpenPage = openPage;
  openPage = function (id) { baseOpenPage(id); if (id === 'archives') loadArchives().catch(error=>toast(error.message)); if (id === 'glue') loadGlueSettings().catch(error=>toast(error.message)); };

  sendEmbed = async function () {
    try { const data=await api(`/api/guild/${gid()}/embed/send`,'POST',{channel_id:$('#embed-channel').value,content:$('#embed-content').value,embed:embed(),asset_token:assetToken,thumbnail_asset_token:thumbnailAssetToken,author_asset_token:authorAssetToken,buttons:messageButtons(),menus:messageMenus()});$('#edit-message-id').value=data.message_id;toast('Message sent'); }
    catch(error){toast(error.message)}
  };
  editEmbed = async function () {
    try { await api(`/api/guild/${gid()}/embed/edit`,'POST',{channel_id:$('#embed-channel').value,message_id:$('#edit-message-id').value,content:$('#embed-content').value,embed:embed(),asset_token:assetToken,thumbnail_asset_token:thumbnailAssetToken,author_asset_token:authorAssetToken,buttons:messageButtons(),menus:messageMenus(),replace_components:true});toast('Changes saved'); }
    catch(error){toast(error.message)}
  };
  loadEmbed = async function () {
    try { const data=await api(`/api/guild/${gid()}/embed/load`,'POST',{channel_id:$('#embed-channel').value,message_id:$('#edit-message-id').value});$('#embed-channel').value=data.channel_id;$('#edit-message-id').value=data.message_id;$('#embed-content').value=data.content||'';for(const [id,key] of [['embed-title','title'],['embed-description','description'],['embed-color','color'],['embed-image','image'],['embed-thumb','thumbnail'],['embed-footer','footer'],['embed-author','author'],['embed-author-icon','author_icon']])if($('#'+id))$('#'+id).value=data.embed[key]||($('#'+id).type==='color'?'#5865f2':'');assetToken='';thumbnailAssetToken='';authorAssetToken='';$('#message-buttons').innerHTML='';$('#message-menus').innerHTML='';(data.components?.buttons||[]).forEach(addMessageButton);(data.components?.menus||[]).forEach(addMessageMenu);preview();renderMessageButtons();toast('Message and components loaded'); }
    catch(error){toast(error.message)}
  };

  function clearGlueEditor(){for(const id of ['glue-id','glue-content','glue-title','glue-embed-description','glue-footer','glue-image','glue-thumb','glue-template','glue-button-emoji'])if($('#'+id))$('#'+id).value='';if($('#glue-template-enabled'))$('#glue-template-enabled').checked=false;if($('#glue-button-label'))$('#glue-button-label').value='Show template';}
  loadGlueSettings = async function () { const data=await api(`/api/guild/${gid()}/glue`);glueCache=data.items||[];if($('#glue-list'))$('#glue-list').innerHTML=glueCache.map((item,index)=>`<article class="data-row"><div class="data-row-main"><b>#${escapeHtml(ctx.channels.find(channel=>channel.id===item.channel_id)?.name||item.channel_id)} · ${escapeHtml(item.embed?.title||item.content||'Embed')}</b></div><button type="button" onclick="editGlueItem(${index})">Edit</button><button type="button" class="danger-action" onclick="deleteGlueItem(${item.id})">Remove</button></article>`).join('')||'<p class="muted">No glued messages enabled.</p>'; };
  window.editGlueItem=function(index){const item=glueCache[index];if(!item)return;for(const [id,value] of [['glue-id',item.id],['glue-channel',item.channel_id],['glue-content',item.content||''],['glue-title',item.embed?.title||''],['glue-embed-description',item.embed?.description||''],['glue-color',item.embed?.color||'#5865f2'],['glue-footer',item.embed?.footer||''],['glue-image',item.embed?.image||''],['glue-thumb',item.embed?.thumbnail||''],['glue-template',item.template||''],['glue-button-label',item.button_label||'Show template'],['glue-button-emoji',item.button_emoji||'']])if($('#'+id))$('#'+id).value=value;if($('#glue-template-enabled'))$('#glue-template-enabled').checked=!!item.template_enabled;};
  saveGlue=async function(){try{await api(`/api/guild/${gid()}/glue`,'POST',{id:$('#glue-id').value||null,channel_id:$('#glue-channel').value,content:$('#glue-content').value,template_enabled:!!$('#glue-template-enabled')?.checked,template:$('#glue-template')?.value||'',button_label:$('#glue-button-label')?.value||'Show template',button_emoji:$('#glue-button-emoji')?.value||'',embed:{title:$('#glue-title').value,description:$('#glue-embed-description').value,color:$('#glue-color').value,footer:$('#glue-footer').value,image:$('#glue-image').value,thumbnail:$('#glue-thumb').value}});clearGlueEditor();await loadGlueSettings();toast('Glued message saved');}catch(error){toast(error.message)}};
  window.deleteGlueItem=async function(id){await api(`/api/guild/${gid()}/glue/${id}`,'DELETE');await loadGlueSettings();};

  async function saveAnnouncement(card,index){const payload={enabled:card.querySelector(':scope > .switch-row .switch').checked,channel_id:card.querySelector(':scope > label select')?.value||'',timezone:card.querySelector('.ann-timezone')?.value||'America/New_York',content:card.querySelector('.ann-content').value,embed:{title:card.querySelector('.ann-title').value,description:card.querySelector('.ann-description').value,color:card.querySelector('.ann-color').value,footer:card.querySelector('.ann-footer').value,author:card.querySelector('.ann-author').value,image:card.querySelector('.ann-image').value,thumbnail:card.querySelector('.ann-thumb').value}};await api(`/api/guild/${gid()}/settings/${announcementKeys[index]}`,'PUT',payload);toast(`${announcementTitles[index]} settings saved`);}
  function installAnnouncementExtensions(){ $$('.announcement-card').forEach(async(card,index)=>{if(!card.dataset.cleanExtensions){card.dataset.cleanExtensions='1';const author=card.querySelector('.ann-author');author?.closest('label').insertAdjacentHTML('afterend',`<div class="fields two"><label>Author icon URL<input class="ann-author-icon"></label><label>Upload author icon<input class="ann-author-file" type="file" accept="image/png,image/jpeg,image/gif,image/webp"></label></div>${index===0?'<div class="switch-row"><p>Track which invite was used</p><input class="switch ann-invite-tracking" type="checkbox"></div><p class="muted">Variables: {inviter}, {inviter_mention}, {invite_code}, {invite_uses}</p>':''}`);card.querySelector('.ann-author-file').onchange=async event=>{const result=await uploadSavedAsset(event.target.files[0],`announcement-${index}-author`);card.dataset.authorAsset=result.asset_token;card.querySelector('.ann-author-icon').value=URL.createObjectURL(event.target.files[0]);};const save=card.querySelector(':scope > button:last-child');if(save)save.onclick=()=>saveAnnouncement(card,index).catch(error=>toast(error.message));card.querySelectorAll('input[type="checkbox"]').forEach(toggle=>toggle.addEventListener('change',()=>saveAnnouncement(card,index).catch(error=>toast(error.message))));}
      if(!card.dataset.cleanHydrated){card.dataset.cleanHydrated='1';try{const data=await baseApi(`/api/guild/${gid()}/settings/${announcementKeys[index]}`);card.querySelector('.ann-author-icon').value=data.embed?.author_icon||'';card.dataset.authorAsset=data.embed?.author_icon_asset||'';if(index===0)card.querySelector('.ann-invite-tracking').checked=!!data.invite_tracking;}catch(error){card.dataset.cleanHydrated='';}}
    }); }

  function installUniversalUploads(){ $$('input:not([type="file"])').forEach(base=>{const marker=`${base.id} ${base.className} ${base.placeholder}`.toLowerCase();if(!/(image|thumbnail|thumb|icon)/.test(marker)||base.type==='color'||base.dataset.imageUpload)return;base.dataset.imageUpload='1';if(base.closest('label')?.querySelector('input[type="file"]'))return;const input=document.createElement('input');input.type='file';input.accept='image/png,image/jpeg,image/gif,image/webp';input.className='universal-image-file';input.onchange=async()=>{if(!input.files[0])return;const result=await uploadSavedAsset(input.files[0],`universal-${base.id||Date.now()}`);base.value=`${location.origin}/api/public/asset/${encodeURIComponent(result.asset_token)}`;base.dispatchEvent(new Event('input',{bubbles:true}));};base.after(input);});enhanceAllFileDrops(); }

  function installPersistentToggles(){for(const [selector,saver] of [['#logs input[type="checkbox"]',saveActionLogs],['#confess-enabled',saveConfessions]])$$(selector).forEach(toggle=>{if(toggle.dataset.autoSave)return;toggle.dataset.autoSave='1';toggle.addEventListener('change',()=>Promise.resolve(saver()).catch(error=>toast(error.message)));});}

  function install(){installProfilePreview();installMessageAuthor();installArchiveNavigation();installAnnouncementExtensions();installUniversalUploads();installPersistentToggles();window.JaneDoeCropEditor?.install();}
  const observer=new MutationObserver(()=>{observer.disconnect();install();observer.observe(document.body,{childList:true,subtree:true});});
  install();observer.observe(document.body,{childList:true,subtree:true});applyBotIdentity();
})();
