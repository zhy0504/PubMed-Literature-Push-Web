# Docker 容器环境数据库迁移指南

## 场景1: 容器已运行,手动执行迁移

### 方法1: 进入容器执行

```bash
# 1. 查看运行中的容器
docker ps

# 2. 进入主应用容器
docker exec -it <容器名称或ID> bash

# 3. 在容器内执行迁移脚本
python migrate_database.py

# 4. 退出容器
exit
```

**示例:**
```bash
# 如果容器名称是 pubmed-literature-push-web-app-1
docker exec -it pubmed-literature-push-web-app-1 bash
python migrate_database.py
exit
```

### 方法2: 直接执行命令(不进入容器)

```bash
docker exec <容器名称或ID> python migrate_database.py
```

**示例:**
```bash
docker exec pubmed-literature-push-web-app-1 python migrate_database.py
```

### 方法3: 使用 docker-compose

```bash
# 进入容器
docker-compose -f docker-compose.prod.yml exec app bash
python migrate_database.py
exit

# 或直接执行
docker-compose -f docker-compose.prod.yml exec app python migrate_database.py
```

---

## 场景2: 重新部署时自动迁移

如果想在每次容器启动时自动执行迁移,可以修改 `docker-entrypoint.sh`。

**不过通常不建议自动迁移,因为:**
- 迁移失败可能导致容器启动失败
- 无法控制迁移时机
- 生产环境最好手动控制迁移

---

## 场景3: 数据库文件映射到宿主机

如果使用了数据卷映射,也可以在宿主机上直接操作数据库:

### 查看数据卷位置

```bash
# 查看容器详情
docker inspect <容器名称> | grep -A 10 Mounts

# 或使用 docker-compose
docker-compose -f docker-compose.prod.yml config | grep -A 5 volumes
```

### 本地执行迁移

如果数据库文件映射到了宿主机的某个目录(如 `./data`):

```bash
# 在项目根目录执行
python migrate_database.py
```

---

## 常见问题

### Q1: 如何确认迁移是否成功?

查看迁移脚本输出,应该看到:
```
[OK] 数据库迁移完成！

提示:
  - 可在管理后台'系统设置'中开启'需要邀请码注册'
  - 在'邀请码管理'中生成邀请码
```

### Q2: 迁移失败怎么办?

1. 查看错误信息
2. 备份数据库文件
3. 检查数据库文件权限
4. 尝试在容器外部使用 SQLite 工具检查数据库

### Q3: 可以重复执行迁移吗?

可以。迁移脚本有幂等性检查,会跳过已存在的表和字段:
- `[SKIP] invite_code 表已存在，跳过创建`
- `[SKIP] require_invite_code 设置已存在`

### Q4: 如何验证邀请码功能已启用?

```bash
# 进入容器
docker exec -it <容器名称> bash

# 查询数据库
sqlite3 /app/data/pubmed_app.db "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'invite%';"

# 应该看到:
# invite_code
# invite_code_usage
```

---

## Docker Compose 完整示例

### docker-compose.prod.yml 配置示例

```yaml
services:
  app:
    image: ghcr.io/zhy0504/pubmed-literature-push-web:latest
    volumes:
      - ./data:/app/data  # 数据持久化
      - ./logs:/app/logs  # 日志持久化
    environment:
      - TZ=Asia/Shanghai
```

### 部署后迁移步骤

```bash
# 1. 启动服务
docker-compose -f docker-compose.prod.yml up -d

# 2. 等待服务启动完成(约10-30秒)
docker-compose -f docker-compose.prod.yml logs -f app

# 3. 执行数据库迁移
docker-compose -f docker-compose.prod.yml exec app python migrate_database.py

# 4. 验证迁移结果
docker-compose -f docker-compose.prod.yml exec app sqlite3 /app/data/pubmed_app.db \
  "SELECT key, value FROM system_setting WHERE key='require_invite_code';"
```

---

## 生产环境最佳实践

1. **迁移前备份数据库**
   ```bash
   # 复制数据库文件
   docker cp <容器名称>:/app/data/pubmed_app.db ./backup_$(date +%Y%m%d_%H%M%S).db
   ```

2. **使用数据卷持久化**
   ```yaml
   volumes:
     - ./data:/app/data
   ```

3. **记录迁移日志**
   ```bash
   docker-compose -f docker-compose.prod.yml exec app python migrate_database.py > migration_$(date +%Y%m%d_%H%M%S).log
   ```

4. **验证迁移结果**
   - 检查迁移脚本输出
   - 登录管理后台验证功能
   - 测试邀请码注册流程
