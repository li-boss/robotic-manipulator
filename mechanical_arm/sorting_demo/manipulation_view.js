/* 本地WebGL三维查看器，无CDN；此文件不具有控制接口。矩阵与尺寸来自本次计划。 */
(()=>{'use strict';
const canvas=document.getElementById('mView'),gl=canvas.getContext('webgl',{antialias:true});
let plan=null,scene=null,step=0,meshes=[],nominal=null,animated=null,yaw=.8,pitch=.65,radius=.75,drag=null;
const I=()=>[1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1];
const flat=m=>m.flat();
function mul(a,b){const c=Array(16).fill(0);for(let r=0;r<4;r++)for(let k=0;k<4;k++)for(let j=0;j<4;j++)c[r*4+j]+=a[r*4+k]*b[k*4+j];return c;}
function column(m){return new Float32Array([m[0],m[4],m[8],m[12],m[1],m[5],m[9],m[13],m[2],m[6],m[10],m[14],m[3],m[7],m[11],m[15]]);}
function cross(a,b){return[a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]];}
const norm=a=>{const n=Math.hypot(...a);return a.map(v=>v/n);},dot=(a,b)=>a.reduce((s,v,i)=>s+v*b[i],0);
function look(eye,at){const z=norm(eye.map((v,i)=>v-at[i])),x=norm(cross([0,0,1],z)),y=cross(z,x);return[...x,-dot(x,eye),...y,-dot(y,eye),...z,-dot(z,eye),0,0,0,1];}
function perspective(aspect){const f=1/Math.tan(Math.PI/7),near=.01,far=10;return[f/aspect,0,0,0,0,f,0,0,0,0,(far+near)/(near-far),2*far*near/(near-far),0,0,-1,0];}
function shader(kind,source){const s=gl.createShader(kind);gl.shaderSource(s,source);gl.compileShader(s);if(!gl.getShaderParameter(s,gl.COMPILE_STATUS))throw Error(gl.getShaderInfoLog(s));return s;}
let program,position,normal,uModel,uCamera,uColor;
if(gl){program=gl.createProgram();gl.attachShader(program,shader(gl.VERTEX_SHADER,'attribute vec3 p;attribute vec3 n;uniform mat4 model;uniform mat4 camera;varying float light;void main(){vec3 N=normalize((model*vec4(n,0.)).xyz);light=.45+.55*abs(dot(N,normalize(vec3(.5,-.6,1.))));gl_Position=camera*model*vec4(p,1.);}'));gl.attachShader(program,shader(gl.FRAGMENT_SHADER,'precision mediump float;uniform vec3 color;varying float light;void main(){gl_FragColor=vec4(color*light,1.);}'));gl.linkProgram(program);position=gl.getAttribLocation(program,'p');normal=gl.getAttribLocation(program,'n');uModel=gl.getUniformLocation(program,'model');uCamera=gl.getUniformLocation(program,'camera');uColor=gl.getUniformLocation(program,'color');}
function buffer(vertices,normals){const result={count:vertices.length/3};result.p=gl.createBuffer();gl.bindBuffer(gl.ARRAY_BUFFER,result.p);gl.bufferData(gl.ARRAY_BUFFER,new Float32Array(vertices),gl.STATIC_DRAW);result.n=gl.createBuffer();gl.bindBuffer(gl.ARRAY_BUFFER,result.n);gl.bufferData(gl.ARRAY_BUFFER,new Float32Array(normals),gl.STATIC_DRAW);return result;}
function dispose(b){if(b){gl.deleteBuffer(b.p);gl.deleteBuffer(b.n);}}
function renderBuffer(b,model,color,mode){gl.bindBuffer(gl.ARRAY_BUFFER,b.p);gl.vertexAttribPointer(position,3,gl.FLOAT,false,0,0);gl.enableVertexAttribArray(position);gl.bindBuffer(gl.ARRAY_BUFFER,b.n);gl.vertexAttribPointer(normal,3,gl.FLOAT,false,0,0);gl.enableVertexAttribArray(normal);gl.uniformMatrix4fv(uModel,false,column(model));gl.uniform3fv(uColor,color);gl.drawArrays(mode||gl.TRIANGLES,0,b.count);}
const cubeVertices=[[-1,-1,-1],[1,-1,-1],[1,1,-1],[-1,1,-1],[-1,-1,1],[1,-1,1],[1,1,1],[-1,1,1]],faces=[[0,2,1],[0,3,2],[4,5,6],[4,6,7],[0,1,5],[0,5,4],[2,3,7],[2,7,6],[1,2,6],[1,6,5],[3,0,4],[3,4,7]];
let cube;if(gl){const v=[],n=[];for(const[a,b,c]of faces){const A=cubeVertices[a],B=cubeVertices[b],C=cubeVertices[c],N=norm(cross(B.map((x,i)=>x-A[i]),C.map((x,i)=>x-A[i])));for(const p of[A,B,C]){v.push(...p);n.push(...N);}}cube=buffer(v,n);}
function box(center,half,color,pose=I()){const m=I();for(let i=0;i<3;i++){m[i*4+i]=half[i];m[i*4+3]=center[i];}renderBuffer(cube,mul(pose,m),color);}
function polyline(points,color){if(points.length<2)return;const v=points.flat(),n=points.map(()=>[0,0,1]).flat(),b=buffer(v,n);renderBuffer(b,I(),color,gl.LINE_STRIP);dispose(b);}
function axisRotation(axis,theta){const[x,y,z]=norm(axis),c=Math.cos(theta),s=Math.sin(theta),v=1-c;return[x*x*v+c,x*y*v-z*s,x*z*v+y*s,0,y*x*v+z*s,y*y*v+c,y*z*v-x*s,0,z*x*v-y*s,z*y*v+x*s,z*z*v+c,0,0,0,0,1];}
function currentMatrices(){return animated||plan?.poses[step].map(flat)||nominal;}
function draw(){if(!gl)return;const width=Math.max(1,Math.round(canvas.clientWidth*devicePixelRatio)),height=Math.max(1,Math.round(canvas.clientHeight*devicePixelRatio));if(canvas.width!==width||canvas.height!==height){canvas.width=width;canvas.height=height;}gl.viewport(0,0,width,height);gl.clearColor(.035,.07,.11,1);gl.clear(gl.COLOR_BUFFER_BIT|gl.DEPTH_BUFFER_BIT);gl.enable(gl.DEPTH_TEST);gl.useProgram(program);const at=[0,.12,.12],eye=[at[0]+radius*Math.cos(yaw)*Math.cos(pitch),at[1]+radius*Math.sin(yaw)*Math.cos(pitch),at[2]+radius*Math.sin(pitch)];gl.uniformMatrix4fv(uCamera,false,column(mul(perspective(width/height),look(eye,at))));
polyline([[0,0,0],[.1,0,0]],[1,.4,.4]);polyline([[0,0,0],[0,.1,0]],[.4,1,.5]);polyline([[0,0,0],[0,0,.1]],[.5,.7,1]);
const matrices=currentMatrices(),colors=[[.55,.6,.65],[.18,.65,.35],[.65,.7,.75],[.18,.65,.35],[.65,.7,.75],[.85,.88,.9]];
if(!matrices)return;
for(const [i,b]of meshes.entries())if(b)renderBuffer(b,matrices[i],colors[i]);
polyline(matrices.map(m=>[m[3],m[7],m[11]]),[.4,.65,1]);
if(scene?.cad?.camera_mount){const c=scene.cad.camera_mount,local=I();for(let i=0;i<3;i++){for(let j=0;j<3;j++)local[i*4+j]=c.rotation_optical_to_link[i][j];local[i*4+3]=c.xyz_mm[i]/1000;}const pose=mul(matrices[4],local);polyline([[pose[3],pose[7],pose[11]],[pose[3]+.06*pose[2],pose[7]+.06*pose[6],pose[11]+.06*pose[10]]],[1,.5,.25]);}
if(!plan||!scene)return;
const z=scene.table_z_mm/1000,workspace=scene.workspace_xy_mm.map(p=>[p[0]/1000,p[1]/1000,z]);polyline([...workspace,workspace[0]],[.4,.8,.6]);
const tcp=mul(matrices[5],flat(plan.tool_matrix));box([0,0,0],[.003,.003,.003],[1,.8,.25],tcp);
for(const b of scene.model.tool_boxes_mm||[])box(b.center.map(v=>v/1000),b.half_size.map(v=>v/1000),[.3,.5,.65],matrices[5]);
for(const b of scene.obstacles||[])box(b.center_mm.map(v=>v/1000),b.half_size_mm.map(v=>v/1000),[.65,.25,.3]);
let carried=false,released=false;for(const s of plan.steps.slice(0,step)){if(s.label==='夹持方块')carried=true;if(s.label==='释放方块'){carried=false;released=true;}}
const blockPose=carried?mul(tcp,flat(plan.block_attachment)):flat(released?plan.block_release_pose:plan.block_initial_pose);box([0,0,0],scene.block_size_mm.map(v=>v/2000),[.95,.75,.15],blockPose);
const target=plan.target;box([target.destination_xy_mm[0]/1000,target.destination_xy_mm[1]/1000,z+.001],[.006,.006,.001],[.9,.35,.4]);
polyline(plan.poses.map(p=>{const m=mul(flat(p[5]),flat(plan.tool_matrix));return[m[3],m[7],m[11]];}),[.5,.65,.8]);}
canvas.onpointerdown=e=>{drag=[e.clientX,e.clientY];canvas.setPointerCapture(e.pointerId);};canvas.onpointermove=e=>{if(drag){yaw-=(e.clientX-drag[0])*.008;pitch=Math.max(.1,Math.min(1.5,pitch+(e.clientY-drag[1])*.008));drag=[e.clientX,e.clientY];draw();}};canvas.onpointerup=()=>{drag=null;};canvas.onpointercancel=()=>{drag=null;};canvas.onwheel=e=>{e.preventDefault();radius=Math.max(.2,Math.min(2,radius*Math.exp(e.deltaY*.001)));draw();};
window.addEventListener('resize',draw);
window.manipulationView={setSnapshot(m,s){plan=null;step=0;animated=null;nominal=m.map(flat);scene=s;document.getElementById('mStepLabel').textContent='按冻结读数计算的模型姿态；几何精度待验证，不发送运动';draw();},setManifest(manifest){nominal=[I()];let m=I();for(const j of manifest.joints){const[r,p,y]=j.rpy;let origin=mul(mul(axisRotation([0,0,1],y),axisRotation([0,1,0],p)),axisRotation([1,0,0],r));for(let k=0;k<3;k++)origin[k*4+3]=j.xyz[k];m=mul(m,origin);nominal.push(m);}draw();},setPlan(p,s){plan=p;scene=s;step=0;animated=null;draw();},setStep(n){step=Math.max(0,Math.min(plan?.steps.length||0,n));animated=null;draw();},animate(n,t){if(!plan||n<1)return;const q=plan.initial_angles.slice();for(const s of plan.steps.slice(0,n-1))for(const[id,v]of s.joints)q[id-1]=v;for(const[id,v]of plan.steps[n-1].joints)q[id-1]+=(v-q[id-1])*t;animated=[I()];let m=I();for(let i=0;i<5;i++){const j=plan.joints[i];m=mul(m,mul(flat(j.origin),axisRotation(j.axis,(q[i]-plan.servo_zero_deg[i])*plan.servo_sign[i]*Math.PI/180)));animated.push(m);}step=n;draw();},async loadMeshes(items){if(!gl)throw Error('当前浏览器不支持WebGL，仍可查看明确关节计划');for(let i=0;i<items.length;i++){if(meshes[i])continue;const response=await fetch('/manipulation/assets/'+items[i].file);if(!response.ok)throw Error('模型加载失败');const data=await response.arrayBuffer(),view=new DataView(data),count=view.getUint32(80,true);if(data.byteLength!==84+count*50)throw Error('无效STL');const v=new Float32Array(count*9),n=new Float32Array(count*9);for(let t=0;t<count;t++){const offset=84+t*50;for(let corner=0;corner<3;corner++)for(let a=0;a<3;a++){v[t*9+corner*3+a]=view.getFloat32(offset+12+corner*12+a*4,true);n[t*9+corner*3+a]=view.getFloat32(offset+a*4,true);}}meshes[i]=buffer(v,n);}draw();if(!plan)document.getElementById('mStepLabel').textContent=scene?'冻结读数对应的模型外形；几何精度待验证':'结构装配示意；不是实机当前姿态，不发送角度';}};
draw();
})();
