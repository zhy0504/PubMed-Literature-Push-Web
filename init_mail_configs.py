#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
初始化邮箱配置脚本
创建一些示例邮箱配置用于测试多邮箱轮询功能
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# 由于app.py是主文件，我们直接在这里重新创建必要的组件
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
import pytz

# 时区配置 - 与app.py保持一致
DEFAULT_TIMEZONE = 'Asia/Shanghai'
SYSTEM_TIMEZONE = os.environ.get('TZ', DEFAULT_TIMEZONE)

try:
    APP_TIMEZONE = pytz.timezone(SYSTEM_TIMEZONE)
    print(f"使用时区: {SYSTEM_TIMEZONE}")
except Exception as e:
    print(f"时区配置错误 '{SYSTEM_TIMEZONE}': {e}")
    print(f"回退到默认时区: {DEFAULT_TIMEZONE}")
    APP_TIMEZONE = pytz.timezone(DEFAULT_TIMEZONE)
    SYSTEM_TIMEZONE = DEFAULT_TIMEZONE

def get_current_time():
    """获取当前系统时间（使用配置的时区）"""
    return datetime.now(APP_TIMEZONE)

# 创建应用和数据库连接
app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///pubmed_app.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SECRET_KEY'] = 'dev-secret-key'

db = SQLAlchemy(app)

# 邮件配置模型（简化版）
class MailConfig(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    smtp_server = db.Column(db.String(100), nullable=False)
    smtp_port = db.Column(db.Integer, nullable=False, default=465)
    username = db.Column(db.String(100), nullable=False)
    password = db.Column(db.String(200), nullable=False)
    from_email = db.Column(db.String(120), nullable=True)  # 发件人邮箱地址
    use_tls = db.Column(db.Boolean, default=True)
    is_active = db.Column(db.Boolean, default=True)
    daily_limit = db.Column(db.Integer, default=100)
    current_count = db.Column(db.Integer, default=0)
    last_used = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=get_current_time)

def init_mail_configs():
    """初始化邮箱配置"""
    with app.app_context():
        print("=== 初始化邮箱配置 ===")
        
        # 创建表（如果不存在）
        try:
            db.create_all()
            print("OK - 数据库表检查完成")
        except Exception as e:
            print(f"ERROR - 数据库表创建失败: {str(e)}")
            return
        
        # 清除现有配置
        try:
            existing_configs = MailConfig.query.all()
            if existing_configs:
                print(f"清除 {len(existing_configs)} 个现有邮箱配置")
                for config in existing_configs:
                    db.session.delete(config)
                db.session.commit()
        except Exception as e:
            print(f"清除现有配置时出错: {str(e)}")
            # 继续执行，可能是表不存在
        
        # 创建示例邮箱配置
        sample_configs = [
            {
                'name': 'QQ邮箱示例1',
                'smtp_server': 'smtp.qq.com',
                'smtp_port': 465,
                'username': 'your-email1@qq.com',
                'password': 'your-app-password1',
                'use_tls': True,
                'daily_limit': 50,
                'is_active': False  # 默认禁用，需要配置真实邮箱后启用
            },
            {
                'name': 'QQ邮箱示例2',
                'smtp_server': 'smtp.qq.com',
                'smtp_port': 465,
                'username': 'your-email2@qq.com',
                'password': 'your-app-password2',
                'use_tls': True,
                'daily_limit': 50,
                'is_active': False
            },
            {
                'name': 'Gmail示例',
                'smtp_server': 'smtp.gmail.com',
                'smtp_port': 465,
                'username': 'your-email@gmail.com',
                'password': 'your-app-password',
                'use_tls': True,
                'daily_limit': 100,
                'is_active': False
            }
        ]
        
        created_count = 0
        for config_data in sample_configs:
            try:
                config = MailConfig(
                    name=config_data['name'],
                    smtp_server=config_data['smtp_server'],
                    smtp_port=config_data['smtp_port'],
                    username=config_data['username'],
                    password=config_data['password'],
                    use_tls=config_data['use_tls'],
                    daily_limit=config_data['daily_limit'],
                    is_active=config_data['is_active'],
                    current_count=0,
                    created_at=get_current_time()
                )
                
                db.session.add(config)
                created_count += 1
                print(f"OK - 创建邮箱配置: {config_data['name']}")
                
            except Exception as e:
                print(f"ERROR - 创建邮箱配置失败 {config_data['name']}: {str(e)}")
        
        try:
            db.session.commit()
            print(f"\\n成功创建 {created_count} 个邮箱配置示例")
            
            print("\\n=== 使用说明 ===")
            print("1. 登录管理员后台: http://127.0.0.1:5003/admin")
            print("2. 进入邮箱管理页面")
            print("3. 编辑示例配置，填入真实的邮箱信息：")
            print("   - 邮箱地址")
            print("   - 应用专用密码（不是登录密码）")
            print("4. 启用需要使用的邮箱配置")
            print("5. 测试邮箱配置确保能正常发送")
            print("\\n注意：")
            print("- QQ邮箱需要开启SMTP服务并使用授权码")
            print("- Gmail需要使用应用专用密码")
            print("- 其他邮箱请查阅服务商SMTP设置文档")
            
        except Exception as e:
            db.session.rollback()
            print(f"保存配置失败: {str(e)}")

if __name__ == '__main__':
    init_mail_configs()