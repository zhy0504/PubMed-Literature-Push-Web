#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
数据库迁移脚本：添加高级筛选器字段
为 subscription 表添加 filter_config 和 use_advanced_filter 字段
"""

import sqlite3
import os

def migrate_database():
    """执行数据库迁移"""
    # 生产环境数据库路径
    db_path = os.path.join(os.path.dirname(__file__), 'pubmed_app.db')

    print(f"正在迁移数据库: {db_path}")

    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # 检查字段是否已存在
        cursor.execute("PRAGMA table_info(subscription)")
        columns = [col[1] for col in cursor.fetchall()]

        print(f"当前字段: {', '.join(columns)}")

        # 添加 filter_config 字段
        if 'filter_config' not in columns:
            print("添加 filter_config 字段...")
            cursor.execute("""
                ALTER TABLE subscription
                ADD COLUMN filter_config TEXT
            """)
            print("✓ filter_config 字段已添加")
        else:
            print("✓ filter_config 字段已存在")

        # 添加 use_advanced_filter 字段
        if 'use_advanced_filter' not in columns:
            print("添加 use_advanced_filter 字段...")
            cursor.execute("""
                ALTER TABLE subscription
                ADD COLUMN use_advanced_filter BOOLEAN DEFAULT 0
            """)
            print("✓ use_advanced_filter 字段已添加")
        else:
            print("✓ use_advanced_filter 字段已存在")

        conn.commit()
        print("\n✅ 数据库迁移完成！")

        # 验证迁移结果
        cursor.execute("PRAGMA table_info(subscription)")
        columns = [col[1] for col in cursor.fetchall()]
        print(f"\n迁移后字段: {', '.join(columns)}")

        conn.close()

    except Exception as e:
        print(f"\n❌ 迁移失败: {str(e)}")
        raise

if __name__ == '__main__':
    migrate_database()
