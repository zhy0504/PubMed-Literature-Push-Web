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

    # 清理可能存在的过期RQ调度标记（容器重启时强制重新调度）
    RQ_SCHEDULE_FLAG="/app/data/rq_schedule_init_done"
    if [ -f "$RQ_SCHEDULE_FLAG" ]; then
        echo "[RQ] 检测到调度标记文件，检查有效性..."
        # 获取文件修改时间（秒）
        FILE_MTIME=$(stat -c %Y "$RQ_SCHEDULE_FLAG" 2>/dev/null || echo 0)
        CURRENT_TIME=$(date +%s)
        AGE=$((CURRENT_TIME - FILE_MTIME))

        # 如果文件超过5分钟，认为是容器重启，删除标记强制重新调度
        if [ $AGE -gt 300 ]; then
            echo "[RQ] 调度标记已过期(${AGE}秒)，删除以触发重新调度"
            rm -f "$RQ_SCHEDULE_FLAG"
        else
            echo "[RQ] 调度标记有效(${AGE}秒内创建)"
        fi
    fi

    # 清理环境变量同步标记（仅主应用容器，容器重启时强制重新同步）
    # Worker容器不应删除标记，避免与主应用竞争
    if [ -z "$RQ_WORKER_NAME" ]; then
        ENV_SYNC_FLAG="/app/data/env_sync_done"
        if [ -f "$ENV_SYNC_FLAG" ]; then
            echo "[同步] 检测到环境变量同步标记，删除以触发重新同步"
            rm -f "$ENV_SYNC_FLAG"
        fi
    else
        echo "[同步] Worker容器跳过清理环境变量同步标记"
    fi

    # 测试RQ配置
    if python -c "from rq_config import redis_conn; redis_conn.ping(); print('[RQ] 配置测试通过')" 2>/dev/null; then
        echo "[RQ] RQ配置验证成功"
        echo "[RQ] 已有订阅将在首次请求时自动调度到队列"
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