# D 侧识别与 JJT 直接联调

## 当前边界

D 侧包含笑脸、手势和联合识别。笑脸识别可以直接调用 JJT；手势仍只显示本地候选，尚未定义机械臂动作。

## 直接调用流程

1. 笑脸规则生成 32 位十六进制请求号、触发时间和过期时间（默认 30 秒）。
2. D 直接读取 JJT `GET /control/status`。
3. 按 `--states` 的顺序查找已保存六关节姿态。
4. 显示实机/模拟模式、姿态名称、六个目标角度、关节顺序和用时。
5. 操作者输入 `EXECUTE <request_id>`，授权本次显示的有限计划。
6. 每一步先查询 `GET /control/jobs/<step_request_id>`；已有任务只读查询，不重新下发。不存在才检查有效期和状态，调用 `POST /control/states/execute`，携带当前 CSRF、姿态修订号、姿态记录、独立幂等请求号及 `interaction` 触发上下文。
7. 服务端在接单时、取得硬件锁后及每个关节下发前检查有效期和设备状态。忙碌、夹物、故障、停止、状态未知或过期均停止后续下发。已发出的运动不能撤回，不自动复位或松爪。
8. 按步骤请求号查询历史 job；流程结束输出 `COMMANDS_SENT` 与 `motion_verified=false`，不能把下发完成当作真实到位。丢失响应、网络超时和重启后未完成记录为 `UNKNOWN`，保留原请求号，不盲重发。

## 动作配置

D 不保存机械臂角度，也不绕过 JJT 控制层。WAVE/NOD 都由 JJT 网页中已保存的有限姿态组成，例如：

```powershell
.venv\Scripts\python.exe -m emotion_interaction.smile_demo `
  --robot-url http://192.168.137.100:8080 `
  --action WAVE `
  --states "wave_left,wave_right,wave_left,wave_home"
```

重复名称表示重复执行同一已保存姿态。未传两个直连参数时只进行本地识别。

### 本地检查结果与待提供资料

本地没有 `mechanical_arm/arm_playground/manual_states.json`，未发现已保存的 `wave_*` 或 `nod_*` 实测姿态。仓库 `arm_playground/config.json` 仍为 `hardware_ready=false`、六关节范围为空，不能当作设备的实际配置。不会从教程填角度或覆盖设备配置。

首次实机联调仍需操作者和 B 提供：

- 实际服务地址，以及设备端服务代码版本；两端都须包含本次接口扩展。
- 设备上真实保存的姿态名称、顺序、角度、用时和经现场确认的空载路径；WAVE/NOD 是操作者给序列的语义标签，程序不自动推断动作。
- 真实夹物/故障/停止状态的读取来源与时间戳；如果设备没有这些信号，需要如实说明，并确定可靠的采集方式，当前实机互动将拒绝执行。
- 实测关节限制、硬件就绪配置、停止方式，以及对电脑/设备时钟的核对。不要把配置文件和已保存姿态直接覆盖为仓库默认值。

### B 侧设备状态采样契约

`Demo(..., interaction_state_provider=callback)` 将采样函数传给 `Control`。此函数只读，不得触发运动；返回对象必须包含：

| 字段 | 要求 |
| --- | --- |
| `source` | 明确的实际驱动/采样来源；实机不接受 `simulation` 或缺失来源 |
| `observed_at` | 设备端 Unix 毫秒；采样年龄必须在 0～500 ms 内 |
| `holding` | 真实是否夹物，严格布尔值；不得由一个关节角度猜测 |
| `fault` | 真实故障状态，严格布尔值 |
| `stopped` | 停止已请求、停止中或已停止时应为 true；严格布尔值 |

任一字段未知、采样失败或过旧则拒绝互动。默认实机没有此 callback，`interaction_safety.allowed=false`、原因 `DEVICE_STATE_UNKNOWN`；没有提供伪造安全状态的开关。默认模拟明确返回 `source=simulation`，只用于离线验证。

`busy` 仍由控制层现有任务锁维护，接单时在同一个控制锁内检查，最终每次硬件写入前还会读取上述真实状态。callback 接入不代替硬件急停，也不能保证已发出的运动停止。原手动控制接口行为保留。

### 协议与恢复

- 触发字段：`request_id/action/confidence/detected_at/expires_at`，不是旧方案的六字段对象。有效期大于 0 且不超过 120000 ms，通过 `--request-ttl-ms` 配置，客户端和服务端均校验。确认后不会刷新有效期；执行期间到期也停止剩余下发，长序列须提前留足时间预算。
- `GET /control/status` 新增 `interaction_api_version=1` 和 `interaction_safety`。旧服务被客户端明确拒绝，不静默降级。
- `POST /control/states/execute` 保留既有字段，新增可选 `interaction`；D 直连必须携带完整触发对象。服务端将其与姿态、步骤请求号一并绑定、审计和持久化。原网页手动按钮不携带该字段，仍按原确认规则执行。
- `GET /control/jobs/<request_id>` 返回 `{mode, job}`；未找到为 `job=null`，查询不启动任务。使用原审计日志恢复历史，接单但未记录终态的任务为 `unknown`，不会重启重跑。
- 对历史已知任务的查询不受触发过期影响；只读恢复结果不重新执行。若序列仅部分完成，剩余步骤仍受原有效期约束，不自动创建新 ID 继续。
- 服务端保留原网页使用的 `job.status=completed`，并新增 `completion_kind=commands_sent`、`motion_verified=false`。D 的最终 `COMMANDS_SENT` 不代表现场到位或路径验收成功。异常不改成成功，通信结果不明不直接判机械动作失败。

### 本次离线验证与部署范围

测试在内存中调用真实 HTTP handler，覆盖 Origin/CSRF、真实姿态字段校验、过期、状态变化、未知状态拒绝、逐关节停止、历史任务、重启去重及丢失响应恢复。控制器和硬件锁全部注入假实现，测试角度只存在于临时目录，不连接设备、不写真实姿态。

本次只修改本地源码和文档，未部署、未启动设备服务、未运动、未提交推送。将来部署须同时更新 D 侧与 B 侧 `control.py/server.py/interaction_guard.py`，保留设备已有配置、姿态与日志；不能只复制 `control.py` 而漏掉新模块。已有 Mac 启动工具使用源码散列清单，设备更新后需由 B 重新审阅并更新清单，不能绕过校验。历史部署脚本针对旧补丁，不作为本次完整部署入口。

Windows 本地离线检查（仓库根目录）：

```powershell
# 让规划子进程也使用 UTF-8；只影响当前终端启动的 Python。
$env:PYTHONUTF8 = '1'
.venv\Scripts\python.exe -m integration_tools.contract_tests
.venv\Scripts\python.exe -m unittest discover -s mechanical_arm/sorting_demo -p test_control.py -q
```

额外修复 Windows 检出时的 URDF 换行问题：`.gitattributes` 为课程 `dofbot.urdf` 固定 LF，本地恢复为仓库原始字节，SHA256 为 `9e0e66c68a08f3e2318bf2256f63a8132f21ded68efc0cc3933707e393ce091e`，与 CAD 资料一致。没有改变几何参数或放宽版本校验。

2026-11-03 离线验收：D 侧 48 项测试通过；B 控制层 27 项测试通过。另尝试了 B 的 246 项完整回归，发现当前 D 虚拟环境的 OpenCV 5.0.0 没有 `cv2.calibrateHandEye`，导致手眼标定用例报错；不安装或替换现有视觉依赖，不用假计算结果让测试通过。这部分需要在 B 的标定环境验证，不能报告完整回归全部通过。旧 CAD 测试向未纳入 Git 的备份目录写调试文件的副作用已移除，不改变抓放断言或生产规划代码。

## 环境

D 使用独立 Python 3.13 环境，JJT 使用设备原 Python/ROS 环境。两边直接通过现有 HTTP 接口通信，不共享 Python 解释器或第三方依赖。
