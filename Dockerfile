# 使用Python 3.11官方镜像作为基础镜像
FROM python:3.11-slim

# 设置工作目录
WORKDIR /app

# 设置环境变量
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    FLASK_APP=app.py \
    FLASK_ENV=production \
    DATABASE_URL=sqlite:////app/pubmed_app.db \
    TZ=Asia/Shanghai

# 安装系统依赖
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    curl \
    sqlite3 \
    && rm -rf /var/lib/apt/lists/*

# 复制依赖文件
COPY requirements.txt .

# 安装Python依赖
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# 复制项目文件
COPY . .

# 创建必要的目录并设置脚本权限
RUN mkdir -p /app/data && \
    mkdir -p /app/logs && \
    chmod +x /app/start.sh && \
    chmod +x /app/docker-entrypoint.sh

# 创建非root用户
RUN adduser --disabled-password --gecos '' appuser && \
    chown -R appuser:appuser /app
USER appuser

# 暴露端口
EXPOSE 5003

# 健康检查
HEALTHCHECK --interval=30s --timeout=30s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:5003/ || exit 1

# 设置入口点
ENTRYPOINT ["/app/docker-entrypoint.sh"]

# 启动命令 - 4worker配置，提升并发处理能力
CMD ["gunicorn", "--bind", "0.0.0.0:5003", "--workers", "4", "--timeout", "600", "--graceful-timeout", "300", "--preload", "app:app"]