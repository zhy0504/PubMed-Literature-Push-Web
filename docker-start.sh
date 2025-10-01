#!/bin/bash

# Docker RQ 部署快速启动脚本

set -e

echo "========================================="
echo " PubMed Literature Push - Docker RQ 版本"
echo "========================================="

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# 帮助信息
show_help() {
    echo "使用方法: $0 [选项]"
    echo ""
    echo "选项:"
    echo "  dev         启动开发环境 (Redis + App + Worker)"
    echo "  prod        启动生产环境 (包含多Worker和Nginx)"
    echo "  dashboard   启动包含RQ Dashboard的完整环境"
    echo "  stop        停止所有服务"
    echo "  restart     重启所有服务"
    echo "  logs        查看服务日志"
    echo "  status      查看服务状态"
    echo "  clean       清理所有容器和网络"
    echo "  test        测试RQ配置"
    echo "  backup      备份数据"
    echo "  help        显示此帮助信息"
    echo ""
    echo "示例:"
    echo "  $0 dev      # 启动开发环境"
    echo "  $0 prod     # 启动生产环境"
    echo "  $0 logs     # 查看日志"
}

# 检查Docker和docker-compose
check_requirements() {
    if ! command -v docker &> /dev/null; then
        echo -e "${RED}错误: Docker未安装${NC}"
        exit 1
    fi
    
    if ! command -v docker-compose &> /dev/null; then
        echo -e "${RED}错误: docker-compose未安装${NC}"
        exit 1
    fi
    
    echo -e "${GREEN}✓${NC} Docker环境检查通过"
}

# 检查.env文件
check_env_file() {
    if [ ! -f .env ]; then
        echo -e "${YELLOW}警告: .env文件不存在，创建默认配置...${NC}"
        cat > .env << EOF
# 基础配置
TZ=Asia/Shanghai
LOG_LEVEL=INFO

# Redis配置
REDIS_URL=redis://redis:6379/0

# RQ Worker配置  
RQ_WORKER_NAME=pubmed-worker-docker
RQ_QUEUES=high,default,low

# RQ Dashboard配置
RQ_DASHBOARD_USER=admin
RQ_DASHBOARD_PASS=admin123

# 邮件配置 (请根据实际情况修改)
MAIL_SERVER=smtp.gmail.com
MAIL_PORT=587
MAIL_USERNAME=your-email@gmail.com
MAIL_PASSWORD=your-app-password
EOF
        echo -e "${GREEN}✓${NC} 已创建默认.env文件，请根据需要修改配置"
    fi
}

# 创建必要的目录
create_directories() {
    mkdir -p data logs logs/redis logs/nginx
    echo -e "${GREEN}✓${NC} 目录结构已准备就绪"
}

# 启动开发环境
start_dev() {
    echo -e "${BLUE}启动开发环境...${NC}"
    docker-compose up -d
    
    echo ""
    echo -e "${GREEN}✓${NC} 开发环境启动完成！"
    echo ""
    echo "访问地址:"
    echo "  主应用: http://localhost:5005"
    echo "  RQ Dashboard: http://localhost:9181 (如果启用)"
    echo ""
    echo "查看日志: $0 logs"
    echo "查看状态: $0 status"
}

# 启动生产环境
start_prod() {
    echo -e "${BLUE}启动生产环境...${NC}"
    docker-compose -f docker-compose.prod.yml up -d
    
    echo ""
    echo -e "${GREEN}✓${NC} 生产环境启动完成！"
    echo ""
    echo "访问地址:"
    echo "  主应用: http://localhost:5005"
    echo "  Nginx: http://localhost:80"
    echo "  RQ Dashboard: http://localhost:9181"
    echo ""
    echo "查看日志: $0 logs prod"
    echo "查看状态: $0 status prod"
}

# 启动包含Dashboard的完整环境
start_dashboard() {
    echo -e "${BLUE}启动完整环境 (包含RQ Dashboard)...${NC}"
    docker-compose --profile dashboard up -d
    
    echo ""
    echo -e "${GREEN}✓${NC} 完整环境启动完成！"
    echo ""
    echo "访问地址:"
    echo "  主应用: http://localhost:5005"
    echo "  RQ Dashboard: http://localhost:9181"
}

# 停止服务
stop_services() {
    echo -e "${YELLOW}停止所有服务...${NC}"
    docker-compose down
    docker-compose -f docker-compose.prod.yml down 2>/dev/null || true
    echo -e "${GREEN}✓${NC} 所有服务已停止"
}

# 重启服务
restart_services() {
    echo -e "${YELLOW}重启服务...${NC}"
    docker-compose restart
    echo -e "${GREEN}✓${NC} 服务重启完成"
}

# 查看日志
show_logs() {
    local env=${1:-dev}
    if [ "$env" = "prod" ]; then
        docker-compose -f docker-compose.prod.yml logs -f --tail=50
    else
        docker-compose logs -f --tail=50
    fi
}

# 查看状态
show_status() {
    local env=${1:-dev}
    
    echo -e "${BLUE}=== 服务状态 ===${NC}"
    if [ "$env" = "prod" ]; then
        docker-compose -f docker-compose.prod.yml ps
    else
        docker-compose ps
    fi
    
    echo ""
    echo -e "${BLUE}=== Redis状态 ===${NC}"
    if docker-compose exec -T redis redis-cli ping &>/dev/null; then
        echo -e "${GREEN}✓${NC} Redis连接正常"
    else
        echo -e "${RED}✗${NC} Redis连接失败"
    fi
    
    echo ""
    echo -e "${BLUE}=== RQ队列状态 ===${NC}"
    docker-compose exec -T app python -c "
from rq_config import get_queue_info
import json
info = get_queue_info()
print('队列状态:')
for name, data in info.items():
    print(f'  {name}: 待处理={data[\"length\"]}, 失败={data[\"failed\"]}, 完成={data[\"finished\"]}')
" 2>/dev/null || echo -e "${RED}✗${NC} 无法获取队列状态"
}

# 清理环境
clean_environment() {
    echo -e "${YELLOW}清理Docker环境...${NC}"
    docker-compose down -v --remove-orphans
    docker-compose -f docker-compose.prod.yml down -v --remove-orphans 2>/dev/null || true
    
    # 清理未使用的镜像和网络
    docker system prune -f
    
    echo -e "${GREEN}✓${NC} 环境清理完成"
}

# 测试RQ配置
test_rq() {
    echo -e "${BLUE}测试RQ配置...${NC}"
    
    if ! docker-compose ps | grep -q "Up"; then
        echo -e "${RED}错误: 服务未运行，请先启动环境${NC}"
        exit 1
    fi
    
    docker-compose exec app python test_rq.py
}

# 备份数据
backup_data() {
    echo -e "${BLUE}备份数据...${NC}"
    
    local backup_dir="backup/$(date +%Y%m%d_%H%M%S)"
    mkdir -p "$backup_dir"
    
    # 备份数据库
    if [ -f "data/pubmed_app.db" ]; then
        cp data/pubmed_app.db "$backup_dir/"
        echo -e "${GREEN}✓${NC} 数据库已备份到 $backup_dir/"
    fi
    
    # 备份Redis数据
    docker-compose exec -T redis redis-cli BGSAVE >/dev/null 2>&1 || true
    echo -e "${GREEN}✓${NC} Redis数据已备份"
    
    echo -e "${GREEN}✓${NC} 备份完成: $backup_dir"
}

# 主逻辑
main() {
    case "${1:-help}" in
        "dev")
            check_requirements
            check_env_file
            create_directories
            start_dev
            ;;
        "prod")
            check_requirements
            check_env_file
            create_directories
            start_prod
            ;;
        "dashboard")
            check_requirements
            check_env_file
            create_directories
            start_dashboard
            ;;
        "stop")
            stop_services
            ;;
        "restart")
            restart_services
            ;;
        "logs")
            show_logs $2
            ;;
        "status")
            show_status $2
            ;;
        "clean")
            clean_environment
            ;;
        "test")
            test_rq
            ;;
        "backup")
            backup_data
            ;;
        "help"|*)
            show_help
            ;;
    esac
}

main "$@"