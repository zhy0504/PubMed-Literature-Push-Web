#!/bin/bash
set -e

echo "================================================"
echo " PubMed Literature Push - Docker Entrypoint"
echo "================================================"

# 检查数据库是否存在且有效
DB_VALID=false
if [ -f "/app/pubmed_app.db" ]; then
    echo "[检查] 验证数据库完整性..."
    # 检查数据库是否包含 user 表
    if sqlite3 /app/pubmed_app.db "SELECT name FROM sqlite_master WHERE type='table' AND name='user';" 2>/dev/null | grep -q "user"; then
        DB_VALID=true
        echo "[信息] 数据库有效，跳过初始化"
    else
        echo "[警告] 数据库文件存在但无效，重新初始化..."
        rm -f /app/pubmed_app.db
    fi
fi

if [ "$DB_VALID" = false ]; then
    echo "[初始化] 创建数据库..."
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
fi

# 创建必要目录
mkdir -p /app/data /app/logs

# 删除 Flask 自动创建的 instance 目录（避免创建错误的数据库）
rm -rf /app/instance

echo "[启动] 启动应用服务..."
echo "================================================"

# 执行传入的命令（gunicorn）
exec "$@"