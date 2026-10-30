"""设备端只读寻找已有相机参数；不打开相机、总线或任何控制器，不自动采用结果。"""
import hashlib
import json
import math
from pathlib import Path


def normalize(data, source):
    if not isinstance(data,dict):raise ValueError('不是相机参数映射')
    width=data.get('image_width');height=data.get('image_height')
    size=data.get('image_size',[width,height])
    if not isinstance(size,list) or len(size)!=2 or any(type(v)is not int or v<=0 for v in size):
        raise ValueError('缺少明确分辨率')
    def values(*keys):
        value=next((data[k] for k in keys if k in data),None)
        if isinstance(value,dict):value=value.get('data')
        if isinstance(value,list) and value and isinstance(value[0],list):value=[x for row in value for x in row]
        if not isinstance(value,list) or any(type(v)not in (int,float) or not math.isfinite(v) for v in value):
            raise ValueError('缺少有限数值矩阵')
        return value
    k=values('camera_matrix','matrix','K');d=values('distortion_coefficients','distortion','D')
    if len(k)!=9 or len(d)!=5:raise ValueError('当前入口需要3×3内参和5项畸变；不截断其他畸变模型')
    if k[0]<=0 or k[4]<=0 or k[6:]!=[0,0,1]:raise ValueError('内参矩阵无效')
    return dict(kind='imported',image_size=size,matrix=[k[:3],k[3:6],k[6:]],distortion=d,source=source)


def read_file(path):
    raw=path.read_bytes()
    if len(raw)>262144:raise ValueError('文件过大，未读取为相机参数')
    text=raw.decode('utf-8-sig')
    if path.suffix.lower()=='.json':data=json.loads(text)
    else:
        import yaml  # 沿用设备ROS依赖；本脚本不会安装或更新系统包。
        class MatrixLoader(yaml.SafeLoader):pass
        MatrixLoader.add_constructor('tag:yaml.org,2002:opencv-matrix',lambda loader,node:loader.construct_mapping(node,deep=True))
        if text.startswith('%YAML:'):text='\n'.join(text.splitlines()[1:])
        try:data=yaml.load(text,Loader=MatrixLoader)
        except yaml.YAMLError as exc:raise ValueError('不是可读取的安全相机YAML') from exc
    return normalize(data,str(path)+' sha256='+hashlib.sha256(raw).hexdigest())


def main():
    project=Path(__file__).resolve().parent
    if project!=Path('/root/jjt/sorting_demo'):raise SystemExit('此只读检查仅在/root/jjt/sorting_demo运行')
    for name in ('AGENTS.md','机械臂操作准则.md'):
        data=(project.parent/name).read_bytes()
        print('本次规则核对',name,hashlib.sha256(data).hexdigest())
    print('只读查询已有参数文件；不打开相机、不读舵机、不运动、不改变设置。')
    roots=[Path('/root/.ros/camera_info'),Path('/root/dofbot_ws/src'),Path('/root/ros2_ws/src'),project]
    found=[];skipped=[];seen=set()
    for root in roots:
        if not root.is_dir():continue
        for index,path in enumerate(root.rglob('*')):
            if index>20000:skipped.append(dict(path=str(root),reason='达到本次扫描上限'));break
            if any(part in ('logs','backups','staging','.git','node_modules','__pycache__') for part in path.relative_to(root).parts):continue
            if path.suffix.lower()not in ('.yaml','.yml','.json') or not any(v in path.name.lower() for v in ('camera','calib','astra')):continue
            if path.is_symlink() or not path.is_file() or path in seen:continue
            seen.add(path)
            try:found.append(read_file(path))
            except (ValueError,KeyError,UnicodeError,ImportError,OSError) as exc:skipped.append(dict(path=str(path),reason=str(exc)))
    output=project/'logs/camera_parameters';output.mkdir(parents=True,exist_ok=True)
    for value in found:
        key=hashlib.sha256(value['source'].encode()).hexdigest()[:16]
        path=output/('candidate_'+key+'.json')
        if not path.exists():path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
    report=dict(candidates=found,skipped=skipped,hardware_reads=0,motor_commands=0,
        note='找到参数不证明属于当前相机、分辨率和焦距；请核对来源后导入，不自动覆盖配置')
    (output/'scan_report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(dict(candidates=len(found),report=str(output/'scan_report.json'),hardware_reads=0,motor_commands=0),ensure_ascii=False))


if __name__=='__main__':main()
