# PubMed Literature Push Web Application

一个智能的 PubMed 文献推送系统，支持多邮箱轮询发送、期刊质量评估和 AI 智能摘要翻译。

## 系统特性

### 核心功能
- **智能文献搜索**：基于关键词和高级筛选条件的 PubMed 文献检索
- **多邮箱轮询**：支持多个邮箱配置，自动轮询发送避免单一邮箱限制
- **期刊质量评估**：集成 JCR 影响因子和中科院分区数据
- **定时推送**：支持每日、每周、每月的灵活推送计划
- **AI 智能功能**：检索式生成和摘要翻译
- **用户管理**：完整的用户注册、登录和权限管理

### 高级特性
- **期刊筛选**：支持按 JCR 分区、影响因子、中科院分区筛选
- **批量处理**：高效的文献数据处理和邮件发送
- **数据库缓存**：期刊数据缓存提升检索性能
- **管理后台**：完整的管理员界面，支持用户、邮箱、AI 设置管理
- **日志系统**：详细的操作日志和系统监控

## 技术栈

- **后端框架**：Flask 2.3.3
- **数据库**：SQLAlchemy + SQLite
- **任务调度**：APScheduler
- **邮件发送**：Flask-Mail + SMTP
- **AI 集成**：OpenAI API
- **前端**：Bootstrap + JavaScript
- **数据处理**：Pandas、CSV

## 安装部署

### 环境要求
- Python 3.8+
- SQLite 3

### 快速安装

1. **克隆项目**
```bash
git clone https://github.com/your-repo/PubMed-Literature-Push-Web.git
cd PubMed-Literature-Push-Web
```

2. **创建虚拟环境**
```bash
python -m venv quick_venv
source quick_venv/bin/activate  # Linux/Mac
# 或
quick_venv\Scripts\activate     # Windows
```

3. **安装依赖**
```bash
pip install -r requirements.txt
```

4. **初始化数据库**
```bash
# 使用默认账号快速设置
python setup.py --default

# 或交互式自定义设置
python setup.py
```

5. **初始化邮箱配置（可选）**
```bash
python init_mail_configs.py
```

6. **启动应用**
```bash
# Linux/Mac
chmod +x start.sh
./start.sh

# Windows
start.bat

# 或直接运行
python app.py
```

### 默认账号信息

使用 `python setup.py --default` 创建的默认账号：

- **主管理员**：admin@pubmed.com / admin123
- **备用管理员**：backup-admin@pubmed.com / admin123  
- **测试用户**：test@example.com / test123

## 配置说明

### 邮箱配置

1. 登录管理后台：`http://127.0.0.1:5003/admin`
2. 进入"邮箱管理"页面
3. 编辑示例配置，填入真实邮箱信息：
   - QQ邮箱：需开启 SMTP 服务并使用授权码
   - 163邮箱：需开启客户端授权密码
   - Gmail：需使用应用专用密码

### AI 功能配置

1. 进入"AI设置"页面
2. 添加 AI 提供商（如 OpenAI）
3. 配置模型用于：
   - 检索式生成：将自然语言转换为 PubMed 检索式
   - 摘要翻译：将英文摘要翻译为中文

### 期刊数据

系统内置期刊质量数据：
- `data/jcr_filtered.csv`：JCR 影响因子和分区数据
- `data/zky_filtered.csv`：中科院分区数据

## 使用指南

### 创建订阅

1. 登录系统后进入"我的订阅"
2. 点击"新建订阅"
3. 设置搜索参数：
   - 关键词：支持自然语言或 PubMed 检索式
   - 期刊筛选：按影响因子、分区筛选
   - 推送设置：频率、时间、数量限制

### 推送设置

支持三种推送频率：
- **每日推送**：每天固定时间推送
- **每周推送**：每周指定日期推送
- **每月推送**：每月指定日期推送

### 管理功能

管理员可以：
- 管理用户账号和权限
- 配置系统邮箱
- 设置 AI 功能
- 查看系统日志
- 调整系统参数

## 数据库结构

### 主要数据表

- `user`：用户信息和推送设置
- `subscription`：用户订阅和筛选条件
- `article`：文献数据缓存
- `mail_config`：邮箱配置和使用统计
- `ai_setting`：AI 提供商配置
- `system_log`：系统操作日志

### 数据库迁移

```bash
# 初始化迁移
python manage.py db init

# 生成迁移文件
python manage.py db migrate -m "描述"

# 应用迁移
python manage.py db upgrade
```

## API 接口

### 主要路由

- `/`：首页和用户功能
- `/admin`：管理后台
- `/api/search`：文献搜索 API
- `/api/ai/query`：AI 检索式生成
- `/api/ai/translate`：AI 摘要翻译

### 认证方式

使用 Flask-Login 进行会话管理，支持：
- 用户登录/注销
- 权限验证
- 管理员权限控制

## 开发说明

### 项目结构

```
PubMed-Literature-Push-Web/
├── app.py              # 主应用文件
├── setup.py           # 数据库初始化脚本
├── manage.py          # Flask-Migrate 管理
├── requirements.txt   # 依赖包列表
├── data/             # 期刊数据文件
├── migrations/       # 数据库迁移文件
├── quick_venv/       # 虚拟环境目录
└── static/           # 静态资源文件
```

### 核心类

- `JournalDataCache`：期刊数据缓存管理
- `MailSender`：邮件发送服务
- `SimpleLiteraturePushService`：文献推送服务
- `AIService`：AI 功能集成

### 扩展开发

1. **添加新的 AI 提供商**：继承 `AIService` 类
2. **自定义邮件模板**：修改 `_generate_email_html` 方法
3. **新增筛选条件**：扩展 `Subscription` 模型
4. **增加推送方式**：扩展 `MailSender` 类

## 故障排查

### 常见问题

1. **邮件发送失败**
   - 检查邮箱配置和授权码
   - 确认 SMTP 服务器设置
   - 查看邮箱使用限制

2. **文献搜索无结果**
   - 检查网络连接
   - 验证 PubMed API 访问
   - 检查关键词和筛选条件

3. **AI 功能异常**
   - 验证 API 密钥配置
   - 检查网络和代理设置
   - 确认模型可用性

### 日志查看

系统日志存储在数据库中，可通过管理后台查看：
- 登录管理后台
- 进入"系统日志"页面
- 按级别和模块筛选日志

## 许可证

本项目采用 MIT 许可证，详见 [LICENSE](LICENSE) 文件。

## 贡献指南

欢迎提交 Issue 和 Pull Request 来改进项目：

1. Fork 项目
2. 创建功能分支
3. 提交变更
4. 发起 Pull Request

## 联系方式

如有问题或建议，请通过以下方式联系：

- 提交 GitHub Issue
- 发送邮件至项目维护者

---

**注意**：本系统仅供学术研究使用，请遵守相关法律法规和 PubMed 使用条款。