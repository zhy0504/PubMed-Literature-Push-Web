# PubMed Literature Push - Docker部署指南

## 🐳 Docker部署方案

本项目提供了完整的Docker部署方案，支持开发环境和生产环境的容器化部署。

## 📁 Docker文件结构

```
PubMed-Literature-Push-Web/
├── Dockerfile                 # 主Dockerfile
├── docker-compose.yml         # 生产环境配置
├── docker-compose.dev.yml     # 开发环境配置
├── .dockerignore              # Docker忽略文件
├── .env.example               # 环境变量示例
└── nginx/
    └── nginx.conf             # Nginx配置文件
```

## 🚀 快速部署

### 1. 准备工作

```bash
# 克隆项目
git clone <项目地址>
cd PubMed-Literature-Push-Web

# 复制环境配置文件
cp .env.example .env

# 编辑环境变量（重要！）
nano .env
```

### 2. 开发环境部署

```bash
# 启动开发环境
docker-compose -f docker-compose.dev.yml up -d

# 查看日志
docker-compose -f docker-compose.dev.yml logs -f

# 停止服务
docker-compose -f docker-compose.dev.yml down
```

开发环境访问地址：http://localhost:5003

### 3. 生产环境部署

```bash
# 构建并启动所有服务
docker-compose up -d

# 查看服务状态
docker-compose ps

# 查看日志
docker-compose logs -f

# 停止所有服务
docker-compose down
```

生产环境访问地址：
- HTTP: http://localhost (重定向到HTTPS)
- HTTPS: https://localhost

## 🔧 环境配置

### 必需配置项

编辑 `.env` 文件，至少配置以下项目：

```bash
# 应用密钥（必须修改）
SECRET_KEY=your-very-secret-key-here

# OpenAI API（用于AI检索式生成）
OPENAI_API_KEY=your-openai-api-key

# PubMed API密钥（推荐配置）
PUBMED_API_KEY=your-pubmed-api-key

# 邮件配置（如需邮件功能）
MAIL_SERVER=smtp.gmail.com
MAIL_USERNAME=your-email@gmail.com
MAIL_PASSWORD=your-app-password
```

### 可选配置项

```bash
# 日志级别
LOG_LEVEL=INFO

# 工作进程数
WORKERS=4

# 用户注册开关
USER_REGISTRATION_ENABLED=true
```

## 🏗️ 服务架构

### 生产环境架构

```
Internet
    ↓
Nginx (端口 80/443)
    ↓
Flask App (端口 5003)
    ↓
SQLite Database + Redis Cache
```

### 服务说明

1. **app**: Flask主应用
   - 端口：5003
   - 功能：PubMed文献检索和推送服务

2. **nginx**: 反向代理
   - 端口：80 (HTTP) / 443 (HTTPS)
   - 功能：SSL终端、负载均衡、静态文件服务

3. **redis**: 缓存服务
   - 端口：6379
   - 功能：会话存储、检索结果缓存

## 📊 数据持久化

### 数据卷配置

```yaml
volumes:
  - ./data:/app/data                    # 期刊数据文件
  - ./pubmed_app.db:/app/pubmed_app.db  # SQLite数据库
  - ./logs:/app/logs                    # 应用日志
  - redis_data:/data                    # Redis数据
```

### 数据备份

```bash
# 备份数据库
docker-compose exec app cp /app/pubmed_app.db /app/data/backup_$(date +%Y%m%d).db

# 备份整个数据目录
tar -czf backup_$(date +%Y%m%d).tar.gz data/ pubmed_app.db logs/
```

## 🔍 健康检查和监控

### 健康检查

应用内置健康检查：

```bash
# 检查应用健康状态
curl http://localhost:5003/

# 检查Docker容器健康状态
docker-compose ps
```

### 日志监控

```bash
# 查看应用日志
docker-compose logs app

# 查看Nginx日志
docker-compose logs nginx

# 实时监控所有日志
docker-compose logs -f
```

## 🛠️ 维护操作

### 初始化数据库

```bash
# 进入应用容器
docker-compose exec app bash

# 运行数据库初始化
python setup.py

# 初始化邮箱配置
python init_mail_configs.py
```

### 更新应用

```bash
# 拉取最新代码
git pull

# 重新构建镜像
docker-compose build

# 重启服务
docker-compose up -d
```

### 扩展服务

```bash
# 增加应用实例数量
docker-compose up -d --scale app=3
```

## 🔒 安全配置

### SSL证书配置

将SSL证书文件放置在 `nginx/ssl/` 目录：

```
nginx/ssl/
├── cert.pem
└── key.pem
```

### 防火墙配置

```bash
# 开放必要端口
sudo ufw allow 80
sudo ufw allow 443
sudo ufw allow 22    # SSH
```

### 安全加固

1. 修改默认密码和密钥
2. 配置SSL证书
3. 启用防火墙
4. 定期更新镜像
5. 监控访问日志

## 🚨 故障排除

### 常见问题

1. **端口冲突**
   ```bash
   # 检查端口占用
   netstat -tulpn | grep :5003
   
   # 修改端口配置
   nano docker-compose.yml
   ```

2. **权限问题**
   ```bash
   # 检查文件权限
   ls -la data/ logs/
   
   # 修复权限
   sudo chown -R 1000:1000 data/ logs/
   ```

3. **容器启动失败**
   ```bash
   # 查看详细错误日志
   docker-compose logs app
   
   # 检查配置文件
   docker-compose config
   ```

### 性能优化

1. **调整工作进程数**
   ```bash
   # 在.env中设置
   WORKERS=4
   ```

2. **配置Redis缓存**
   ```bash
   # 启用Redis会话存储
   REDIS_URL=redis://redis:6379/0
   ```

3. **优化数据库**
   ```bash
   # 定期备份和清理
   docker-compose exec app python -c "
   from app import app, db
   with app.app_context():
       # 清理旧日志等
       pass
   "
   ```

## 📝 生产环境检查清单

- [ ] 修改默认SECRET_KEY
- [ ] 配置OpenAI API密钥
- [ ] 配置PubMed API密钥
- [ ] 设置邮件服务器
- [ ] 配置SSL证书
- [ ] 设置防火墙规则
- [ ] 配置日志轮转
- [ ] 设置定期备份
- [ ] 测试应用功能
- [ ] 配置监控告警

## 🆘 支持和帮助

如有问题，请检查：

1. 日志文件：`docker-compose logs`
2. 配置文件：`.env` 和 `docker-compose.yml`
3. 网络连接：防火墙和端口配置
4. 资源使用：`docker stats`

更多详细信息，请参考项目文档或提交Issue。