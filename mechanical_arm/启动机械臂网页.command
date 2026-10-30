#!/bin/zsh
# 明确启用实机网页；仅启动服务，不发送任何机械臂运动。
cd "${0:A:h}" || exit 1
print '启动实机网页和 ROS2 纯计算服务。运动必须在网页上当次确认。'
/usr/bin/python3 -B mac_tools/service.py --start --real
result=$?
if (( result == 0 )); then
    /usr/bin/open 'http://192.168.137.100:8080'
else
    print '未完成启动检查。请查看上方错误；不会自动重试或停止现有服务。'
fi
read -r 'reply?按回车关闭此窗口：'
exit "$result"
