"""方块识别自动抓取（电脑端运行）。

先在网页 http://192.168.137.100:8080/modes 开启“方块识别自动抓取”，再运行：
    python pc/run_block_grasp.py                                   # 示例颜色检测器
    python pc/run_block_grasp.py --detector my_model:MyDetector    # 换成你们的模型类
本地无硬件联调：先 python board/simulate.py，再加 --board http://127.0.0.1:8092 。

循环：冻结一帧 → detector.detect() → 提交得分最高的方块 → 等待抓取结束 → 下一次。
会话结束（次数用完/超时/失败/网页关闭）后程序退出。Ctrl+C 只停止本程序并请求结束会话，
不能撤回已发出的动作，不是急停。
"""
import argparse
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
from armlink import BoardClient, BoardError, DEFAULT_BOARD  # noqa: E402
from frames import load_class  # noqa: E402


def run(client, detector, log=print, once=False, save_dir=None):
    mode = client.wait_for_mode('block_grasp', log=log)
    session = mode['session_id']
    log(f"会话 {session}：{mode['task_mode']}，最多 {mode['max_actions']} 次，置信度下限 {mode['min_score']}")
    while True:
        mode = client.mode()
        if not mode['armed'] or mode['session_id'] != session:
            log('会话已结束：' + str(mode.get('message') or '网页已关闭或次数用完'))
            return
        if mode['busy']:
            time.sleep(0.5)
            continue
        try:
            frame = client.freeze_frame(session)
        except BoardError as exc:
            log('冻结失败：' + str(exc))
            time.sleep(1)
            continue
        image = frame.image
        if save_dir:
            Path(save_dir).mkdir(parents=True, exist_ok=True)
            (Path(save_dir) / f'{frame.frame_id}.jpg').write_bytes(frame.jpeg)
        detections = [d for d in detector.detect(image) if d.score >= mode['min_score']]
        if not detections:
            log('画面中没有找到方块，1 秒后重试')
            time.sleep(1)
            continue
        best = max(detections, key=lambda d: d.score)
        log(f'识别到方块（{best.label}，score={best.score:.2f}），提交开发板规划……')
        try:
            result = client.submit_block(session, frame.frame_id, best)
        except BoardError as exc:
            log('开发板未执行：' + str(exc))
            time.sleep(1)
            continue
        target = result['target']
        log(f"规划通过（{result['steps']} 步），方块位置 {[round(v, 1) for v in target['xyz_mm']]} mm，正在执行……")
        for warning in result.get('warnings', []):
            log('  提示：' + warning)
        job = client.wait_job(result['job']['id'])
        log(f"任务结束：{job['status']} {job.get('message') or job.get('error') or ''}")
        if job['status'] != 'completed' or once:
            return


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--board', default=DEFAULT_BOARD, help='开发板地址')
    parser.add_argument('--detector', default='block_vision.color_detector:ColorBlockDetector',
                        help='检测器类，格式 模块:类名（需在 pc/ 目录或 PYTHONPATH 下）')
    parser.add_argument('--save-frames', metavar='DIR', help='把每次冻结的原图存到该目录（方便收集训练数据）')
    parser.add_argument('--once', action='store_true', help='只抓一次就退出')
    args = parser.parse_args()
    client = BoardClient(args.board)
    log = lambda text: print(time.strftime('%H:%M:%S'), text, flush=True)
    log('开发板：' + str(client.health()))
    detector = load_class(args.detector)
    try:
        run(client, detector, log=log, once=args.once, save_dir=args.save_frames)
    except KeyboardInterrupt:
        log('Ctrl+C：请求结束会话（已下发的动作无法撤回）')
        try:
            client.stop()
        except BoardError as exc:
            log(str(exc))


if __name__ == '__main__':
    main()
