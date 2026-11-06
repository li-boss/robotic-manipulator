#!/bin/zsh
# 明确启用实机网页；仅启动服务，不发送任何机械臂运动。随后在本窗口运行电脑端规划服务（纯计算）。
cd "${0:A:h}" || exit 1
print '启动实机网页和 ROS2 纯计算服务。运动必须在网页上当次确认。'
/usr/bin/python3 -B tools/mac/service.py --start --real
result=$?
if (( result == 0 )); then
    /usr/bin/open 'http://192.168.137.100:8080'
    print ''
    print '接着在本窗口运行电脑端规划服务（让“生成计划”更快）。保持窗口打开；关闭窗口只停止电脑端规划，开发板自动改用本地计算，网页服务不受影响。'
    zsh tools/mac/plan_server.zsh
else
    print '未完成启动检查。请查看上方错误；不会自动重试或停止现有服务。'
fi
read -r 'reply?按回车关闭此窗口：'
exit "$result"
