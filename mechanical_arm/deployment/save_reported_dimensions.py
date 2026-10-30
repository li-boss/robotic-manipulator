"""只保存用户报告的桌面高度及尺寸资料；无控制、取帧、舵机读写或服务重启。

在/root/jjt内运行，经原settings入口保存一个字段，由服务备份并撤销旧预览。
尺寸资料从同目录的受散列保护清单复制；只允许清单列明的四个数据/文档文件。
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import urllib.request

ROOT = Path('/root/jjt')
URL = 'http://192.168.137.100:8080'
ALLOWED = {'sorting_demo/现场已知尺寸.json', 'sorting_demo/现场测量与验收清单.md',
           'sorting_demo/人工完整抓放说明.md', 'sorting_demo/models/manipulation/dimensions.json'}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def get(path):
    with urllib.request.urlopen(URL + path, timeout=10) as response:
        return json.load(response)


def main():
    stage = Path(sys.argv[1]).resolve()
    if ROOT / 'staging' not in stage.parents:
        raise ValueError('暂存路径须在/root/jjt/staging内')
    manifest = json.loads((stage / 'update_manifest.json').read_text())
    if set(manifest['files']) != ALLOWED:
        raise ValueError('文件范围不符合本次审阅')
    for name in ('AGENTS.md', '机械臂操作准则.md'):
        print('规则核对', name, sha((ROOT / name).read_bytes()), flush=True)
    print('仅保存table_z_mm=-16及尺寸文档；不读舵机、不运动、不重启服务、不重试。', flush=True)
    model = (ROOT / 'sorting_demo/models/course/dofbot.urdf').read_bytes()
    if sha(model) != '9e0e66c68a08f3e2318bf2256f63a8132f21ded68efc0cc3933707e393ce091e':
        raise ValueError('模型已变化，未保存')
    for relative, info in manifest['files'].items():
        if sha((stage / relative).read_bytes()) != info['sha256']:
            raise ValueError('暂存文件散列不符')
        target = ROOT / relative
        if target.exists() and sha(target.read_bytes()) not in (info['sha256'], info.get('previous_sha256')):
            raise ValueError('已有用户修改，保留文件：' + relative)
    state = get('/control/status')
    if state['busy']:
        raise ValueError('任务正在执行，未保存')
    current = get('/manipulation/status')
    cfg = current['setup']
    if cfg['table_z_mm'] not in (None, -16):
        raise ValueError('现场已有不同桌面高度，未覆盖')
    if cfg['model_reviewed'] or cfg['calibration_reviewed'] or cfg['path_validations']:
        raise ValueError('现场已标定或验证，未覆盖')
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ')
    backup = ROOT / 'backups' / ('reported_dimensions_' + stamp)
    backup.mkdir(parents=True)
    affected = ALLOWED | {'sorting_demo/manipulation_setup.json'}
    for relative in affected:
        path = ROOT / relative
        if path.exists():
            copy = backup / relative
            copy.parent.mkdir(parents=True, exist_ok=True)
            copy.write_bytes(path.read_bytes())
    protected = list((ROOT / 'sorting_demo').glob('*.json')) + list((ROOT / 'arm_playground').glob('*.json'))
    protected = {str(p): sha(p.read_bytes()) for p in protected if str(p.relative_to(ROOT)) not in affected}
    original = json.loads(json.dumps(cfg))
    cfg['table_z_mm'] = -16.0
    fields = dict(setup=cfg, expected_sha256=current['setup_sha256'], csrf=state['csrf'])
    request = urllib.request.Request(URL + '/manipulation/settings',
        data=json.dumps(fields).encode(), headers={'Content-Type': 'application/json', 'Origin': URL})
    with urllib.request.urlopen(request, timeout=15) as response:
        saved = json.load(response)
    if saved['setup'] != cfg:
        raise ValueError('保存返回值与预期不符，请查看备份；不自动重试')
    for relative, info in manifest['files'].items():
        target = ROOT / relative
        if target.exists() and sha(target.read_bytes()) not in (info['sha256'], info.get('previous_sha256')):
            raise ValueError('复制前发现新修改，保留现场文件')
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + '.reported_tmp')
        temporary.write_bytes((stage / relative).read_bytes())
        temporary.replace(target)
    if any(sha(Path(p).read_bytes()) != value for p, value in protected.items()):
        raise ValueError('其他配置资料变化，请核对现场；不自动覆盖')
    after = get('/control/status')
    report = dict(backup=str(backup), changed_setup_fields=[k for k in cfg if cfg[k] != original[k]],
        table_z_mm=cfg['table_z_mm'], execution_ready=saved['execution_readiness']['ready'],
        hardware_reads=0, motor_commands=0, control_job_unchanged=after['job'] == state['job'],
        files={name:sha((ROOT/name).read_bytes()) for name in ALLOWED}, protected_json_unchanged=True)
    (backup / 'update_report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(report, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
