# 网页运动写入失败自动重发（2026-10-30）

## 用户反馈
人工标注抓放执行一步后停止，提示 “Arm_serial_servo_write I2C error”。

## 原因
- 14:04:35 第1步（张开夹爪并到取物上方，衔接）写入成功；14:04:36.531 第2步“接近抓取”批量写入时 I2C 写入失败，按原规则停止。执行前读数六关节完整，与读数无关。
- 板上无其他进程占用 I2C 设备。历史统计：关节读取 4614 次失败 92 次（2.0%），发送记录 727 次写入失败 13 次（约1.8%）；一次完整抓放约20次单关节写入，约每5次任务遇到一次。

## 修改
- 规则：AGENTS.md、机械臂操作准则.md 增加用户10-30要求。
- `arm_playground/arm.py`：`Controller.write(..., retries=0)`，单关节写入失败间隔30 ms 重发同一绝对目标，最多2次（`MAX_WRITE_RETRIES`），`last_retries` 记录；默认0，其他入口不变。
- `sorting_demo/control.py`：单关节手动调整和六关节姿态按钮使用 retries=2 并记录 `write_retry`。
- `sorting_demo/manipulation_task.py`：抓放计划每批写入使用 retries=2，记录 `write_retry` 并计入任务警告。

## 验证（假设备）
- 新增：重发同一目标后继续本批、3次失败停止、非法重发次数拒绝；手动调整和抓放计划请求重发并记录。
- 回归：arm_playground 54、test_control 27、test_dynamic_manipulation 31 及其余 sorting_demo 测试组、4 项页面 JS 测试全部通过。

## 部署
- 设备备份 `/root/jjt/backups/write_retry_20261030_e21ab567cb5f`；零舵机读取与运动；服务实机模式重启。

## 未验证
- I2C 偶发失败的硬件根因（供电、线缆、总线速率）未排查；重发在实机上的效果待现场观察日志中的 write_retry 记录。
