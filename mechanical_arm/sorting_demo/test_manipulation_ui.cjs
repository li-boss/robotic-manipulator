// 纯输入转换测试；不访问网络、相机或设备。
const assert=require('node:assert/strict');
const {imagePoint,normalizedBox,targetInput,selectionIssue,outlineIssue,outlineBounds}=require('./manipulation_ui.js');
assert.deepEqual(imagePoint({clientX:170,clientY:140},{left:10,top:20,width:320,height:240},[640,480]),[320,240]);
assert.equal(imagePoint({clientX:400,clientY:140},{left:10,top:20,width:320,height:240},[640,480]),null);
assert.deepEqual(normalizedBox([300,200],[100,50]),[100,50,300,200]);
const selection={bbox:[10,10,100,100],center:[50,60],edgeA:[30,30],edgeB:[80,30]};
const t=targetInput({id:'f',image_size:[640,480]},selection,{block_size_id:'block'},{kind:'fixed',name:'A'});
assert.deepEqual(t.center_px,[50,60]);assert.equal(t.source,'manual');assert.deepEqual(t.edge_px,[[30,30],[80,30]]);
const pickup=targetInput({id:'f',image_size:[640,480]},selection,{block_size_id:'block'},null,'pickup');
assert.equal(pickup.task_mode,'pickup');assert(!Object.hasOwn(pickup,'destination'));
assert.throws(()=>targetInput(null,selection,{},{}));assert.throws(()=>targetInput({id:'f'}, {...selection,edgeB:null},{},{}));
console.log('人工选点：缩放、反向框选、中心与方向输入测试通过');

const f={id:'test',image_size:[640,480]};
assert(selectionIssue(f,{...selection,bbox:[10,10,10,10]},null,'pickup').includes('方块框无效'));
assert(selectionIssue(f,{...selection,center:[150,150]},null,'pickup').includes('框内'));
assert(selectionIssue(f,{...selection,edgeB:selection.edgeA},null,'pickup').includes('太近'));
assert(selectionIssue(f,selection,{kind:'pixel'},'pick_place').includes('放置中心'));
assert.equal(selectionIssue(f,selection,null,'pickup'),null);

// 旋转菱形、透视四边形和顺逆时针都可作为目标框；不从框推导中心。
const quad=[[100,180],[180,100],[270,190],[190,270]];
assert.equal(outlineIssue(quad,[640,480]),null);
assert.equal(outlineIssue([...quad].reverse(),[640,480]),null);
assert.equal(outlineIssue([[110,110],[240,130],[220,260],[90,250]],[640,480]),null);
assert(outlineIssue([[100,100],[200,200],[200,100],[100,200]],[640,480]));
assert(outlineIssue([[100,100],[100,100],[200,200],[100,200]],[640,480]));
const rotated={bbox:outlineBounds(quad),outline_px:quad,center:[185,185],edgeA:quad[0],edgeB:quad[1]};
assert.equal(selectionIssue(f,rotated,null,'pickup'),null);
assert.deepEqual(targetInput(f,rotated,{block_size_id:'block'},null,'pickup').outline_px,quad);
assert(selectionIssue(f,{...rotated,center:[105,105]},null,'pickup').includes('顶面内'));
console.log('旋转/透视四角、轮廓合法性与独立中心测试通过');
