#!/bin/bash

# PubMed Literature Push - GitHub Docker部署脚本

echo "🚀 开始部署 PubMed Literature Push..."

# 检查Docker和docker-compose
if ! command -v docker &> /dev/null; then
    echo "❌ Docker未安装，请先安装Docker"
    exit 1
fi

if ! command -v docker-compose &> /dev/null; then
    echo "❌ docker-compose未安装，请先安装docker-compose"
    exit 1
fi

# 创建必要的目录
echo "📁 创建必要的目录..."
mkdir -p nginx/ssl logs/nginx data

# 检查环境变量文件
if [ ! -f .env ]; then
    echo "📋 复制环境配置文件..."
    cp .env.example .env
    echo "⚠️  请编辑 .env 文件配置必要的环境变量"
    echo "   - SECRET_KEY: 应用密钥"
    echo "   - OPENAI_API_KEY: OpenAI API密钥" 
    echo "   - PUBMED_API_KEY: PubMed API密钥"
    read -p "按回车键继续..."
fi

# 拉取最新镜像
echo "📦 拉取最新Docker镜像..."
docker-compose -f docker-compose.prod.yml pull

# 启动服务
echo "🔄 启动服务..."
docker-compose -f docker-compose.prod.yml up -d

# 检查服务状态
echo "🔍 检查服务状态..."
sleep 10
docker-compose -f docker-compose.prod.yml ps

# 显示访问信息
echo ""
echo "✅ 部署完成！"
echo "📍 访问地址："
echo "   - HTTP: http://localhost"
echo "   - HTTPS: https://localhost (需要SSL证书)"
echo "   - 直接访问Flask: http://localhost:5003"
echo ""
echo "📊 查看日志："
echo "   docker-compose -f docker-compose.prod.yml logs -f"
echo ""
echo "🛑 停止服务："
echo "   docker-compose -f docker-compose.prod.yml down"