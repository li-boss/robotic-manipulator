// 内存DOM和假HTTP：完整页面操作测试，不打开设备、相机或真实控制器。
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const elements={};
class Element{
 constructor(tag=''){this.tag=tag;this.children=[];this._value='';this.textContent='';this.hidden=false;this.disabled=false;this.checked=false;this.style={};this.events={};}
 get value(){return this._value;}
 set value(v){this._value=String(v);}
 append(...nodes){this.children.push(...nodes);}
 replaceChildren(...nodes){this.children=nodes;}
 addEventListener(name,fn){(this.events[name]??=[]).push(fn);}
 dispatch(name){for(const fn of this.events[name]||[])fn();}
 getBoundingClientRect(){return{left:10,top:20,width:320,height:240};}
 setPointerCapture(){} scrollIntoView(){} click(){}
 getContext(){return{clearRect(){},drawImage(){},strokeRect(){},beginPath(){},arc(){},fill(){},fillText(text){globalThis.canvasTexts=(globalThis.canvasTexts||[]).concat(text);},moveTo(){},lineTo(){},stroke(){},save(){},restore(){},setLineDash(){}};}
}
const html=fs.readFileSync(__dirname+'/manipulation.html','utf8');
for(const m of html.matchAll(/<([a-z0-9]+)[^>]*\bid="([^"]+)"/g)){assert(!elements[m[2]],'duplicate id');elements[m[2]]=new Element(m[1]);elements[m[2]].id=m[2];}
const document={getElementById:id=>elements[id],createElement(tag){const e=new Element(tag);Object.defineProperty(e,'id',{get(){return e._id;},set(id){e._id=id;elements[id]=e;}});return e;},querySelectorAll(selector){return Object.values(elements).filter(e=>selector==='button'?e.tag==='button':['input','textarea','select'].includes(e.tag));}};
elements.mTaskMode.value='pick_place';elements.mCadOptics.value='diagonal';elements.mCadRoll.value='0';elements.mPickMode.value='bbox';elements.mDestinationKind.value='pixel';elements.mCheckKind.value='table';
const setup=JSON.parse(fs.readFileSync(__dirname+'/manipulation_setup.json','utf8'));setup.destinations={A:[80,200]};
const state={setup,setup_sha256:'v1',mode:'simulation',readiness:{ready:true,issues:[]},execution_readiness:{ready:true,issues:[]},drafts:[],recent_tasks:[],mesh_manifest:{meshes:[],joints:[]}};
const control={csrf:'fake-csrf',mode:'simulation',busy:false,job:null};
const plan={steps:[{label:'测试有限步骤',joints:[[1,100]],motion_ms:1500,settle_ms:300}],target:{xyz_mm:[0,150,30]},duration_ms:1800};
let count=0,offline=false,statusTimeout=false,previewTimeout=false,previewGate=null;const calls=[],timers=[];
async function fetch(path,options={}){
 const body=options.body?JSON.parse(options.body):null;if(body)calls.push({path,body});
 if((statusTimeout&&path==='/control/status')||(previewTimeout&&path==='/manipulation/preview'))throw Object.assign(Error('signal timed out'),{name:'TimeoutError'});
 if(offline)throw Error('synthetic offline');if(path==='/manipulation/preview'&&previewGate)await previewGate;let value={};
 if(path==='/control/status')value=control;
 else if(path==='/manipulation/status')value=state;
 else if(path==='/manipulation/acceptance')value={fixed:{passed:false},pixel:{passed:false}};
 else if(path==='/manipulation/invalidate')value={invalidated:true};
 else if(path==='/manipulation/target')value={target:{xyz_mm:[0,260,14],task_mode:body.target.task_mode}};
 else if(path==='/manipulation/prepare-cad'){state.setup.cad={capture_mode:body.capture_mode,camera_mount:{link:4},intrinsics_source:{description:'测试估算',accuracy_verified:false},gripper:{open:10,closed:127},notes:[],assumptions_confirmed:false};state.setup_sha256='cad1';if(body.capture_mode==='joint_snapshot_v1'){state.readiness={ready:true,issues:[]};state.execution_readiness={ready:true,issues:[]};}value=state;}
 else if(path==='/manipulation/settings'){state.setup=body.setup;state.setup_sha256='cad2';value=state;}
 else if(path==='/manipulation/capture')value={id:(++count).toString(16).padStart(32,'0'),image_size:[640,480],joints:[],jpeg_base64:'AA==',reach_overlay:{radii_mm:[133,319],lines:[{radius_mm:319,points:[[10,40],[320,20],[630,40]]}]}};
 else if(path==='/manipulation/preview')value=body.dry_run?{draft_id:'draft',plan,scene:{},dry_run:true}:{token:'token-'+count,mode:'simulation',parameters:{operation:'manipulation',plan},scene:{},conditions:'仅模拟'};
 else if(path==='/manipulation/step')value={token:'step-token',mode:'simulation',parameters:{operation:'manipulation',step_only:true,plan},conditions:'仅模拟空载一步'};
 else if(path==='/control/execute'){control.job={id:body.token,status:'completed',parameters:{operation:'manipulation'}};value={job:control.job};}
 else if(path==='/manipulation/result')value={acceptance:{fixed:{passed:false}}};
 else throw Error('unexpected request '+path);
 return{ok:true,json:async()=>JSON.parse(JSON.stringify(value))};
}
class FakeImage{set src(value){queueMicrotask(()=>this.onload());}}
const viewer={setPlan(){},setStep(){},setManifest(){},animate(){},async loadMeshes(){}};
const context=vm.createContext({document,fetch,Image:FakeImage,structuredClone,AbortSignal,window:{manipulationView:viewer},setTimeout(fn){timers.push(fn);},queueMicrotask,console,Blob,URL,performance,requestAnimationFrame(){}});
vm.runInContext(fs.readFileSync(__dirname+'/manipulation_ui.js','utf8'),context);
const flush=()=>new Promise(resolve=>setImmediate(resolve));
function event(x,y){return{clientX:10+x/2,clientY:20+y/2,pointerId:1};}
(async()=>{
 await flush();await flush();assert(elements.mMode.textContent.includes('模拟'));
 await elements.mCapture.onclick();assert(elements.mFrameInfo.textContent.includes('640×480'));
 elements.mImage.onpointerdown(event(300,300));elements.mImage.onpointerup(event(100,100));
 elements.mImage.onpointerdown(event(180,200));elements.mImage.onpointerdown(event(150,150));elements.mImage.onpointerdown(event(250,150));elements.mImage.onpointerdown(event(450,350));
 await flush();elements.mSceneConfirmed.checked=true;
 const before=calls.filter(c=>c.path==='/control/execute').length;
 await elements.mPreview.onclick();const sent=calls.findLast(c=>c.path==='/manipulation/preview').body.target;
 assert.deepEqual(sent.bbox,[100,100,300,300]);assert.deepEqual(sent.center_px,[180,200]);assert.deepEqual(sent.edge_px,[[150,150],[250,150]]);assert.deepEqual(sent.destination,{kind:'pixel',pixel:[450,350]});
 assert.equal(calls.filter(c=>c.path==='/control/execute').length,before,'preview must not move');
 assert.equal(elements.mApproval.hidden,false);assert.equal(elements.mExecute.disabled,true);
 elements.mApproveCheck.checked=true;elements.mApproveCheck.onchange();assert.equal(elements.mExecute.disabled,false);
 await Promise.all([elements.mExecute.onclick(),elements.mExecute.onclick()]);assert.equal(calls.filter(c=>c.path==='/control/execute').length,before+1,'double click executes once');
 await elements.mPreview.onclick();elements.mImage.onpointerdown(event(460,360));assert.equal(elements.mApproval.hidden,true,'target edit clears confirmation');
 await flush();await elements.mPreview.onclick();elements.mTable.value='123';elements.mTable.dispatch('input');assert.equal(elements.mApproval.hidden,true);assert.equal(elements.mPreview.disabled,true,'unsaved geometry prevents planning with old values');
 assert(!calls.some(c=>/detection|teaching|auto\//.test(c.path)),'manual mode must never call detection');
 await elements.mReload.onclick();
 const writes=calls.filter(c=>c.path==='/control/execute').length;
 elements.mCadObserve.value='90,80,35,10,90';elements.mCadOptics.value='diagonal';elements.mCadRoll.value='0';
 await elements.mPrepareCad.onclick();assert.equal(state.setup.cad.assumptions_confirmed,false);
 elements.mCadAccept.checked=true;elements.mCadAccept.onchange();await elements.mAdoptCad.onclick();
 assert.equal(state.setup.cad.assumptions_confirmed,true,elements.mNotice.textContent);assert.equal(state.setup.model_reviewed,false);
 elements.mTaskMode.value='pickup';elements.mTaskMode.onchange();await flush();
 assert.equal(elements.mDestinationGroup.hidden,true);
 await elements.mLocate.onclick();const located=calls.findLast(c=>c.path==='/manipulation/target').body.target;
 assert.equal(located.task_mode,'pickup');assert(!Object.hasOwn(located,'destination'));
 assert.equal(calls.filter(c=>c.path==='/control/execute').length,writes,'CAD setup and locate must not move');

 // 未完成首次设置时按钮可点，并给出原因；不会调用规划或运动。
 const cad=structuredClone(state.setup.cad);state.setup.cad=null;
 state.readiness={ready:false,issues:[{item:'parameters',reason:'缺少模型定位参数'}]};
 await timers.pop()();assert.equal(elements.mGenerate.disabled,false);
 const previews=calls.filter(c=>c.path==='/manipulation/preview').length;
 await elements.mGenerate.onclick();assert.equal(elements.mApproval.hidden,false);assert(elements.mNotice.textContent.includes('完整计划已生成'));assert.equal(elements.mExecute.disabled,false);assert.equal(elements.mApprovalCheckLabel.hidden,true);assert(!elements.mExecutionHint.textContent.includes('勾选'));assert.equal(state.setup.cad.assumptions_confirmed,false);
 assert.equal(calls.filter(c=>c.path==='/manipulation/preview').length,previews+1);assert.equal(calls.findLast(c=>c.path==='/manipulation/preview').body.dry_run,false);
 state.setup.cad=cad;state.setup.cad.assumptions_confirmed=true;state.readiness={ready:true,issues:[]};
 state.execution_readiness={ready:false,issues:[{item:'paths',reason:'尚缺空载验证'}]};
 await timers.pop()();elements.mSceneConfirmed.checked=true;
 await elements.mGenerate.onclick();assert.equal(calls.findLast(c=>c.path==='/manipulation/preview').body.dry_run,false,'新入口不强制先做空载');
 await elements.mDryrun.onclick();
 assert.equal(elements.mEmptyValidation.hidden,false);assert.equal(elements.mApproval.hidden,true);
 state.drafts=[{id:'draft',next_step:0,total:1,failed:false}];await timers.pop()();
 elements.mEmpty.checked=true;elements.mEmpty.dispatch('input');elements.mEmpty.dispatch('change');
 await elements.mNextEmptyStep.onclick();assert.equal(calls.findLast(c=>c.path==='/manipulation/step').body.draft_id,'draft','空载确认不应清空当前计划');
 assert.equal(calls.filter(c=>c.path==='/control/execute').length,writes,'设置、生成及逐步预览均不运动');
 elements.mExactCenter.value='190,210';await elements.mApplyExact.onclick();assert.equal(elements.mApproval.hidden,true,'精确选点修改也必须取消旧运动令牌');
 // 冻结后回到框选；只点一下不能形成有效矩形，也不能直接跳到点中心。
 await elements.mCapture.onclick();assert.equal(elements.mPickMode.value,'bbox');
 elements.mImage.onpointerdown(event(200,200));elements.mImage.onpointerup(event(200,200));
 assert.equal(elements.mPickMode.value,'bbox');assert(elements.mNotice.textContent.includes('按住鼠标拖动'));
 assert(elements.mPickHint.textContent.includes('外围框'));
 assert(!html.includes('id="mSetupPanel" open'),'首次设置默认收起');
 assert(html.indexOf('id="mSetupPanel"')>html.indexOf('3．确认运动'),'专业参数排在主流程之后');

 // 四角框选：四角可斜放；首两点定义方向，中心独立点选；撤销可回到第四角。
 elements.mTaskMode.value='pick_place';elements.mTaskMode.onchange();
 await elements.mSelectQuad.onclick();assert.equal(elements.mPickMode.value,'quad');
 for(const p of [[120,180],[220,120],[280,220],[180,280]])elements.mImage.onpointerdown(event(...p));
 assert.equal(elements.mPickMode.value,'center');
 elements.mUndo.onclick();assert.equal(elements.mPickMode.value,'quad');
 elements.mImage.onpointerdown(event(180,280));elements.mImage.onpointerdown(event(200,200));
 assert.equal(elements.mPickMode.value,'place','四角首条边已是人工方向，不要求重复点边');
 elements.mImage.onpointerdown(event(450,350));await flush();
 plan.warnings=['相机模型投影边长24.0毫米，已知30.0毫米，精度待验证'];
 plan.reference={sample_count:9,position_tolerance_mm:8,stages:[{source:{names:['中心','左中']},position_error_mm:6.3}]};await elements.mGenerate.onclick();assert(elements.mPlanSummary.textContent.includes('九点参考：9点'));assert(elements.mPlanSummary.textContent.includes('6.30毫米 / 允许 8毫米'));const rotated=calls.findLast(c=>c.path==='/manipulation/preview').body.target;
 assert.deepEqual(rotated.outline_px,[[120,180],[220,120],[280,220],[180,280]]);
 assert.deepEqual(rotated.bbox,[120,120,280,280]);assert.deepEqual(rotated.center_px,[200,200]);
 assert.deepEqual(rotated.edge_px,[[120,180],[220,120]]);
 assert(elements.mPlanSummary.textContent.includes('24.0毫米'));
 state.setup.cad.camera_calibration={intrinsics:'aaaaaaaa12345678',hand_eye:'bbbbbbbb12345678'};state.setup.table_z_mm=0;state.position_check={valid:true,limit_mm:5};
 state.nine_point_reference={available:true,sample_count:9};state.planning={status:'running',elapsed_seconds:12,stage:'45°：接近抓取，检查扫掠路径'};await timers.pop()();assert(elements.mPlanningStatus.textContent.includes('12秒'));assert(elements.mPlanningStatus.textContent.includes('未下发运动'));
 assert(elements.mSource.textContent.includes('已接入九点参考（9点）'));assert(elements.mSource.textContent.includes('aaaaaaaa'));assert(elements.mSource.textContent.includes('bbbbbbbb'));assert(elements.mSource.textContent.includes('桌面 Z=0'));assert(elements.mSource.textContent.includes('不会重新拟合相机参数'));
 const plansBeforeTimeout=calls.filter(c=>c.path==='/manipulation/preview').length,movesBeforeTimeout=calls.filter(c=>c.path==='/control/execute').length;
 previewTimeout=true;await elements.mGenerate.onclick();assert(elements.mNotice.textContent.includes('计划计算等待超时'));assert(!elements.mNotice.textContent.includes('连接中断'));assert.equal(calls.filter(c=>c.path==='/manipulation/preview').length,plansBeforeTimeout+1);assert.equal(calls.filter(c=>c.path==='/control/execute').length,movesBeforeTimeout);previewTimeout=false;
 statusTimeout=true;await timers.pop()();assert(elements.mNotice.textContent.includes('状态查询响应超时'));assert(!elements.mNotice.textContent.includes('连接中断'));statusTimeout=false;await timers.pop()();
 let releasePreview;previewGate=new Promise(resolve=>{releasePreview=resolve;});const stale=elements.mGenerate.onclick();await flush();elements.mTaskMode.value='pickup';elements.mTaskMode.onchange();releasePreview();await stale;previewGate=null;assert.equal(elements.mApproval.hidden,true,'计算期间修改选点不能恢复旧确认');assert(elements.mNotice.textContent.includes('计算期间选点或设置已改变'));
 const noWrites=calls.filter(c=>c.path==='/control/execute').length;
 await elements.mSelectBox.onclick();assert.equal(elements.mApproval.hidden,true,'重画框必须撤销旧确认');
 assert.equal(calls.filter(c=>c.path==='/control/execute').length,noWrites,'四角框选、选点和生成计划不运动');
 await flush();await flush();
 offline=true;await timers.pop()();assert.equal(elements.mPreview.disabled,true);assert(elements.mNotice.textContent.includes('连接中断'));
 assert((globalThis.canvasTexts||[]).some(t=>String(t).includes('可达外界 319mm')),'reach overlay label drawn');console.log('完整抓放页面：缩放框选、中心方向、放置点、预览不运动、双击去重、修改失效、未保存测量及断线保护通过');
})().catch(e=>{console.error(e);process.exitCode=1;});
