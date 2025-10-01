# ==================== 构建阶段 ====================
FROM python:3.11-slim AS builder

# 安装构建依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    && rm -rf /var/lib/apt/lists/*

# 创建虚拟环境并安装Python依赖
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt /tmp/
RUN pip install --no-cache-dir --upgrade pip setuptools wheel && \
    pip install --no-cache-dir -r /tmp/requirements.txt

# ==================== 运行阶段 ====================
FROM python:3.11-slim

# 设置标签
LABEL maintainer="zhy0504@github.com" \
      version="1.0.0" \
      description="PubMed Literature Push Web Application - Production Ready"

# 安装运行时依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    sqlite3 \
    redis-tools \
    tzdata \
    && rm -rf /var/lib/apt/lists/*

# 设置时区
ENV TZ=Asia/Shanghai
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

# 设置环境变量
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    FLASK_APP=app.py \
    FLASK_ENV=production \
    DATABASE_URL=sqlite:////app/data/pubmed_app.db \
    PATH="/opt/venv/bin:$PATH"

# 创建非root用户
RUN groupadd -r appuser && useradd -r -g appuser -u 1000 appuser

# 设置工作目录
WORKDIR /app

# 从构建阶段复制虚拟环境
COPY --from=builder /opt/venv /opt/venv

# 复制应用代码
COPY --chown=appuser:appuser . .

# 创建必要目录并设置权限
RUN mkdir -p /app/data /app/logs && \
    chown -R appuser:appuser /app/data /app/logs && \
    chmod +x /app/docker-entrypoint.sh

# 切换到非root用户
USER appuser

# 暴露端口
EXPOSE 5005

# 健康检查
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD python -c "import requests; requests.get('http://localhost:5005/', timeout=5)" || exit 1

# 设置入口点
ENTRYPOINT ["/app/docker-entrypoint.sh"]

# 默认启动命令(生产级Gunicorn配置)
CMD ["gunicorn", \
     "--bind", "0.0.0.0:5005", \
     "--workers", "4", \
     "--worker-class", "sync", \
     "--worker-connections", "1000", \
     "--timeout", "600", \
     "--graceful-timeout", "300", \
     "--keep-alive", "5", \
     "--max-requests", "1000", \
     "--max-requests-jitter", "50", \
     "--preload", \
     "--access-logfile", "-", \
     "--error-logfile", "-", \
     "--log-level", "info", \
     "app:app"]
