# PubMed Literature Push Web

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.8+-3776AB?style=flat&logo=python&logoColor=white" alt="Python Version">
  <img src="https://img.shields.io/badge/Flask-2.3.3-000000?style=flat&logo=flask&logoColor=white" alt="Flask">
  <img src="https://img.shields.io/badge/License-MIT-yellow.svg" alt="License">
  <img src="https://img.shields.io/badge/SQLAlchemy-3.0.5-red?style=flat" alt="SQLAlchemy">
  <img src="https://img.shields.io/badge/OpenAI-API-412991?style=flat&logo=openai&logoColor=white" alt="OpenAI">
</p>

<p align="center">
  <strong>智能的 PubMed 文献推送系统</strong><br>
  支持多邮箱轮询发送、期刊质量评估以及 AI 驱动的检索式生成和摘要翻译
</p>

---

## 目录

- [功能概览](#功能概览)
- [技术栈](#技术栈)
- [快速开始](#快速开始)
  - [环境要求](#环境要求)
  - [安装步骤](#安装步骤)
  - [默认账号](#默认账号)
- [关键配置](#关键配置)
  - [邮箱配置](#邮箱配置)
  - [AI 配置](#ai-配置)
  - [时区配置](#时区配置)
  - [期刊数据](#期刊数据)
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

### 环境要求

- **Python**: 3.8 或更高版本
- **Redis**: 用于任务队列（可选，用于异步任务）
- **SQLite**: 默认数据库（可扩展至 PostgreSQL/MySQL）

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

应用将在 `http://127.0.0.1:5003` 启动。

### 默认账号

执行 `python setup.py --default` 后会生成以下账号：

| 角色 | 邮箱 | 密码 |
|------|------|------|
| 主管理员 | `admin@pubmed.com` | `admin123` |
| 备用管理员 | `backup-admin@pubmed.com` | `admin123` |
| 测试用户 | `test@example.com` | `test123` |

---

## 关键配置

### 邮箱配置

1. 登录后台管理：`http://127.0.0.1:5003/admin`
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

### 切换数据库

如需切换到 PostgreSQL 或 MySQL，请按以下步骤操作：

**1. 安装对应的数据库驱动**
```bash
# PostgreSQL
pip install psycopg2-binary

# MySQL
pip install pymysql
```

**2. 修改 `app.py` 中的数据库连接字符串**
```python
# PostgreSQL 示例
app.config['SQLALCHEMY_DATABASE_URI'] = 'postgresql://user:password@localhost/dbname'

# MySQL 示例
app.config['SQLALCHEMY_DATABASE_URI'] = 'mysql+pymysql://user:password@localhost/dbname'
```

**3. 重新初始化数据库**
```bash
python setup.py --default
```

---

## API 接口

| 路由 | 功能 | 权限 |
|------|------|------|
| `/` | 前台用户入口 | 公开 |
| `/admin` | 管理后台 | 管理员 |
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

### 使用 Docker 部署

**1. 构建镜像**
```bash
docker build -t pubmed-push-web .
```

**2. 运行容器**
```bash
docker run -d -p 5003:5003 \
  -e TZ=Asia/Shanghai \
  -v $(pwd)/data:/app/data \
  -v $(pwd)/logs:/app/logs \
  --name pubmed-push-web \
  pubmed-push-web
```

### 使用 Gunicorn 部署

**1. 安装 Gunicorn**
```bash
pip install gunicorn
```

**2. 启动服务**
```bash
gunicorn -w 4 -b 0.0.0.0:5003 app:app
```

**参数说明**
- `-w 4`：使用 4 个 worker 进程
- `-b 0.0.0.0:5003`：绑定到所有网络接口的 5003 端口

### 生产环境配置建议

- 使用 Nginx 作为反向代理
- 配置 HTTPS 证书
- 启用日志轮转
- 定期备份数据库和配置文件

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

本项目采用 **MIT License** 许可证，详见 [LICENSE](LICENSE) 文件。

```
MIT License

Copyright (c) 2025 zhy0504

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software...
```

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
