# 同步运动与读数失败例外（2026-10-29）

## 用户反馈
1. 框选抓放确认执行后机械臂不动。
2. 一次抓放25步、关节依次运动，要求合并为同步运动、流程流畅。

## 原因
1. 16:44:58 执行被接收后，执行前六关节读取中 ID2、ID3 返回 I2C 读取失败，按原规则“读取不完整禁止执行”停止，0 条运动指令。另：执行前读数与预览比较原为完全相等，夹爪读数在 89/90 间跳动也会导致拒绝。
2. 规划器假定 `write` 不同步（`auto_geometry.Robot.transition`），每阶段拆成逐关节指令；8 个阶段因此展开为 25 步，45 s。

## 修改
- 规则：AGENTS.md、机械臂操作准则.md 增加用户 10-29 要求（仿照 pickup_only 读取失败例外；同步批量下发）。
- `manipulation_geometry.py`：`sync_transition`（同步线性插值 + 20 ms 发送滞后包络，枚举发送顺序）、`grouped_transition`（端点检查 → 同步 → 经零点同步 → 原逐关节）；张开夹爪并入第一段（仅 CAD 模式，碰撞模型按张开夹爪包络）；撤离保持腕部与夹爪朝向。
- `manipulation_guided.py`：路径缓存改用分组同步路径。
- `manipulation_task.py`：`continue_read_failures`、`start_rows`；执行中个别关节读取失败记警告继续、不计验证；夹持期间 ID6 被方块阻挡记警告；有效读数偏差 >1° 仍停止。
- `control.py`：随臂抓放执行前读数允许失败关节、比较容差 1°。
- `manipulation_ui.js`：失败读数显示“读取失败”，状态文案。

## 验证（假设备，不连接硬件）
- 当天真实请求 `today.json`：旧 25 步 45.0 s → 新 8 步 14.4 s（`old_plan.json`、`new_plan.json`）。
- 设备上 11 个历史规划请求：可规划的 7 个均为 24–25 步 → 8 步；其余 4 个新旧代码同样在逆解阶段失败（旧配置 1 mm 容差）。
- 单元测试：test_manipulation 19、test_dynamic_manipulation 20（新增 4 项）、test_cad_manipulation 21、test_manipulation_guided 22、test_control 26、test_teaching 29、test_reported_cards 18、test_pickup_only 2、test_rotated_annotation 5、test_ros_client 2、arm_playground 51、两项页面 JS 测试，全部通过。

## 部署
- 设备备份：`/root/jjt/backups/sync_motion_20261029_03848892c024`；硬件读取 0、运动指令 0；受保护 JSON 与审计日志未变。
- 已重启网页与 ROS2 纯计算服务（实机模式），启动不运动。

## 未验证
- 同步轨迹按“同用时线性插值 + 发送间隔滞后”建模，舵机实际加减速曲线未实测；实机抓放结果待现场确认。
- 夹爪夹住方块时 ID6 的实际读数未知（历史日志无此记录）。
