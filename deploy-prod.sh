#!/bin/bash
#
# PubMed Literature Push - 生产环境部署脚本
# 用途: 自动化部署、验证和健康检查
#

set -e

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info() { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }
log_step() { echo -e "${BLUE}[STEP]${NC} $1"; }

# 环境检查
check_prerequisites() {
    log_step "1. 检查部署环境..."

    if ! command -v docker &> /dev/null; then
        log_error "Docker未安装"
        exit 1
    fi
    log_info "Docker版本: $(docker --version)"

    if ! command -v docker-compose &> /dev/null; then
        log_error "docker-compose未安装"
        exit 1
    fi
    log_info "docker-compose版本: $(docker-compose --version)"

    if [ ! -f .env ]; then
        log_error ".env文件不存在,请从.env.example复制并配置"
        exit 1
    fi
    log_info ".env文件存在"
}

# 准备目录结构
prepare_directories() {
    log_step "2. 准备目录结构..."

    mkdir -p data logs logs/redis
    log_info "目录结构已创建"
}

# 验证配置文件
validate_config() {
    log_step "3. 验证配置文件..."

    # 检查必需的环境变量
    source .env
    if [ -z "$REDIS_URL" ]; then
        log_warn "REDIS_URL未设置,将使用默认值"
    fi

    log_info "配置文件验证通过"
}

# 拉取最新镜像
pull_images() {
    log_step "4. 拉取Docker镜像..."

    docker-compose -f docker-compose.prod.yml pull
    log_info "镜像拉取完成"
}

# 启动服务
start_services() {
    log_step "5. 启动生产环境服务..."

    docker-compose -f docker-compose.prod.yml up -d
    log_info "服务已启动"
}

# 等待服务就绪
wait_for_services() {
    log_step "6. 等待服务就绪..."

    local MAX_WAIT=120
    local WAIT_TIME=0

    while [ $WAIT_TIME -lt $MAX_WAIT ]; do
        if docker-compose -f docker-compose.prod.yml ps | grep -q "Up (healthy)"; then
            log_info "服务已就绪"
            return 0
        fi
        echo -n "."
        sleep 5
        WAIT_TIME=$((WAIT_TIME + 5))
    done

    log_error "服务启动超时"
    return 1
}

# 健康检查
health_check() {
    log_step "7. 执行健康检查..."

    # Redis检查
    if docker-compose -f docker-compose.prod.yml exec -T redis redis-cli ping &>/dev/null; then
        log_info "✓ Redis运行正常"
    else
        log_error "✗ Redis检查失败"
        return 1
    fi

    # 应用检查
    if curl -f http://localhost:5005/ &>/dev/null; then
        log_info "✓ 主应用运行正常"
    else
        log_error "✗ 主应用检查失败"
        return 1
    fi

    # RQ Dashboard检查
    if curl -f http://localhost:9181/ &>/dev/null; then
        log_info "✓ RQ Dashboard运行正常"
    else
        log_warn "✗ RQ Dashboard访问失败(可能正常)"
    fi

    # Worker检查
    local worker_count=$(docker-compose -f docker-compose.prod.yml ps | grep "worker" | grep "Up" | wc -l)
    if [ "$worker_count" -ge 2 ]; then
        log_info "✓ Worker进程运行正常 (${worker_count}个)"
    else
        log_warn "Worker进程数量异常: ${worker_count}"
    fi
}

# 显示服务状态
show_status() {
    log_step "8. 服务状态总览..."

    docker-compose -f docker-compose.prod.yml ps

    echo ""
    log_info "访问地址:"
    echo "  主应用:        http://localhost:5005"
    echo "  RQ Dashboard:  http://localhost:9181"
    echo ""
    log_info "提示: 如需Nginx反向代理,请在宿主机或独立服务器上配置"
}

# 显示日志查看命令
show_logs_info() {
    echo ""
    log_info "日志查看命令:"
    echo "  所有服务:     docker-compose -f docker-compose.prod.yml logs -f"
    echo "  主应用:       docker-compose -f docker-compose.prod.yml logs -f app"
    echo "  Worker:       docker-compose -f docker-compose.prod.yml logs -f worker-1"
    echo "  Redis:        docker-compose -f docker-compose.prod.yml logs -f redis"
}

# 主流程
main() {
    echo "================================================"
    echo " PubMed Literature Push - 生产环境部署"
    echo "================================================"
    echo ""

    check_prerequisites
    prepare_directories
    validate_config
    pull_images
    start_services
    wait_for_services
    health_check
    show_status
    show_logs_info

    echo ""
    log_info "部署完成!"
    echo "================================================"
}

# 错误处理
trap 'log_error "部署过程中发生错误,请检查日志"; exit 1' ERR

# 执行主流程
main "$@"
