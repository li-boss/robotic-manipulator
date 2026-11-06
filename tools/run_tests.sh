#!/bin/zsh
# 全部本地测试：假设备 + 合成相机，不连接机械臂。用法：zsh tools/run_tests.sh [python解释器]
cd "${0:A:h}/.." || exit 1
PY=${1:-.venv-mac/bin/python}
set -e
echo '== 开发板端 Python 测试'
(cd board/tests && PYTHONPATH=..:. ../../$PY -B -m unittest discover -p 'test_*.py' 2>&1 | grep -v '^模拟：\|^实机：' | tail -4)
echo '== 电脑端 Python 测试'
$PY -B -m unittest discover -s pc/tests -p 'test_*.py' 2>&1 | grep -v '^模拟：' | tail -3
echo '== 网页脚本测试'
for f in board/tests/*.cjs; do node "$f"; done
node --check board/web/home_ui.js && node --check board/web/modes_ui.js && echo '首页/模式页脚本语法通过'
