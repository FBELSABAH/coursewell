/* Workspace presentation only: no new backend or academic inference. */
const themeMedia=matchMedia('(prefers-color-scheme: dark)');
function setAppearance(value){
 if(!['light','dark','system'].includes(value))value='light';
 try{localStorage.setItem('academic-theme',value)}catch(e){}
 document.documentElement.dataset.theme=value==='system'?(themeMedia.matches?'dark':'light'):value;
 document.getElementById('appearance').value=value;
}
let appearance='light';try{appearance=localStorage.getItem('academic-theme')||'light'}catch(e){}
setAppearance(appearance);
themeMedia.addEventListener('change',()=>{if(document.getElementById('appearance').value==='system')setAppearance('system')});
const navIcons={settings:'<circle cx="12" cy="12" r="3"/><path d="M12 2v3m0 14v3M2 12h3m14 0h3M5 5l2 2m10 10 2 2M5 19l2-2M17 7l2-2"/>',discover:'<path d="m12 3 9 9-9 9-9-9z"/><circle cx="12" cy="12" r="2"/>',overview:'<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>',courses:'<path d="M3 5h7l2 2h9v13H3z"/>',calendar:'<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M7 3v4m10-4v4M3 11h18"/>',study:'<path d="M12 3 9 9l-6 3 6 3 3 6 3-6 6-3-6-3z"/>',library:'<path d="M4 4v16m5-16v16m5-16 6 15"/>'};
document.querySelectorAll('[data-nav]').forEach(button=>{const span=button.querySelector('span');if(span){span.setAttribute('aria-hidden','true');span.innerHTML='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor">'+navIcons[button.dataset.nav]+'</svg>'}});
const searchDialog=document.createElement('dialog');searchDialog.className='search-dialog';searchDialog.setAttribute('aria-label','Search courses and files');searchDialog.innerHTML='<div class="search-dialog-head"><input type="search" id="globalSearch" aria-label="Search courses and files" placeholder="Search courses and files…" autocomplete="off"><button class="quiet-button" aria-label="Close search" onclick="searchDialog.close()">Esc</button></div><div id="searchResults" class="search-results"></div>';document.body.append(searchDialog);
function openSearch(){searchDialog.showModal();document.getElementById('globalSearch').value='';paintSearch('');document.getElementById('globalSearch').focus()}
function paintSearch(query){
 const q=query.trim().toLowerCase(),foundCourses=courses.filter(c=>(c.code+' '+c.name).toLowerCase().includes(q));
 const files=q?allFiles().filter(({c,f})=>(c.code+' '+c.name+' '+f.name+' '+f.title).toLowerCase().includes(q)):[];
 document.getElementById('searchResults').innerHTML=(foundCourses.length?'<div class="search-help">Courses</div>'+foundCourses.map(c=>`<button class="search-result ${c.color}" onclick="searchDialog.close();showCourse('${c.id}')"><span class="dot"></span><span>${esc(c.code)}<small>${esc(c.name)}</small></span></button>`).join(''):'')+(files.length?'<div class="search-help">Files · '+files.length+' matches</div>'+files.slice(0,30).map(({c,f})=>`<a class="search-result" href="${fileURL(c,f)}" target="_blank" rel="noopener"><span>${esc(f.name)}<small>${esc(c.code)}</small></span></a>`).join(''):'')+(!foundCourses.length&&!files.length?'<p class="search-help">No matching courses or files. Try a course code or part of a filename.</p>':'')+(files.length>30?'<p class="search-help">Showing the first 30 files. Refine your search to narrow the results.</p>':'');
}
document.getElementById('globalSearch').addEventListener('input',e=>paintSearch(e.target.value));
document.addEventListener('keydown',e=>{if((e.metaKey||e.ctrlKey)&&e.key.toLowerCase()==='k'){e.preventDefault();if(!searchDialog.open)openSearch()}});
function academicRow(e){
 const c=courses.find(c=>c.id===e.course_id);const date=e.date?new Date(e.date+'T12:00:00'):null;
 const dateText=e.cancelled?'Cancelled in Moodle':e.conflict_dates?'Conflicting dates · check sources':e.tentative?'Tentative date · check source':!e.date?'Date not confirmed':e.review?'Needs review':e.start_at?dateLabel(e.start_at):date.toLocaleDateString('en-CA',{month:'short',day:'numeric'})+' · Time not specified';
 return `<button class="academic-row ${c?.color||''}" data-assessment-id="${esc(e.id)}">${date?`<span class="date-stamp">${date.toLocaleDateString('en-CA',{month:'short'}).toUpperCase()}<b>${date.getDate()}</b></span>`:'<span class="dot" aria-hidden="true"></span>'}<span class="row-body"><span class="course-code">${esc(c?.code||e.course)}</span><strong>${esc(e.display_title||e.title)} <span class="pill">${esc(e.kind)}</span>${e.ai_reviewed?'<span class="ai-badge" title="AI second review">✦ AI</span>':''}</strong><small>${esc(e.date_role?e.date_role+' · '+dateText:dateText)}</small></span><span class="row-arrow" aria-hidden="true">↗</span></button>`;
}
function bindAssessmentRows(root){root.querySelectorAll('[data-assessment-id]').forEach(b=>b.onclick=()=>showAssessment(b.dataset.assessmentId))}
function paintAssessments(){
 if(view!=='overview')return;
 const panel=document.getElementById('assessmentsPanel');if(!panel)return;
 const choices=[['week','This week'],['month','Next 30 days'],['review','Needs review'],['all','All dates']];
 let html='<div class="section-title"><h2>Upcoming dates</h2></div><div class="assessment-tabs">'+choices.map(([id,label])=>`<button aria-pressed="${assessmentFilter===id}" class="${assessmentFilter===id?'active':''}" data-date-filter="${id}">${label}</button>`).join('')+'</div>';
 const signature=JSON.stringify([assessmentFilter,assessmentData,courses.length,typeof discovery!=='undefined'?discovery:null,typeof discoveryBusy!=='undefined'?discoveryBusy:false,typeof discoveryError!=='undefined'?discoveryError:'']);if(panel.dataset.signature===signature)return;panel.dataset.signature=signature;
 if(!assessmentData||assessmentData.error){const message=assessmentData?.error||'Reading saved dates…';panel.innerHTML=html+'<p class="empty-state">'+esc(message)+'</p>';return}
 const period=assessmentData.period;const today=period.today;const plus=n=>{const d=new Date(today+'T12:00:00');d.setDate(d.getDate()+n);return d.toLocaleDateString('en-CA')};
 const uncertain=e=>e.review||e.tentative||!e.date||e.conflict_dates;
 const events=assessmentData.events.filter(e=>assessmentFilter==='all'||assessmentFilter==='review'&&(uncertain(e)||academicProposalsFor(e).length)||assessmentFilter==='week'&&e.date>=period.start&&e.date<=period.end||assessmentFilter==='month'&&e.date>=today&&e.date<=plus(30)).sort((a,b)=>(a.date||'9999').localeCompare(b.date||'9999'));
 const group=e=>!e.date?'Date not confirmed':e.date<today?'Earlier dates':e.date===today?'Today':e.date===plus(1)?'Tomorrow':e.date<=period.end?'This week':'Later';
 let last='';
 const renderRows=items=>items.map(e=>{const label=group(e),heading=label!==last?'<div class="date-group">'+label+'</div>':'';last=label;return heading+academicRow(e)+(assessmentFilter==='review'?academicReviewDetails(e):'')}).join('');
 const earlier=assessmentFilter==='review'?events.filter(e=>e.date&&e.date<today):[];
 const current=assessmentFilter==='review'?events.filter(e=>!e.date||e.date>=today):events;
 const otherReviews=assessmentFilter==='review'&&typeof unlinkedAcademicReviews==='function'?unlinkedAcademicReviews():'';
 html+=current.length?renderRows(current):!otherReviews?'<div class="empty-state"><strong>'+ (assessmentFilter==='review'?'No coursework dates waiting for review':'No dates found for this period')+'</strong></div>':'';
 html+=otherReviews;
 if(earlier.length)html+='<details class="coverage"><summary>Earlier coursework dates · '+earlier.length+'</summary>'+renderRows(earlier)+'</details>';

 const coverage=assessmentData.coverage||[],problems=coverage.filter(c=>c.status!=='Text analyzed');
 html+=`<details class="coverage"><summary>Source coverage · ${problems.length} need review</summary><p>${coverage.length-problems.length} sources read. Some dates may be in sources the app could not read.</p><ul>${problems.map(p=>`<li>${esc(p.name)} — ${esc(p.status)}</li>`).join('')}</ul></details>`;
 if(assessmentData.changes?.length)html+='<details class="coverage"><summary>Date changes</summary>'+assessmentData.changes.map(c=>`<div class="evidence"><b>${esc(courses.find(course=>course.id===c.course_id)?.code||c.course_id)} · ${esc(c.title)}</b><br>${esc(c.before.join(', ')||'Not previously found')} → ${esc(c.after.join(', '))}</div>`).join('')+'</details>';
 panel.innerHTML=html;panel.querySelectorAll('[data-date-filter]').forEach(b=>b.onclick=()=>{assessmentFilter=b.dataset.dateFilter;history.replaceState(null,'',assessmentFilter==='review'?'#overview-review':location.pathname);paintAssessments();panel.querySelector('[data-date-filter="'+assessmentFilter+'"]').focus()});bindAssessmentRows(panel);

}
paintAssessments();
