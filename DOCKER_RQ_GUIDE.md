# Docker RQ 部署指南

## 快速启动

### 开发环境
```bash
# 1. 启动所有服务（Redis + App + Worker）
docker-compose up -d

# 2. 查看服务状态
docker-compose ps

# 3. 查看日志
docker-compose logs -f app
docker-compose logs -f worker

# 4. 访问服务
# 应用: http://localhost:5003
# RQ Dashboard: http://localhost:9181 (可选)
```

### 生产环境
```bash
# 1. 启动生产环境（包括Nginx）
docker-compose -f docker-compose.prod.yml up -d

# 2. 仅启动核心服务（无Nginx）
docker-compose -f docker-compose.prod.yml up -d redis app worker-1 worker-2

# 3. 启动包含RQ Dashboard的完整环境
docker-compose -f docker-compose.prod.yml --profile dashboard up -d
```

## 服务架构

### 开发环境 (docker-compose.yml)
- **redis**: Redis 7 队列服务
- **app**: Flask主应用 (端口5003)
- **worker**: 单个RQ Worker进程
- **rq-dashboard**: RQ监控面板 (端口9181，可选)

### 生产环境 (docker-compose.prod.yml)
- **redis**: Redis 7 队列服务 (512MB内存限制)
- **app**: Flask主应用 (GitHub镜像)
- **worker-1/2**: 多个RQ Worker进程 (负载均衡)
- **rq-dashboard**: RQ监控面板
- **nginx**: Nginx反向代理 (端口80/443)

## 环境变量配置

创建 `.env` 文件:
```bash
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

# 邮件配置 (根据需要)
MAIL_SERVER=smtp.gmail.com
MAIL_PORT=587
MAIL_USERNAME=your-email@gmail.com
MAIL_PASSWORD=your-app-password
```

## 健康检查

### 检查服务状态
```bash
# 检查所有容器健康状态
docker-compose ps

# 检查Redis连接
docker-compose exec redis redis-cli ping

# 检查RQ队列状态
docker-compose exec app python -c "from rq_config import get_queue_info; print(get_queue_info())"

# 检查Worker进程
docker-compose exec worker ps aux | grep rq_worker
```

### 监控和日志
```bash
# 查看实时日志
docker-compose logs -f app worker

# 查看RQ Dashboard
# http://localhost:9181

# 查看Redis内存使用
docker-compose exec redis redis-cli info memory
```

## 故障排除

### Redis连接问题
```bash
# 1. 检查Redis容器是否运行
docker-compose ps redis

# 2. 检查Redis日志
docker-compose logs redis

# 3. 测试Redis连接
docker-compose exec app redis-cli -h redis ping
```

### Worker进程问题
```bash
# 1. 检查Worker日志
docker-compose logs worker

# 2. 重启Worker
docker-compose restart worker

# 3. 手动启动Worker调试
docker-compose exec app python rq_worker.py
```

### 应用降级模式
如果Redis不可用，应用会自动降级到APScheduler模式：
```bash
# 查看应用日志确认模式
docker-compose logs app | grep -E "(RQ|APScheduler|调度器)"
```

## 扩展配置

### 增加Worker数量
```yaml
# 在docker-compose.yml中添加
worker-2:
  build: .
  container_name: pubmed-rq-worker-2
  command: python rq_worker.py
  environment:
    - RQ_WORKER_NAME=pubmed-worker-2
  # ... 其他配置同worker
```

### 自定义Redis配置
```yaml
redis:
  image: redis:7-alpine
  command: redis-server --maxmemory 1gb --maxmemory-policy allkeys-lru --appendonly yes
  volumes:
    - ./redis.conf:/etc/redis/redis.conf
```

### Nginx SSL配置
```yaml
nginx:
  volumes:
    - ./nginx/nginx.conf:/etc/nginx/nginx.conf
    - ./nginx/ssl:/etc/nginx/ssl
```

## 数据持久化

### 重要目录挂载
- `./data:/app/data` - 数据库和上传文件
- `./logs:/app/logs` - 应用日志
- `redis-data:/data` - Redis数据持久化

### 备份策略
```bash
# 备份数据库
docker-compose exec app cp /app/data/pubmed_app.db /app/data/backup-$(date +%Y%m%d).db

# 备份Redis数据
docker-compose exec redis redis-cli BGSAVE
```

## 升级指南

### 更新到新版本
```bash
# 1. 备份数据
docker-compose exec app cp /app/data/pubmed_app.db /app/data/backup.db

# 2. 拉取新镜像
docker-compose pull

# 3. 重启服务
docker-compose up -d

# 4. 查看升级日志
docker-compose logs -f app
```

### 回滚版本
```bash
# 1. 停止服务
docker-compose down

# 2. 恢复备份
cp ./data/backup.db ./data/pubmed_app.db

# 3. 使用指定版本启动
docker-compose up -d
```