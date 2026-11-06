#!/bin/zsh
# 在本机运行电脑端规划服务（纯计算，不连接机械臂）。前台运行，关闭窗口或 Ctrl+C 即停止；
# 停止后开发板自动改用本地计算。第一次运行会在项目根目录建 .venv-mac 并安装 numpy、opencv-python。
cd "${0:A:h}/../.." || exit 1
PY=.venv-mac/bin/python
if [[ ! -x $PY ]] || ! $PY -c 'import numpy, cv2' 2>/dev/null; then
    print '首次准备 Python 环境（.venv-mac）……'
    /usr/bin/python3 -m venv .venv-mac || exit 1
    $PY -m pip install -q -r pc/requirements.txt || { print '安装依赖失败，请检查网络'; exit 1; }
fi
if /usr/bin/curl -s -m 2 http://127.0.0.1:8095/health | /usr/bin/grep -q jjt_pc_planner; then
    print '电脑端规划服务已在本机运行（端口 8095），无需重复启动。'
    exit 0
fi
if ! /sbin/ifconfig | /usr/bin/grep -q 'inet 192.168.137.101 '; then
    print '提示：本机有线网卡不是 192.168.137.101，开发板默认连不到本服务（会自动改用本地计算）。'
fi
exec $PY -B -u pc/plan_server.py
