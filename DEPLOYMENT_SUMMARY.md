# 邀请码功能部署总结

## ✅ 已完成的修改

### 1. 数据库初始化 (setup.py)
- ✅ 添加 `invite_code` 表创建
- ✅ 添加 `invite_code_usage` 表创建
- ✅ 添加 `require_invite_code` 系统设置

**影响**: 新部署的系统会自动包含邀请码功能相关的数据表

### 2. 数据库迁移 (migrate_database.py)
- ✅ 包含邀请码表的迁移逻辑
- ✅ 支持从旧版本升级

**影响**: 已运行的旧系统可以通过迁移脚本升级

### 3. 文档更新
- ✅ 创建 `DOCKER_MIGRATION_GUIDE.md` - Docker 环境迁移指南
- ✅ 更新 `README.md` - 包含容器环境的迁移说明

## 📋 部署场景说明

### 场景 1: 全新部署

**步骤:**
1. 部署容器 (会自动执行 `setup.py`)
2. `setup.py` 会创建所有表,包括邀请码表
3. 无需手动迁移

**结果**: 邀请码功能开箱可用

---

### 场景 2: 从旧版本升级

**步骤:**
1. 升级到新版本代码
2. 手动执行数据库迁移:

**本地环境:**
```bash
python migrate_database.py
```

**Docker 容器环境:**
```bash
# 查看容器名称
docker ps

# 方法1: 进入容器执行
docker exec -it <容器名称> bash
python migrate_database.py
exit

# 方法2: 直接执行 (推荐)
docker exec <容器名称> python migrate_database.py

# 方法3: 使用 docker-compose
docker-compose -f docker-compose.prod.yml exec app python migrate_database.py
```

**结果**: 添加邀请码相关表和设置

---

## 🎯 使用邀请码功能

### 1. 启用功能
登录管理后台 → 系统设置 → 勾选「需要邀请码注册」→ 保存

### 2. 生成邀请码
管理后台 → 邀请码管理 → 生成邀请码

### 3. 用户注册
用户访问 `/register` 时需要输入邀请码

---

## 📁 相关文件

| 文件 | 说明 |
|------|------|
| `app.py` | 邀请码模型、路由、业务逻辑 |
| `setup.py` | 新部署时自动创建邀请码表 |
| `migrate_database.py` | 旧版本升级时的迁移脚本 |
| `README.md` | 功能说明和使用指南 |
| `DOCKER_MIGRATION_GUIDE.md` | Docker 环境详细迁移指南 |

---

## ⚠️ 注意事项

1. **新部署**: 自动包含邀请码表,无需迁移
2. **旧版本升级**: 必须手动执行 `migrate_database.py`
3. **容器环境**: 需要进入容器或使用 `docker exec` 执行迁移
4. **迁移安全**: 迁移脚本有幂等性,可以重复执行
5. **备份建议**: 生产环境迁移前建议备份数据库

---

## 🔍 验证迁移是否成功

### 方法1: 查看迁移脚本输出
应该看到:
```
[OK] invite_code 表创建成功
[OK] invite_code_usage 表创建成功
[OK] require_invite_code 设置添加成功
```

### 方法2: 查询数据库
```bash
# 容器环境
docker exec <容器名称> sqlite3 /app/data/pubmed_app.db \
  "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'invite%';"

# 本地环境
sqlite3 pubmed_app.db \
  "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'invite%';"
```

应该看到:
```
invite_code
invite_code_usage
```

### 方法3: 检查管理后台
- 登录管理后台
- 侧边栏应该有「邀请码管理」菜单
- 系统设置应该有「需要邀请码注册」选项

---

## 📞 常见问题

**Q: 容器重启后需要重新迁移吗?**
A: 不需要。迁移只需执行一次,数据持久化在数据卷中。

**Q: 如何确认容器名称?**
A: 执行 `docker ps` 查看运行中的容器列表。

**Q: 迁移失败怎么办?**
A:
1. 查看错误信息
2. 备份数据库
3. 检查文件权限
4. 参考 `DOCKER_MIGRATION_GUIDE.md` 故障排查章节

**Q: 可以在不停止容器的情况下迁移吗?**
A: 可以。使用 `docker exec` 直接在运行中的容器执行迁移。

**Q: 数据库文件在哪里?**
A:
- 容器内: `/app/data/pubmed_app.db`
- 宿主机: 取决于 docker-compose.yml 的 volumes 配置

---

## 📚 参考文档

- [README.md](README.md) - 项目主文档
- [DOCKER_MIGRATION_GUIDE.md](DOCKER_MIGRATION_GUIDE.md) - Docker 迁移详细指南
- [邀请码功能说明](README.md#邀请码注册) - README 中的邀请码章节
