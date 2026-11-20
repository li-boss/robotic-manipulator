/* 识别模式页：预览 → 勾选确认 → 开启（授权本会话有限次数动作）；随时可关闭。 */
const el=id=>document.getElementById(id);
async function api(path,body){const r=await fetch(path,{cache:'no-store',signal:AbortSignal.timeout(15000),...(body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{})});const data=await r.json();if(!r.ok)throw Error(data.error||'服务错误');return data;}
let csrf=null,status=null,offer=null,kind='block_grasp',busy=false,starting=false;
async function post(path,body={}){if(!csrf)csrf=(await api('/control/status')).csrf;return api(path,{...body,csrf});}
function esc(s){return String(s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));}
const TABS={block_grasp:['tabBlock','blockPanel'],gesture_dance:['tabDance','dancePanel'],voice_control:['tabVoice','voicePanel']};
function tab(k){kind=k;for(const [name,[t,p]] of Object.entries(TABS)){el(t).classList.toggle('on',k===name);el(p).hidden=k!==name;}clearOffer();}
for(const [name,[t]] of Object.entries(TABS))el(t).onclick=()=>tab(name);
function clearOffer(){offer=null;el('offerPanel').hidden=true;el('confirm').checked=false;el('arm').disabled=true;}
el('voiceGrasp').onchange=()=>{el('voiceGraspRow').hidden=!el('voiceGrasp').checked;clearOffer();};
el('voiceTaskMode').onchange=()=>{el('voiceDestination').disabled=el('voiceTaskMode').value==='pickup';clearOffer();};
el('taskMode').onchange=()=>{el('blockMax').disabled=el('taskMode').value==='pickup';el('destination').disabled=el('taskMode').value==='pickup';clearOffer();};
for(const n of document.querySelectorAll('#blockPanel input,#blockPanel select,#dancePanel input,#dancePanel select,#voicePanel input,#voicePanel select'))n.addEventListener('input',clearOffer);
function fillSelect(id,items,keep){const node=el(id),old=node.value;node.replaceChildren();for(const [value,text] of items){const o=document.createElement('option');o.value=value;o.textContent=text;node.append(o);}if(keep&&items.some(([v])=>v===old))node.value=old;}
function render(s){status=s;el('mode').textContent=s.mode==='real'?'实机':'模拟（不驱动设备）';
 fillSelect('destination',s.destinations.map(d=>[d,d]),true);fillSelect('voiceDestination',s.destinations.map(d=>[d,d]),true);
 const states=s.saved_states.map(x=>[x.id,x.name+'（'+x.angles.join(',')+'）']);fillSelect('baseState',states,true);fillSelect('voiceVertical',states,true);fillSelect('voiceObserve',states,true);
 if(!el('routineChecks').dataset.ready){for(const [name,r] of Object.entries(s.routines)){const l=document.createElement('label');l.style.display='inline-flex';l.style.gap='6px';l.style.marginRight='16px';const c=document.createElement('input');c.type='checkbox';c.checked=true;c.value=name;c.style.width='auto';c.onchange=clearOffer;l.append(c,name+'：'+(r.label||s.patterns[r.pattern])+' ±'+r.amplitude_deg+'°×'+r.repeats);el('routineChecks').append(l);}el('routineChecks').dataset.ready='1';}
 const ss=s.session;el('sessionPanel').hidden=!ss;
 if(ss){el('sessionKind').textContent=s.kinds[ss.kind];el('sessionText').textContent='已用 '+ss.used+' / '+ss.max_actions+' 次 · 剩余 '+Math.floor(ss.expires_in/60)+' 分 '+(ss.expires_in%60)+' 秒 · '+ss.message+' · 会话编号 '+ss.id;}
 const grasp=!!ss&&(ss.kind==='block_grasp'||(ss.kind==='voice_control'&&!!ss.plan.grasp));el('graspBox').hidden=!grasp;el('startGrasp').hidden=!ss||ss.kind!=='block_grasp';
 if(grasp){const req=ss.grasp_request,last=ss.last_result;
  el('startGrasp').disabled=starting||!!req||s.busy||ss.used>=ss.max_actions;
  el('graspState').textContent=req?'本次抓方块进行中：'+({waiting_pc:'等待电脑端程序（Mac 上需正在运行 '+(ss.kind==='voice_control'?'python -m pc.voice_control.main':'run_block_grasp.py')+'）',freezing:'冻结画面',frozen:'识别中',planning:'规划路径'}[req.stage]||req.stage)+'……':(ss.used>=ss.max_actions?'本会话次数已用完':ss.kind==='voice_control'?'说“抓方块”即开始一次完整的识别夹取；机械臂会运动，请确认周围安全':'点击后做一次完整的识别夹取；机械臂会运动，请确认周围安全');
  el('graspResult').textContent=last?'上一次（'+new Date(last.at*1000).toLocaleTimeString()+'）：'+last.message:'';}
 el('blockPreview').disabled=el('dancePreview').disabled=el('voicePreview').disabled=!!ss||busy;el('history').textContent=JSON.stringify(s.history,null,2);}
async function poll(){try{render(await api('/modes/status'));}catch(e){el('notice').textContent='连接中断：'+e.message;}finally{setTimeout(poll,1500);}}
function params(){if(kind==='block_grasp')return {kind,task_mode:el('taskMode').value,destination:el('destination').value||null,max_actions:Number(el('blockMax').value),min_score:Number(el('minScore').value),expires_minutes:Number(el('blockExpires').value)};
 if(kind==='voice_control'){const g=el('voiceGrasp').checked;return {kind,vertical_state_id:el('voiceVertical').value,observe_state_id:el('voiceObserve').value,servo_motion_ms:Number(el('voiceServoMs').value),max_actions:Number(el('voiceMax').value),cooldown_s:Number(el('voiceCooldown').value),expires_minutes:Number(el('voiceExpires').value),grasp_enabled:g,...(g?{task_mode:el('voiceTaskMode').value,destination:el('voiceDestination').value||null,min_score:Number(el('voiceMinScore').value)}:{})};}
 return {kind,base_state_id:el('baseState').value,routines:[...document.querySelectorAll('#routineChecks input:checked')].map(c=>c.value),max_actions:Number(el('danceMax').value),cooldown_s:Number(el('cooldown').value),expires_minutes:Number(el('danceExpires').value)};}
function describe(p){const mode=status.mode==='real'?'实机':'模拟';let h='<p><b>'+mode+'</b> · 有效期 '+p.expires_minutes+' 分钟 · 最多 '+p.max_actions+' 次</p>';
 const grasp=g=>'任务：'+(g.task_mode==='pickup'?'只夹取并抬升（结束保持夹持）':'夹取并放到固定点“'+esc(g.destination)+'”（'+g.destination_xy_mm.map(v=>v.toFixed(1)).join(', ')+' mm），放置后撤离')+' · 置信度下限 '+g.min_score;
 if(p.kind==='block_grasp'){h+='<p>'+grasp(p)+'</p>';}
 else if(p.kind==='voice_control'){h+='<h3>姿态（按姿态按钮顺序逐关节，每关节用时如表）</h3><table><tr><th>语音</th><th>姿态按钮</th><th>六关节目标</th><th>顺序</th><th>每关节用时</th></tr>';
  for(const [k,label] of [['vertical','回到竖直姿态'],['observe','回到观察姿态']]){const st=p.poses[k];h+='<tr><td>'+label+'</td><td>'+esc(st.name)+'</td><td>'+st.angles.join(', ')+'°</td><td>'+st.order.join('→')+'</td><td>'+st.motion_ms+' ms</td></tr>';}
  h+='</table><h3>N号舵机转到X度</h3><p>每次只转说出的一个舵机，用时 '+p.servo.motion_ms+'+'+p.servo.settle_ms+' ms；角度须为整数且在范围内（超出直接拒绝，不截断）：'+Object.entries(p.servo.limits).map(([j,[a,b]])=>j+'号 '+a+'～'+b+'°').join('，')+'</p>';
  h+='<h3>抓方块</h3><p>'+(p.grasp?grasp(p.grasp)+'。每说一次做一次完整的冻结→识别→规划→执行，不自动重试':'本会话不允许语音抓方块')+'</p>';
  h+='<h3>跳舞（语音或表情/手势识别触发；起始姿态：'+esc(p.base_state.name)+'，两次间隔 ≥ '+p.cooldown_s+' 秒）</h3>';
  for(const r of Object.values(p.routines)){h+='<details><summary>'+esc(r.name)+'：'+esc(r.label)+' · '+r.steps.length+' 步 · 约 '+(r.duration_ms/1000).toFixed(1)+' 秒（展开看全部目标）</summary><table><tr><th>#</th><th>步骤</th><th>关节目标</th><th>用时</th></tr>';
   r.steps.forEach((s,i)=>{h+='<tr><td>'+(i+1)+'</td><td>'+esc(s.label)+'</td><td>'+s.joints.map(([j,a])=>j+'号→'+a+'°').join('，')+'</td><td>'+s.motion_ms+'+'+s.settle_ms+' ms</td></tr>';});h+='</table></details>';}}
 else{h+='<p>起始姿态：'+esc(p.base_state.name)+'（'+p.base_state.angles.join(', ')+'°，顺序 '+p.base_state.order.join('→')+'）· 两次间隔 ≥ '+p.cooldown_s+' 秒</p>';
  for(const r of Object.values(p.routines)){h+='<details open><summary>'+esc(r.name)+'：'+esc(r.label)+' · '+r.steps.length+' 步 · 约 '+(r.duration_ms/1000).toFixed(1)+' 秒</summary><table><tr><th>#</th><th>步骤</th><th>关节目标</th><th>用时</th></tr>';
   r.steps.forEach((s,i)=>{h+='<tr><td>'+(i+1)+'</td><td>'+esc(s.label)+'</td><td>'+s.joints.map(([j,a])=>j+'号→'+a+'°').join('，')+'</td><td>'+s.motion_ms+'+'+s.settle_ms+' ms</td></tr>';});h+='</table></details>';}}
 return h+'<p class="muted">'+esc(p.conditions)+'</p>';}
async function preview(){busy=true;clearOffer();try{offer=await post('/modes/preview',params());el('offer').innerHTML=describe(offer.plan);el('confirmText').textContent=kind==='voice_control'?'我已核对以上全部参数和现场空间，同意在本会话内由语音命令（及其开启的表情/手势识别）触发以上有限次数的动作':'我已核对以上全部参数和现场空间，同意在本会话内由电脑端识别结果触发以上有限次数的动作';el('offerPanel').hidden=false;el('offerPanel').scrollIntoView({behavior:'smooth',block:'start'});el('notice').textContent='预览有效期 120 秒；勾选确认后开启。';}catch(e){el('notice').textContent=e.message;}finally{busy=false;}}
el('blockPreview').onclick=preview;el('dancePreview').onclick=preview;el('voicePreview').onclick=preview;
el('confirm').onchange=()=>{el('arm').disabled=!offer||!el('confirm').checked;};el('cancel').onclick=clearOffer;
el('arm').onclick=async()=>{if(!offer||!el('confirm').checked)return;const token=offer.token;clearOffer();try{const r=await post('/modes/arm',{token,confirmed:true});el('notice').textContent='模式已开启：会话 '+r.session.id+'。'+({block_grasp:'确认 Mac 上 run_block_grasp.py 在运行，然后点“开始识别夹取”。',voice_control:'现在在 Mac 上运行 python -m pc.voice_control.main，按回车说命令。'}[r.session.kind]||'现在运行电脑端程序即可。');render(await api('/modes/status'));}catch(e){el('notice').textContent='未开启：'+e.message;}};
el('disarm').onclick=async()=>{try{const r=await post('/modes/disarm');el('notice').textContent=r.message;render(await api('/modes/status'));}catch(e){el('notice').textContent=e.message;}};
el('startGrasp').onclick=async()=>{const ss=status&&status.session;if(!ss||starting)return;starting=true;el('startGrasp').disabled=true;
 try{await post('/modes/grasp/start',{session_id:ss.id});el('notice').textContent='已开始本次识别夹取';render(await api('/modes/status'));}catch(e){el('notice').textContent='未开始：'+e.message;}finally{starting=false;}};
tab('block_grasp');poll();
