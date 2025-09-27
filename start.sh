#!/bin/bash

# PubMed Literature Push Web Application - Linux/Mac启动脚本

echo "================================================"
echo " PubMed Literature Push Web Application"
echo "================================================"
echo

# 检查Python是否安装
if ! command -v python3 &> /dev/null; then
    echo "[错误] 未找到Python3，请先安装Python 3.8+"
    echo
    echo "Ubuntu/Debian: sudo apt update && sudo apt install python3 python3-pip python3-venv"
    echo "CentOS/RHEL: sudo yum install python3 python3-pip"
    echo "macOS: brew install python3"
    exit 1
fi

echo "[信息] Python版本:"
python3 --version

# 检查虚拟环境
if [ ! -d "quick_venv" ]; then
    echo
    echo "[警告] 未找到虚拟环境 'quick_venv'"
    echo "[信息] 正在创建虚拟环境..."
    python3 -m venv quick_venv
    if [ $? -ne 0 ]; then
        echo "[错误] 创建虚拟环境失败"
        exit 1
    fi
    echo "[完成] 虚拟环境创建成功"
fi

# 激活虚拟环境
echo
echo "[信息] 激活虚拟环境..."
source quick_venv/bin/activate
if [ $? -ne 0 ]; then
    echo "[错误] 激活虚拟环境失败"
    exit 1
fi

# 检查依赖是否安装
echo "[信息] 检查依赖包..."
python -c "import flask" 2>/dev/null
if [ $? -ne 0 ]; then
    echo "[信息] 正在安装依赖包..."
    pip install -r requirements.txt
    if [ $? -ne 0 ]; then
        echo "[错误] 依赖安装失败"
        exit 1
    fi
    echo "[完成] 依赖安装成功"
else
    echo "[信息] 依赖包已安装"
fi

# 检查数据库是否存在并更新schema
if [ ! -f "pubmed_app.db" ]; then
    echo
    echo "[信息] 初始化数据库..."
    echo
    echo "选择设置方式："
    echo "  1. 交互式设置 (自定义账号) - python setup.py"
    echo "  2. 快速设置 (默认账号) - python setup.py --default"
    echo
    read -p "选择设置方式 (1/2): " setup_choice
    
    case "$setup_choice" in
        1)
            echo "运行交互式设置..."
            python setup.py
            ;;
        2)
            echo "运行快速默认设置..."
            python setup.py --default
            ;;
        *)
            echo "无效选择，使用快速默认设置..."
            python setup.py --default
            ;;
    esac
    
    if [ $? -ne 0 ]; then
        echo "[错误] 数据库设置失败"
        exit 1
    fi
    echo "[完成] 数据库设置成功"
else
    echo "[信息] 检查数据库schema..."
    python -c "import sqlite3; conn = sqlite3.connect('pubmed_app.db'); cursor = conn.cursor(); cursor.execute('PRAGMA table_info(user)'); columns = [row[1] for row in cursor.fetchall()]; missing = []; missing.append('push_day') if 'push_day' not in columns else None; missing.append('last_push') if 'last_push' not in columns else None; [cursor.execute(f'ALTER TABLE user ADD COLUMN {col} {\"VARCHAR(10) DEFAULT \\\"monday\\\"\" if col == \"push_day\" else \"TIMESTAMP\"}') for col in missing if missing]; conn.commit(); conn.close(); print(f'Schema updated: {len(missing)} columns' if missing else 'Schema is up to date')" 2>/dev/null
    echo "[完成] 数据库schema检查完成"
fi

# 创建日志目录
mkdir -p logs

# 启动应用
echo
echo "[信息] 启动PubMed Literature Push Web应用..."
echo "[地址] http://127.0.0.1:5003"
echo "[管理员] admin@pubmed.com / admin123"
echo "[管理员] backup-admin@pubmed.com / admin123"  
echo "[用户] test@example.com / test123"
echo "[提示] 如使用自定义设置，请使用您设置的账号密码"
echo "[提示] 按 Ctrl+C 停止服务器"
echo
echo "================================================"
echo " 应用正在启动中..."
echo "================================================"
echo

# 设置信号处理
trap 'echo; echo "[信息] 正在停止应用..."; kill $APP_PID 2>/dev/null; exit 0' INT TERM

# 启动应用并获取进程ID
python app.py &
APP_PID=$!

# 等待应用进程
wait $APP_PID
APP_EXIT_CODE=$?

# 检查应用是否正常退出
if [ $APP_EXIT_CODE -ne 0 ]; then
    echo
    echo "[错误] 应用启动失败，错误代码: $APP_EXIT_CODE"
    echo
    echo "可能的解决方案:"
    echo "1. 检查端口5003是否被占用: lsof -i :5003"
    echo "2. 检查配置文件config.py"
    echo "3. 查看详细错误信息"
    echo "4. 检查日志文件: tail -f logs/app.log"
    echo
    exit 1
fi

echo
echo "[信息] 应用已停止"