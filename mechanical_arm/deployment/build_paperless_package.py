"""本地组装无纸模式更新包；设备资料不打包覆盖，测试夹具不部署。"""
import ast
import hashlib
import io
import json
from pathlib import Path
import tarfile

ROOT=Path(__file__).resolve().parent.parent
OUTPUT=ROOT/'implementation_backups/paperless_20261016'


def sha(data):return hashlib.sha256(data).hexdigest()


def main():
    previous=json.loads((ROOT/'implementation_backups/manipulation_20261016/device_deployment_report.json').read_text())['files']
    previous.update(json.loads((ROOT/'implementation_backups/manipulation_20261016/reported_dimensions_update_report.json').read_text())['files'])
    names=['control.py','server.py','ros_planner.py','start_service.py','manipulation_geometry.py',
        'manipulation_task.py','manipulation_ui.js','manipulation_view.js','manipulation.html','cad_geometry.py',
        'inspect_camera_parameters.py','manipulation_setup.json','现场测量与验收清单.md',
        '人工完整抓放说明.md','无标定纸方案与参数来源.md','models/manipulation/cad_profile.json',
        'models/manipulation/dimensions.json']
    initial=json.loads((ROOT/'sorting_demo/manipulation_setup.json').read_text())
    if initial.get('cad') is not None or initial['model_reviewed'] or initial['calibration_reviewed'] or initial['path_validations'] or initial['camera']['matrix'] is not None:
        raise ValueError('禁止把活动候选、实测标志或合成相机资料打包成初始配置')
    files={}
    for name in names:
        relative='sorting_demo/'+name;data=(ROOT/relative).read_bytes()
        if name.endswith('.py'):ast.parse(data,filename=name)
        if name.endswith('.json'):json.loads(data)
        info=dict(sha256=sha(data))
        if relative in previous:info['previous_sha256']=previous[relative]
        files[relative]=info
    manifest=json.dumps(dict(version=1,files=files,initial_setup_create_only=True,
        synthetic_fixtures_included=False,estimated_cad_asset_is_not_measured_calibration=True),ensure_ascii=False,indent=2).encode()
    temporary=OUTPUT/'paperless.pending.tar.gz'
    with tarfile.open(temporary,'w:gz') as archive:
        item=tarfile.TarInfo('deployment_manifest.json');item.size=len(manifest)
        archive.addfile(item,io.BytesIO(manifest))
        for relative in files:archive.add(ROOT/relative,arcname=relative,recursive=False)
    digest=sha(temporary.read_bytes());path=OUTPUT/('paperless_'+digest[:16]+'.tar.gz');temporary.replace(path)
    report=dict(package=str(path),sha256=digest,files=len(files),bytes=path.stat().st_size,
        deploy_helper=str(ROOT/'deployment/deploy_manipulation.py'),
        deploy_helper_sha256=sha((ROOT/'deployment/deploy_manipulation.py').read_bytes()),
        deployed=False,device_report='用户确认开发板离线；本轮不运行设备部署或运动')
    (OUTPUT/'package_report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(report,ensure_ascii=False))


if __name__=='__main__':main()
