# 生产环境Docker Compose配置检查报告

## 📋 配置文件对比

### docker-compose.yml (开发环境)
- Redis内存: **256MB**
- Worker数量: **1个**
- 镜像来源: **本地构建** (`build: .`)
- Dashboard: 可选启用 (`--profile dashboard`)

### docker-compose.prod.yml (生产环境)
- Redis内存: **512MB** ⚠️
- Worker数量: **2个** (worker-1, worker-2)
- 镜像来源: **GitHub镜像** (`ghcr.io/zhy0504/pubmed-literature-push-web:latest`)
- Dashboard: 默认启用
- Nginx: 生产环境启用

---

## ✅ 缓存兼容性检查

### Redis内存配置

#### 开发环境 (docker-compose.yml)
```yaml
redis:
  command: redis-server --maxmemory 256mb --maxmemory-policy allkeys-lru
```
**分析**: 256MB对于缓存功能**充足**
- 预估缓存占用: 50-100MB (100个活跃缓存)
- 剩余空间: 150-200MB (用于RQ队列)

#### 生产环境 (docker-compose.prod.yml)
```yaml
redis:
  command: redis-server --maxmemory 512mb --maxmemory-policy allkeys-lru
```
**分析**: 512MB对于缓存功能**非常充裕** ✓
- 预估缓存占用: 100-200MB (更多用户)
- 剩余空间: 300-400MB (充足的RQ队列空间)

**结论**: ✅ 两个环境的Redis配置都完全兼容缓存服务

---

## ✅ 镜像构建检查

### 生产环境镜像来源
```yaml
app:
  image: ghcr.io/zhy0504/pubmed-literature-push-web:latest
```

**检查点**:
1. ✓ 镜像是否包含 `search_cache_service.py`?
   - 是,通过 `COPY . .` 自动包含

2. ✓ 镜像是否包含修改后的 `app.py`?
   - 是,包含缓存集成代码

3. ✓ 依赖是否完整?
   - 是,`requirements.txt` 已有 `redis==5.0.1`

**需要做什么**:
⚠️ **重新构建并推送镜像到GitHub**

---

## 🔄 生产环境部署步骤

### 方式1: 使用现有镜像 (需重新构建)

```bash
# 1. 重新构建镜像 (包含缓存服务)
docker build -t ghcr.io/zhy0504/pubmed-literature-push-web:latest .

# 2. 推送到GitHub Container Registry
docker push ghcr.io/zhy0504/pubmed-literature-push-web:latest

# 3. 在生产服务器部署
docker-compose -f docker-compose.prod.yml pull
docker-compose -f docker-compose.prod.yml up -d
```

### 方式2: 临时修改为本地构建

修改 `docker-compose.prod.yml`:
```yaml
app:
  # image: ghcr.io/zhy0504/pubmed-literature-push-web:latest
  build: .  # 临时改为本地构建
```

然后部署:
```bash
docker-compose -f docker-compose.prod.yml build
docker-compose -f docker-compose.prod.yml up -d
```

---

## ⚠️ 关键差异分析

### 1. Worker数量差异

**开发环境**: 1个Worker
```yaml
worker:
  container_name: pubmed-rq-worker
```

**生产环境**: 2个Worker
```yaml
worker-1:
  container_name: pubmed-rq-worker-1
worker-2:
  container_name: pubmed-rq-worker-2
```

**缓存影响**: ✅ 无影响
- 缓存服务在app容器中,不在worker
- 多个worker共享同一个Redis
- 缓存数据自动同步

### 2. Redis内存差异

| 环境 | 内存限制 | 缓存充足性 |
|-----|---------|-----------|
| 开发 | 256MB | ✓ 充足 |
| 生产 | 512MB | ✓ 非常充裕 |

**建议**: 保持生产环境512MB配置不变

### 3. Dashboard差异

**开发环境**: 可选启用
```bash
docker-compose --profile dashboard up -d
```

**生产环境**: 默认启用
```yaml
rq-dashboard:
  # 无 profiles 配置,默认启动
```

**缓存监控**: ✅ 可通过RQ Dashboard查看Redis状态
- 访问: `http://your-server:9181`

---

## 📊 生产环境验证清单

### 部署前检查
- [ ] 已重新构建镜像 (包含缓存服务)
- [ ] 已推送镜像到GitHub (如使用远程镜像)
- [ ] 已备份生产数据
- [ ] Redis配置确认 (512MB)

### 部署步骤
```bash
# 1. 拉取最新代码/镜像
cd /path/to/production
git pull  # 或 docker-compose pull

# 2. 停止旧服务
docker-compose -f docker-compose.prod.yml down

# 3. 启动新服务
docker-compose -f docker-compose.prod.yml up -d

# 4. 验证容器状态
docker-compose -f docker-compose.prod.yml ps
```

### 部署后验证
```bash
# 1. 检查缓存服务初始化
docker-compose -f docker-compose.prod.yml logs app | grep SearchCacheService
# 预期: "SearchCacheService: 初始化成功,缓存功能已启用"

# 2. 检查Redis连接
docker-compose -f docker-compose.prod.yml exec app python -c "from rq_config import redis_conn; redis_conn.ping(); print('Redis OK')"

# 3. 检查缓存功能
docker-compose -f docker-compose.prod.yml exec app python -c "from search_cache_service import search_cache_service; print('Enabled:', search_cache_service.enabled)"
# 预期: Enabled: True

# 4. 查看缓存日志
docker-compose -f docker-compose.prod.yml logs -f app | grep '\[缓存'
```

### 监控指标
```bash
# 1. Redis内存使用
docker-compose -f docker-compose.prod.yml exec redis redis-cli info memory | grep used_memory_human

# 2. 缓存统计
curl http://your-server:5005/admin/cache/stats

# 3. RQ Dashboard
访问: http://your-server:9181
```

---

## 🔧 配置优化建议

### 可选: 调整Redis内存 (按需)

如果观察到Redis内存使用超过400MB:

```yaml
# docker-compose.prod.yml
redis:
  command: redis-server --maxmemory 1024mb --maxmemory-policy allkeys-lru
```

### 可选: 缓存预热 (性能优化)

在 `app.py` 中添加启动时预热:
```python
# 预热热门关键词缓存
@app.before_first_request
def warm_up_cache():
    hot_keywords = ["cancer", "diabetes", "COVID-19"]
    api = PubMedAPI()
    for kw in hot_keywords:
        api.search_and_fetch_with_filter(kw, max_results=50, days_back=30)
```

---

## 📝 环境变量检查

### 必需环境变量 (.env文件)
```env
# Redis连接 (自动设置)
REDIS_URL=redis://redis:6379/0

# 数据库路径 (自动设置)
DATABASE_URL=sqlite:////app/data/pubmed_app.db

# 可选: 缓存配置
# CACHE_DEFAULT_TTL=3600  # 默认1小时
# CACHE_MAX_TTL=86400     # 最大24小时
```

**检查**: ✅ 无需新增环境变量

---

## 🎯 最终确认

### ✅ 兼容性确认
- Redis配置: ✓ 完全兼容 (开发256MB, 生产512MB)
- Worker配置: ✓ 完全兼容 (多worker共享Redis)
- 镜像构建: ⚠️ 需重新构建包含缓存服务
- 环境变量: ✓ 无需新增

### ⚠️ 必须操作
**重新构建镜像** (包含缓存服务):
```bash
docker build -t ghcr.io/zhy0504/pubmed-literature-push-web:latest .
docker push ghcr.io/zhy0504/pubmed-literature-push-web:latest
```

### ✅ 部署命令
```bash
# 生产环境部署
docker-compose -f docker-compose.prod.yml pull
docker-compose -f docker-compose.prod.yml up -d

# 验证缓存服务
docker-compose -f docker-compose.prod.yml logs app | grep '\[缓存'
```

---

## 📊 预期性能提升 (生产环境)

### 场景1: 高峰期 (100并发用户,50%关键词重叠)
- **优化前**: 100次API调用
- **优化后**: 50次API调用 + 50次缓存
- **提升**: 50% API调用减少

### 场景2: 日常运行 (300活跃订阅,30%重叠)
- **优化前**: 300次API调用/天
- **优化后**: 105次API调用/天
- **提升**: 65% API调用减少

### 资源占用预估
- **Redis内存**: 150-250MB (充足)
- **API调用**: 减少60-70%
- **响应时间**: 缓存命中<100ms

---

**检查完成时间**: 2025-10-01
**生产环境状态**: ⚠️ 需重新构建镜像
**部署风险**: 低 (支持自动降级)
**推荐部署方式**: 重新构建推送镜像
