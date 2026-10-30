// Fake DOM/HTTP only. This test never connects to a camera or the arm.
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),{webcrypto}=require('node:crypto');
const elements={};
class Element{
 constructor(tag=''){this.tag=tag;this.children=[];this._value='';this.textContent='';this.hidden=false;this.disabled=false;this.checked=false;this.style={};}
 get value(){return this._value;}set value(v){this._value=String(v);}
 append(...nodes){this.children.push(...nodes);if(this.tag==='select'&&!this.value&&nodes[0])this.value=nodes[0].value;}
 replaceChildren(...nodes){this.children=[];this._value='';this.append(...nodes);}
 click(){}getBoundingClientRect(){return{left:10,top:20,width:320,height:240};}
 getContext(){return{clearRect(){},drawImage(){},beginPath(){},arc(){},fill(){},fillText(){}};}
}
const html=fs.readFileSync(__dirname+'/calibration.html','utf8');
for(const m of html.matchAll(/<([a-z0-9]+)[^>]*\bid="([^"]+)"/g)){assert(!elements[m[2]],'duplicate id '+m[2]);elements[m[2]]=new Element(m[1]);}
const document={getElementById(id){assert(elements[id],'missing DOM '+id);return elements[id];},createElement:tag=>new Element(tag),querySelectorAll:selector=>Object.values(elements).filter(e=>selector==='button'&&e.tag==='button')};
elements.cKind.value='intrinsics';elements.cPointKind.value='table';
for(let i=1;i<=6;i++)elements['cMoveTime'+i].value='1500';
let state={revision:0,real:true,capability:{available:true,message:'fake ready'},sessions:[],solutions:[],adopted:{},has_cad:true,setup_sha256:'setup1'};
const calls=[],timers=[];
const windowEvents={};
const control={csrf:'fake-csrf',busy:false,mode:'real',joint_limits:Object.fromEntries([1,2,3,4,5,6].map(i=>[String(i),i===5?[90,270]:[10,170]])),joints:[],commanded:{},job:null};
let allowManualMove=false,moveBehaviour='normal',sessionError=null;
function clone(x){return JSON.parse(JSON.stringify(x));}
async function fetch(path,options={}){
 const body=options.body?JSON.parse(options.body):undefined;
 if(body){assert.equal(body.csrf,'fake-csrf');if(path.startsWith('/calibration/'))assert.equal(body.revision,state.revision);calls.push({path,body});}
 assert(!/execute|torque|learning|\/auto\//.test(path),'calibration must not call batch/torque/automatic motion');
 if(path==='/control/move')assert(allowManualMove,'only explicit manual row confirmation can move');
 let value;
 if(path==='/control/status')value=control;
 else if(path==='/control/read'){control.joints=[1,2,3,4,5,6].map(i=>({id:i,angle:i===5?89:90,read_ok:true}));value={status:control};}
 else if(path==='/control/move'){
  assert.equal(body.confirmed,true);assert(/^[a-f0-9]{32}$/.test(body.request_id));
  if(moveBehaviour==='notDelivered')throw Error('synthetic request not delivered');
  control.busy=true;control.job={id:body.request_id,status:'running',parameters:{operation:'direct_move',joint:body.joint,angle:body.angle,motion_ms:body.motion_ms}};
  if(moveBehaviour==='lostResponse')throw Error('synthetic lost response');
  value={job:control.job};
 }
 else if(path==='/calibration/status')value=state;
 else if(path==='/calibration/session'){if(sessionError)return{ok:false,status:400,json:async()=>({error:sessionError})};assert(body.kind!=='position'||state.adopted.hand_eye,'backend requires adopted hand-eye');const s={id:'a'.repeat(31)+(state.sessions.length+1),kind:body.kind,real:true,samples:[],point_checks:[]};state.sessions.push(s);state.revision++;value={status:state,session_id:s.id};}
 else if(path==='/calibration/capture'){state.revision++;value={status:state,image_url:'/calibration/frame/fake.jpg',capture:{id:'f'.repeat(32),session_id:body.session_id,image_size:[640,480],joints:null,detection:{acceptable:true,pixels:[[100,100]],ids:[0],marker_count:27,corner_count:40}}};}
 else if(path==='/calibration/add'){state.sessions.find(s=>s.id===body.session_id).samples.push({capture_id:body.capture_id,role:body.role,corners:40});state.revision++;value=state;}
 else if(path==='/calibration/solve'){const result={id:'b'.repeat(32),session_id:body.session_id,kind:'intrinsics',passed:true,real:true};state.solutions.push(result);state.revision++;value={status:state,result};}
 else if(path==='/calibration/apply'){assert.equal(body.confirmed,true);const result=state.solutions.find(s=>s.id===body.solution_id);if(result.kind==='hand_eye'&&body.board_on_table)return{ok:false,status:400,json:async()=>({error:'求得板面并不接近水平，不能据此更新桌面高度'})};state.adopted[result.kind==='hand_eye'?'hand_eye':'intrinsics']=body.solution_id;state.revision++;value=state;}
 else if(path==='/calibration/export')value={data:state};
 else if(path==='/calibration/check'){assert.deepEqual(body.pixel,[200,200]);state.revision++;const check={capture_id:body.capture_id,kind:body.kind,error_mm:.5,actual_xyz_mm:body.actual_xyz_mm,predicted_xyz_mm:body.actual_xyz_mm,record_sha256:'d'.repeat(64)};state.sessions.find(s=>s.id===body.session_id).point_checks.push(check);value={status:state,check,report:{passed:false,reasons:['need more points']}};}
 else if(path==='/calibration/exclude-point'){const s=state.sessions.find(s=>s.id===body.session_id),i=s.point_checks.findIndex(r=>r.record_sha256===body.record_sha256);assert(i>=0);const record=s.point_checks.splice(i,1)[0];(s.excluded_point_checks||=[]).push({record,reason:body.reason});state.revision++;value=state;}
 else if(path==='/calibration/adopt-position'){assert.equal(body.confirmed,true);const s=state.sessions.find(s=>s.id===body.session_id);s.position_limit_mm=body.limit_mm;const errors=s.point_checks.map(r=>r.error_mm);s.report={passed:false,table_passed:true,top_passed:false,reasons:['顶面未检查'],errors_mm:errors,limit_mm:body.limit_mm};state.position_result={valid:true,session_id:s.id,scope:body.scope,point_count:s.point_checks.length,limit_mm:body.limit_mm,max_error_mm:Math.max(...errors)};state.revision++;value=state;}
 else throw Error('unexpected path '+path);
 return{ok:true,json:async()=>clone(value)};
}
class FakeImage{set src(v){queueMicrotask(()=>this.onload());}}
const context=vm.createContext({document,fetch,Image:FakeImage,AbortSignal,Blob,URL,crypto:webcrypto,window:{addEventListener(name,fn){windowEvents[name]=fn;}},setTimeout(fn){timers.push(fn);},queueMicrotask,console});
vm.runInContext(fs.readFileSync(__dirname+'/calibration_ui.js','utf8'),context);
const flush=()=>new Promise(r=>setImmediate(r));
(async()=>{
 await flush();await flush();assert(elements.cConnection.textContent.includes('实机'));
 assert.equal(elements.cCamera.src,undefined,'live camera must not block first document load');
 windowEvents.load();assert(elements.cCamera.src.startsWith('/calibration/live.jpg?'));
 elements.cRulerX.value='100';elements.cRulerY.value='100';elements.cBoardReady.checked=true;
 await elements.cNew.onclick();assert.equal(elements.cSession.value,state.sessions[0].id);
 await elements.cCapture.onclick();assert(elements.cQuality.textContent.includes('40'));
 assert.equal(calls.find(c=>c.path==='/calibration/capture').body.session_id,state.sessions[0].id);
 await elements.cFit.onclick();assert(elements.cCounts.textContent.includes('拟合组 1'));
 await elements.cSolve.onclick();assert.equal(elements.cApply.disabled,true,'applying needs explicit confirmation');
 elements.cApplyConfirmed.checked=true;elements.cApplyConfirmed.onchange();assert.equal(elements.cApply.disabled,false);
 await elements.cApply.onclick();assert.equal(state.adopted.intrinsics,'b'.repeat(32));
 elements.cKind.value='position';elements.cKind.onchange();assert.equal(elements.cSampleActions.hidden,true);
 assert.equal(elements.cNew.disabled,true);assert.equal(elements.cGoHandEye.hidden,false);assert(elements.cCreateHelp.textContent.includes('尚未采用'));
 const sessionsBefore=state.sessions.length,appliesBefore=calls.filter(c=>c.path==='/calibration/apply').length;
 await elements.cNew.onclick();assert.equal(state.sessions.length,sessionsBefore);assert(elements.cCreateStatus.textContent.includes('尚未采用'));
 elements.cGoHandEye.onclick();assert.equal(elements.cKind.value,'hand_eye');assert.equal(calls.filter(c=>c.path==='/calibration/apply').length,appliesBefore,'navigation does not adopt');
 await elements.cNew.onclick();const handSession=state.sessions.at(-1);
 state.solutions.push({id:'c'.repeat(32),session_id:handSession.id,kind:'hand_eye',passed:true,real:true,camera_mount:{xyz_mm:[0,0,0]},consistency_limits:{translation_mm:10,rotation_deg:2},base_to_board_mm:[[1,0,0,0],[0,Math.cos(.05),-Math.sin(.05),200],[0,Math.sin(.05),Math.cos(.05),-40],[0,0,0,1]]});
 await elements.cRefresh.onclick();assert.equal(elements.cApply.disabled,true);
 assert.equal(elements.cOnTable.checked,false);assert(elements.cTableDiagnostic.textContent.includes('6.00毫米'));
 elements.cOnTable.checked=true;elements.cOnTable.onchange();elements.cThickness.value='5';elements.cThickness.oninput();
 elements.cApplyConfirmed.checked=true;elements.cApplyConfirmed.onchange();await elements.cApply.onclick();
 assert.equal(state.adopted.hand_eye,undefined);assert(elements.cApplyStatus.textContent.includes('不代表实物板没有放平'));
 const appliesAtFailure=calls.filter(c=>c.path==='/calibration/apply').length;
 elements.cKeepTable.onclick();assert.equal(elements.cOnTable.checked,false);assert.equal(elements.cApplyConfirmed.checked,false);assert.equal(calls.filter(c=>c.path==='/calibration/apply').length,appliesAtFailure,'cancel optional height does not adopt');
 elements.cApplyConfirmed.checked=true;elements.cApplyConfirmed.onchange();await elements.cApply.onclick();assert.equal(state.adopted.hand_eye,'c'.repeat(32));
 assert.equal(calls.filter(c=>c.path==='/calibration/apply').at(-1).body.board_on_table,false);
 assert(elements.cApplyStatus.textContent.includes('本次未按板面更新桌面高度'));
 elements.cKind.value='position';elements.cKind.onchange();assert.equal(elements.cNew.disabled,false);assert.equal(elements.cGoHandEye.hidden,true);
 sessionError='标定证据与当前参数不符';await elements.cNew.onclick();assert(elements.cCreateStatus.textContent.includes('创建被拒绝'));assert(elements.cCreateStatus.textContent.includes(sessionError));assert.equal(elements.cNew.disabled,false);
 sessionError=null;const positionRequests=calls.filter(c=>c.path==='/calibration/session').length;
 await Promise.all([elements.cNew.onclick(),elements.cNew.onclick()]);assert.equal(calls.filter(c=>c.path==='/calibration/session').length,positionRequests+1,'double click creates once');assert(elements.cCreateStatus.textContent.includes('已创建批次'));
 assert.equal(elements.cSetPixel.disabled,true);assert.equal(elements.cPixel.disabled,true);
 await elements.cCamera.onclick();assert.equal(elements.cImage.hidden,false);assert.equal(elements.cCamera.hidden,true);
 assert.equal(elements.cPixel.value,'','clicking live image only freezes, never records a stale pixel');
 assert(elements.cPickStatus.textContent.includes('已冻结'));
 elements.cImage.onpointermove({clientX:110,clientY:120});assert(elements.cPointer.textContent.includes('u=200.00'));
 elements.cImage.onpointerdown({clientX:9,clientY:120});assert.equal(elements.cPixel.value,'');
 elements.cImage.onpointerdown({clientX:110,clientY:120});
 assert.equal(elements.cPixel.value,'200.00, 200.00');assert(elements.cPickStatus.textContent.includes('已选原图像素'));
 elements.cMeasured.checked=true;elements.cMeasured.onchange();
 elements.cPixel.value='640,200';elements.cPixel.oninput();assert.equal(elements.cMeasured.checked,false);assert.equal(elements.cRecordPoint.disabled,true);
 elements.cSetPixel.onclick();assert(elements.cNotice.textContent.includes('0≤u<640'));assert.equal(elements.cRecordPoint.disabled,true);
 elements.cPixel.value='200，200';elements.cPixel.oninput();elements.cSetPixel.onclick();assert.equal(elements.cPixel.value,'200.00, 200.00');
 elements.cMeasured.checked=true;elements.cActual.oninput();assert.equal(elements.cMeasured.checked,false);
 elements.cActual.value='10,20,0';elements.cActual.oninput();assert.equal(elements.cRecordPoint.disabled,true);assert(elements.cPointSaveHelp.textContent.includes('最后勾选'));
 elements.cMeasured.checked=true;elements.cMeasured.onchange();assert.equal(elements.cRecordPoint.disabled,false);
 await elements.cRecordPoint.onclick();assert(elements.cNotice.textContent.includes('0.50'));
 assert(elements.cPointSaveStatus.textContent.includes('检查点已保存'));assert(elements.cPointSaveStatus.textContent.includes('记录已保留'));
 assert(elements.cSession.children.find(e=>e.value===elements.cSession.value).textContent.includes('1个已保存点'));
 assert(elements.cCounts.textContent.includes('已保存 1 条记录'));assert(elements.cCounts.textContent.includes('桌面不同位置 1 个'));
 assert.equal(elements.cSamples.children[0].children[2].textContent,'10.00, 20.00, 0.00');assert.equal(elements.cSamples.children[0].children[5].textContent,'已保存');
 const ps=state.sessions.find(s=>s.id===elements.cSession.value),basePoint=ps.point_checks[0];
 ps.point_checks.push({...basePoint,record_sha256:'2'.repeat(64),actual_xyz_mm:[20,20,0],error_mm:1.9},{...basePoint,record_sha256:'3'.repeat(64),actual_xyz_mm:[30,20,0],error_mm:19.73},{...basePoint,record_sha256:'4'.repeat(64),actual_xyz_mm:[30,40,0],error_mm:3.97});
 await elements.cRefresh.onclick();await elements.cSamples.children[2].children[6].children[0].onclick();
 assert.equal(ps.point_checks.length,3);assert(ps.point_checks.some(p=>p.record_sha256==='4'.repeat(64)),'corrected record on same frame must remain');assert(elements.cExcludedPoints.textContent.includes('19.73'));assert(elements.cExcludedSummary.textContent.includes('1 条'));
 elements.cPositionLimit.value='5';elements.cPositionScope.value='table';await elements.cAdoptPosition.onclick();
 assert(elements.cPositionResult.textContent.includes('仅桌面定位'));assert(elements.cPositionResult.textContent.includes('门槛5毫米'));assert(elements.cResult.textContent.includes('顶面检查尚未通过'));
 elements.cLive.onclick();assert.equal(elements.cPixel.value,'');assert.equal(elements.cPixel.disabled,true);assert.equal(elements.cRecordPoint.disabled,true);
 await elements.cCapture.onclick();
 assert(calls.every(c=>c.path.startsWith('/calibration/')));
 assert(!calls.some(c=>c.path==='/control/read'),'calibration does not automatically read joints');
 const poll=timers.find(fn=>fn.name==='poll');assert(poll);
 const moveCount=()=>calls.filter(c=>c.path==='/control/move').length;
 const finish=async()=>{control.busy=false;control.job.status='completed';control.commanded[String(control.job.parameters.joint)]=control.job.parameters.angle;control.joints=[];await poll();};
 // Bounds and durations are checked before any manual request; no read is needed.
 allowManualMove=true;
 elements.cAngle5.value='89';await elements.cMove5.onclick();assert.equal(moveCount(),0);
 elements.cAngle1.value='100.5';await elements.cMove1.onclick();assert.equal(moveCount(),0);
 elements.cAngle1.value='100';elements.cMoveTime1.value='499';await elements.cMove1.onclick();assert.equal(moveCount(),0);
 elements.cMoveTime1.value='1500';
 await Promise.all([elements.cMove1.onclick(),elements.cMove1.onclick()]);assert.equal(moveCount(),1,'double click sends once');
 assert.equal(elements.cCapture.disabled,true,'motion blocks capture');assert.equal(elements.cImage.hidden,true,'motion clears old frozen image');
 assert.equal(elements.cFit.disabled,true);assert(!calls.some(c=>c.path==='/control/read'),'manual move does not require readback');
 const captures=calls.filter(c=>c.path==='/calibration/capture').length;await elements.cCapture.onclick();assert.equal(calls.filter(c=>c.path==='/calibration/capture').length,captures);
 await finish();assert.equal(elements.cMove1.disabled,false);assert(elements.cManualStatus.textContent.includes('未自动回读'));
 assert.equal(elements.cReadback1.textContent,'未读取');assert(elements.cCommand1.textContent.includes('100'));
 // A held Enter is ignored. One Enter confirms exactly one row and duration.
 elements.cAngle3.value='120';elements.cMoveTime3.value='1800';
 await elements.cAngle3.onkeydown({key:'Enter',repeat:true,preventDefault(){}});assert.equal(moveCount(),1);
 await elements.cAngle3.onkeydown({key:'Enter',repeat:false,preventDefault(){}});assert.equal(moveCount(),2);
 const submitted=calls.filter(c=>c.path==='/control/move').at(-1).body;assert.equal(submitted.joint,3);assert.equal(submitted.angle,120);assert.equal(submitted.motion_ms,1800);await finish();
 // Lost responses never resend; matching task status resolves uncertainty.
 moveBehaviour='lostResponse';elements.cAngle2.value='110';await elements.cMove2.onclick();assert.equal(moveCount(),3);assert.equal(elements.cRecoverMove.hidden,false);
 await elements.cMove2.onclick();assert.equal(moveCount(),3);await poll();assert.equal(elements.cRecoverMove.hidden,true);await finish();assert.equal(moveCount(),3);
 // No matching accepted task stays locked until the operator explicitly recovers.
 moveBehaviour='notDelivered';await elements.cMove2.onclick();assert.equal(moveCount(),4);await poll();assert.equal(elements.cMove1.disabled,true);
 await elements.cMove1.onclick();assert.equal(moveCount(),4);await elements.cRecoverMove.onclick();assert.equal(moveCount(),4);assert.equal(elements.cMove1.disabled,false);
 // Explicit read is independent; a boundary observation is never filled as a target.
 elements.cAngle5.value='';await elements.cReadJoints.onclick();assert.equal(elements.cReadback5.textContent,'89°');assert.equal(elements.cAngle5.value,'');
 delete control.joint_limits['4'];await poll();assert.equal(elements.cMove4.disabled,true);elements.cAngle4.value='100';await elements.cMove4.onclick();assert.equal(moveCount(),4);
 console.log('标定页面：采集/拟合不运动；六关节范围/整数/用时、回车确认、双击去重、移动后清图、采集互锁、断线不重发与独立读数检查通过');
})().catch(e=>{console.error(e);process.exitCode=1;});
