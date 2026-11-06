"""开发板端统一路径。代码在 board/，项目规则文件在仓库根目录，运行数据在 board/data/。

部署到设备时为 /root/jjt/board；可用环境变量 ARM_DATA_DIR 指向其他数据目录（测试用）。
"""
import os
from pathlib import Path

BOARD = Path(__file__).resolve().parent          # board/ 代码目录
PROJECT = BOARD.parent                            # 仓库根目录（AGENTS.md、机械臂操作准则.md）
WEB = BOARD / 'web'                               # 网页与前端脚本
MODELS = BOARD / 'models'                         # URDF / STL / CAD 预处理结果
DATA = Path(os.environ.get('ARM_DATA_DIR', BOARD / 'data'))   # 配置与运行记录（不随代码覆盖）
LOGS = DATA / 'logs'
RULE_FILES = (PROJECT / 'AGENTS.md', PROJECT / '机械臂操作准则.md')
