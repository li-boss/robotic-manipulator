/* 首页：摄像头、单关节手动调整、六关节姿态按钮、跳舞预设。
   回车/“确定”即授权该次单关节调整；姿态卡片的执行按钮即授权该次六步调整；
   跳舞卡片的“开始跳舞”即授权卡片列出的这一套动作。异常不重发。 */
const el=id=>document.getElementById(id);
async function api(path,body){const r=await fetch(path,{cache:'no-store',signal:AbortSignal.timeout(8000),...(body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{})});const data=await r.json();if(!r.ok)throw Error(data.error||'服务错误');return data;}
function requestId(){return Array.from(crypto.getRandomValues(new Uint8Array(16)),x=>x.toString(16).padStart(2,'0')).join('');}
function cell(row,text){const td=document.createElement('td');td.textContent=text??'—';row.append(td);return td;}
let controlState=null,pending=false,recovering=false,stateRevision=null,stateAnalysis=null,stateEditing=null,stateEditingStage='',stateRendered=null;
async function post(path,body={}){if(!controlState)throw Error('控制服务尚未连接');return api(path,{...body,csrf:controlState.csrf});}
function locked(){return pending||recovering||!controlState||controlState.busy;}

// ---- 摄像头
let streaming=false;
async function camera(){try{const s=await api('/status');el('cameraState').textContent=s.connected?s.resolution.join(' × '):'未连接';if(s.connected){if(!streaming){el('video').src='/stream.mjpg?t='+Date.now();streaming=true;}el('video').hidden=false;el('cameraError').hidden=true;}else{streaming=false;el('video').hidden=true;el('cameraError').hidden=false;el('cameraError').textContent=s.message;}}catch(e){el('cameraError').hidden=false;el('cameraError').textContent='摄像头连接中断';}finally{setTimeout(camera,1000);}}
el('video').onerror=()=>{streaming=false;};camera();

// ---- 单关节
for(let i=1;i<=6;i++){const tr=document.createElement('tr');cell(tr,i===6?'6 夹爪':i===5?'5 腕转':'ID '+i);cell(tr);cell(tr);const td=document.createElement('td'),range=document.createElement('small'),input=document.createElement('input');range.id='range'+i;input.id='angle'+i;input.type='number';input.step='1';input.placeholder='绝对目标';td.append(range,input);tr.append(td);const action=document.createElement('td'),button=document.createElement('button');button.textContent='确定';button.onclick=()=>directMove(i);input.onkeydown=e=>{if(e.key==='Enter'&&!e.repeat){e.preventDefault();directMove(i);}};action.append(button);tr.append(action);el('jointRows').append(tr);}
function disable(){const d=locked();for(const n of document.querySelectorAll('#jointRows button,#jointRows input,#readJoints,#motionTime,#statePanel input,#statePanel button'))n.disabled=d;el('stateSave').disabled=d||!stateAnalysis;
 for(const b of document.querySelectorAll('#danceCards button[data-routine]'))b.disabled=d||!dancePreviews[b.dataset.routine]?.ready;el('danceState').disabled=d;
 const j=controlState?.job;el('danceStop').hidden=!(controlState?.busy&&j?.parameters?.operation==='dance'&&!j.parameters.session);}
function render(state){controlState=state;el('armMode').textContent=state.mode==='real'?(state.hardware_ready?'实机 · 确定即执行':'实机 · 未就绪（hardware_ready=false）'):'模拟 · 不驱动设备';
 for(let i=1;i<=6;i++){const cells=el('jointRows').children[i-1].children,row=state.joints.find(r=>r.id===i),b=state.joint_limits[String(i)];cells[1].textContent=row?(row.read_ok?row.angle+'°':row.error):'未读取';cells[2].textContent=state.commanded[String(i)]??'—';el('range'+i).textContent=b?b.join('～')+'°':'未配置';if(b){el('angle'+i).min=b[0];el('angle'+i).max=b[1];}}
 const j=state.job;el('armJob').textContent=j?((j.status==='running'?'正在执行':j.status==='completed'?'已完成':j.status==='failed'?'执行失败':j.status==='stopped'?'已停止':'结果待核对')+'：'+(j.error||j.message||'请看现场结果')):'';
 renderStates(state);danceStates(state);disable();}
async function refresh(){render(await api('/control/status'));}
async function guarded(fn,notice){if(locked())return;pending=true;disable();try{return await fn();}catch(e){recovering=true;el(notice).textContent='请求结果待核对：'+e.message+'。不重发，正在查询状态。';}finally{try{await refresh();}catch(e){recovering=true;}pending=false;disable();}}
el('readJoints').onclick=()=>guarded(async()=>{const r=await post('/control/read');el('armAudit').textContent=JSON.stringify(r.audit,null,2);el('armNotice').textContent=r.status.mode==='real'?'读取完成，未运动':'模拟读数';},'armNotice');
async function directMove(joint){const angle=Number(el('angle'+joint).value.trim()),time=Number(el('motionTime').value.trim()),b=controlState?.joint_limits[String(joint)];
 if(!el('angle'+joint).value.trim()||!Number.isInteger(angle)||!Number.isInteger(time)||!b||angle<b[0]||angle>b[1]||time<500||time>10000){el('armNotice').textContent='请输入范围内的整数角度，时间为500～10000毫秒';return;}
 el('armNotice').textContent='正在调整 ID '+joint+' → '+angle+'°，'+time+'毫秒';
 await guarded(async()=>{const r=await post('/control/move',{request_id:requestId(),joint,angle,motion_ms:time,confirmed:true});el('armAudit').textContent=JSON.stringify(r.job.audit,null,2);el('armNotice').textContent='已接收本次调整，请看现场结果。';},'armNotice');}

// ---- 姿态按钮
for(let i=1;i<=6;i++){const label=document.createElement('label');label.textContent=i+'号'+(i===6?'（夹爪）':'')+'目标°';const input=document.createElement('input');input.id='stateAngle'+i;input.type='number';input.step='1';input.oninput=invalidate;label.append(input);el('stateAngles').append(label);}
function invalidate(){stateAnalysis=null;el('stateSave').disabled=true;}
for(const id of ['stateName','stateOrder','stateTime'])el(id).oninput=invalidate;
function numbers(text){return text.trim()?text.split(',').map(x=>x.trim()===''?null:Number(x.trim())):null;}
function record(){return {name:el('stateName').value,angles:[1,2,3,4,5,6].map(i=>el('stateAngle'+i).value===''?null:Number(el('stateAngle'+i).value)),order:numbers(el('stateOrder').value),motion_ms:el('stateTime').value===''?null:Number(el('stateTime').value),teaching_stage:stateEditingStage};}
function renderStates(state){const saved=state.saved_states;if(!saved)return;if(stateRevision!==saved.sha256){invalidate();stateRevision=saved.sha256;}
 const key=saved.sha256+'|'+state.mode;if(stateRendered===key)return;stateRendered=key;el('stateButtons').replaceChildren();
 for(const item of saved.states){const card=document.createElement('div');card.className='panel';const title=document.createElement('strong');title.textContent=item.name;const p=document.createElement('p');p.className='muted';p.textContent='1～6号：'+item.angles.join(', ')+'° · 顺序 '+item.order.join('→')+' · 每关节 '+item.motion_ms+' ms';
  const run=document.createElement('button');run.textContent=(state.mode==='real'?'实机':'模拟')+'：确认依次调整到“'+item.name+'”';run.onclick=()=>execute(item,saved.sha256);
  const edit=document.createElement('button');edit.textContent='编辑';edit.onclick=()=>{stateEditing=item.id;stateEditingStage=item.teaching_stage||'';el('stateName').value=item.name;item.angles.forEach((v,i)=>el('stateAngle'+(i+1)).value=String(v));el('stateOrder').value=item.order.join(',');el('stateTime').value=String(item.motion_ms);invalidate();el('stateNotice').textContent='正在编辑“'+item.name+'”；重新分析后保存，不会运动。';};
  const del=document.createElement('button');del.textContent='删除';del.onclick=()=>{if(confirm('删除姿态按钮“'+item.name+'”？（保留文件备份，不运动）'))guarded(async()=>{await post('/control/states/delete',{id:item.id,expected_sha256:saved.sha256});stateRendered=null;el('stateNotice').textContent='已删除，没有运动。';},'stateNotice');};
  card.append(title,p,run,edit,del);el('stateButtons').append(card);}
 if(!saved.states.length)el('stateButtons').textContent='还没有姿态按钮。';}
el('stateAnalyze').onclick=()=>guarded(async()=>{invalidate();const r=await post('/control/states/analyze',{...record(),id:stateEditing,expected_sha256:stateRevision});stateAnalysis=r;el('stateAnalysis').textContent='检查通过：'+r.steps.map(([id,a])=>'ID '+id+' → '+a+'°').join('；')+'。每关节 '+r.record.motion_ms+' ms，含停稳约 '+(r.duration_ms/1000)+' 秒。未运动；现场路径未验证。';},'stateNotice');
el('stateSave').onclick=()=>guarded(async()=>{if(!stateAnalysis)return;await post('/control/states/save',{token:stateAnalysis.token});stateAnalysis=null;stateEditing=null;stateRendered=null;el('stateNotice').textContent='已保存为按钮，没有运动。';},'stateNotice');
el('stateNew').onclick=()=>{stateEditing=null;stateEditingStage='';el('stateName').value='';invalidate();};
el('stateCopyTargets').onclick=()=>{const v=[1,2,3,4,5,6].map(i=>controlState?.commanded[String(i)]);if(v.some(x=>!Number.isInteger(x))){el('stateNotice').textContent='还没有全部六个已发送目标，请自己填写；不使用读数补齐。';return;}v.forEach((x,i)=>el('stateAngle'+(i+1)).value=String(x));invalidate();};
function execute(item,revision){el('stateNotice').textContent='正在按保存顺序调整到“'+item.name+'”，请看现场。';
 const rec={name:item.name,angles:item.angles,order:item.order,motion_ms:item.motion_ms,teaching_stage:item.teaching_stage};
 return guarded(async()=>{const r=await post('/control/states/execute',{id:item.id,record:rec,expected_sha256:revision,request_id:requestId(),confirmed:true});el('armAudit').textContent=JSON.stringify(r.job.audit,null,2);},'stateNotice');}

// ---- 跳舞预设
let dancePresets=null,dancePreviews={},danceKey=null;
function danceStates(state){const saved=state.saved_states;if(!saved)return;const key=saved.sha256+'|'+state.mode;if(danceKey===key)return;danceKey=key;
 const chosen=el('danceState').value;el('danceState').replaceChildren();
 for(const item of saved.states){const o=document.createElement('option');o.value=item.id;o.textContent=item.name+'（'+item.angles.join(', ')+'°）';el('danceState').append(o);}
 if(saved.states.some(s=>s.id===chosen))el('danceState').value=chosen;else{const dance=saved.states.find(s=>/跳舞|舞/.test(s.name));if(dance)el('danceState').value=dance.id;}
 loadDances();}
async function loadDances(){dancePreviews={};el('danceCards').replaceChildren();const id=el('danceState').value;
 if(!id){el('danceCards').textContent='先在上面保存一个姿态按钮作为跳舞起始姿态。';return;}
 try{dancePresets=dancePresets||await post('/modes/dance/presets');}catch(e){el('danceNotice').textContent='读取跳舞预设失败：'+e.message;return;}
 for(const [name,spec] of Object.entries(dancePresets.routines)){const card=document.createElement('div');card.className='panel';const title=document.createElement('strong');title.textContent=spec.label||name;
  const info=document.createElement('p');info.className='muted';info.textContent='正在生成计划并做模型碰撞检查（不运动）……';const steps=document.createElement('details'),summary=document.createElement('summary'),pre=document.createElement('pre');summary.textContent='全部关节目标与用时';steps.append(summary,pre);
  const run=document.createElement('button');run.dataset.routine=name;run.disabled=true;run.textContent=(controlState?.mode==='real'?'实机':'模拟')+'：开始跳舞“'+(spec.label||name)+'”';run.onclick=()=>startDance(name);
  card.append(title,info,steps,run);el('danceCards').append(card);
  post('/modes/dance/preview',{routine:name,state_id:id}).then(r=>{if(el('danceState').value!==id)return;const c=r.collision,rt=r.routine;const ok=!c.checked||c.ok;
    dancePreviews[name]={ready:ok,routine:rt,states_sha256:r.states_sha256,state_id:id};
    info.textContent=PATTERN_TEXT(rt)+' · 共'+rt.steps.length+'步，约'+(rt.duration_ms/1000).toFixed(1)+'秒 · '+(c.checked?(c.ok?'✓ '+c.note:'✗ '+c.error):c.note);
    pre.textContent=rt.steps.map((s,i)=>(i+1)+'. '+s.label+'：'+s.joints.map(([j,v])=>j+'号→'+v+'°').join('，')+' / '+s.motion_ms+'ms+'+s.settle_ms+'ms').join('\n');disable();})
   .catch(e=>{if(el('danceState').value===id){info.textContent='不能执行：'+e.message;dancePreviews[name]={ready:false};disable();}});}}
function PATTERN_TEXT(rt){return (dancePresets.patterns[rt.pattern]||rt.pattern)+'，幅度'+rt.amplitude_deg+'°，重复'+rt.repeats+'次，起点“'+rt.base_state.name+'”';}
el('danceState').onchange=()=>loadDances();
function startDance(name){const p=dancePreviews[name];if(!p?.ready)return;el('danceNotice').textContent='正在跳“'+p.routine.label+'”，请看现场。';
 return guarded(async()=>{const r=await post('/modes/dance/start',{routine:name,state_id:p.state_id,routine_sha256:p.routine.sha256,states_sha256:p.states_sha256,request_id:requestId(),confirmed:true});el('danceNotice').textContent='已开始：共'+r.job.total+'步，约'+(r.duration_ms/1000).toFixed(1)+'秒。';},'danceNotice');}
el('danceStop').onclick=async()=>{try{const r=await post('/modes/dance/stop');el('danceNotice').textContent=r.message;}catch(e){el('danceNotice').textContent='停止请求失败：'+e.message;}};

// ---- 轮询
async function poll(){try{const s=await api('/control/status');if(recovering&&!s.busy){recovering=false;}render(s);}catch(e){el('armNotice').textContent='控制连接中断：'+e.message+'。页面不能停止已发出的运动。';}finally{setTimeout(poll,1000);}}
poll();
