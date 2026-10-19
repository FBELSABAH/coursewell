let semesterFeedback="";
function semesterSettings(){
 const value=semesterState.mode||'auto';
 return settingsSection('settingsSemester','Semester',esc(semesterState.label||''),settingsRow('Active semester','Auto follows January–April, May–August and September–December. Older files and Google events are kept.',`<form onsubmit="event.preventDefault();saveSemester()"><label class="sr-only" for="semesterChoice">Semester</label><select id="semesterChoice" aria-label="Semester">${['auto',...Array.from({length:5},(_,i)=>new Date().getFullYear()-2+i).flatMap(y=>['W','S','F'].map(t=>t+y))].map(t=>`<option value="${t}" ${value===t?'selected':''}>${t==='auto'?'Auto (current semester)':({W:'Winter',S:'Summer',F:'Fall'}[t[0]]+' '+t.slice(1))}</option>`).join('')}</select><button class="primary" type="submit">Save semester</button></form>`)+`<p id="semesterFeedback" role="status">${esc(semesterFeedback)}</p>`);
}
async function saveSemester(){
 const feedback=document.getElementById('semesterFeedback');
 try{if(!dashboardToken)await status();const r=await fetch('/api/semester',{method:'POST',headers:{'Content-Type':'application/json','X-Dashboard-Token':dashboardToken},body:JSON.stringify({term:document.getElementById('semesterChoice').value})});const data=await r.json();if(!r.ok)throw Error(data.error);semesterState=data;semesterFeedback='Saved. Update Coursewell to discover and fetch this semester’s courses.';assessmentData=null;studyLoaded=false;studySelected.clear();await load();await loadAssessments();await loadCalendarPlan();await loadDiscover();await studyLoad();document.getElementById('semesterFeedback').textContent='Saved. Update Coursewell to discover and fetch this semester’s courses.'}catch(e){(document.getElementById('semesterFeedback')||feedback).textContent=e.message}
}
/* Daily work stays in Overview and Calendar; service controls stay here. */
function settingsRow(title,description,control){return `<div class="settings-row"><div><h3>${title}</h3>${description?'<p>'+description+'</p>':''}</div><div class="settings-control">${control}</div></div>`}
function settingsSection(id,title,status,body){return `<section class="settings-section" aria-labelledby="${id}"><header><h2 id="${id}">${title}</h2>${status?'<span class="tag">'+esc(status)+'</span>':''}</header>${body}</section>`}
function googleSettings(){
 const s=calendarState,busy=calendarActionBusy||s.busy;
 const action=!s.connected||s.needs_reconnect?`<button class="primary" onclick="calendarAction('connect')" ${!s.configured||busy?'disabled':''}>${s.needs_reconnect?'Reconnect Google':'Connect Google'}</button>`:!s.enabled?`<button class="primary" onclick="calendarAction('enable')" ${busy||calendarPlanLoading||!calendarPlan?'disabled':''}>Enable calendar sync</button>`:`<button class="primary" onclick="calendarAction('sync')" ${busy?'disabled':''}>${s.busy?'Syncing…':'Sync now'}</button>`;
 let body=settingsRow('Calendar sync',esc(calendarServiceError||s.message||'Send eligible dates to Google Calendar.'),action);
 body+=settingsRow('Last successful sync',s.last_sync?esc(dateLabel(s.last_sync)):'Not synced yet','');
 const links=(s.calendars||[]).map(c=>`<a href="${esc(c.url)}" target="_blank" rel="noopener noreferrer">${esc(c.name.replace(/^Coursewell\s*[·—-]\s*/i,''))} ↗</a>`).join('');
 if(links)body+=settingsRow('Your calendars','Classes, assessments, and optional events can be shown or hidden separately.',links);
 body+=`<details class="settings-details"><summary>Connection & reminders</summary><p>Sync runs after Update Coursewell. Times use Halifax time; dates without a time stay all-day.</p><p>Classes: 15 minutes before. Timed assessments: 24 hours and 1 hour before. All-day dates: 9 a.m. the day before. Enable notifications in Google Calendar on your device.</p>${s.connected?`<div class="settings-control"><button class="textbutton" onclick="calendarAction('connect')" ${busy?'disabled':''}>Reconnect Google</button><button class="textbutton" onclick="calendarDisconnect()" ${busy?'disabled':''}>Disconnect Google</button></div>`:''}${!s.configured?'<p>Google app credentials are missing.</p>':''}</details>`;
 if(s.warnings?.length)body+='<details class="settings-details"><summary>Sync warnings · '+s.warnings.length+'</summary><ul>'+s.warnings.map(w=>'<li>'+esc(w)+'</li>').join('')+'</ul></details>';
 return settingsSection('settingsGoogle','Google Calendar',calendarStatusLabel(),body);
}
function outlookSettings(){
 if(!discovery)return settingsSection('settingsOutlook','Outlook','','<p class="settings-loading" role="status">Checking connection…</p>');
 const o=discovery?.outlook||{},busy=discoveryBusy||['running','connecting'].includes(o.status);
 let body=settingsRow('University email',esc(o.message||'Connect your university mailbox.'),`<button class="primary" onclick="discoveryAction('connect-outlook')" ${busy?'disabled':''}>${o.connected?'Reconnect Outlook':'Connect Outlook'}</button>`);
 body+=settingsRow('Last complete check',o.last_success?esc(dateLabel(o.last_success)):'Not checked yet','');
 body+='<details class="settings-details"><summary>How email checking works</summary><p>Checks new mail across folders when you update Coursewell. The first check covers seven days; completed checks save a checkpoint. Opening mail may mark it as read.</p><p>Coursework dates appear under Upcoming dates. Optional events appear in Calendar for your approval.</p></details>';
 if(o.issues?.length)body+='<details class="settings-details"><summary>Collection issues · '+o.issues.length+'</summary>'+o.issues.map(i=>'<p>'+esc(i)+'</p>').join('')+'</details>';
 return settingsSection('settingsOutlook','Outlook',o.connected?'Connected':'Not connected',body);
}
function chatGPTSettings(){
 if(!studyLoaded)return settingsSection('settingsChat','ChatGPT','',`<p class="settings-loading" role="status">${esc(studyError||'Checking connection…')}</p>`);
 const s=studyState;
 let body=settingsRow('Account',s.connected?esc(s.email||'Signed in'):'Use your eligible ChatGPT plan for source interpretation.',`<button class="primary" onclick="studyConnect(false)" ${studyBusy?'disabled':''}>${s.connected?'Reconnect':'Continue with ChatGPT'}</button>`);
 body+=settingsRow('Plan allowance',s.plan_enabled?'Plan usage is enabled. There is no paid API fallback.':'Plan permission is not enabled.', '<a href="https://chatgpt.com/settings/usage" target="_blank" rel="noopener noreferrer">Manage usage ↗</a>');
 body+=`<details class="settings-details"><summary>Account options</summary>${s.accounts?.length?'<label for="studyAccount">Saved account</label><select id="studyAccount">'+s.accounts.map(a=>`<option value="${esc(a.id)}" ${a.id===s.active?'selected':''}>${esc(a.email||'Pending registration')}</option>`).join('')+'</select>':''}<div class="settings-control"><button class="textbutton" onclick="studyConnect(true)">Use another account</button>${s.connected?'<button class="textbutton" onclick="studyDisconnect()">Disconnect ChatGPT</button>':''}</div></details>`;
 return settingsSection('settingsChat','ChatGPT',s.connected?'Connected':'Not connected',body);
}
function automationSettings(){
 const d=discovery||{},s=d.settings||{},state=d.review_state||{},enabled=d.connection?.plan_enabled;
 const busy=discoveryBusy||d.busy;
 let body=settingsRow('Review queue',d.busy?'Reviewing unclear sources…':d.unreviewed?`${d.unreviewed} ${d.unreviewed===1?'source':'sources'} waiting`:'No sources waiting',`<button class="primary" onclick="discoveryAction('review',{model:discovery.settings.model})" ${busy||!enabled||!s.model||!d.unreviewed?'disabled':''}>${d.busy?'Reviewing…':'Review now'}</button>`);
 body+=settingsRow('Model','Used only for sources that need a second interpretation.',`<select id="reviewModel" aria-label="AI review model" onchange="discoveryAction('settings',{model:this.value})" ${busy||!enabled||!discoveryModels.length?'disabled':''}>${discoveryModels.map(m=>`<option value="${esc(m.id)}" ${m.id===s.model?'selected':''}>${esc(m.name)}</option>`).join('')||'<option>Connect ChatGPT first</option>'}</select>`);
 body+=settingsRow('Review after fetching','Run AI review after Update Coursewell.',`<label class="settings-toggle"><input type="checkbox" aria-label="Review after fetching" ${s.auto_review?'checked':''} ${busy||!enabled?'disabled':''} onchange="discoveryAction('settings',{auto_review:this.checked})"><span>${s.auto_review?'On':'Off'}</span></label>`);
 body+=settingsRow('Apply supported coursework dates','Uncertain dates still need confirmation. Optional events always need your approval.',`<label class="settings-toggle"><input type="checkbox" aria-label="Apply supported coursework dates" ${s.auto_apply?'checked':''} ${busy||!enabled?'disabled':''} onchange="discoveryAction('settings',{auto_apply:this.checked})"><span>${s.auto_apply?'On':'Off'}</span></label>`);
 body+=`<details class="settings-details"><summary>Usage & diagnostics</summary><p>Relevant source text is sent to OpenAI and uses your plan allowance. ${d.filtered||0} sources were skipped by the scheduling filter; originals stay available.</p>${usageSummary(d.usage)}<button class="textbutton" onclick="discoveryAction('collect')" ${busy?'disabled':''}>Check saved Moodle files</button>${d.coverage?.length?'<details><summary>'+d.coverage.length+' sources have reading limitations</summary>'+d.coverage.map(c=>'<p><strong>'+esc(c.title)+'</strong><br>'+esc(c.notes.join(' · '))+'</p>').join('')+'</details>':''}</details>`;
 if(state.errors?.length)body+='<p class="settings-error" role="status">'+esc(state.errors.join(' · '))+'</p>';
 return settingsSection('settingsReview','Automatic review',d.busy?'Running':s.auto_review?'Enabled':'Manual',body);
}
function paintSettings(){
 if(view!=='settings')return;
 document.getElementById('crumb').textContent='Settings';updateSidebarSelection();
 const content=document.getElementById('content');
 const signature=JSON.stringify([semesterState,semesterFeedback,calendarState,calendarServiceError,calendarActionBusy,calendarPlanLoading,!!calendarPlan,discovery,discoveryModels,discoveryBusy,discoveryError,studyState,studyLoading,studyLoaded,studyError]);
 if(content.dataset.settingsSignature===signature&&document.getElementById('settingsPage'))return;
 const active=document.activeElement,focusId=content.contains(active)?active.id:'';
 const opened=[...content.querySelectorAll('details')].map((d,i)=>d.open?i:-1);
 content.dataset.settingsSignature=signature;
 content.innerHTML=`<div id="settingsPage"><div class="intro"><div><h1>Settings</h1><p>Manage your connections and update preferences.</p></div>${pageArt('settings')}<button class="textbutton" onclick="loadCalendar();loadDiscover();studyLoad()">Refresh status</button></div><div class="settings-sections">${semesterSettings()}${googleSettings()}${outlookSettings()}${chatGPTSettings()}${automationSettings()}</div><p role="status">${esc(discoveryError||studyError||'')}</p></div>`;
 content.querySelectorAll('details').forEach((d,i)=>d.open=opened.includes(i));
 if(focusId)document.getElementById(focusId)?.focus();
}
const beforeSettingsRender=render;
render=function(){if(view==='settings'){paintSettings()}else beforeSettingsRender()};
const beforeSettingsNavigate=navigate;
navigate=function(v){if(v==='discover'){assessmentFilter='review';v='overview'}beforeSettingsNavigate(v);history.replaceState(null,'',v==='overview'?location.pathname:'#'+v);if(v==='settings'){loadCalendar();loadCalendarPlan();loadDiscover();studyLoad()}};
if(location.hash==='#settings'){view='settings';render();loadCalendar();loadCalendarPlan();loadDiscover();studyLoad()}
