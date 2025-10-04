# PubMed Literature Push Web

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.8+-3776AB?style=flat&logo=python&logoColor=white" alt="Python Version">
  <img src="https://img.shields.io/badge/Flask-2.3.3-000000?style=flat&logo=flask&logoColor=white" alt="Flask">
  <img src="https://img.shields.io/badge/License-Dual%20License-blue.svg" alt="License">
  <img src="https://img.shields.io/badge/SQLAlchemy-3.0.5-red?style=flat" alt="SQLAlchemy">
  <img src="https://img.shields.io/badge/OpenAI-API-412991?style=flat&logo=openai&logoColor=white" alt="OpenAI">
</p>

<p align="center">
  <strong>智能的 PubMed 文献推送系统</strong><br>
  支持多邮箱轮询发送、期刊质量评估以及 AI 驱动的检索式生成和摘要翻译
  使用deepwiki解析本项目，请点击下面的链接
  [![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/zhy0504/PubMed-Literature-Push-Web)
</p>

---

## 系统架构

### 整体架构图

```
┌─────────────────────────────────────────────────────────────────────┐
│                          用户访问层                                   │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐            │
│  │ 浏览器    │  │  邮件客户端│  │  API客户端│  │  管理员  │            │
│  └────┬─────┘  └─────┬────┘  └─────┬────┘  └────┬─────┘            │
└───────┼──────────────┼─────────────┼────────────┼──────────────────┘
        │              │             │            │
        ▼              │             │            ▼
┌───────────────┐      │             │      ┌──────────────┐
│  Nginx反向代理 │      │             │      │  RQ Dashboard │
│  (80/443)     │      │             │      │  (9181)      │
└───────┬───────┘      │             │      └──────┬───────┘
        │              │             │             │
        ▼              │             │             ▼
┌─────────────────────────────────────────────────────────────────────┐
│                        Flask 应用层 (app.py)                          │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐              │
│  │  路由控制    │  │  用户认证    │  │  权限管理    │              │
│  │  (Flask)     │  │(Flask-Login) │  │  (装饰器)    │              │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘              │
│         │                 │                  │                       │
│  ┌──────▼─────────────────▼──────────────────▼───────┐              │
│  │              业务逻辑层                             │              │
│  │  ┌────────────┐ ┌────────────┐ ┌────────────┐    │              │
│  │  │ 订阅管理   │ │ 文献检索   │ │ AI服务     │    │              │
│  │  └────────────┘ └────────────┘ └────────────┘    │              │
│  │  ┌────────────┐ ┌────────────┐ ┌────────────┐    │              │
│  │  │ 邮件服务   │ │ 期刊评估   │ │ 用户管理   │    │              │
│  │  └────────────┘ └────────────┘ └────────────┘    │              │
│  └──────────────────────────────────────────────────┘              │
└───────┬────────────────┬────────────────┬──────────────────────────┘
        │                │                │
        ▼                ▼                ▼
┌─────────────┐  ┌─────────────┐  ┌─────────────────────────────────┐
│   SQLite    │  │   Redis     │  │      RQ 任务队列系统             │
│   数据库    │  │   缓存      │  │  ┌──────────┐  ┌──────────┐     │
│             │  │             │  │  │ Worker-1 │  │ Worker-2 │     │
│  ┌────────┐ │  │  ┌────────┐ │  │  │(推送任务) │  │(推送任务) │     │
│  │用户表  │ │  │  │期刊缓存│ │  │  └──────────┘  └──────────┘     │
│  │订阅表  │ │  │  │搜索缓存│ │  │  ┌──────────────────────────┐   │
│  │文章表  │ │  │  │会话数据│ │  │  │   Scheduler (调度器)      │   │
│  │日志表  │ │  │  └────────┘ │  │  │ - 定时推送任务             │   │
│  │邀请码  │ │  │             │  │  │ - 健康检查                 │   │
│  └────────┘ │  │             │  │  │ - 队列监控                 │   │
└─────────────┘  └─────────────┘  └──┴──────────────────────────────┘
        │                │                │
        ▼                ▼                ▼
┌─────────────────────────────────────────────────────────────────────┐
│                        外部服务集成                                   │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐              │
│  │ PubMed API   │  │  SMTP邮箱    │  │  OpenAI API  │              │
│  │ (文献检索)   │  │  (邮件发送)  │  │  (AI智能)    │              │
│  └──────────────┘  └──────────────┘  └──────────────┘              │
└─────────────────────────────────────────────────────────────────────┘
```

### 核心组件说明

| 组件 | 技术栈 | 职责 | 高可用 |
|------|--------|------|--------|
| **Web应用** | Flask + Gunicorn | HTTP请求处理、业务逻辑、页面渲染 | 可水平扩展 |
| **反向代理** | Nginx | 负载均衡、SSL终止、静态资源 | 支持 |
| **数据库** | SQLite | 用户、订阅、文章数据持久化 | 文件锁 |
| **缓存** | Redis | 期刊数据缓存、会话存储 | 支持主从 |
| **任务队列** | RQ (Redis Queue) | 异步推送任务、定时调度 | 多Worker |
| **调度器** | APScheduler | 定时任务触发、健康监控 | 单实例 |
| **监控面板** | RQ Dashboard | 任务队列可视化监控 | 只读 |

### 数据流说明

**文献推送流程**
```
1. 用户创建订阅 → 保存至数据库
2. 调度器触发 → 生成RQ任务
3. Worker执行 → PubMed检索 → 期刊评估 → AI处理
4. 邮件发送 → SMTP轮询发送
5. 记录日志 → 数据库 + 系统日志
```

**缓存策略**
- **期刊数据**: 启动时加载到Redis，TTL=24h
- **搜索结果**: 相同检索式缓存5分钟
- **会话数据**: Flask-Session存储到Redis

**任务队列优先级**
- **High**: 立即推送、管理员操作
- **Default**: 定时推送任务
- **Low**: 统计分析、清理任务

---

## 目录

- [功能概览](#功能概览)
- [技术栈](#技术栈)
- [快速开始](#快速开始)
  - [系统要求](#系统要求)
  - [安装步骤](#安装步骤)
  - [默认账号](#默认账号)
- [关键配置](#关键配置)
  - [邮箱配置](#邮箱配置)
  - [AI 配置](#ai-配置)
  - [时区配置](#时区配置)
  - [期刊数据](#期刊数据)
  - [邀请码注册](#邀请码注册)
- [常用操作](#常用操作)
- [数据库管理](#数据库管理)
- [API 接口](#api-接口)
- [故障排查](#故障排查)
- [日志查看](#日志查看)
- [部署指南](#部署指南)
- [贡献指南](#贡献指南)
- [许可证](#许可证)
- [联系方式](#联系方式)

---

## 功能概览

### 核心功能

- **智能文献搜索**：支持关键词、布尔逻辑与高级筛选条件的 PubMed 检索
- **多邮箱轮询**：允许配置多个 SMTP 邮箱，自动轮询发送以规避单一邮箱限制
- **定时推送**：可按日/周/月灵活设置推送计划与邮件收件人
- **AI 智能助手**：自动生成检索式、摘要翻译及高亮重点
- **用户与权限**：支持注册、登录、角色区分和权限控制

### 高级能力

- **期刊质量评估**：融合 JCR 影响因子与中科院分区数据
- **批量处理**：支持批量文献导入、筛选与推送
- **数据缓存**：JournalDataCache 缓存期刊数据，提高检索性能
- **管理后台**：集中管理用户、邮箱、AI 配置与系统日志
- **运行监控**：记录任务执行日志，便于审计与排错
- **邀请码系统**：支持邀请码注册，灵活控制用户准入

---

## 技术栈

**后端框架**
- Flask 2.3.3 - 轻量级 Web 框架
- SQLAlchemy 3.0.5 - ORM 数据库操作
- Flask-Login 0.6.3 - 用户认证管理
- APScheduler 3.10.4 - 定时任务调度

**任务队列**
- RQ 1.15.1 - 基于 Redis 的任务队列
- Redis 5.0.1 - 缓存与消息代理

**邮件服务**
- Flask-Mail 0.9.1 - SMTP 邮件发送

**AI 集成**
- OpenAI 1.109.1 - AI 检索式生成与摘要翻译

**数据处理**
- Pandas - CSV 数据处理
- Requests 2.32.5 - HTTP 请求库

**安全与验证**
- Cryptography 46.0.1 - 加密库
- Email-Validator 2.0.0 - 邮箱验证

**生产部署**
- Gunicorn 21.2.0 - WSGI HTTP 服务器

---

## 快速开始

### 系统要求

#### 硬件配置要求

| 配置类型 | CPU | 内存 | 磁盘空间 | 适用规模 |
|---------|-----|------|---------|---------|
| **最小配置** | 2 核 | 2 GB | 10 GB | 1-10 人，少量订阅 |
| **推荐配置** | 4 核 | 4 GB | 20 GB | 10-50 人，中等订阅量 |
| **高性能配置** | 8 核 | 8 GB | 50 GB | 50-200 人，大量订阅 |

**资源分配说明**（基于 Docker Compose 配置）：
- **主应用 (app)**: 最多占用 2 CPU / 2 GB 内存
- **Redis**: 最多占用 1 CPU / 768 MB 内存
- **每个 RQ Worker**: 最多占用 1 CPU / 1 GB 内存
- **RQ Dashboard**: 最多占用 0.5 CPU / 256 MB 内存

**总计**（完整服务栈）：约 **4.5 CPU / 4 GB 内存**

#### 适用场景

| 使用场景 | 用户规模 | 订阅数 | 推送频率 | 配置建议 |
|---------|---------|--------|---------|---------|
| **个人使用** | 1-3 人 | < 10 个 | 每日 | 最小配置 |
| **小型团队** | 5-20 人 | 10-50 个 | 每日/每周 | 推荐配置 |
| **中型团队** | 20-50 人 | 50-200 个 | 每日/每周/每月 | 推荐配置 |
| **大型团队** | 50-200 人 | 200-1000 个 | 高频推送 | 高性能配置 |

**注意事项**：
- SQLite 数据库适合中小规模使用（< 50 并发用户）
- 大量订阅或高频推送建议增加 Worker 数量
- 磁盘空间主要用于日志、数据库和缓存

#### 软件环境要求

- **Python**: 3.8 或更高版本
- **Docker**: 20.10+ / Docker Compose: 2.0+（生产部署）
- **Redis**: 7.x（Docker Compose 自动部署）
- **SQLite**: 3.x（系统自带或 Docker 镜像包含）

### 安装步骤

#### 1. 克隆项目

```bash
git clone https://github.com/zhy0504/PubMed-Literature-Push-Web.git
cd PubMed-Literature-Push-Web
```

#### 2. 创建虚拟环境

**Linux / macOS**
```bash
python3 -m venv quick_venv
source quick_venv/bin/activate
```

**Windows PowerShell**
```powershell
python -m venv quick_venv
quick_venv\Scripts\Activate.ps1
```

**Windows CMD**
```cmd
python -m venv quick_venv
quick_venv\Scripts\activate.bat
```

#### 3. 安装依赖

```bash
pip install -r requirements.txt
```

#### 4. 初始化数据库

**使用默认账号快速配置**
```bash
python setup.py --default
```

**或使用交互式向导自定义配置**
```bash
python setup.py
```

#### 5. （可选）初始化邮箱示例配置

```bash
python init_mail_configs.py
```

#### 6. 启动应用

**Linux / macOS**
```bash
chmod +x start.sh
./start.sh
```

**Windows**
```cmd
start.bat
```

**或直接运行 Python**
```bash
python app.py
```

应用将在 `http://127.0.0.1:5005` 启动。

### 默认账号

执行 `python setup.py --default` 后会生成以下账号：

| 角色 | 邮箱 | 密码 | 说明 |
|------|------|------|------|
| 管理员 | `admin@pubmed.com` | `admin123` | 可通过环境变量 `DEFAULT_ADMIN_EMAIL` 和 `DEFAULT_ADMIN_PASSWORD` 自定义 |

**安全提示**：
- ⚠️ 首次登录后请立即修改默认密码
- 生产环境部署前务必设置强密码
- 可在后台管理页面创建更多用户账号

---

## 关键配置

### 邮箱配置

1. 登录后台管理：`http://127.0.0.1:5005/admin`
2. 进入「邮箱管理」，编辑示例配置
3. 根据邮箱类型填写 SMTP 主机、端口、用户名与授权码

**常见邮箱配置**

| 邮箱类型 | SMTP 主机 | 端口 | 说明 |
|---------|----------|------|------|
| QQ 邮箱 | `smtp.qq.com` | `465` | 开启 SMTP 服务，使用授权码 |
| 163 邮箱 | `smtp.163.com` | `465` | 开启客户端授权密码 |
| Gmail | `smtp.gmail.com` | `587` | 启用应用专用密码 |

### AI 配置

1. 在后台进入「AI 设置」
2. 新增提供商（默认示例为 OpenAI）
3. 配置模型与用途：
   - **检索式生成**：将自然语言需求转为 PubMed 检索式
   - **摘要翻译**：输出中文摘要或要点

### 时区配置

从版本 **v2.1.0** 开始支持灵活的时区配置，不再强制使用北京时间。

#### 配置方法

**1. 在 `.env` 文件中设置**
```bash
TZ=Asia/Shanghai    # 北京时间
```

**2. Docker 环境中设置**
```yaml
services:
  app:
    environment:
      - TZ=Asia/Shanghai
```

#### 常用时区示例

| 时区 | 标识符 | UTC 偏移 |
|------|--------|---------|
| 北京时间 | `Asia/Shanghai` | UTC+8 |
| 东京时间 | `Asia/Tokyo` | UTC+9 |
| 纽约时间 | `America/New_York` | UTC-5/-4 |
| 伦敦时间 | `Europe/London` | UTC+0/+1 |
| 协调世界时 | `UTC` | UTC+0 |

完整时区列表参考：[IANA Time Zone Database](https://en.wikipedia.org/wiki/List_of_tz_database_time_zones)

#### 影响范围

- **定时推送**：推送时间基于配置时区执行
- **日志时间戳**：所有日志记录使用配置时区
- **管理界面**：显示时间使用配置时区

#### 注意事项

- 未配置时默认使用 `Asia/Shanghai`
- 修改时区后需重启容器生效
- 使用标准 IANA 时区标识符（避免使用 EST、PST 等缩写）

### 期刊数据

- `data/jcr_filtered.csv`：JCR 影响因子与分区
- `data/zky_filtered.csv`：中科院分区数据
- 可按需替换或更新 CSV 文件，以覆盖最新期刊指标

### 邀请码注册

从版本 **v2.1.0** 开始支持邀请码注册功能，允许管理员控制新用户注册。

#### 快速开始

**1. 执行数据库迁移**

首次使用邀请码功能需要先执行数据库迁移：

**本地环境:**
```bash
python migrate_database.py
```

**Docker 容器环境:**
```bash
# 方法1: 进入容器执行
docker exec -it <容器名称> bash
python migrate_database.py
exit

# 方法2: 直接执行命令
docker exec <容器名称> python migrate_database.py

# 方法3: 使用 docker-compose
docker-compose -f docker-compose.prod.yml exec app python migrate_database.py
```

> **注意**: 新部署的系统会自动包含邀请码表,无需手动迁移。只有从旧版本升级的系统才需要执行迁移。

迁移脚本会自动创建邀请码相关的数据表和系统设置。详细的容器迁移指南请参考: [DOCKER_MIGRATION_GUIDE.md](DOCKER_MIGRATION_GUIDE.md)

**2. 启用邀请码功能**

1. 登录管理后台 → 系统设置
2. 勾选「需要邀请码注册」
3. 点击「保存系统配置」

**3. 生成邀请码**

1. 管理后台 → 邀请码管理
2. 点击「生成邀请码」
3. 设置参数：
   - **最大使用次数**：此邀请码可被使用的次数（默认 1 次）
   - **有效天数**：邀请码的有效期（留空表示永久有效）
4. 生成后将邀请码分发给需要注册的用户

**4. 用户注册**

用户访问注册页面时，需要输入邀请码才能完成注册。

#### 功能特性

- ✅ **灵活的有效期设置**：可设置邀请码过期时间或永久有效
- ✅ **可重复使用**：支持设置邀请码最大使用次数
- ✅ **使用记录追踪**：查看每个邀请码的使用历史和用户信息
- ✅ **状态管理**：可禁用/启用/删除邀请码
- ✅ **统计信息**：实时查看可用、已使用、已过期邀请码数量
- ✅ **系统级开关**：可随时开启/关闭邀请码功能

#### 管理功能

在管理后台的「邀请码管理」页面可以：

- 查看所有邀请码及其状态
- 生成新的邀请码
- 查看邀请码使用记录
- 禁用/启用邀请码
- 删除无效邀请码

#### 使用场景

- **内测阶段**：限制注册，只允许特定用户注册
- **付费服务**：通过邀请码控制付费用户注册
- **社区管理**：通过邀请制保证用户质量
- **推广活动**：发放限时邀请码进行推广

---

## 常用操作

### 创建订阅

1. 登录前端页面
2. 进入「我的订阅」→「新建订阅」
3. 设置关键词、期刊筛选条件与推送频率

### 管理推送任务

通过后台查看任务状态、手动触发或暂停计划任务

### 自定义邮件模板

在代码中自定义 `_generate_email_html`，调整推送邮件样式与内容

### AI 能力扩展

继承 `AIService`，集成新的大模型或第三方服务

---

## 数据库管理

项目使用 SQLAlchemy 管理数据库，通过 `db.create_all()` 自动创建表结构。

### 数据库初始化

数据库会在首次运行 `setup.py` 时自动创建和初始化：

```bash
# 使用默认配置初始化
python setup.py --default

# 或使用交互式向导
python setup.py
```

### 重置数据库

如需重置数据库，只需删除数据库文件后重新运行初始化：

```bash
# 删除数据库文件
rm pubmed_app.db  # Linux/macOS
del pubmed_app.db  # Windows

# 重新初始化
python setup.py --default
```

### 数据库说明

**当前数据库支持**：项目目前仅支持 SQLite 数据库。

**技术限制**：
- `setup.py` 使用 `sqlite3` 模块和原生 SQL 创建表，硬编码 SQLite 语法
- `docker-entrypoint.sh` 使用 `sqlite3` 命令验证数据库完整性
- 虽然应用层使用 SQLAlchemy ORM，但初始化脚本未使用 `db.create_all()`

**如需支持 PostgreSQL/MySQL**：
需要重构以下组件：
1. 重写 `setup.py`：使用 SQLAlchemy 的 `db.create_all()` 替代原生 SQL
2. 修改 `docker-entrypoint.sh`：移除 SQLite 特定的验证逻辑
3. 更新 Docker Compose 配置：添加 PostgreSQL/MySQL 服务
4. 测试所有数据库操作的兼容性

**建议**：SQLite 对于中小规模部署已足够使用。如有大规模并发需求，建议提交 Issue 讨论迁移方案。

---

## API 接口

### 主要路由

| 路由 | 功能 | 权限 |
|------|------|------|
| `/` | 前台用户入口 | 公开 |
| `/register` | 用户注册（支持邀请码） | 公开 |
| `/login` | 用户登录 | 公开 |
| `/admin` | 管理后台 | 管理员 |
| `/admin/users` | 用户管理 | 管理员 |
| `/admin/invite-codes` | 邀请码管理 | 管理员 |
| `/admin/invite-codes/create` | 生成邀请码 | 管理员 |
| `/admin/invite-codes/<id>/usage` | 查看邀请码使用记录 | 管理员 |
| `/api/search` | 文献检索 API | 登录用户 |
| `/api/ai/query` | AI 检索式生成 | 登录用户 |
| `/api/ai/translate` | AI 摘要翻译 | 登录用户 |

所有接口通过 Flask-Login 管理会话，敏感操作需具备相应角色权限。

---

## 故障排查

### 1. 邮件发送失败

**可能原因**
- 邮箱授权码或 SMTP 配置错误
- 发送频率超限
- 网络连接问题

**解决方案**
- 核对邮箱授权码、SMTP 主机与端口
- 检查发送频率限制与网络可达性
- 查看系统日志获取详细错误信息

### 2. 文献搜索无结果

**可能原因**
- 网络连接问题
- PubMed API 不可访问
- 检索关键词过于严格

**解决方案**
- 检查网络连接和 PubMed API 可访问性
- 调整检索关键词与筛选条件
- 使用 AI 检索式生成功能优化查询

### 3. AI 功能异常

**可能原因**
- API Key 配置错误
- OpenAI 余额不足
- 网络代理设置问题

**解决方案**
- 确认 API Key 正确、余额充足
- 检查代理或网络设置
- 查看后台「AI 设置」确认配置

---

## 日志查看

### 方式一：后台管理界面

登录后台 → 「系统日志」查看任务执行与异常信息

### 方式二：数据库查询

直接查看数据库中的 `system_log` 表

```sql
SELECT * FROM system_log ORDER BY created_at DESC LIMIT 100;
```

### 方式三：Flask 调试日志

启用 Flask 调试模式以查看更详细的日志

```bash
export FLASK_ENV=development  # Linux/macOS
set FLASK_ENV=development     # Windows CMD
python app.py
```

---

## 部署指南

### 部署架构说明

**重要提示**：本项目必须使用 Docker Compose 部署，强依赖 Redis 和 RQ 任务队列。

| 组件 | 是否必需 | 说明 |
|------|---------|------|
| **Redis** | ✅ 必需 | 缓存和任务队列消息代理 |
| **主应用 (app)** | ✅ 必需 | Flask Web 应用，处理 HTTP 请求 |
| **RQ Worker** | ✅ 必需 | 处理异步任务（文献推送、邮件发送） |
| **RQ Dashboard** | ❌ 可选 | 任务队列监控面板 |

### Docker Compose 服务组件

| 服务名 | 端口 | 说明 | 资源限制 |
|-------|------|------|---------|
| `app` | 5005 | 主应用服务（Gunicorn + Flask） | 2 CPU, 2G 内存 |
| `redis` | 6379 | 缓存与消息代理 | 1 CPU, 768M 内存 |
| `worker-1` | - | RQ 任务队列 Worker 1 | 1 CPU, 1G 内存 |
| `worker-2` | - | RQ 任务队列 Worker 2 | 1 CPU, 1G 内存 |
| `rq-dashboard` | 9181 | 任务队列监控面板 | 0.5 CPU, 256M 内存 |

---

### 使用 Docker Compose 部署（推荐）

项目提供两种生产级 Docker Compose 配置：

| 配置文件 | 适用场景 | 安全级别 | 说明 |
|---------|---------|---------|------|
| **docker-compose.prod.yml** | 标准部署 | 中等 | 所有端口对外开放，适合测试环境 |
| **docker-compose.prod.internal.yml** | 内网隔离部署 | 高 | 仅Nginx对外，其他服务完全隔离 |

#### 配置选择指南

**标准部署** - 适合以下场景：
- 开发测试环境
- 内网环境部署
- 需要直接访问各服务端口

**内网隔离部署（推荐）** - 适合以下场景：
- ✅ 生产环境部署
- ✅ 公网暴露服务
- ✅ 高安全要求场景
- ✅ 符合等保合规要求

---

### 方式一：标准部署（所有端口可访问）

#### 1. 准备环境文件

创建 `.env` 文件配置环境变量：

```bash
# 时区设置
TZ=Asia/Shanghai

# 日志级别
LOG_LEVEL=INFO

# RQ Dashboard 认证（可选）
RQ_DASHBOARD_USER=admin
RQ_DASHBOARD_PASS=your_secure_password

# OpenAI API 配置（可选，在后台管理界面配置更灵活）
# OPENAI_API_KEY=your_api_key
```

#### 2. 启动服务

```bash
# 使用预构建镜像（推荐）
docker-compose -f docker-compose.prod.yml up -d

# 或本地构建镜像
docker build -t ghcr.io/zhy0504/pubmed-literature-push-web:latest .
docker-compose -f docker-compose.prod.yml up -d
```

#### 3. 验证服务状态

```bash
# 查看所有服务状态
docker-compose -f docker-compose.prod.yml ps

# 查看应用日志
docker-compose -f docker-compose.prod.yml logs -f app

# 查看 Worker 日志
docker-compose -f docker-compose.prod.yml logs -f worker-1
```

#### 4. 访问服务

- **主应用**: http://localhost:5005
- **RQ Dashboard**: http://localhost:9181 (监控任务队列)
- **默认管理员**: `admin@pubmed.com` / `admin123` （⚠️ 请立即修改密码）

#### 5. 常用管理命令

```bash
# 停止所有服务
docker-compose -f docker-compose.prod.yml down

# 重启服务
docker-compose -f docker-compose.prod.yml restart

# 查看资源占用
docker stats

# 清理未使用的资源
docker system prune -a
```

---

### 方式二：内网隔离部署（推荐生产环境）

此配置实现完整的网络隔离，仅通过 Nginx 反向代理对外提供服务。

#### 安全特性

- ✅ **完全网络隔离**：Redis、Worker、RQ Dashboard 完全隔离在内部网络
- ✅ **最小暴露原则**：仅 Nginx 80/443 端口对外
- ✅ **本地管理访问**：应用和监控面板仅绑定 127.0.0.1
- ✅ **双层网络架构**：内部网络（internal）+ 外部网络（external）

#### 网络架构

```
公网流量
   ↓
Nginx (80/443) ← 唯一对外入口
   ↓
[外部网络: pubmed-external]
   ↓
App (127.0.0.1:5005) ← 仅本地+容器访问
   ↓
[内部网络: pubmed-internal, internal=true]
   ↓
Redis + Worker-1 + Worker-2 + RQ Dashboard ← 完全隔离
```

#### 1. 准备配置文件

创建 `.env` 文件（同标准部署）：

```bash
TZ=Asia/Shanghai
LOG_LEVEL=INFO
RQ_DASHBOARD_USER=admin
RQ_DASHBOARD_PASS=your_secure_password
```

#### 2. 配置 Nginx（如需自定义）

编辑 [nginx/conf.d/pubmed.conf](nginx/conf.d/pubmed.conf)：

```nginx
# 修改域名
server_name your-domain.com;

# 启用 HTTPS（需要SSL证书）
# 取消注释 HTTPS server 块配置
```

#### 3. 启动服务

```bash
# 使用预构建镜像
docker-compose -f docker-compose.prod.internal.yml up -d

# 或本地构建
docker build -t ghcr.io/zhy0504/pubmed-literature-push-web:latest .
docker-compose -f docker-compose.prod.internal.yml up -d
```

#### 4. 验证隔离配置

```bash
# 1. 验证服务启动
docker-compose -f docker-compose.prod.internal.yml ps

# 2. 验证网络隔离（应无法从宿主机访问）
curl http://localhost:6379  # 应失败 - Redis已隔离
curl http://localhost:5005  # 应失败 - App仅127.0.0.1

# 3. 验证Nginx正常工作
curl http://localhost  # 应成功 - Nginx对外服务

# 4. 本地管理访问（需在宿主机上）
curl http://127.0.0.1:5005/health  # 应成功
curl http://127.0.0.1:9181  # RQ Dashboard
```

#### 5. 访问服务

| 服务 | 访问方式 | 访问地址 |
|------|---------|---------|
| **用户访问** | 公网/内网 | http://your-server-ip 或 https://your-domain.com |
| **管理后台** | 公网/内网 | http://your-server-ip/admin |
| **应用直连** | 仅宿主机本地 | http://127.0.0.1:5005 |
| **RQ Dashboard** | 仅宿主机本地 | http://127.0.0.1:9181 |

#### 6. SSL/HTTPS 配置（生产必备）

**获取免费 SSL 证书（Let's Encrypt）**

```bash
# 安装 certbot
sudo apt-get update
sudo apt-get install certbot

# 生成证书（需要停止 Nginx）
docker-compose -f docker-compose.prod.internal.yml stop nginx
sudo certbot certonly --standalone -d your-domain.com

# 证书将保存在 /etc/letsencrypt/live/your-domain.com/
# 创建软链接到项目目录
mkdir -p nginx/ssl
sudo ln -s /etc/letsencrypt/live/your-domain.com/fullchain.pem nginx/ssl/
sudo ln -s /etc/letsencrypt/live/your-domain.com/privkey.pem nginx/ssl/
```

**启用 HTTPS 配置**

编辑 `nginx/conf.d/pubmed.conf`，取消注释 HTTPS server 块，然后重启：

```bash
docker-compose -f docker-compose.prod.internal.yml restart nginx
```

#### 7. 防火墙配置（可选增强）

```bash
# Ubuntu/Debian - 使用 ufw
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow 22/tcp    # SSH
sudo ufw allow 80/tcp    # HTTP
sudo ufw allow 443/tcp   # HTTPS
sudo ufw enable

# CentOS/RHEL - 使用 firewalld
sudo firewall-cmd --permanent --add-service=http
sudo firewall-cmd --permanent --add-service=https
sudo firewall-cmd --permanent --add-service=ssh
sudo firewall-cmd --reload
```

#### 8. 常用管理命令

```bash
# 查看服务状态
docker-compose -f docker-compose.prod.internal.yml ps

# 查看日志
docker-compose -f docker-compose.prod.internal.yml logs -f nginx
docker-compose -f docker-compose.prod.internal.yml logs -f app

# 重启服务
docker-compose -f docker-compose.prod.internal.yml restart

# 停止服务
docker-compose -f docker-compose.prod.internal.yml down

# 更新镜像
docker-compose -f docker-compose.prod.internal.yml pull
docker-compose -f docker-compose.prod.internal.yml up -d
```

#### 9. 监控和维护

**查看 RQ 任务队列**（仅宿主机本地）
```bash
# 通过浏览器访问（需在服务器上配置SSH隧道）
ssh -L 9181:127.0.0.1:9181 user@your-server-ip

# 然后在本地浏览器访问
# http://localhost:9181
```

**查看应用健康状态**（仅宿主机本地）
```bash
curl http://127.0.0.1:5005/health
```

---

### 生产环境配置建议

**安全加固**
- ✅ 使用内网隔离部署模式（docker-compose.prod.internal.yml）
- ✅ 配置 HTTPS 证书（Let's Encrypt 免费证书）
- ✅ 启用防火墙仅开放必要端口
- ✅ 定期更新 Docker 镜像和系统补丁
- ✅ 修改默认管理员密码

**运维管理**
- 启用日志轮转（已在 docker-compose 中配置）
- 定期备份数据库（`data/pubmed_app.db`）和配置文件
- 监控 Redis 内存使用，调整 `maxmemory` 参数
- 根据负载调整 Worker 数量

**性能优化**
- 使用 CDN 加速静态资源
- 配置 Nginx 缓存策略
- 根据实际负载调整资源限制

---

## 贡献指南

我们欢迎所有形式的贡献！

### 如何贡献

1. **Fork 仓库**并创建功能分支
   ```bash
   git checkout -b feature/your-feature-name
   ```

2. **编写或更新测试**，确保现有测试通过
   ```bash
   pytest tests/
   ```

3. **提交 Pull Request** 并说明变更内容
   - 使用清晰的提交信息
   - 确保代码符合 PEP 8 规范
   - 添加必要的文档说明

### 代码规范

- 遵循 PEP 8 Python 代码风格指南
- 为新功能添加单元测试
- 更新相关文档

---

## 许可证

本项目采用 **双重许可证（Dual License）** 模式：

### 个人使用许可（免费）

✅ **允许用途**：
- 个人研究和学习
- 学术和教育目的（学生/研究人员）
- 个人非商业项目
- 对本项目的开源贡献

### 商业使用许可（需授权）

如果您的使用场景包括以下任何一种，需要获取商业许可：

❌ **需要商业许可的场景**：
- 在营利性组织、公司或企业内使用
- 集成到产生收入的产品或服务中
- 作为付费服务（SaaS）提供
- 用于内部业务运营或工作流程
- 分发给付费客户或客户端

**申请商业许可**：
请通过 [GitHub Issues](https://github.com/zhy0504/PubMed-Literature-Push-Web/issues) 创建标题为 `[Commercial License Request]` 的issue，说明您的使用场景。

### 许可证判定

**个人许可适用于**：
- ✓ 个人用于个人项目
- ✓ 学生/研究人员用于学术工作
- ✓ 不产生收入且非商业目的的使用

**商业许可要求**：
- ✓ 在公司或组织内使用
- ✓ 产生任何直接或间接收入
- ✓ 使用本软件向客户提供服务
- ✓ 集成到商业产品中

详细许可证条款请参阅 [LICENSE](LICENSE) 文件。

---

## 联系方式

- **GitHub Issues**: [提交问题](https://github.com/zhy0504/PubMed-Literature-Push-Web/issues)
- **项目主页**: [GitHub Repository](https://github.com/zhy0504/PubMed-Literature-Push-Web)

---

## 免责声明

**本系统仅供科研与教学用途，请遵守 PubMed 使用条款及相关法律法规。**

- 使用本系统时请遵守 [PubMed Terms of Use](https://www.ncbi.nlm.nih.gov/home/about/policies/)
- 请勿将本系统用于商业用途或大规模数据抓取
- 用户应对使用本系统产生的后果承担责任

---

<p align="center">
  Made with ❤️ by <a href="https://github.com/zhy0504">zhy0504</a>
</p>

<p align="center">
  如果这个项目对您有帮助，请给一个 ⭐️ Star！
</p>
