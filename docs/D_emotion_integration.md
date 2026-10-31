# D 侧识别与 JJT 直接联调

## 当前边界

D 侧包含笑脸、手势和联合识别。笑脸识别可以直接调用 JJT；手势仍只显示本地候选，尚未定义机械臂动作。

## 直接调用流程

1. 笑脸规则生成 32 位十六进制请求号。
2. D 直接读取 JJT `GET /control/status`。
3. 按 `--states` 的顺序查找已保存六关节姿态。
4. 显示实机/模拟模式、姿态名称、六个目标角度、关节顺序和用时。
5. 操作者输入 `EXECUTE <request_id>`，授权本次显示的有限计划。
6. 每一步直接调用 `POST /control/states/execute`，携带当前 CSRF、姿态修订号、姿态记录和独立的幂等请求号。
7. 查询 `/control/status`；状态、配置或执行异常时停止后续步骤，不自动重发。

## 动作配置

D 不保存机械臂角度，也不绕过 JJT 控制层。WAVE/NOD 都由 JJT 网页中已保存的有限姿态组成，例如：

```powershell
.venv\Scripts\python.exe -m emotion_interaction.smile_demo `
  --robot-url http://192.168.137.100:8080 `
  --action WAVE `
  --states "wave_left,wave_right,wave_left,wave_home"
```

重复名称表示重复执行同一已保存姿态。未传两个直连参数时只进行本地识别。

## 环境

D 使用独立 Python 3.13 环境，JJT 使用设备原 Python/ROS 环境。两边直接通过现有 HTTP 接口通信，不共享 Python 解释器或第三方依赖。
