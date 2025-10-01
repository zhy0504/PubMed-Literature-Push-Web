#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
数据库迁移脚本：统一迁移脚本
1. 为 subscription 表添加 filter_config 和 use_advanced_filter 字段
2. 更新 user 表的 allowed_frequencies 字段（从 'weekly' 更新为 'daily,weekly,monthly'）
"""

import sqlite3
import os

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
            print("  ✓ filter_config 字段已添加")
        else:
            print("  ✓ filter_config 字段已存在")

        # 添加 use_advanced_filter 字段
        if 'use_advanced_filter' not in columns:
            print("  添加 use_advanced_filter 字段...")
            cursor.execute("ALTER TABLE subscription ADD COLUMN use_advanced_filter BOOLEAN DEFAULT 0")
            print("  ✓ use_advanced_filter 字段已添加")
        else:
            print("  ✓ use_advanced_filter 字段已存在")

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
            print(f"  ✓ 已更新 {updated_count} 个用户的推送频率权限")
        else:
            print("  ✓ 所有用户已具有完整的推送频率权限")

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

        print("\n✅ 数据库迁移完成！")
        conn.close()

    except Exception as e:
        print(f"\n❌ 迁移失败: {str(e)}")
        raise

if __name__ == '__main__':
    migrate_database()
