/* 六关节姿态按钮；输入、分析、保存不运动，点击明确参数的执行按钮授权一次。 */
let stateRevision=null,stateAnalysis=null,stateEditing=null,stateRendered=null;
const stateStageNames={'':'仅作为姿态按钮',reference:'空桌面观察基准',target:'观察姿态下的木块',transfer:'转移姿态',place_above:'固定放置上方',place:'固定放置姿态',above:'本样本取物上方',grasp:'本样本抓取姿态'};
function stateInvalidate(){stateAnalysis=null;el('stateSave').disabled=true;clearApproval();}
for(let i=1;i<=6;i++){const label=document.createElement('label');label.textContent=i+'号'+(i===6?'（夹爪）':i===5?'（相机）':'')+'目标°';const input=document.createElement('input');input.id='stateAngle'+i;input.type='number';input.step='1';input.placeholder='填写整数目标';input.oninput=stateInvalidate;label.append(input);el('stateAngles').append(label);}
function stateRecord(){return {name:el('stateName').value,angles:[1,2,3,4,5,6].map(i=>el('stateAngle'+i).value===''?null:Number(el('stateAngle'+i).value)),order:autoValues(el('stateOrder').value),motion_ms:el('stateTime').value===''?null:Number(el('stateTime').value),teaching_stage:el('stateStage').value};}
function stateDisabled(){const disabled=controlPending||awaitingRecovery||controlState?.busy||!controlState;for(const node of document.querySelectorAll('#statePanel input,#statePanel select,#statePanel button'))node.disabled=disabled;el('stateSave').disabled=disabled||!stateAnalysis;}
function savedStatesRender(state){
 const saved=state.saved_states;if(!saved)return;
 if(stateRevision!==saved.sha256){stateInvalidate();stateRevision=saved.sha256;}
 for(let i=1;i<=6;i++){const bounds=state.joint_limits[String(i)],input=el('stateAngle'+i);if(bounds){input.min=bounds[0];input.max=bounds[1];input.placeholder=bounds.join('～')+'°';}}
 const key=saved.sha256+'|'+state.mode;
 if(stateRendered!==key){stateRendered=key;el('stateButtons').replaceChildren();
  for(const item of saved.states){const card=document.createElement('div');card.className='panel';const title=document.createElement('strong');title.textContent=item.name;
   const description=document.createElement('p');description.textContent='1～6号目标：'+item.angles.join(', ')+'° · 顺序：'+item.order.join(' → ')+' · 每关节'+item.motion_ms+'毫秒 · '+stateStageNames[item.teaching_stage];
   const execute=document.createElement('button');execute.textContent=(state.mode==='real'?'实机':'模拟')+'：确认依次调整到“'+item.name+'”';execute.onclick=()=>stateExecute(item,saved.sha256);
   const edit=document.createElement('button');edit.textContent='编辑';edit.onclick=()=>{stateEditing=item.id;el('stateName').value=item.name;item.angles.forEach((v,i)=>el('stateAngle'+(i+1)).value=String(v));el('stateOrder').value=item.order.join(',');el('stateTime').value=String(item.motion_ms);el('stateStage').value=item.teaching_stage;stateInvalidate();el('stateNotice').textContent='正在编辑“'+item.name+'”；重新分析后保存，不会运动。';};
   const fill=document.createElement('button');fill.textContent='填入标定记录（不运动）';fill.onclick=()=>stateFillTeaching(item);
   const remove=document.createElement('button');remove.textContent='删除';remove.onclick=()=>stateAction(async()=>{const r=await controlPost('/control/states/delete',{id:item.id,expected_sha256:saved.sha256});stateRendered=null;savedStatesRender({...controlState,saved_states:r.status});el('stateNotice').textContent='按钮已删除，保留原文件备份，没有运动。';});
   card.append(title,description,execute,edit,fill,remove);el('stateButtons').append(card);
  }
  if(!saved.states.length)el('stateButtons').textContent='还没有姿态按钮。先填写六个目标，分析后保存。';
 }
 stateDisabled();
}
function stateFillTeaching(item){el('teachingAngles').value=item.angles.slice(0,5).join(', ');if(item.teaching_stage)el('teachingKind').value=item.teaching_stage;teachingInvalidate();el('teachingNotice').textContent='已填入“'+item.name+'”的标定目标。到位并停稳后再点拍照；填写本身不运动、不保存标定。';}
async function stateAction(fn){if(controlPending||awaitingRecovery||controlState?.busy||!controlState)return;controlPending=true;controlDisabled(true);stateDisabled();teachingDisabled();try{return await fn();}catch(e){el('stateNotice').textContent=e.message;}finally{controlPending=false;controlDisabled(controlState?.busy);stateDisabled();teachingDisabled();}}
el('stateAnalyze').onclick=()=>stateAction(async()=>{stateInvalidate();const r=await controlPost('/control/states/analyze',{...stateRecord(),id:stateEditing,expected_sha256:stateRevision});stateAnalysis=r;el('stateAnalysis').textContent='检查通过：'+r.steps.map(([id,angle])=>'ID '+id+' → '+angle+'°').join('；')+'。每关节'+r.record.motion_ms+'毫秒，含停稳共约'+(r.duration_ms/1000)+'秒。未运动；尚未验证现场路径。';el('armAudit').textContent=JSON.stringify(r.audit,null,2);});
el('stateSave').onclick=()=>stateAction(async()=>{if(!stateAnalysis)return;const r=await controlPost('/control/states/save',{token:stateAnalysis.token});stateAnalysis=null;stateEditing=null;stateRendered=null;savedStatesRender({...controlState,saved_states:r.status});el('stateNotice').textContent='姿态按钮已保存，没有运动；点击该按钮才会依次执行。';});
el('stateNew').onclick=()=>{stateEditing=null;el('stateName').value='';stateInvalidate();el('stateNotice').textContent='填写新名字，保存一个新的姿态按钮。';};
el('stateCopyTargets').onclick=()=>{const values=[1,2,3,4,5,6].map(i=>controlState?.commanded[String(i)]);if(values.some(v=>!Number.isInteger(v))){el('stateNotice').textContent='还没有全部六个已发送目标，请自己填写；不使用原始读数补齐。';return;}values.forEach((v,i)=>el('stateAngle'+(i+1)).value=String(v));stateInvalidate();};
for(const id of ['stateName','stateOrder','stateTime','stateStage'])el(id).oninput=stateInvalidate;
async function stateExecute(item,revision){
 if(controlPending||awaitingRecovery||controlState?.busy||!controlState)return;
 clearApproval();controlPending=true;controlDisabled(true);stateDisabled();teachingDisabled();
 const record={name:item.name,angles:item.angles,order:item.order,motion_ms:item.motion_ms,teaching_stage:item.teaching_stage};
 const request_id=Array.from(crypto.getRandomValues(new Uint8Array(16)),x=>x.toString(16).padStart(2,'0')).join('');
 el('stateNotice').textContent='正在按保存顺序调整到“'+item.name+'”，请看现场。';
 try{const r=await controlPost('/control/states/execute',{id:item.id,record,expected_sha256:revision,request_id,confirmed:true});el('armAudit').textContent=JSON.stringify(r.job.audit,null,2);stateFillTeaching(item);}
 catch(e){awaitingRecovery=true;el('stateNotice').textContent='执行结果待核对：'+e.message+'。不重发，正在查询状态。';}
 finally{try{renderControl(await api('/control/status'));if(!awaitingRecovery)controlPending=false;}catch(e){awaitingRecovery=true;el('stateNotice').textContent+=' 状态不可用，暂时禁用。';}controlDisabled(controlPending||controlState?.busy);stateDisabled();teachingDisabled();}
}
if(controlState)savedStatesRender(controlState);
