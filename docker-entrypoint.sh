#!/bin/bash
set -e

echo "================================================"
echo " PubMed Literature Push - Docker Entrypoint"
echo "================================================"

# 检查数据库是否存在
if [ ! -f "/app/pubmed_app.db" ]; then
    echo "[初始化] 数据库不存在，正在创建..."
    python setup.py --default
    if [ $? -eq 0 ]; then
        echo "[完成] 数据库初始化成功"
        echo "[账号] admin@pubmed.com / admin123"
        echo "[账号] backup-admin@pubmed.com / admin123"
        echo "[账号] test@example.com / test123"
    else
        echo "[错误] 数据库初始化失败"
        exit 1
    fi
else
    echo "[信息] 数据库已存在，跳过初始化"
fi

# 创建必要目录
mkdir -p /app/data /app/logs

echo "[启动] 启动应用服务..."
echo "================================================"

# 执行传入的命令（gunicorn）
exec "$@"