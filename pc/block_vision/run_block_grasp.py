"""方块识别自动抓取（电脑端运行）。

先在网页 http://192.168.137.100:8080/modes 开启“方块识别自动抓取”，再运行：
    python pc/block_vision/run_block_grasp.py                 # 按 pc/lan/lan_config.json：有 block_detect_url 就用局域网识别服务
    python pc/block_vision/run_block_grasp.py --local         # 忽略配置，用本机检测器（默认示例颜色检测器）
    python pc/block_vision/run_block_grasp.py --local --detector my_model:MyDetector
开发板与识别服务地址写在 pc/lan/lan_config.json（换局域网只改这个文件）；命令行 --board / --detect-url 可临时覆盖。
本地无硬件联调：先 python board/simulate.py，再加 --board http://127.0.0.1:8092 。

程序常驻等待：会话开启后，每当用户在 /modes 点一次“开始识别夹取”，做一次完整流程
（冻结一帧 → 识别 → 提交得分最高的方块 → 等待抓取结束），然后继续等下一次点击。
没找到方块、识别出错、规划未通过都只报告到网页，不自动重试。会话结束后等下一个会话；
--once 处理一次点击后退出。Ctrl+C 只停止本程序并请求结束会话，不能撤回已发出的动作，不是急停。
"""
import argparse
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from armlink import BoardClient, BoardError  # noqa: E402
from armlink.frames import load_class  # noqa: E402
from lan.remote_detector import RemoteDetectError, RemoteDetector  # noqa: E402
from lan import lan_config  # noqa: E402


def run(client, detector, log=print, once=False, save_dir=None, poll_s=0.5):
    """等用户在 /modes 点“开始识别夹取”，每次点击做一次完整流程；不自动重试。"""
    done = set()
    while True:
        mode = client.wait_for_mode('block_grasp', log=log)
        session = mode['session_id']
        log(f"会话 {session}：{mode['task_mode']}，最多 {mode['max_actions']} 次，置信度下限 {mode['min_score']}")
        log('等待在网页 /modes 点击“开始识别夹取”……')
        while True:
            mode = client.mode()
            if not mode['armed'] or mode['session_id'] != session:
                log('会话已结束：' + str(mode.get('message') or '网页已关闭或次数用完'))
                break
            request = mode.get('grasp_request')
            if not request or request in done:
                time.sleep(poll_s)
                continue
            done.add(request)
            log('收到开始识别夹取（' + request[:8] + '）')
            status = attempt(client, detector, session, request, mode['min_score'], log, save_dir)
            if once:
                return status
            log('等待下一次点击……' if status != 'failed' else '本次执行失败，开发板会结束会话')


def attempt(client, detector, session, request, min_score, log, save_dir=None):
    """一次完整的冻结 → 识别 → 提交 → 等待执行结果。返回 not_submitted / rejected / completed / failed。"""
    def report(message):
        log('本次未提交：' + message)
        try:
            client.report_block(session, request, message)
        except BoardError as exc:
            log('（告知开发板失败：' + str(exc) + '）')
        return 'not_submitted'

    try:
        frame = client.freeze_frame(session, request)
    except BoardError as exc:
        log('冻结失败：' + str(exc))
        return 'not_submitted'
    if save_dir:
        Path(save_dir).mkdir(parents=True, exist_ok=True)
        (Path(save_dir) / f'{frame.frame_id}.jpg').write_bytes(frame.jpeg)
    try:
        # 远端识别：转发开发板冻结的原图（不重新压缩）；本地检测器照旧传解码后的图像。
        found = (detector.detect_jpeg(frame.jpeg, frame.frame_id, frame.image_size)
                 if hasattr(detector, 'detect_jpeg') else detector.detect(frame.image))
    except RemoteDetectError as exc:
        return report('识别服务出错：' + str(exc))
    except Exception as exc:  # noqa: BLE001  本地检测器异常同样只结束本次
        return report(f'识别出错：{type(exc).__name__}: {exc}')
    detections = [d for d in found if d.score >= min_score]
    if not detections:
        return report(f'画面中没有找到方块（{len(found)} 个候选，均低于置信度下限 {min_score}）' if found
                      else '画面中没有找到方块')
    best = max(detections, key=lambda d: d.score)
    log(f'识别到方块（{best.label}，score={best.score:.2f}），提交开发板规划……')
    try:
        result = client.submit_block(session, request, frame.frame_id, best)
    except BoardError as exc:
        log('开发板未执行：' + str(exc))
        return 'rejected'
    target = result['target']
    log(f"规划通过（{result['steps']} 步），方块位置 {[round(v, 1) for v in target['xyz_mm']]} mm，正在执行……")
    for warning in result.get('warnings', []):
        log('  提示：' + warning)
    job = client.wait_job(result['job']['id'])
    log(f"任务结束：{job['status']} {job.get('message') or job.get('error') or ''}")
    return 'completed' if job['status'] == 'completed' else 'failed'


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--config', default=str(lan_config.PATH), help='局域网地址配置文件（默认 pc/lan/lan_config.json）')
    parser.add_argument('--board', help='开发板地址（默认取配置文件 board）')
    parser.add_argument('--detector', default='block_vision.color_detector:ColorBlockDetector',
                        help='检测器类，格式 模块:类名（需在 pc/ 目录或 PYTHONPATH 下）')
    parser.add_argument('--detect-url', metavar='URL',
                        help='局域网方块识别服务地址（默认取配置文件 block_detect_url；jjt-detect/1）')
    parser.add_argument('--local', action='store_true', help='不用局域网识别服务，改用本机 --detector')
    parser.add_argument('--save-frames', metavar='DIR', help='把每次冻结的原图存到该目录（方便收集训练数据）')
    parser.add_argument('--once', action='store_true', help='只处理一次“开始识别夹取”点击就退出')
    args = parser.parse_args()
    config = lan_config.load(args.config)
    board = args.board or config['board']
    detect_url = '' if args.local else (args.detect_url if args.detect_url is not None else config['block_detect_url'])
    client = BoardClient(board)
    log = lambda text: print(time.strftime('%H:%M:%S'), text, flush=True)
    log(f'配置：{args.config}；开发板 {board}；识别 ' + (detect_url or '本机 ' + args.detector))
    log('开发板：' + str(client.health()))
    if detect_url:
        detector = RemoteDetector(detect_url, log=log)
        try:
            log('识别服务：' + str(detector.health()))
        except RemoteDetectError as exc:
            log(str(exc) + '（地址不对就改 ' + args.config + ' 的 block_detect_url）');return
    else:
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
