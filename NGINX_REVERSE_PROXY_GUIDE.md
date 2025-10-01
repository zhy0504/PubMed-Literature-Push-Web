# Nginx 反向代理配置参考

> **重要**: 本项目的 Docker Compose 配置中**不包含** Nginx 容器。
> Nginx 反向代理应在**宿主机**或**独立负载均衡服务器**上配置。

---

## 为什么不在容器内部署 Nginx？

1. **灵活性**: 宿主机 Nginx 可以同时代理多个应用
2. **SSL 管理**: 统一的证书管理和自动续期（Let's Encrypt）
3. **性能**: 减少容器网络开销
4. **维护性**: 独立更新 Nginx 不影响应用容器
5. **高可用**: 可以配置多个 Nginx 节点进行负载均衡

---

## 宿主机 Nginx 配置示例

### 1. 基础 HTTP 配置

**文件路径**: `/etc/nginx/sites-available/pubmed-app`

```nginx
upstream pubmed_backend {
    # Docker 应用容器地址
    server 127.0.0.1:5005 max_fails=3 fail_timeout=30s;

    # 如果有多个应用实例，可以添加负载均衡
    # server 127.0.0.1:5006 max_fails=3 fail_timeout=30s;

    keepalive 32;
}

server {
    listen 80;
    server_name your-domain.com;  # 修改为你的域名

    # 日志配置
    access_log /var/log/nginx/pubmed_access.log;
    error_log /var/log/nginx/pubmed_error.log warn;

    # 客户端最大请求体大小
    client_max_body_size 20M;

    # Gzip 压缩
    gzip on;
    gzip_vary on;
    gzip_proxied any;
    gzip_comp_level 6;
    gzip_types text/plain text/css text/xml text/javascript
               application/javascript application/xml+rss application/json;

    # 主应用代理
    location / {
        proxy_pass http://pubmed_backend;
        proxy_http_version 1.1;
        proxy_set_header Connection "";

        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # 超时配置
        proxy_connect_timeout 60s;
        proxy_send_timeout 60s;
        proxy_read_timeout 600s;

        # 缓冲配置
        proxy_buffering on;
        proxy_buffer_size 4k;
        proxy_buffers 8 4k;
    }

    # 健康检查（无日志）
    location /health {
        proxy_pass http://pubmed_backend/;
        access_log off;
    }
}
```

### 2. HTTPS 配置（推荐生产环境）

```nginx
upstream pubmed_backend {
    server 127.0.0.1:5005 max_fails=3 fail_timeout=30s;
    keepalive 32;
}

# HTTP 重定向到 HTTPS
server {
    listen 80;
    server_name your-domain.com;
    return 301 https://$server_name$request_uri;
}

# HTTPS 服务器
server {
    listen 443 ssl http2;
    server_name your-domain.com;

    # SSL 证书配置（Let's Encrypt）
    ssl_certificate /etc/letsencrypt/live/your-domain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/your-domain.com/privkey.pem;

    # SSL 优化配置
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers 'ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384';
    ssl_prefer_server_ciphers off;
    ssl_session_cache shared:SSL:10m;
    ssl_session_timeout 10m;
    ssl_session_tickets off;

    # HSTS (HTTP Strict Transport Security)
    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;

    # 安全头
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-XSS-Protection "1; mode=block" always;
    add_header Referrer-Policy "no-referrer-when-downgrade" always;

    # 日志配置
    access_log /var/log/nginx/pubmed_ssl_access.log;
    error_log /var/log/nginx/pubmed_ssl_error.log warn;

    client_max_body_size 20M;

    # Gzip 压缩
    gzip on;
    gzip_vary on;
    gzip_proxied any;
    gzip_comp_level 6;
    gzip_types text/plain text/css text/xml text/javascript
               application/javascript application/xml+rss application/json;

    # 主应用代理
    location / {
        proxy_pass http://pubmed_backend;
        proxy_http_version 1.1;
        proxy_set_header Connection "";

        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        proxy_connect_timeout 60s;
        proxy_send_timeout 60s;
        proxy_read_timeout 600s;
    }

    # 健康检查
    location /health {
        proxy_pass http://pubmed_backend/;
        access_log off;
    }
}
```

### 3. RQ Dashboard 代理配置（可选）

如果需要通过域名访问 RQ Dashboard：

```nginx
upstream rq_dashboard {
    server 127.0.0.1:9181;
}

server {
    listen 443 ssl http2;
    server_name rq.your-domain.com;  # RQ Dashboard 子域名

    # SSL 证书（与主域名相同或单独申请）
    ssl_certificate /etc/letsencrypt/live/your-domain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/your-domain.com/privkey.pem;

    # 基本认证（额外安全层）
    auth_basic "RQ Dashboard";
    auth_basic_user_file /etc/nginx/.htpasswd;

    location / {
        proxy_pass http://rq_dashboard;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

---

## 部署步骤

### 1. 安装 Nginx（如未安装）

```bash
# Ubuntu/Debian
sudo apt update
sudo apt install nginx

# CentOS/RHEL
sudo yum install nginx

# 启动 Nginx
sudo systemctl start nginx
sudo systemctl enable nginx
```

### 2. 配置站点

```bash
# 创建配置文件
sudo nano /etc/nginx/sites-available/pubmed-app

# 粘贴上面的配置（根据需求选择 HTTP 或 HTTPS）

# 创建软链接启用站点
sudo ln -s /etc/nginx/sites-available/pubmed-app /etc/nginx/sites-enabled/

# 测试配置
sudo nginx -t

# 重载 Nginx
sudo systemctl reload nginx
```

### 3. 配置 SSL 证书（推荐使用 Let's Encrypt）

```bash
# 安装 Certbot
sudo apt install certbot python3-certbot-nginx  # Ubuntu/Debian
# 或
sudo yum install certbot python3-certbot-nginx  # CentOS/RHEL

# 获取证书并自动配置 Nginx
sudo certbot --nginx -d your-domain.com

# 测试自动续期
sudo certbot renew --dry-run

# 查看证书状态
sudo certbot certificates
```

### 4. 配置基本认证（可选，用于 RQ Dashboard）

```bash
# 安装 htpasswd 工具
sudo apt install apache2-utils  # Ubuntu/Debian
# 或
sudo yum install httpd-tools    # CentOS/RHEL

# 创建用户
sudo htpasswd -c /etc/nginx/.htpasswd admin

# 添加更多用户（不使用 -c 参数）
sudo htpasswd /etc/nginx/.htpasswd user2
```

---

## 负载均衡配置（多实例场景）

如果运行多个应用实例，可以配置负载均衡：

```nginx
upstream pubmed_backend {
    # 轮询（默认）
    server 127.0.0.1:5005 max_fails=3 fail_timeout=30s;
    server 127.0.0.1:5006 max_fails=3 fail_timeout=30s;

    # IP Hash（会话保持）
    # ip_hash;

    # 最少连接
    # least_conn;

    # 权重
    # server 127.0.0.1:5005 weight=2;
    # server 127.0.0.1:5006 weight=1;

    keepalive 32;
}
```

---

## 防火墙配置

确保防火墙允许 Nginx 访问：

```bash
# UFW (Ubuntu)
sudo ufw allow 'Nginx Full'
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp

# Firewalld (CentOS)
sudo firewall-cmd --permanent --add-service=http
sudo firewall-cmd --permanent --add-service=https
sudo firewall-cmd --reload
```

---

## 监控和日志

### 查看 Nginx 日志

```bash
# 实时查看访问日志
sudo tail -f /var/log/nginx/pubmed_access.log

# 实时查看错误日志
sudo tail -f /var/log/nginx/pubmed_error.log

# 查看 Nginx 状态
sudo systemctl status nginx
```

### 配置日志轮转

Nginx 默认已配置日志轮转，配置文件在：
```bash
/etc/logrotate.d/nginx
```

如需自定义：
```bash
sudo nano /etc/logrotate.d/nginx
```

示例配置：
```
/var/log/nginx/*.log {
    daily
    rotate 14
    compress
    delaycompress
    notifempty
    create 0640 www-data adm
    sharedscripts
    postrotate
        [ -f /var/run/nginx.pid ] && kill -USR1 `cat /var/run/nginx.pid`
    endscript
}
```

---

## 性能优化建议

### 1. 启用 HTTP/2

```nginx
listen 443 ssl http2;  # 已在上面的配置中启用
```

### 2. 启用 Brotli 压缩（可选，需要编译模块）

```nginx
brotli on;
brotli_comp_level 6;
brotli_types text/plain text/css text/xml text/javascript application/javascript application/json;
```

### 3. 配置缓存

```nginx
# 在 http 块中
proxy_cache_path /var/cache/nginx levels=1:2 keys_zone=pubmed_cache:10m max_size=1g inactive=60m;

# 在 server 或 location 块中
proxy_cache pubmed_cache;
proxy_cache_valid 200 10m;
proxy_cache_use_stale error timeout updating http_500 http_502 http_503 http_504;
```

### 4. 限流配置

```nginx
# 在 http 块中
limit_req_zone $binary_remote_addr zone=pubmed_limit:10m rate=10r/s;

# 在 location 块中
limit_req zone=pubmed_limit burst=20 nodelay;
```

---

## 故障排查

### 常见问题

1. **502 Bad Gateway**
   ```bash
   # 检查应用是否运行
   docker-compose -f docker-compose.prod.yml ps

   # 检查端口是否监听
   netstat -tulnp | grep 5005

   # 查看 Nginx 错误日志
   sudo tail -f /var/log/nginx/error.log
   ```

2. **504 Gateway Timeout**
   ```nginx
   # 增加超时时间
   proxy_read_timeout 600s;
   ```

3. **权限问题**
   ```bash
   # 检查 SELinux（CentOS/RHEL）
   sudo getsebool httpd_can_network_connect

   # 如果为 off，启用它
   sudo setsebool -P httpd_can_network_connect 1
   ```

---

## 完整部署流程总结

```bash
# 1. 启动 Docker 应用
cd /path/to/PubMed-Literature-Push-Web
./deploy-prod.sh

# 2. 配置 Nginx
sudo nano /etc/nginx/sites-available/pubmed-app
sudo ln -s /etc/nginx/sites-available/pubmed-app /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx

# 3. 配置 SSL（可选但推荐）
sudo certbot --nginx -d your-domain.com

# 4. 验证部署
curl -I https://your-domain.com
```

---

## 参考资源

- [Nginx 官方文档](https://nginx.org/en/docs/)
- [Let's Encrypt 文档](https://letsencrypt.org/docs/)
- [Nginx 性能优化指南](https://www.nginx.com/blog/tuning-nginx/)

---

**注意**:
- 所有配置示例中的 `your-domain.com` 需要替换为实际域名
- 生产环境强烈建议配置 HTTPS
- 定期更新 SSL 证书（Let's Encrypt 自动续期）
- 监控 Nginx 性能和日志
