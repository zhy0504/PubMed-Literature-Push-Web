#!/bin/bash
# Flask-Migrate 辅助脚本 - 支持 Windows/Linux

show_help() {
    echo "Flask-Migrate 使用帮助:"
    echo "  ./migrate.sh migrate \"描述\"  - 创建新迁移"
    echo "  ./migrate.sh upgrade          - 应用迁移"
    echo "  ./migrate.sh history          - 查看历史"
    echo "  ./migrate.sh current          - 查看当前版本"
    echo "  ./migrate.sh downgrade        - 回滚到上一版本"
    echo "  ./migrate.sh help             - 显示帮助"
}

# 设置FLASK_APP环境变量
export FLASK_APP=app.py

# 检测操作系统并设置正确的Python路径
if [[ "$OSTYPE" == "msys" ]] || [[ "$OSTYPE" == "win32" ]]; then
    PYTHON_CMD="quick_venv/Scripts/python.exe"
else
    PYTHON_CMD="quick_venv/bin/python"
fi

run_flask_command() {
    local cmd_args="$1"
    $PYTHON_CMD -c "
import os
os.environ['FLASK_APP'] = 'app.py'
from flask.cli import main
import sys
sys.argv = $cmd_args
main()
"
}

case "$1" in
    migrate)
        if [ -z "$2" ]; then
            echo "错误: 请提供迁移描述"
            echo "用法: ./migrate.sh migrate \"你的描述\""
            exit 1
        fi
        echo "创建迁移: $2"
        run_flask_command "['flask', 'db', 'migrate', '-m', '$2']"
        ;;
    upgrade)
        echo "应用迁移..."
        run_flask_command "['flask', 'db', 'upgrade']"
        ;;
    history)
        echo "迁移历史:"
        run_flask_command "['flask', 'db', 'history']"
        ;;
    current)
        echo "当前版本:"
        run_flask_command "['flask', 'db', 'current']"
        ;;
    downgrade)
        echo "回滚到上一版本..."
        run_flask_command "['flask', 'db', 'downgrade']"
        ;;
    help|--help|-h)
        show_help
        ;;
    *)
        echo "用法: ./migrate.sh [migrate|upgrade|history|current|downgrade|help]"
        echo "运行 './migrate.sh help' 查看详细帮助"
        exit 1
        ;;
esac