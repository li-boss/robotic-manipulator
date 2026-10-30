"""六关节手动检查：默认模拟，只读六关节或经确认移动一个关节一次。"""
import argparse
from contextlib import nullcontext
import hashlib
import json
from pathlib import Path
import sys
import uuid

import arm

ROOT = Path(__file__).resolve().parent


def audit(config_path, arguments, script_path=None):
    """每次运行重新读取规则及实际源码，报告版本和参数；不代表硬件已安全。"""
    for path in (ROOT.parent / 'AGENTS.md', ROOT.parent / '机械臂操作准则.md',
                 Path(script_path).resolve() if script_path else Path(__file__).resolve(), ROOT / 'arm.py', config_path):
        data = path.read_bytes()
        if not data:
            raise ValueError(f'规则/源码/配置为空：{path}')
        print(f'本次核对 {path.name} SHA256={hashlib.sha256(data).hexdigest()[:16]}')
    print('实际参数：' + json.dumps(arguments, ensure_ascii=False))
    print('软件约束：默认模拟；统一校验；单关节单次运动；异常不重试、不复位、不切换扭矩。')
    print('这不证明实机姿态或路径安全；运动必须由操作者当次确认。')


def read_once(controller, log_path):
    rows = controller.read_report()
    event = dict(check_id=str(uuid.uuid4()), recorded_at=arm.now(),
                 mode='real' if controller.real else 'simulation', operation='read', joints=rows)
    arm.append_event(log_path, event)
    for row in rows:
        if row['read_ok']:
            note = '' if row['within_motion_hard_limits'] else '（超出项目运动硬边界，仅记录）'
            print(f"ID {row['id']}：{row['angle']}° {note}")
        else:
            print(f"ID {row['id']}：读取失败：{row['error']}")
    if not controller.real:
        print('以上为模拟读数，不能作为实机角度或工作范围。')
    if not all(row['read_ok'] for row in rows):
        raise RuntimeError('角度读取不完整，未重试、未发送运动指令；请先解决通信问题')
    return rows


def test_once(config, joint, target, real, log_path, ask=input, factory=arm.Controller):
    # 全部目标和时间在任何硬件初始化之前验证，不填充未知工作范围。
    target = arm.angle(config, joint, target)
    duration, settle = arm.timing(config)
    if real and config.get('hardware_ready') is not True:
        raise ValueError('请先核对停止方式、工作范围、起始姿态和线缆，再人工设置hardware_ready=true')
    controller = factory(config, real=real)
    rows = read_once(controller, log_path)
    row = next(row for row in rows if row['id'] == joint)
    # 原样保留回读；1°观察容差不进入目标角度校验，更不自动补发纠正。
    current = arm.observed_angle(config, joint, row['angle'])
    event = dict(check_id=str(uuid.uuid4()), mode='real' if real else 'simulation', operation='test',
                 joint=joint, current_angle=current, target_angle=target,
                 motion_ms=duration, settle_ms=settle, moves=1, retry_count=0,
                 started_at=arm.now(), ended_at=None, status='awaiting_confirmation',
                 observed_result=None, read_after=None, error=None)
    event['readback_tolerance_deg'] = arm.READBACK_TOLERANCE_DEG
    arm.append_event(log_path, event)
    print(f"{'实机' if real else '模拟'}计划：仅ID {joint}，{current}° → {target}°，"
          f'绝对目标；运动{duration} ms，额外等待{settle} ms；共1次，不自动返回。')
    print('起始条件：空载、姿态与路径已核对、线缆无牵拉、夹爪无物体/手指、仅一个控制来源。')
    print('停止：确认前输入非y取消；下发后Ctrl+C只阻止后续指令，已发运动可能继续。')
    print('必须掌握设备已确认的实际停止方式，不能用此脚本代替硬件急停。')
    print('起始读数允许配置边缘1°偏差；目标仍严格校验，读数不加1°、不截断、不自动纠正。')
    try:
        if ask('确认起始条件与停止方式，并批准上述单次操作？输入 y 执行：').strip().lower() != 'y':
            event['status'] = 'cancelled'
            return event
        # Controller.write再次核对整批目标及时间。这里只传一个关节，绝不直接调用硬件库。
        event['status'] = 'sending'
        arm.append_event(log_path, event)  # 日志不可写时不下发。
        controller.write([(joint, target)])
        event['status'] = 'awaiting_observation'
        arm.append_event(log_path, event)
        after = controller.read_report()  # 只读取一次，不据偏差纠正、不补发。
        selected = next(item for item in after if item['id'] == joint)
        event['read_after'] = after
        if not selected['read_ok']:
            raise RuntimeError(f"ID {joint} 回读失败：{selected['error']}；不再下发")
        print(f"目标{target}°，回读{selected['angle']}°；差值仅记录，不自动纠正。")
        if real:
            answer = ask('目视观察该关节是否正常？y=正常，n=异常，其他=未确认：').strip().lower()
            event['observed_result'] = {'y': 'normal', 'n': 'abnormal'}.get(answer, 'unconfirmed')
            if answer == 'n':
                event['error'] = ask('异常现象（不动/抖动/碰撞趋势/异响等）：').strip() or '人工报告异常'
        else:
            event['observed_result'] = 'simulated'
        event['status'] = 'completed'
        return event
    except BaseException as exc:
        event.update(status='aborted', error=f'{type(exc).__name__}: {exc}')
        raise
    finally:
        event['ended_at'] = arm.now()
        arm.append_event(log_path, event)
        print(f"测试结束：{event['status']}；日志：{log_path}；无自动返回、松爪或重试。")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--real', action='store_true', help='显式连接实机；默认模拟')
    parser.add_argument('--config', type=Path, default=ROOT / 'joint_check_config.json')
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('read', help='一次读取六关节，不运动')
    move = sub.add_parser('test', help='一次测试一个关节，确认后仅移动一次')
    move.add_argument('--joint', type=int, required=True, choices=range(1, 7))
    move.add_argument('--angle', type=int, help='绝对目标角度整数；省略时交互输入')
    args = parser.parse_args(argv)
    audit(args.config, dict(command=args.command, real=args.real,
                           joint=getattr(args, 'joint', None), angle=getattr(args, 'angle', None),
                           config=str(args.config)))
    config = json.loads(args.config.read_text(encoding='utf-8-sig'))
    if args.command == 'test' and args.angle is None:
        args.angle = int(input(f'输入ID {args.joint}本次绝对目标角度（整数，无默认值）：'))
        print(f'本次补充参数：ID={args.joint}，目标={args.angle}°')
    log = ROOT / 'logs' / ('joint_check_real.jsonl' if args.real else 'joint_check_simulation.jsonl')
    # 实机读和写均复用同一硬件锁；本脚本无法约束外部Notebook。
    with arm.hardware_lock() if args.real else nullcontext():
        if args.command == 'read':
            read_once(arm.Controller(config, real=args.real), log)
        else:
            test_once(config, args.joint, args.angle, args.real, log)


if __name__ == '__main__':
    try:
        main()
    except (Exception, KeyboardInterrupt) as exc:
        print(f'已中止：{type(exc).__name__}: {exc}；不重试、不回零、不松爪。')
        print('Ctrl+C不等同硬件急停，已发指令可能继续运动。')
        sys.exit(1)
