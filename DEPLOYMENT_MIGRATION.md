# 数据库迁移指南

## 容器部署后手动迁移数据库

### 方法1: 进入容器执行迁移脚本（推荐）

```bash
# 进入运行中的容器
docker exec -it pubmed-literature-push bash

# 执行迁移脚本
python migrate_database.py

# 退出容器
exit
```

### 方法2: 直接执行命令（一行完成）

```bash
docker exec -it pubmed-literature-push python migrate_database.py
```

### 方法3: 使用docker-compose

```bash
docker-compose exec web python migrate_database.py
```

## 迁移内容

本次迁移包含以下更新：

1. **订阅筛选功能** - 添加 `filter_config` 和 `use_advanced_filter` 字段
2. **推送频率权限** - 更新用户推送频率权限为 `daily,weekly,monthly`
3. **邀请码功能** - 创建 `invite_code` 和 `invite_code_usage` 表
4. **邮箱配置增强** - 添加 `from_email` 字段，支持分离SMTP用户名和发件人地址

## 迁移验证

迁移完成后，脚本会自动验证：
- 所有字段已正确添加
- 表结构完整
- 系统设置已更新

## 注意事项

- 迁移脚本支持幂等性，重复执行不会出错
- 迁移脚本自动识别Docker环境和本地环境
- 数据库路径：
  - Docker环境: `/app/data/pubmed_app.db`
  - 本地环境: `./pubmed_app.db`

## 新功能使用说明

### 邮箱配置 - 分离用户名和发件人地址

**场景**: 某些SMTP服务（如ahasend）的登录用户名是纯字符串（如 `ls5B8XBWIx`），而发件人地址需要完整邮箱格式（如 `sender@example.com`）

**配置方法**:
1. 进入管理后台 → 邮箱管理
2. 编辑或添加邮箱配置：
   - **SMTP用户名**: 填入登录认证用的用户名（如 `ls5B8XBWIx`）
   - **发件人邮箱地址**: 填入显示为发件人的邮箱（如 `sender@example.com`）
3. 如果留空发件人地址，系统将自动使用SMTP用户名

**兼容性**: 现有配置无需修改，`from_email` 为空时自动使用 `username` 字段
