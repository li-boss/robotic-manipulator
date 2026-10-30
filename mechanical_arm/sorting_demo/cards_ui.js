/* 十卡片示教。记录与保存不运动；运动只由显示明确参数的姿态按钮触发。 */
let cardsState=null,cardsBuilt=false;
const cardNames=['空桌面观察','左上','上中','右上','左中','中心','右中','左下','下中','右下'],cardPhases=['above','grasp'];
const cardDirty={},cardSignatures={},cardPixels={};
function cardNode(tag,id,text){const node=document.createElement(tag);if(id)node.id=id;if(text)node.textContent=text;return node;}
function cardPhoto(id,record){const node=el(id);if(record?.frame_id&&record.has_image!==false){const src='/teaching/photos/'+record.frame_id;if(node.src!==src)node.src=src;node.hidden=false;}else{node.hidden=true;node.removeAttribute('src');}}
function cardBuildPhase(index,phase,parent){
 const title={reference:'观察姿态',above:'取物上方',grasp:'抓取姿态'}[phase],box=cardNode('div');box.className='card-phase';box.append(cardNode('h4',null,title+'：1～6号目标（含夹爪）'));
 const angles=cardNode('div');angles.className='card-angles';
 for(let joint=1;joint<=6;joint++){const label=cardNode('label',null,joint+'号'),input=cardNode('input','cardAngle'+index+'_'+phase+'_'+joint);input.type='number';input.step='1';input.oninput=()=>{cardDirty[index+'_'+phase]=true;clearApproval();};label.append(input);angles.append(label);}box.append(angles);
 const orderLabel=cardNode('label',null,'此姿态顺序（1～6号各一次）'),order=cardNode('input','cardOrder'+index+'_'+phase);order.placeholder='按现场路径填写，例如6,1,2,3,4,5';order.oninput=()=>{cardDirty[index+'_'+phase]=true;clearApproval();};orderLabel.append(order);
 const timeLabel=cardNode('label',null,'每关节用时（毫秒）'),duration=cardNode('input','cardTime'+index+'_'+phase);duration.type='number';duration.value='1500';duration.min='500';duration.max='10000';duration.oninput=order.oninput;timeLabel.append(duration);
 const save=cardNode('button','cardSave'+index+'_'+phase,'检查并保存角度和顺序');save.onclick=()=>cardSaveAngles(index,phase);
 const move=cardNode('button','cardMove'+index+'_'+phase,'确认依次到此姿态');move.onclick=()=>cardMove(index,phase);
 const record=cardNode('button','cardRecord'+index+'_'+phase,index===0?'拍空桌面并记录观察':phase==='grasp'?'到位后记录抓取角度（不拍照）':'到位后拍照记录'+title);record.onclick=()=>cardCapture(index,phase);
 box.append(orderLabel,timeLabel,save,move,cardNode('p','cardMotion'+index+'_'+phase));
 const receipts=cardNode('details');receipts.append(cardNode('summary',null,index?'可选：程序采集到位资料（九点映射不需要）':'观察基准工具（已有基准无需重拍）'),record,cardNode('p','cardReadings'+index+'_'+phase));
 if(index){const photo=cardNode('img','cardPhoto'+index+'_'+phase);photo.hidden=true;photo.alt=title+'阶段照片';photo.className='card-thumb';receipts.append(photo);}box.append(receipts);
 parent.append(box);
}
function cardsBuild(){
 for(let index=0;index<10;index++){const card=cardNode('article','positionCard'+index);card.className='position-card';card.append(cardNode('h3',null,(index?index+'．':'0．')+cardNames[index]),cardNode('p','cardProgress'+index));
  const view=cardNode('div');view.className='view';const photo=cardNode('img','cardTarget'+index);photo.hidden=true;photo.alt=cardNames[index]+'保存的定位照片';const hint=cardNode('span','cardHint'+index,index?'尚未拍木块。先回观察姿态，把木块放在'+cardNames[index]+'。':'空桌面观察照片；已有基准不用重复拍。');view.append(photo,hint);
  if(index){const canvas=cardNode('canvas','cardCanvas'+index);canvas.onclick=event=>{const target=cardsState?.slots[index]?.target;if(!target)return;const rect=canvas.getBoundingClientRect(),size=target.image_size||[640,480];cardPixels[index]=[Math.round((event.clientX-rect.left)*size[0]/rect.width),Math.round((event.clientY-rect.top)*size[1]/rect.height)];clearApproval();cardDraw(index);};view.append(canvas);}
  card.append(view,cardNode('p','cardNotice'+index));
  const editor=cardNode('details','cardEditor'+index);editor.append(cardNode('summary',null,index?'展开本位置：拍照、角度和到位按钮':'展开：记录空桌面与观察角度'));
  if(index){const capture=cardNode('button','cardCapture'+index,'1．观察姿态下拍这块木块');capture.onclick=()=>cardCapture(index,'target');const confirm=cardNode('button','cardConfirm'+index,'确认照片中标出的木块中心');confirm.onclick=()=>cardConfirm(index);editor.append(capture,confirm,cardNode('p','cardCenter'+index));for(const phase of cardPhases)cardBuildPhase(index,phase,editor);
   const legacy=cardNode('details');legacy.append(cardNode('summary',null,'导入之前拍过的资料（可选，不运动）'));
   for(const phase of ['target','above','grasp']){const label=cardNode('label',null,{target:'旧木块照片',above:'旧取物上方照片',grasp:'旧抓取照片'}[phase]),select=cardNode('select','cardLegacy'+index+'_'+phase);if(phase==='target')select.onchange=()=>cardPhoto('cardLegacyPhoto'+index,select.value?{frame_id:select.value}:null);label.append(select);legacy.append(label);}
   const legacyPhoto=cardNode('img','cardLegacyPhoto'+index);legacyPhoto.className='card-thumb';legacyPhoto.hidden=true;legacy.append(legacyPhoto);
   const importButton=cardNode('button','cardImport'+index,'把选中的旧资料保存到本位置');importButton.onclick=()=>cardImport(index);legacy.append(importButton,cardNode('p',null,'请按照片核对它们是否属于同一个位置。旧记录没有明确夹爪目标时，6号留空，不用回读数补齐。'));editor.append(legacy);
   const verify=cardNode('button','cardVerify'+index,'资料齐全后，生成原程序验证计划');verify.onclick=()=>cardPreview(index);const validation=cardNode('details');validation.append(cardNode('summary',null,'可选：原程序逐步验证'),verify);editor.append(validation);
  }else{editor.append(cardNode('p',null,'先移走黄色木块。重新记录观察基准会让已有位置需要重新采集。记录只保存，不运动。'));cardBuildPhase(index,'reference',editor);}
  card.append(editor);el(index?'cardsGrid':'cardsObservation').append(card);
 }cardsBuilt=true;
}
function cardDraw(index){const canvas=el('cardCanvas'+index),ctx=canvas.getContext('2d'),record=cardsState?.slots[index]?.target,size=record?.image_size||[640,480];canvas.width=size[0];canvas.height=size[1];ctx.clearRect(0,0,...size);const point=cardPixels[index]||record?.pixel;if(point){ctx.strokeStyle='#ffd36c';ctx.lineWidth=3;ctx.beginPath();ctx.arc(...point,7,0,Math.PI*2);ctx.stroke();el('cardCenter'+index).textContent='中心：'+point.join(', ')+' · '+(cardsState.slots[index].center_confirmed?'已保存确认':'请看照片核对后确认');}}
function cardsDisabled(){const disabled=controlPending||awaitingRecovery||controlState?.busy||!controlState||!cardsState;for(const node of document.querySelectorAll('#cardsPanel input,#cardsPanel button,#cardsPanel select'))node.disabled=disabled;
 if(!disabled)for(let index=0;index<10;index++){const slot=cardsState.slots[index];for(const phase of index?cardPhases:['reference'])el('cardMove'+index+'_'+phase).disabled=!cardPlan(index,phase);if(index){el('cardConfirm'+index).disabled=!slot.target;el('cardVerify'+index).disabled=!slot.complete;for(const phase of cardPhases)el('cardRecord'+index+'_'+phase).disabled=!slot.target||slot.stale;}}
}
function cardPlan(index,phase){const plan=cardsState?.slots[index]?.plans?.[phase];if(!plan)return null;const saved=controlState?.saved_states?.states.find(s=>s.id===plan.id);return saved&&JSON.stringify([saved.angles,saved.order,saved.motion_ms])===JSON.stringify([plan.angles,plan.order,plan.motion_ms])?saved:null;}
function cardsRender(state){cardsState=state;if(!cardsBuilt)cardsBuild();
 for(let index=0;index<10;index++){const slot=state.slots[index],target=index?slot.target:slot.reference;cardPhoto('cardTarget'+index,target);el('cardHint'+index).hidden=!!target;
  el('cardProgress'+index).textContent=index?(slot.stale?'观察基准或配置已变化，请核对资料':slot.reported?'已用于九点映射 · 验证来源：用户报告':slot.verified?'程序验证已保存':slot.target&&slot.center_confirmed&&slot.plans?.above&&slot.plans?.grasp?'已有定位照片和明确目标 · 可建立九点映射':['定位照片'+(slot.target?'✓':'待保存'),'上方目标'+(slot.plans?.above?'✓':'待保存'),'抓取目标'+(slot.plans?.grasp?'✓':'待保存')].join(' · ')):(target?'观察照片已保存':'等待记录观察基准');
  if(index&&target){if(cardSignatures['target'+index]!==target.frame_id){cardPixels[index]=slot.pixel||target.pixel;cardSignatures['target'+index]=target.frame_id;}cardDraw(index);}
  if(index)for(const phase of ['target','above','grasp']){const frames=(state.legacy_frames||[]).filter(f=>f.kind===phase),signature=JSON.stringify(frames);if(cardSignatures['legacy'+index+'_'+phase]!==signature){cardSignatures['legacy'+index+'_'+phase]=signature;const select=el('cardLegacy'+index+'_'+phase),selected=select.value;select.replaceChildren();const empty=cardNode('option',null,'不选择');empty.value='';select.append(empty);frames.forEach((f,n)=>{const option=cardNode('option',null,'旧照片 '+(n+1)+' · '+new Date(f.captured_at).toLocaleTimeString());option.value=f.id;select.append(option);});select.value=selected||'';}}
  for(const phase of index?cardPhases:['reference']){const record=slot[phase],plan=slot.plans?.[phase],signature=JSON.stringify([record?.frame_id,plan?.updated_at]);
   if(cardSignatures[index+'_'+phase]!==signature){cardSignatures[index+'_'+phase]=signature;if(!cardDirty[index+'_'+phase]){const values=plan?.angles||record?.angles||[];for(let joint=1;joint<=6;joint++)el('cardAngle'+index+'_'+phase+'_'+joint).value=values[joint-1]==null?'':String(values[joint-1]);if(plan){el('cardOrder'+index+'_'+phase).value=plan.order.join(',');el('cardTime'+index+'_'+phase).value=String(plan.motion_ms);}}}
   for(let joint=1;joint<=6;joint++){const input=el('cardAngle'+index+'_'+phase+'_'+joint),range=controlState?.joint_limits[String(joint)];if(range){input.min=range[0];input.max=range[1];input.placeholder=range.join('～')+'°';}}
   const verifiedPlan=cardPlan(index,phase);el('cardMotion'+index+'_'+phase).textContent=verifiedPlan?((controlState.mode==='real'?'实机':'模拟')+'：'+verifiedPlan.angles.join(', ')+'°；顺序 '+verifiedPlan.order.join(' → ')+'；每关节 '+verifiedPlan.motion_ms+'ms'):(plan?'姿态已被其他工具修改或删除，请重新保存角度':'保存角度和顺序后，可以一键依次到位');
   el('cardReadings'+index+'_'+phase).textContent=record?.readings?'该阶段原始读数：'+record.readings.map(r=>r.angle).join(', ')+'°（不会自动修正为目标）':'';if(index)cardPhoto('cardPhoto'+index+'_'+phase,record);
  }
 }cardsDisabled();
}
function cardFields(index,phase){return {slot:index,phase,expected_cards_sha256:cardsState.sha256,expected_sha256:cardsState.teaching_sha256};}
function cardAngles(index,phase){return [1,2,3,4,5,6].map(j=>{const text=el('cardAngle'+index+'_'+phase+'_'+j).value;return text===''?null:Number(text);});}
async function cardAction(index,fn){if(controlPending||awaitingRecovery||controlState?.busy||!cardsState)return;clearApproval();controlPending=true;controlDisabled(true);cardsDisabled();teachingDisabled();try{return await fn();}catch(error){el('cardNotice'+index).textContent='未完成：'+error.message;}finally{controlPending=false;controlDisabled(controlState?.busy);cardsDisabled();teachingDisabled();}}
function cardPost(path,fields){return autoApi(path,fields);}
function cardSaveAngles(index,phase){return cardAction(index,async()=>{const r=await cardPost('/teaching/cards/angles',{...cardFields(index,phase),angles:cardAngles(index,phase),order:autoValues(el('cardOrder'+index+'_'+phase).value),motion_ms:Number(el('cardTime'+index+'_'+phase).value)});cardDirty[index+'_'+phase]=false;renderControl(r.control);teachingStatus(r.teaching);cardsRender(r.status);el('cardNotice'+index).textContent='这组角度和顺序已保存，没有运动。';});}
function cardCapture(index,phase){return cardAction(index,async()=>{el('cardNotice'+index).textContent=phase==='grasp'?'正在只读记录角度，请稍候……':'正在拍照并记录，请稍候……';const r=await cardPost('/teaching/cards/record',{...cardFields(index,phase),angles:phase==='target'?null:cardAngles(index,phase)});if(phase!=='target')cardDirty[index+'_'+phase]=false;teachingStatus(r.teaching);cardsRender(r.status);el('cardNotice'+index).textContent=phase==='grasp'?'抓取角度与到位读数已保存，没有拍照或运动。':'本卡片照片和角度已存到设备，刷新页面仍保留；没有运动。';});}
function cardConfirm(index){return cardAction(index,async()=>{cardsRender(await cardPost('/teaching/cards/confirm',{...cardFields(index,'target'),pixel:cardPixels[index],confirmed:true}));el('cardNotice'+index).textContent='木块中心已确认保存。';});}
function cardImport(index){return cardAction(index,async()=>{const fields=cardFields(index,'target');for(const phase of ['target','above','grasp'])fields[phase+'_frame']=el('cardLegacy'+index+'_'+phase).value||null;cardsRender(await cardPost('/teaching/cards/import',fields));el('cardNotice'+index).textContent='旧照片和角度已归入此卡片，没有运动；缺少的目标请明确填写。';});}
async function cardMove(index,phase){if(controlPending||awaitingRecovery||controlState?.busy)return;const plan=cardPlan(index,phase);if(!plan)return;el('cardNotice'+index).textContent='已确认本次显示的六关节目标，正在依次调整；到位后再点记录。';await stateExecute(plan,controlState.saved_states.sha256);cardsDisabled();}
function cardPreview(index){return cardAction(index,async()=>{const r=await cardPost('/teaching/cards/preview',cardFields(index,'target'));teachingAdoptDraft(r);el('cardNotice'+index).textContent='验证计划已生成，没有运动。请展开“任务验证与执行”逐步验证后保存。';el('validationTools').open=true;});}
async function cardsPoll(){try{cardsRender(await api('/teaching/cards/status'));}catch(error){el('cardsNotice').textContent='卡片读取失败：'+error.message;}finally{setTimeout(cardsPoll,1500);}}cardsPoll();
el('cardsFit').onclick=()=>cardAction(0,async()=>{
 if(!el('cardsFitReport').checked||!el('cardsFitScene').checked)throw Error('请确认九点已实际验证，且底座、桌面与相机安装未改变');
 const r=await cardPost('/teaching/cards/fit',{expected_cards_sha256:cardsState.sha256,expected_sha256:cardsState.teaching_sha256,user_reported_verified:true,scene_confirmed:true});
 teachingStatus(r.teaching);cardsRender(r.status);el('cardsNotice').textContent=r.message;el('validationTools').open=true;
});
for(const id of ['cardsFitReport','cardsFitScene'])el(id).oninput=clearApproval;
