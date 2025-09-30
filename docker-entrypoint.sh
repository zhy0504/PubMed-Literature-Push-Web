#!/bin/bash
set -e

echo "================================================"
echo " PubMed Literature Push - Docker Entrypoint"
echo " RQ Version with Redis Queue Support"
echo "================================================"

# 检查Redis连接
echo "[检查] 验证Redis连接..."
REDIS_URL=${REDIS_URL:-redis://localhost:6379/0}
if redis-cli -u "$REDIS_URL" ping >/dev/null 2>&1; then
    echo "[信息] Redis连接正常: $REDIS_URL"
    export RQ_MODE=enabled
else
    echo "[警告] Redis连接失败，将使用APScheduler降级模式"
    export RQ_MODE=fallback
fi

# 检查数据库是否存在且有效
DB_VALID=false
if [ -f "/app/data/pubmed_app.db" ]; then
    echo "[检查] 验证数据库完整性..."
    # 检查数据库是否包含 user 表
    if sqlite3 /app/data/pubmed_app.db "SELECT name FROM sqlite_master WHERE type='table' AND name='user';" 2>/dev/null | grep -q "user"; then
        DB_VALID=true
        echo "[信息] 数据库有效，跳过初始化"
    else
        echo "[警告] 数据库文件存在但无效，重新初始化..."
        rm -f /app/data/pubmed_app.db
    fi
fi

if [ "$DB_VALID" = false ]; then
    echo "[初始化] 创建数据库..."
    python setup.py --default
    if [ $? -eq 0 ]; then
        echo "[完成] 数据库初始化成功"
        # 从环境变量读取管理员账号，如果没有则显示默认值
        ADMIN_EMAIL=${DEFAULT_ADMIN_EMAIL:-admin@pubmed.com}
        ADMIN_PASSWORD=${DEFAULT_ADMIN_PASSWORD:-admin123}
        echo "[账号] $ADMIN_EMAIL / $ADMIN_PASSWORD"
    else
        echo "[错误] 数据库初始化失败"
        exit 1
    fi
fi

# 创建必要目录并设置权限
mkdir -p /app/data /app/logs

# 如果 logs 目录是挂载的，尝试修复权限（需要 root）
if [ -w /app/logs ] || [ "$(stat -c %U /app/logs 2>/dev/null)" = "appuser" ]; then
    touch /app/logs/app.log 2>/dev/null && echo "[信息] 日志文件已就绪"
else
    echo "[警告] /app/logs 目录权限不足，日志将输出到控制台"
fi

# 删除 Flask 自动创建的 instance 目录（避免创建错误的数据库）
rm -rf /app/instance

# RQ相关初始化
if [ "$RQ_MODE" = "enabled" ]; then
    echo "[RQ] Redis队列模式已启用"
    echo "[RQ] Worker配置: ${RQ_WORKER_NAME:-default-worker}"
    echo "[RQ] 监听队列: ${RQ_QUEUES:-high,default,low}"
    
    # 测试RQ配置
    if python -c "from rq_config import redis_conn; redis_conn.ping(); print('[RQ] 配置测试通过')" 2>/dev/null; then
        echo "[RQ] RQ配置验证成功"
    else
        echo "[警告] RQ配置验证失败，但将继续启动"
    fi
else
    echo "[调度器] 使用APScheduler降级模式"
fi

echo "[启动] 启动应用服务..."
echo "================================================"

# 执行传入的命令（gunicorn或其他）
exec "$@"