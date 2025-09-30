# RQ调度器初始化指南

## 问题说明

使用Gunicorn部署时,应用启动不会自动触发批量调度,导致RQ Scheduler调度表为空,Queues无任务。

## 解决方案

### 方法1: 运行初始化脚本(推荐)

在Docker容器内或服务器上执行:

```bash
# 进入应用容器
docker exec -it pubmed-literature-push bash

# 运行初始化脚本
python /app/init_rq_schedules.py

# 退出容器
exit
```

脚本会自动:
1. 检查Redis连接
2. 触发批量调度任务
3. 等待任务执行完成
4. 显示调度结果统计

### 方法2: 通过管理后台手动触发

1. 登录管理后台: `http://your-domain/admin`
2. 进入"推送管理"页面
3. 点击"批量调度所有订阅"按钮(如果界面有提供)

### 方法3: 通过API手动触发

```bash
# 使用curl触发(需要先登录获取session)
curl -X POST http://your-domain/admin/rq/trigger-batch-schedule \
  -H "Cookie: session=YOUR_SESSION_COOKIE"
```

### 方法4: 通过Python控制台

```bash
# 进入应用容器
docker exec -it pubmed-literature-push python

# 执行以下Python代码
from app import app
from rq_config import enqueue_job
from tasks import batch_schedule_all_subscriptions

with app.app_context():
    job = enqueue_job(batch_schedule_all_subscriptions, priority='high')
    print(f"任务ID: {job.id}")
```

## 验证调度是否成功

### 1. 查看RQ Dashboard

访问: `http://your-domain:9181`

检查:
- **Scheduled Jobs**: 应该显示已调度的订阅任务数量
- **Queues**: 当任务到达执行时间时会出现在队列中

### 2. 查看Scheduler日志

```bash
# 查看最近的日志
docker logs pubmed-rq-scheduler-prod | tail -50

# 实时监控日志
docker logs -f pubmed-rq-scheduler-prod
```

成功调度后应该看到:
```
DEBUG - Checking for scheduled jobs
```
每10秒检查一次

### 3. 检查队列状态

访问管理后台的推送管理页面,查看:
- Scheduled Jobs数量 > 0
- 显示待执行任务列表

## 注意事项

1. **首次部署后必须执行初始化**
   - 新部署环境需要手动触发一次批量调度
   - 之后新建订阅会自动调度

2. **定期检查调度状态**
   - 如果发现订阅未推送,检查Scheduler日志
   - 确认Scheduled Jobs数量是否正常

3. **重启服务后的处理**
   - RQ Scheduler的调度数据存储在Redis中
   - 重启服务不会丢失已调度的任务
   - 如果Redis数据丢失,需要重新执行初始化

## 自动化方案(可选)

### 在docker-entrypoint.sh中添加自动初始化

编辑 `docker-entrypoint.sh`,在主应用启动后添加:

```bash
# 等待应用完全启动
sleep 10

# 仅在app容器中执行初始化(避免worker/scheduler重复执行)
if [ "$CONTAINER_ROLE" = "app" ]; then
    echo "执行RQ调度器初始化..."
    python /app/init_rq_schedules.py &
fi
```

然后在docker-compose中设置环境变量:

```yaml
environment:
  - CONTAINER_ROLE=app  # 仅app容器设置此变量
```

## 常见问题

### Q: 为什么Queues一直是空的?

A: 这是正常的!任务在未到达执行时间前保存在Scheduler的调度表中,不在Queue里。只有到达执行时间时,Scheduler才会将任务移入Queue,Worker立即处理。

### Q: Scheduled Jobs数量为0怎么办?

A: 说明批量调度未执行或未成功,按照本文方法1重新执行初始化脚本。

### Q: 如何查看具体调度了哪些订阅?

A: 在RQ Dashboard的"Scheduled Jobs"标签页可以看到所有已调度任务的详细信息,包括执行时间、参数等。

### Q: 新建订阅后需要手动调度吗?

A: 不需要。代码中已经处理了新建/编辑订阅时的自动调度逻辑(tasks.py:68和tasks.py:81)。

## 相关文件

- `init_rq_schedules.py` - 初始化脚本
- `tasks.py` - 任务定义和调度逻辑
- `rq_config.py` - RQ配置
- `rq_scheduler_runner.py` - Scheduler进程
- `app.py:1898-1902` - 自动初始化逻辑(仅python app.py运行时生效)