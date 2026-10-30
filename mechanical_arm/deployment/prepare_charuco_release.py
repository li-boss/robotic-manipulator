"""Prepare reviewed source/runtime archives locally. Never connects to hardware."""
import hashlib
import argparse
import io
import json
from pathlib import Path
import tarfile

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'implementation_backups/charuco_20261024'
FILES=('server.py','manipulation_task.py','manipulation_geometry.py','manipulation.html','cad_geometry.py',
       'charuco_geometry.py','charuco_task.py','charuco_worker.py','calibration.html',
       'calibration_ui.js','相机标定使用说明.md')


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def package(path, files):
    definition={name:dict(sha256=sha(source)) for name,source in files.items()}
    if path.name.startswith('code'):
        for name in definition:
            old=OUT/'before'/name
            if old.exists():definition[name]['previous_sha256']=sha(old)
    raw=json.dumps(dict(version=1,files=definition,hardware_actions=0),ensure_ascii=False,indent=2).encode()
    with tarfile.open(path,'w:gz') as archive:
        item=tarfile.TarInfo('deployment_manifest.json');item.size=len(raw);archive.addfile(item,io.BytesIO(raw))
        for name,source in files.items():archive.add(source,arcname=name,recursive=False)
    return dict(path=str(path),sha256=sha(path),files=len(files),bytes=path.stat().st_size)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--final-code-only',action='store_true')
    args=parser.parse_args()
    source={'sorting_demo/'+name:ROOT/'sorting_demo'/name for name in FILES}
    if args.final_code_only:
        report=dict(code=package(OUT/'code_final.tar.gz',source),motor_commands=0,existing_configuration_included=False)
        (OUT/'final_package_report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
        print(json.dumps(report,ensure_ascii=False,indent=2));return
    files={str(p.relative_to(OUT/'runtime/site-packages')):sha(p)
           for p in sorted((OUT/'runtime/site-packages').rglob('*')) if p.is_file() and '__pycache__' not in p.parts and p.suffix!='.pyc'}
    (OUT/'runtime/manifest.json').write_text(json.dumps(dict(version=1,opencv='4.11.0.86',
        distribution='opencv-contrib-python-headless',platform='aarch64-manylinux_2_17',files=files),indent=2)+'\n')
    runtime={'calibration_runtime/manifest.json':OUT/'runtime/manifest.json'}
    runtime.update({'calibration_runtime/site-packages/'+name:OUT/'runtime/site-packages'/name for name in files})
    report=dict(code=package(OUT/'code.tar.gz',source),runtime=package(OUT/'runtime.tar.gz',runtime),
        motor_commands=0,existing_configuration_included=False)
    (OUT/'package_report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
