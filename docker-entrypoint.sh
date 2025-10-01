#!/bin/bash
set -e

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

echo "================================================"
echo " PubMed Literature Push - Production Entrypoint"
echo " Version: 1.0.0"
echo "================================================"

# 1. 环境变量验证
log_info "验证必需的环境变量..."
REDIS_URL=${REDIS_URL:-redis://localhost:6379/0}
DATABASE_URL=${DATABASE_URL:-sqlite:////app/data/pubmed_app.db}

log_info "Redis URL: ${REDIS_URL}"
log_info "Database: ${DATABASE_URL}"

# 2. Redis连接检查(带重试)
log_info "检查Redis连接..."
REDIS_READY=false
MAX_RETRY=30
RETRY_COUNT=0

while [ $RETRY_COUNT -lt $MAX_RETRY ]; do
    if redis-cli -u "$REDIS_URL" ping >/dev/null 2>&1; then
        log_info "Redis连接成功"
        REDIS_READY=true
        export RQ_MODE=enabled
        break
    else
        RETRY_COUNT=$((RETRY_COUNT + 1))
        log_warn "Redis未就绪,等待中... (${RETRY_COUNT}/${MAX_RETRY})"
        sleep 2
    fi
done

if [ "$REDIS_READY" = false ]; then
    log_error "Redis连接失败,切换到APScheduler降级模式"
    export RQ_MODE=fallback
fi

# 3. 创建必要目录
log_info "初始化目录结构..."
mkdir -p /app/data /app/logs

# 4. 数据库初始化和迁移
DB_VALID=false
DB_PATH="/app/data/pubmed_app.db"

if [ -f "$DB_PATH" ]; then
    log_info "检测到现有数据库,验证完整性..."
    if sqlite3 "$DB_PATH" "SELECT name FROM sqlite_master WHERE type='table' AND name='user';" 2>/dev/null | grep -q "user"; then
        DB_VALID=true
        log_info "数据库验证通过"

        # 执行数据库迁移(如有需要)
        if [ -d "/app/migrations/versions" ]; then
            log_info "检查数据库迁移..."
            python manage.py db upgrade 2>/dev/null || log_warn "数据库迁移失败或无需迁移"
        fi
    else
        log_warn "数据库文件损坏,重新初始化..."
        rm -f "$DB_PATH"
    fi
fi

if [ "$DB_VALID" = false ]; then
    log_info "初始化数据库..."

    # 从环境变量读取管理员账号
    ADMIN_EMAIL=${DEFAULT_ADMIN_EMAIL:-admin@pubmed.com}
    ADMIN_PASSWORD=${DEFAULT_ADMIN_PASSWORD:-admin123}

    if python setup.py --default; then
        log_info "数据库初始化成功"
        log_info "默认管理员账号: ${ADMIN_EMAIL}"
        log_warn "生产环境请务必修改默认密码!"
    else
        log_error "数据库初始化失败"
        exit 1
    fi
fi

# 5. 文件权限检查
log_info "检查文件权限..."
if [ -w /app/logs ]; then
    touch /app/logs/app.log 2>/dev/null && log_info "日志文件就绪"
else
    log_warn "日志目录权限不足,将输出到stdout"
fi

# 6. 清理Flask自动生成的instance目录
rm -rf /app/instance

# 7. RQ任务队列初始化
if [ "$RQ_MODE" = "enabled" ]; then
    log_info "RQ任务队列模式已启用"
    log_info "Worker名称: ${RQ_WORKER_NAME:-default-worker}"
    log_info "监听队列: ${RQ_QUEUES:-high,default,low}"

    # 清理过期的调度标记(容器重启后强制重新调度)
    RQ_SCHEDULE_FLAG="/app/data/rq_schedule_init_done"
    if [ -f "$RQ_SCHEDULE_FLAG" ]; then
        FILE_MTIME=$(stat -c %Y "$RQ_SCHEDULE_FLAG" 2>/dev/null || echo 0)
        CURRENT_TIME=$(date +%s)
        AGE=$((CURRENT_TIME - FILE_MTIME))

        # 超过5分钟认为是容器重启
        if [ $AGE -gt 300 ]; then
            log_info "清理过期的调度标记(${AGE}秒前创建)"
            rm -f "$RQ_SCHEDULE_FLAG"
        fi
    fi

    # 仅主应用容器清理环境变量同步标记
    if [ -z "$RQ_WORKER_NAME" ]; then
        ENV_SYNC_FLAG="/app/data/env_sync_done"
        [ -f "$ENV_SYNC_FLAG" ] && rm -f "$ENV_SYNC_FLAG"
        log_info "环境变量同步标记已清理"
    fi

    # 验证RQ配置
    if python -c "from rq_config import redis_conn; redis_conn.ping()" 2>/dev/null; then
        log_info "RQ配置验证成功"
    else
        log_warn "RQ配置验证失败,但将继续启动"
    fi
else
    log_info "使用APScheduler降级模式"
fi

# 8. 健康检查端点验证(可选)
if [ -z "$RQ_WORKER_NAME" ]; then
    log_info "主应用容器,准备启动Web服务..."
else
    log_info "Worker容器,准备启动任务处理..."
fi

# 9. 显示启动信息
echo "================================================"
log_info "环境配置完成,启动应用..."
echo "================================================"

# 10. 执行主命令(gunicorn或worker)
exec "$@"
