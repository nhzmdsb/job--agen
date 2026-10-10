const $ = id => document.getElementById(id);
const stages = {pool:'机会池',preparing:'准备投递',applied:'已投递',interview:'面试中',ended:'已结束',trash:'垃圾桶'};
const colors = {green:'符合且有吸引力',yellow:'有不利条件',beige:'兴趣待确认',grey:'资料待确认'};
let records = [], artifacts = [], quota = {version:0,data:{items:[]}}, stage = 'pool', current = null;
let saving = Promise.resolve(), noteTimer, dirtyNotes = false, noteConflict = null, syncConflict = null, resolvedData = {}, lastChecked;
let quotaRequest = null;
const form = $('job-form');

async function api(path, method='GET', data) {
  const headers = {'X-Workspace':'1'};
  if (data && !(data instanceof FormData)) headers['Content-Type'] = 'application/json';
  const response = await fetch(path, {method, headers, body:data instanceof FormData ? data : data ? JSON.stringify(data) : undefined});
  const result = await response.json();
  if (!response.ok) { const error = new Error(result.error || '操作未完成'); error.result=result; error.status=response.status; throw error; }
  return result;
}
function toast(message) { $('toast').textContent=message; $('toast').hidden=false; setTimeout(()=>{$('toast').hidden=true;},5000); }
function node(tag, text, className) { const el=document.createElement(tag); if(text!==undefined) el.textContent=text; if(className) el.className=className; return el; }
function dataStage(r) { return r.data.stage || 'pool'; }
function remember(record) { const i=records.findIndex(r=>r.id===record.id); if(i<0) records.push(record); else records[i]=record; }
function render() {
  const nav=$('stages'); nav.replaceChildren();
  for (const [key,label] of Object.entries(stages)) {
    const button=node('button',undefined,key===stage?'active':''); button.append(node('span',label),node('span',records.filter(r=>dataStage(r)===key).length,'count'));
    button.onclick=()=>{stage=key;render();}; nav.append(button);
  }
  $('stage-title').textContent=stage==='pool'?'看机会':stages[stage];
  $('stage-description').textContent=stage==='trash'?'已删除机会保留去重记录，不会因重复采集重新出现。':stage==='ended'?'保留申请过程与结果，为之后的选择提供依据。':'先看岗位，再决定如何行动。资料不全的线索也会保留。';
  const term=$('search').value.trim().toLowerCase(), filter=$('color-filter').value;
  const shown=records.filter(r=>dataStage(r)===stage && (!filter || r.data.display_color===filter) && (!term || JSON.stringify(r.data).toLowerCase().includes(term))).reverse();
  const cards=$('cards'); cards.replaceChildren();
  for (const record of shown) {
    const d=record.data, color=d.display_color || 'grey';
    const card=node('button',undefined,'card '+color); card.setAttribute('aria-label',`${d.company} ${d.title} ${colors[color]}`);
    const top=node('div',undefined,'top'); top.append(node('h2',d.title),node('span',d.salary || '薪资待确认','salary'));
    const company=node('div',d.company,'company'); if(d.possible_no_fit) company.append(node('span','  ! 可能暂无合适岗位','warning'));
    card.append(top,company,node('div',[d.city || '城市未知',d.education || '学历待核对',d.experience || '经验待核对'].join(' · '),'metadata'));
    const tags=node('div',undefined,'tags'); const values=Array.isArray(d.tags)?d.tags:[];
    for(const tag of values.slice(0,6)) tags.append(node('span',tag,'tag'));
    card.append(tags,node('p',d.summary || (d.jd ? d.jd.slice(0,170) : '已保留招聘入口，完整 JD 尚未取得。'),'summary'));
    const foot=node('div',undefined,'card-footer'); foot.append(node('span',colors[color]),node('span',d.eligible_years?`可申请毕业年份：${d.eligible_years}`:'毕业年份待确认')); card.append(foot);
    card.onclick=()=>openJob(record); cards.append(card);
  }
  $('empty').hidden=shown.length>0;
  if(records.length) {$('empty').querySelector('h2').textContent='这里暂时没有机会'; $('empty').querySelector('p').textContent='可以切换进度、调整筛选，或保存新的招聘线索。';}
}
async function refresh() {
  const data=await api('/api/jobs'); records=data.items; artifacts=data.artifacts; quota=data.quotas; render();
}
function fillForm(data) {
  form.reset();
  for(const field of form.elements) { if(!field.name)continue; const value=data[field.name]; if(field.type==='checkbox') field.checked=!!value; else if(field.name==='tags')field.value=Array.isArray(value)?value.join('，'):value||''; else field.value=value??''; }
  form.elements.qualification.value=data.qualification||'unknown'; form.elements.interest.value=data.interest||'unknown';
}
function fillSide() {
  $('stage-select').value=current?dataStage(current):'pool';
  $('detail-company').textContent=current?.data.company || '保留线索，稍后补全';
  $('detail-title').textContent=current?.data.title || '保存机会';
  const links=$('source-links'); links.replaceChildren();
  for(const [key,label] of [['url','原始来源 ↗'],['boss_url','BOSS 来源 ↗']]) {
    const url=current?.data[key]; if(!url || !/^https?:\/\//i.test(url))continue;
    const a=node('a',label); a.href=url; a.target='_blank'; a.rel='noopener noreferrer'; links.append(a);
  }
  const box=$('attachments'); box.replaceChildren();
  const ids=current?.data.artifacts || [];
  for(const id of ids) {
    const a=artifacts.find(a=>a.id===id);
    if(!a) {box.append(node('p',`${id} · 原文件未恢复`)); continue;}
    if(a.data.sha256) {const link=node('a',`${a.data.kind === "resume" ? "简历" : "来源资料"} · ${a.data.name} · 下载原文件 ↓`); link.href=`/api/artifacts/${encodeURIComponent(id)}/download`; box.append(link);}
    else box.append(node('p',`${a.data.name || id} · 原文件未恢复`));
  }
  if(!ids.length)box.append(node('p','尚未关联真实简历','muted'));
  for(const id of ['stage-select','notes','upload','generate-prompt'])$(id).disabled=!current;
}
async function history() {
  $('history').replaceChildren(); if(!current)return;
  const result=await api(`/api/records/jobs/${encodeURIComponent(current.id)}`);
  for(const h of result.history.slice().reverse()) $('history').append(node('p',`v${h.version} · ${new Date(h.updated_at).toLocaleString()} · ${h.actor}\n${h.reason}`));
}
async function openJob(record=null) {
  if($('detail').open && !(await flushNotes()))return;
  current=record ? await api(`/api/records/jobs/${encodeURIComponent(record.id)}`).then(r=>r.record) : null;
  fillForm(current?.data || {}); $('notes').value=current?.data.notes||''; dirtyNotes=false; noteConflict=null;
  $('note-conflict').hidden=true; $('note-status').textContent='编辑后自动保存';
  for(const id of ['form-status','upload-status','prompt-status','prompt','description']) { if('value' in $(id))$(id).value=''; else $(id).textContent=''; }
  fillSide(); if(!$('detail').open)$('detail').showModal(); await history();
}
function formData() {
  const data={}; for(const field of form.elements) { if(!field.name)continue; data[field.name]=field.type==='checkbox'?field.checked:field.value.trim(); }
  data.tags=data.tags.split(/[,，]/).map(s=>s.trim()).filter(Boolean); data.cycle=data.cycle||'未指定批次'; return data;
}
function patchCurrent(patch, extra={}) {
  const targetId=current.id;
  const operation=saving.then(async()=> {
    if(current?.id!==targetId)throw new Error('岗位已切换，请重新编辑');
    const r=await api(`/api/records/jobs/${encodeURIComponent(targetId)}`,'PATCH',{expected_version:current.version,patch,...extra});
    current=r; remember(r); fillSide(); render(); return r;
  });
  saving=operation.catch(()=>{}); return operation;
}
async function saveNotes() {
  clearTimeout(noteTimer); if(!current || !dirtyNotes)return true;
  if(noteConflict)return false;
  const value=$('notes').value; $('note-status').textContent='正在保存…';
  try { await patchCurrent({notes:value}); dirtyNotes=$('notes').value!==value; $('note-status').textContent=dirtyNotes?'继续编辑中…':'备注已保存'; if(dirtyNotes)noteTimer=setTimeout(saveNotes,600); return true; }
  catch(error) {
    $('note-status').textContent=error.message;
    if(error.result?.current) { noteConflict=error.result.current; $('other-notes').value=noteConflict.data.notes||''; $('note-conflict').hidden=false; }
    return false;
  }
}
async function flushNotes() { clearTimeout(noteTimer); await saving; return await saveNotes(); }
$('notes').oninput=()=>{dirtyNotes=true;$('note-status').textContent='尚未保存…';clearTimeout(noteTimer);noteTimer=setTimeout(saveNotes,600);};
async function chooseNotes(mode) {
  const mine=$('notes').value, other=noteConflict.data.notes||'';
  current=noteConflict; noteConflict=null; $('note-conflict').hidden=true;
  $('notes').value=mode==='merge'?`${other}\n\n—— 我的补充 ——\n${mine}`:mode==='other'?other:mine;
  dirtyNotes=true; await saveNotes();
}
$('merge-notes').onclick=()=>chooseNotes('merge'); $('use-my-notes').onclick=()=>chooseNotes('mine'); $('use-other-notes').onclick=()=>chooseNotes('other');
form.onsubmit=async event=> {
  event.preventDefault(); if(current && !(await flushNotes()))return;
  $('form-status').textContent='正在保存…';
  try {
    const data=formData();
    if(current) await patchCurrent(data); else {current=await api('/api/jobs','POST',{data:{...data,stage:'pool',notes:''}}); remember(current);}
    $('form-status').textContent='岗位资料已保存'; await refresh(); fillSide(); await history();
  } catch(error) { $('form-status').textContent=error.message; if(error.result?.current) {toast('岗位资料已更新，请关闭并重新打开；您填写的内容仍保留在表单。');} }
};
$('stage-select').onchange=async()=> {
  const requested=$('stage-select').value; if(!(await flushNotes())){$('stage-select').value=dataStage(current);return;}
  try {await patchCurrent({stage:requested});toast(`已移至${stages[requested]}`); await history();}
  catch(error) {
    if(error.message.startsWith('quota:')) {
      quotaRequest={id:current.id,stage:requested};
      $('quota-warning').textContent=error.message.slice(6);
      $('quota-reason').value=''; $('quota-confirm-status').textContent='';
      $('quota-confirm-dialog').showModal();
    }else toast(error.message);
    $('stage-select').value=dataStage(current);
  }
};
$('cancel-quota-confirm').onclick=()=>$('quota-confirm-dialog').close();
$('quota-confirm-form').onsubmit=async event=> {
  event.preventDefault(); const reason=$('quota-reason').value.trim();
  if(!reason){$('quota-confirm-status').textContent='请填写超额原因';return;}
  try {
    if(current?.id!==quotaRequest?.id)throw new Error('岗位已切换，请重新确认');
    if(!(await flushNotes()))return;
    await patchCurrent({stage:quotaRequest.stage},{quota_confirm:true,quota_reason:reason});
    $('quota-confirm-dialog').close();toast('已记录明确确认及原因');await history();
  }catch(error){$('quota-confirm-status').textContent=error.message;}
};
$('upload').onchange=async()=> {
  const file=$('upload').files[0]; if(!file || !current)return;
  if(!(await flushNotes()))return;
  $('upload-status').textContent='正在保存原文件…';
  try { const data=new FormData();data.append('file',file);data.append('expected_version',current.version);await saving;
    const result=await api(`/api/jobs/${encodeURIComponent(current.id)}/artifacts`,'POST',data);current=result.record;await refresh();fillSide();$('upload-status').textContent=`原文件已保存 · SHA256 ${result.artifact.data.sha256.slice(0,12)}…`; }
  catch(error){$('upload-status').textContent=error.message;} finally{$('upload').value='';}
};
$('generate-prompt').onclick=async()=> {
  if(!(await flushNotes()))return;
  try { const result=await api(`/api/jobs/${encodeURIComponent(current.id)}/prompt`,'POST',{task:$('task').value,description:$('description').value});$('prompt').value=result.prompt;$('prompt-status').textContent='已生成，可以复制到新会话'; }
  catch(error){$('prompt-status').textContent=error.message;}
};
$('copy-prompt').onclick=async()=> {
  if(!$('prompt').value){$('prompt-status').textContent='请先生成 Prompt';return;}
  try {await navigator.clipboard.writeText($('prompt').value);$('prompt-status').textContent='已复制，打开 ChatGPT 后粘贴';}
  catch { $('prompt').focus();$('prompt').select();$('prompt-status').textContent='浏览器未允许自动复制；已选中全文，请按 Ctrl+C'; }
};
async function closeDetail() {if(await flushNotes())$('detail').close();else toast('备注尚未保存，请先处理冲突或恢复连接');}
$('close-detail').onclick=closeDetail; $('detail').addEventListener('cancel',event=>{event.preventDefault();closeDetail();});
$('add-button').onclick=()=>openJob(); $('empty-add').onclick=()=>openJob(); $('search').oninput=render; $('color-filter').onchange=render;
function showQuotas() {
  const box=$('quota-items');box.replaceChildren();for(const q of quota.data.items)box.append(node('p',`${q.company} · ${q.cycle} · 最多 ${q.limit} 个岗位`));
  if(!quota.data.items.length)box.append(node('p','还没有记录名额限制','muted'));
}
$('quota-button').onclick=async()=>{await refresh();showQuotas();$('quota-dialog').showModal();}; $('close-quota').onclick=()=>$('quota-dialog').close();
$('quota-form').onsubmit=async event=> {
  event.preventDefault();const fields=event.target.elements,q={company:fields.company.value.trim(),cycle:fields.cycle.value.trim(),limit:Number(fields.limit.value)};
  const items=quota.data.items.filter(v=>!(v.company.toLowerCase()===q.company.toLowerCase()&&v.cycle===q.cycle));items.push(q);
  try {quota=await api('/api/quotas','PUT',{items,expected_version:quota.version});showQuotas();$('quota-status').textContent='名额已保存';}
  catch(error){$('quota-status').textContent=error.message;await refresh();}
};
async function checkSync() {
  try {
    const result=await api('/api/sync');$('sync-status').textContent=`${result.message}${result.running?' · 检查中':''}${result.pending?' · '+result.pending+' 项待同步':''}${result.conflicts.length?' · 有冲突待处理':''}`;
    if(result.state==='connected' && result.checked_at!==lastChecked) {lastChecked=result.checked_at;await refresh();}
    const box=$('conflict-list');box.replaceChildren();
    for(const c of result.conflicts) {
      const b=node('button',`处理冲突：${c.local.record.data.title||c.id}`,'conflict-box');b.onclick=async()=>{
        const fresh=await api(`/api/records/${encodeURIComponent(c.collection)}/${encodeURIComponent(c.id)}`);
        syncConflict={...c,local:{...c.local,record:fresh.record}};
        $('conflict-local-summary').value=describe(fresh.record.data);$('conflict-remote-summary').value=c.remote?describe(c.remote.record.data):'云端记录已移除，请检查私有仓库';
        selectBranch('local');$('resolve-status').textContent='';$('sync-conflict-dialog').showModal();
      };box.append(b);
    }
  }catch(error){$('sync-status').textContent='同步状态读取失败，本地资料仍保留';}
}
$('sync-button').onclick=async()=>{await api('/api/sync','POST',{});await checkSync();toast('已安排后台检查，您可以继续查看岗位');};
$('close-sync-conflict').onclick=()=>$('sync-conflict-dialog').close();
function describe(d) {return Object.entries(d).filter(([k])=>!['dedupe_key','dedupe_keys','display_color','application_history'].includes(k)).map(([k,v])=>`${({company:'公司',title:'岗位',notes:'备注',stage:'进度',jd:'完整 JD',url:'来源',eligible_years:'允许毕业年份'})[k]||k}：${Array.isArray(v)?v.join('，'):typeof v==='object'?JSON.stringify(v):v}`).join('\n');}
function selectBranch(mode) {const local=syncConflict.local.record.data,remote=syncConflict.remote?.record.data||{};resolvedData={...(mode==='remote'?remote:local)};if(mode==='merge')resolvedData.notes=`${remote.notes||''}\n\n—— 本地补充 ——\n${local.notes||''}`;$('resolved-notes').value=resolvedData.notes||'';$('resolved-origin').textContent=mode==='remote'?'将采用云端岗位资料':'将采用本地岗位资料';}
$('conflict-local').onclick=()=>selectBranch('local');$('conflict-remote').onclick=()=>selectBranch('remote');$('conflict-merge').onclick=()=>selectBranch('merge');
$('resolve-button').onclick=async()=> {
  try {
    await api(`/api/conflicts/${encodeURIComponent(syncConflict.collection)}/${encodeURIComponent(syncConflict.id)}/resolve`,'POST',{expected_version:syncConflict.local.record.version,data:{...resolvedData,notes:$('resolved-notes').value}});
    $('sync-conflict-dialog').close();await refresh();await checkSync();toast('合并结果已保存，两份原始分支仍保留');}
  catch(error){$('resolve-status').textContent=error.message;}
};
window.addEventListener('beforeunload',event=>{if(dirtyNotes){event.preventDefault();event.returnValue='';}});
window.addEventListener('unhandledrejection',event=>{toast(event.reason?.message||'操作未完成，请重试');});
for(const [key,label] of Object.entries(stages)) {const opt=node('option',label);opt.value=key;$('stage-select').append(opt);}
refresh().catch(error=>toast(error.message));api('/api/sync','POST',{}).then(checkSync).catch(error=>toast(error.message));setInterval(checkSync,5000);
