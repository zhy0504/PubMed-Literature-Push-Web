#!/usr/bin/env python3
"""
PubMed Literature Push - 统一设置脚本
支持交互式自定义设置和快速默认设置
"""

import os
import sys
import sqlite3
import getpass
import re
import argparse
from pathlib import Path
import datetime

def get_database_path():
    """获取数据库文件路径 - 支持Docker和本地环境"""
    if os.path.exists('/app/data'):
        # Docker环境
        return Path("/app/data/pubmed_app.db")
    else:
        # 本地环境
        return Path("pubmed_app.db")

def validate_email(email):
    """验证邮箱格式"""
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    if re.match(pattern, email):
        return True, "邮箱格式正确"
    else:
        return False, "邮箱格式不正确"

def validate_password(password):
    """验证密码强度"""
    if len(password) < 6:
        return False, "密码长度至少6位"
    return True, "密码格式正确"

def get_user_input(prompt, validator=None, required=True, is_password=False):
    """获取用户输入并验证"""
    while True:
        try:
            if is_password:
                value = getpass.getpass(prompt)
            else:
                value = input(prompt).strip()
            
            if not value and required:
                print("  [错误] 此项为必填项，请重新输入")
                continue
            
            if not value and not required:
                return None
                
            if validator:
                is_valid, message = validator(value)
                if not is_valid:
                    print(f"  [错误] {message}")
                    continue
            
            return value
            
        except KeyboardInterrupt:
            print("\n\n用户取消设置")
            sys.exit(0)

def create_custom_database(admin_email, admin_password, user_email=None, user_password=None, backup_admin_email=None, backup_admin_password=None):
    """使用自定义账号创建数据库"""
    print("\n正在创建数据库...")
    
    try:
        # 数据库文件路径 - 支持Docker环境
        db_path = get_database_path()
        
        # 删除现有数据库
        if db_path.exists():
            print(f"删除现有数据库: {db_path}")
            db_path.unlink()
        
        # 创建数据库连接
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()
        
        print("创建数据表...")
        
        # 创建用户表
        cursor.execute('''
            CREATE TABLE user (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email VARCHAR(100) NOT NULL UNIQUE,
                password_hash VARCHAR(200) NOT NULL,
                push_method VARCHAR(20) DEFAULT 'email',
                push_time VARCHAR(10) DEFAULT '09:00',
                push_frequency VARCHAR(20) DEFAULT 'daily',
                push_day VARCHAR(10) DEFAULT 'monday',
                push_month_day INTEGER DEFAULT 1,
                max_articles INTEGER DEFAULT 10,
                is_admin BOOLEAN DEFAULT 0,
                is_active BOOLEAN DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_push TIMESTAMP,
                max_subscriptions INTEGER DEFAULT 10,
                allowed_frequencies VARCHAR(100) DEFAULT 'daily,weekly,monthly'
            )
        ''')
        
        # 创建订阅表
        cursor.execute('''
            CREATE TABLE subscription (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                keywords VARCHAR(500) NOT NULL,
                is_active BOOLEAN DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_search TIMESTAMP,
                max_results INTEGER DEFAULT 10000,
                days_back INTEGER DEFAULT 30,
                exclude_no_issn BOOLEAN DEFAULT 1,
                jcr_quartiles TEXT,
                min_impact_factor REAL,
                cas_categories TEXT,
                cas_top_only BOOLEAN DEFAULT 0,
                push_frequency VARCHAR(20) DEFAULT 'daily',
                push_time VARCHAR(10) DEFAULT '09:00',
                push_day VARCHAR(10) DEFAULT 'monday',
                push_month_day INTEGER DEFAULT 1,
                FOREIGN KEY (user_id) REFERENCES user (id)
            )
        ''')
        
        # 创建文章表
        cursor.execute('''
            CREATE TABLE article (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pmid VARCHAR(20) NOT NULL UNIQUE,
                title TEXT NOT NULL,
                authors TEXT,
                journal VARCHAR(200),
                publish_date TIMESTAMP,
                abstract TEXT,
                doi VARCHAR(100),
                pubmed_url VARCHAR(200),
                keywords TEXT,
                issn VARCHAR(20),
                eissn VARCHAR(20),
                abstract_cn TEXT,
                brief_intro TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # 创建用户文章关联表
        cursor.execute('''
            CREATE TABLE user_article (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                article_id INTEGER NOT NULL,
                subscription_id INTEGER NOT NULL,
                push_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                is_read BOOLEAN DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES user (id),
                FOREIGN KEY (article_id) REFERENCES article (id),
                FOREIGN KEY (subscription_id) REFERENCES subscription (id)
            )
        ''')
        
        # 创建系统日志表
        cursor.execute('''
            CREATE TABLE system_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                level VARCHAR(10) NOT NULL,
                module VARCHAR(50) NOT NULL,
                message VARCHAR(500) NOT NULL,
                user_id INTEGER,
                ip_address VARCHAR(45),
                FOREIGN KEY (user_id) REFERENCES user (id)
            )
        ''')
        
        # 创建系统设置表
        cursor.execute('''
            CREATE TABLE system_setting (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                key VARCHAR(100) NOT NULL UNIQUE,
                value TEXT NOT NULL,
                description VARCHAR(200),
                category VARCHAR(50) DEFAULT 'general',
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # 创建密码重置令牌表
        cursor.execute('''
            CREATE TABLE password_reset_token (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                token VARCHAR(100) NOT NULL UNIQUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expires_at TIMESTAMP NOT NULL,
                used BOOLEAN DEFAULT 0,
                FOREIGN KEY (user_id) REFERENCES user (id)
            )
        ''')
        
        # 创建AI设置表
        cursor.execute('''
            CREATE TABLE ai_setting (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                provider_name VARCHAR(50) NOT NULL,
                base_url VARCHAR(200) NOT NULL,
                api_key VARCHAR(200) NOT NULL,
                is_active BOOLEAN DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # 创建AI模型表
        cursor.execute('''
            CREATE TABLE ai_model (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                provider_id INTEGER NOT NULL,
                model_name VARCHAR(100) NOT NULL,
                model_id VARCHAR(100) NOT NULL,
                model_type VARCHAR(20) NOT NULL,
                is_available BOOLEAN DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (provider_id) REFERENCES ai_setting (id)
            )
        ''')
        
        # 创建AI提示词模板表
        cursor.execute('''
            CREATE TABLE ai_prompt_template (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                template_type VARCHAR(20) NOT NULL,
                prompt_content TEXT NOT NULL,
                is_default BOOLEAN DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # 创建邮件配置表
        cursor.execute('''
            CREATE TABLE mail_config (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name VARCHAR(100) NOT NULL,
                smtp_server VARCHAR(100) NOT NULL,
                smtp_port INTEGER NOT NULL DEFAULT 465,
                username VARCHAR(100) NOT NULL,
                password VARCHAR(200) NOT NULL,
                use_tls BOOLEAN DEFAULT 1,
                is_active BOOLEAN DEFAULT 1,
                daily_limit INTEGER DEFAULT 100,
                current_count INTEGER DEFAULT 0,
                last_used TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        print("数据表创建成功！")
        
        # 生成密码哈希
        print("创建用户账号...")
        
        try:
            from werkzeug.security import generate_password_hash
            admin_password_hash = generate_password_hash(admin_password)
            backup_admin_password_hash = generate_password_hash(backup_admin_password) if backup_admin_password else None
            user_password_hash = generate_password_hash(user_password) if user_password else None
            print("使用 Werkzeug 生成密码哈希")
        except ImportError:
            import hashlib
            admin_password_hash = hashlib.sha256(admin_password.encode()).hexdigest()
            backup_admin_password_hash = hashlib.sha256(backup_admin_password.encode()).hexdigest() if backup_admin_password else None
            user_password_hash = hashlib.sha256(user_password.encode()).hexdigest() if user_password else None
            print("使用 SHA256 生成密码哈希")
        
        # 创建用户账号...
        created_users = [(admin_email, True)]
        if backup_admin_email:
            created_users.append((backup_admin_email, True))
        if user_email:
            created_users.append((user_email, False))
        
        # 创建主管理员账号
        cursor.execute('''
            INSERT INTO user (email, password_hash, push_method, push_time, push_frequency, max_articles, is_admin, is_active)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            admin_email,
            admin_password_hash,
            'email',
            '09:00',
            'daily',
            10,
            1,  # is_admin = True
            1   # is_active = True
        ))
        
        # 创建备用管理员账号（如果提供）
        if backup_admin_email and backup_admin_password:
            cursor.execute('''
                INSERT INTO user (email, password_hash, push_method, push_time, push_frequency, max_articles, is_admin, is_active)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                backup_admin_email,
                backup_admin_password_hash,
                'email',
                '09:00',
                'daily',
                10,
                1,  # is_admin = True
                1   # is_active = True
            ))
        
        # 创建普通用户账号（如果提供）
        if user_email and user_password:
            cursor.execute('''
                INSERT INTO user (email, password_hash, push_method, push_time, push_frequency, max_articles, is_admin, is_active)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                user_email,
                user_password_hash,
                'email',
                '09:00',
                'daily',
                10,
                0,  # is_admin = False
                1   # is_active = True
            ))
        
        # 插入默认系统设置
        default_settings = [
            ('pubmed_max_results', '100', 'PubMed每次最大检索数量', 'pubmed'),
            ('pubmed_timeout', '30', 'PubMed请求超时时间(秒)', 'pubmed'),
            ('pubmed_api_key', '', 'PubMed API Key', 'pubmed'),
            ('push_daily_time', '09:00', '默认每日推送时间', 'push'),
            ('push_max_articles', '50', '每次推送最大文章数', 'push'),
            ('push_enabled', 'true', '启用自动推送', 'push'),
            ('push_check_frequency', '0.0833', '推送任务检查频率(小时)', 'push'),
            ('system_name', 'PubMed Literature Push', '系统名称', 'system'),
            ('log_retention_days', '30', '日志保留天数', 'system'),
            ('user_registration_enabled', 'true', '允许用户注册', 'system'),
        ]
        
        for key, value, desc, category in default_settings:
            cursor.execute('''
                INSERT INTO system_setting (key, value, description, category)
                VALUES (?, ?, ?, ?)
            ''', (key, value, desc, category))
        
        # 插入默认AI提示词模板
        default_prompts = [
            # 检索式生成提示词
            ('query_builder', """# 任务：构建专业级PubMed文献检索式

## 1. 角色与目标
你将扮演一位精通PubMed检索策略的顶级医学信息专家和策略决策者，你的核心目标是根据用户提供的自然语言关键词 `{keywords}`，通过严谨的PICO框架进行结构化分析，并以"极致查准"为首要策略，仅在用户明确要求时切换为"查全优先"，最终生成一个逻辑严谨、覆盖周全、可直接在PubMed中执行的、符合系统评价（Systematic Review）标准的高质量检索式。

## 2. 背景与上下文
医学研究人员、临床医生及学生在科研或实践中，需要快速、准确地从PubMed数据库获取高质量文献。然而，构建一个兼具高查全率（Recall）和高查准率（Precision）的检索式需要专业的知识和技巧，而用户通常缺乏这方面的训练。因此，需要你的专业能力将他们的研究问题转化为一个高效、严谨的检索方案。

## 3. 关键步骤
在你的创作过程中，请遵循以下内部步骤来构思和打磨作品：
1.  **核心概念识别与PICO解构**: 首先，识别用户输入 `{keywords}` 中的所有核心概念。然后，将这些概念系统性地映射到PICO框架（P=人群/问题, I=干预/关注点, C=比较, O=结局），并优先聚焦于构建P和I的检索模块。
2.  **概念词汇扩展**: 对每个核心概念（尤其是P和I），进行系统的词汇扩展，包括但不限于：MeSH官方入口词、同义词、近义词、相关术语、缩写、药物/设备商品名、拼写变体（如英美差异）和单复数形式。这是确保覆盖周全的关键。
3.  **智能策略决策**: 分析用户意图，默认采用"极致查准"策略。仅当用户明确表达需要更广泛的结果（如包含"太少"、"找不到"、"更全面"）时，才切换至"查全优先"策略。
4.  **分策略构建检索模块**: 根据上一步的决策执行相应的构建逻辑。
    - **极致查准模式 (默认)**: 彻底重构检索式为"双重狙击"结构：`((P_mesh[Majr] AND I_mesh[Majr]) OR (P_freetext[ti] AND I_freetext[ti]))`。此结构通过 `OR` 连接"主要主题模块"（使用扩展后的MeSH词作为焦点）和"标题模块"（使用扩展后的自由词在标题中进行精确匹配），以实现最高的精准度。
    - **查全优先模式 (触发)**: 为每个核心概念（如P和I）创建独立的检索模块，模块内部使用 `OR` 连接其对应的所有MeSH词和扩展后的自由词 `(MeSH词[Mesh] OR 自由词1[tiab] OR 自由词2[tiab]...)`，然后使用 `AND` 连接各模块。
5.  **生成最终检索式**: 组合所有模块，生成一个语法正确、无任何多余解释的完整PubMed检索式。

## 4. 输出要求
- **格式**: 纯文本，仅包含最终的PubMed检索式字符串。
- **风格**: 专业、严谨、语法精确。
- **约束**:
    - 必须确保检索式语法完全符合PubMed官方规范，可直接复制使用。
    - 检索词的选择必须系统且周全：MeSH词需准确选取，自由词部分必须全面覆盖在"概念词汇扩展"步骤中分析出的同义词、近义词、缩写、拼写变体及单复数形式。
    - 每个概念模块必须使用括号 `()` 清晰地组织，确保布尔运算的优先级正确无误。
    - **最终输出**: 你的最终回复应仅包含最终成果本身，不得包含任何步骤说明、分析或其他无关内容。""", True),
            
            # 翻译提示词
            ('translator', """# Role：医学文献翻译专家

## Background：
用户可能是一名医学研究者、临床医生、医学生或生物医药从业者。他们需要快速、准确地理解一篇英文医学文献的核心内容，以便用于学术研究、临床决策、论文写作或学习。由于时间限制或语言壁垒，他们需要一个可靠的、专业的翻译工具来将英文摘要转化为高质量的中文内容，确保科学信息的无损传递。

## Attention：
你的每一次翻译都是在为医学知识的传播和应用铺路。精准、专业的翻译能够帮助中国的科研人员和医生与国际前沿保持同步，推动医学进步。请以最高的专业标准和严谨的科学态度对待这项任务，你的工作极具价值。

## Profile：
- Author: prompt-optimizer
- Version: 1.0
- Language: 中文
- Description: 一位顶级的医学文献翻译专家，致力于将英文医学摘要精准、流畅、专业地翻译成中文，完美保留原文的科学内涵和学术风格。

### Skills:
- **双语医学术语精通**: 深度掌握英汉两种语言在解剖学、生理学、病理学、药理学、分子生物学等领域的专业术语，并能实现精准对应。
- **科学逻辑分析能力**: 能够快速解析医学研究摘要中的研究设计、方法、结果和结论，准确理解并再现复杂的科学逻辑链条。
- **中文学术写作能力**: 擅长使用规范、严谨、客观的中文学术语言，文风符合国内核心医学期刊的发表标准。
- **上下文语境理解**: 能够根据上下文准确判断多义词、缩写和特定表达的含义，避免出现歧义和误译。
- **细节与数据处理能力**: 对数字、单位、统计学指标（如P值、置信区间）、基因/蛋白名称等关键信息具有极高的敏感度和准确性。

## Goals:
- 将用户提供的英文医学摘要 `{abstract}` 完整翻译成专业、准确的中文版本。
- 确保译文100%忠实于原文的科学内容、实验数据和研究结论，无任何信息遗漏或失真。
- 在翻译过程中统一并使用中国大陆地区广泛接受的官方或权威医学术语。
- 保持原文的客观、严谨的学术风格，使译文读起来就像一篇原生的中文医学摘要。
- 产出一篇流畅、连贯、符合中文表达习惯的译文，便于专业人士快速阅读和理解。

## Constrains:
- 严格遵循信、达、雅的翻译原则，其中"信"（忠实原文）是最高准则。
- 禁止在译文中添加任何原文不存在的个人解释、评论或补充信息。
- 对于关键的或新兴的专业术语，在中文译文后首次出现时，必须用半角括号 `()` 注明英文原文。
- 输出内容仅包含翻译后的中文摘要正文，不得包含"摘要"、"译文"等任何标题、标签或说明性文字。
- 翻译过程中必须保持中立和客观，不得使用任何口语化、情绪化或主观性的词汇。

## Workflow:
1. **通读原文，把握主旨**: 首先快速浏览整个英文摘要，理解研究的核心领域、目的、主要发现和结论，建立宏观认知。
2. **术语识别与预处理**: 精准识别文中的所有医学专业术语、统计学词汇、缩写等，并确定其最恰当的中文对应词。标记出首次出现的、需要加注英文的术语。
3. **逐句翻译与逻辑重构**: 以句子为单位进行翻译，重点处理长难句，确保主谓宾结构清晰，句间逻辑关系（因果、转折、并列等）在中文语境下表达准确。
4. **润色与风格统一**: 完成初稿后，通读中文译文，从中文学术写作的角度进行润色，调整语序，优化措辞，确保全文风格统一、流畅自然。
5. **终审与格式检查**: 将译文与原文逐字逐句进行最终比对，核实所有数据、术语和关键信息的准确性，并确保输出格式完全符合`OutputFormat`的要求。

## OutputFormat:
- 输出内容为一段完整的、连续的中文文本，不分段。
- 首次出现的专业术语，格式为"中文译名 (English Term)"。
- 除翻译内容和必要的术语注释外，不包含任何其他字符或格式。

## Suggestions:
- 优先采用意译而非死板的直译，特别是对于复杂的从句结构，应先理解其内在逻辑，再用符合中文表达习惯的方式重组句子。
- 在遇到不确定的术语时，应基于上下文的科学逻辑进行推断，选择在该领域内最可能和最贴切的译法。
- 持续学习和更新自己的术语库，特别是对于最新的药物名称、基因靶点和治疗技术，力求与国际研究前沿保持一致。
- 在处理统计结果的描述时，要格外注意时态和语气的准确性，如区分"suggests"、"indicates"和"demonstrates"等词的强度差异。
- 面对结构复杂的长句，可采用"拆分-重组"的策略：先将长句拆解为多个独立的语义单元，翻译每个单元，然后按照中文的逻辑顺序重新组合成通顺的句子。

## Initialization
作为医学文献翻译专家，你必须遵守Constrains中列出的所有规则，并使用中文作为默认交流语言。

英文摘要：
{abstract}""", True),
            
            # 简介生成提示词
            ('brief_intro', """# Role：医学文献分析师

## Background：
在医学研究和临床实践领域，专业人士（如医生、研究员、学生）每天需要处理海量的医学文献。为了快速筛选和评估文献的相关性与价值，他们迫切需要一种高效的方法来迅速掌握每篇文献的核心内容。本任务旨在通过对文献标题和摘要的深度分析，生成一个高度浓缩、精准传达核心发现的一句话简介，以满足用户快速获取信息、节省时间的核心诉求。

## Attention：
你的专业能力至关重要。每一个精准的提炼，都能帮助医学工作者在知识的海洋中快速导航，加速科研进程和临床决策。请以最高的专业标准，将复杂的医学信息转化为清晰、精炼的知识结晶，你的每一次输出都是在为医学知识的传播贡献力量。

## Profile：
- Author: prompt-optimizer
- Version: 1.0
- Language: 中文
- Description: 一位专业的医学文献分析专家，擅长深度解读医学研究的标题与摘要，精准提炼其核心发现与主要贡献，并生成高度凝练、专业且易于理解的一句话简介。

### Skills:
- **医学术语理解能力**: 精准理解并运用复杂的医学专业术语和概念。
- **核心信息提取**: 快速从大量文本中识别并抓取研究的关键变量、主要结果和最终结论。
- **学术语言精炼**: 能够将冗长、复杂的学术描述转化为简洁、流畅、专业的中文表达。
- **逻辑归纳与判断**: 准确判断文献的主要贡献，区分主要发现与次要信息。
- **信息整合能力**: 将文献的标题和摘要信息进行有效整合，形成一个完整、连贯的核心观点。

## Goals:
- 为给定的医学文献标题（{title}）和摘要（{abstract}）生成一句专业简介。
- 在简介中明确、突出文献的核心发现、创新方法或主要贡献。
- 使用专业、严谨且通俗易懂的中文进行表达，确保信息传递的准确性。
- 严格控制简介的长度在30至50个汉字之间。
- 忠实于原文内容，确保简介是对原文核心思想的精准概括。

## Constrains:
- 绝对禁止输出简介内容之外的任何文字，包括但不限于解释、标题、标签或问候语。
- 输出的字数必须严格控制在30-50字以内，不得超出或远低于此范围。
- 禁止引入任何未在标题和摘要中提及的外部信息或个人解读。
- 简介内容必须保持客观、中立的科学立场，避免使用带有主观色彩的评价性词语（如"重大突破"、"首次发现"等）。
- 必须确保输出内容的科学准确性，不得曲解或夸大原文的研究结论。

## Workflow:
1. **通读与理解**: 首先，完整阅读并深入理解所提供的文献`{title}`和`{abstract}`，明确研究的背景、目的、方法、结果和结论。
2. **定位核心发现**: 其次，在摘要中重点分析"Results"和"Conclusions"部分，精准定位研究最主要、最具影响力的发现或贡献。
3. **构建核心句式**: 然后，围绕核心发现，使用"本研究揭示了/证实了/提出了一种..."等学术句式，草拟一句话简介的初稿。
4. **精炼与优化**: 接着，对初稿进行反复修改和润色，删除冗余信息，替换更精准的词语，确保表达清晰、语言流畅，并符合字数要求。
5. **最终审核**: 最后，将最终版本的简介与原文进行比对，检查其是否准确反映了核心思想，并确认是否满足所有约束条件，然后输出。

## OutputFormat:
- 输出内容为纯文本字符串，不包含任何格式标记。
- 仅包含一句话简介内容，无任何前缀或后缀。
- 句子以中文句号"。"结尾，确保格式的完整性。

## Suggestions:
- **优先关注结论句**: 摘要的最后一句通常是作者对整个研究最核心的总结，应作为提炼简介的首要切入点。
- **采用动词驱动表达**: 尽量使用强有力的动词（如：揭示、证实、阐明、构建）作为句子的开端，能让简介更具概括性和冲击力。
- **量化结果优先原则**: 如果摘要中提供了关键的量化数据（如有效率、风险比等），应优先考虑将其精炼后纳入简介，以提升信息价值。
- **标题与摘要交叉验证**: 将从摘要中提炼的要点与标题进行比对，确保简介的核心内容与标题的指向高度一致。
- **建立因果逻辑链**: 在分析时，尝试构建"研究对象-干预措施-关键结果"的逻辑链条，这有助于快速抓住研究主干，形成简介的核心结构。

## Initialization
作为医学文献分析师，你必须遵守所有约束条件，使用默认的中文与用户进行交流。

标题: {title}
摘要: {abstract}""", True)
        ]
        
        for template_type, prompt_content, is_default in default_prompts:
            cursor.execute('''
                INSERT INTO ai_prompt_template (template_type, prompt_content, is_default)
                VALUES (?, ?, ?)
            ''', (template_type, prompt_content, is_default))
        
        print("默认AI提示词模板创建完成")
        
        # 提交更改
        conn.commit()
        
        # ===== 详细验证创建结果 =====
        print("\n" + "="*60)
        print("📊 数据库表结构验证报告")
        print("="*60)
        
        # 检查所有表
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [row[0] for row in cursor.fetchall()]
        print(f"🗂️  已创建的表 ({len(tables)}): {', '.join(tables)}")
        
        # 重点检查Article表结构
        if 'article' in tables:
            print(f"\n📋 Article表详细结构:")
            cursor.execute("PRAGMA table_info(article)")
            columns = cursor.fetchall()
            print(f"   总字段数: {len(columns)}")
            print(f"   字段详情:")
            
            for col in columns:
                col_id, name, col_type, not_null, default_value, pk = col
                nullable = "NOT NULL" if not_null else "NULL"
                default_info = f", DEFAULT: {default_value}" if default_value else ""
                pk_info = " (PRIMARY KEY)" if pk else ""
                print(f"     {col_id+1:2d}. {name:15s} | {col_type:15s} | {nullable}{default_info}{pk_info}")
            
            # 验证关键AI字段
            actual_columns = {col[1] for col in columns}
            ai_fields = {
                'abstract_cn': '中文翻译字段',
                'brief_intro': 'AI简介字段', 
                'issn': 'ISSN字段',
                'eissn': '电子ISSN字段'
            }
            
            print(f"\n🔍 关键AI字段验证:")
            all_present = True
            for field, desc in ai_fields.items():
                if field in actual_columns:
                    print(f"     ✅ {field:15s} : 存在 ({desc})")
                else:
                    print(f"     ❌ {field:15s} : 缺失 ({desc})")
                    all_present = False
                    
            if all_present:
                print(f"\n🎉 Article表结构完整！所有AI功能字段都存在")
            else:
                print(f"\n⚠️  Article表存在缺失字段，可能影响AI功能")
        else:
            print("❌ Article表未创建！")
        
        # 检查AI提示词模板
        cursor.execute("SELECT template_type, is_default FROM ai_prompt_template WHERE is_default=1")
        prompts = cursor.fetchall()
        print(f"\n📝 默认AI提示词模板:")
        for template_type, is_default in prompts:
            print(f"     ✅ {template_type}")
        
        print("\n" + "="*60)
        print("📊 验证报告完成")
        print("="*60)
        
        # 验证创建结果
        print("验证账号创建...")
        cursor.execute('SELECT email, is_admin FROM user ORDER BY is_admin DESC, email')
        users = cursor.fetchall()
        
        print("\n创建的用户账号:")
        for email, is_admin in users:
            user_type = "管理员" if is_admin else "普通用户"
            print(f"  {email} - {user_type}")
        
        # 显示账号信息给用户（不保存到文件）
        display_passwords = {
            'admin': [admin_password],
            'user': []
        }
        
        if backup_admin_password:
            display_passwords['admin'].append(backup_admin_password)
        
        if user_password:
            display_passwords['user'].append(user_password)
        
        display_final_credentials(created_users, display_passwords)
        
        conn.close()
        
        print(f"\n成功：数据库创建成功！")
        return True, created_users
        
    except Exception as e:
        print(f"错误：数据库创建失败: {e}")
        if 'conn' in locals():
            conn.rollback()
            conn.close()
        return False, []

def display_final_credentials(users, display_passwords=None):
    """显示最终的账号信息给用户（仅显示，不保存）"""
    try:
        # 显示完整的账号信息（包含密码）给用户，但不保存
        if display_passwords:
            print("\n" + "=" * 60)
            print("重要：请记录以下登录信息（密码只显示这一次）")
            print("=" * 60)
            
            admin_passwords = display_passwords.get('admin', [])
            user_passwords = display_passwords.get('user', [])
            
            if admin_passwords:
                print("\n管理员账号:")
                for i, email in enumerate([e for e, is_admin in users if is_admin]):
                    if i < len(admin_passwords):
                        print(f"  {email} / {admin_passwords[i]}")
            
            if user_passwords:
                print("\n普通用户账号:")
                for i, email in enumerate([e for e, is_admin in users if not is_admin]):
                    if i < len(user_passwords):
                        print(f"  {email} / {user_passwords[i]}")
            
            print("\n重要提醒：")
            print("   - 密码不会保存到任何文件中")
            print("   - 请立即记录上述密码信息")  
            print("   - 如忘记密码，需重新运行 setup.py 重置")
            print("=" * 60)
        
    except Exception as e:
        print(f"显示账号信息失败: {e}")

def setup_default_accounts():
    """使用默认账号快速设置 - 从环境变量读取或使用默认值"""
    print("=" * 60)
    print("     PubMed Literature Push - 快速默认设置")
    print("=" * 60)
    print()

    # 从环境变量读取账号配置，如果没有则使用默认值
    admin_email = os.environ.get('DEFAULT_ADMIN_EMAIL', 'admin@pubmed.com')
    admin_password = os.environ.get('DEFAULT_ADMIN_PASSWORD', 'admin123')

    print("将使用以下默认账号：")
    print(f"  管理员: {admin_email} / {admin_password}")
    print()

    # 检查是否已存在数据库
    db_path = get_database_path()
    if db_path.exists():
        print(f"警告：检测到已存在数据库文件 {db_path}")
        overwrite = input("是否要重新创建数据库？这将删除所有现有数据 (y/N): ").strip().lower()
        if overwrite not in ['y', 'yes', '是']:
            print("设置已取消。")
            return 0
        print()

    # 使用配置的账号创建数据库 - 只创建一个管理员账号
    success, created_users = create_custom_database(
        admin_email, admin_password,
        None, None,  # 不创建普通用户
        None, None   # 不创建备用管理员
    )

    if success:
        print()
        print("=" * 60)
        print("成功：默认设置完成！")
        print("=" * 60)
        print()
        print("可以使用以下账号登录：")
        print(f"  管理员: {admin_email} / {admin_password}")
        print()
        print("=" * 60)
        return 0
    else:
        print("错误：默认设置失败！")
        return 1

def main():
    """主函数"""
    # 解析命令行参数
    parser = argparse.ArgumentParser(
        description='PubMed Literature Push - 数据库设置工具',
        epilog='示例: python setup.py --default  # 使用默认账号快速设置'
    )
    parser.add_argument(
        '--default', 
        action='store_true',
        help='使用默认账号快速设置（跳过交互式配置）'
    )
    
    args = parser.parse_args()
    
    # 如果指定了 --default 参数，使用默认账号设置
    if args.default:
        return setup_default_accounts()
    
    # 否则进行交互式自定义设置
    print("=" * 60)
    print("     PubMed Literature Push - 交互式设置")
    print("=" * 60)
    print()
    print("欢迎使用 PubMed Literature Push 系统！")
    print("请根据提示设置您的管理员账号和普通用户账号。")
    print()
    print("提示:")
    print("- 如需快速使用默认账号，请运行: python setup.py --default")
    print("- 管理员账号是必须的，用于系统管理")
    print("- 普通用户账号是可选的，用于测试和日常使用")
    print("- 可以设置备用管理员账号以提高安全性")
    print()
    
    # 检查是否已存在数据库
    db_path = get_database_path()
    if db_path.exists():
        print(f"警告：检测到已存在数据库文件 {db_path}")
        overwrite = input("是否要重新创建数据库？这将删除所有现有数据 (y/N): ").strip().lower()
        if overwrite not in ['y', 'yes', '是']:
            print("设置已取消。")
            return 0
        print()
    
    try:
        # 设置主管理员账号
        print("第1步：设置主管理员账号")
        print("-" * 40)
        admin_email = get_user_input(
            "管理员邮箱: ", 
            validator=validate_email,
            required=True
        )
        admin_password = get_user_input(
            "管理员密码: ",
            validator=validate_password,
            required=True,
            is_password=True
        )
        admin_password_confirm = get_user_input(
            "确认密码: ",
            required=True,
            is_password=True
        )
        
        if admin_password != admin_password_confirm:
            print("错误：两次输入的密码不一致！")
            return 1
        
        print(f"成功：主管理员账号: {admin_email}")
        print()
        
        # 设置备用管理员账号（可选）
        print("第2步：设置备用管理员账号（可选）")
        print("-" * 40)
        print("备用管理员账号可以提高系统安全性，建议设置")
        add_backup = input("是否添加备用管理员账号？(Y/n): ").strip().lower()
        
        backup_admin_email = None
        backup_admin_password = None
        
        if add_backup not in ['n', 'no', '否']:
            backup_admin_email = get_user_input(
                "备用管理员邮箱: ",
                validator=validate_email,
                required=True
            )
            
            if backup_admin_email == admin_email:
                print("错误：备用管理员邮箱不能与主管理员相同！")
                return 1
                
            backup_admin_password = get_user_input(
                "备用管理员密码: ",
                validator=validate_password,
                required=True,
                is_password=True
            )
            backup_admin_password_confirm = get_user_input(
                "确认密码: ",
                required=True,
                is_password=True
            )
            
            if backup_admin_password != backup_admin_password_confirm:
                print("错误：两次输入的密码不一致！")
                return 1
            
            print(f"成功：备用管理员账号: {backup_admin_email}")
        else:
            print("跳过备用管理员设置")
        
        print()
        
        # 设置普通用户账号（可选）
        print("第3步：设置普通用户账号（可选）")
        print("-" * 40)
        print("普通用户账号用于测试系统功能，可以不设置")
        add_user = input("是否添加普通用户账号？(y/N): ").strip().lower()
        
        user_email = None
        user_password = None
        
        if add_user in ['y', 'yes', '是']:
            user_email = get_user_input(
                "普通用户邮箱: ",
                validator=validate_email,
                required=True
            )
            
            if user_email == admin_email or user_email == backup_admin_email:
                print("错误：普通用户邮箱不能与管理员邮箱相同！")
                return 1
                
            user_password = get_user_input(
                "普通用户密码: ",
                validator=validate_password,
                required=True,
                is_password=True
            )
            user_password_confirm = get_user_input(
                "确认密码: ",
                required=True,
                is_password=True
            )
            
            if user_password != user_password_confirm:
                print("错误：两次输入的密码不一致！")
                return 1
            
            print(f"成功：普通用户账号: {user_email}")
        else:
            print("跳过普通用户设置")
        
        print()
        
        # 确认设置
        print("第4步：确认设置")
        print("-" * 40)
        print("即将创建以下账号：")
        print(f"  主管理员: {admin_email}")
        if backup_admin_email:
            print(f"  备用管理员: {backup_admin_email}")
        if user_email:
            print(f"  普通用户: {user_email}")
        print()
        
        confirm = input("确认创建数据库？(Y/n): ").strip().lower()
        if confirm in ['n', 'no', '否']:
            print("设置已取消。")
            return 0
        
        # 创建数据库
        success, created_users = create_custom_database(
            admin_email, admin_password,
            user_email, user_password,
            backup_admin_email, backup_admin_password
        )
        
        if success:
            print()
            print("=" * 60)
            print("成功：设置完成！")
            print("=" * 60)
            print()
            print("数据库已创建完成，您可以开始使用系统了。")
            print()
            print()
            print("重要：密码信息出于安全考虑不会保存到任何文件。")
            print("    请使用上面显示的账号和密码进行登录。")
            print("=" * 60)
            
            return 0
        else:
            print("错误：设置失败！")
            return 1
            
    except KeyboardInterrupt:
        print("\n\n用户取消设置")
        return 0
    except Exception as e:
        print(f"\n错误：设置过程中发生错误: {e}")
        import traceback
        traceback.print_exc()
        return 1

if __name__ == '__main__':
    sys.exit(main())