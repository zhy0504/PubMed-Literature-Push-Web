# RQ版本部署说明

PubMed Literature Push系统已成功改造为使用RQ (Redis Queue) 替代APScheduler。

## 主要改动

### 1. 新增文件
- `rq_config.py` - RQ配置和队列管理
- `tasks.py` - RQ任务定义
- `rq_worker.py` - Worker启动脚本

### 2. 依赖更新
- 新增: `rq==1.15.1`
- 新增: `redis==5.0.1` 
- 新增: `rq-dashboard==0.6.1`
- 保留: `APScheduler==3.10.4` (降级备用)

### 3. 核心功能变更
- **调度方式**: 从定时检查改为动态任务调度
- **推送逻辑**: 每个订阅独立排队执行
- **监控方式**: RQ队列状态监控
- **容错机制**: 双重保障(RQ + APScheduler降级)

## 部署步骤

### 1. 安装Redis
```bash
# Ubuntu/Debian
sudo apt update && sudo apt install redis-server

# CentOS/RHEL  
sudo yum install redis

# Docker
docker run -d --name redis -p 6379:6379 redis:latest
```

### 2. 安装Python依赖
```bash
pip install -r requirements.txt
```

### 3. 配置环境变量
```bash
# Redis连接(可选，默认localhost:6379)
export REDIS_URL=redis://localhost:6379/0

# RQ Worker配置(可选)
export RQ_WORKER_NAME=pubmed-worker-1
export RQ_QUEUES=high,default,low
```

### 4. 启动服务

#### 方式一：传统启动
```bash
# 1. 启动Redis
redis-server

# 2. 启动Flask应用
python app.py

# 3. 启动RQ Worker (新终端)
python rq_worker.py
```

#### 方式二：Docker部署
```bash
# 使用现有Docker配置，已自动包含Redis
docker-compose up -d
```

### 5. 验证部署
访问管理页面查看调度器状态:
- 访问: `http://localhost:5003/admin/push`
- 检查: 调度器模式显示为"rq"
- 验证: Redis连接状态正常

## 工作原理

### RQ调度流程
1. **应用启动**: 初始化RQ调度器，批量调度所有活跃订阅
2. **动态调度**: 每个订阅根据推送时间独立排队
3. **任务执行**: Worker进程异步处理推送任务
4. **自动重调度**: 任务完成后自动计算并调度下次推送

### 队列优先级
- **high**: 立即推送、测试任务
- **default**: 定时推送任务  
- **low**: 统计分析任务

### 降级机制
- RQ连接失败时自动降级到APScheduler
- 保留原有的调度器监控和恢复功能
- 确保服务持续可用

## 管理功能

### Web管理界面
- `/admin/push` - 推送管理页面(已集成RQ状态)
- `/admin/rq/status` - RQ状态API
- RQ管理操作:
  - 批量调度订阅
  - 立即推送指定订阅  
  - 清空失败任务
  - 连接测试

### RQ Dashboard (可选)
```bash
# 安装并启动RQ Dashboard
rq-dashboard -H localhost -p 9181

# 访问: http://localhost:9181
```

## 优势对比

| 特性 | APScheduler | RQ方案 |
|------|------------|--------|
| **调度精度** | 每小时批量检查 | 精确到分钟的动态调度 |
| **资源利用** | 持续占用内存 | 按需执行任务 |
| **扩展性** | 单进程限制 | 多Worker水平扩展 |
| **监控能力** | 基础状态检查 | 丰富的队列监控 |
| **错误处理** | 简单重试 | 完善的失败任务管理 |
| **维护性** | 进程内调度 | 独立Worker进程 |

## 故障排除

### Redis连接问题
```bash
# 检查Redis服务
redis-cli ping

# 查看Redis日志
sudo tail -f /var/log/redis/redis-server.log
```

### Worker进程问题
```bash
# 检查Worker状态
ps aux | grep rq_worker

# 查看Worker日志
tail -f /app/logs/rq_worker.log
```

### 降级模式
如果RQ完全不可用，系统会自动降级到APScheduler模式：
- 管理页面显示"apscheduler"模式
- 恢复原有的每小时检查机制
- 保持基本的推送功能

## 注意事项

1. **Redis持久化**: 建议启用Redis持久化避免任务丢失
2. **Worker监控**: 生产环境建议使用进程管理器(如supervisord)管理Worker
3. **任务幂等性**: 推送任务支持重复执行，避免重复推送
4. **时区处理**: 保持与原系统一致的时区设置
5. **资源监控**: 监控Redis内存使用和队列长度

## 下一步优化

1. **多Worker部署**: 根据负载启动多个Worker进程
2. **任务优先级**: 细化队列优先级策略
3. **监控告警**: 集成更完善的监控和告警机制
4. **性能调优**: 根据实际使用情况优化Redis配置

---

**改造完成！** RQ方案提供了更灵活、可扩展的定时推送架构。