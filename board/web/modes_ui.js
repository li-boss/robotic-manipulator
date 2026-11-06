/* 识别模式页：预览 → 勾选确认 → 开启（授权本会话有限次数动作）；随时可关闭。 */
const el=id=>document.getElementById(id);
async function api(path,body){const r=await fetch(path,{cache:'no-store',signal:AbortSignal.timeout(15000),...(body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{})});const data=await r.json();if(!r.ok)throw Error(data.error||'服务错误');return data;}
let csrf=null,status=null,offer=null,kind='block_grasp',busy=false;
async function post(path,body={}){if(!csrf)csrf=(await api('/control/status')).csrf;return api(path,{...body,csrf});}
function esc(s){return String(s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));}
function tab(k){kind=k;el('tabBlock').classList.toggle('on',k==='block_grasp');el('tabDance').classList.toggle('on',k==='gesture_dance');el('blockPanel').hidden=k!=='block_grasp';el('dancePanel').hidden=k!=='gesture_dance';clearOffer();}
el('tabBlock').onclick=()=>tab('block_grasp');el('tabDance').onclick=()=>tab('gesture_dance');
function clearOffer(){offer=null;el('offerPanel').hidden=true;el('confirm').checked=false;el('arm').disabled=true;}
el('taskMode').onchange=()=>{el('blockMax').disabled=el('taskMode').value==='pickup';el('destination').disabled=el('taskMode').value==='pickup';clearOffer();};
for(const n of document.querySelectorAll('#blockPanel input,#blockPanel select,#dancePanel input,#dancePanel select'))n.addEventListener('input',clearOffer);
function fillSelect(id,items,keep){const node=el(id),old=node.value;node.replaceChildren();for(const [value,text] of items){const o=document.createElement('option');o.value=value;o.textContent=text;node.append(o);}if(keep&&items.some(([v])=>v===old))node.value=old;}
function render(s){status=s;el('mode').textContent=s.mode==='real'?'实机':'模拟（不驱动设备）';
 fillSelect('destination',s.destinations.map(d=>[d,d]),true);fillSelect('baseState',s.saved_states.map(x=>[x.id,x.name+'（'+x.angles.join(',')+'）']),true);
 if(!el('routineChecks').dataset.ready){for(const [name,r] of Object.entries(s.routines)){const l=document.createElement('label');l.style.display='inline-flex';l.style.gap='6px';l.style.marginRight='16px';const c=document.createElement('input');c.type='checkbox';c.checked=true;c.value=name;c.style.width='auto';c.onchange=clearOffer;l.append(c,name+'：'+(r.label||s.patterns[r.pattern])+' ±'+r.amplitude_deg+'°×'+r.repeats);el('routineChecks').append(l);}el('routineChecks').dataset.ready='1';}
 const ss=s.session;el('sessionPanel').hidden=!ss;
 if(ss){el('sessionKind').textContent=s.kinds[ss.kind];el('sessionText').textContent='已用 '+ss.used+' / '+ss.max_actions+' 次 · 剩余 '+Math.floor(ss.expires_in/60)+' 分 '+(ss.expires_in%60)+' 秒 · '+ss.message+' · 会话编号 '+ss.id;}
 el('blockPreview').disabled=el('dancePreview').disabled=!!ss||busy;el('history').textContent=JSON.stringify(s.history,null,2);}
async function poll(){try{render(await api('/modes/status'));}catch(e){el('notice').textContent='连接中断：'+e.message;}finally{setTimeout(poll,1500);}}
function params(){if(kind==='block_grasp')return {kind,task_mode:el('taskMode').value,destination:el('destination').value||null,max_actions:Number(el('blockMax').value),min_score:Number(el('minScore').value),expires_minutes:Number(el('blockExpires').value)};
 return {kind,base_state_id:el('baseState').value,routines:[...document.querySelectorAll('#routineChecks input:checked')].map(c=>c.value),max_actions:Number(el('danceMax').value),cooldown_s:Number(el('cooldown').value),expires_minutes:Number(el('danceExpires').value)};}
function describe(p){const mode=status.mode==='real'?'实机':'模拟';let h='<p><b>'+mode+'</b> · 有效期 '+p.expires_minutes+' 分钟 · 最多 '+p.max_actions+' 次</p>';
 if(p.kind==='block_grasp'){h+='<p>任务：'+(p.task_mode==='pickup'?'只夹取并抬升（结束保持夹持）':'夹取并放到固定点“'+esc(p.destination)+'”（'+p.destination_xy_mm.map(v=>v.toFixed(1)).join(', ')+' mm），放置后撤离')+' · 置信度下限 '+p.min_score+'</p>';}
 else{h+='<p>起始姿态：'+esc(p.base_state.name)+'（'+p.base_state.angles.join(', ')+'°，顺序 '+p.base_state.order.join('→')+'）· 两次间隔 ≥ '+p.cooldown_s+' 秒</p>';
  for(const r of Object.values(p.routines)){h+='<details open><summary>'+esc(r.name)+'：'+esc(r.label)+' · '+r.steps.length+' 步 · 约 '+(r.duration_ms/1000).toFixed(1)+' 秒</summary><table><tr><th>#</th><th>步骤</th><th>关节目标</th><th>用时</th></tr>';
   r.steps.forEach((s,i)=>{h+='<tr><td>'+(i+1)+'</td><td>'+esc(s.label)+'</td><td>'+s.joints.map(([j,a])=>j+'号→'+a+'°').join('，')+'</td><td>'+s.motion_ms+'+'+s.settle_ms+' ms</td></tr>';});h+='</table></details>';}}
 return h+'<p class="muted">'+esc(p.conditions)+'</p>';}
async function preview(){busy=true;clearOffer();try{offer=await post('/modes/preview',params());el('offer').innerHTML=describe(offer.plan);el('confirmText').textContent='我已核对以上全部参数和现场空间，同意在本会话内由电脑端识别结果触发以上有限次数的动作';el('offerPanel').hidden=false;el('offerPanel').scrollIntoView({behavior:'smooth',block:'start'});el('notice').textContent='预览有效期 120 秒；勾选确认后开启。';}catch(e){el('notice').textContent=e.message;}finally{busy=false;}}
el('blockPreview').onclick=preview;el('dancePreview').onclick=preview;
el('confirm').onchange=()=>{el('arm').disabled=!offer||!el('confirm').checked;};el('cancel').onclick=clearOffer;
el('arm').onclick=async()=>{if(!offer||!el('confirm').checked)return;const token=offer.token;clearOffer();try{const r=await post('/modes/arm',{token,confirmed:true});el('notice').textContent='模式已开启：会话 '+r.session.id+'。现在运行电脑端程序即可。';render(await api('/modes/status'));}catch(e){el('notice').textContent='未开启：'+e.message;}};
el('disarm').onclick=async()=>{try{const r=await post('/modes/disarm');el('notice').textContent=r.message;render(await api('/modes/status'));}catch(e){el('notice').textContent=e.message;}};
tab('block_grasp');poll();
