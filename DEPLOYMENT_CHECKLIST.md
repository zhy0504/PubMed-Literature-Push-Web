# PubMed Literature Push - 生产环境部署验证清单

## 部署前检查

### 环境准备
- [ ] Docker已安装 (版本 >= 20.10)
- [ ] docker-compose已安装 (版本 >= 1.29)
- [ ] 服务器防火墙已配置端口开放
  - [ ] 5005 (主应用)
  - [ ] 9181 (RQ Dashboard)
  - [ ] 80/443 (Nginx)
- [ ] 足够的磁盘空间 (建议 >= 20GB)
- [ ] 足够的内存 (建议 >= 4GB)

### 配置文件准备
- [ ] `.env` 文件已从 `.env.example` 复制并配置
- [ ] 数据库路径配置正确
- [ ] Redis连接地址配置正确
- [ ] 时区设置正确 (TZ变量)
- [ ] 日志级别已设置 (LOG_LEVEL)
- [ ] 管理员账号密码已修改 (DEFAULT_ADMIN_PASSWORD)
- [ ] RQ Dashboard密码已修改 (RQ_DASHBOARD_PASS)
- [ ] OpenAI API Key已配置 (如需AI功能)
- [ ] 邮件服务器配置已完成 (如需邮件推送)

### 目录结构
- [ ] `data/` 目录已创建
- [ ] `logs/` 目录已创建
- [ ] `nginx/nginx.conf` 文件已配置
- [ ] `nginx/ssl/` SSL证书已准备 (如使用HTTPS)

## 部署过程验证

### 镜像准备
- [ ] 成功拉取 `ghcr.io/zhy0504/pubmed-literature-push-web:latest`
- [ ] 成功拉取 `redis:7-alpine`
- [ ] 成功拉取 `cjlapao/rq-dashboard:latest`
- [ ] 成功拉取 `nginx:alpine`

### 容器启动
- [ ] Redis容器启动成功
- [ ] 主应用容器启动成功
- [ ] Worker-1容器启动成功
- [ ] Worker-2容器启动成功
- [ ] RQ Dashboard容器启动成功
- [ ] Nginx容器启动成功 (如启用)

### 健康检查
```bash
# 检查所有容器状态
docker-compose -f docker-compose.prod.yml ps

# 应显示所有服务为 "Up (healthy)" 状态
```

- [ ] Redis健康检查通过
- [ ] 主应用健康检查通过
- [ ] Worker健康检查通过
- [ ] 容器间网络连通正常

## 功能验证

### 基础功能测试
- [ ] 访问 http://localhost:5005 可以打开主页
- [ ] 访问 http://localhost:9181 可以打开RQ Dashboard
- [ ] 使用默认账号可以登录系统
- [ ] 数据库初始化成功 (检查 `data/pubmed_app.db` 存在)

### Redis连接验证
```bash
# 在容器内测试Redis连接
docker-compose -f docker-compose.prod.yml exec app python -c "from rq_config import redis_conn; redis_conn.ping(); print('Redis OK')"
```

- [ ] Redis连接测试通过
- [ ] RQ队列信息可以查询

### 数据库验证
```bash
# 检查数据库表结构
docker-compose -f docker-compose.prod.yml exec app sqlite3 /app/data/pubmed_app.db ".tables"
```

- [ ] 数据库包含所有必需的表
- [ ] 管理员账号已创建
- [ ] 可以正常登录管理后台

### Worker任务验证
```bash
# 查看RQ队列状态
docker-compose -f docker-compose.prod.yml exec app python -c "
from rq_config import get_queue_info
import json
print(json.dumps(get_queue_info(), indent=2))
"
```

- [ ] Worker进程正常运行
- [ ] 队列状态可以查询
- [ ] 测试任务可以正常入队和执行

### 日志验证
```bash
# 查看应用日志
docker-compose -f docker-compose.prod.yml logs app --tail=50

# 查看Worker日志
docker-compose -f docker-compose.prod.yml logs worker-1 --tail=50
```

- [ ] 应用日志正常输出
- [ ] Worker日志正常输出
- [ ] 无严重错误信息
- [ ] 日志文件轮转配置正确

## 性能验证

### 资源使用检查
```bash
# 查看容器资源使用
docker stats --no-stream
```

- [ ] 各容器CPU使用率正常 (< 80%)
- [ ] 各容器内存使用正常 (在限制范围内)
- [ ] Redis内存使用在配置的最大值内
- [ ] 磁盘空间充足

### 并发测试
- [ ] 应用可以处理多个并发请求
- [ ] Worker可以并发处理多个任务
- [ ] Redis队列无阻塞

## 安全验证

### 容器安全
- [ ] 应用以非root用户运行 (appuser)
- [ ] 敏感信息通过环境变量管理
- [ ] `.env` 文件未提交到版本库
- [ ] 容器网络隔离正确配置

### 数据安全
- [ ] 数据库文件权限正确
- [ ] 日志文件不包含敏感信息
- [ ] 管理员密码已修改 (非默认值)
- [ ] RQ Dashboard密码已修改

### 网络安全
- [ ] 仅开放必要的端口
- [ ] Nginx配置了安全头 (如启用)
- [ ] SSL证书配置正确 (如使用HTTPS)

## 备份和恢复验证

### 数据备份
```bash
# 备份数据库
docker-compose -f docker-compose.prod.yml exec -T app sqlite3 /app/data/pubmed_app.db ".backup /app/data/pubmed_app_backup.db"

# 备份Redis数据
docker-compose -f docker-compose.prod.yml exec -T redis redis-cli BGSAVE
```

- [ ] 数据库备份功能正常
- [ ] Redis持久化配置正确
- [ ] 备份文件可以正常恢复

### 容器重启测试
```bash
# 重启所有服务
docker-compose -f docker-compose.prod.yml restart

# 等待服务恢复
sleep 30

# 验证服务状态
docker-compose -f docker-compose.prod.yml ps
```

- [ ] 服务可以正常重启
- [ ] 重启后数据未丢失
- [ ] 订阅任务自动恢复调度

## 监控和告警

### 日志监控
- [ ] 配置日志聚合工具 (可选)
- [ ] 配置日志告警规则 (可选)
- [ ] 日志轮转正常工作

### 性能监控
- [ ] 配置Prometheus监控 (可选)
- [ ] 配置Grafana仪表板 (可选)
- [ ] 配置资源告警阈值 (可选)

### 健康监控
- [ ] 配置健康检查告警 (推荐)
- [ ] 配置服务宕机通知 (推荐)

## 部署后操作

### 初始配置
1. [ ] 登录管理后台 (http://localhost:5005/admin)
2. [ ] 修改管理员密码
3. [ ] 配置邮箱服务器信息
4. [ ] 配置AI服务提供商 (如需要)
5. [ ] 创建测试订阅并验证推送功能

### 文档和记录
- [ ] 记录部署时间和版本
- [ ] 记录服务器配置信息
- [ ] 记录管理员账号信息 (安全存储)
- [ ] 记录常用运维命令
- [ ] 更新部署文档

## 故障排查清单

### 常见问题检查

#### Redis连接失败
```bash
# 检查Redis服务状态
docker-compose -f docker-compose.prod.yml ps redis

# 查看Redis日志
docker-compose -f docker-compose.prod.yml logs redis

# 手动测试Redis连接
docker-compose -f docker-compose.prod.yml exec redis redis-cli ping
```

#### 应用无法启动
```bash
# 查看应用日志
docker-compose -f docker-compose.prod.yml logs app

# 检查数据库文件
ls -lh data/

# 进入容器调试
docker-compose -f docker-compose.prod.yml exec app /bin/bash
```

#### Worker任务不执行
```bash
# 查看Worker日志
docker-compose -f docker-compose.prod.yml logs worker-1

# 检查队列状态
docker-compose -f docker-compose.prod.yml exec app python -c "from rq_config import get_queue_info; print(get_queue_info())"

# 检查Worker进程
docker-compose -f docker-compose.prod.yml exec worker-1 ps aux
```

#### 内存不足
```bash
# 查看资源使用
docker stats

# 调整docker-compose.prod.yml中的资源限制
# 重启服务
docker-compose -f docker-compose.prod.yml restart
```

## 回滚方案

### 紧急回滚步骤
1. [ ] 停止当前版本服务
   ```bash
   docker-compose -f docker-compose.prod.yml down
   ```

2. [ ] 恢复数据库备份
   ```bash
   cp data/pubmed_app_backup.db data/pubmed_app.db
   ```

3. [ ] 切换到旧版本镜像
   ```bash
   # 修改docker-compose.prod.yml中的镜像tag
   # 或直接拉取旧版本
   docker pull ghcr.io/zhy0504/pubmed-literature-push-web:<old-version>
   ```

4. [ ] 重新启动服务
   ```bash
   docker-compose -f docker-compose.prod.yml up -d
   ```

5. [ ] 验证回滚后的服务状态

## 签字确认

- 部署执行人: ________________  日期: ________
- 验证负责人: ________________  日期: ________
- 项目负责人: ________________  日期: ________

---

**注**: 此清单应在每次生产部署时使用,并保存已完成的检查记录。
