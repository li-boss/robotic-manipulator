# 混合高度与运输段衔接（2026-10-29 第三次）

## 用户反馈
1. 桌面改为手眼板面（z=−40.41）后，夹爪到了桌面以下；用户确认水平位置已对准。要求恢复80 mm。
2. 运动不连贯。

## 原因
1. 机械臂运动学与安装板80 mm 实测基准自洽（九点真实夹取姿态的模型TCP高度也支持），手眼标定的相机模型自身含约40 mm 高度偏差；因此图像换算需在手眼板面求交，而运动高度应使用80 mm 基准。
2. 18:01 记录：每步“运动1500 ms → 停稳300 ms → 读6关节约300 ms → 下一步”，步间停约0.6 s，共7次停顿。

## 修改
- 规则：AGENTS.md、机械臂操作准则.md 增加用户10-29第三次要求。
- `manipulation_geometry.py`：`projection_offset`/`project_to_plane` 在投影板面求交、返回实测基准高度；`blend_check` 检查提前下发切角路径（提前比例0.55/0.8/0.9对应三个切点＋发送滞后），通过的相邻移动段标记 `blend_next_ms=300`。
- `manipulation_task.py`：衔接步以 `hold_ms=用时−提前量` 下发后立即发下一段，不停稳不回读，不计验证；提前量预检。
- `arm_playground/arm.py`：`Controller.write(pairs, hold_ms=None)`，只缩短写入后等待（0～用时+停稳整数毫秒）。
- `charuco_task.py`：`board_projection_only` 采用选项与 `projection_table` 来源核对；`board_plane` 公共计算。
- `manipulation_ui.js`：确认清单显示衔接。

## 验证（假设备）
- 新增测试：hold_ms 校验与等待；衔接步无回读且等待正确；投影板面只改变平面坐标；仅投影采用不改桌面且篡改被拒；规划中衔接仅出现在运输段。
- 全部回归通过：sorting_demo 11 组共 220 项、arm_playground 52 项、4 项页面 JS 测试。
- 18:01 请求离线重算：混合方案取物平面坐标 (−29.2, 266.6) 与本次实测对准的结果一致，方块边长计算30.1 mm，取物顶面高度回到 z=30；8 步、3 处衔接、总时长 12.6 s。

## 部署与参数
- 设备备份 `/root/jjt/backups/blend_projection_20261029_6a85aee56620`；零舵机读取、零运动；服务已实机模式重启。
- 经 `/calibration/table-height` 用原记录恢复 80 mm 实测基准（table_z_mm=0）；经 `/calibration/apply`（board_projection_only，板厚3 mm）写入投影板面 z=−40.4115。资料检查通过，九点参考已重建。

## 未验证
- 衔接按“线性插值＋发送滞后”采样检查，舵机实际减速曲线未实测；衔接段不回读。
- 远距离夹取可能受机械臂下垂影响偏低，需现场观察。
