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
            ('query_builder', """你是一个专业的PubMed检索专家。用户会给你一个研究主题或关键词，请帮助生成精确的PubMed检索式。

要求：
1. 使用MeSH词汇和自由词结合
2. 合理使用AND、OR、NOT逻辑操作符
3. 使用字段限定符（如[Title/Abstract], [MeSH Terms]等）
4. 考虑同义词和相关术语
5. 只返回最终的PubMed检索式，不要任何解释说明或分析过程
6. 检索式应该可以直接复制到PubMed搜索框中使用

用户关键词: {keywords}""", True),
            
            # 翻译提示词
            ('translator', """你是一个专业的医学文献翻译专家，精通英文医学术语和中文医学表达。

请将以下英文医学摘要翻译成中文，要求：
1. 准确传达原文的科学内容和逻辑
2. 使用规范的中文医学术语
3. 保持原文的学术风格和专业性
4. 确保翻译流畅自然，符合中文表达习惯
5. 对于专业术语，在首次出现时可以加注英文原文
6. 只返回中文翻译结果，不要任何额外说明或格式

英文摘要：
{abstract}""", True),
            
            # 简介生成提示词
            ('brief_intro', """请为以下医学文献生成一句话简介，要求：
1. 突出文献的核心发现或主要贡献
2. 使用简洁明了的中文表达
3. 控制在30-50字以内
4. 只返回简介内容，不要其他文字

标题: {title}
摘要: {abstract}
简介:""", True)
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
    """使用默认账号快速设置"""
    print("=" * 60)
    print("     PubMed Literature Push - 快速默认设置")
    print("=" * 60)
    print()
    print("将使用以下默认账号：")
    print("  主管理员: admin@pubmed.com / admin123")
    print("  备用管理员: backup-admin@pubmed.com / admin123")
    print("  普通用户: test@example.com / test123")
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
    
    # 使用默认账号创建数据库
    success, created_users = create_custom_database(
        'admin@pubmed.com', 'admin123',
        'test@example.com', 'test123',
        'backup-admin@pubmed.com', 'admin123'
    )
    
    if success:
        print()
        print("=" * 60)
        print("成功：默认设置完成！")
        print("=" * 60)
        print()
        print("可以使用以下账号登录：")
        print("  主管理员: admin@pubmed.com / admin123")
        print("  备用管理员: backup-admin@pubmed.com / admin123")
        print("  普通用户: test@example.com / test123")
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