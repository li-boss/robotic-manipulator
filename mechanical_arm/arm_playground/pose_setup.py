"""固定点抓放姿态配置。只读硬件和保存人工核对的参数，不发送运动。"""
import argparse
from contextlib import nullcontext
from datetime import datetime
import json
from pathlib import Path
import shutil
import sys
import uuid

import arm
from joint_check import audit

ROOT = Path(__file__).resolve().parent
POSE_NAMES = ('ready', 'pickup_above', 'pickup', 'targets.yellow.above', 'targets.yellow.drop',
              'gripper.open', 'gripper.closed')


def required(config):
    missing = []
    for name in POSE_NAMES:
        try:
            if name.startswith('gripper.'):
                arm.angle(config, 6, config.get('gripper', {}).get(name.split('.')[1]))
            else:
                arm.get_pose(config, name)
        except ValueError as exc:
            missing.append(dict(name=name, reason=str(exc)))
    return missing


def save_config(path, config, expected_bytes):
    if path.read_bytes() != expected_bytes:
        raise RuntimeError('配置在记录期间被其他程序修改，拒绝覆盖，请重新核对')
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    backup = path.with_name(path.name + '.before_pose_' + stamp)
    shutil.copy2(path, backup)
    temporary = path.with_name(path.name + '.pose_tmp')
    temporary.write_text(json.dumps(config, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)
    return backup


def capture(controller, config, path, name, manual=None, ask=input):
    if not controller.real:
        raise ValueError('姿态记录必须显式--real，禁止保存模拟读数')
    if name not in POSE_NAMES:
        raise ValueError('未知姿态名称')
    original = path.read_bytes()
    rows = controller.read_report()
    is_grip = name.startswith('gripper.')
    selected = rows[5:] if is_grip else rows[:5]
    sample = dict(sample_id=str(uuid.uuid4()), captured_at=arm.now(), name=name,
                  measured_joints=rows, manual_targets=manual, status='observed_only')
    log = ROOT / 'logs/pose_samples.jsonl'
    arm.append_event(log, sample)  # 保留原始读数，即使无法生成合法目标。
    print(json.dumps(sample, ensure_ascii=False, indent=2))
    if not all(row['read_ok'] for row in rows):
        raise RuntimeError('读取不完整，不能记录姿态；未重试、未运动')
    values = manual if manual is not None else [row['angle'] for row in selected]
    if not isinstance(values, list) or len(values) != (1 if is_grip else 5):
        raise ValueError('夹爪须一个目标角度，姿态须五个目标角度')
    for row in selected:
        arm.observed_angle(config, row['id'], row['angle'])
    checked = [arm.angle(config, row['id'], value) for row, value in zip(selected, values)]
    if manual is not None:
        print('采用你明确填写的目标，不自动修正读数。须核对这些目标对应当前姿态。')
    print(f'拟保存{name}：{checked}；本命令不执行这些角度，不认定路径已验证。')
    if ask('当前位置确实是所命名姿态且目标已核对？输入 y 保存，其他输入取消：').strip().lower() != 'y':
        sample['status'] = 'cancelled'
        arm.append_event(log, sample)
        return None
    updated = json.loads(json.dumps(config))
    if is_grip:
        updated.setdefault('gripper', {})[name.split('.')[1]] = checked[0]
    elif name.startswith('targets.'):
        _, color, kind = name.split('.')
        updated.setdefault('targets', {}).setdefault(color, {})[kind] = checked
    else:
        updated.setdefault('poses', {})[name] = checked
    backup = save_config(path, updated, original)
    sample.update(status='saved', saved_targets=checked, backup=str(backup))
    arm.append_event(log, sample)
    print(f'已保存；原配置备份：{backup}。未运动，未标记路径验证通过。')
    return sample


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--real', action='store_true')
    parser.add_argument('--config', type=Path, default=ROOT / 'config.json')
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('status', help='检查尚缺哪些姿态，不连接硬件')
    record = sub.add_parser('capture', help='只读当前姿态；不要在不同命名下重复记录同一位置')
    record.add_argument('name', choices=POSE_NAMES)
    record.add_argument('--angles', type=int, nargs='+', help='显式人工核对目标；姿态五个，夹爪一个，不自动补1度')
    args = parser.parse_args(argv)
    audit(args.config, dict(command=args.command, real=args.real, name=getattr(args, 'name', None),
                           angles=getattr(args, 'angles', None)), script_path=__file__)
    config = json.loads(args.config.read_text(encoding='utf-8-sig'))
    if args.command == 'status':
        missing = required(config)
        print(json.dumps(dict(missing=missing, all_targets_configured=not missing,
                              path_verified=False, message='参数齐全不代表路径已通过'), ensure_ascii=False, indent=2))
        return
    if not args.real:
        raise ValueError('capture须显式--real；默认模式不连接硬件')
    with arm.hardware_lock():
        capture(arm.Controller(config, real=True), config, args.config, args.name, args.angles)


if __name__ == '__main__':
    try:
        main()
    except (Exception, KeyboardInterrupt) as exc:
        print(f'未完成：{exc}；本工具不发送运动、扭矩或复位指令。')
        sys.exit(1)
