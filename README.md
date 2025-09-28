# PubMed Literature Push Web

## 简介
PubMed Literature Push Web 是一个智能的 PubMed 文献推送系统，支持多邮箱轮询发送、期刊质量评估以及 AI 驱动的检索式生成和摘要翻译功能。系统提供完整的用户管理和后台管理能力，帮助科研团队高效追踪本领域的最新研究进展。

## 功能概览

### 核心功能
- 智能文献搜索：支持关键词、布尔逻辑与高级筛选条件的 PubMed 检索
- 多邮箱轮询：允许配置多个 SMTP 邮箱，自动轮询发送以规避单一邮箱限制
- 定时推送：可按日/周/月灵活设置推送计划与邮件收件人
- AI 智能助手：自动生成检索式、摘要翻译及高亮重点
- 用户与权限：支持注册、登录、角色区分和权限控制

### 高级能力
- 期刊质量评估：融合 JCR 影响因子与中科院分区数据
- 批量处理：支持批量文献导入、筛选与推送
- 数据缓存：JournalDataCache 缓存期刊数据，提高检索性能
- 管理后台：集中管理用户、邮箱、AI 配置与系统日志
- 运行监控：记录任务执行日志，便于审计与排错

## 技术栈
- 后端：Flask 2.3.3、SQLAlchemy、APScheduler
- 数据库：SQLite（可扩展至其他数据库）
- 邮件服务：Flask-Mail + SMTP
- AI 集成：OpenAI API（支持扩展其他厂商）
- 前端：Bootstrap、JavaScript
- 数据处理：Pandas、CSV

## 快速开始

### 环境要求
- Python 3.8+
- SQLite 3

### 安装步骤
1. 克隆项目
   ```bash
   git clone https://github.com/your-repo/PubMed-Literature-Push-Web.git
   cd PubMed-Literature-Push-Web
   ```
2. 创建虚拟环境
   ```bash
   python -m venv quick_venv
   # Linux / macOS
   source quick_venv/bin/activate
   # Windows PowerShell
   quick_venv\Scripts\Activate.ps1
   ```
3. 安装依赖
   ```bash
   pip install -r requirements.txt
   ```
4. 初始化数据库
   ```bash
   # 使用默认账号快速配置
   python setup.py --default
   # 或进入交互式向导自定义配置
   python setup.py
   ```
5. （可选）初始化邮箱示例配置
   ```bash
   python init_mail_configs.py
   ```
6. 启动应用
   ```bash
   # Linux / macOS
   chmod +x start.sh
   ./start.sh

   # Windows
   start.bat
   # 或直接运行
   python app.py
   ```

### 默认账号
执行 `python setup.py --default` 后会生成以下账号：
- 主管理员：`admin@pubmed.com` / `admin123`
- 备用管理员：`backup-admin@pubmed.com` / `admin123`
- 测试用户：`test@example.com` / `test123`

## 关键配置

### 邮箱配置
1. 登录后台管理：`http://127.0.0.1:5003/admin`
2. 进入「邮箱管理」，编辑示例配置
3. 根据邮箱类型填写 SMTP 主机、端口、用户名与授权码
   - QQ 邮箱：开启 SMTP 服务，使用授权码
   - 163 邮箱：开启客户端授权密码
   - Gmail：启用应用专用密码

### AI 配置
1. 在后台进入「AI 设置」
2. 新增提供商（默认示例为 OpenAI）
3. 配置模型与用途：
   - 检索式生成：将自然语言需求转为 PubMed 检索式
   - 摘要翻译：输出中文摘要或要点

### 期刊数据
- `data/jcr_filtered.csv`：JCR 影响因子与分区
- `data/zky_filtered.csv`：中科院分区数据
- 可按需替换或更新 CSV 文件，以覆盖最新期刊指标

## 常用操作
- 创建订阅：在前端页面进入「我的订阅」→「新建订阅」，设置关键词、期刊筛选条件与推送频率
- 管理推送任务：通过后台查看任务状态、手动触发或暂停计划任务
- 邮件模板：在代码中自定义 `_generate_email_html`，调整推送邮件样式与内容
- AI 能力扩展：继承 `AIService`，集成新的大模型或第三方服务

## 数据库与迁移
```bash
# 初始化迁移目录（首次）
python manage.py db init
# 生成迁移文件
python manage.py db migrate -m "描述"
# 应用迁移
python manage.py db upgrade
```
如需切换数据库，请在 `app.py` 与相关配置中更新 SQLAlchemy 连接字符串。

## API 速览
- `/`：前台用户入口
- `/admin`：管理后台
- `/api/search`：文献检索 API
- `/api/ai/query`：AI 检索式生成
- `/api/ai/translate`：AI 摘要翻译
所有接口通过 Flask-Login 管理会话，敏感操作需具备相应角色权限。

## 故障排查
1. 邮件发送失败
   - 核对邮箱授权码、SMTP 主机与端口
   - 检查发送频率限制与网络可达性
2. 文献搜索无结果
   - 检查网络连接和 PubMed API 可访问性
   - 调整检索关键词与筛选条件
3. AI 功能异常
   - 确认 API Key 正确、余额充足
   - 检查代理或网络设置

## 日志查看
- 登录后台 → 「系统日志」查看任务执行与异常信息
- 若需更多细节，可启用 Flask 调试日志或直接查看数据库中的 `system_log` 表

## 贡献指南
1. Fork 仓库并创建功能分支
2. 编写或更新测试，确保现有测试通过
3. 提交 Pull Request 并说明变更内容

## 许可证
项目采用 MIT License，详见 `LICENSE` 文件。

## 联系
- 在 GitHub 提交 Issue

---
**说明**：本系统仅供科研与教学用途，请遵守 PubMed 使用条款及相关法律法规。
