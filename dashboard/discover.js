/* Second review: evidence first, optional events always opt-in. */
let discoveryRequest=0,discoveryActionId=null;
let discovery=null,discoveryError='',discoveryBusy=false,discoveryModels=[],discoveryFilter='pending';
const beforeDiscoverRender=render;
render=function(){if(view==='discover'){view='overview';assessmentFilter='review';beforeDiscoverRender();loadDiscover()}else beforeDiscoverRender()};
const isAcademicProposal=i=>(i.review_destination|| (i.payload.kind==='event'?'calendar':'coursework'))==='coursework';
const aiBadge=()=>'<span class="ai-badge" title="Interpreted by AI in a second source review">✦ AI</span>';
async function loadDiscover(){
 if(discoveryBusy)return;
 const request=++discoveryRequest,before=JSON.stringify(discovery);
 try{const r=await fetch('/api/discover');if(!r.ok)throw Error('Reopen Coursewell.app to load AI review.');const next=await r.json();if(request!==discoveryRequest||discoveryBusy)return;discovery=next;if(discoveryError==='Failed to fetch')discoveryError='';if(discovery.connection.plan_enabled&&!discoveryModels.length)discoveryModels=await studyGet('models')}
 catch(e){discoveryError=e.message}
 if(before!==JSON.stringify(discovery)){if(view==='settings')paintSettings();else if(view==='discover')paintDiscover();else if(view==='calendar')paintCalendarReview();else if(view==='overview'){paintAssessments();decorate()}}
}
function proposalCard(i,compact=false){
 const p=i.payload,academic=isAcademicProposal(i);
 const reasons=[...(p.uncertainties||[]),...(i.notes||[]),...(academic?p.application?.reasons||[]:[])];
 const stale=i.current_hash!==i.source_hash;
 const uncertain=reasons.length||!p.date||stale;
 const actions=i.decision==='pending'?`<button class="${compact?'textbutton':'primary'}" ${discoveryBusy||(!academic&&uncertain)?'disabled':''} onclick="${academic?`confirmProposal('${i.id}')`:`discoveryAction('add',{id:'${i.id}'})`}">${academic?'Review & confirm':'Add to calendar'}</button><button class="textbutton" ${discoveryBusy?'disabled':''} onclick="discoveryAction('skip',{id:'${i.id}'})">Skip</button>`:'';
 return `<article class="discover-item ${compact?'compact':''}"><div class="section-title"><h${compact?'3':'2'}>${esc(p.title)}</h${compact?'3':'2'}>${aiBadge()}</div><p class="section-note">${esc(courses.find(c=>c.id===(p.application?.course_id||p.course_id))?.code||'University')}</p><p class="proposal-date">${esc(p.date||'Date needs checking')}${p.start_at?' · '+esc(new Date(p.start_at).toLocaleTimeString('en-CA',{timeZone:'America/Halifax',hour:'numeric',minute:'2-digit'})):''}${p.location?' · '+esc(p.location):''}</p>${p.summary?`<p>${esc(p.summary)}</p>`:''}<small>${esc(i.source_kind==='outlook'?'Outlook':'Moodle')} · ${esc(i.decision==='added'?(academic?'In your calendar plan':'Added to Google'):i.decision)}</small><details><summary>Source & evidence</summary><p>${esc(i.source_title)}</p><blockquote>${esc(p.quote)}</blockquote>${[...new Set(reasons)].map(n=>'<p class="section-note">'+esc(n)+'</p>').join('')}${stale?'<p>The source changed. This proposal needs another review.</p>':''}${/^https:\/\//.test(i.url)?'<a href="'+esc(i.url)+'" target="_blank" rel="noopener noreferrer">Open original ↗</a>':''}${p.verification_reason?'<p class="section-note">AI verification: '+esc(p.verification_reason)+'</p>':''}</details><div class="cal-actions">${actions}</div></article>`;
}
function usageSummary(u={}){
 const n=v=>Number(v||0).toLocaleString();
 return `<details class="coverage"><summary>AI usage · ${n(u.requests)} requests recorded</summary><p>${n(u.input_tokens)} input tokens · ${n(u.output_tokens)} output tokens</p><p>${n(u.cached_tokens)} cached input · ${n(u.reasoning_tokens)} reasoning tokens (included in totals)</p><p>${n(u.failed_requests)} failed requests · ${n((u.requests||0)-(u.measured_requests||0))} requests without token measurements</p><p class="section-note">Since ${u.since?esc(dateLabel(u.since)):'tracking was enabled'}. Earlier usage is not included. Unreported usage may still consume allowance. These are tokens, not a percentage of your plan.</p></details>`;
}
function openAcademicReviews(){assessmentFilter='review';navigate('overview');history.replaceState(null,'','#overview-review');loadDiscover();document.getElementById('assessmentsPanel')?.scrollIntoView({block:'start',behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'instant':'smooth'})}
function paintDiscover(){openAcademicReviews()}
function academicProposalsFor(e){
 const ids=new Set([e.id,...(e.merged_ids||[])]);
 return (discovery?.items||[]).filter(i=>i.decision==='pending'&&isAcademicProposal(i)&&((e.related_proposal_ids||[]).includes(i.id)||ids.has(i.payload.application?.target_id)||ids.has(i.payload.target_id)));
}
function academicReviewDetails(e){
 const items=academicProposalsFor(e);
 return items.length?`<details class="academic-review-details"><summary>Review evidence & confirm · ${items.length} ${items.length===1?'interpretation':'interpretations'}</summary>${items.map(i=>proposalCard(i,true)).join('')}</details>`:'';
}
function unlinkedAcademicReviews(){
 const linked=new Set((assessmentData?.events||[]).flatMap(e=>academicProposalsFor(e).map(i=>i.id)));
 const items=(discovery?.items||[]).filter(i=>i.decision==='pending'&&isAcademicProposal(i)&&!linked.has(i.id));
 return (discoveryError?`<p role="status">${esc(discoveryError)}</p>`:'')+(items.length?'<div class="date-group">Other coursework dates to confirm</div>'+groupedProposalCards(items):'');
}
let optionalFilter='pending';
function optionalCard(i){
 const p=i.payload;
 const reasons=[...new Set([...(p.uncertainties||[]),...(i.notes||[])])];
 if(i.current_hash!==i.source_hash)reasons.push('The source changed; another review is needed.');
 if(!p.date)reasons.push('The date needs checking.');
 else if(p.date<calendarToday)reasons.push('This event has already passed.');
 const busy=discoveryBusy&&discoveryActionId===i.id;
 const when=p.date?new Date(p.date+'T12:00:00').toLocaleDateString('en-CA',{weekday:'short',month:'short',day:'numeric'}):'Date to confirm';
 return `<article class="suggestion-card" aria-busy="${busy}" aria-labelledby="suggestionTitle"><div class="suggestion-meta">${esc(i.source_kind==='outlook'?'Outlook':'Moodle')}${aiBadge()}</div><h3 id="suggestionTitle" tabindex="-1">${esc(p.title)}</h3><dl class="suggestion-facts"><div><dt>When</dt><dd>${esc(when)}${p.start_at?' · '+esc(calendarTime(p)):''}</dd></div>${p.location?`<div><dt>Where</dt><dd>${esc(p.location)}</dd></div>`:''}</dl>${p.summary?`<p class="suggestion-summary">${esc(p.summary.length>280?p.summary.slice(0,277)+'…':p.summary)}</p>`:''}<details><summary>Read source</summary><p>${esc(i.source_title)}</p>${p.summary?.length>280?'<p>'+esc(p.summary)+'</p>':''}<blockquote>${esc(p.quote)}</blockquote>${/^https:\/\//.test(i.url)?`<a href="${esc(i.url)}" target="_blank" rel="noopener noreferrer">Open original ↗</a>`:''}</details>${reasons.length?`<p class="section-note">${esc(reasons.join(' '))}</p>`:''}<div class="suggestion-actions"><button class="textbutton" ${discoveryBusy?'disabled':''} onclick="discoveryAction('skip',{id:'${i.id}'})">${busy?'Please wait…':'Ignore'}</button><button class="primary" ${discoveryBusy||reasons.length?'disabled':''} onclick="discoveryAction('add',{id:'${i.id}'})">${busy?'Please wait…':'Add'}</button></div></article>`;
}
function paintCalendarReview(){
 const root=document.getElementById('calendarReview');if(!root)return;
 const optional=(discovery?.items||[]).filter(i=>!isAcademicProposal(i));
 const resolved=new Set(optional.filter(i=>['added','changed','skipped'].includes(i.decision)).map(i=>optionalIdentity(i.payload)));
 const groups=new Map();
 for(const i of optional.filter(i=>i.decision==='pending'&&!resolved.has(optionalIdentity(i.payload))))if(!groups.has(optionalIdentity(i.payload)))groups.set(optionalIdentity(i.payload),i);
 const pending=[...groups.values()];
 const history=optional.filter(i=>['added','changed','skipped'].includes(i.decision));
 root.hidden=false;
 root.innerHTML=`<div class="section-title"><h2>Worth adding?</h2><span class="tag">${pending.length} waiting</span></div><p class="section-note">Choose what belongs in your calendar.</p><p class="suggestion-status" role="status">${esc(discoveryError||(!discovery?'Loading events…':''))}</p>${pending.length?optionalCard(pending[0]):'<div class="empty-state">'+(discovery?'You’re all caught up.':'Checking suggestions…')+'</div>'}${history.length?`<details class="suggestion-history"><summary>Previous choices</summary>${history.map(i=>`<div><b>${esc(i.payload.title)}</b><small>${esc(i.payload.date||'')} · ${i.decision==='changed'?'Already added · source changed; check Google Calendar':i.decision==='added'?'Added':'Ignored / duplicate'}</small></div>`).join('')}</details>`:''}`;
}
function confirmProposal(id){
 const row=discovery?.items.find(i=>i.id===id);if(!row)return;
 const p=row.payload,plan=p.application||{},targets=discovery.targets||[];
 modal('Confirm academic date',`<p>Check the original evidence before applying this correction. It may update an existing calendar entry.</p><blockquote>${esc(p.quote)}</blockquote><label for="confirmCourse">Course</label><select id="confirmCourse">${courses.map(c=>`<option value="${c.id}" ${c.id===plan.course_id?'selected':''}>${esc(c.code)}</option>`).join('')}</select><label for="confirmTarget">Existing assessment</label><select id="confirmTarget"><option value="">New assessment</option>${targets.map(t=>`<option value="${esc(t.id)}" ${t.id===plan.target_id?'selected':''}>${esc(courses.find(c=>c.id===t.course_id)?.code||t.course_id)} · ${esc(t.title)} · ${esc(t.date||'date unknown')}</option>`).join('')}</select><label for="confirmDate">Date</label><input id="confirmDate" type="date" value="${esc(p.date||'')}"><div class="cal-actions"><button class="primary" id="confirmReview">Confirm ${p.kind==='cancellation'?'cancellation':'date'}</button></div>`);
 document.getElementById('confirmReview').onclick=()=>{const body={id,course_id:document.getElementById('confirmCourse').value,target_id:document.getElementById('confirmTarget').value,date:document.getElementById('confirmDate').value};document.getElementById('dialog').close();discoveryAction('confirm',body)};
}
async function discoveryAction(action,body={}){
 if(discoveryBusy)return;discoveryBusy=true;discoveryActionId=body.id;++discoveryRequest;discoveryError='';if(view==='settings')paintSettings();else if(view==='discover')paintDiscover();else if(view==='overview')paintAssessments();else paintCalendarReview();
 try{if(!dashboardToken)await status();const r=await fetch('/api/discover/'+action,{method:'POST',headers:{'Content-Type':'application/json','X-Dashboard-Token':dashboardToken},body:JSON.stringify(body)});const result=await r.json();if(!r.ok)throw Error(result.error||'Request failed.');discoveryError=result.message||'';
 if(['add','skip'].includes(action)&&discovery){
  const selected=discovery.items.find(i=>i.id===body.id);
  if(selected&&!isAcademicProposal(selected)){
   const key=optionalIdentity(selected.payload);
   discovery.items=discovery.items.map(i=>!isAcademicProposal(i)&&optionalIdentity(i.payload)===key&&i.decision==='pending'?{...i,decision:i.id===body.id&&action==='add'?'added':'skipped'}:i);
  }
 }
 }
 catch(e){discoveryError=e.message}
 finally{discoveryBusy=false;discoveryActionId=null;if(view==='calendar')paintCalendarReview();await loadDiscover();if(['add','confirm','skip'].includes(action)){await loadAssessments();await loadCalendarPlan()}if(view==='calendar')document.getElementById('suggestionTitle')?.focus({preventScroll:true})}
}
setInterval(()=>{if(['discover','calendar','overview','settings'].includes(view))loadDiscover()},5000);
if(['#discover','#overview-review'].includes(location.hash)){view='overview';assessmentFilter='review';render();loadDiscover()}


function optionalIdentity(p){return p.title.toLowerCase().replace(/[^a-z0-9]+/g,' ').trim()+'|'+(p.date||'')+'|'+(p.start_at||'')}
function groupedProposalCards(items,compact=false){
 const groups=new Map();
 for(const item of items){
  const p=item.payload;let title=p.title.toLowerCase().replace(/[#]/g,'').replace(/\s+/g,' ').trim();
  if(isAcademicProposal(item))title=title.replace(/ (submission|delivery window)$/,'');
  const key=isAcademicProposal(item)?(p.application?.course_id||p.course_id||'')+'|'+(p.application?.target_id||p.target_id||title):optionalIdentity(p);
  if(!groups.has(key))groups.set(key,{title: p.title.replace(/ (submission|delivery window)$/i,''),course:p.application?.course_id||p.course_id,items:[]});groups.get(key).items.push(item);
 }
 return [...groups.values()].map(g=>g.items.length===1?proposalCard(g.items[0],compact):`<article class="discover-item"><div class="section-title"><h2>${esc(g.title)}</h2>${aiBadge()}</div><p>${esc(courses.find(c=>c.id===g.course)?.code||'University')} · ${g.items.length} source interpretations.</p><details><summary>Compare evidence & resolve</summary>${g.items.map(i=>proposalCard(i,true)).join('')}</details></article>`).join('');
}
