# 一个人的机械臂搬运练习

所有操作须遵守[机械臂操作行为准则](../机械臂操作准则.md)。助手每次执行机械臂运动前必须取得当次同意，包括运动重试；基础指令及经审查无运动副作用的角度读取无需逐次确认。角度硬边界：1/2/3/4/6号为0～180°，5号为90～270°；实际配置只能缩小范围，未核对时禁止运动。

先读角度，再逐步验证动作，最后运行固定位置搬运。程序只依赖 Python 标准库；实机模式另外使用设备上现有的 `Arm_Lib`。不需要在 Windows 上安装机械臂驱动库。

## 第一次只做这一步

将整个 `arm_playground` 目录复制到开发板上。在之前成功运行示例的终端和 Python 环境中进入该目录。课程的环境激活命令如下，若你使用的环境不同，沿用已成功的环境：

```bash
conda activate ros-arm-env
cd /实际存放位置/arm_playground
python arm.py --real read
```

预期输出六个角度，对应 1～6 号舵机。该命令不发送运动指令。将完整输出提供给助手，一起判断下一步；若报错，提供报错全文，不用先重装系统或环境。

电脑上也能练习命令：

```bash
python arm.py --help
python arm.py read
python arm.py targets
python -m unittest -v
```

不加 `--real` 就不会导入硬件库。模拟读取显示的六个 90 是虚拟值，不能用于配置真实动作。初始配置中的姿态与范围刻意留空，因此此时模拟搬运也会提示缺配置。

## 理解三种参数

- 单个关节角度：某个舵机的绝对目标角度，不是相对转动量。
- 姿态：1～5 号舵机的五个角度，决定机械臂的姿态；6 号夹爪另行控制。
- 运动时间：下发给舵机的运动用时，单位毫秒；程序还会额外等待稳定时间。等待结束不代表已经确认抓到了物体。

## 逐步配置和验证

编辑 `config.json`。先依据实际设备说明和已经成功的例程填写 `joint_limits`，每个编号使用 `[最小角度, 最大角度]`。教程对5号舵机范围有矛盾，不直接采用教程中的极限值。确认停止方式、线缆余量和当前空载姿态后，才将 `hardware_ready` 改为 `true`。

先用现有、已验证的操作方式把机械臂移动到需要的姿态，再读取保存。本程序的 `capture` 只读取，不会关闭扭矩，也不会带机械臂到该位置。若使用任务5的手动示教，应按教程进入学习模式，注意失去扭矩时支撑机械臂；不要在保持扭矩时强行掰动，不要清空已有动作组。

```bash
python arm.py --real capture ready
python arm.py --real capture pickup_above
python arm.py --real capture pickup
python arm.py --real capture targets.yellow.above
python arm.py --real capture targets.yellow.drop
python arm.py --real capture gripper.open
python arm.py --real capture gripper.closed
```

这些命令是在分别到达各个姿态以后执行的，不能直接连续执行。`capture` 会覆盖对应配置项；重新记录前可以手动备份配置。动作采用整数角度，若读数含小数，应核对后手动调整配置。

| 名称 | 实际含义 |
| --- | --- |
| ready | 空载开始与返回姿态 |
| pickup_above | 积木上方的接近、抬起姿态 |
| pickup | 固定取物姿态 |
| targets.yellow.above | 黄色区域上方的转移姿态 |
| targets.yellow.drop | 黄色区域放置姿态 |
| gripper.open / closed | 经验证的夹爪张开、夹持角度 |

需要单独测试动作时，使用 `pose 名称`、`gripper open` / `gripper closed` 或 `joint 编号 绝对角度` 子命令。先不加 `--real` 查看计划参数，再在核对过当前位置与运动路径后执行实机命令。关节范围检查不能证明中间路径无碰撞；准备姿态也不是从任意位置都能直接到达。

## 第一次完整搬运

填完并验证所有姿态后，先查看模拟流程：

```bash
python arm.py run --target yellow
```

将机械臂置于已验证的空载起始状态，逐步验证空载路径，再放一块积木。使用：

```bash
python arm.py --real run --target yellow --step
```

每一步输入 `y` 才会执行，其他输入中止。完整路径验证后可省略 `--step`；省略 `--target` 时终端会让你选择区域。新增区域时记录 `targets.red.above` 和 `targets.red.drop` 等姿态，并单独验证对应路径。

实机执行结束后程序会问是否真正搬运成功：`y` 成功、`n` 失败、其他输入未确认。夹空、掉落应选择失败。连续三次成功后再增加其他区域。

## 记录、停止和程序边界

- 任务事件写入 `logs/real.jsonl`，模拟写入 `logs/simulation.jsonl`。同一个任务编号会有开始、等待确认和结束事件，查看结果应取该编号最后一条记录，不能按行数统计任务数。
- 遇到异常或 Ctrl+C，不再下发后续指令，不重试、不自动返回。已发出的舵机指令可能继续运动；Ctrl+C 和本项目都不能替代设备实际的急停措施。
- 同一项目目录中的实机命令使用 `.hardware.lock` 互斥。不要同时运行其他 Notebook、示教或另一份项目中的硬件程序，这个锁无法约束外部程序。
- 若断电或强制结束，日志可能只留下开始事件，需人工检查。残留锁仅在确认程序退出、设备停止后手动删除。
- 程序不自动识别夹空、碰撞和堵转，也不进行逆运动学、视觉定位或轨迹规划。本阶段由人观察确认。

固定点搬运稳定后，再依次加入固定点颜色识别、标定与任意位置抓取、真实垃圾识别及看板。
