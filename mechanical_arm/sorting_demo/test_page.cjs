// 内存DOM与假HTTP响应：不打开浏览器、摄像头或机械臂。
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
class Element{
  constructor(){this.children=[];this.value='';this.textContent='';this.hidden=false;this.disabled=false;this.checked=false;this.style={};}
  append(...items){this.children.push(...items);}
  replaceChildren(...items){this.children=items;}
  removeAttribute(){}
  getBoundingClientRect(){return {left:0,top:0,width:640,height:480};}
  scrollIntoView(){}
  getContext(){return {clearRect(){},strokeRect(){},beginPath(){},arc(){},fill(){},moveTo(){},lineTo(){},stroke(){},closePath(){}};}
}
const html=fs.readFileSync(__dirname+'/index.html','utf8'),elements={};
for(const m of html.matchAll(/id="([^"]+)"/g)){assert(!elements[m[1]],'duplicate id');elements[m[1]]=new Element();}
elements.motionTime.value='1500';elements.poseName.value='gripper.open';elements.teachingKind.value='reference';elements.teachingLoad.value='empty';elements.teachingCommonOrder.value='1,2,3,4,5';elements.stateTime.value='1500';elements.stateStage.value='';
const document={getElementById(id){return elements[id];},createTextNode(text){return {textContent:text};},
  createElement(){const node=new Element();Object.defineProperty(node,'id',{set(id){elements[id]=node;}});return node;},
  querySelectorAll(selector){if(selector.includes('#cardsPanel'))return Object.entries(elements).filter(([id])=>id.startsWith('card')).map(([,v])=>v);if(selector.includes('#statePanel'))return Object.entries(elements).filter(([id])=>id.startsWith('state')).map(([,v])=>v);if(selector.includes('#teachingPanel'))return Object.entries(elements).filter(([id])=>id.startsWith('teaching')).map(([,v])=>v);return Object.values(elements).filter(x=>x===elements.readJoints||x===elements.previewPose||x===elements.poseName||x===elements.poseAngles||x===elements.motionTime||Object.entries(elements).some(([id,v])=>v===x&&id.startsWith('angle')));}};
const state={mode:'real',csrf:'test',busy:false,joints:[],commanded:{},job:null,poses:{},targets:{},gripper:{},missing:[],joint_limits:Object.fromEntries(Array.from({length:6},(_,i)=>[i+1,i===4?[90,250]:[10,170]]))};
const calls=[];
state.saved_states={sha256:'empty-states',states:[]};let analyzedState=null,captureCounter=0,captureError=false;
const autoSetup=JSON.parse(fs.readFileSync(__dirname+'/auto_setup.json','utf8'));
const autoStateFixture={setup:autoSetup,setup_sha256:'old',readiness:{ready:false,issues:[{reason:'not calibrated'}]},real:true};
const teachingSetup=JSON.parse(fs.readFileSync(__dirname+'/teaching_setup.json','utf8'));
const teachingStateFixture={setup:teachingSetup,sha256:'teach-old',routes:Object.keys(teachingSetup.orders),drafts:[],readiness:{ready:false,issues:['not taught'],cells:[]},real:true};
const teachingPlanFixture={steps:[{label:'test',joints:[[1,100]],motion_ms:1500,settle_ms:300}],pixel:[200,160],cell_id:null,duration_ms:1800};
const cardSlots=Array.from({length:10},()=>({}));let cardVersion=0;
function cardView(){return JSON.parse(JSON.stringify({sha256:'cards-'+cardVersion,teaching_sha256:teachingStateFixture.sha256,slots:cardSlots.map((s,i)=>({...s,index:i,stale:false,verified:false,complete:!!(s.target&&s.above&&s.grasp&&s.center_confirmed)}))}));}
async function fetch(path,options={}){
  const body=options.body?JSON.parse(options.body):null;if(body)calls.push({path,body});
  let value={};
  if(path==='/control/status')value=state;
  else if(path==='/auto/status')value=autoStateFixture;
  else if(path==='/teaching/status')value=teachingStateFixture;
  else if(path==='/teaching/cards/status')value=cardView();
  else if(path==='/teaching/cards/fit'){teachingStateFixture.setup.mapping_mode='reported_cards';teachingStateFixture.setup.reported_mapping={samples:Array.from({length:9},(_,i)=>({slot:i+1,pixel:[100+(i%3)*150,100+Math.floor(i/3)*120]}))};teachingStateFixture.readiness={ready:true,issues:[],cells:[{indices:[0,1,3],enabled:true}]};value={status:cardView(),teaching:teachingStateFixture,message:'九点用户报告映射已建立，没有运动；新位置为插值试抓'};}
  else if(path==='/teaching/cards/record'){cardVersion++;const item={frame_id:cardVersion.toString(16).padStart(32,'0'),image_size:[640,480],pixel:[body.slot*50+100,160],angles:body.angles,readings:[{id:5,angle:89}]};if(body.phase==='target'){cardSlots[body.slot].target=item;delete cardSlots[body.slot].above;delete cardSlots[body.slot].grasp;cardSlots[body.slot].center_confirmed=false;}else cardSlots[body.slot][body.phase]=item;value={status:cardView(),teaching:teachingStateFixture};}
  else if(path==='/teaching/cards/confirm'){cardVersion++;cardSlots[body.slot].center_confirmed=true;cardSlots[body.slot].pixel=body.pixel;value=cardView();}
  else if(path==='/teaching/cards/angles'){cardVersion++;const plan={id:'p'.repeat(32),name:'左上·取物上方',angles:body.angles,order:body.order,motion_ms:body.motion_ms,teaching_stage:body.phase};cardSlots[body.slot].plans={[body.phase]:plan};state.saved_states={sha256:'card-profile',states:[plan]};value={status:cardView(),control:state,teaching:teachingStateFixture};}
  else if(path==='/teaching/cards/preview')value={draft:{id:'card-draft',case:'sample',plan:teachingPlanFixture,empty:{next:0,status:'pending'},loaded:{next:0,status:'pending'}},audit:{},message:'no movement'};
  else if(path==='/teaching/capture'){if(captureError)throw Error('synthetic capture failure');captureCounter++;value={id:captureCounter.toString(16).padStart(32,'0'),image_size:[640,480],joints:[{id:5,angle:89}],jpeg_base64:Buffer.from('photo-'+captureCounter).toString('base64'),audit:{test:true},target:{pixel:[200,160]},scene:{message:'manual check'},status:teachingStateFixture};}
  else if(path==='/teaching/samples'){teachingStateFixture.setup.orders=body.orders;teachingStateFixture.sha256='teach-new';value=teachingStateFixture;}
  else if(path==='/teaching/preview'){
    if(body.action==='sample')value={draft:{id:'draft-one',case:'sample',plan:teachingPlanFixture,empty:{next:0,status:'pending'},loaded:{next:0,status:'pending'}},audit:{test:true},message:'no movement'};
    else value={mode:'real',token:'teach-once',parameters:body.action==='step'?{operation:'move',joint:1,angle:100,motion_ms:1500}:{operation:'teaching',plan:teachingPlanFixture},target:{pixel:[200,160]},joints:[],conditions:'test',audit:{test:true}};
  }
  else if(path==='/auto/capture')value={id:'a'.repeat(32),image_size:[640,480],joints:[{id:5,angle:89}],jpeg_base64:'AA==',audit:{test:true}};
  else if(path==='/auto/settings'||path==='/auto/anchor'){autoStateFixture.setup=body.setup;autoStateFixture.setup_sha256='new';value={status:autoStateFixture,message:'saved'};}
  else if(path==='/auto/preview')value={mode:'real',token:'auto-one',parameters:{operation:'auto',plan:{steps:[{label:'one',joints:[[1,100]],motion_ms:1500,settle_ms:300}],duration_ms:1800}},joints:[],conditions:'test',audit:{test:true},target:{xyz_mm:[0,200,30]}};
  else if(path==='/settings')value={roi:null,min_area:400,min_color_fraction:.65,colors:{},color_targets:{},vision_reviewed:false};
  else if(path==='/status')value={connected:false,message:'synthetic'};
  else if(path==='/detection')value={result:'no_target',image_width:640,image_height:480};
  else if(path==='/events')value={events:[],malformed_lines:0};
  else if(path==='/statistics')value={};
  else if(path==='/control/read')value={status:{...state,joints:[{id:5,angle:89,read_ok:true}]},audit:{test:true}};
  else if(path==='/control/states/analyze'){analyzedState={name:body.name,angles:body.angles,order:body.order,motion_ms:body.motion_ms,teaching_stage:body.teaching_stage};value={token:'state-token',record:analyzedState,steps:body.order.map(i=>[i,body.angles[i-1]]),duration_ms:10800,audit:{}};}
  else if(path==='/control/states/save'){state.saved_states={sha256:'saved-state',states:[{...analyzedState,id:'s'.repeat(32)}]};value={status:state.saved_states};}
  else if(path==='/control/states/execute'){state.job={id:body.request_id,status:'completed',parameters:{operation:'state_move',...body.record},audit:{}};value={job:state.job};}
  else if(path==='/control/move'){state.job={id:body.request_id,status:'completed',parameters:body,audit:{test:true}};state.commanded[String(body.joint)]=body.angle;state.joints=[];value={job:state.job};}
  else if(path==='/control/preview')value={mode:'real',token:'one-time',parameters:body,joints:[{id:5,angle:89,read_ok:true}],conditions:'test',audit:{test:true}};
  else if(path==='/control/execute'){state.job={id:body.token,status:'completed'};value={job:state.job};}
  else throw Error('unexpected request '+path);
  return {ok:true,json:async()=>value};
}
const context=vm.createContext({document,fetch,setTimeout(){},AbortSignal:{timeout(){}},crypto:require('node:crypto').webcrypto,console,Date,Math,JSON,Number,Promise,Error});
vm.runInContext(html.match(/<script>([\s\S]*?)<\/script>/)[1],context);

vm.runInContext(fs.readFileSync(__dirname+'/teaching_ui.js','utf8'),context);
vm.runInContext(fs.readFileSync(__dirname+'/state_ui.js','utf8'),context);
vm.runInContext(fs.readFileSync(__dirname+'/cards_ui.js','utf8'),context);
const tick=()=>new Promise(resolve=>setImmediate(resolve));
(async()=>{
  await tick();assert.equal(calls.length,0,'page load must not perform hardware POST');
  await elements.readJoints.onclick();assert.equal(elements.jointRows.children[4].children[1].textContent,'89°');
  assert.equal(elements.angle5.value,'','readback must not fill target');
  const before=calls.length;elements.angle1.value='100';elements.angle1.oninput();assert.equal(calls.length,before,'input must not move');
  const moveButton=elements.jointRows.children[0].children[4].children[0];
  const first=moveButton.onclick();const repeated=moveButton.onclick();await Promise.all([first,repeated]);
  assert.equal(calls.filter(x=>x.path==='/control/move').length,1,'overlapping double click sends once');
  assert.equal(calls.filter(x=>x.path==='/control/preview').length,0,'manual move does not preview');
  assert.equal(calls.filter(x=>x.path==='/control/read').length,1,'move does not read');
  assert.equal(elements.approval.hidden,true);assert.equal(calls.at(-1).body.confirmed,true);
  elements.angle5.value='89';await elements.jointRows.children[4].children[4].children[0].onclick();
  assert.equal(calls.filter(x=>x.path==='/control/move').length,1,'illegal 5 target rejected');
  elements.angle5.value='90';await elements.angle5.onkeydown({key:'Enter',repeat:false,preventDefault(){}});
  assert.equal(calls.filter(x=>x.path==='/control/move').length,2,'Enter executes directly');
  await elements.angle5.onkeydown({key:'Enter',repeat:true,preventDefault(){}});
  assert.equal(calls.filter(x=>x.path==='/control/move').length,2,'held key must not repeat');
  elements.teachingAngles.value='keep';elements.teachingUseTargets.onclick();assert.equal(elements.teachingAngles.value,'keep','partial sent targets do not fill pose');
  elements.poseAngles.value='80';await elements.previewPose.onclick();
  assert.equal(calls.at(-1).body.operation,'save');assert.deepEqual(calls.at(-1).body.angles,[80]);
  assert.equal(elements.executeOnce.disabled,true,'saving still previews data');
  elements.motionTime.oninput();assert.equal(elements.approval.hidden,true,'parameter change invalidates approval');
  assert(!html.includes('id="autoPanel"')&&!html.includes('id="stats"')&&!html.includes('id="colors"'),'remove unused panels');
  const teachingBefore=calls.length;elements.teachingAngles.value='90,50,40,40,90';elements.teachingAngles.oninput();elements.teachingCandidateOrders.onclick();assert.equal(calls.length,teachingBefore,'teaching inputs and candidate order must not POST');
  assert.equal(elements.teachingOrder0.value,'1,2,3,4,5');
  elements.teachingCommonOrder.value='2,1,3,4,5';elements.teachingCandidateOrders.onclick();assert.equal(elements.teachingOrder0.value,'2,1,3,4,5');
  elements.teachingCommonOrder.value='1,1,3,4,5';elements.teachingCandidateOrders.onclick();assert.equal(elements.teachingOrder0.value,'2,1,3,4,5','invalid order must not overwrite');
  elements.teachingCommonOrder.value='1,2,3,4,5';elements.teachingCandidateOrders.onclick();
  await elements.teachingCapture.onclick();assert.equal(elements.teachingFrozen.hidden,false,'reference photo must be visible');assert.equal(elements.teachingImageHint.hidden,true);assert.equal(elements.teachingCenterConfirmed.checked,false,'empty reference is not a target');
  await elements.teachingSaveOrders.onclick();assert.equal(calls.at(-1).path,'/teaching/samples');assert.deepEqual(Object.values(calls.at(-1).body.orders)[0],[1,2,3,4,5]);
  elements.teachingKind.value='target';await elements.teachingCapture.onclick();assert.equal(elements.teachingCenterConfirmed.checked,false);assert.equal(elements.teachingAngles.value,'90,50,40,40,90','raw89 must not fill targets');
  const noCenterCount=calls.length;await elements.teachingSampleDraft.onclick();assert.equal(calls.length,noCenterCount,'sample draft requires center confirmation');
  const targetPhoto=elements.teachingFrozen.src,targetLatest=elements.teachingLatest.src;elements.teachingKind.value='above';await elements.teachingCapture.onclick();assert.equal(elements.teachingFrozen.src,targetPhoto,'above pose keeps the original target photo');assert.notEqual(elements.teachingLatest.src,targetLatest,'latest stage photo must update');const aboveLatest=elements.teachingLatest.src;elements.teachingKind.value='grasp';await elements.teachingCapture.onclick();assert.equal(elements.teachingFrozen.src,targetPhoto,'grasp pose keeps the original target photo');assert.notEqual(elements.teachingLatest.src,aboveLatest,'grasp stage photo must update');elements.teachingCenterConfirmed.checked=true;
  await elements.teachingSampleDraft.onclick();assert.equal(calls.at(-1).body.action,'sample');assert.deepEqual(calls.at(-1).body.pixel,[200,160]);
  const beforeStep=calls.filter(x=>x.path==='/control/execute').length;elements.teachingSceneConfirmed.checked=true;elements.teachingEmptyConfirmed.checked=true;
  await elements.teachingSteps.children[0].children[2].children[0].onclick();assert.equal(elements.executeOnce.disabled,true);assert.equal(calls.filter(x=>x.path==='/control/execute').length,beforeStep);
  elements.approveCheck.checked=true;elements.approveCheck.onchange();await elements.executeOnce.onclick();await elements.executeOnce.onclick();assert.equal(calls.filter(x=>x.path==='/control/execute').length,beforeStep+1);
  await elements.teachingAutoPreview.onclick();assert.equal(elements.executeOnce.disabled,true);assert.equal(calls.at(-1).body.action,'auto');
  elements.teachingAngles.oninput();assert.equal(elements.approval.hidden,true,'teaching edits invalidate approval');
  const statesBefore=calls.length;elements.stateName.value='观察';elements.stateName.oninput();elements.stateOrder.value='6,2,3,4,1,5';elements.stateStage.value='reference';[90,50,40,40,90,80].forEach((v,i)=>elements['stateAngle'+(i+1)].value=String(v));assert.equal(calls.length,statesBefore,'editing state must not move');
  await elements.stateAnalyze.onclick();assert.equal(calls.at(-1).path,'/control/states/analyze');assert.equal(elements.stateSave.disabled,false);
  elements.stateName.oninput();assert.equal(elements.stateSave.disabled,true,'editing invalidates analysis');
  await elements.stateAnalyze.onclick();await elements.stateSave.onclick();assert.equal(calls.at(-1).path,'/control/states/save');
  const stateButton=elements.stateButtons.children[0].children[2];const execution=stateButton.onclick();const repeat=stateButton.onclick();await Promise.all([execution,repeat]);
  assert.equal(calls.filter(x=>x.path==='/control/states/execute').length,1,'state double click executes once');
  assert.equal(calls.at(-1).body.confirmed,true);assert.deepEqual(calls.at(-1).body.record.order,[6,2,3,4,1,5]);
  assert.equal(elements.teachingAngles.value,'90, 50, 40, 40, 90');assert.equal(elements.teachingKind.value,'reference');
  assert.equal(calls.filter(x=>x.path==='/teaching/capture').length,4,'executing state must not automatically capture');
  const lastPhoto=elements.teachingLatest.src;captureError=true;await elements.teachingCapture.onclick();assert.equal(elements.teachingLatest.src,lastPhoto,'failure must not pretend to refresh');assert(elements.teachingCaptureStatus.textContent.includes('记录失败'),'show failure next to capture button');
  assert.equal(elements.cardsGrid.children.length,9);assert.equal(elements.cardsObservation.children.length,1,'exactly ten position cards');
  const beforeCard=calls.length;elements.cardAngle1_above_1.value='90';elements.cardAngle1_above_1.oninput();assert.equal(calls.length,beforeCard,'editing card must not move');
  await elements.cardCapture1.onclick();const firstPhoto=elements.cardTarget1.src;await elements.cardCapture2.onclick();assert.equal(elements.cardTarget1.src,firstPhoto,'second card must not replace first photo');
  assert.equal(elements.cardVerify1.disabled,true,'incomplete card cannot plan');await elements.cardConfirm1.onclick();
  [90,55,40,40,90,80].forEach((v,i)=>elements['cardAngle1_above_'+(i+1)].value=String(v));elements.cardOrder1_above.value='6,2,3,4,1,5';
  const writesBefore=calls.filter(x=>x.path==='/control/states/execute').length;await elements.cardSave1_above.onclick();assert.equal(calls.filter(x=>x.path==='/control/states/execute').length,writesBefore,'card angle save must not move');
  const a=elements.cardMove1_above.onclick(),b=elements.cardMove1_above.onclick();await Promise.all([a,b]);assert.equal(calls.filter(x=>x.path==='/control/states/execute').length,writesBefore+1,'card movement double click executes once');
  await elements.cardRecord1_above.onclick();[90,45,40,40,90,80].forEach((v,i)=>elements['cardAngle1_grasp_'+(i+1)].value=String(v));await elements.cardRecord1_grasp.onclick();assert.equal(elements.cardTarget1.src,firstPhoto,'stage photos keep target photo');assert.equal(elements.cardVerify1.disabled,false);
  await elements.cardVerify1.onclick();assert.equal(calls.at(-1).path,'/teaching/cards/preview');assert.equal(elements.validationTools.open,true);
  context.restoredCards=cardView();vm.runInContext('cardsRender(restoredCards)',context);assert.equal(elements.cardTarget1.src,firstPhoto,'saved cards restore after reload');assert.equal(elements.cardAngle1_grasp_2.value,'45');
  vm.runInContext("teachingState.setup.task_mode='pickup_only';teachingStatus(teachingState)",context);
  assert.equal(elements.teachingCommonTools.hidden,true,'pickup needs no public placement settings');
  assert(elements.teachingAutoPreview.textContent.includes('生成抓取计划（不运动）'));
  assert(elements.cardRecord1_grasp.textContent.includes('不拍照'));
  assert(elements.teachingResultText.textContent.includes('保持抓取姿态'));
  const beforeFit=calls.length,movesBeforeFit=calls.filter(x=>x.path==='/control/execute'||x.path==='/control/states/execute').length;
  await elements.cardsFit.onclick();assert.equal(calls.length,beforeFit,'reported fit requires operator and installation confirmation');
  elements.cardsFitReport.checked=true;elements.cardsFitScene.checked=true;
  const once=elements.cardsFit.onclick(),twice=elements.cardsFit.onclick();await Promise.all([once,twice]);
  assert.equal(calls.filter(x=>x.path==='/teaching/cards/fit').length,1,'reported fit double click submits once');
  assert.equal(calls.filter(x=>x.path==='/control/execute'||x.path==='/control/states/execute').length,movesBeforeFit,'fit cannot move');
  assert.equal(calls.at(-1).body.user_reported_verified,true);assert.equal(calls.at(-1).body.scene_confirmed,true);
  assert(elements.teachingReady.textContent.includes('用户报告'));assert(elements.teachingReady.textContent.includes('插值试抓'));
  assert.equal(elements.teachingAutoPreview.disabled,false,'reported nine cards enable preview without five independent validations');
  assert.equal(elements.validationTools.open,true);
  assert.equal(teachingStateFixture.setup.samples.length,0,'coverage drawing uses reported samples when legacy samples are empty');
  const beforeNewPreview=calls.filter(x=>x.path==='/control/execute').length;
  await elements.teachingAutoPreview.onclick();
  assert.equal(elements.approval.hidden,false,'reported mode with coverage triangles must display approval');
  assert.equal(calls.filter(x=>x.path==='/control/execute').length,beforeNewPreview,'recognition and preview must not execute');
  assert.equal(elements.executeOnce.textContent,'确认执行抓取（机械臂运动）');
  assert(elements.approvalText.textContent.includes('ID 1 → 100°'));
  assert(elements.approvalText.textContent.includes('运动 1500 ms，停稳 300 ms'));
  assert(elements.teachingTarget.textContent.includes('200, 160'));
  elements.cancelApproval.onclick();await elements.executeOnce.onclick();
  assert.equal(calls.filter(x=>x.path==='/control/execute').length,beforeNewPreview,'cancelled preview cannot execute');
  context.simulationPreview={mode:'sim',token:'simulated',parameters:{operation:'teaching',plan:teachingPlanFixture},read_warnings:[{joint:1,error:'intermittent I2C'}],conditions:'fake only',audit:{}};
  vm.runInContext('teachingApproval(simulationPreview)',context);
  assert.equal(elements.executeOnce.textContent,'确认模拟执行（不驱动设备）');
  assert(elements.approvalText.textContent.includes('ID 1 读取失败：intermittent I2C；按人工确认继续，不重读'));
  assert.equal(elements.executeOnce.disabled,true,'a read warning must not authorize execution');
  context.simulationPreview.parameters.target_policy='confirmed_preview';
  vm.runInContext('teachingApproval(simulationPreview)',context);
  assert(elements.approveText.textContent.includes('本次识别画面的方块中心正确、方块位置未移动'));
  elements.cancelApproval.onclick();assert(!elements.approveText.textContent.includes('方块中心'),'generic confirmation restored on cancellation');
  console.log('Page flow passed: manual, states, ten cards and pickup-only mode without grasp photos; fake HTTP only.');
})().catch(e=>{console.error(e);process.exitCode=1;});
