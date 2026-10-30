"""本地组装可审阅部署包；不连接设备，不运行任何控制器。"""
import hashlib
import io
import json
from pathlib import Path
import tarfile

ROOT=Path(__file__).resolve().parent.parent
BACKUP=ROOT/'implementation_backups/manipulation_20261016'


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    changed=('control.py','server.py','index.html','ros_planner.py','start_service.py')
    added=('manipulation_geometry.py','manipulation_task.py','manipulation.html','manipulation_ui.js',
           'manipulation_view.js','manipulation_setup.json','人工完整抓放说明.md','现场测量与验收清单.md')
    cfg=json.loads((ROOT/'sorting_demo/manipulation_setup.json').read_text())
    if (cfg['model_reviewed'] or cfg['calibration_reviewed'] or cfg['path_validations'] or cfg['scene_revision'] is not None
            or cfg['camera']['matrix'] is not None or cfg['camera']['base_to_camera'] is not None):
        raise ValueError('禁止把标定或测试夹具打包为设备初始配置')
    files={}
    for name in changed+added:
        relative='sorting_demo/'+name
        files[relative]=dict(sha256=sha(ROOT/relative))
        if name in changed:files[relative]['previous_sha256']=sha(BACKUP/name)
    for p in sorted((ROOT/'sorting_demo/models/manipulation').rglob('*')):
        if p.is_file():files[str(p.relative_to(ROOT))]=dict(sha256=sha(p))
    manifest=json.dumps(dict(version=1,files=files,initial_setup_create_only=True,
        synthetic_fixtures_included=False),ensure_ascii=False,indent=2).encode()
    temporary=BACKUP/'package.pending.tar.gz'
    with tarfile.open(temporary,'w:gz') as archive:
        item=tarfile.TarInfo('deployment_manifest.json');item.size=len(manifest)
        archive.addfile(item,io.BytesIO(manifest))
        for name in files:archive.add(ROOT/name,arcname=name,recursive=False)
    checksum=sha(temporary);path=BACKUP/('manipulation_'+checksum[:16]+'.tar.gz')
    temporary.replace(path)
    report=dict(package=str(path),sha256=checksum,files=len(files),bytes=path.stat().st_size,
                deploy_script_sha256=sha(ROOT/'deployment/deploy_manipulation.py'))
    (BACKUP/'package_report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(report,ensure_ascii=False))


if __name__=='__main__':main()
