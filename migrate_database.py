#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
数据库迁移脚本：统一迁移脚本
1. 为 subscription 表添加 filter_config 和 use_advanced_filter 字段
2. 更新 user 表的 allowed_frequencies 字段（从 'weekly' 更新为 'daily,weekly,monthly'）
3. 添加邀请码功能表（invite_code 和 invite_code_usage）
4. 为 mail_config 表添加 from_email 字段
"""

import sqlite3
import os
import sys

# 设置输出编码为UTF-8（Windows兼容）
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

def migrate_database():
    """执行数据库迁移"""
    # 支持Docker和本地环境
    if os.path.exists('/app/data'):
        # Docker环境
        db_path = '/app/data/pubmed_app.db'
    else:
        # 本地环境
        db_path = os.path.join(os.path.dirname(__file__), 'pubmed_app.db')

    if not os.path.exists(db_path):
        print(f"数据库文件不存在: {db_path}")
        return

    print(f"正在迁移数据库: {db_path}\n")

    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # ==================== 迁移 1: 添加订阅筛选字段 ====================
        print("【迁移 1】检查 subscription 表字段...")
        cursor.execute("PRAGMA table_info(subscription)")
        columns = [col[1] for col in cursor.fetchall()]

        # 添加 filter_config 字段
        if 'filter_config' not in columns:
            print("  添加 filter_config 字段...")
            cursor.execute("ALTER TABLE subscription ADD COLUMN filter_config TEXT")
            print("  [OK] filter_config 字段已添加")
        else:
            print("  [OK] filter_config 字段已存在")

        # 添加 use_advanced_filter 字段
        if 'use_advanced_filter' not in columns:
            print("  添加 use_advanced_filter 字段...")
            cursor.execute("ALTER TABLE subscription ADD COLUMN use_advanced_filter BOOLEAN DEFAULT 0")
            print("  [OK] use_advanced_filter 字段已添加")
        else:
            print("  [OK] use_advanced_filter 字段已存在")

        # ==================== 迁移 2: 更新用户推送频率权限 ====================
        print("\n【迁移 2】更新用户推送频率权限...")

        # 检查当前有多少用户只有 weekly 权限
        cursor.execute("SELECT COUNT(*) FROM user WHERE allowed_frequencies = 'weekly'")
        count_weekly_only = cursor.fetchone()[0]

        print(f"  找到 {count_weekly_only} 个用户当前仅有 weekly 推送权限")

        if count_weekly_only > 0:
            # 更新所有只有 weekly 权限的用户为全部频率
            cursor.execute("""
                UPDATE user
                SET allowed_frequencies = 'daily,weekly,monthly'
                WHERE allowed_frequencies = 'weekly'
            """)
            updated_count = cursor.rowcount
            print(f"  [OK] 已更新 {updated_count} 个用户的推送频率权限")
        else:
            print("  [OK] 所有用户已具有完整的推送频率权限")

        # ==================== 迁移 3: 添加邀请码功能表 ====================
        print("\n【迁移 3】添加邀请码功能表...")

        # 检查 invite_code 表是否已存在
        cursor.execute("""
            SELECT name FROM sqlite_master
            WHERE type='table' AND name='invite_code'
        """)

        if cursor.fetchone():
            print("  [SKIP] invite_code 表已存在，跳过创建")
        else:
            # 创建 invite_code 表
            print("  创建 invite_code 表...")
            cursor.execute("""
                CREATE TABLE invite_code (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    code VARCHAR(50) UNIQUE NOT NULL,
                    created_by INTEGER NOT NULL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    expires_at DATETIME,
                    max_uses INTEGER DEFAULT 1,
                    used_count INTEGER DEFAULT 0,
                    is_active BOOLEAN DEFAULT 1,
                    FOREIGN KEY (created_by) REFERENCES user (id)
                )
            """)
            print("  [OK] invite_code 表创建成功")

        # 检查 invite_code_usage 表是否存在
        cursor.execute("""
            SELECT name FROM sqlite_master
            WHERE type='table' AND name='invite_code_usage'
        """)

        if cursor.fetchone():
            print("  [SKIP] invite_code_usage 表已存在，跳过创建")
        else:
            # 创建 invite_code_usage 表
            print("  创建 invite_code_usage 表...")
            cursor.execute("""
                CREATE TABLE invite_code_usage (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    invite_code_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    used_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (invite_code_id) REFERENCES invite_code (id),
                    FOREIGN KEY (user_id) REFERENCES user (id)
                )
            """)
            print("  [OK] invite_code_usage 表创建成功")

        # 检查并添加系统设置
        cursor.execute("""
            SELECT key FROM system_setting
            WHERE key='require_invite_code'
        """)

        if cursor.fetchone():
            print("  [SKIP] require_invite_code 设置已存在")
        else:
            print("  添加 require_invite_code 系统设置...")
            cursor.execute("""
                INSERT INTO system_setting (key, value, description, category)
                VALUES ('require_invite_code', 'false', '需要邀请码注册', 'system')
            """)
            print("  [OK] require_invite_code 设置添加成功")

        # ==================== 迁移 4: 添加邮箱配置from_email字段 ====================
        print("\n【迁移 4】检查 mail_config 表字段...")

        cursor.execute("PRAGMA table_info(mail_config)")
        mail_columns = [col[1] for col in cursor.fetchall()]

        if 'from_email' not in mail_columns:
            print("  添加 from_email 字段...")
            cursor.execute("ALTER TABLE mail_config ADD COLUMN from_email VARCHAR(120)")
            print("  [OK] from_email 字段已添加")
            print("  说明: 现有配置的from_email为空,将自动使用username作为发件人地址")
        else:
            print("  [OK] from_email 字段已存在")

        # 提交所有更改
        conn.commit()

        # ==================== 验证迁移结果 ====================
        print("\n【验证结果】")

        # 验证 subscription 表字段
        cursor.execute("PRAGMA table_info(subscription)")
        columns = [col[1] for col in cursor.fetchall()]
        print(f"  subscription 表字段数: {len(columns)}")
        print(f"  包含 filter_config: {'filter_config' in columns}")
        print(f"  包含 use_advanced_filter: {'use_advanced_filter' in columns}")

        # 验证用户推送频率分布
        cursor.execute("""
            SELECT allowed_frequencies, COUNT(*)
            FROM user
            GROUP BY allowed_frequencies
        """)
        print("\n  用户推送频率权限分布:")
        for row in cursor.fetchall():
            print(f"    {row[0]}: {row[1]} 个用户")

        # 验证邀请码表
        cursor.execute("""
            SELECT name FROM sqlite_master
            WHERE type='table' AND name IN ('invite_code', 'invite_code_usage')
        """)
        invite_tables = [row[0] for row in cursor.fetchall()]
        print(f"\n  邀请码功能表:")
        print(f"    invite_code: {'invite_code' in invite_tables}")
        print(f"    invite_code_usage: {'invite_code_usage' in invite_tables}")

        # 验证系统设置
        cursor.execute("""
            SELECT value FROM system_setting
            WHERE key='require_invite_code'
        """)
        result = cursor.fetchone()
        if result:
            print(f"\n  邀请码注册设置: {result[0]} (关闭)")

        # 验证邮箱配置字段
        cursor.execute("PRAGMA table_info(mail_config)")
        mail_columns = [col[1] for col in cursor.fetchall()]
        print(f"\n  mail_config 表:")
        print(f"    包含 from_email: {'from_email' in mail_columns}")

        print("\n[OK] 数据库迁移完成！")
        print("\n提示:")
        print("  - 可在管理后台'系统设置'中开启'需要邀请码注册'")
        print("  - 在'邮箱管理'中编辑配置,设置发件人邮箱地址")
        print("  - 发件人地址留空时将自动使用SMTP用户名")

        conn.close()

    except Exception as e:
        print(f"\n[ERROR] 迁移失败: {str(e)}")
        raise

if __name__ == '__main__':
    migrate_database()
