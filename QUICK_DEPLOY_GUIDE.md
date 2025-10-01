# PubMed Literature Push - 生产环境快速部署指南

## 快速开始 (5分钟部署)

### 1. 准备环境
```bash
# 克隆项目
git clone https://github.com/zhy0504/PubMed-Literature-Push-Web.git
cd PubMed-Literature-Push-Web

# 创建并配置环境变量
cp .env.example .env
nano .env  # 修改必要的配置
```

### 2. 一键部署
```bash
# 使用自动化部署脚本
chmod +x deploy-prod.sh
./deploy-prod.sh
```

### 3. 访问应用
- 主应用: http://localhost:5005
- RQ Dashboard: http://localhost:9181
- 默认账号: `admin@pubmed.com` / `admin123`

---

## 手动部署步骤

### 步骤1: 准备目录
```bash
mkdir -p data logs logs/redis logs/nginx nginx/ssl
```

### 步骤2: 配置环境变量
```bash
# 复制并编辑 .env
cp .env.example .env

# 必须修改的配置项:
# - DEFAULT_ADMIN_PASSWORD (管理员密码)
# - RQ_DASHBOARD_PASS (Dashboard密码)
# - OPENAI_API_KEY (如需AI功能)
# - 邮件服务器配置 (如需邮件推送)
```

### 步骤3: 拉取镜像
```bash
docker-compose -f docker-compose.prod.yml pull
```

### 步骤4: 启动服务
```bash
docker-compose -f docker-compose.prod.yml up -d
```

### 步骤5: 验证部署
```bash
# 查看服务状态
docker-compose -f docker-compose.prod.yml ps

# 查看日志
docker-compose -f docker-compose.prod.yml logs -f app
```

---

## 常用运维命令

### 服务管理
```bash
# 启动所有服务
docker-compose -f docker-compose.prod.yml up -d

# 停止所有服务
docker-compose -f docker-compose.prod.yml down

# 重启特定服务
docker-compose -f docker-compose.prod.yml restart app
docker-compose -f docker-compose.prod.yml restart worker-1

# 查看服务状态
docker-compose -f docker-compose.prod.yml ps

# 查看资源使用
docker stats
```

### 日志查看
```bash
# 查看所有日志
docker-compose -f docker-compose.prod.yml logs -f

# 查看特定服务日志
docker-compose -f docker-compose.prod.yml logs -f app
docker-compose -f docker-compose.prod.yml logs -f worker-1
docker-compose -f docker-compose.prod.yml logs -f redis

# 查看最近50行日志
docker-compose -f docker-compose.prod.yml logs --tail=50 app
```

### 进入容器调试
```bash
# 进入主应用容器
docker-compose -f docker-compose.prod.yml exec app /bin/bash

# 进入Redis容器
docker-compose -f docker-compose.prod.yml exec redis sh

# 进入Worker容器
docker-compose -f docker-compose.prod.yml exec worker-1 /bin/bash
```

### 数据库操作
```bash
# 备份数据库
docker-compose -f docker-compose.prod.yml exec -T app \
  sqlite3 /app/data/pubmed_app.db ".backup /app/data/backup_$(date +%Y%m%d).db"

# 查看数据库表
docker-compose -f docker-compose.prod.yml exec app \
  sqlite3 /app/data/pubmed_app.db ".tables"

# 执行SQL查询
docker-compose -f docker-compose.prod.yml exec app \
  sqlite3 /app/data/pubmed_app.db "SELECT * FROM user LIMIT 5;"
```

### Redis操作
```bash
# 测试Redis连接
docker-compose -f docker-compose.prod.yml exec redis redis-cli ping

# 查看Redis信息
docker-compose -f docker-compose.prod.yml exec redis redis-cli info

# 查看所有键
docker-compose -f docker-compose.prod.yml exec redis redis-cli keys '*'

# 手动触发Redis持久化
docker-compose -f docker-compose.prod.yml exec redis redis-cli BGSAVE
```

### RQ队列管理
```bash
# 查看队列状态
docker-compose -f docker-compose.prod.yml exec app python -c "
from rq_config import get_queue_info
import json
print(json.dumps(get_queue_info(), indent=2))
"

# 清理失败任务
docker-compose -f docker-compose.prod.yml exec app python -c "
from rq_config import clear_failed_jobs
clear_failed_jobs()
print('Failed jobs cleared')
"

# 重试失败任务
docker-compose -f docker-compose.prod.yml exec app python -c "
from rq_config import get_failed_jobs, requeue_failed_job
jobs = get_failed_jobs()
for job in jobs:
    requeue_failed_job(job['id'])
    print(f\"Requeued: {job['id']}\")
"
```

---

## 镜像管理

### 构建本地镜像
```bash
# 构建镜像
docker build -t pubmed-literature-push:local .

# 查看镜像大小
docker images | grep pubmed

# 标记镜像
docker tag pubmed-literature-push:local ghcr.io/zhy0504/pubmed-literature-push-web:latest
```

### 更新镜像
```bash
# 拉取最新镜像
docker-compose -f docker-compose.prod.yml pull

# 重新创建容器
docker-compose -f docker-compose.prod.yml up -d --force-recreate

# 清理旧镜像
docker image prune -f
```

---

## 故障排查

### 问题1: 服务无法启动
```bash
# 检查日志
docker-compose -f docker-compose.prod.yml logs app

# 检查端口占用
netstat -tulnp | grep 5005

# 检查磁盘空间
df -h

# 检查内存
free -h
```

### 问题2: Redis连接失败
```bash
# 检查Redis状态
docker-compose -f docker-compose.prod.yml ps redis

# 测试Redis连接
docker-compose -f docker-compose.prod.yml exec redis redis-cli ping

# 查看Redis日志
docker-compose -f docker-compose.prod.yml logs redis
```

### 问题3: Worker任务不执行
```bash
# 检查Worker状态
docker-compose -f docker-compose.prod.yml ps | grep worker

# 查看Worker日志
docker-compose -f docker-compose.prod.yml logs worker-1

# 重启Worker
docker-compose -f docker-compose.prod.yml restart worker-1 worker-2
```

### 问题4: 数据库锁定
```bash
# 停止所有服务
docker-compose -f docker-compose.prod.yml down

# 等待10秒
sleep 10

# 重新启动
docker-compose -f docker-compose.prod.yml up -d
```

### 问题5: 内存不足
```bash
# 查看资源使用
docker stats

# 调整资源限制 (编辑 docker-compose.prod.yml)
# 修改 deploy.resources.limits 部分

# 重启服务
docker-compose -f docker-compose.prod.yml up -d --force-recreate
```

---

## 性能优化建议

### 1. 调整Worker数量
```yaml
# 在 docker-compose.prod.yml 中增加Worker
worker-3:
  image: ghcr.io/zhy0504/pubmed-literature-push-web:latest
  container_name: pubmed-rq-worker-3
  # ... 复制worker-2的配置并修改名称
```

### 2. 调整Gunicorn Worker数量
```dockerfile
# 在 Dockerfile CMD 中修改 --workers 参数
CMD ["gunicorn", "--bind", "0.0.0.0:5005", "--workers", "8", ...]
```

### 3. Redis内存优化
```yaml
# 在 docker-compose.prod.yml 中调整Redis配置
command: >
  redis-server
  --maxmemory 1gb
  --maxmemory-policy allkeys-lru
```

### 4. 日志轮转配置
```bash
# 创建 /etc/logrotate.d/pubmed-app
cat > /etc/logrotate.d/pubmed-app << 'EOF'
/path/to/logs/*.log {
    daily
    rotate 14
    compress
    delaycompress
    notifempty
    create 0640 appuser appuser
    sharedscripts
}
EOF
```

---

## 安全加固建议

### 1. 修改默认密码
```bash
# 登录系统后立即修改:
# - 管理员密码
# - RQ Dashboard密码
```

### 2. 配置HTTPS
```bash
# 生成SSL证书
openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
  -keyout nginx/ssl/key.pem -out nginx/ssl/cert.pem

# 或使用Let's Encrypt
certbot certonly --standalone -d your-domain.com
```

### 3. 限制端口访问
```bash
# 使用防火墙限制访问
ufw allow 80/tcp
ufw allow 443/tcp
ufw deny 5005/tcp  # 仅允许Nginx访问
ufw deny 9181/tcp  # 仅内网访问
```

### 4. 定期备份
```bash
# 创建备份脚本
cat > backup.sh << 'EOF'
#!/bin/bash
DATE=$(date +%Y%m%d_%H%M%S)
docker-compose -f docker-compose.prod.yml exec -T app \
  sqlite3 /app/data/pubmed_app.db ".backup /app/data/backup_${DATE}.db"
docker-compose -f docker-compose.prod.yml exec -T redis redis-cli BGSAVE
EOF

# 添加到crontab
crontab -e
# 添加: 0 2 * * * /path/to/backup.sh
```

---

## 监控配置 (可选)

### Prometheus监控
```yaml
# 添加到 docker-compose.prod.yml
prometheus:
  image: prom/prometheus:latest
  volumes:
    - ./prometheus.yml:/etc/prometheus/prometheus.yml
  ports:
    - "9090:9090"
```

### Grafana仪表板
```yaml
grafana:
  image: grafana/grafana:latest
  ports:
    - "3000:3000"
  environment:
    - GF_SECURITY_ADMIN_PASSWORD=admin
```

---

## 升级流程

### 蓝绿部署
```bash
# 1. 拉取新版本镜像
docker pull ghcr.io/zhy0504/pubmed-literature-push-web:v2.0.0

# 2. 备份数据
./backup.sh

# 3. 更新镜像标签
# 编辑 docker-compose.prod.yml 修改镜像版本

# 4. 滚动更新
docker-compose -f docker-compose.prod.yml up -d --no-deps app

# 5. 验证新版本
curl http://localhost:5005/

# 6. 如有问题,快速回滚
docker-compose -f docker-compose.prod.yml up -d --no-deps --force-recreate app
```

---

## 参考资源

- 详细部署检查清单: [DEPLOYMENT_CHECKLIST.md](DEPLOYMENT_CHECKLIST.md)
- 项目文档: [README.md](README.md)
- Docker官方文档: https://docs.docker.com/
- docker-compose文档: https://docs.docker.com/compose/

---

## 技术支持

遇到问题请:
1. 查看日志: `docker-compose -f docker-compose.prod.yml logs`
2. 检查清单: `DEPLOYMENT_CHECKLIST.md`
3. 提交Issue: https://github.com/zhy0504/PubMed-Literature-Push-Web/issues

**重要提示**: 生产环境部署前请务必完整阅读 `DEPLOYMENT_CHECKLIST.md` 并完成所有检查项！
