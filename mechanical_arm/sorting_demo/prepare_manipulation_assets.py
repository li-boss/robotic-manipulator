"""仅本地：核对STL并生成资源清单；不导入控制器，不连接设备。"""
import hashlib
import json
from pathlib import Path
import struct
import xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parent


def main():
    folder=ROOT/'models/manipulation';items=[]
    for name in ('base_link','link1','link2','link3','link4','link5'):
        file=name+'.STL';data=(folder/'meshes'/file).read_bytes()
        count=struct.unpack_from('<I',data,80)[0]
        if len(data)!=84+count*50:raise ValueError('无效二进制STL：'+file)
        bounds=[[float('inf')]*3,[float('-inf')]*3]
        for triangle in struct.iter_unpack('<12fH',data[84:]):
            for offset in (3,6,9):
                for axis in range(3):
                    v=triangle[offset+axis]
                    bounds[0][axis]=min(bounds[0][axis],v);bounds[1][axis]=max(bounds[1][axis],v)
        items.append(dict(link=name,file=file,triangles=count,sha256=hashlib.sha256(data).hexdigest(),
            bounds_native=bounds,source='三维结构文件/'+file))
    tree=ET.parse(ROOT/'models/course/dofbot.urdf')
    joints=[dict(name=j.get('name'),xyz=[float(v) for v in j.find('origin').get('xyz').split()],
                 rpy=[float(v) for v in j.find('origin').get('rpy').split()]) for j in tree.findall('joint')]
    for mesh in tree.findall('.//mesh'):
        mesh.set('filename','meshes/'+Path(mesh.get('filename')).name)
    tree.write(folder/'dofbot_display.urdf',encoding='utf-8',xml_declaration=True)
    manifest=dict(version=1,meshes=items,joints=joints,total_triangles=sum(x['triangles'] for x in items),
        native_scale_to_metres=1,scale_status='坐标约0.1量级，与课程米制尺寸相符；仍须实物核对',
        kinematics_source_sha256=hashlib.sha256((ROOT/'models/course/dofbot.urdf').read_bytes()).hexdigest(),
        display_urdf_sha256=hashlib.sha256((folder/'dofbot_display.urdf').read_bytes()).hexdigest(),
        alignment_verified=False,note='显示资源不证明实物标定；碰撞使用实测保守包络，不直接使用高密度STL')
    (folder/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print('已核对',len(items),'个STL，共',manifest['total_triangles'],'个三角面；无硬件操作')


if __name__=='__main__':main()
