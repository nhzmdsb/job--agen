const $ = id => document.getElementById(id);
const stages = {pool:'机会池',pending:'内部待办',preparing:'准备投递',applied:'已投递',interview:'面试中',ended:'已结束',trash:'回收站'};
const colors = {green:'符合条件，值得考虑',yellow:'有不利条件',beige:'旧判断待重核',grey:'已读但有歧义'};
let records = [], artifacts = [], quota = {version:0,data:{items:[]}}, stage = 'pool', current = null;
let saving = Promise.resolve(), noteTimer, dirtyNotes = false, noteConflict = null, syncConflict = null, resolvedData = {}, lastChecked;
let quotaRequest = null;
let profileRecord = null, profileProposal = null;
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
function safeLink(url, label) { const a=node('a',label); if(/^https?:\/\//i.test(url)){a.href=url;a.target='_blank';a.rel='noopener noreferrer';} return a; }
function dataStage(r) { return r.data.stage || 'pool'; }
function inView(record, key) { if(record.view.merged)return false; if(key==='pending')return ['pool','preparing'].includes(dataStage(record)) && (record.view.internal_pending || record.view.stale || record.view.translation_pending || (record.data.source_problems||[]).some(p=>p.status!=='resolved')); return key==='hidden' ? record.view.low_priority : dataStage(record)===key && (key!=='pool' || (!record.view.low_priority && !record.view.internal_pending)); }
function remember(record) { const i=records.findIndex(r=>r.id===record.id); if(i<0) records.push(record); else records[i]=record; }
function render() {
  const nav=$('stages'); nav.replaceChildren();
  for (const [key,label] of Object.entries(stages)) {
    const button=node('button',undefined,key===stage?'active':''); button.append(node('span',label),node('span',records.filter(r=>inView(r,key)).length,'count'));
    button.onclick=()=>{stage=key;render();}; nav.append(button);
  }
  $('hidden-count').textContent=records.filter(r=>inView(r,'hidden')).length;
  $('hidden-opportunities').classList.toggle('active',stage==='hidden');
  $('stage-title').textContent=stage==='hidden'?'已收起':stage==='pool'?'看机会':stages[stage];
  $('stage-description').textContent=stage==='pending'?'尚需补齐详情、翻译或判断；待重核机会保留原结论。':stage==='hidden'?'这些机会已退出日常浏览。来源和历史仍保留，可在详情中放回机会池。':stage==='trash'?'已删除机会保留去重记录，不会因重复采集重新出现。':stage==='ended'?'保留申请过程与结果，为之后的选择提供依据。':'先看岗位，再决定如何行动。资料不全的线索也会保留。';
  const term=$('search').value.trim().toLowerCase(), filter=$('color-filter').value;
  const shown=records.filter(r=>inView(r,stage) && (!filter || r.view.color===filter) && (!term || JSON.stringify(r.data).toLowerCase().includes(term))).reverse();
  const cards=$('cards'); cards.replaceChildren();
  for (const record of shown) {
    const d=record.data, view=record.view, color=view.color;
    const card=node('button',undefined,'card '+color); card.setAttribute('aria-label',`${d.company} ${view.title} ${colors[color]}`);
    const top=node('div',undefined,'top'); top.append(node('h2',view.title),node('span',d.salary || '薪资待确认','salary'));
    const company=node('div',d.company,'company'); if(d.possible_no_fit) company.append(node('span','  ! 可能暂无合适岗位','warning'));
    card.append(top,company,node('div',[d.city || '城市未知',d.education || '学历待核对',d.experience || '经验待核对'].join(' · '),'metadata'));
    const tags=node('div',undefined,'tags'); const values=Array.isArray(d.tags)?d.tags:[];
    for(const tag of values.slice(0,6)) tags.append(node('span',tag,'tag'));
    card.append(tags,node('p',view.summary || (d.jd ? d.jd.slice(0,170) : '已保留招聘入口，完整 JD 尚未取得。'),'summary'),node('p',`${view.stale?'待重核 · ':''}${view.origin}：${view.reason}${view.low_priority?' · 已收起':''}`,'judgment'));
    const foot=node('div',undefined,'card-footer'); foot.append(node('span',colors[color]),node('span',d.eligible_years?`可申请毕业年份：${d.eligible_years}`:'毕业年份待确认')); card.append(foot);
    card.onclick=()=>openJob(record); cards.append(card);
  }
  $('empty').hidden=shown.length>0;
  if(records.length) {$('empty').querySelector('h2').textContent='这里暂时没有机会'; $('empty').querySelector('p').textContent='可以切换进度、调整筛选，或保存新的招聘线索。';}
}
async function refresh() {
  const data=await api('/api/jobs'); records=data.items; artifacts=data.artifacts; quota=data.quotas; $('review-count').textContent=data.review_count; render();
}
function fillForm(data) {
  form.reset();
  for(const field of form.elements) { if(!field.name)continue; const value=data[field.name]; if(field.type==='checkbox') field.checked=!!value; else if(field.name==='tags')field.value=Array.isArray(value)?value.join('，'):value||''; else field.value=value??''; }
  form.elements.qualification.value=data.qualification||'unknown'; form.elements.interest.value=data.interest||'unknown';
}
function fillSide() {
  $('stage-select').value=current?dataStage(current):'pool';
  $('trash-job').textContent=current && dataStage(current)==='trash'?'从回收站恢复到机会池':'移入回收站';
  $('detail-company').textContent=current?.data.company || '保留线索，稍后补全';
  $('detail-title').textContent=current?.view.title || '保存机会';
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
  for(const id of ['stage-select','trash-job','trash-reason','notes','upload','generate-prompt'])$(id).disabled=!current;
  if(current)fillReading();
}
const dimensionLabels={graduation:'毕业资格',education:'学历',major:'专业',experience:'经验',responsibilities:'职责关联',preferences:'求职偏好'};
const kindLabels={mandatory:'强制要求',preferred:'优先条件',inferred:'推断',unknown:'未知',unacceptable:'明确不能接受'};
function fillReading() {
  const d=current.data,v=current.view,work=d.screening?.work;
  $('read-facts').textContent=[d.city||'地点待确认',d.salary||'薪资待确认',d.education||'学历待确认',d.experience||'经验待确认',`招聘年份：${d.recruitment_year||'未知'}`,`面向届别：${d.target_cohort||'未知'}`,`允许毕业年份：${d.eligible_years||'未知'}`].join(' · ');
  $('read-summary').textContent=v.summary||'尚无中文摘要';
  $('translation-status').textContent=v.translation_valid?'完整中文 JD 由 Work 翻译，可展开查看原文。':v.translation_pending?'完整中文 JD 待 Work 补全，当前显示原文；已有标题和摘要不代表完成翻译。':'';
  $('read-reason').textContent=`${v.stale?'待重核 · ':''}${v.origin} · ${colors[v.color]}：${v.reason}`;
  const evidence=$('read-evidence');evidence.replaceChildren();
  if(work) {
    if(v.stale)evidence.append(node('p','待重核：保留原判断与依据，等待补充核验。','warning'));
    for(const c of work.checks||[]) {
      const box=node('article',undefined,'evidence-card');
      box.append(node('strong',dimensionLabels[c.dimension]),node('span',`${kindLabels[c.kind]} · ${{pass:'符合',fail:'不符',unknown:'待确认'}[c.status]}`,'evidence-status'),node('p',c.reason));
      const details=node('details');details.append(node('summary','查看原文与档案依据'),node('p',`JD 原文：${c.jd_quote||'原文未明确'}`),node('p',`档案事实：${c.profile_fact}`));if(c.evidence_url)details.append(safeLink(c.evidence_url,'资格原始来源'));box.append(details);evidence.append(box);
    }
    for(const q of work.questions||[]){const box=node('article',undefined,'evidence-card question-card');box.append(node('strong','待确认'),node('p',q));evidence.append(box);}
    evidence.append(node('small',`档案 v${work.profile_version} · 判断时间 ${work.assessed_at}`));
  }
  if(d.screening){const details=node('details',undefined,'auxiliary-checks');details.append(node('summary','程序辅助检查（供核对）'),node('p',JSON.stringify(Object.fromEntries(Object.entries(d.screening).filter(([k])=>k!=='work')))));evidence.append(details);}
  if(d.color_reason)evidence.append(node('p',`人工调整：${d.color_reason}`));
  $('restore-pool').hidden=!v.low_priority;
  $('fold-pool').hidden=!(dataStage(current)==='pool' && d.pool_visibility==='show' && v.fresh && work?.decision==='ineligible');
  $('jd-heading').textContent=v.translation_valid?'完整中文 JD':'完整 JD';
  $('read-jd').textContent=v.jd||'完整 JD 尚未取得，已保留原始入口。';
  $('original-jd-panel').hidden=!v.translation_valid;
  $('original-title').textContent=`原始岗位名称：${d.title}`; $('original-jd').textContent=d.jd||'';
  $('read-source-evidence').textContent=typeof d.qualification_evidence==='string'?d.qualification_evidence:JSON.stringify(d.qualification_evidence||'');
  const problems=$('source-problems');problems.replaceChildren();for(const p of d.source_problems||[])problems.append(node('p',`${p.status==='resolved'?'已核验':'待核验'}：${p.description}`));
}
function readingMode() { $('read-pane').hidden=!current;form.hidden=!!current; $('quick-form').hidden=!current; }
function fillQuick() {for(const f of $('quick-form').elements){if(f.name)f.value=f.name==='tags'?(current.data.tags||[]).join('，'):current.data[f.name]|| (f.name==='interest'?'unknown':'');}}
async function history() {
  $('history').replaceChildren(); if(!current)return;
  const result=await api(`/api/records/jobs/${encodeURIComponent(current.id)}`);
  for(const h of result.history.slice().reverse()) $('history').append(node('p',`v${h.version} · ${new Date(h.updated_at).toLocaleString()} · ${h.actor}\n${h.reason}`));
}
async function openJob(record=null) {
  if($('detail').open && !(await flushNotes()))return;
  current=record ? await api(`/api/records/jobs/${encodeURIComponent(record.id)}`).then(r=>r.record) : null;
  $('trash-reason').value=current?.data.rejection_reason||'';
  fillForm(current?.data || {}); $('notes').value=current?.data.notes||''; dirtyNotes=false; noteConflict=null;
  $('note-conflict').hidden=true; $('note-status').textContent='编辑后自动保存';
  for(const id of ['form-status','upload-status','prompt-status','prompt','description']) { if('value' in $(id))$(id).value=''; else $(id).textContent=''; }
  fillSide();readingMode();if(current)fillQuick(); if(!$('detail').open)$('detail').showModal(); await history();
}
function formData() {
  const data={}; for(const field of form.elements) { if(!field.name)continue; data[field.name]=field.type==='checkbox'?field.checked:field.value.trim(); }
  data.tags=data.tags.split(/[,，]/).map(s=>s.trim()).filter(Boolean); data.cycle=data.cycle||'未指定批次'; return data;
}
function patchCurrent(patch, extra={}) {
  const targetId=current.id;
  const operation=saving.then(async()=> {
    if(current?.id!==targetId)throw new Error('岗位已切换，请重新编辑');
    const r=await api(`/api/records/jobs/${encodeURIComponent(targetId)}`,'PATCH',{expected_version:current.version,patch,human_confirm:true,confirmation_reason:'本人在工作台编辑',...extra});
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
    $('form-status').textContent='岗位资料已保存'; await refresh(); fillSide();readingMode();fillQuick(); await history();
  } catch(error) { $('form-status').textContent=error.message; if(error.result?.current) {toast('岗位资料已更新，请关闭并重新打开；您填写的内容仍保留在表单。');} }
};
async function changeStage(requested, patch={}) {
  if(!(await flushNotes())){$('stage-select').value=dataStage(current);return;}
  try {await patchCurrent({...patch,stage:requested},{reason:requested==='trash'?'用户移入回收站':'用户修改求职进度'});toast(`已移至${stages[requested]}`); await history();}
  catch(error) {
    if(error.message.startsWith('quota:')) {
      quotaRequest={id:current.id,stage:requested};
      $('quota-warning').textContent=error.message.slice(6);
      $('quota-reason').value=''; $('quota-confirm-status').textContent='';
      $('quota-confirm-dialog').showModal();
    }else toast(error.message);
    $('stage-select').value=dataStage(current);
  }
}
$('stage-select').onchange=()=>changeStage($('stage-select').value);
$('trash-job').onclick=()=>changeStage(dataStage(current)==='trash'?'pool':'trash',dataStage(current)==='trash'?{}:{rejection_reason:$('trash-reason').value.trim(),interest:'no'});
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
$('hidden-opportunities').onclick=()=>{stage='hidden';render();};
$('edit-facts').onclick=()=>{fillForm(current.data);$('read-pane').hidden=true;form.hidden=false;};
$('quick-form').onsubmit=async event=>{event.preventDefault();if(!(await flushNotes()))return;const patch={};for(const f of event.target.elements)if(f.name)patch[f.name]=f.value.trim();patch.tags=patch.tags.split(/[,，]/).map(s=>s.trim()).filter(Boolean);try{await patchCurrent(patch);$('quick-status').textContent='已保存';await refresh();await history();}catch(error){$('quick-status').textContent=error.message;}};
$('restore-pool').onclick=async()=>{if(!(await flushNotes()))return;try{await patchCurrent({pool_visibility:'show'});await history();}catch(error){toast(error.message);}};
$('fold-pool').onclick=async()=>{if(!(await flushNotes()))return;try{await patchCurrent({pool_visibility:'auto'});await history();}catch(error){toast(error.message);}};
$('report-source').onclick=async()=>{const description=$('source-problem').value.trim();if(!description)return;if(!(await flushNotes()))return;await patchCurrent({source_problems:[...(current.data.source_problems||[]),{id:crypto.randomUUID(),description,status:'pending',reported_at:new Date().toISOString()}]});$('source-problem').value='';await refresh();toast('已保存待核验问题，原文保留');};
const lines=value=>value.split('\n').map(s=>s.trim()).filter(Boolean);
function experienceEditor(data={}) {
  const box=node('fieldset',undefined,'experience-editor');box.original=data;
  for(const [key,label] of [['organization','机构'],['role','角色'],['title','项目名称'],['start_month','开始时间'],['end_month','结束时间'],['facts','已确认事实（每行一项）'],['pending','待确认（每行一项）']]){const l=node('label',label),input=node(['facts','pending'].includes(key)?'textarea':'input');input.name=key;input.value=Array.isArray(data[key])?data[key].join('\n'):data[key]||'';l.append(input);box.append(l);}
  const remove=node('button','移除此经历');remove.type='button';remove.onclick=()=>box.remove();box.append(remove);$('profile-experiences').append(box);
}
async function loadProfile() {
  const result=await api('/api/profile');profileRecord=result.record;profileProposal=result.proposal;
  const d=profileRecord?.data||{},c=d.confirmed||{},p=d.preferences||{},f=$('profile-form').elements;
  const values={education:c.degree_label,institution:c.education_institution,major:c.major,graduation_month:c.graduation_month,formal_work_years:c.formal_experience_years,english:typeof c.english==='object'?c.english?.self_description:c.english,priority:(p.priority_fields||[]).join('\n'),retain:(p.retain_fields||[]).join('\n'),unacceptable:(p.unacceptable_conditions||[]).join('\n'),frontline:p.frontline,locations:(p.location_soft_dispreference||[]).join('，'),exclude_mandatory_formal_experience_from_years:p.exclude_mandatory_formal_experience_from_years,pending_confirmation:(d.pending_confirmation||[]).join('\n')};
  for(const [key,value] of Object.entries(values))f[key].value=value??'';
  $('profile-experiences').replaceChildren();for(const e of c.experience||[])experienceEditor(e);
  const sources=$('profile-sources');sources.replaceChildren();for(const s of d.sources||[]){const a=node('a',s.title||s.url);if(/^https?:\/\//.test(s.url)){a.href=s.url;a.target='_blank';a.rel='noopener noreferrer';}sources.append(a,node('p',s.page_last_edited_at||''));}
  $('profile-status').textContent=`档案 v${profileRecord?.version||0} · 留空的信息不会被当作已确认事实`;
  $('profile-proposal').hidden=profileProposal?.data.status!=='pending';
  if(profileProposal?.data.status==='pending'){
    $('proposal-reason').textContent=profileProposal.data.reason;const diff=$('proposal-diff');diff.replaceChildren();
    for(const [key,value] of Object.entries(profileProposal.data.patch||{})){diff.append(node('h4',key),node('pre',`当前：${JSON.stringify(d[key],null,2)}\n建议：${JSON.stringify(value,null,2)}`));}
    for(const s of profileProposal.data.sources||[])diff.append(node('p',`建议来源：${s.url||s}`));
  }
}
$('profile-button').onclick=async()=>{await loadProfile();$('profile-dialog').showModal();};
$('close-profile').onclick=()=>$('profile-dialog').close();
$('add-experience').onclick=()=>experienceEditor();
async function updateCurrentProjection(){await refresh();if(current){current=(await api(`/api/records/jobs/${encodeURIComponent(current.id)}`)).record;fillSide();}}
$('profile-form').onsubmit=async event=>{
  event.preventDefault();const f=event.target.elements,d=profileRecord?.data||{},c=d.confirmed||{},p=d.preferences||{};
  const experience=[...$('profile-experiences').children].map(box=>{const e={...box.original};for(const input of box.querySelectorAll('input,textarea')){const value=input.value.trim();e[input.name]=['facts','pending'].includes(input.name)?lines(value):value;}return e;});
  const patch={confirmed:{...c,degree_label:f.education.value.trim(),education_institution:f.institution.value.trim(),major:f.major.value.trim(),graduation_month:f.graduation_month.value.trim(),formal_experience_years:f.formal_work_years.value===''?null:Number(f.formal_work_years.value),english:typeof c.english==='object'?{...c.english,self_description:f.english.value.trim()}:f.english.value.trim(),experience},preferences:{...p,priority_fields:lines(f.priority.value),retain_fields:lines(f.retain.value),unacceptable_conditions:lines(f.unacceptable.value),frontline:f.frontline.value.trim(),location_soft_dispreference:f.locations.value.split(/[,，]/).map(s=>s.trim()).filter(Boolean),exclude_mandatory_formal_experience_from_years:f.exclude_mandatory_formal_experience_from_years.value===''?null:Number(f.exclude_mandatory_formal_experience_from_years.value)},pending_confirmation:lines(f.pending_confirmation.value)};
  if(patch.confirmed.degree_label!==c.degree_label)patch.confirmed.degree=({'专科':'associate','本科':'bachelor','硕士':'master','博士':'doctor'})[patch.confirmed.degree_label]||null;
  if(!(await flushNotes()))return;
  try{profileRecord=await api('/api/profile','PUT',{expected_version:profileRecord?.version||0,patch});$('profile-status').textContent=`档案 v${profileRecord.version} 已保存；岗位待重新判断`;await updateCurrentProjection();}
  catch(error){$('profile-status').textContent=error.message+'；编辑内容仍保留，请复制后重新打开核对。';}
};
$('confirm-proposal').onclick=async()=>{if(!(await flushNotes()))return;try{await api('/api/profile/confirm','POST',{expected_version:profileRecord.version,proposal_version:profileProposal.version});await loadProfile();await updateCurrentProjection();toast('差异已确认，档案与建议历史均保留');}catch(error){$('profile-status').textContent=error.message;}};
async function showReview(notion=false){const result=await api('/api/review-prompt','POST',{notion});$('review-prompt').value=result.prompt;$('review-status').textContent='复制后粘贴到获授权的会话；未自动执行';$('review-dialog').showModal();}
$('review-button').onclick=()=>showReview();$('notion-request').onclick=()=>showReview(true);$('close-review').onclick=()=>$('review-dialog').close();
$('copy-review').onclick=async()=>{try{await navigator.clipboard.writeText($('review-prompt').value);$('review-status').textContent='已复制，打开 ChatGPT 后粘贴';}catch{$('review-prompt').focus();$('review-prompt').select();$('review-status').textContent='已选中全文，请按 Ctrl+C';}};
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
    await api(`/api/conflicts/${encodeURIComponent(syncConflict.collection)}/${encodeURIComponent(syncConflict.id)}/resolve`,'POST',{expected_version:syncConflict.local.record.version,data:{...resolvedData,notes:$('resolved-notes').value},human_confirm:true,confirmation_reason:'本人在工作台选择合并结果'});
    $('sync-conflict-dialog').close();await refresh();await checkSync();toast('合并结果已保存，两份原始分支仍保留');}
  catch(error){$('resolve-status').textContent=error.message;}
};
window.addEventListener('beforeunload',event=>{if(dirtyNotes){event.preventDefault();event.returnValue='';}});
window.addEventListener('unhandledrejection',event=>{toast(event.reason?.message||'操作未完成，请重试');});
for(const [key,label] of Object.entries(stages)) {if(key==='pending')continue;const opt=node('option',label);opt.value=key;$('stage-select').append(opt);}
refresh().catch(error=>toast(error.message));api('/api/sync','POST',{}).then(checkSync).catch(error=>toast(error.message));setInterval(checkSync,5000);
