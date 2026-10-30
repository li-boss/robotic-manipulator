# 即时执行、回读容差与桌面高度来源（2026-10-29 第二次）

## 用户反馈
1. 点击执行后数秒才开始运动。
2. 夹爪偏离方块约2 cm 未夹起（用户确认：偏高、偏向底座）。

## 原因
1. 17:11 实测：17:11:28.3 收到请求 → 40.5 接受（`control.execute` 同步调用 `validate_execution` 完整重算计划约12 s）→ 41.0 执行前读数 → 55.0 首条指令（`run` 中再次完整重算约14 s）。合计约27 s。
2. 桌面高度两套基准不一致：配置用安装板80 mm 推出的 z=0；手眼标定解出的标定板平面约 z=−34～−40（四角高差6.4 mm）。冻结图中用户框出的30 mm 顶面按 z=0 计算仅23.8 mm；目标因此算得偏近、偏高，与用户观察一致。
3. 另：17:12 抬升后 ID2 目标58°回读56°，按1°上限停止。

## 修改
- 规则：AGENTS.md、机械臂操作准则.md 增加用户10-29第二次要求（即时执行、3°回读容差、手眼板面桌面来源）。
- `manipulation_task.py`：预览时为计划记录输入指纹 `plan_inputs` 与 `plan_sha256`；执行核对指纹与审计散列，不重算；随臂完整计划运动后偏差≤3°记 `deviation` 警告、不计验证，>3°停止。
- `charuco_task.py`：板面估计四角高差上限3→10 mm 并记录 `board_corner_spread_mm`；原 `measured_model_datum` 保留为 `superseded_measured_datum`。
- `calibration_ui.js`：提示阈值改为10 mm。

## 验证（假设备）
- 新增测试：执行不调用 `build_plan`/`compute_plan`、计划篡改被拒、2°偏差警告继续、4°停止、6 mm 板面可采用。
- 全部回归：test_manipulation 19、test_dynamic_manipulation 24、test_cad_manipulation 21、test_manipulation_guided 22、test_control 26、test_charuco_calibration 31、test_teaching 29、test_reported_cards 18、test_rotated_annotation 5、test_pickup_only 2、test_ros_client 2、arm_playground 51、四项页面 JS 测试，全部通过。
- 当天请求离线重算（含重建九点参考）：桌面 z=−40.41 时方块框边长计算为30.3 mm（原23.8 mm）；抓取目标由 (−31.2, 248.6, 30) 变为 (−39.8, 267.2, −10.4)；8 步。

## 部署与参数
- 设备备份 `/root/jjt/backups/execute_latency_table_20261029_0ce0c9c1a781`；零舵机读取、零运动；服务已以实机模式重启。
- 经 `/calibration/apply`（手眼 6d03ee15，board_on_table，板厚3 mm，按用户“贴硬纸板”选择取常见值）写入 `table_z_mm=−40.4115`；资料检查通过，九点参考已重建。

## 未验证 / 风险
- 九点示教中真实夹取姿态的模型TCP高度为22～42 mm（越远越高），与板面基准不完全一致，疑似远伸时机械臂下垂或模型误差；远处位置夹取高度可能偏低，需现场观察。
- 板厚3 mm 为假定值。
