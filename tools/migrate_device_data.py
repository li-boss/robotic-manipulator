"""在开发板上把旧目录结构的配置与记录 **复制** 到新的 board/data/。不读写舵机、不删除旧文件。

旧结构（2026-11-06 之前）                         新结构
  /root/jjt/arm_playground/config.json            → /root/jjt/board/data/arm_config.json
  /root/jjt/arm_playground/manual_states.json     → /root/jjt/board/data/manual_states.json
  /root/jjt/arm_playground/logs/web_control.jsonl → /root/jjt/board/data/logs/web_control.jsonl（去重记录）
  /root/jjt/arm_playground/logs/其他              → /root/jjt/board/data/logs/arm_playground/
  /root/jjt/sorting_demo/manipulation_setup.json  → /root/jjt/board/data/
  /root/jjt/sorting_demo/teaching_setup.json      → /root/jjt/board/data/（九点参考）
  /root/jjt/sorting_demo/teaching_cards.json      → /root/jjt/board/data/
  /root/jjt/sorting_demo/charuco_calibration.json → /root/jjt/board/data/
  /root/jjt/sorting_demo/logs/*                   → /root/jjt/board/data/logs/

用法（先看计划，再执行）：
    python3 /root/jjt/tools/migrate_device_data.py            # 只列出将要复制的文件
    python3 /root/jjt/tools/migrate_device_data.py --apply    # 实际复制
目标已存在且内容相同则跳过；内容不同则整体拒绝（不覆盖，请人工核对）。
"""
import argparse
import hashlib
from pathlib import Path
import shutil

ROOT = Path('/root/jjt')


def plan(root):
    old_arm, old_demo, data = root / 'arm_playground', root / 'sorting_demo', root / 'board/data'
    pairs = [(old_arm / 'config.json', data / 'arm_config.json'),
             (old_arm / 'manual_states.json', data / 'manual_states.json'),
             (old_arm / 'logs/web_control.jsonl', data / 'logs/web_control.jsonl')]
    for name in ('manipulation_setup.json', 'teaching_setup.json', 'teaching_cards.json', 'charuco_calibration.json'):
        pairs.append((old_demo / name, data / name))
    if (old_arm / 'logs').is_dir():
        for path in sorted((old_arm / 'logs').rglob('*')):
            if path.is_file() and path.name != 'web_control.jsonl':
                pairs.append((path, data / 'logs/arm_playground' / path.relative_to(old_arm / 'logs')))
    if (old_demo / 'logs').is_dir():
        for path in sorted((old_demo / 'logs').rglob('*')):
            if path.is_file():
                pairs.append((path, data / 'logs' / path.relative_to(old_demo / 'logs')))
    return [(s, d) for s, d in pairs if s.is_file()]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    pairs = plan(args.root)
    if (args.root / 'board/data/.hardware.lock').exists() or (args.root / 'arm_playground/.hardware.lock').exists():
        raise SystemExit('存在硬件锁：请先确认没有程序在控制机械臂、服务已停止。')
    conflicts, todo, same = [], [], 0
    for source, target in pairs:
        if target.exists():
            if digest(source) == digest(target):
                same += 1
            else:
                conflicts.append((source, target))
        else:
            todo.append((source, target))
    for source, target in todo:
        print('复制', source, '→', target)
    print(f'共 {len(todo)} 个待复制，{same} 个已相同')
    if conflicts:
        for source, target in conflicts:
            print('冲突（目标已存在且内容不同）：', source, '≠', target)
        raise SystemExit('有冲突，未复制任何文件。请人工核对后再运行。')
    if not args.apply:
        print('（只预览。确认无误后加 --apply 执行）')
        return
    for source, target in todo:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        if digest(source) != digest(target):
            raise SystemExit('复制后校验失败：' + str(target))
    print('完成。旧目录保持不变；确认新服务正常后可再决定是否归档旧目录。')


if __name__ == '__main__':
    main()
