# L1缓存层 - Docker快速部署

## 🚀 一键部署

```bash
# 停止现有服务
docker-compose down

# 重新构建 (包含缓存服务)
docker-compose build

# 启动所有服务
docker-compose up -d

# 验证缓存服务
docker-compose logs -f app | grep '\[缓存'
```

**预期日志输出**:
```
INFO: SearchCacheService: 初始化成功,缓存功能已启用
[缓存未命中] 调用PubMed API搜索: cancer
[缓存写入] 已缓存 45 篇文章
[缓存命中-精确] 直接使用 45 篇缓存文章
```

---

## ✅ Docker兼容性确认

### 无需修改的文件
- ✅ `Dockerfile` - 自动复制所有.py文件
- ✅ `docker-compose.yml` - Redis配置已充足
- ✅ `requirements.txt` - Redis/RQ依赖已存在
- ✅ `.dockerignore` - 正确排除测试文件

### 自动包含的文件
- ✅ `search_cache_service.py` (16KB)
- ✅ `app.py` (已集成缓存)
- ✅ `rq_config.py` (Redis连接)

---

## 📊 服务架构

```
┌─────────────────────────────────────┐
│  Redis (端口6379)                   │
│  - 256MB内存限制                     │
│  - LRU淘汰策略                       │
│  - 缓存+队列双重用途                 │
└──────────┬──────────────────────────┘
           │
      ┌────┴────┬──────────────┐
      │         │              │
┌─────▼────┐ ┌─▼─────┐ ┌─────▼────┐
│   App    │ │Worker │ │Dashboard │
│ (5005)   │ │  RQ   │ │  (9181)  │
│          │ │       │ │ (可选)    │
│ 缓存集成 │ │       │ │          │
└──────────┘ └───────┘ └──────────┘
```

---

## 🔍 验证步骤

### 1. 检查容器状态
```bash
docker-compose ps

# 预期输出:
NAME                    STATUS
pubmed-redis            Up (healthy)
pubmed-literature-push  Up (healthy)
pubmed-rq-worker        Up (healthy)
```

### 2. 检查Redis连接
```bash
docker-compose exec app python -c "from rq_config import redis_conn; redis_conn.ping(); print('✓ Redis连接正常')"
```

### 3. 检查缓存服务
```bash
docker-compose exec app python -c "from search_cache_service import search_cache_service; print('Enabled:', search_cache_service.enabled)"

# 预期输出: Enabled: True
```

### 4. 访问管理接口
```bash
# 获取缓存统计
curl http://localhost:5005/admin/cache/stats

# 需要先登录获取session cookie
```

---

## 📈 性能监控

### Redis内存使用
```bash
docker-compose exec redis redis-cli info memory | grep used_memory_human
```

### 缓存统计
```bash
# 通过Web界面
http://localhost:5005/admin/cache/stats

# 或命令行
docker-compose exec app python -c "
from search_cache_service import search_cache_service
import json
print(json.dumps(search_cache_service.get_cache_stats(), indent=2))
"
```

### 应用日志
```bash
# 实时查看缓存日志
docker-compose logs -f app | grep '\[缓存'

# 查看最近的缓存活动
docker-compose exec app tail -50 /app/logs/app.log | grep '\[缓存'
```

---

## 🔧 常见问题

### Q1: 缓存服务未启用?
```bash
# 检查Redis是否正常
docker-compose ps redis
docker-compose exec redis redis-cli ping

# 重启app容器
docker-compose restart app
```

### Q2: 内存不足?
```bash
# 检查Redis内存
docker-compose exec redis redis-cli info memory

# 如需扩容,修改 docker-compose.yml:
redis:
  command: redis-server --maxmemory 512mb ...
```

### Q3: 查看详细日志?
```bash
# 应用日志
docker-compose logs app

# Redis日志
docker-compose exec redis redis-cli monitor

# Worker日志
docker-compose logs worker
```

---

## 🎯 性能对比

### 部署前 (无缓存)
```
10个用户订阅"cancer" = 10次API调用 (30-50秒)
100个活跃订阅 = 100次API调用/天
```

### 部署后 (有缓存)
```
10个用户订阅"cancer" = 1次API调用 + 9次缓存 (3-5秒)
100个活跃订阅(30%重叠) = 35次API调用/天
节省: 65次API调用 = 65%优化
```

---

## 📚 相关文档

- **[CACHE_L1_SUMMARY.md](CACHE_L1_SUMMARY.md)** - 功能总览
- **[CACHE_L1_DEPLOY_CHECKLIST.md](CACHE_L1_DEPLOY_CHECKLIST.md)** - 完整检查清单
- **[CACHE_L1_GUIDE.md](CACHE_L1_GUIDE.md)** - 详细使用文档
- **[DOCKER_CACHE_CHECK.md](DOCKER_CACHE_CHECK.md)** - Docker兼容性检查

---

**部署时间**: < 5分钟
**风险等级**: 低 (自动降级)
**回滚时间**: < 2分钟
**生产就绪**: ✅
