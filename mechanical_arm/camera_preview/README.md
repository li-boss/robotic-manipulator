# 摄像头预览

设备目录：`/root/jjt/camera_preview`。仅使用 OpenCV 采集摄像头，不导入机械臂控制库。

在已安装 OpenCV 的环境运行：

```bash
cd /root/jjt/camera_preview
/root/miniconda3/envs/ros-arm-env/bin/python -B server.py
```

电脑访问 http://192.168.137.100:8080 。不需要外网，也不依赖 CDN。默认 `/dev/video0`、请求640×480、预览约不超过10帧/秒；实际分辨率显示在页面。通过 `--device /dev/video2` 可选择其他摄像头。

摄像头不存在或断开时显示错误，后台每2秒重新尝试。多个浏览器共享同一采集线程。暂停仅暂停浏览器显示，不关闭服务器采集。此局域网页面无登录功能，不配置外网端口转发。

后台启动（不要重复启动）：

```bash
nohup /root/miniconda3/envs/ros-arm-env/bin/python -u -B server.py >server.log 2>&1 < /dev/null &
echo $! >server.pid
```

停止前先用 `ps -fp "$(cat server.pid)"` 确认是本目录的预览进程，然后 `kill "$(cat server.pid)"`。后台日志为 `server.log`，重启开发板后需重新启动服务。
