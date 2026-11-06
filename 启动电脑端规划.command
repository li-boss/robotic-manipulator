#!/bin/zsh
# 只启动本机电脑端规划服务（纯计算，不连接机械臂、不发送运动）。关闭此窗口即停止。
cd "${0:A:h}" || exit 1
print '启动电脑端规划服务：开发板生成抓放计划时交给本机计算，再由开发板复核。'
print '关闭此窗口或按 Ctrl+C 停止；停止后开发板自动改用本地计算。'
zsh tools/mac/plan_server.zsh
result=$?
read -r 'reply?按回车关闭此窗口：'
exit "$result"
