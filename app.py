
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PubMed Literature Push Web Application - v2.0.0

一个智能的PubMed文献推送系统，支持多邮箱轮询发送
- 用户管理和订阅
- PubMed API集成 
- 多邮箱轮询发送
- 管理员后台
- 定时推送调度
"""

from flask import Flask, render_template_string, request, flash, redirect, url_for, jsonify, send_from_directory, session
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
from datetime import datetime, timedelta
import pytz
import requests
import time
import xml.etree.ElementTree as ET
from urllib.parse import quote
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
import atexit
import os
import csv
import os
import time
import threading
import queue
from datetime import datetime, timedelta

# 加载 .env 文件
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # python-dotenv 未安装，跳过

class JournalDataCache:
    """期刊数据缓存单例类，避免重复加载大量数据"""
    
    _instance = None
    _lock = threading.Lock()
    _initialized = False
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        if not self._initialized:
            with self._lock:
                if not self._initialized:
                    self.jcr_data = {}
                    self.zky_data = {}
                    self.last_loaded = None
                    self.load_timestamp = None
                    self._load_data()
                    JournalDataCache._initialized = True
    
    def _load_data(self):
        """加载期刊质量数据"""
        import os
        try:
            start_time = time.time()
            data_dir = os.path.join(os.path.dirname(__file__), 'data')
            
            # 加载JCR数据
            jcr_file = os.path.join(data_dir, 'jcr_filtered.csv')
            if os.path.exists(jcr_file):
                with open(jcr_file, 'r', encoding='utf-8-sig') as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        issn = row.get('ISSN', '').strip()
                        eissn = row.get('eISSN', '').strip()
                        if issn:
                            self.jcr_data[issn] = {
                                'if': row.get('IF', ''),
                                'quartile': row.get('IF_Quartile', ''),
                                'eissn': eissn
                            }
                        if eissn and eissn != issn:  # 避免重复
                            self.jcr_data[eissn] = {
                                'if': row.get('IF', ''),
                                'quartile': row.get('IF_Quartile', ''),
                                'issn': issn
                            }
            
            # 加载中科院数据
            zky_file = os.path.join(data_dir, 'zky_filtered.csv')
            if os.path.exists(zky_file):
                with open(zky_file, 'r', encoding='utf-8-sig') as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        issn = row.get('ISSN', '').strip()
                        eissn = row.get('eISSN', '').strip()
                        if issn:
                            self.zky_data[issn] = {
                                'category': row.get('大类分区', ''),
                                'top': row.get('Top', ''),
                                'eissn': eissn
                            }
                        if eissn and eissn != issn:  # 避免重复
                            self.zky_data[eissn] = {
                                'category': row.get('大类分区', ''),
                                'top': row.get('Top', ''),
                                'issn': issn
                            }
            
            load_time = time.time() - start_time
            self.last_loaded = datetime.now()
            self.load_timestamp = time.time()
            
            worker_id = os.getpid()
            print(f"[Worker {worker_id}] 期刊数据缓存加载完成: JCR({len(self.jcr_data)}条) + 中科院({len(self.zky_data)}条), 耗时 {load_time:.2f}秒")
            
        except Exception as e:
            print(f"加载期刊数据失败: {str(e)}")
    
    def get_jcr_data(self, issn):
        """获取JCR数据"""
        return self.jcr_data.get(issn, {})
    
    def get_zky_data(self, issn):
        """获取中科院数据"""
        return self.zky_data.get(issn, {})
    
    def get_cache_info(self):
        """获取缓存信息"""
        return {
            'jcr_count': len(self.jcr_data),
            'zky_count': len(self.zky_data),
            'last_loaded': self.last_loaded,
            'load_timestamp': self.load_timestamp
        }
    
    @classmethod
    def reload_data(cls):
        """重新加载数据（用于数据文件更新后）"""
        if cls._instance:
            with cls._lock:
                cls._instance._load_data()

# 创建全局单例实例
journal_cache = JournalDataCache()

import re

# 东八区时区（北京时间）
BEIJING_TZ = pytz.timezone('Asia/Shanghai')

def beijing_now():
    """获取北京时间"""
    return datetime.now(BEIJING_TZ)

def beijing_utcnow():
    """获取北京时间（兼容utcnow格式）"""
    return datetime.now(BEIJING_TZ)

def check_and_process_journal_data():
    """检查并处理期刊数据文件"""
    data_dir = os.path.join(os.path.dirname(__file__), 'data')
    jcr_filtered_path = os.path.join(data_dir, 'jcr_filtered.csv')
    zky_filtered_path = os.path.join(data_dir, 'zky_filtered.csv')
    jcr_source_path = os.path.join(data_dir, 'jcr.csv')
    zky_source_path = os.path.join(data_dir, 'zky.csv')
    
    processed = False
    
    # 检查JCR筛选数据是否存在
    if not os.path.exists(jcr_filtered_path) and os.path.exists(jcr_source_path):
        print("正在处理JCR期刊数据...")
        try:
            process_jcr_data(jcr_source_path, jcr_filtered_path)
            print(f"JCR数据处理完成，保存到: {jcr_filtered_path}")
            processed = True
        except Exception as e:
            print(f"处理JCR数据失败: {str(e)}")
    
    # 检查中科院筛选数据是否存在
    if not os.path.exists(zky_filtered_path) and os.path.exists(zky_source_path):
        print("正在处理中科院期刊数据...")
        try:
            process_zky_data(zky_source_path, zky_filtered_path)
            print(f"中科院数据处理完成，保存到: {zky_filtered_path}")
            processed = True
        except Exception as e:
            print(f"处理中科院数据失败: {str(e)}")
    
    if processed:
        print("期刊数据预处理完成")
    
    return processed

def process_jcr_data(source_path, output_path):
    """处理JCR数据文件"""
    with open(source_path, 'r', encoding='utf-8') as f:
        reader = csv.reader(f)
        headers = next(reader)
        
        # 找到需要的列的索引
        issn_idx = headers.index('ISSN')
        eissn_idx = headers.index('eISSN') 
        if_idx = headers.index('IF(2024)')
        quartile_idx = headers.index('IF Quartile(2024)')
        
        # 提取数据
        jcr_data = []
        for row in reader:
            if len(row) > max(issn_idx, eissn_idx, if_idx, quartile_idx):
                jcr_data.append([
                    row[issn_idx],      # ISSN
                    row[eissn_idx],     # eISSN  
                    row[if_idx],        # IF
                    row[quartile_idx]   # IF_Quartile
                ])
    
    # 保存筛选数据
    with open(output_path, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.writer(f)
        writer.writerow(['ISSN', 'eISSN', 'IF', 'IF_Quartile'])
        writer.writerows(jcr_data)

def process_zky_data(source_path, output_path):
    """处理中科院数据文件"""
    with open(source_path, 'r', encoding='utf-8') as f:
        reader = csv.reader(f)
        headers = next(reader)
        
        # 找到需要的列的索引
        issn_eissn_idx = headers.index('ISSN/EISSN')
        category_idx = headers.index('大类分区')
        top_idx = headers.index('Top')
        
        # 提取和处理数据
        zky_data = []
        for row in reader:
            if len(row) > max(issn_eissn_idx, category_idx, top_idx):
                issn_eissn = row[issn_eissn_idx].strip()
                category = row[category_idx].strip()
                top = row[top_idx].strip()
                
                # 拆分ISSN/EISSN
                issn = ''
                eissn = ''
                if '/' in issn_eissn:
                    parts = issn_eissn.split('/')
                    issn = parts[0].strip()
                    eissn = parts[1].strip() if len(parts) > 1 else ''
                else:
                    issn = issn_eissn
                
                # 提取大类分区的第一个数字
                category_num = ''
                if category:
                    match = re.search(r'\d+', category)
                    if match:
                        category_num = match.group()
                
                zky_data.append([issn, eissn, category_num, top])
    
    # 保存筛选数据
    with open(output_path, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.writer(f)
        writer.writerow(['ISSN', 'eISSN', '大类分区', 'Top'])
        writer.writerows(zky_data)

# 配置类
class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'dev-secret-key-change-in-production'
    # 修复数据库路径：确保使用绝对路径
    db_url = os.environ.get('DATABASE_URL')
    if not db_url:
        db_url = f'sqlite:///{os.path.abspath("pubmed_app.db")}'
    # 如果是相对路径的 sqlite URL，转换为绝对路径
    elif db_url.startswith('sqlite:///') and not db_url.startswith('sqlite:////'):
        # sqlite:///pubmed_app.db -> sqlite:////app/pubmed_app.db
        db_path = db_url.replace('sqlite:///', '')
        if not os.path.isabs(db_path):
            db_url = f'sqlite:///{os.path.abspath(db_path)}'
    SQLALCHEMY_DATABASE_URI = db_url
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    
    # PubMed API配置
    PUBMED_BASE_URL = 'https://eutils.ncbi.nlm.nih.gov/entrez/eutils/'
    PUBMED_API_KEY = os.environ.get('PUBMED_API_KEY')  # 可选
    
    # AI功能加密密钥
    ENCRYPTION_KEY = os.environ.get('ENCRYPTION_KEY') or None
    
    # 邮件配置（现在使用多邮箱管理，这些作为默认值）
    MAIL_SERVER = os.environ.get('MAIL_SERVER') or 'smtp.gmail.com'
    MAIL_PORT = int(os.environ.get('MAIL_PORT') or 587)
    MAIL_USE_TLS = os.environ.get('MAIL_USE_TLS', 'true').lower() in ['true', '1', 'yes']
    MAIL_USERNAME = os.environ.get('MAIL_USERNAME') or ''
    MAIL_PASSWORD = os.environ.get('MAIL_PASSWORD') or ''

# 管理员权限装饰器
def admin_required(f):
    """管理员权限装饰器"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated:
            flash('请先登录')
            return redirect(url_for('login'))
        
        if not current_user.is_administrator():
            flash('需要管理员权限')
            return redirect(url_for('index'))
        
        return f(*args, **kwargs)
    return decorated_function

def is_admin():
    """检查当前用户是否为管理员"""
    return current_user.is_authenticated and current_user.is_administrator()

def toggle_user_status(user_id):
    """切换用户激活状态"""
    try:
        user = User.query.get(user_id)
        if user:
            user.is_active = not user.is_active
            db.session.commit()
            return True
        return False
    except Exception as e:
        db.session.rollback()
        return False

# 创建应用（禁用 instance 文件夹）
app = Flask(__name__, instance_path='/tmp/instance')
app.config.from_object(Config)

# 配置日志
import logging
from logging.handlers import RotatingFileHandler

# 从环境变量获取日志级别和文件路径
log_level_name = os.environ.get('LOG_LEVEL', 'INFO').upper()
log_file = os.environ.get('LOG_FILE', '/app/logs/app.log')

# 设置日志级别
log_level = getattr(logging, log_level_name, logging.INFO)
app.logger.setLevel(log_level)

# 配置日志文件处理器
if log_file:
    try:
        # 确保日志目录存在
        log_dir = os.path.dirname(log_file)
        if log_dir and not os.path.exists(log_dir):
            os.makedirs(log_dir, exist_ok=True)
        
        # 创建文件处理器（10MB 轮转，保留 5 个备份）
        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=10 * 1024 * 1024,  # 10MB
            backupCount=5,
            encoding='utf-8'
        )
        file_handler.setLevel(log_level)
        
        # 设置日志格式
        formatter = logging.Formatter(
            '[%(asctime)s] %(levelname)s in %(module)s: %(message)s'
        )
        file_handler.setFormatter(formatter)
        
        # 添加到 app.logger
        app.logger.addHandler(file_handler)
        import os
        worker_id = os.getpid()
        app.logger.info(f"[Worker {worker_id}] 应用启动，日志级别: {log_level_name}, 日志文件: {log_file}")
    except PermissionError:
        # 如果无法写入日志文件，只使用控制台输出
        print(f"[警告] 无权限写入日志文件: {log_file}，仅使用控制台输出")
    except Exception as e:
        print(f"[警告] 日志文件配置失败: {e}，仅使用控制台输出")

# 初始化扩展
db = SQLAlchemy(app)
migrate = Migrate(app, db)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

# 用户模型
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(128))
    is_admin = db.Column(db.Boolean, default=False)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=beijing_utcnow)
    
    # 推送相关字段
    push_method = db.Column(db.String(20), default='email')  # email, wechat, both
    push_time = db.Column(db.String(5), default='09:00')
    push_frequency = db.Column(db.String(10), default='daily')  # daily, weekly, monthly
    push_day = db.Column(db.String(10), default='monday')  # for weekly
    push_month_day = db.Column(db.Integer, default=1)  # for monthly
    max_articles = db.Column(db.Integer, default=10)
    last_push = db.Column(db.DateTime)
    
    # 订阅权限控制字段
    max_subscriptions = db.Column(db.Integer, default=3)  # 最大订阅数量
    allowed_frequencies = db.Column(db.Text, default='weekly')  # 允许的推送频率，逗号分隔
    
    def set_password(self, password):
        self.password_hash = generate_password_hash(password)
    
    def check_password(self, password):
        return check_password_hash(self.password_hash, password)
    
    def get_allowed_frequencies(self):
        """获取用户允许的推送频率列表"""
        if not self.allowed_frequencies:
            return ['weekly']  # 默认只允许每周
        return [freq.strip() for freq in self.allowed_frequencies.split(',') if freq.strip()]
    
    def set_allowed_frequencies(self, frequencies):
        """设置用户允许的推送频率"""
        if isinstance(frequencies, list):
            self.allowed_frequencies = ','.join(frequencies)
        else:
            self.allowed_frequencies = frequencies
    
    def can_create_subscription(self):
        """检查用户是否可以创建新订阅"""
        current_count = Subscription.query.filter_by(user_id=self.id).count()
        return current_count < self.max_subscriptions
    
    def get_subscription_limit_info(self):
        """获取订阅限制信息"""
        current_count = Subscription.query.filter_by(user_id=self.id).count()
        return {
            'current': current_count,
            'max': self.max_subscriptions,
            'remaining': self.max_subscriptions - current_count,
            'can_create': current_count < self.max_subscriptions
        }
    
    def generate_reset_token(self):
        """生成密码重置令牌"""
        import secrets
        from datetime import timedelta
        token = secrets.token_urlsafe(32)
        expires_at = beijing_now() + timedelta(hours=1)  # 1小时过期
        
        # 删除该用户所有未使用的旧令牌
        PasswordResetToken.query.filter_by(user_id=self.id, used=False).delete()
        
        # 创建新令牌
        reset_token = PasswordResetToken(
            user_id=self.id,
            token=token,
            expires_at=expires_at
        )
        db.session.add(reset_token)
        db.session.commit()
        return token
    
    @staticmethod
    def verify_reset_token(token):
        """验证密码重置令牌"""
        reset_token = PasswordResetToken.query.filter_by(token=token, used=False).first()
        if reset_token and not reset_token.is_expired():
            return reset_token.user
        return None
    
    def is_administrator(self):
        """检查用户是否为管理员"""
        return self.is_admin

# 订阅模型
class Subscription(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    keywords = db.Column(db.String(500), nullable=False)
    created_at = db.Column(db.DateTime, default=beijing_utcnow)
    is_active = db.Column(db.Boolean, default=True)
    last_search = db.Column(db.DateTime)
    
    # 推送参数设置
    max_results = db.Column(db.Integer, default=10000)  # 每次搜索的最大结果数
    days_back = db.Column(db.Integer, default=30)     # 搜索过去N天的文章
    
    # 期刊质量筛选参数
    exclude_no_issn = db.Column(db.Boolean, default=True)  # 排除没有ISSN的文献
    
    # JCR筛选参数
    jcr_quartiles = db.Column(db.Text)  # JSON格式存储，如 ["Q1", "Q2"]
    min_impact_factor = db.Column(db.Float)  # 最小影响因子
    
    # 中科院筛选参数  
    cas_categories = db.Column(db.Text)  # JSON格式存储，如 ["1", "2"]
    cas_top_only = db.Column(db.Boolean, default=False)  # 只要Top期刊
    
    # 推送频率设置
    push_frequency = db.Column(db.String(20), default='daily')  # daily, weekly, monthly
    push_time = db.Column(db.String(5), default='09:00')  # 推送时间 HH:MM
    push_day = db.Column(db.String(10), default='monday')  # 每周推送的星期几
    push_month_day = db.Column(db.Integer, default=1)  # 每月推送的日期
    
    user = db.relationship('User', backref='subscriptions')
    
    def get_jcr_quartiles(self):
        """获取JCR分区列表"""
        if self.jcr_quartiles:
            try:
                import json
                return json.loads(self.jcr_quartiles)
            except:
                return []
        return []
    
    def set_jcr_quartiles(self, quartiles):
        """设置JCR分区列表"""
        if quartiles:
            import json
            self.jcr_quartiles = json.dumps(quartiles)
        else:
            self.jcr_quartiles = None
    
    def get_cas_categories(self):
        """获取中科院分区列表"""
        if self.cas_categories:
            try:
                import json
                return json.loads(self.cas_categories)
            except:
                return []
        return []
    
    def set_cas_categories(self, categories):
        """设置中科院分区列表"""
        if categories:
            import json
            self.cas_categories = json.dumps(categories)
        else:
            self.cas_categories = None
    
    def get_filter_params(self):
        """获取搜索筛选参数"""
        # JCR筛选参数
        jcr_filter = None
        jcr_quartiles = self.get_jcr_quartiles()
        if jcr_quartiles or self.min_impact_factor:
            jcr_filter = {}
            if jcr_quartiles:
                jcr_filter['quartile'] = jcr_quartiles
            if self.min_impact_factor:
                jcr_filter['min_if'] = self.min_impact_factor
        
        # 中科院筛选参数
        zky_filter = None
        cas_categories = self.get_cas_categories()
        if cas_categories or self.cas_top_only:
            zky_filter = {}
            if cas_categories:
                zky_filter['category'] = cas_categories
            if self.cas_top_only:
                zky_filter['top'] = True
        
        return {
            'max_results': self.max_results,
            'days_back': self.days_back,
            'jcr_filter': jcr_filter,
            'zky_filter': zky_filter,
            'exclude_no_issn': self.exclude_no_issn
        }

# 文章模型
class Article(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    pmid = db.Column(db.String(20), unique=True, nullable=False)
    title = db.Column(db.Text, nullable=False)
    authors = db.Column(db.Text)
    journal = db.Column(db.String(200))
    publish_date = db.Column(db.DateTime)
    abstract = db.Column(db.Text)
    doi = db.Column(db.String(100))
    pubmed_url = db.Column(db.String(200))
    keywords = db.Column(db.Text)
    issn = db.Column(db.String(20))  # 添加ISSN字段
    eissn = db.Column(db.String(20))  # 添加电子ISSN字段
    created_at = db.Column(db.DateTime, default=beijing_utcnow)

# 用户文章关联模型
class UserArticle(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    article_id = db.Column(db.Integer, db.ForeignKey('article.id'), nullable=False)
    subscription_id = db.Column(db.Integer, db.ForeignKey('subscription.id'), nullable=True)  # 允许为空
    push_date = db.Column(db.DateTime, default=datetime.utcnow)
    is_read = db.Column(db.Boolean, default=False)
    
    user = db.relationship('User', backref='user_articles')
    article = db.relationship('Article', backref='user_articles')
    subscription = db.relationship('Subscription', backref='matched_articles')

# 系统日志模型
class SystemLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    timestamp = db.Column(db.DateTime, default=beijing_utcnow)
    level = db.Column(db.String(10), nullable=False)  # INFO, WARNING, ERROR
    module = db.Column(db.String(50), nullable=False)
    message = db.Column(db.String(500), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    ip_address = db.Column(db.String(45), nullable=True)
    
    user = db.relationship('User', backref='logs')

# 密码重置令牌模型
class PasswordResetToken(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    token = db.Column(db.String(100), unique=True, nullable=False)
    created_at = db.Column(db.DateTime, default=beijing_utcnow)
    expires_at = db.Column(db.DateTime, nullable=False)
    used = db.Column(db.Boolean, default=False)
    
    user = db.relationship('User', backref='password_reset_tokens')
    
    def is_expired(self):
        return beijing_now() > self.expires_at
    
    def mark_as_used(self):
        self.used = True
        db.session.commit()

# 系统设置模型
class SystemSetting(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(100), unique=True, nullable=False)
    value = db.Column(db.Text, nullable=False)
    description = db.Column(db.String(200), nullable=True)
    category = db.Column(db.String(50), nullable=False, default='general')
    updated_at = db.Column(db.DateTime, default=beijing_utcnow, onupdate=beijing_utcnow)
    
    @staticmethod
    def get_setting(key, default=None):
        """获取系统设置"""
        setting = SystemSetting.query.filter_by(key=key).first()
        return setting.value if setting else default
    
    @staticmethod
    def set_setting(key, value, description=None, category='general'):
        """设置系统配置"""
        setting = SystemSetting.query.filter_by(key=key).first()
        if setting:
            setting.value = str(value)
            setting.updated_at = beijing_now()
        else:
            setting = SystemSetting(
                key=key,
                value=str(value),
                description=description,
                category=category
            )
            db.session.add(setting)
        db.session.commit()
        return setting

# 邮件配置模型
class MailConfig(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)  # 配置名称
    smtp_server = db.Column(db.String(100), nullable=False)
    smtp_port = db.Column(db.Integer, nullable=False, default=465)
    username = db.Column(db.String(100), nullable=False)
    password = db.Column(db.String(200), nullable=False)
    use_tls = db.Column(db.Boolean, default=True)
    is_active = db.Column(db.Boolean, default=True)
    daily_limit = db.Column(db.Integer, default=100)  # 每日发送限制
    current_count = db.Column(db.Integer, default=0)  # 今日已发送数量
    last_used = db.Column(db.DateTime)  # 最后使用时间
    created_at = db.Column(db.DateTime, default=beijing_utcnow)
    
    def can_send(self):
        """检查是否可以发送邮件"""
        if not self.is_active:
            return False
        
        # 检查今日发送量
        today = beijing_now().date()
        if self.last_used and self.last_used.date() == today:
            return self.current_count < self.daily_limit
        return True
    
    def reset_daily_count(self):
        """重置今日计数"""
        today = beijing_now().date()
        if self.last_used and self.last_used.date() != today:
            self.current_count = 0
    
    def increment_count(self):
        """增加发送计数"""
        self.reset_daily_count()
        self.current_count += 1
        self.last_used = beijing_now()
        db.session.commit()

# AI配置模型
class AISetting(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    provider_name = db.Column(db.String(50), nullable=False)  # AI提供商名称
    base_url = db.Column(db.String(200), nullable=False)  # API接入点
    api_key = db.Column(db.Text, nullable=False)  # API密钥(加密存储)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=beijing_utcnow)
    updated_at = db.Column(db.DateTime, default=beijing_utcnow, onupdate=beijing_utcnow)
    
    # 关联关系
    models = db.relationship('AIModel', backref='provider', lazy=True, cascade='all, delete-orphan')

    def get_decrypted_api_key(self):
        """获取解密后的API密钥"""
        try:
            from cryptography.fernet import Fernet
            key = app.config.get('ENCRYPTION_KEY')
            if not key:
                return self.api_key  # 如果没有加密密钥，返回原文
            f = Fernet(key)
            return f.decrypt(self.api_key.encode()).decode()
        except:
            return self.api_key  # 解密失败，可能是未加密的数据
    
    def set_encrypted_api_key(self, api_key):
        """设置加密的API密钥"""
        try:
            from cryptography.fernet import Fernet
            key = app.config.get('ENCRYPTION_KEY')
            if not key:
                self.api_key = api_key  # 如果没有加密密钥，存储原文
                return
            f = Fernet(key)
            self.api_key = f.encrypt(api_key.encode()).decode()
        except:
            self.api_key = api_key  # 加密失败，存储原文

# AI模型表
class AIModel(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    provider_id = db.Column(db.Integer, db.ForeignKey('ai_setting.id'), nullable=False)
    model_name = db.Column(db.String(100), nullable=False)  # 显示名称
    model_id = db.Column(db.String(100), nullable=False)  # API标识符
    model_type = db.Column(db.String(20), nullable=False)  # query_builder, translator, general
    is_available = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=beijing_utcnow)

# AI提示词模板表
class AIPromptTemplate(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    template_type = db.Column(db.String(20), nullable=False)  # query_builder, translator
    prompt_content = db.Column(db.Text, nullable=False)
    is_default = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=beijing_utcnow)
    updated_at = db.Column(db.DateTime, default=beijing_utcnow, onupdate=beijing_utcnow)

    @staticmethod
    def get_default_prompt(template_type):
        """获取默认提示词"""
        template = AIPromptTemplate.query.filter_by(
            template_type=template_type, 
            is_default=True
        ).first()
        return template.prompt_content if template else None

# 邮件发送器类 - 支持多邮箱轮询
class MailSender:
    def __init__(self):
        self.current_config = None
        
    def get_available_mail_config(self):
        """获取可用的邮件配置（轮询策略）"""
        # 获取所有活跃的邮件配置，按最后使用时间排序
        configs = MailConfig.query.filter_by(is_active=True).order_by(
            MailConfig.last_used.asc().nullsfirst()
        ).all()
        
        # 重置过期的计数器
        for config in configs:
            config.reset_daily_count()
        
        # 找到可用的配置
        for config in configs:
            if config.can_send():
                return config
        
        return None
    
    def send_email(self, to_email, subject, html_body, text_body=None):
        """发送邮件，自动选择可用的邮箱配置"""
        config = self.get_available_mail_config()
        
        if not config:
            log_activity('ERROR', 'mail', '没有可用的邮件配置')
            return False
        
        try:
            from flask_mail import Message, Mail
            
            # 创建临时邮件配置
            app.config['MAIL_SERVER'] = config.smtp_server
            app.config['MAIL_PORT'] = config.smtp_port
            app.config['MAIL_USERNAME'] = config.username
            app.config['MAIL_PASSWORD'] = config.password
            
            # 根据端口设置正确的加密方式
            if config.smtp_port == 465:
                # 465端口使用SSL，不使用TLS
                app.config['MAIL_USE_SSL'] = True
                app.config['MAIL_USE_TLS'] = False
            elif config.smtp_port == 587:
                # 587端口使用TLS，不使用SSL
                app.config['MAIL_USE_SSL'] = False
                app.config['MAIL_USE_TLS'] = True
            else:
                # 其他端口按配置设置
                app.config['MAIL_USE_TLS'] = config.use_tls
                app.config['MAIL_USE_SSL'] = False
            
            # 初始化邮件对象
            mail = Mail(app)
            
            # 创建邮件消息
            msg = Message(
                subject=subject,
                sender=config.username,
                recipients=[to_email]
            )
            msg.html = html_body
            if text_body:
                msg.body = text_body
            
            # 发送邮件
            mail.send(msg)
            
            # 增加使用计数
            config.increment_count()
            
            log_activity('INFO', 'mail', f'邮件发送成功: {to_email} via {config.name}')
            return True
            
        except Exception as e:
            log_activity('ERROR', 'mail', f'邮件发送失败: {to_email} via {config.name} - {str(e)}')
            return False
    
    def get_mail_stats(self):
        """获取邮箱使用统计"""
        configs = MailConfig.query.filter_by(is_active=True).all()
        stats = []
        
        for config in configs:
            stats.append({
                'id': config.id,
                'name': config.name,
                'username': config.username,
                'daily_limit': config.daily_limit,
                'current_count': config.current_count,
                'available': config.can_send(),
                'last_used': config.last_used
            })
        
        return stats

# 全局邮件发送器实例
mail_sender = MailSender()

# 日志记录函数
def log_activity(level, module, message, user_id=None, ip_address=None):
    """记录系统活动日志"""
    def _log_to_db():
        log_entry = SystemLog(
            level=level,
            module=module,
            message=message,
            user_id=user_id,
            ip_address=ip_address
        )
        db.session.add(log_entry)
        db.session.commit()
    
    try:
        # 尝试直接记录（如果在应用上下文中）
        _log_to_db()
    except Exception as e:
        # 检查是否是应用上下文错误
        error_msg = str(e).lower()
        if "application context" in error_msg or "outside of application context" in error_msg:
            # 没有应用上下文，创建一个
            try:
                with app.app_context():
                    _log_to_db()
            except Exception as inner_e:
                print(f"日志记录失败: {inner_e}")
        else:
            print(f"日志记录失败: {e}")

# 简化的推送服务类
class SimpleLiteraturePushService:
    def __init__(self):
        self.mail_sender = mail_sender  # 使用全局邮件发送器实例
        
    def process_user_subscriptions(self, user_id=None):
        """处理用户订阅，搜索并推送新文章"""
        if user_id:
            users = [User.query.get(user_id)]
        else:
            users = User.query.filter_by(is_active=True).all()
        
        results = []
        
        for user in users:
            if not user:
                continue
                
            try:
                user_result = self._process_single_user(user)
                results.append(user_result)
            except Exception as e:
                log_activity('ERROR', 'push', f'处理用户 {user.email} 订阅失败: {str(e)}')
                results.append({
                    'user_id': user.id,
                    'user_email': user.email,
                    'success': False,
                    'error': str(e)
                })
        
        return results
    
    def _process_single_user(self, user):
        """处理单个用户的订阅"""
        subscriptions = Subscription.query.filter_by(user_id=user.id, is_active=True).all()
        
        if not subscriptions:
            return {
                'user_id': user.id,
                'user_email': user.email,
                'success': True,
                'message': 'No active subscriptions',
                'articles_found': 0
            }
        
        all_new_articles = []
        articles_by_subscription = {}  # 按订阅分组的文章
        
        for subscription in subscriptions:
            try:
                # 使用订阅的个人参数设置
                filter_params = subscription.get_filter_params()
                
                # 搜索新文章
                api = PubMedAPI()
                
                # 直接获取文章详细信息（避免重复调用AI检索式生成）
                fetch_result = api.search_and_fetch_with_filter(
                    keywords=subscription.keywords,
                    max_results=min(filter_params['max_results'], int(SystemSetting.get_setting('push_max_articles', '10'))),
                    days_back=filter_params['days_back'],
                    jcr_filter=filter_params['jcr_filter'],
                    zky_filter=filter_params['zky_filter'],
                    exclude_no_issn=filter_params['exclude_no_issn'],
                    user_email=user.email
                )
                
                # 检查是否有符合条件的文章
                if fetch_result.get('filtered_count', 0) > 0:
                    
                    # 过滤已推送的文章并保存新文章
                    new_articles = []
                    for article_data in fetch_result.get('articles', []):
                        # 检查文章是否已存在
                        existing_article = Article.query.filter_by(pmid=article_data['pmid']).first()
                        
                        if not existing_article:
                            # 保存新文章
                            article = Article(
                                pmid=article_data['pmid'],
                                title=article_data['title'],
                                authors=article_data['authors'],
                                journal=article_data['journal'],
                                pubmed_url=article_data['url'],
                                abstract=article_data.get('abstract', ''),
                                issn=article_data.get('issn', ''),
                                eissn=article_data.get('eissn', ''),
                            )
                            db.session.add(article)
                            db.session.flush()
                        else:
                            # 使用已存在的文章，但更新ISSN信息（如果之前没有）
                            article = existing_article
                            
                            # 检查并更新ISSN信息
                            updated = False
                            if not article.issn and article_data.get('issn'):
                                article.issn = article_data.get('issn')
                                updated = True
                            if not article.eissn and article_data.get('eissn'):
                                article.eissn = article_data.get('eissn')
                                updated = True
                            
                            if updated:
                                db.session.flush()
                                log_activity('INFO', 'push', f'更新文章 {article.pmid} 的ISSN信息')
                        
                        # 检查用户是否已收到此文章推送
                        existing_user_article = UserArticle.query.filter_by(
                            user_id=user.id, article_id=article.id
                        ).first()
                        
                        if not existing_user_article:
                            # 重新检查ISSN筛选条件（基于最新的文章数据）
                            if filter_params['exclude_no_issn']:
                                has_issn = bool(article.issn or article.eissn)
                                if not has_issn:
                                    log_activity('INFO', 'push', f'跳过无ISSN文章: {article.pmid}')
                                    continue
                            
                            # 创建用户-文章关联
                            user_article = UserArticle(
                                user_id=user.id,
                                article_id=article.id,
                                subscription_id=subscription.id
                            )
                            db.session.add(user_article)
                            new_articles.append(article)
                    
                    # 如果这个订阅有新文章，记录到分组中
                    if new_articles:
                        articles_by_subscription[subscription.keywords] = new_articles
                        all_new_articles.extend(new_articles)
                
                # 更新订阅的最后搜索时间
                subscription.last_search = beijing_now()
                
            except Exception as e:
                log_activity('ERROR', 'push', f'处理订阅 {subscription.id} 失败: {str(e)}')
                continue
        
        db.session.commit()
        
        # 为每个有新文章的订阅单独发送邮件
        total_sent_articles = 0
        emails_sent = 0
        
        for keywords, articles in articles_by_subscription.items():
            if articles:  # 只为有新文章的订阅发送邮件
                # 使用AI翻译摘要（如果启用）
                if SystemSetting.get_setting('ai_translation_enabled', 'false') == 'true':
                    try:
                        log_activity('INFO', 'push', f'开始为用户 {user.email} 的关键词 "{keywords}" 的 {len(articles)} 篇文章进行AI翻译')
                        ai_service.batch_translate_abstracts(articles)
                        log_activity('INFO', 'push', f'用户 {user.email} 关键词 "{keywords}" 的文章AI翻译完成')
                    except Exception as e:
                        log_activity('WARNING', 'push', f'用户 {user.email} 关键词 "{keywords}" 的AI翻译失败: {str(e)}')
                
                # 为这个关键词单独发送邮件
                single_subscription_data = {keywords: articles}
                self._send_email_notification(user, articles, single_subscription_data)
                
                total_sent_articles += len(articles)
                emails_sent += 1
                
                log_activity('INFO', 'push', f'为用户 {user.email} 的关键词 "{keywords}" 推送了 {len(articles)} 篇新文章')
        
        # 更新用户最后推送时间
        if total_sent_articles > 0:
            user.last_push = beijing_now()
            db.session.commit()
            
            log_activity('INFO', 'push', f'为用户 {user.email} 总共发送了 {emails_sent} 封邮件，推送了 {total_sent_articles} 篇新文章')
        
        # 检查并清理过多的文章
        try:
            self._cleanup_old_articles_if_needed()
        except Exception as e:
            log_activity('WARNING', 'system', f'文章自动清理失败: {str(e)}')
        
        return {
            'user_id': user.id,
            'user_email': user.email,
            'success': True,
            'articles_found': total_sent_articles,
            'emails_sent': emails_sent,
            'message': f'Sent {emails_sent} emails with {total_sent_articles} new articles'
        }
    
    def _cleanup_old_articles_if_needed(self):
        """检查文章数量，超过1000篇时清理最早的100篇"""
        try:
            total_articles = Article.query.count()
            max_articles = int(SystemSetting.get_setting('max_articles_limit', '1000'))
            cleanup_count = int(SystemSetting.get_setting('cleanup_articles_count', '100'))
            
            if total_articles > max_articles:
                # 获取最早的文章（按ID排序，ID越小越早）
                oldest_articles = Article.query.order_by(Article.id.asc()).limit(cleanup_count).all()
                
                if oldest_articles:
                    # 删除这些文章对应的UserArticle记录
                    article_ids = [article.id for article in oldest_articles]
                    UserArticle.query.filter(UserArticle.article_id.in_(article_ids)).delete(synchronize_session=False)
                    
                    # 删除文章本身
                    Article.query.filter(Article.id.in_(article_ids)).delete(synchronize_session=False)
                    
                    db.session.commit()
                    
                    log_activity('INFO', 'system', 
                               f'自动清理完成：删除了{len(oldest_articles)}篇最早的文章，当前文章总数：{total_articles - len(oldest_articles)}')
                    
                    app.logger.info(f"文章自动清理: 删除了{len(oldest_articles)}篇文章，剩余{total_articles - len(oldest_articles)}篇")
                    
        except Exception as e:
            app.logger.error(f"文章自动清理失败: {str(e)}")
            raise
    
    def _send_email_notification(self, user, articles, articles_by_subscription=None):
        """发送邮件通知 - 现在只处理单个订阅"""
        try:
            # 生成邮件主题，包含关键词信息
            if articles_by_subscription and len(articles_by_subscription) == 1:
                # 获取关键词（现在总是只有一个）
                keywords = list(articles_by_subscription.keys())[0]
                subject = f"{keywords}文献推送-您有{len(articles)}篇新文献"
            else:
                # 备用格式
                subject = f"PubMed文献推送-您有{len(articles)}篇新文献"
            
            # 生成邮件内容
            html_body = self._generate_email_html(user, articles, articles_by_subscription)
            text_body = self._generate_email_text(user, articles, articles_by_subscription)
            
            # 使用MailSender发送邮件
            success = self.mail_sender.send_email(user.email, subject, html_body, text_body)
            
            if success:
                log_activity('INFO', 'push', f'邮件推送成功: {user.email}, {len(articles)} 篇文章')
            else:
                log_activity('ERROR', 'push', f'邮件推送失败: {user.email}')
                
        except Exception as e:
            log_activity('ERROR', 'push', f'邮件推送异常: {user.email}, {e}')
    
    def _generate_email_html(self, user, articles, articles_by_subscription=None):
        """生成邮件HTML内容 - 现在只处理单个订阅"""
        
        # 生成开头文案，包含关键词信息
        if articles_by_subscription and len(articles_by_subscription) == 1:
            # 获取关键词（现在总是只有一个）
            keywords = list(articles_by_subscription.keys())[0]
            greeting_text = f"您设置的<strong>{keywords}</strong>主题词，我们为您找到了以下最新的学术文献："
        else:
            # 备用格式
            greeting_text = "我们为您找到了以下最新的学术文献："
        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>PubMed文献推送</title>
            <style>
                /* 基础样式 */
                * {{ box-sizing: border-box; }}
                body {{ 
                    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Arial, sans-serif; 
                    margin: 0; 
                    padding: 15px; 
                    background-color: #f8f9fa; 
                    line-height: 1.5;
                    color: #212529;
                }}
                
                /* 容器样式 */
                .container {{ 
                    max-width: 800px; 
                    margin: 0 auto; 
                    background-color: white; 
                    border-radius: 12px; 
                    box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);
                    overflow: hidden;
                }}
                
                /* 头部样式 */
                .header {{ 
                    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); 
                    color: white; 
                    padding: 30px 20px; 
                    text-align: center; 
                }}
                .header h1 {{ 
                    margin: 0 0 10px 0; 
                    font-size: 28px; 
                    font-weight: 600; 
                }}
                .header p {{ 
                    margin: 0; 
                    font-size: 16px; 
                    opacity: 0.9; 
                }}
                
                /* 内容区域 */
                .content {{ 
                    padding: 30px 20px; 
                }}
                .greeting {{ 
                    font-size: 16px; 
                    margin-bottom: 25px; 
                    color: #495057; 
                }}
                
                /* 文章样式 */
                .article {{ 
                    border: 1px solid #e9ecef; 
                    border-radius: 8px; 
                    padding: 20px; 
                    margin-bottom: 20px; 
                    background-color: #fff;
                    box-shadow: 0 2px 4px rgba(0, 0, 0, 0.05);
                }}
                .article:last-child {{ 
                    margin-bottom: 0; 
                }}
                
                /* 序号和标题 */
                .article-header {{ 
                    display: flex; 
                    align-items: flex-start; 
                    margin-bottom: 15px; 
                }}
                .article-number {{ 
                    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); 
                    color: white; 
                    font-weight: bold; 
                    padding: 8px 12px; 
                    border-radius: 20px; 
                    min-width: 35px; 
                    text-align: center; 
                    margin-right: 15px; 
                    flex-shrink: 0;
                    font-size: 14px;
                }}
                .title {{ 
                    font-size: 18px; 
                    font-weight: 600; 
                    color: #2c3e50; 
                    margin: 0; 
                    line-height: 1.4;
                }}
                .title a {{ 
                    color: #2c3e50; 
                    text-decoration: none; 
                }}
                .title a:hover {{ 
                    color: #667eea; 
                }}
                
                /* 期刊信息 */
                .journal-info {{ 
                    margin: 15px 0; 
                    padding: 15px; 
                    background-color: #f8f9fa; 
                    border-radius: 6px;
                }}
                .journal-name {{ 
                    font-weight: 600; 
                    color: #495057; 
                    font-size: 15px; 
                    margin-bottom: 8px; 
                }}
                
                /* 质量标签 */
                .quality-badges {{ 
                    margin: 10px 0; 
                }}
                .quality-badge {{ 
                    display: inline-block; 
                    padding: 6px 12px; 
                    border-radius: 20px; 
                    font-size: 12px; 
                    font-weight: 600; 
                    margin: 2px 5px 2px 0; 
                    white-space: nowrap;
                }}
                .jcr-quartile {{ 
                    background-color: #e3f2fd; 
                    color: #1565c0; 
                }}
                .impact-factor {{ 
                    background-color: #f3e5f5; 
                    color: #7b1fa2; 
                }}
                .cas-category {{ 
                    background-color: #e8f5e8; 
                    color: #2e7d32; 
                }}
                .top-journal {{ 
                    background-color: #fff3e0; 
                    color: #f57c00; 
                    border: 1px solid #ffcc02; 
                }}
                
                /* 摘要样式 */
                .abstract-section {{ 
                    margin: 20px 0; 
                }}
                .abstract-title {{ 
                    font-weight: 600; 
                    color: #495057; 
                    font-size: 14px; 
                    margin-bottom: 8px; 
                    border-left: 4px solid #667eea; 
                    padding-left: 10px;
                }}
                .abstract-content {{ 
                    color: #6c757d; 
                    font-size: 14px; 
                    line-height: 1.6; 
                    padding: 12px; 
                    background-color: #f8f9fa; 
                    border-radius: 6px; 
                    border: 1px solid #e9ecef;
                }}
                .chinese-abstract {{ 
                    background-color: #fff8e1; 
                    border: 1px solid #ffecb3; 
                }}
                
                /* 底部样式 */
                .footer {{ 
                    text-align: center; 
                    padding: 30px 20px; 
                    background-color: #f8f9fa; 
                    color: #6c757d; 
                    font-size: 13px; 
                    line-height: 1.5;
                }}
                .footer p {{ 
                    margin: 5px 0; 
                }}
                
                /* 移动端适配 */
                @media only screen and (max-width: 600px) {{
                    body {{ padding: 10px; }}
                    .container {{ border-radius: 8px; }}
                    .header {{ padding: 20px 15px; }}
                    .header h1 {{ font-size: 24px; }}
                    .content {{ padding: 20px 15px; }}
                    .article {{ padding: 15px; }}
                    .article-header {{ flex-direction: column; align-items: flex-start; }}
                    .article-number {{ margin-bottom: 10px; margin-right: 0; }}
                    .title {{ font-size: 16px; }}
                    .quality-badge {{ margin: 2px 3px 2px 0; font-size: 11px; padding: 4px 8px; }}
                    .abstract-content {{ font-size: 13px; padding: 10px; }}
                }}
                
                /* 超小屏幕适配 */
                @media only screen and (max-width: 480px) {{
                    body {{ padding: 5px; }}
                    .header {{ padding: 15px 10px; }}
                    .header h1 {{ font-size: 22px; }}
                    .content {{ padding: 15px 10px; }}
                    .article {{ padding: 12px; }}
                    .title {{ font-size: 15px; }}
                }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h1>📚 PubMed 文献推送</h1>
                    <p>为您推送 {len(articles)} 篇最新文献</p>
                </div>
                
                <div class="content">
                    <div class="greeting">
                        <p>亲爱的用户，</p>
                        <p>{greeting_text}</p>
                    </div>
        """
        
        # 获取PubMed API实例来查询期刊质量
        api = PubMedAPI()
        
        for i, article in enumerate(articles, 1):
            # 获取期刊质量信息
            issn = getattr(article, 'issn', '') or getattr(article, 'eissn', '')
            journal_quality = api.get_journal_quality(issn) if issn else {}
            
            # 构建质量标签
            quality_badges = []
            
            # JCR分区
            if journal_quality.get('jcr_quartile'):
                quality_badges.append(f'<span class="quality-badge jcr-quartile">JCR {journal_quality["jcr_quartile"]}</span>')
            
            # 影响因子
            if journal_quality.get('jcr_if'):
                quality_badges.append(f'<span class="quality-badge impact-factor">IF {journal_quality["jcr_if"]}</span>')
                
            # 中科院分区（如果是Top期刊，显示为"1区Top"格式）
            if journal_quality.get('zky_category'):
                if journal_quality.get('zky_top') and journal_quality['zky_top'] == '是':
                    quality_badges.append(f'<span class="quality-badge top-journal">{journal_quality["zky_category"]}区 Top</span>')
                else:
                    quality_badges.append(f'<span class="quality-badge cas-category">中科院 {journal_quality["zky_category"]}区</span>')
            
            quality_html = f'<div class="quality-badges">{"".join(quality_badges)}</div>' if quality_badges else ''
            
            # 构建摘要部分
            abstract_html = ""
            if hasattr(article, 'abstract') and article.abstract:
                # 英文摘要
                abstract_html += f'''
                    <div class="abstract-section">
                        <div class="abstract-title">📄 英文摘要</div>
                        <div class="abstract-content">{article.abstract}</div>
                    </div>
                '''
                
                # 中文翻译（如果有）
                if hasattr(article, 'abstract_translation') and article.abstract_translation:
                    abstract_html += f'''
                        <div class="abstract-section">
                            <div class="abstract-title">🇨🇳 中文摘要</div>
                            <div class="abstract-content chinese-abstract">{article.abstract_translation}</div>
                        </div>
                    '''
            
            # 获取发表日期
            pub_date = ""
            if hasattr(article, 'publish_date') and article.publish_date:
                pub_date = f" • {article.publish_date.strftime('%Y-%m-%d')}"
            elif hasattr(article, 'pub_date') and article.pub_date:
                pub_date = f" • {article.pub_date}"
            
            # 构建ISSN信息
            issn_info = ""
            article_issn = getattr(article, 'issn', '')
            article_eissn = getattr(article, 'eissn', '')
            
            issn_parts = []
            if article_issn:
                issn_parts.append(f"ISSN: {article_issn}")
            if article_eissn:
                issn_parts.append(f"eISSN: {article_eissn}")
            
            if issn_parts:
                issn_info = f'<div style="color: #6c757d; font-size: 13px; margin-top: 5px;">📝 {" • ".join(issn_parts)}</div>'
            
            html_content += f"""
                    <div class="article">
                        <div class="article-header">
                            <div class="article-number">{i}</div>
                            <h3 class="title">
                                <a href="{getattr(article, 'pubmed_url', '#')}" target="_blank">
                                    {getattr(article, 'title', '未知标题')}
                                </a>
                            </h3>
                        </div>
                        
                        <div class="journal-info">
                            <div class="journal-name">
                                📖 {getattr(article, 'journal', '未知期刊')}{pub_date}
                            </div>
                            {issn_info}
                            {quality_html}
                        </div>
                        
                        {abstract_html}
                    </div>
            """
        
        html_content += f"""
                </div>
                
                <div class="footer">
                    <p><strong>此邮件由 PubMed Literature Push 自动发送，请勿回复。</strong></p>
                    <p>如需修改推送设置，请登录系统管理后台</p>
                </div>
            </div>
        </body>
        </html>
        """
        
        return html_content
    
    def _generate_email_text(self, user, articles, articles_by_subscription=None):
        """生成邮件纯文本内容 - 现在只处理单个订阅"""
        
        # 生成开头文案，包含关键词信息
        if articles_by_subscription and len(articles_by_subscription) == 1:
            # 获取关键词（现在总是只有一个）
            keywords = list(articles_by_subscription.keys())[0]
            greeting_text = f"您设置的{keywords}主题词，我们为您找到了以下最新的学术文献："
        else:
            # 备用格式
            greeting_text = "我们为您找到了以下最新的学术文献："
            
        content = f"PubMed 文献推送\\n\\n{greeting_text}\\n\\n"
        
        api = PubMedAPI()
        
        for i, article in enumerate(articles, 1):
            # 获取期刊质量信息
            issn = getattr(article, 'issn', '') or getattr(article, 'eissn', '')
            journal_quality = api.get_journal_quality(issn) if issn else {}
            
            content += f"{i}. {getattr(article, 'title', '未知标题')}\\n"
            content += f"   期刊: {getattr(article, 'journal', '未知期刊')}"
            
            # 添加发表日期
            if hasattr(article, 'publish_date') and article.publish_date:
                content += f" • {article.publish_date.strftime('%Y-%m-%d')}"
            elif hasattr(article, 'pub_date') and article.pub_date:
                content += f" • {article.pub_date}"
            content += "\\n"
            
            # 添加ISSN信息
            article_issn = getattr(article, 'issn', '')
            article_eissn = getattr(article, 'eissn', '')
            issn_parts = []
            if article_issn:
                issn_parts.append(f"ISSN: {article_issn}")
            if article_eissn:
                issn_parts.append(f"eISSN: {article_eissn}")
            
            if issn_parts:
                content += f"   {' • '.join(issn_parts)}\\n"
            
            # 添加期刊质量信息
            quality_info = []
            if journal_quality.get('jcr_quartile'):
                quality_info.append(f"JCR {journal_quality['jcr_quartile']}")
            if journal_quality.get('jcr_if'):
                quality_info.append(f"IF {journal_quality['jcr_if']}")
            if journal_quality.get('zky_category'):
                if journal_quality.get('zky_top') and journal_quality['zky_top'] == '是':
                    quality_info.append(f"中科院 {journal_quality['zky_category']}区 Top")
                else:
                    quality_info.append(f"中科院 {journal_quality['zky_category']}区")
            
            if quality_info:
                content += f"   期刊质量: {' | '.join(quality_info)}\\n"
            
            content += f"   链接: {getattr(article, 'pubmed_url', '#')}\\n"
            
            # 添加英文摘要
            if hasattr(article, 'abstract') and article.abstract:
                content += f"   英文摘要: {article.abstract[:200]}{'...' if len(article.abstract) > 200 else ''}\\n"
                
                # 添加中文摘要（如果有）
                if hasattr(article, 'abstract_translation') and article.abstract_translation:
                    content += f"   中文摘要: {article.abstract_translation[:200]}{'...' if len(article.abstract_translation) > 200 else ''}\\n"
            
            content += "\\n"
        
        content += "此邮件由 PubMed Literature Push 自动发送，请勿回复。\\n"
        
        return content

# 全局推送服务实例
push_service = SimpleLiteraturePushService()

# 初始化调度器
scheduler = BackgroundScheduler(timezone=BEIJING_TZ)

def init_scheduler():
    """初始化定时推送调度器"""
    import os
    import socket
    
    # 在gunicorn环境下，只有主进程才应该运行调度器
    # 使用文件锁确保只有一个进程运行调度器
    lock_file = '/app/data/scheduler.lock'
    
    try:
        # 尝试创建锁文件
        if os.path.exists(lock_file):
            # 检查锁文件中的进程是否还在运行
            try:
                with open(lock_file, 'r') as f:
                    old_pid = int(f.read().strip())
                # 检查进程是否存在
                os.kill(old_pid, 0)
                print(f"调度器已在进程 {old_pid} 中运行，跳过初始化")
                return
            except (OSError, ValueError):
                # 进程不存在或文件损坏，删除锁文件
                os.remove(lock_file)
        
        # 创建新的锁文件
        with open(lock_file, 'w') as f:
            f.write(str(os.getpid()))
        
        print(f"进程 {os.getpid()} 开始初始化调度器")
        
        # 添加定时任务
        # 根据配置的频率检查是否有用户需要推送
        check_frequency = int(SystemSetting.get_setting('push_check_frequency', '1'))
        if check_frequency == 1:
            # 每小时检查（默认）
            trigger = CronTrigger(minute=0)  # 每小时的0分执行
            job_name = '每小时推送检查'
        else:
            # 每N小时检查
            trigger = CronTrigger(minute=0, hour=f'*/{check_frequency}')
            job_name = f'每{check_frequency}小时推送检查'
        
        scheduler.add_job(
            func=check_and_push_articles,
            trigger=trigger,
            id='push_check',
            name=job_name,
            replace_existing=True,
            max_instances=1
        )
        
        # 启动调度器
        if not scheduler.running:
            scheduler.start()
            print(f"定时推送调度器已启动 (PID: {os.getpid()})")
        
        # 注册关闭处理器
        def cleanup_scheduler():
            if scheduler.running:
                scheduler.shutdown()
            if os.path.exists(lock_file):
                try:
                    os.remove(lock_file)
                    print("调度器锁文件已清理")
                except:
                    pass
        
        atexit.register(cleanup_scheduler)
        
    except Exception as e:
        print(f"调度器初始化失败: {e}")

def check_and_push_articles():
    """检查并执行推送任务"""
    with app.app_context():  # 添加Flask应用上下文
        try:
            current_time = beijing_now()
            hour = current_time.hour
            minute = current_time.minute
            weekday = current_time.strftime('%A').lower()
            day_of_month = current_time.day
            
            # 详细日志：记录每次检查
            app.logger.info(f"[调度器] 开始检查推送任务 - {current_time.strftime('%Y-%m-%d %H:%M:%S')} (PID: {os.getpid()})")
            print(f"[调度器] 检查推送任务 - {current_time.strftime('%Y-%m-%d %H:%M:%S')} (PID: {os.getpid()})")
            
            # 获取所有活跃用户
            users = User.query.filter_by(is_active=True).all()
            app.logger.info(f"[调度器] 找到 {len(users)} 个活跃用户")
            
            push_count = 0
            for user in users:
                if should_push_now(user, hour, minute, weekday, day_of_month):
                    try:
                        app.logger.info(f"[调度器] 开始为用户 {user.email} 推送文章 (推送时间: {user.push_time}, 频率: {user.push_frequency})")
                        print(f"[调度器] 开始为用户 {user.email} 推送文章")
                        
                        result = push_service.process_user_subscriptions(user.id)
                        push_count += 1
                        
                        if result and result[0].get('success'):
                            articles_count = result[0].get('articles_found', 0)
                            if articles_count > 0:
                                log_activity('INFO', 'push', f'用户 {user.email} 推送成功: {articles_count} 篇文章')
                                app.logger.info(f"[调度器] 用户 {user.email} 推送成功: {articles_count} 篇文章")
                            else:
                                log_activity('INFO', 'push', f'用户 {user.email} 无新文章推送')
                                app.logger.info(f"[调度器] 用户 {user.email} 无新文章推送")
                    except Exception as e:
                        log_activity('ERROR', 'push', f'用户 {user.email} 推送失败: {str(e)}')
                        app.logger.error(f"[调度器] 用户 {user.email} 推送失败: {e}")
                        print(f"[调度器] 用户 {user.email} 推送失败: {e}")
                else:
                    # 详细日志：记录为什么不推送
                    if user.push_time:
                        app.logger.debug(f"[调度器] 用户 {user.email} 时间不匹配 (设定: {user.push_time}, 当前: {hour:02d}:{minute:02d})")
            
            if push_count > 0:
                app.logger.info(f"[调度器] 本次检查完成，推送了 {push_count} 个用户")
                print(f"[调度器] 本次检查完成，推送了 {push_count} 个用户")
            else:
                app.logger.debug(f"[调度器] 本次检查完成，无用户需要推送")
                        
        except Exception as e:
            log_activity('ERROR', 'push', f'推送检查任务失败: {str(e)}')
            app.logger.error(f"[调度器] 推送检查任务失败: {e}")
            print(f"[调度器] 推送检查任务失败: {e}")

def should_push_now(user, current_hour, current_minute, current_weekday, current_day):
    """判断用户是否应该在当前时间推送"""
    # 检查推送时间
    if user.push_time:
        try:
            push_hour, push_minute = map(int, user.push_time.split(':'))
            # 允许1分钟误差
            if not (current_hour == push_hour and abs(current_minute - push_minute) <= 1):
                return False
        except:
            return False
    else:
        # 默认推送时间8:00
        if not (current_hour == 8 and current_minute <= 1):
            return False
    
    # 检查推送频率
    if user.push_frequency == 'daily':
        return should_push_daily(user)
    elif user.push_frequency == 'weekly':
        return should_push_weekly(user, current_weekday)
    elif user.push_frequency == 'monthly':
        return should_push_monthly(user, current_day)
    
    return False

def should_push_daily(user):
    """检查是否应该每日推送"""
    if not user.last_push:
        return True
    
    # 检查距离上次推送是否超过20小时（避免重复推送）
    time_since_last = beijing_now() - user.last_push
    return time_since_last.total_seconds() > 20 * 3600

def should_push_weekly(user, current_weekday):
    """检查是否应该每周推送"""
    if not user.last_push:
        return True
    
    # 检查今天是否是用户设置的推送日
    user_weekday = user.push_day or 'monday'
    if current_weekday != user_weekday:
        return False
    
    # 检查距离上次推送是否超过6天
    time_since_last = beijing_now() - user.last_push
    return time_since_last.days >= 6

def should_push_monthly(user, current_day):
    """检查是否应该每月推送"""
    if not user.last_push:
        return True
    
    # 检查今天是否是用户设置的推送日
    user_day = user.push_month_day or 1
    if current_day != user_day:
        return False
    
    # 检查距离上次推送是否超过25天
    time_since_last = beijing_now() - user.last_push
    return time_since_last.days >= 25

def get_search_days_by_frequency(push_frequency):
    """根据推送频率确定搜索天数"""
    if push_frequency == 'daily':
        return 3  # 每日推送搜索最近3天
    elif push_frequency == 'weekly':
        return 10  # 每周推送搜索最近10天
    elif push_frequency == 'monthly':
        return 35  # 每月推送搜索最近35天
    else:
        return 3  # 默认3天

@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))

# AI服务模块
class AIService:
    def __init__(self):
        self.default_query_prompt = """你是一个专业的医学文献检索专家。请根据用户提供的关键词，生成规范的PubMed检索式。

要求：
1. 使用适当的MeSH术语和自由词
2. 合理使用布尔运算符(AND, OR)
3. 使用[Title/Abstract]字段限定
4. 考虑同义词和相关术语
5. 只返回最终的PubMed检索式，不要任何解释说明或分析过程
6. 不要包含"检索式："等前缀

用户关键词: {keywords}"""

        self.default_translation_prompt = """请将以下英文医学摘要准确翻译成中文，要求：
1. 保持专业术语的准确性
2. 语句通顺自然
3. 保持原文的逻辑结构
4. 只返回中文翻译结果，不要任何额外说明、标题或格式
5. 不要包含"中文译文："等前缀

英文摘要: {abstract}"""
    
    def get_active_provider(self):
        """获取活跃的AI提供商，优先使用数据库配置，其次使用环境变量"""
        # 首先尝试从数据库获取
        db_provider = AISetting.query.filter_by(is_active=True).first()
        if db_provider:
            return db_provider
        
        # 如果数据库没有配置，尝试从环境变量创建临时提供商对象
        openai_api_key = os.environ.get('OPENAI_API_KEY')
        openai_api_base = os.environ.get('OPENAI_API_BASE', 'https://api.openai.com/v1')
        
        if openai_api_key:
            # 创建临时 AISetting 对象（不保存到数据库）
            class TempProvider:
                def __init__(self, api_key, base_url):
                    self.api_key = api_key
                    self.base_url = base_url
                    self.provider_name = 'Environment Variable'
                    self.is_active = True
                
                def get_decrypted_api_key(self):
                    return self.api_key
            
            app.logger.info(f"使用环境变量 OPENAI_API_KEY 作为 AI 提供商")
            return TempProvider(openai_api_key, openai_api_base)
        
        return None
    
    def get_model_by_type(self, model_type):
        """根据类型获取可用的模型"""
        provider = self.get_active_provider()
        if not provider:
            return None
        
        return AIModel.query.filter_by(
            provider_id=provider.id,
            model_type=model_type,
            is_available=True
        ).first()
    
    def get_configured_model(self, function_type):
        """根据配置获取指定功能的模型"""
        if function_type == 'query_builder':
            provider_id = SystemSetting.get_setting('ai_query_builder_provider_id', '')
            model_id = SystemSetting.get_setting('ai_query_builder_model_id', '')
        elif function_type == 'translator':
            provider_id = SystemSetting.get_setting('ai_translation_provider_id', '')
            model_id = SystemSetting.get_setting('ai_translation_model_id', '')
        else:
            return None
            
        if not provider_id or not model_id:
            return None
            
        try:
            # 获取指定的模型
            model = AIModel.query.filter_by(
                id=int(model_id),
                provider_id=int(provider_id),
                is_available=True
            ).first()
            
            if model and model.provider.is_active:
                return model
                
        except (ValueError, AttributeError):
            pass
            
        return None
    
    def create_openai_client(self, provider):
        """创建OpenAI兼容的客户端"""
        try:
            from openai import OpenAI
            return OpenAI(
                api_key=provider.get_decrypted_api_key(),
                base_url=provider.base_url
            )
        except Exception as e:
            app.logger.error(f"创建AI客户端失败: {str(e)}")
            return None
    
    def build_pubmed_query(self, keywords):
        """使用AI生成PubMed检索式"""
        try:
            # 检查是否启用AI检索式生成
            if SystemSetting.get_setting('ai_query_builder_enabled', 'false') != 'true':
                return keywords  # 未启用，返回原始关键词
            
            # 获取配置的模型
            model = self.get_configured_model('query_builder')
            if not model:
                app.logger.warning("未找到或未配置检索式构建模型")
                return keywords
            
            # 获取提供商
            provider = model.provider
            if not provider or not provider.is_active:
                app.logger.warning("提供商未激活")
                return keywords
            
            client = self.create_openai_client(provider)
            if not client:
                return keywords
            
            # 获取提示词模板
            prompt_template = AIPromptTemplate.get_default_prompt('query_builder')
            if not prompt_template:
                prompt_template = self.default_query_prompt
            
            # 构建完整提示词
            full_prompt = prompt_template.format(keywords=keywords)
            
            # 调用AI API
            response = client.chat.completions.create(
                model=model.model_id,
                messages=[
                    {"role": "system", "content": "你是一个专业的医学文献检索专家。请确保生成完整的PubMed检索式，必须以完整的括号结尾。"},
                    {"role": "user", "content": full_prompt}
                ],
                temperature=0.1  # 降低随机性，保证结果一致性
            )
            
            # 提取检索式
            query = response.choices[0].message.content.strip()
            
            # 简单验证：如果包含明显解释性文字，返回原始关键词
            if '解释' in query or '说明' in query:
                app.logger.warning("AI返回的检索式格式不正确，使用原始关键词")
                return keywords
            
            app.logger.info(f"AI生成检索式成功: {keywords} -> {query}")
            return query
            
        except Exception as e:
            app.logger.error(f"AI检索式生成失败: {str(e)}")
            return keywords  # 失败时返回原始关键词
    
    def translate_abstract(self, abstract):
        """翻译英文摘要为中文"""
        try:
            # 检查是否启用AI翻译
            if SystemSetting.get_setting('ai_translation_enabled', 'false') != 'true':
                return ""  # 未启用，返回空字符串
            
            if not abstract or len(abstract.strip()) == 0:
                return ""
            
            # 获取配置的翻译模型
            model = self.get_configured_model('translator')
            if not model:
                app.logger.warning("未找到或未配置翻译模型")
                return ""
            
            # 获取提供商
            provider = model.provider
            if not provider or not provider.is_active:
                app.logger.warning("提供商未激活")
                return ""
            
            client = self.create_openai_client(provider)
            if not client:
                return ""
            
            # 获取提示词模板
            prompt_template = AIPromptTemplate.get_default_prompt('translator')
            if not prompt_template:
                prompt_template = self.default_translation_prompt
            
            # 构建完整提示词
            full_prompt = prompt_template.format(abstract=abstract)
            
            # 调用AI API
            response = client.chat.completions.create(
                model=model.model_id,
                messages=[
                    {"role": "system", "content": "你是一个专业的医学文献翻译专家。"},
                    {"role": "user", "content": full_prompt}
                ],
                temperature=0.2  # 稍微提高一点创造性以获得更自然的翻译
            )
            
            # 提取翻译结果
            translation = response.choices[0].message.content.strip()
            
            app.logger.info(f"AI翻译成功，原文长度: {len(abstract)}, 译文长度: {len(translation)}")
            return translation
            
        except Exception as e:
            app.logger.error(f"AI翻译失败: {str(e)}")
            return ""  # 失败时返回空字符串
    
    def translate_abstracts_batch(self, articles):
        """批量翻译多篇摘要（一次性发送）"""
        try:
            # 检查是否启用AI翻译
            if SystemSetting.get_setting('ai_translation_enabled', 'false') != 'true':
                return []
            
            if not articles:
                return []
            
            # 获取配置的翻译模型
            model = self.get_configured_model('translator')
            if not model:
                app.logger.warning("未找到或未配置翻译模型")
                return []
            
            # 获取提供商
            provider = model.provider
            if not provider or not provider.is_active:
                app.logger.warning("提供商未激活")
                return []
            
            client = self.create_openai_client(provider)
            if not client:
                return []
            
            # 构建批量翻译的提示词
            abstracts_text = ""
            for i, article in enumerate(articles, 1):
                abstracts_text += f"[摘要{i}]\n{article.abstract}\n\n"
            
            batch_prompt = f"""你是一个专业的医学文献翻译专家。请将以下{len(articles)}篇英文摘要翻译成中文。

要求：
1. 保持专业术语的准确性
2. 语言流畅自然
3. 保持原文的逻辑结构
4. 按照[摘要1]、[摘要2]的格式返回翻译结果
5. 每个翻译结果之间用"---"分隔

请翻译以下摘要：

{abstracts_text}

请按照格式返回翻译结果：
[摘要1]
[中文翻译内容]
---
[摘要2]
[中文翻译内容]
---
..."""
            
            # 调用AI API
            response = client.chat.completions.create(
                model=model.model_id,
                messages=[
                    {"role": "system", "content": "你是一个专业的医学文献翻译专家。"},
                    {"role": "user", "content": batch_prompt}
                ],
                temperature=0.2
            )
            
            # 提取翻译结果
            response_text = response.choices[0].message.content.strip()
            
            # 解析批量翻译结果
            translations = self.parse_batch_translation_result(response_text, len(articles))
            
            app.logger.info(f"批量翻译成功，处理了{len(articles)}篇摘要，获得{len(translations)}个翻译结果")
            return translations
            
        except Exception as e:
            app.logger.error(f"批量翻译失败: {str(e)}")
            return []
    
    def parse_batch_translation_result(self, response_text, expected_count):
        """解析批量翻译的AI响应结果"""
        try:
            translations = []
            
            # 按分隔符分割
            parts = response_text.split('---')
            
            for part in parts:
                part = part.strip()
                if not part:
                    continue
                
                # 移除摘要编号标记
                import re
                # 匹配并移除 [摘要1]、[摘要2] 等标记
                cleaned_part = re.sub(r'^\[摘要\d+\]\s*', '', part, flags=re.MULTILINE)
                cleaned_part = cleaned_part.strip()
                
                if cleaned_part:
                    translations.append(cleaned_part)
            
            # 如果解析结果数量不匹配，尝试其他解析方式
            if len(translations) != expected_count:
                app.logger.warning(f"批量翻译结果数量不匹配，期望{expected_count}个，实际{len(translations)}个")
                
                # 尝试按换行符分组的方式解析
                lines = response_text.split('\n')
                translations = []
                current_translation = ""
                
                for line in lines:
                    line = line.strip()
                    if re.match(r'^\[摘要\d+\]', line):
                        if current_translation:
                            translations.append(current_translation.strip())
                        current_translation = ""
                    elif line and line != '---':
                        current_translation += line + " "
                
                if current_translation:
                    translations.append(current_translation.strip())
            
            # 确保返回正确数量的翻译结果
            while len(translations) < expected_count:
                translations.append("")  # 补充空翻译
            
            return translations[:expected_count]  # 截取到期望数量
            
        except Exception as e:
            app.logger.error(f"解析批量翻译结果失败: {str(e)}")
            return [""] * expected_count  # 返回空翻译列表
    
    def batch_translate_abstracts(self, articles):
        """批量翻译摘要 - 真正的批量处理"""
        try:
            # 检查是否启用AI翻译
            if SystemSetting.get_setting('ai_translation_enabled', 'false') != 'true':
                return
            
            batch_size = int(SystemSetting.get_setting('ai_translation_batch_size', '20'))
            batch_delay = int(SystemSetting.get_setting('ai_translation_batch_delay', '3'))
            
            # 筛选出有摘要的文章
            articles_with_abstract = [article for article in articles 
                                    if hasattr(article, 'abstract') and article.abstract]
            
            if not articles_with_abstract:
                app.logger.info("没有需要翻译的摘要")
                return
            
            app.logger.info(f"开始批量翻译 {len(articles_with_abstract)} 篇文章摘要，批次大小: {batch_size}, 间隔: {batch_delay}秒")
            
            for i in range(0, len(articles_with_abstract), batch_size):
                batch = articles_with_abstract[i:i+batch_size]
                
                # 使用真正的批量翻译
                translations = self.translate_abstracts_batch(batch)
                
                # 将翻译结果分配给对应文章
                for j, article in enumerate(batch):
                    if j < len(translations) and translations[j]:
                        article.abstract_translation = translations[j]
                
                # 非最后一批时等待
                if i + batch_size < len(articles_with_abstract):
                    time.sleep(batch_delay)
                    
            app.logger.info(f"批量翻译完成")
            
        except Exception as e:
            app.logger.error(f"批量翻译失败: {str(e)}")
    
    def test_connection(self, base_url, api_key):
        """测试AI连接"""
        try:
            from openai import OpenAI
            client = OpenAI(api_key=api_key, base_url=base_url)
            
            # 尝试获取模型列表
            models = client.models.list()
            model_list = [model.id for model in models.data]
            
            return True, f"连接成功，发现 {len(model_list)} 个模型"
        except Exception as e:
            return False, f"连接失败: {str(e)}"
    
    def fetch_models(self, provider):
        """获取AI提供商的模型列表"""
        try:
            client = self.create_openai_client(provider)
            if not client:
                return []
            
            models = client.models.list()
            return [{"id": model.id, "name": model.id} for model in models.data]
        except Exception as e:
            app.logger.error(f"获取模型列表失败: {str(e)}")
            return []

# ========== AI管理模板函数 ==========

def get_ai_management_template():
    """AI管理页面模板"""
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <title>AI设置 - 管理后台</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.1.3/dist/css/bootstrap.min.css" rel="stylesheet">
        <link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css" rel="stylesheet">
    </head>
    <body class="bg-light">
        <!-- 导航栏 -->
        <nav class="navbar navbar-expand-lg navbar-dark bg-primary">
            <div class="container">
                <a class="navbar-brand" href="/"><i class="fas fa-book-medical"></i> PubMed推送系统</a>
                <div class="navbar-nav ms-auto">
                    <a class="nav-link" href="/">首页</a>
                    <a class="nav-link" href="/admin">管理后台</a>
                    <a class="nav-link active" href="/admin/ai">AI设置</a>
                    <a class="nav-link" href="/logout">退出</a>
                </div>
            </div>
        </nav>
        
        <div class="container mt-4">
            <!-- 面包屑导航 -->
            <nav aria-label="breadcrumb">
                <ol class="breadcrumb">
                    <li class="breadcrumb-item"><a href="/admin">管理后台</a></li>
                    <li class="breadcrumb-item active">AI设置</li>
                </ol>
            </nav>
            
            <!-- 消息提示 -->
            {% with messages = get_flashed_messages(with_categories=true) %}
                {% if messages %}
                    {% for category, message in messages %}
                        <div class="alert alert-{{ 'danger' if category == 'error' else 'success' if category == 'success' else 'info' }} alert-dismissible fade show">
                            {{ message }}
                            <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
                        </div>
                    {% endfor %}
                {% endif %}
            {% endwith %}
            
            <!-- AI提供商管理 -->
            <div class="card mb-4">
                <div class="card-header d-flex justify-content-between align-items-center flex-wrap">
                    <h5 class="mb-0"><i class="fas fa-cloud"></i> AI提供商管理</h5>
                    <div class="btn-group" role="group">
                        <a href="/admin/ai/prompts" class="btn btn-info btn-sm">
                            <i class="fas fa-edit"></i> 提示词管理
                        </a>
                        <a href="/admin/ai/provider/add" class="btn btn-success btn-sm">
                            <i class="fas fa-plus"></i> 添加提供商
                        </a>
                    </div>
                </div>
                <div class="card-body">
                    {% if providers %}
                        <div class="table-responsive">
                            <table class="table">
                                <thead>
                                    <tr>
                                        <th>提供商名称</th>
                                        <th>API地址</th>
                                        <th>模型数量</th>
                                        <th>状态</th>
                                        <th>创建时间</th>
                                        <th>操作</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {% for provider in providers %}
                                    <tr>
                                        <td>
                                            <strong>{{ provider.provider_name }}</strong>
                                        </td>
                                        <td><code class="small">{{ provider.base_url }}</code></td>
                                        <td>{{ provider.models|length }} 个</td>
                                        <td>
                                            {% if provider.is_active %}
                                                <span class="badge bg-success">活跃</span>
                                            {% else %}
                                                <span class="badge bg-secondary">禁用</span>
                                            {% endif %}
                                        </td>
                                        <td>{{ provider.created_at.strftime('%Y-%m-%d %H:%M') }}</td>
                                        <td>
                                            <form method="POST" action="/admin/ai/provider/{{ provider.id }}/toggle" class="d-inline">
                                                <button type="submit" class="btn btn-sm {{ 'btn-outline-warning' if provider.is_active else 'btn-outline-success' }}">
                                                    {% if provider.is_active %}
                                                        <i class="fas fa-pause"></i> 禁用
                                                    {% else %}
                                                        <i class="fas fa-play"></i> 启用
                                                    {% endif %}
                                                </button>
                                            </form>
                                            <form method="POST" action="/admin/ai/provider/{{ provider.id }}/delete" class="d-inline" 
                                                  onsubmit="return confirm('确定删除提供商【{{ provider.provider_name }}】？这将同时删除相关的模型配置。')">
                                                <button type="submit" class="btn btn-outline-danger btn-sm">
                                                    <i class="fas fa-trash"></i> 删除
                                                </button>
                                            </form>
                                        </td>
                                    </tr>
                                    {% endfor %}
                                </tbody>
                            </table>
                        </div>
                    {% else %}
                        <div class="text-center py-5">
                            <div class="mb-4">
                                <i class="fas fa-robot fa-4x text-muted"></i>
                            </div>
                            <h6 class="text-muted mb-3">暂无AI提供商配置</h6>
                            <p class="text-muted mb-4">添加AI提供商后，您可以使用AI功能进行检索式查询生成和摘要翻译</p>
                            <a href="/admin/ai/provider/add" class="btn btn-primary btn-lg">
                                <i class="fas fa-plus me-2"></i> 添加第一个AI提供商
                            </a>
                        </div>
                    {% endif %}
                </div>
            </div>
            
            <!-- AI功能配置 -->
            <div class="row">
                <!-- 检索式生成配置 -->
                <div class="col-md-6">
                    <div class="card border-primary h-100">
                        <div class="card-header bg-primary text-white">
                            <h6 class="mb-0"><i class="fas fa-search"></i> 检索式生成配置</h6>
                        </div>
                        <div class="card-body">
                            <form method="POST" action="/admin/ai/config/query-builder">
                                <div class="mb-3">
                                    <div class="form-check form-switch">
                                        <input class="form-check-input" type="checkbox" id="queryBuilderEnabled" 
                                               name="enabled" value="true" {{ 'checked' if ai_settings.ai_query_builder_enabled == 'true' }}>
                                        <label class="form-check-label" for="queryBuilderEnabled">
                                            <strong>启用检索式生成功能</strong>
                                        </label>
                                    </div>
                                    <small class="text-muted">启用后可在搜索页面使用AI生成检索式</small>
                                </div>
                                
                                <div class="mb-3">
                                    <label class="form-label">选择提供商：</label>
                                    <select class="form-select" name="provider_id" id="queryProviderSelect" onchange="updateQueryModels()">
                                        <option value="">请选择提供商</option>
                                        {% for provider in providers %}
                                            {% if provider.is_active and provider.models %}
                                                <option value="{{ provider.id }}" data-provider-name="{{ provider.provider_name }}"
                                                        {{ 'selected' if ai_settings.ai_query_builder_provider_id == provider.id|string }}>
                                                    {{ provider.provider_name }}
                                                </option>
                                            {% endif %}
                                        {% endfor %}
                                    </select>
                                </div>
                                
                                <div class="mb-3">
                                    <label class="form-label">选择模型：</label>
                                    <select class="form-select" name="model_id" id="queryModelSelect"
                                            {{ 'disabled' if not ai_settings.ai_query_builder_provider_id else '' }}>
                                        <option value="">{{ '请先选择提供商' if not ai_settings.ai_query_builder_provider_id else '请选择模型' }}</option>
                                    </select>
                                </div>
                                
                                <button type="submit" class="btn btn-primary w-100 mb-3">
                                    <i class="fas fa-save"></i> 保存配置
                                </button>
                            </form>
                            
                            <!-- 功能测试 -->
                            <div class="border-top pt-3">
                                <h6 class="mb-3"><i class="fas fa-flask"></i> 功能测试</h6>
                                <form id="testQueryForm" onsubmit="return false;">
                                    <div class="mb-2">
                                        <input type="text" class="form-control form-control-sm" name="keywords" 
                                               placeholder="输入关键词，如：肺癌，免疫治疗">
                                    </div>
                                    <button type="button" class="btn btn-outline-primary btn-sm w-100" onclick="testQuery()">
                                        <i class="fas fa-play"></i> 测试生成检索式
                                    </button>
                                </form>
                                <div id="queryResult" class="mt-2"></div>
                            </div>
                            
                            <!-- 当前配置显示 -->
                            <div class="mt-3 p-3 bg-light rounded">
                                <h6 class="small mb-2">当前配置：</h6>
                                <p class="small mb-1">状态：
                                    {% if ai_settings.ai_query_builder_enabled == 'true' %}
                                        <span class="badge bg-success">已启用</span>
                                    {% else %}
                                        <span class="badge bg-secondary">已禁用</span>
                                    {% endif %}
                                </p>
                                <p class="small mb-0">模型：
                                    {% if ai_settings.ai_query_builder_provider_id and ai_settings.ai_query_builder_model_id %}
                                        {% for provider in providers %}
                                            {% if provider.id|string == ai_settings.ai_query_builder_provider_id %}
                                                {% for model in provider.models %}
                                                    {% if model.id|string == ai_settings.ai_query_builder_model_id %}
                                                        <code class="small">{{ provider.provider_name }} / {{ model.model_name }}</code>
                                                    {% endif %}
                                                {% endfor %}
                                            {% endif %}
                                        {% endfor %}
                                    {% else %}
                                        <code class="small">未配置</code>
                                    {% endif %}
                                </p>
                            </div>
                        </div>
                    </div>
                </div>
                
                <!-- 翻译配置 -->
                <div class="col-md-6">
                    <div class="card border-info h-100">
                        <div class="card-header bg-info text-white">
                            <h6 class="mb-0"><i class="fas fa-language"></i> 摘要翻译配置</h6>
                        </div>
                        <div class="card-body">
                            <form method="POST" action="/admin/ai/config/translator">
                                <div class="mb-3">
                                    <div class="form-check form-switch">
                                        <input class="form-check-input" type="checkbox" id="translatorEnabled" 
                                               name="enabled" value="true" {{ 'checked' if ai_settings.ai_translation_enabled == 'true' }}>
                                        <label class="form-check-label" for="translatorEnabled">
                                            <strong>启用摘要翻译功能</strong>
                                        </label>
                                    </div>
                                    <small class="text-muted">启用后可在推送邮件中包含中文翻译</small>
                                </div>
                                
                                <div class="mb-3">
                                    <label class="form-label">选择提供商：</label>
                                    <select class="form-select" name="provider_id" id="translatorProviderSelect" onchange="updateTranslatorModels()">
                                        <option value="">请选择提供商</option>
                                        {% for provider in providers %}
                                            {% if provider.is_active and provider.models %}
                                                <option value="{{ provider.id }}" data-provider-name="{{ provider.provider_name }}"
                                                        {{ 'selected' if ai_settings.ai_translation_provider_id == provider.id|string }}>
                                                    {{ provider.provider_name }}
                                                </option>
                                            {% endif %}
                                        {% endfor %}
                                    </select>
                                </div>
                                
                                <div class="mb-3">
                                    <label class="form-label">选择模型：</label>
                                    <select class="form-select" name="model_id" id="translatorModelSelect"
                                            {{ 'disabled' if not ai_settings.ai_translation_provider_id else '' }}>
                                        <option value="">{{ '请先选择提供商' if not ai_settings.ai_translation_provider_id else '请选择模型' }}</option>
                                    </select>
                                </div>
                                
                                <div class="row mb-3">
                                    <div class="col-6">
                                        <label class="form-label small">每批翻译数量</label>
                                        <input type="number" class="form-control form-control-sm" name="batch_size" 
                                               value="{{ ai_settings.ai_translation_batch_size }}" min="1" max="20">
                                        <small class="text-muted">推荐1-10篇</small>
                                    </div>
                                    <div class="col-6">
                                        <label class="form-label small">批次间隔(秒)</label>
                                        <input type="number" class="form-control form-control-sm" name="batch_delay" 
                                               value="{{ ai_settings.ai_translation_batch_delay }}" min="1" max="60">
                                        <small class="text-muted">避免API限制</small>
                                    </div>
                                </div>
                                
                                <button type="submit" class="btn btn-info w-100 mb-3">
                                    <i class="fas fa-save"></i> 保存配置
                                </button>
                            </form>
                            
                            <!-- 功能测试 -->
                            <div class="border-top pt-3">
                                <h6 class="mb-3"><i class="fas fa-flask"></i> 功能测试</h6>
                                <form id="testTranslationForm" onsubmit="return false;">
                                    <div class="mb-2">
                                        <textarea class="form-control form-control-sm" name="abstract" rows="3" 
                                                  placeholder="输入英文摘要进行翻译测试..."></textarea>
                                    </div>
                                    <button type="button" class="btn btn-outline-info btn-sm w-100" onclick="testTranslation()">
                                        <i class="fas fa-play"></i> 测试翻译功能
                                    </button>
                                </form>
                                <div id="translationResult" class="mt-2"></div>
                            </div>
                            
                            <!-- 当前配置显示 -->
                            <div class="mt-3 p-3 bg-light rounded">
                                <h6 class="small mb-2">当前配置：</h6>
                                <p class="small mb-1">状态：
                                    {% if ai_settings.ai_translation_enabled == 'true' %}
                                        <span class="badge bg-success">已启用</span>
                                    {% else %}
                                        <span class="badge bg-secondary">已禁用</span>
                                    {% endif %}
                                </p>
                                <p class="small mb-0">模型：
                                    {% if ai_settings.ai_translation_provider_id and ai_settings.ai_translation_model_id %}
                                        {% for provider in providers %}
                                            {% if provider.id|string == ai_settings.ai_translation_provider_id %}
                                                {% for model in provider.models %}
                                                    {% if model.id|string == ai_settings.ai_translation_model_id %}
                                                        <code class="small">{{ provider.provider_name }} / {{ model.model_name }}</code>
                                                    {% endif %}
                                                {% endfor %}
                                            {% endif %}
                                        {% endfor %}
                                    {% else %}
                                        <code class="small">未配置</code>
                                    {% endif %}
                                </p>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </div>
        
        <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.1.3/dist/js/bootstrap.bundle.min.js"></script>
        <script>
            // 存储所有提供商的模型数据
            const providerModelsData = {
                {% for provider in providers %}
                {{ provider.id }}: {
                    name: "{{ provider.provider_name }}",
                    models: [
                        {% for model in provider.models %}
                        {id: {{ model.id }}, name: "{{ model.model_name }}", type: "{{ model.model_type }}"},
                        {% endfor %}
                    ]
                },
                {% endfor %}
            };
            
            // 存储已保存的配置
            const savedConfig = {
                queryBuilder: {
                    providerId: "{{ ai_settings.ai_query_builder_provider_id }}",
                    modelId: "{{ ai_settings.ai_query_builder_model_id }}"
                },
                translator: {
                    providerId: "{{ ai_settings.ai_translation_provider_id }}",
                    modelId: "{{ ai_settings.ai_translation_model_id }}"
                }
            };
            
            // 页面加载时初始化选择
            document.addEventListener('DOMContentLoaded', function() {
                // 初始化检索式生成的模型选择
                if (savedConfig.queryBuilder.providerId) {
                    updateQueryModels();
                    if (savedConfig.queryBuilder.modelId) {
                        setTimeout(() => {
                            document.getElementById('queryModelSelect').value = savedConfig.queryBuilder.modelId;
                        }, 100);
                    }
                }
                
                // 初始化翻译的模型选择
                if (savedConfig.translator.providerId) {
                    updateTranslatorModels();
                    if (savedConfig.translator.modelId) {
                        setTimeout(() => {
                            document.getElementById('translatorModelSelect').value = savedConfig.translator.modelId;
                        }, 100);
                    }
                }
            });
            
            // 更新检索式生成的模型选择
            function updateQueryModels() {
                const providerSelect = document.getElementById('queryProviderSelect');
                const modelSelect = document.getElementById('queryModelSelect');
                const providerId = providerSelect.value;
                
                // 清空模型选择
                modelSelect.innerHTML = '<option value="">请选择模型</option>';
                
                if (providerId && providerModelsData[providerId]) {
                    const models = providerModelsData[providerId].models;
                    models.forEach(model => {
                        const option = document.createElement('option');
                        option.value = model.id;
                        option.textContent = model.name;
                        modelSelect.appendChild(option);
                    });
                    modelSelect.disabled = false;
                    
                    // 如果有保存的模型ID，自动选择
                    if (savedConfig.queryBuilder.modelId && providerId === savedConfig.queryBuilder.providerId) {
                        modelSelect.value = savedConfig.queryBuilder.modelId;
                    }
                } else {
                    modelSelect.disabled = true;
                }
            }
            
            // 更新翻译的模型选择
            function updateTranslatorModels() {
                const providerSelect = document.getElementById('translatorProviderSelect');
                const modelSelect = document.getElementById('translatorModelSelect');
                const providerId = providerSelect.value;
                
                // 清空模型选择
                modelSelect.innerHTML = '<option value="">请选择模型</option>';
                
                if (providerId && providerModelsData[providerId]) {
                    const models = providerModelsData[providerId].models;
                    models.forEach(model => {
                        const option = document.createElement('option');
                        option.value = model.id;
                        option.textContent = model.name;
                        modelSelect.appendChild(option);
                    });
                    modelSelect.disabled = false;
                    
                    // 如果有保存的模型ID，自动选择
                    if (savedConfig.translator.modelId && providerId === savedConfig.translator.providerId) {
                        modelSelect.value = savedConfig.translator.modelId;
                    }
                } else {
                    modelSelect.disabled = true;
                }
            }
            
            function testQuery() {
                const form = document.getElementById('testQueryForm');
                const formData = new FormData(form);
                const resultDiv = document.getElementById('queryResult');
                
                resultDiv.innerHTML = '<div class="alert alert-info"><i class="fas fa-spinner fa-spin"></i> 测试中...</div>';
                
                fetch('/admin/ai/test/query', {
                    method: 'POST',
                    body: formData
                })
                .then(response => response.json())
                .then(data => {
                    if (data.success) {
                        resultDiv.innerHTML = `
                            <div class="alert alert-success">
                                <strong>测试成功！</strong><br>
                                <small>${data.message}</small><br>
                                ${data.debug_info ? `<small class="text-muted">${data.debug_info}</small><br>` : ''}
                                <strong>生成的检索式：</strong><br>
                                <pre class="bg-light p-2 mt-2 rounded" style="white-space: pre-wrap; word-break: break-all; max-height: 300px; overflow-y: auto;">${data.query}</pre>
                            </div>
                        `;
                    } else {
                        resultDiv.innerHTML = `<div class="alert alert-danger"><strong>测试失败：</strong> ${data.message}</div>`;
                    }
                })
                .catch(error => {
                    resultDiv.innerHTML = `<div class="alert alert-danger"><strong>请求失败：</strong> ${error.message}</div>`;
                });
            }
            
            function testTranslation() {
                const form = document.getElementById('testTranslationForm');
                const formData = new FormData(form);
                const resultDiv = document.getElementById('translationResult');
                
                resultDiv.innerHTML = '<div class="alert alert-info"><i class="fas fa-spinner fa-spin"></i> 翻译中...</div>';
                
                fetch('/admin/ai/test/translation', {
                    method: 'POST',
                    body: formData
                })
                .then(response => response.json())
                .then(data => {
                    if (data.success) {
                        resultDiv.innerHTML = `
                            <div class="alert alert-success">
                                <strong>翻译成功！</strong><br>
                                <small>${data.message}</small><br>
                                <strong>翻译结果：</strong><br>
                                <div class="border rounded p-2 mt-2">${data.translation}</div>
                            </div>
                        `;
                    } else {
                        resultDiv.innerHTML = `<div class="alert alert-danger"><strong>翻译失败：</strong> ${data.message}</div>`;
                    }
                })
                .catch(error => {
                    resultDiv.innerHTML = `<div class="alert alert-danger"><strong>请求失败：</strong> ${error.message}</div>`;
                });
            }
        </script>
    </body>
    </html>
    """

def get_ai_provider_form_template():
    """AI提供商添加表单模板"""
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <title>添加AI提供商 - 管理后台</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.1.3/dist/css/bootstrap.min.css" rel="stylesheet">
        <link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css" rel="stylesheet">
    </head>
    <body class="bg-light">
        <!-- 导航栏 -->
        <nav class="navbar navbar-expand-lg navbar-dark bg-primary">
            <div class="container">
                <a class="navbar-brand" href="/"><i class="fas fa-book-medical"></i> PubMed推送系统</a>
                <div class="navbar-nav ms-auto">
                    <a class="nav-link" href="/">首页</a>
                    <a class="nav-link" href="/admin">管理后台</a>
                    <a class="nav-link" href="/admin/ai">AI设置</a>
                    <a class="nav-link" href="/logout">退出</a>
                </div>
            </div>
        </nav>
        
        <div class="container mt-4">
            <!-- 面包屑导航 -->
            <nav aria-label="breadcrumb">
                <ol class="breadcrumb">
                    <li class="breadcrumb-item"><a href="/admin">管理后台</a></li>
                    <li class="breadcrumb-item"><a href="/admin/ai">AI设置</a></li>
                    <li class="breadcrumb-item active">添加AI提供商</li>
                </ol>
            </nav>
            
            <!-- 消息提示 -->
            {% with messages = get_flashed_messages(with_categories=true) %}
                {% if messages %}
                    {% for category, message in messages %}
                        <div class="alert alert-{{ 'danger' if category == 'error' else 'success' }} alert-dismissible fade show">
                            {{ message }}
                            <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
                        </div>
                    {% endfor %}
                {% endif %}
            {% endwith %}
            
            <div class="row justify-content-center">
                <div class="col-md-8">
                    <div class="card">
                        <div class="card-header">
                            <h5><i class="fas fa-plus"></i> 添加AI提供商</h5>
                        </div>
                        <div class="card-body">
                            <form method="POST">
                                <div class="mb-3">
                                    <label class="form-label">
                                        <i class="fas fa-tag"></i> 提供商名称 *
                                    </label>
                                    <input type="text" class="form-control" name="provider_name" 
                                           placeholder="如：OpenAI, DeepSeek, 通义千问" required>
                                    <div class="form-text">用于识别此AI提供商的名称</div>
                                </div>
                                
                                <div class="mb-3">
                                    <label class="form-label">
                                        <i class="fas fa-link"></i> API基础地址 *
                                    </label>
                                    <input type="url" class="form-control" name="base_url" 
                                           placeholder="https://api.openai.com/v1" required>
                                    <div class="form-text">OpenAI兼容的API端点，通常以/v1结尾</div>
                                </div>
                                
                                <div class="mb-3">
                                    <label class="form-label">
                                        <i class="fas fa-key"></i> API密钥 *
                                    </label>
                                    <input type="password" class="form-control" name="api_key" 
                                           placeholder="sk-..." required>
                                    <div class="form-text">API密钥将加密存储</div>
                                </div>
                                
                                <div class="alert alert-info">
                                    <i class="fas fa-info-circle"></i> 
                                    <strong>提示：</strong>添加后系统将自动测试连接并获取可用的模型列表。
                                </div>
                                
                                <div class="d-grid gap-2">
                                    <button type="submit" class="btn btn-primary">
                                        <i class="fas fa-save"></i> 添加并测试连接
                                    </button>
                                    <a href="/admin/ai" class="btn btn-secondary">
                                        <i class="fas fa-arrow-left"></i> 返回AI设置
                                    </a>
                                </div>
                            </form>
                        </div>
                    </div>
                </div>
            </div>
        </div>
        
        <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.1.3/dist/js/bootstrap.bundle.min.js"></script>
    </body>
    </html>
    """
    
def get_ai_prompts_template():
    """AI提示词管理页面模板"""
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <title>AI提示词管理 - 管理后台</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.1.3/dist/css/bootstrap.min.css" rel="stylesheet">
        <link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css" rel="stylesheet">
    </head>
    <body class="bg-light">
        <!-- 导航栏 -->
        <nav class="navbar navbar-expand-lg navbar-dark bg-primary">
            <div class="container">
                <a class="navbar-brand" href="/"><i class="fas fa-book-medical"></i> PubMed推送系统</a>
                <div class="navbar-nav ms-auto">
                    <a class="nav-link" href="/">首页</a>
                    <a class="nav-link" href="/admin">管理后台</a>
                    <a class="nav-link" href="/admin/ai">AI设置</a>
                    <a class="nav-link" href="/logout">退出</a>
                </div>
            </div>
        </nav>
        
        <div class="container mt-4">
            <!-- 面包屑导航 -->
            <nav aria-label="breadcrumb">
                <ol class="breadcrumb">
                    <li class="breadcrumb-item"><a href="/admin">管理后台</a></li>
                    <li class="breadcrumb-item"><a href="/admin/ai">AI设置</a></li>
                    <li class="breadcrumb-item active">提示词管理</li>
                </ol>
            </nav>
            
            <!-- 消息提示 -->
            {% with messages = get_flashed_messages(with_categories=true) %}
                {% if messages %}
                    {% for category, message in messages %}
                        <div class="alert alert-{{ 'danger' if category == 'error' else 'success' }} alert-dismissible fade show">
                            {{ message }}
                            <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
                        </div>
                    {% endfor %}
                {% endif %}
            {% endwith %}
            
            <div class="row">
                <!-- 检索式生成提示词 -->
                <div class="col-md-6">
                    <div class="card">
                        <div class="card-header">
                            <h5><i class="fas fa-search"></i> 检索式生成提示词</h5>
                        </div>
                        <div class="card-body">
                            <form method="POST" action="/admin/ai/prompt/save">
                                <input type="hidden" name="template_type" value="query_builder">
                                <div class="mb-3">
                                    <label class="form-label">提示词内容</label>
                                    <textarea name="prompt_content" class="form-control" rows="12" placeholder="输入检索式生成提示词...">{% for prompt in query_prompts %}{% if prompt.is_default %}{{ prompt.prompt_content }}{% endif %}{% endfor %}</textarea>
                                    <div class="form-text">使用 {keywords} 作为关键词占位符</div>
                                </div>
                                <button type="submit" class="btn btn-primary">
                                    <i class="fas fa-save"></i> 保存检索式提示词
                                </button>
                            </form>
                        </div>
                    </div>
                    
                    <!-- 历史版本 -->
                    {% if query_prompts|length > 1 %}
                    <div class="card mt-3">
                        <div class="card-header">
                            <h6><i class="fas fa-history"></i> 历史版本</h6>
                        </div>
                        <div class="card-body">
                            {% for prompt in query_prompts %}
                                {% if not prompt.is_default %}
                                <div class="border rounded p-2 mb-2">
                                    <div class="d-flex justify-content-between align-items-center">
                                        <span class="small text-muted">{{ prompt.created_at.strftime('%Y-%m-%d %H:%M') }}</span>
                                        <div>
                                            <form method="POST" action="/admin/ai/prompt/{{ prompt.id }}/set-default" class="d-inline">
                                                <button type="submit" class="btn btn-sm btn-outline-primary">设为默认</button>
                                            </form>
                                            <form method="POST" action="/admin/ai/prompt/{{ prompt.id }}/delete" class="d-inline"
                                                  onsubmit="return confirm('确定删除此提示词版本？')">
                                                <button type="submit" class="btn btn-sm btn-outline-danger">删除</button>
                                            </form>
                                        </div>
                                    </div>
                                    <div class="small mt-1" style="max-height: 100px; overflow-y: auto;">
                                        {{ prompt.prompt_content[:200] }}{% if prompt.prompt_content|length > 200 %}...{% endif %}
                                    </div>
                                </div>
                                {% endif %}
                            {% endfor %}
                        </div>
                    </div>
                    {% endif %}
                </div>
                
                <!-- 翻译提示词 -->
                <div class="col-md-6">
                    <div class="card">
                        <div class="card-header">
                            <h5><i class="fas fa-language"></i> 翻译提示词</h5>
                        </div>
                        <div class="card-body">
                            <form method="POST" action="/admin/ai/prompt/save">
                                <input type="hidden" name="template_type" value="translator">
                                <div class="mb-3">
                                    <label class="form-label">提示词内容</label>
                                    <textarea name="prompt_content" class="form-control" rows="12" placeholder="输入翻译提示词...">{% for prompt in translator_prompts %}{% if prompt.is_default %}{{ prompt.prompt_content }}{% endif %}{% endfor %}</textarea>
                                    <div class="form-text">使用 {abstract} 作为摘要占位符</div>
                                </div>
                                <button type="submit" class="btn btn-primary">
                                    <i class="fas fa-save"></i> 保存翻译提示词
                                </button>
                            </form>
                        </div>
                    </div>
                    
                    <!-- 历史版本 -->
                    {% if translator_prompts|length > 1 %}
                    <div class="card mt-3">
                        <div class="card-header">
                            <h6><i class="fas fa-history"></i> 历史版本</h6>
                        </div>
                        <div class="card-body">
                            {% for prompt in translator_prompts %}
                                {% if not prompt.is_default %}
                                <div class="border rounded p-2 mb-2">
                                    <div class="d-flex justify-content-between align-items-center">
                                        <span class="small text-muted">{{ prompt.created_at.strftime('%Y-%m-%d %H:%M') }}</span>
                                        <div>
                                            <form method="POST" action="/admin/ai/prompt/{{ prompt.id }}/set-default" class="d-inline">
                                                <button type="submit" class="btn btn-sm btn-outline-primary">设为默认</button>
                                            </form>
                                            <form method="POST" action="/admin/ai/prompt/{{ prompt.id }}/delete" class="d-inline"
                                                  onsubmit="return confirm('确定删除此提示词版本？')">
                                                <button type="submit" class="btn btn-sm btn-outline-danger">删除</button>
                                            </form>
                                        </div>
                                    </div>
                                    <div class="small mt-1" style="max-height: 100px; overflow-y: auto;">
                                        {{ prompt.prompt_content[:200] }}{% if prompt.prompt_content|length > 200 %}...{% endif %}
                                    </div>
                                </div>
                                {% endif %}
                            {% endfor %}
                        </div>
                    </div>
                    {% endif %}
                </div>
            </div>
        </div>
        
        <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.1.3/dist/js/bootstrap.bundle.min.js"></script>
    </body>
    </html>
    """

# 全局AI服务实例
ai_service = AIService()

# PubMed API全局限流器

class PubMedRateLimiter:
    """PubMed API全局限流器，确保整个服务器的请求频率不超过NCBI限制"""
    
    def __init__(self):
        self._lock = threading.Lock()
        self._request_queue = queue.Queue()
        self._last_request_time = 0
        self._worker_thread = None
        self._stop_worker = False
        # 缓存API Key状态和间隔时间，避免在工作线程中访问数据库
        self._api_key_status = False
        self._min_interval = 0.5  # 默认无API Key的间隔
        self._last_check_time = 0
        self._check_interval = 60  # 每60秒检查一次API Key状态
        self._start_worker()
    
    def _update_api_key_status(self):
        """更新API Key状态（在主线程中调用）"""
        try:
            with app.app_context():
                api_key = SystemSetting.get_setting('pubmed_api_key', '').strip()
                has_api_key = bool(api_key)
                
                with self._lock:
                    self._api_key_status = has_api_key
                    # 根据API Key状态设置限流参数（增加缓冲）
                    if has_api_key:
                        self._min_interval = 0.12  # 有API Key：10请求/秒理论值0.1秒，实际使用0.12秒缓冲
                    else:
                        self._min_interval = 0.5   # 无API Key：3请求/秒理论值0.33秒，实际使用0.5秒缓冲
                    self._last_check_time = time.time()
        except Exception as e:
            # 如果无法访问数据库，使用保守设置
            with self._lock:
                self._api_key_status = False
                self._min_interval = 0.5
    
    def _start_worker(self):
        """启动工作线程处理请求队列"""
        def worker():
            while not self._stop_worker:
                try:
                    # 从队列获取请求任务，超时1秒
                    task = self._request_queue.get(timeout=1)
                    if task is None:  # 停止信号
                        break
                    
                    request_func, future = task
                    
                    # 执行限流控制
                    self._wait_if_needed()
                    
                    # 执行实际请求
                    try:
                        result = request_func()
                        future.set_result(result)
                    except Exception as e:
                        future.set_exception(e)
                    finally:
                        self._request_queue.task_done()
                        
                except queue.Empty:
                    continue
                except Exception as e:
                    print(f"限流器工作线程错误: {e}")
        
        self._worker_thread = threading.Thread(target=worker, daemon=True)
        self._worker_thread.start()
    
    def _wait_if_needed(self):
        """根据缓存的API Key状态进行延迟控制"""
        with self._lock:
            # 检查是否需要更新API Key状态
            current_time = time.time()
            if current_time - self._last_check_time > self._check_interval:
                # 在工作线程中不能直接访问数据库，跳过更新
                # 实际更新会在execute_request方法中进行
                pass
            
            # 使用缓存的间隔时间
            min_interval = self._min_interval
            time_since_last = current_time - self._last_request_time
            
            if time_since_last < min_interval:
                sleep_time = min_interval - time_since_last
                time.sleep(sleep_time)
            
            self._last_request_time = time.time()
    
    def execute_request(self, request_func):
        """
        执行限流的请求
        
        Args:
            request_func: 要执行的请求函数
            
        Returns:
            请求结果
        """
        import concurrent.futures
        
        # 在主线程中检查并更新API Key状态
        current_time = time.time()
        if current_time - self._last_check_time > self._check_interval:
            self._update_api_key_status()
        
        # 创建Future对象用于获取结果
        future = concurrent.futures.Future()
        
        # 将请求任务加入队列
        self._request_queue.put((request_func, future))
        
        # 等待结果
        return future.result()
    
    def shutdown(self):
        """关闭限流器"""
        self._stop_worker = True
        self._request_queue.put(None)  # 发送停止信号
        if self._worker_thread:
            self._worker_thread.join()

# 全局限流器实例
pubmed_rate_limiter = PubMedRateLimiter()

# 在应用上下文中初始化API Key状态
def init_rate_limiter():
    """初始化限流器的API Key状态"""
    try:
        pubmed_rate_limiter._update_api_key_status()
    except Exception as e:
        # 如果初始化失败，使用默认保守设置
        print(f"限流器初始化警告: {e}")
        pass

# PubMed API完整版
class PubMedAPI:
    # 文章类型过滤常量 - 使用正向选择避免负向过滤的语法问题
    ARTICLE_TYPE_FILTER = '("Journal Article"[PT] OR "Review"[PT] OR "Case Reports"[PT] OR "Clinical Trial"[PT] OR "Randomized Controlled Trial"[PT] OR "Meta-Analysis"[PT] OR "Systematic Review"[PT])'
    
    def __init__(self):
        self.base_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
        # 从系统配置获取API Key
        api_key = SystemSetting.get_setting('pubmed_api_key', '')
        self.api_key = api_key if api_key.strip() else None
        # 不再需要request_delay，使用全局限流器
    
    
    def get_journal_quality(self, issn, eissn=None):
        """获取期刊质量信息"""
        quality_info = {
            'jcr_if': '',
            'jcr_quartile': '',
            'zky_category': '',
            'zky_top': '',
            'has_quality_data': False
        }
        
        # 使用全局缓存获取数据
        # 优先使用ISSN查询
        if issn:
            jcr_info = journal_cache.get_jcr_data(issn)
            if jcr_info:
                quality_info['jcr_if'] = jcr_info.get('if', '')
                quality_info['jcr_quartile'] = jcr_info.get('quartile', '')
                quality_info['has_quality_data'] = True
            
            zky_info = journal_cache.get_zky_data(issn)
            if zky_info:
                quality_info['zky_category'] = zky_info.get('category', '')
                quality_info['zky_top'] = zky_info.get('top', '')
                quality_info['has_quality_data'] = True
        
        # 如果ISSN没找到，尝试eISSN
        if not quality_info['has_quality_data'] and eissn:
            jcr_info = journal_cache.get_jcr_data(eissn)
            if jcr_info:
                quality_info['jcr_if'] = jcr_info.get('if', '')
                quality_info['jcr_quartile'] = jcr_info.get('quartile', '')
                quality_info['has_quality_data'] = True
            
            zky_info = journal_cache.get_zky_data(eissn)
            if zky_info:
                quality_info['zky_category'] = zky_info.get('category', '')
                quality_info['zky_top'] = zky_info.get('top', '')
                quality_info['has_quality_data'] = True
        
        return quality_info
    
    def search_articles(self, keywords, max_results=20, days_back=30, user_email=None):
        """
        搜索PubMed文章
        
        Args:
            keywords: 关键词列表或字符串  
            max_results: 最大结果数
            days_back: 搜索过去N天的文章（固定30天）
            user_email: 用户邮箱（用于PubMed API请求标识）
        
        Returns:
            list: PMID列表
        """
        # 首先使用AI优化关键词
        original_keywords = keywords
        if isinstance(keywords, str):
            # AI查询构建器防重复调用机制
            import time
            current_time = time.time()
            ai_cache_key = f'ai_query_{keywords}'
            
            # 检查缓存中是否有最近的结果
            if hasattr(self, '_ai_query_cache'):
                cache_data = self._ai_query_cache.get(ai_cache_key)
                if cache_data and current_time - cache_data['timestamp'] < 300:  # 300秒内复用结果
                    app.logger.info(f"使用缓存的AI检索式: {keywords} -> {cache_data['query'][:50]}...")
                    optimized_keywords = cache_data['query']
                else:
                    # 缓存过期，重新生成
                    optimized_keywords = ai_service.build_pubmed_query(keywords)
                    if not hasattr(self, '_ai_query_cache'):
                        self._ai_query_cache = {}
                    self._ai_query_cache[ai_cache_key] = {
                        'query': optimized_keywords,
                        'timestamp': current_time
                    }
            else:
                # 首次调用，初始化缓存
                optimized_keywords = ai_service.build_pubmed_query(keywords)
                self._ai_query_cache = {
                    ai_cache_key: {
                        'query': optimized_keywords,
                        'timestamp': current_time
                    }
                }
            # 如果AI优化成功（返回的不是原始关键词），直接使用优化后的完整检索式
            if optimized_keywords != keywords and optimized_keywords.strip():
                # AI返回的是完整的检索式，但需要添加日期限制和文章类型过滤
                end_date = beijing_now()
                start_date = end_date - timedelta(days=days_back)
                date_range = f'("{start_date.strftime("%Y/%m/%d")}"[Date - Publication] : "{end_date.strftime("%Y/%m/%d")}"[Date - Publication])'
                final_query = f'{optimized_keywords} AND {date_range} AND {self.ARTICLE_TYPE_FILTER}'
                
                # 直接使用AI优化的检索式进行搜索
                esearch_url = f"{self.base_url}esearch.fcgi"
                params = {
                    'db': 'pubmed',
                    'term': final_query,
                    'retmax': str(max_results),  # 确保是字符串类型
                    'sort': 'relevance',         # 改为相关性排序
                    'tool': 'PubMedPushSystem',  # 添加工具标识
                    'retmode': 'json'            # 改为JSON格式
                }
                
                # 添加用户邮箱标识（如果提供）
                if user_email:
                    params['email'] = user_email
                
                if self.api_key:
                    params['api_key'] = self.api_key
                
                try:
                    # 使用全局限流器执行请求
                    def make_request():
                        return requests.get(esearch_url, params=params, timeout=30)
                    
                    response = pubmed_rate_limiter.execute_request(make_request)
                    response.raise_for_status()
                    
                    # 解析JSON响应
                    data = response.json()
                    pmids = data.get('esearchresult', {}).get('idlist', [])
                    
                    return pmids
                    
                except Exception as e:
                    app.logger.error(f"使用AI优化检索式搜索失败: {str(e)}")
                    # 如果AI优化的检索式失败，继续使用原始方法
        
        # 构建搜索查询（原始方法）
        if isinstance(keywords, str):
            keywords = [kw.strip() for kw in keywords.split(',')]
        
        query_terms = []
        for keyword in keywords:
            if keyword.strip():
                # 添加字段限定，搜索标题和摘要
                query_terms.append(f'({keyword.strip()}[Title/Abstract])')
        
        if not query_terms:
            return []
        
        # 组合关键词（固定使用AND逻辑）
        search_query = ' AND '.join(query_terms)
        
        # 添加日期限制和文章类型过滤
        end_date = beijing_now()
        start_date = end_date - timedelta(days=days_back)
        date_range = f'("{start_date.strftime("%Y/%m/%d")}"[Date - Publication] : "{end_date.strftime("%Y/%m/%d")}"[Date - Publication])'
        
        final_query = f'({search_query}) AND {date_range} AND {self.ARTICLE_TYPE_FILTER}'
        
        # 构建请求URL
        esearch_url = f"{self.base_url}esearch.fcgi"
        params = {
            'db': 'pubmed',
            'term': final_query,
            'retmax': str(max_results),  # 确保是字符串类型
            'sort': 'relevance',         # 改为相关性排序
            'tool': 'PubMedPushSystem',  # 添加工具标识
            'retmode': 'json'            # 改为JSON格式
        }
        
        # 添加用户邮箱标识（如果提供）
        if user_email:
            params['email'] = user_email
        
        if self.api_key:
            params['api_key'] = self.api_key
        
        try:
            # 使用全局限流器执行请求
            def make_request():
                return requests.get(esearch_url, params=params, timeout=30)
            
            response = pubmed_rate_limiter.execute_request(make_request)
            response.raise_for_status()
            
            # 解析JSON响应
            data = response.json()
            pmids = data.get('esearchresult', {}).get('idlist', [])
            
            return pmids
            
        except requests.RequestException as e:
            print(f"PubMed请求错误: {e}")
            return []
        except ValueError as e:
            print(f"JSON解析错误: {e}")
            return []
        except Exception as e:
            print(f"PubMed搜索错误: {e}")
            return []
    
    def get_article_issn_only(self, pmids):
        """
        轻量级获取文章ISSN信息，用于期刊质量筛选
        
        Args:
            pmids: PMID列表
        
        Returns:
            list: 包含PMID、ISSN、eISSN的轻量级信息列表
        """
        if not pmids:
            return []
        
        # 分批处理PMID以避免URL太长
        batch_size = 200
        all_articles = []
        
        for i in range(0, len(pmids), batch_size):
            batch_pmids = pmids[i:i + batch_size]
            
            efetch_url = f"{self.base_url}efetch.fcgi"
            params = {
                'db': 'pubmed',
                'id': ','.join(batch_pmids),
                'retmode': 'xml'
            }
            
            if self.api_key:
                params['api_key'] = self.api_key
            
            try:
                # 使用全局限流器执行请求
                def make_request():
                    return requests.get(efetch_url, params=params, timeout=60)
                
                response = pubmed_rate_limiter.execute_request(make_request)
                response.raise_for_status()
                
                batch_articles = self._parse_issn_only_xml(response.content)
                all_articles.extend(batch_articles)
                
            except Exception as e:
                print(f"获取第{i//batch_size + 1}批ISSN信息错误: {e}")
                continue
        
        return all_articles
    
    def _parse_issn_only_xml(self, xml_content):
        """
        解析XML，只提取PMID和ISSN信息
        """
        try:
            root = ET.fromstring(xml_content)
            articles = []
            
            for article in root.findall('.//PubmedArticle'):
                pmid_elem = article.find('.//PMID')
                if pmid_elem is not None:
                    pmid = pmid_elem.text
                    
                    # 查找ISSN和eISSN
                    issn = ""
                    eissn = ""
                    
                    journal = article.find('.//Journal')
                    if journal is not None:
                        # 查找所有ISSN元素
                        for issn_elem in journal.findall('.//ISSN'):
                            issn_type = issn_elem.get('IssnType', '')
                            issn_value = issn_elem.text or ''
                            
                            if issn_type == 'Print':
                                issn = issn_value
                            elif issn_type == 'Electronic':
                                eissn = issn_value
                    
                    articles.append({
                        'pmid': pmid,
                        'issn': issn,
                        'eissn': eissn
                    })
            
            return articles
            
        except ET.ParseError as e:
            print(f"解析ISSN XML错误: {e}")
            return []
    
    def get_article_details(self, pmids):
        """
        获取文章详细信息
        
        Args:
            pmids: PMID列表
        
        Returns:
            list: 文章详细信息列表
        """
        if not pmids:
            return []
        
        # 分批处理PMID以避免URL太长
        batch_size = 200  # PubMed建议每批不超过200个ID
        all_articles = []
        
        for i in range(0, len(pmids), batch_size):
            batch_pmids = pmids[i:i + batch_size]
            
            efetch_url = f"{self.base_url}efetch.fcgi"
            params = {
                'db': 'pubmed',
                'id': ','.join(batch_pmids),
                'retmode': 'xml'
            }
            
            if self.api_key:
                params['api_key'] = self.api_key
            
            try:
                # 使用全局限流器执行请求
                def make_request():
                    return requests.get(efetch_url, params=params, timeout=60)
                
                response = pubmed_rate_limiter.execute_request(make_request)
                response.raise_for_status()
                
                batch_articles = self._parse_article_xml(response.content)
                all_articles.extend(batch_articles)
                
            except Exception as e:
                print(f"获取第{i//batch_size + 1}批文章详情错误: {e}")
                continue
        
        return all_articles
    
    def _parse_article_xml(self, xml_content):
        """
        解析文章XML数据
        使用内置ElementTree进行XML解析，无需lxml依赖
        """
        articles = []
        
        try:
            # 使用内置ElementTree解析器
            root = ET.fromstring(xml_content)
            
            for article_elem in root.findall('.//PubmedArticle'):
                try:
                    article_data = self._extract_article_data(article_elem)
                    if article_data:
                        articles.append(article_data)
                except Exception as e:
                    print(f"解析单篇文章错误: {e}")
                    continue
                    
        except ET.ParseError as e:
            print(f"XML解析错误: {e}")
        except Exception as e:
            print(f"解析文章XML失败: {e}")
        
        return articles
    
    def _extract_article_data(self, article_elem):
        """从XML元素中提取文章数据"""
        try:
            # PMID
            pmid_elem = article_elem.find('.//PMID')
            pmid = pmid_elem.text if pmid_elem is not None else None
            
            if not pmid:
                return None
            
            # 标题 - 处理可能的None值
            title_elem = article_elem.find('.//ArticleTitle')
            title = title_elem.text if title_elem is not None and title_elem.text else 'No title available'
            
            # 作者
            authors = []
            for author_elem in article_elem.findall('.//Author'):
                last_name_elem = author_elem.find('LastName')
                first_name_elem = author_elem.find('ForeName')
                
                if last_name_elem is not None and last_name_elem.text:
                    author_name = last_name_elem.text
                    if first_name_elem is not None and first_name_elem.text:
                        author_name += f" {first_name_elem.text}"
                    authors.append(author_name)
            
            # 期刊
            journal_elem = article_elem.find('.//Journal/Title')
            journal = journal_elem.text if journal_elem is not None and journal_elem.text else 'Unknown Journal'
            
            # 发表日期
            pub_date = self._extract_publication_date(article_elem)
            
            # 摘要 - 提取所有AbstractText段落并合并
            abstract_elems = article_elem.findall('.//Abstract/AbstractText')
            abstract_parts = []
            
            for abstract_elem in abstract_elems:
                # 使用itertext()获取包括子元素在内的所有文本内容
                text_parts = []
                for text in abstract_elem.itertext():
                    if text and text.strip():
                        text_parts.append(text.strip())
                
                if text_parts:
                    # 获取段落标签
                    label = abstract_elem.get('Label', '')
                    content = ' '.join(text_parts)
                    
                    # 如果有标签，格式化为"标签: 内容"
                    if label:
                        abstract_parts.append(f"{label}: {content}")
                    else:
                        abstract_parts.append(content)
            
            # 合并所有段落，用换行符分隔
            abstract = '\n\n'.join(abstract_parts) if abstract_parts else ''
            
            # DOI
            doi = None
            for article_id in article_elem.findall('.//ArticleId'):
                if article_id.get('IdType') == 'doi' and article_id.text:
                    doi = article_id.text
                    break
            
            # 关键词
            keywords = []
            for keyword_elem in article_elem.findall('.//Keyword'):
                if keyword_elem.text:
                    keywords.append(keyword_elem.text)
            
            # 提取ISSN和eISSN信息
            issn = None
            eissn = None
            
            # 查找期刊的ISSN信息
            for issn_elem in article_elem.findall('.//Journal/ISSN'):
                issn_type = issn_elem.get('IssnType', '').lower()
                if issn_elem.text:
                    if issn_type == 'print' or not issn_type:
                        issn = issn_elem.text.strip()
                    elif issn_type == 'electronic':
                        eissn = issn_elem.text.strip()
            
            # 如果没有找到ISSN信息，尝试从ISSNLinking中获取
            if not issn and not eissn:
                issn_linking_elem = article_elem.find('.//Journal/ISSNLinking')
                if issn_linking_elem is not None and issn_linking_elem.text:
                    issn = issn_linking_elem.text.strip()
            
            # 获取期刊质量信息
            quality_info = self.get_journal_quality(issn, eissn)
            
            return {
                'pmid': pmid,
                'title': title,
                'authors': ', '.join(authors) if authors else 'Unknown Authors',
                'journal': journal,
                'issn': issn or '',
                'eissn': eissn or '',
                'publish_date': pub_date,
                'abstract': abstract,
                'doi': doi,
                'pubmed_url': f'https://pubmed.ncbi.nlm.nih.gov/{pmid}/',
                'keywords': ', '.join(keywords),
                'url': f'https://pubmed.ncbi.nlm.nih.gov/{pmid}/',  # 兼容性字段
                # 期刊质量信息
                'jcr_if': quality_info['jcr_if'],
                'jcr_quartile': quality_info['jcr_quartile'],
                'zky_category': quality_info['zky_category'],
                'zky_top': quality_info['zky_top'],
                'has_quality_data': quality_info['has_quality_data']
            }
            
        except Exception as e:
            print(f"提取文章数据错误: {e}")
            return None
    
    def _extract_publication_date(self, article_elem):
        """提取发表日期"""
        try:
            # 优先使用PubDate
            pub_date_elem = article_elem.find('.//PubDate')
            if pub_date_elem is not None:
                year_elem = pub_date_elem.find('Year')
                month_elem = pub_date_elem.find('Month')
                day_elem = pub_date_elem.find('Day')
                
                if year_elem is not None and year_elem.text:
                    try:
                        year = int(year_elem.text)
                        month = 1
                        day = 1
                        
                        if month_elem is not None and month_elem.text:
                            try:
                                month = int(month_elem.text)
                            except ValueError:
                                # 月份可能是英文缩写
                                month_map = {
                                    'Jan': 1, 'Feb': 2, 'Mar': 3, 'Apr': 4,
                                    'May': 5, 'Jun': 6, 'Jul': 7, 'Aug': 8,
                                    'Sep': 9, 'Oct': 10, 'Nov': 11, 'Dec': 12
                                }
                                month = month_map.get(month_elem.text, 1)
                        
                        if day_elem is not None and day_elem.text:
                            try:
                                day = int(day_elem.text)
                            except ValueError:
                                day = 1
                        
                        return datetime(year, month, day, tzinfo=BEIJING_TZ)
                    except ValueError:
                        pass
            
            # 如果没有PubDate，尝试其他日期字段
            date_completed = article_elem.find('.//DateCompleted')
            if date_completed is not None:
                year_elem = date_completed.find('Year')
                if year_elem is not None and year_elem.text:
                    try:
                        return datetime(int(year_elem.text), 1, 1, tzinfo=BEIJING_TZ)
                    except ValueError:
                        pass
            
            return beijing_now()
            
        except Exception as e:
            print(f"解析发表日期错误: {e}")
            return beijing_now()
    
    def search_and_fetch(self, keywords, max_results=20, days_back=30):
        """
        搜索并获取文章详细信息的组合方法
        
        Returns:
            list: 完整的文章信息列表
        """
        # 第一步：搜索获取PMID
        pmids = self.search_articles(keywords, max_results, days_back)
        
        if not pmids:
            return []
        
        # 第二步：获取详细信息
        articles = self.get_article_details(pmids)
        
        return articles
    
    def search_and_fetch_with_filter(self, keywords, max_results=20, days_back=30, 
                                   jcr_filter=None, zky_filter=None, exclude_no_issn=True, user_email=None):
        """
        搜索并获取文章详细信息，支持期刊质量筛选
        
        Args:
            keywords: 关键词
            max_results: 最大结果数
            days_back: 搜索天数（固定30天）
            jcr_filter: JCR筛选条件，如 {'quartile': ['Q1', 'Q2']}
            zky_filter: 中科院筛选条件，如 {'category': ['1', '2'], 'top': True}
            exclude_no_issn: 是否排除没有ISSN的文献
            user_email: 用户邮箱，用于PubMed API请求标识
        
        Returns:
            dict: 包含筛选前后数量和文章列表的字典
        """
        # 第一步：搜索获取PMID
        pmids = self.search_articles(keywords, max_results * 2, days_back, user_email)  # 获取更多数据用于筛选
        
        if not pmids:
            return {
                'total_found': 0,
                'articles': [],
                'filtered_count': 0,
                'excluded_no_issn': 0
            }
        
        # 第二步：获取详细信息
        articles = self.get_article_details(pmids)
        
        # 第三步：应用筛选条件
        filtered_articles = []
        excluded_no_issn = 0
        
        for article in articles:
            # 检查是否有ISSN信息
            has_issn = bool(article.get('issn') or article.get('eissn'))
            
            if exclude_no_issn and not has_issn:
                excluded_no_issn += 1
                continue
            
            # 如果没有ISSN但不排除，则保留文章但不应用期刊筛选
            if not has_issn:
                filtered_articles.append(article)
                continue
                
            # 应用JCR筛选
            if jcr_filter:
                jcr_quartile = article.get('jcr_quartile', '')
                if 'quartile' in jcr_filter:
                    if not jcr_quartile or jcr_quartile not in jcr_filter['quartile']:
                        continue
                
                if 'min_if' in jcr_filter:
                    jcr_if = article.get('jcr_if', '')
                    try:
                        if_value = float(jcr_if) if jcr_if else 0
                        if if_value < jcr_filter['min_if']:
                            continue
                    except (ValueError, TypeError):
                        continue
            
            # 应用中科院筛选
            if zky_filter:
                zky_category = article.get('zky_category', '')
                zky_top = article.get('zky_top', '')
                
                if 'category' in zky_filter:
                    if not zky_category or zky_category not in zky_filter['category']:
                        continue
                
                if 'top' in zky_filter:
                    is_top = zky_top == '是'
                    if zky_filter['top'] and not is_top:
                        continue
                    if not zky_filter['top'] and is_top:
                        continue
            
            filtered_articles.append(article)
            
            # 限制最终结果数量
            if len(filtered_articles) >= max_results:
                break
        
        return {
            'total_found': len(articles),
            'articles': filtered_articles,
            'filtered_count': len(filtered_articles),
            'excluded_no_issn': excluded_no_issn
        }
    
    def search_and_count_with_filter(self, keywords, max_results=5000, days_back=30, 
                                   jcr_filter=None, zky_filter=None, exclude_no_issn=True, user_email=None):
        """
        搜索并统计文献数量，支持期刊质量筛选，只返回统计结果不获取详细信息
        
        Args:
            keywords: 关键词
            max_results: 最大搜索结果数
            days_back: 搜索天数（固定30天）
            jcr_filter: JCR筛选条件，如 {'quartile': ['Q1', 'Q2']}
            zky_filter: 中科院筛选条件，如 {'category': ['1', '2'], 'top': True}
            exclude_no_issn: 是否排除没有ISSN的文献
            user_email: 用户邮箱，用于PubMed API请求标识
        
        Returns:
            dict: 包含筛选前后数量统计的字典
        """
        # 第一步：搜索获取PMID
        pmids = self.search_articles(keywords, max_results, days_back, user_email)
        
        if not pmids:
            return {
                'total_found': 0,
                'filtered_count': 0,
                'excluded_no_issn': 0,
                'max_searched': max_results
            }
        
        # 检查是否有实际的筛选条件
        has_quality_filter = bool(jcr_filter or zky_filter)
        has_issn_filter = exclude_no_issn
        
        # 如果没有任何筛选条件，直接返回搜索结果统计
        if not has_quality_filter and not has_issn_filter:
            return {
                'total_found': len(pmids),
                'filtered_count': len(pmids),  # 无筛选时等同于总数
                'excluded_no_issn': 0,        # 未执行ISSN筛选
                'max_searched': max_results,
                'no_filter_applied': True      # 标记无筛选条件
            }
        
        # 第二步：只获取ISSN信息用于筛选（轻量级）
        articles = self.get_article_issn_only(pmids)
        
        # 第三步：应用筛选条件并统计
        filtered_count = 0
        excluded_no_issn = 0
        
        for article in articles:
            # 检查是否有ISSN信息
            has_issn = bool(article.get('issn') or article.get('eissn'))
            
            if exclude_no_issn and not has_issn:
                excluded_no_issn += 1
                continue
            
            # 如果没有ISSN但不排除，则计入筛选结果但不应用期刊筛选
            if not has_issn:
                filtered_count += 1
                continue
                
            # 应用JCR筛选
            if jcr_filter:
                # 获取期刊质量信息
                issn = article.get('issn', '')
                eissn = article.get('eissn', '')
                quality_info = self.get_journal_quality(issn, eissn)
                
                jcr_quartile = quality_info.get('jcr_quartile', '')
                if 'quartile' in jcr_filter:
                    if not jcr_quartile or jcr_quartile not in jcr_filter['quartile']:
                        continue
                
                if 'min_if' in jcr_filter:
                    jcr_if = quality_info.get('jcr_if', '')
                    try:
                        if_value = float(jcr_if) if jcr_if else 0
                        if if_value < jcr_filter['min_if']:
                            continue
                    except (ValueError, TypeError):
                        continue
            
            # 应用中科院筛选
            if zky_filter:
                # 如果还没获取质量信息，现在获取
                if 'quality_info' not in locals():
                    issn = article.get('issn', '')
                    eissn = article.get('eissn', '')
                    quality_info = self.get_journal_quality(issn, eissn)
                
                zky_category = quality_info.get('zky_category', '')
                zky_top = quality_info.get('zky_top', '')
                
                if 'category' in zky_filter:
                    if not zky_category or zky_category not in zky_filter['category']:
                        continue
                
                if 'top' in zky_filter:
                    is_top = zky_top == '是'
                    if zky_filter['top'] and not is_top:
                        continue
                    if not zky_filter['top'] and is_top:
                        continue
            
            filtered_count += 1
        
        return {
            'total_found': len(articles),
            'filtered_count': filtered_count,
            'excluded_no_issn': excluded_no_issn,
            'max_searched': max_results,
            'no_filter_applied': False  # 标记已应用筛选条件
        }

# 初始化环境变量同步
def sync_env_to_database():
    """同步环境变量到数据库配置"""
    import os
    worker_id = os.getpid()
    print(f"[Worker {worker_id}] [同步] 开始执行环境变量同步...")
    try:
        with app.app_context():
            # 检查数据库表是否存在
            try:
                # 使用模型查询来检查表是否存在
                SystemSetting.query.first()
            except Exception as e:
                print(f"[Worker {worker_id}] [同步] 数据库表尚未创建，跳过同步")
                return
            
            # 同步 PubMed 相关配置
            pubmed_settings = {
                'pubmed_api_key': os.environ.get('PUBMED_API_KEY'),
                'pubmed_max_results': os.environ.get('PUBMED_MAX_RESULTS'),
                'pubmed_timeout': os.environ.get('PUBMED_TIMEOUT'),
            }
            
            print(f"[同步] 检测到环境变量: {list(k for k,v in pubmed_settings.items() if v)}")
            
            desc_map = {
                'pubmed_api_key': 'PubMed API Key',
                'pubmed_max_results': 'PubMed每次最大检索数量',
                'pubmed_timeout': 'PubMed请求超时时间(秒)',
            }
            
            for key, env_value in pubmed_settings.items():
                if env_value:
                    current_value = SystemSetting.get_setting(key)
                    print(f"[同步] {key}: 环境变量={env_value}, 数据库={current_value}")
                    if current_value != env_value:
                        SystemSetting.set_setting(key, env_value, desc_map.get(key, ''), 'pubmed')
                        print(f"[同步] ✓ 已更新 {key}")
                        app.logger.info(f"已从环境变量同步配置: {key} = {env_value}")
                    else:
                        print(f"[同步] - {key} 无需更新（值相同）")
            
            # 同步 OpenAI 相关配置（如果数据库中没有活跃的 AI 提供商）
            openai_api_key = os.environ.get('OPENAI_API_KEY')
            openai_api_base = os.environ.get('OPENAI_API_BASE', 'https://api.openai.com/v1')
            
            if openai_api_key:
                print(f"[同步] 检测到 OPENAI_API_KEY")
                # 检查是否已存在活跃的 OpenAI 提供商
                existing_provider = AISetting.query.filter_by(provider_name='OpenAI', is_active=True).first()
                
                if not existing_provider:
                    # 如果没有活跃的 OpenAI 配置，创建一个
                    new_provider = AISetting(
                        provider_name='OpenAI',
                        base_url=openai_api_base,
                        is_active=True
                    )
                    new_provider.set_encrypted_api_key(openai_api_key)
                    db.session.add(new_provider)
                    db.session.commit()
                    print(f"[同步] ✓ 已创建 OpenAI 配置: {openai_api_base}")
                    app.logger.info(f"已从环境变量创建 OpenAI 配置: {openai_api_base}")
                    
                    # 自动获取并创建模型列表
                    try:
                        ai_service = AIService()
                        models = ai_service.fetch_models(new_provider)
                        if models:
                            for model_data in models:
                                # 检查模型是否已存在
                                existing_model = AIModel.query.filter_by(
                                    provider_id=new_provider.id,
                                    model_id=model_data['id']
                                ).first()
                                
                                if not existing_model:
                                    new_model = AIModel(
                                        provider_id=new_provider.id,
                                        model_name=model_data['id'],
                                        model_id=model_data['id'],
                                        model_type='general',
                                        is_available=True
                                    )
                                    db.session.add(new_model)
                            
                            db.session.commit()
                            print(f"[同步] ✓ 自动创建了 {len(models)} 个AI模型")
                            app.logger.info(f"自动创建了 {len(models)} 个AI模型")
                        else:
                            print(f"[同步] ⚠ 未能获取到模型列表，请手动刷新")
                    except Exception as e:
                        print(f"[同步] ⚠ 自动获取模型失败: {e}")
                        app.logger.warning(f"自动获取AI模型失败: {e}")
                else:
                    print(f"[同步] - OpenAI 配置已存在，跳过创建")
            
            print(f"[Worker {worker_id}] [同步] 环境变量同步完成")
    except Exception as e:
        print(f"[同步] ✗ 同步失败: {e}")
        app.logger.error(f"同步环境变量失败: {e}")

# 使用 before_first_request 在第一次请求时执行同步
_sync_done = False

@app.before_request
def before_request_sync():
    global _sync_done
    if not _sync_done:
        sync_env_to_database()
        _sync_done = True

# 在应用启动时初始化调度器（对Gunicorn生产环境友好）
@app.before_first_request
def initialize_scheduler():
    """在第一个请求前初始化调度器"""
    try:
        with app.app_context():
            init_scheduler()
    except Exception as e:
        print(f"调度器自动初始化失败: {e}")

# 路由
@app.route('/', methods=['GET', 'POST'])
def index():
    search_results = None
    
    # 处理搜索请求
    if request.method == 'POST' and current_user.is_authenticated:
        try:
            # 获取搜索参数
            keywords = request.form.get('keywords', '').strip()
            
            if keywords:
                # 防止重复提交：检查是否在短时间内有相同的搜索请求
                import time
                current_time = time.time()
                session_key = f'search_{keywords}_{current_user.id}'
                last_search_time = session.get(session_key, 0)
                
                # 调整时间窗口到5秒，防止重复搜索请求
                if current_time - last_search_time < 5:
                    app.logger.warning(f"重复搜索请求被拒绝: {keywords} (用户: {current_user.email}, 间隔: {current_time - last_search_time:.1f}秒)")
                    flash('请不要重复提交搜索请求', 'warning')
                    return render_template_string(get_index_template(), search_results=search_results)
                
                # 记录本次搜索时间
                session[session_key] = current_time
                app.logger.info(f"开始处理搜索请求: {keywords} (用户: {current_user.email})")
                # 从系统设置获取最大结果数
                max_results = int(SystemSetting.get_setting('pubmed_max_results', '200'))
                
                # 获取筛选参数
                exclude_no_issn = request.form.get('exclude_no_issn') == 'on'
                
                # JCR筛选参数
                jcr_filter = None
                jcr_quartiles = request.form.getlist('jcr_quartile')
                min_if = request.form.get('min_if', '').strip()
                
                if jcr_quartiles or min_if:
                    jcr_filter = {}
                    if jcr_quartiles:
                        jcr_filter['quartile'] = jcr_quartiles
                    if min_if:
                        try:
                            jcr_filter['min_if'] = float(min_if)
                        except ValueError:
                            flash('影响因子必须是数字', 'error')
                            return render_template_string(get_index_template(), search_results=search_results)
                
                # 中科院筛选参数
                zky_filter = None
                zky_categories = request.form.getlist('zky_category')
                zky_top_only = request.form.get('zky_top_only') == 'on'
                
                if zky_categories or zky_top_only:
                    zky_filter = {}
                    if zky_categories:
                        zky_filter['category'] = zky_categories
                    if zky_top_only:
                        zky_filter['top'] = True
                
                # 搜索统计固定使用30天
                search_days = 30
                
                # 使用统计搜索方法（只返回数量，不获取详细信息）
                api = PubMedAPI()
                search_stats = api.search_and_count_with_filter(
                    keywords=keywords,
                    max_results=max_results,
                    days_back=search_days,
                    jcr_filter=jcr_filter,
                    zky_filter=zky_filter,
                    exclude_no_issn=exclude_no_issn,
                    user_email=current_user.email
                )
                
                # 检查用户是否已订阅此关键词
                existing_subscription = Subscription.query.filter_by(
                    user_id=current_user.id,
                    keywords=keywords
                ).first()
                
                # 构建搜索结果
                search_results = {
                    'keywords': keywords,
                    'count': search_stats['filtered_count'],
                    'total_found': search_stats['total_found'],
                    'excluded_no_issn': search_stats['excluded_no_issn'],
                    'max_searched': search_stats['max_searched'],
                    'period': f'<span class="badge bg-info" style="font-size: 14px; padding: 8px 12px;">最近{search_days}天</span>',
                    'is_subscribed': existing_subscription is not None,
                    'has_filters': not search_stats.get('no_filter_applied', False),
                    'jcr_filter': jcr_filter,
                    'zky_filter': zky_filter,
                    'exclude_no_issn': exclude_no_issn
                }
                
                log_activity('INFO', 'search', f'搜索: {keywords}, 搜索{search_stats["total_found"]}篇，筛选后{search_stats["filtered_count"]}篇', current_user.id, request.remote_addr)
            else:
                flash('请输入搜索关键词', 'error')
                
        except Exception as e:
            flash(f'搜索失败: {str(e)}', 'error')
            log_activity('ERROR', 'search', f'搜索失败: {str(e)}', current_user.id, request.remote_addr)
    
    return render_template_string(get_index_template(), search_results=search_results)

def get_index_template():
    """获取主页模板"""
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <title>PubMed Literature Push</title>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <link href="https://cdn.bootcdn.net/ajax/libs/bootstrap/5.1.3/css/bootstrap.min.css" rel="stylesheet">
        <link href="https://cdn.bootcdn.net/ajax/libs/font-awesome/6.0.0/css/all.min.css" rel="stylesheet">
    </head>
    <body>
        <nav class="navbar navbar-expand-lg navbar-dark bg-primary">
            <div class="container">
                <a class="navbar-brand" href="/">📚 PubMed Push</a>
                <div class="navbar-nav ms-auto">
                    {% if current_user.is_authenticated %}
                        <a class="nav-link" href="/subscriptions">我的订阅</a>
                        <a class="nav-link" href="/profile">个人设置</a>
                        {% if current_user.is_admin %}
                            <a class="nav-link" href="/admin">
                                <i class="fas fa-cogs"></i> 管理后台
                            </a>
                        {% endif %}
                        <a class="nav-link" href="/logout">退出 ({{current_user.email}})</a>
                    {% else %}
                        <a class="nav-link" href="/login">登录</a>
                        <a class="nav-link" href="/register">注册</a>
                    {% endif %}
                </div>
            </div>
        </nav>
        
        <div class="container mt-4">
            {% with messages = get_flashed_messages(with_categories=true) %}
                {% if messages %}
                    {% for category, message in messages %}
                        <div class="alert alert-{{ 'danger' if category == 'error' else 'success' if category == 'success' else 'info' }} alert-dismissible">
                            {{ message }}
                            <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
                        </div>
                    {% endfor %}
                {% endif %}
            {% endwith %}
            
            {% if current_user.is_authenticated %}
            <div class="row">
                <div class="col-md-4">
                    <div class="card">
                        <div class="card-header">
                            <h5><i class="fas fa-search"></i> 文献搜索</h5>
                        </div>
                        <div class="card-body">
                            <form method="POST" id="searchForm">
                                <!-- 基本搜索 -->
                                <div class="mb-3">
                                    <label class="form-label">关键词</label>
                                    <input type="text" class="form-control" name="keywords" required 
                                           placeholder="输入搜索关键词" value="{{ request.form.get('keywords', '') }}">
                                </div>
                                
                                <!-- 高级搜索选项已由系统设置控制 -->
                                
                                <hr>
                                
                                <!-- 期刊质量筛选 -->
                                <h6><i class="fas fa-filter"></i> 期刊质量筛选</h6>
                                
                                <div class="mb-3">
                                    <div class="form-check">
                                        <input class="form-check-input" type="checkbox" name="exclude_no_issn" checked>
                                        <label class="form-check-label">排除无ISSN信息的文献</label>
                                    </div>
                                </div>
                                
                                <!-- JCR筛选 -->
                                <div class="mb-3">
                                    <label class="form-label">JCR分区筛选</label>
                                    <div class="row">
                                        {% for quartile in ['Q1', 'Q2', 'Q3', 'Q4'] %}
                                        <div class="col-6">
                                            <div class="form-check">
                                                <input class="form-check-input" type="checkbox" name="jcr_quartile" value="{{ quartile }}">
                                                <label class="form-check-label">{{ quartile }}</label>
                                            </div>
                                        </div>
                                        {% endfor %}
                                    </div>
                                </div>
                                
                                <div class="mb-3">
                                    <label class="form-label">最小影响因子</label>
                                    <input type="number" class="form-control" name="min_if" step="0.1" 
                                           placeholder="如 1.5">
                                </div>
                                
                                <!-- 中科院筛选 -->
                                <div class="mb-3">
                                    <label class="form-label">中科院分区筛选</label>
                                    <div class="row">
                                        {% for category in ['1', '2', '3', '4'] %}
                                        <div class="col-6">
                                            <div class="form-check">
                                                <input class="form-check-input" type="checkbox" name="zky_category" value="{{ category }}">
                                                <label class="form-check-label">{{ category }}区</label>
                                            </div>
                                        </div>
                                        {% endfor %}
                                    </div>
                                </div>
                                
                                <div class="mb-3">
                                    <div class="form-check">
                                        <input class="form-check-input" type="checkbox" name="zky_top_only">
                                        <label class="form-check-label">只显示Top期刊</label>
                                    </div>
                                </div>
                                
                                
                                <button type="submit" class="btn btn-primary w-100" onclick="disableSearchButton(this)">
                                    <i class="fas fa-search"></i> 搜索文献
                                </button>
                            </form>
                        </div>
                    </div>
                </div>
                
                <div class="col-md-8">
                    {% if search_results %}
                        <!-- 搜索结果 -->
                        <div class="card">
                            <div class="card-header">
                                <h5><i class="fas fa-chart-bar"></i> 搜索统计结果</h5>
                                <div class="d-flex justify-content-between align-items-center">
                                    <div>
                                        <h4 class="mb-0">
                                            关键词: <span class="text-primary">{{ search_results.keywords }}</span>
                                        </h4>
                                        <small class="text-muted">{{ search_results.period|safe }}</small>
                                    </div>
                                    {% if not search_results.is_subscribed %}
                                        <form method="POST" action="/subscribe_keyword" class="d-inline">
                                            <input type="hidden" name="keywords" value="{{ search_results.keywords }}">
                                            
                                            <!-- 传递筛选参数 -->
                                            <input type="hidden" name="exclude_no_issn" value="{{ 'on' if request.form.get('exclude_no_issn') else '' }}">
                                            {% for quartile in request.form.getlist('jcr_quartile') %}
                                            <input type="hidden" name="jcr_quartile" value="{{ quartile }}">
                                            {% endfor %}
                                            <input type="hidden" name="min_if" value="{{ request.form.get('min_if', '') }}">
                                            {% for category in request.form.getlist('zky_category') %}
                                            <input type="hidden" name="zky_category" value="{{ category }}">
                                            {% endfor %}
                                            <input type="hidden" name="zky_top_only" value="{{ 'on' if request.form.get('zky_top_only') else '' }}">
                                            
                                            <button type="submit" class="btn btn-success">
                                                <i class="fas fa-bell"></i> 立即订阅
                                            </button>
                                        </form>
                                    {% else %}
                                        <span class="badge bg-secondary p-2">
                                            <i class="fas fa-check-circle"></i> 已订阅
                                        </span>
                                    {% endif %}
                                </div>
                            </div>
                            <div class="card-body">
                                <!-- 统计数据展示 -->
                                <div class="row text-center mb-4">
                                    <div class="col-md-4">
                                        <div class="p-3 border rounded">
                                            <h3 class="text-primary mb-0">
                                                {% if search_results.total_found >= search_results.max_searched %}
                                                    {{ search_results.max_searched }}+
                                                {% else %}
                                                    {{ search_results.total_found }}
                                                {% endif %}
                                            </h3>
                                            <small class="text-muted">总搜索结果</small>
                                            {% if search_results.total_found >= search_results.max_searched %}
                                                <br><small class="text-warning">(实际可能更多)</small>
                                            {% endif %}
                                        </div>
                                    </div>
                                    <div class="col-md-4">
                                        <div class="p-3 border rounded">
                                            <h3 class="text-success mb-0">{{ search_results.count }}</h3>
                                            <small class="text-muted">
                                                {% if search_results.has_filters %}
                                                    筛选后符合条件
                                                {% else %}
                                                    符合条件文献
                                                {% endif %}
                                            </small>
                                        </div>
                                    </div>
                                    {% if search_results.excluded_no_issn > 0 %}
                                    <div class="col-md-4">
                                        <div class="p-3 border rounded">
                                            <h3 class="text-secondary mb-0">{{ search_results.excluded_no_issn }}</h3>
                                            <small class="text-muted">排除无ISSN文献</small>
                                        </div>
                                    </div>
                                    {% endif %}
                                </div>
                                
                                <!-- 筛选条件说明 -->
                                {% if search_results.has_filters %}
                                <div class="alert alert-info">
                                    <h6><i class="fas fa-filter"></i> 已应用筛选条件</h6>
                                    <div class="mb-2">
                                        {% if search_results.exclude_no_issn %}
                                            <span class="badge bg-secondary me-1">排除无ISSN文献</span>
                                        {% endif %}
                                        {% if search_results.jcr_filter and search_results.jcr_filter.quartile %}
                                            {% for q in search_results.jcr_filter.quartile %}
                                                <span class="badge bg-warning text-dark me-1">JCR {{ q }}</span>
                                            {% endfor %}
                                        {% endif %}
                                        {% if search_results.jcr_filter and search_results.jcr_filter.min_if %}
                                            <span class="badge bg-warning text-dark me-1">影响因子 ≥ {{ search_results.jcr_filter.min_if }}</span>
                                        {% endif %}
                                        {% if search_results.zky_filter and search_results.zky_filter.category %}
                                            {% for cat in search_results.zky_filter.category %}
                                                <span class="badge bg-success me-1">中科院{{ cat }}区</span>
                                            {% endfor %}
                                        {% endif %}
                                        {% if search_results.zky_filter and search_results.zky_filter.top %}
                                            <span class="badge bg-danger me-1">中科院Top期刊</span>
                                        {% endif %}
                                    </div>
                                    <p class="mb-0 small">上述统计结果已根据您选择的期刊质量条件进行筛选。订阅后将按相同条件推送符合要求的最新文献。</p>
                                </div>
                                {% endif %}
                                
                                <div class="text-center">
                                    <p class="text-muted mb-0">
                                        <i class="fas fa-info-circle"></i> 
                                        这是文献数量统计结果。如需查看具体文献详情，请使用订阅功能接收推送。
                                    </p>
                                </div>
                            </div>
                        </div>
                    {% else %}
                        <div class="card">
                            <div class="card-body text-center">
                                <i class="fas fa-search fa-3x mb-3 text-muted"></i>
                                <h5>开始您的文献搜索</h5>
                                <p class="text-muted">输入关键词并设置筛选条件，获取高质量期刊文献统计</p>
                            </div>
                        </div>
                    {% endif %}
                </div>
            </div>
            {% else %}
                <!-- 未登录用户的欢迎页面 -->
                <div class="row">
                    <div class="col-lg-8 mx-auto">
                        <div class="card">
                            <div class="card-body text-center py-5">
                                <h2 class="mb-4">🚀 欢迎使用 PubMed Literature Push</h2>
                                <p class="lead mb-4">智能文献推送系统，支持JCR和中科院期刊质量筛选</p>
                                <div class="row text-start">
                                    <div class="col-md-6">
                                        <div class="d-flex align-items-start mb-3">
                                            <span class="fs-4 me-3">🔐</span>
                                            <div>
                                                <strong class="text-info">注册/登录</strong>
                                                <div class="text-muted small">创建账户开始使用</div>
                                            </div>
                                        </div>
                                    </div>
                                    <div class="col-md-6">
                                        <div class="d-flex align-items-start mb-3">
                                            <span class="fs-4 me-3">🔍</span>
                                            <div>
                                                <strong class="text-info">智能搜索</strong>
                                                <div class="text-muted small">支持期刊质量筛选的文献搜索</div>
                                            </div>
                                        </div>
                                    </div>
                                    <div class="col-md-6">
                                        <div class="d-flex align-items-start mb-3">
                                            <span class="fs-4 me-3">📬</span>
                                            <div>
                                                <strong class="text-info">推送订阅</strong>
                                                <div class="text-muted small">自动跟踪关键词，定时推送最新文献</div>
                                            </div>
                                        </div>
                                    </div>
                                    <div class="col-md-6">
                                        <div class="d-flex align-items-start">
                                            <span class="fs-4 me-3">📋</span>
                                            <div>
                                                <strong class="text-info">订阅管理</strong>
                                                <div class="text-muted small">灵活管理推送时间和频率</div>
                                            </div>
                                        </div>
                                    </div>
                                </div>
                                
                                <div class="text-center mt-4">
                                    <a href="/login" class="btn btn-primary btn-lg me-3">
                                        <i class="fas fa-sign-in-alt"></i> 立即登录
                                    </a>
                                    <a href="/register" class="btn btn-outline-primary btn-lg">
                                        <i class="fas fa-user-plus"></i> 免费注册
                                    </a>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            {% endif %}
        </div>
        
        <!-- JavaScript -->
        <script>
        // 删除搜索模式切换功能，因为现在只有一种搜索模式
        
        // 防止重复提交搜索表单
        function disableSearchButton(button) {
            button.disabled = true;
            button.innerHTML = '<i class="fas fa-spinner fa-spin"></i> 搜索中...';
            // 避免禁用按钮导致表单无法提交
            setTimeout(function() {
                button.closest('form').submit();
            }, 100);
        }
        </script>
        <script src="https://cdn.bootcdn.net/ajax/libs/bootstrap/5.1.3/js/bootstrap.bundle.min.js"></script>
    </body>
    </html>
    """

@app.route('/unsubscribe', methods=['POST'])
@login_required
def unsubscribe():
    # Unsubscribe from keywords
    keywords = request.form.get('keywords', '').strip()
    
    if not keywords:
        flash('关键词不能为空', 'warning')
        return redirect(url_for('index'))
    
    # 查找并删除订阅
    subscription = Subscription.query.filter_by(
        user_id=current_user.id,
        keywords=keywords
    ).first()
    
    if subscription:
        db.session.delete(subscription)
        db.session.commit()
        log_activity('INFO', 'subscription', f'用户 {current_user.email} 取消订阅关键词: {keywords}', current_user.id, request.remote_addr)
        flash(f'成功取消订阅关键词: {keywords}', 'success')
    else:
        flash('未找到该订阅', 'warning')
    
    return redirect(url_for('index'))


@app.route('/subscribe', methods=['POST'])
@login_required
def subscribe():
    """订阅关键词"""
    keywords = request.form.get('keywords', '').strip()
    
    if not keywords:
        flash('关键词不能为空', 'error')
        return redirect(url_for('index'))
    
    # 检查是否已经订阅
    existing_subscription = Subscription.query.filter_by(
        user_id=current_user.id,
        keywords=keywords
    ).first()
    
    if existing_subscription:
        flash(f'您已经订阅了关键词: {keywords}', 'info')
        return redirect(url_for('index'))
    
    try:
        # 创建新订阅
        subscription = Subscription(
            user_id=current_user.id,
            keywords=keywords,
            is_active=True
        )
        db.session.add(subscription)
        db.session.commit()
        
        log_activity('INFO', 'subscription', f'用户 {current_user.email} 订阅关键词: {keywords}', current_user.id, request.remote_addr)
        flash(f'成功订阅关键词: {keywords}', 'success')
        
    except Exception as e:
        db.session.rollback()
        log_activity('ERROR', 'subscription', f'订阅失败: {keywords} - {str(e)}', current_user.id, request.remote_addr)
        flash(f'订阅失败: {str(e)}', 'error')
    
    return redirect(url_for('index'))

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        
        if User.query.filter_by(email=email).first():
            flash('邮箱已存在')
            return redirect(url_for('register'))
        
        user = User(email=email)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        
        log_activity('INFO', 'auth', f'用户注册成功: {email}', user.id, request.remote_addr)
        flash('注册成功！请登录')
        return redirect(url_for('login'))
    
    template = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>注册 - PubMed Push</title>
        <meta charset="utf-8">
        <link href="https://cdn.bootcdn.net/ajax/libs/bootstrap/5.1.3/css/bootstrap.min.css" rel="stylesheet">
    </head>
    <body>
        <div class="container mt-5">
            <div class="row justify-content-center">
                <div class="col-md-6">
                    <div class="card">
                        <div class="card-header"><h4>用户注册</h4></div>
                        <div class="card-body">
                            <form method="POST">
                                <div class="mb-3">
                                    <label for="email" class="form-label">邮箱</label>
                                    <input type="email" class="form-control" id="email" name="email" required>
                                </div>
                                <div class="mb-3">
                                    <label for="password" class="form-label">密码</label>
                                    <input type="password" class="form-control" id="password" name="password" required>
                                </div>
                                <button type="submit" class="btn btn-primary">注册</button>
                                <a href="/login" class="btn btn-link">已有账户？登录</a>
                            </form>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    </body>
    </html>
    """
    return render_template_string(template)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        
        user = User.query.filter_by(email=email).first()
        if user and user.check_password(password):
            login_user(user)
            log_activity('INFO', 'auth', f'用户登录成功: {email}', user.id, request.remote_addr)
            return redirect(url_for('index'))
        else:
            log_activity('WARNING', 'auth', f'登录失败: {email}', None, request.remote_addr)
            flash('邮箱或密码错误')
    
    template = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>登录 - PubMed Push</title>
        <meta charset="utf-8">
        <link href="https://cdn.bootcdn.net/ajax/libs/bootstrap/5.1.3/css/bootstrap.min.css" rel="stylesheet">
    </head>
    <body>
        <div class="container mt-5">
            <div class="row justify-content-center">
                <div class="col-md-6">
                    <div class="card">
                        <div class="card-header"><h4>用户登录</h4></div>
                        <div class="card-body">
                            {% with messages = get_flashed_messages() %}
                                {% if messages %}
                                    {% for message in messages %}
                                        <div class="alert alert-warning">{{ message }}</div>
                                    {% endfor %}
                                {% endif %}
                            {% endwith %}
                            <form method="POST">
                                <div class="mb-3">
                                    <label for="email" class="form-label">邮箱</label>
                                    <input type="email" class="form-control" id="email" name="email" required>
                                </div>
                                <div class="mb-3">
                                    <label for="password" class="form-label">密码</label>
                                    <input type="password" class="form-control" id="password" name="password" required>
                                </div>
                                <button type="submit" class="btn btn-primary">登录</button>
                                <a href="/register" class="btn btn-link">没有账户？注册</a>
                                <a href="/forgot_password" class="btn btn-link">忘记密码？</a>
                            </form>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    </body>
    </html>
    """
    return render_template_string(template)

@app.route('/forgot_password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        email = request.form.get('email')
        user = User.query.filter_by(email=email).first()
        
        if user:
            # 生成重置令牌
            token = user.generate_reset_token()
            
            # 发送重置邮件
            reset_url = url_for('reset_password', token=token, _external=True)
            subject = "PubMed Literature Push - 密码重置"
            
            html_body = f"""
            <div style="max-width: 600px; margin: 0 auto; font-family: Arial, sans-serif;">
                <h2 style="color: #0d6efd;">密码重置请求</h2>
                <p>您好，</p>
                <p>我们收到了您重置密码的请求。请点击下面的链接来重置您的密码：</p>
                <p style="margin: 20px 0;">
                    <a href="{reset_url}" style="background-color: #0d6efd; color: white; padding: 12px 24px; text-decoration: none; border-radius: 5px; display: inline-block;">重置密码</a>
                </p>
                <p>或者复制以下链接到浏览器：</p>
                <p style="word-break: break-all; background-color: #f8f9fa; padding: 10px; border-radius: 5px;">{reset_url}</p>
                <p style="color: #dc3545; font-weight: bold;">重要提醒：</p>
                <ul style="color: #dc3545;">
                    <li>此链接将在1小时后失效</li>
                    <li>如果您没有请求重置密码，请忽略此邮件</li>
                    <li>为了账户安全，请不要将此链接分享给他人</li>
                </ul>
                <p>如有问题，请联系系统管理员。</p>
                <hr style="margin: 20px 0; border: 1px solid #dee2e6;">
                <p style="color: #6c757d; font-size: 12px;">
                    此邮件由 PubMed Literature Push 系统自动发送，请勿直接回复。
                </p>
            </div>
            """
            
            text_body = f"""
            密码重置请求
            
            您好，
            
            我们收到了您重置密码的请求。请访问以下链接来重置您的密码：
            
            {reset_url}
            
            重要提醒：
            - 此链接将在1小时后失效
            - 如果您没有请求重置密码，请忽略此邮件
            - 为了账户安全，请不要将此链接分享给他人
            
            如有问题，请联系系统管理员。
            """
            
            try:
                success = mail_sender.send_email(email, subject, html_body, text_body)
                if success:
                    log_activity('INFO', 'auth', f'密码重置邮件发送成功: {email}', user.id, request.remote_addr)
                    flash('密码重置邮件已发送，请检查您的邮箱')
                else:
                    log_activity('ERROR', 'auth', f'密码重置邮件发送失败: {email}', user.id, request.remote_addr)
                    flash('邮件发送失败，请稍后重试或联系管理员')
            except Exception as e:
                log_activity('ERROR', 'auth', f'密码重置邮件发送异常: {email} - {str(e)}', user.id, request.remote_addr)
                flash('邮件发送失败，请稍后重试或联系管理员')
        else:
            # 即使用户不存在，也显示相同的消息（安全考虑）
            log_activity('WARNING', 'auth', f'尝试重置不存在的用户密码: {email}', None, request.remote_addr)
            flash('密码重置邮件已发送，请检查您的邮箱')
        
        return redirect(url_for('login'))
    
    template = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>忘记密码 - PubMed Push</title>
        <meta charset="utf-8">
        <link href="https://cdn.bootcdn.net/ajax/libs/bootstrap/5.1.3/css/bootstrap.min.css" rel="stylesheet">
    </head>
    <body>
        <div class="container mt-5">
            <div class="row justify-content-center">
                <div class="col-md-6">
                    <div class="card">
                        <div class="card-header"><h4>忘记密码</h4></div>
                        <div class="card-body">
                            <p class="text-muted">请输入您的注册邮箱，我们将发送密码重置链接给您。</p>
                            <form method="POST">
                                <div class="mb-3">
                                    <label for="email" class="form-label">邮箱</label>
                                    <input type="email" class="form-control" id="email" name="email" required>
                                </div>
                                <button type="submit" class="btn btn-primary">发送重置邮件</button>
                                <a href="/login" class="btn btn-link">返回登录</a>
                            </form>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    </body>
    </html>
    """
    return render_template_string(template)

@app.route('/reset_password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    user = User.verify_reset_token(token)
    if not user:
        flash('重置链接无效或已过期')
        return redirect(url_for('forgot_password'))
    
    if request.method == 'POST':
        password = request.form.get('password')
        confirm_password = request.form.get('confirm_password')
        
        if password != confirm_password:
            flash('两次输入的密码不一致')
        elif len(password) < 6:
            flash('密码长度至少6位')
        else:
            # 更新密码
            user.set_password(password)
            
            # 标记令牌为已使用
            reset_token = PasswordResetToken.query.filter_by(token=token, used=False).first()
            if reset_token:
                reset_token.mark_as_used()
            
            db.session.commit()
            log_activity('INFO', 'auth', f'用户密码重置成功: {user.email}', user.id, request.remote_addr)
            flash('密码重置成功，请使用新密码登录')
            return redirect(url_for('login'))
    
    template = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>重置密码 - PubMed Push</title>
        <meta charset="utf-8">
        <link href="https://cdn.bootcdn.net/ajax/libs/bootstrap/5.1.3/css/bootstrap.min.css" rel="stylesheet">
    </head>
    <body>
        <div class="container mt-5">
            <div class="row justify-content-center">
                <div class="col-md-6">
                    <div class="card">
                        <div class="card-header"><h4>重置密码</h4></div>
                        <div class="card-body">
                            {% with messages = get_flashed_messages() %}
                                {% if messages %}
                                    {% for message in messages %}
                                        <div class="alert alert-warning">{{ message }}</div>
                                    {% endfor %}
                                {% endif %}
                            {% endwith %}
                            <p class="text-muted">为账户 <strong>{{ user.email }}</strong> 设置新密码。</p>
                            <form method="POST">
                                <div class="mb-3">
                                    <label for="password" class="form-label">新密码</label>
                                    <input type="password" class="form-control" id="password" name="password" required minlength="6">
                                    <div class="form-text">密码长度至少6位</div>
                                </div>
                                <div class="mb-3">
                                    <label for="confirm_password" class="form-label">确认新密码</label>
                                    <input type="password" class="form-control" id="confirm_password" name="confirm_password" required minlength="6">
                                </div>
                                <button type="submit" class="btn btn-primary">重置密码</button>
                                <a href="/login" class="btn btn-link">返回登录</a>
                            </form>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    </body>
    </html>
    """
    return render_template_string(template, user=user)

@app.route('/logout')
@login_required
def logout():
    log_activity('INFO', 'auth', f'用户登出: {current_user.email}', current_user.id, request.remote_addr)
    logout_user()
    return redirect(url_for('index'))

# 旧的搜索页面路由，现已废弃 - 搜索功能已集成到主页
@app.route('/search', methods=['GET', 'POST'])
@login_required
def search():
    """旧的搜索页面，重定向到主页"""
    # 如果有关键词参数，重定向到主页并保持参数
    keywords = request.form.get('keywords') or request.args.get('keywords')
    if keywords:
        flash(f'搜索功能已集成到主页，请在主页搜索: {keywords}', 'info')
    return redirect(url_for('index'))

@app.route('/subscribe_keyword', methods=['POST'])
@login_required
def subscribe_keyword():
    """订阅关键词"""
    keywords = request.form.get('keywords', '').strip()
    
    if not keywords:
        flash('关键词不能为空', 'warning')
        return redirect(url_for('index'))
    
    # 检查订阅权限（管理员不受限制）
    if not current_user.is_admin and not current_user.can_create_subscription():
        limit_info = current_user.get_subscription_limit_info()
        flash(f'您已达到最大订阅数量限制（{limit_info["current"]}/{limit_info["max"]}），无法创建新订阅', 'warning')
        return redirect(url_for('index'))
    
    # 检查是否已经订阅
    existing_subscription = Subscription.query.filter_by(
        user_id=current_user.id, 
        keywords=keywords
    ).first()
    
    if existing_subscription:
        flash('您已经订阅了此关键词', 'info')
    else:
        # 创建新订阅，包含筛选参数
        subscription = Subscription(user_id=current_user.id, keywords=keywords)
        
        # 使用系统默认设置
        subscription.max_results = int(SystemSetting.get_setting('pubmed_max_results', '200'))
        subscription.exclude_no_issn = request.form.get('exclude_no_issn') == 'on'
        
        # 设置JCR筛选参数
        jcr_quartiles = request.form.getlist('jcr_quartile')
        if jcr_quartiles:
            subscription.set_jcr_quartiles(jcr_quartiles)
        
        min_if = request.form.get('min_if', '').strip()
        if min_if:
            try:
                subscription.min_impact_factor = float(min_if)
            except ValueError:
                pass
        
        # 设置中科院筛选参数
        cas_categories = request.form.getlist('zky_category')
        if cas_categories:
            subscription.set_cas_categories(cas_categories)
        
        subscription.cas_top_only = request.form.get('zky_top_only') == 'on'
        
        # 使用用户的个人推送偏好设置，但要检查频率权限
        user_frequency = current_user.push_frequency or SystemSetting.get_setting('push_frequency', 'daily')
        
        # 检查用户是否有权使用该推送频率（管理员不受限制）
        allowed_frequencies = current_user.get_allowed_frequencies()
        if not current_user.is_admin and user_frequency not in allowed_frequencies:
            # 如果用户个人设置的频率不被允许，使用第一个允许的频率
            user_frequency = allowed_frequencies[0]
            flash(f'您的个人推送频率设置不被允许，已自动设置为: {user_frequency}', 'info')
        
        subscription.push_frequency = user_frequency
        subscription.push_time = current_user.push_time or SystemSetting.get_setting('push_time', '09:00')
        subscription.push_day = current_user.push_day or SystemSetting.get_setting('push_day', 'monday')
        subscription.push_month_day = current_user.push_month_day or int(SystemSetting.get_setting('push_month_day', '1'))
        
        # 根据推送频率设置搜索天数
        subscription.days_back = get_search_days_by_frequency(subscription.push_frequency)
        
        db.session.add(subscription)
        db.session.commit()
        log_activity('INFO', 'subscription', f'用户 {current_user.email} 订阅关键词: {keywords}', current_user.id, request.remote_addr)
        flash(f'成功订阅关键词: {keywords}', 'success')
    
    return redirect(url_for('subscriptions'))

@app.route('/unsubscribe_keyword', methods=['POST'])
@login_required
def unsubscribe_keyword():
    """取消订阅关键词"""
    keywords = request.form.get('keywords', '').strip()
    
    if not keywords:
        flash('关键词不能为空', 'warning')
        return redirect(url_for('index'))
    
    subscription = Subscription.query.filter_by(
        user_id=current_user.id, 
        keywords=keywords
    ).first()
    
    if subscription:
        db.session.delete(subscription)
        db.session.commit()
        log_activity('INFO', 'subscription', f'用户 {current_user.email} 取消订阅关键词: {keywords}', current_user.id, request.remote_addr)
        flash(f'已取消订阅关键词: {keywords}', 'info')
    else:
        flash('您没有订阅此关键词', 'warning')
    
    # 重新搜索并显示结果
    from urllib.parse import urlencode
    return redirect(url_for('search') + '?' + urlencode({'keywords': keywords}))

@app.route('/subscriptions')
@login_required
def subscriptions():
    user_subscriptions = Subscription.query.filter_by(user_id=current_user.id).order_by(Subscription.created_at.desc()).all()
    
    template = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>我的订阅 - PubMed Push</title>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <link href="https://cdn.bootcdn.net/ajax/libs/bootstrap/5.1.3/css/bootstrap.min.css" rel="stylesheet">
        <link href="https://cdn.bootcdn.net/ajax/libs/font-awesome/6.0.0/css/all.min.css" rel="stylesheet">
    </head>
    <body>
        <nav class="navbar navbar-expand-lg navbar-dark bg-primary">
            <div class="container">
                <a class="navbar-brand" href="/">📚 PubMed Push</a>
                <div class="navbar-nav ms-auto">
                    <a class="nav-link" href="/">首页</a>
                    <a class="nav-link active" href="/subscriptions">我的订阅</a>
                    <a class="nav-link" href="/profile">个人设置</a>
                    {% if current_user.is_admin %}
                        <a class="nav-link" href="/admin">
                            <i class="fas fa-cogs"></i> 管理后台
                        </a>
                    {% endif %}
                    <a class="nav-link" href="/logout">退出 ({{current_user.email}})</a>
                </div>
            </div>
        </nav>
        
        <div class="container mt-4">
            <div class="d-flex justify-content-between align-items-center mb-4">
                <h4><i class="fas fa-bell"></i> 我的订阅管理</h4>
                <a href="/" class="btn btn-primary">
                    <i class="fas fa-plus"></i> 添加订阅
                </a>
            </div>
            
            {% with messages = get_flashed_messages(with_categories=true) %}
                {% if messages %}
                    {% for category, message in messages %}
                        <div class="alert alert-{{ 'danger' if category == 'error' else 'success' if category == 'success' else 'info' }} alert-dismissible">
                            {{ message }}
                            <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
                        </div>
                    {% endfor %}
                {% endif %}
            {% endwith %}
            
            {% if subscriptions %}
                <!-- 订阅管理表格 -->
                <div class="card">
                    <div class="card-header">
                        <h5><i class="fas fa-list"></i> 订阅列表与推送设置</h5>
                        <p class="mb-0 text-muted small">管理您的订阅关键词和推送参数设置</p>
                    </div>
                    <div class="card-body">
                        <div class="table-responsive">
                            <table class="table table-hover">
                                <thead>
                                    <tr>
                                        <th>关键词</th>
                                        <th>最大结果数</th>
                                        <th>搜索天数</th>
                                        <th>推送频率</th>
                                        <th>推送时间</th>
                                        <th>期刊筛选</th>
                                        <th>状态</th>
                                        <th>操作</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {% for subscription in subscriptions %}
                                    <tr>
                                        <td>
                                            <strong class="text-primary">{{ subscription.keywords }}</strong>
                                            <br><small class="text-muted">订阅于: {{ subscription.created_at.strftime('%Y-%m-%d %H:%M') }}</small>
                                        </td>
                                        <td>
                                            <span class="badge bg-info">{{ subscription.max_results }}篇</span>
                                        </td>
                                        <td>
                                            <span class="badge bg-secondary">{{ subscription.days_back }}天</span>
                                        </td>
                                        <td>
                                            {% if subscription.push_frequency == 'daily' %}
                                                <span class="badge bg-success">每日</span>
                                            {% elif subscription.push_frequency == 'weekly' %}
                                                <span class="badge bg-warning">每周 {{ subscription.push_day|title }}</span>
                                            {% elif subscription.push_frequency == 'monthly' %}
                                                <span class="badge bg-primary">每月 {{ subscription.push_month_day }}号</span>
                                            {% endif %}
                                        </td>
                                        <td>
                                            <span class="text-muted">{{ subscription.push_time or '09:00' }}</span>
                                        </td>
                                        <td>
                                            <div class="d-flex flex-wrap gap-1">
                                                {% set jcr_quartiles = subscription.get_jcr_quartiles() %}
                                                {% set cas_categories = subscription.get_cas_categories() %}
                                                
                                                {% if jcr_quartiles %}
                                                    <small class="badge bg-light text-dark">JCR: {{ jcr_quartiles|join(',') }}</small>
                                                {% endif %}
                                                
                                                {% if cas_categories %}
                                                    <small class="badge bg-light text-dark">
                                                        中科院: {{ cas_categories|join(',') }}区
                                                        {% if subscription.cas_top_only %} Top{% endif %}
                                                    </small>
                                                {% endif %}
                                                
                                                {% if subscription.min_impact_factor %}
                                                    <small class="badge bg-light text-dark">IF≥{{ subscription.min_impact_factor }}</small>
                                                {% endif %}
                                                
                                                {% if subscription.exclude_no_issn %}
                                                    <small class="badge bg-light text-dark">排除无ISSN</small>
                                                {% endif %}
                                            </div>
                                        </td>
                                        <td>
                                            {% if subscription.is_active %}
                                                <span class="badge bg-success">活跃</span>
                                            {% else %}
                                                <span class="badge bg-secondary">已停用</span>
                                            {% endif %}
                                        </td>
                                        <td>
                                            <div class="btn-group" role="group">
                                                <a href="/edit_subscription/{{ subscription.id }}" 
                                                   class="btn btn-sm btn-outline-primary" 
                                                   title="编辑订阅设置">
                                                    <i class="fas fa-edit"></i>
                                                </a>
                                                <a href="/search_subscription/{{ subscription.id }}" 
                                                   class="btn btn-sm btn-outline-info" 
                                                   title="测试搜索">
                                                    <i class="fas fa-search"></i>
                                                </a>
                                                <a href="/delete_subscription/{{ subscription.id }}" 
                                                   class="btn btn-sm btn-outline-danger" 
                                                   onclick="return confirm('确定删除此订阅？')"
                                                   title="删除订阅">
                                                    <i class="fas fa-trash"></i>
                                                </a>
                                            </div>
                                        </td>
                                    </tr>
                                    {% endfor %}
                                </tbody>
                            </table>
                        </div>
                    </div>
                </div>
                
                <div class="mt-4">
                    <div class="row">
                        <div class="col-md-4">
                            <div class="card text-center">
                                <div class="card-body">
                                    <h5 class="card-title text-primary">{{ subscriptions|length }}</h5>
                                    <p class="card-text">总订阅数</p>
                                </div>
                            </div>
                        </div>
                        <div class="col-md-4">
                            <div class="card text-center">
                                <div class="card-body">
                                    <h5 class="card-title text-success">{{ subscriptions|selectattr('is_active')|list|length }}</h5>
                                    <p class="card-text">活跃订阅</p>
                                </div>
                            </div>
                        </div>
                        <div class="col-md-4">
                            <div class="card text-center">
                                <div class="card-body">
                                    <h5 class="card-title text-warning">{{ subscriptions|selectattr('push_frequency', 'equalto', 'daily')|list|length }}</h5>
                                    <p class="card-text">每日推送</p>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
                
            {% else %}
                <div class="text-center py-5">
                    <div class="card">
                        <div class="card-body">
                            <i class="fas fa-inbox fa-4x text-muted mb-3"></i>
                            <h5>还没有任何订阅</h5>
                            <p class="text-muted">开始订阅感兴趣的研究关键词，获取最新文献推送</p>
                            <a href="/" class="btn btn-primary">
                                <i class="fas fa-search"></i> 开始搜索订阅
                            </a>
                        </div>
                    </div>
                </div>
            {% endif %}
        </div>
        
        <script src="https://cdn.bootcdn.net/ajax/libs/bootstrap/5.1.3/js/bootstrap.bundle.min.js"></script>
    </body>
    </html>
    """
    return render_template_string(template, subscriptions=user_subscriptions)

@app.route('/delete_subscription/<int:sub_id>')
@login_required
def delete_subscription(sub_id):
    subscription = Subscription.query.filter_by(id=sub_id, user_id=current_user.id).first()
    if subscription:
        keywords = subscription.keywords
        
        # 先更新相关的UserArticle记录，将subscription_id设为NULL
        user_articles = UserArticle.query.filter_by(subscription_id=sub_id).all()
        for user_article in user_articles:
            user_article.subscription_id = None
        
        # 删除订阅
        db.session.delete(subscription)
        db.session.commit()
        
        log_activity('INFO', 'subscription', f'用户 {current_user.email} 删除订阅: {keywords}', current_user.id, request.remote_addr)
        flash('订阅已删除', 'info')
    return redirect(url_for('subscriptions'))

@app.route('/search_subscription/<int:sub_id>')
@login_required
def search_subscription(sub_id):
    subscription = Subscription.query.filter_by(id=sub_id, user_id=current_user.id).first()
    if subscription:
        # 模拟搜索表单提交
        from werkzeug.datastructures import ImmutableMultiDict
        form_data = ImmutableMultiDict([('keywords', subscription.keywords), ('action', 'search')])
        
        # 创建临时request对象
        with app.test_request_context(method='POST', data=form_data):
            return search()
    
    flash('订阅不存在', 'warning')
    return redirect(url_for('subscriptions'))

# 管理员路由
@app.route('/admin')
@admin_required
def admin_dashboard():
    """管理员仪表板"""
    # 直接在路由中获取统计数据，避免AdminUtils导入问题
    try:
        # 获取用户统计
        total_users = db.session.execute(db.text("SELECT COUNT(*) FROM user")).scalar()
        active_users = db.session.execute(db.text("SELECT COUNT(*) FROM user WHERE is_active = 1")).scalar() or 0
        admin_users = db.session.execute(db.text("SELECT COUNT(*) FROM user WHERE is_admin = 1")).scalar() or 0
        total_subscriptions = db.session.execute(db.text("SELECT COUNT(*) FROM subscription")).scalar() or 0
        total_articles = db.session.execute(db.text("SELECT COUNT(*) FROM article")).scalar() or 0
        
        stats = {
            'total_users': total_users or 0,
            'active_users': active_users or 0,
            'admin_users': admin_users or 0,
            'total_subscriptions': total_subscriptions or 0,
            'total_articles': total_articles or 0
        }
    except Exception as e:
        # 如果查询失败，返回默认值
        print(f"获取统计数据失败: {e}")
        stats = {
            'total_users': 0,
            'active_users': 0,
            'admin_users': 0,
            'total_subscriptions': 0,
            'total_articles': 0
        }
    
    # 获取最近用户 - 也直接查询
    try:
        result = db.session.execute(
            db.text("SELECT id, email, is_admin, is_active, created_at FROM user ORDER BY created_at DESC LIMIT 5")
        ).fetchall()
        
        recent_users = []
        for row in result:
            # 处理创建时间，确保兼容性
            created_at = row[4]
            if isinstance(created_at, str):
                from datetime import datetime
                try:
                    created_at = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                except:
                    created_at = None
            
            user = type('User', (), {
                'id': row[0],
                'email': row[1],
                'is_admin': bool(row[2]),
                'is_active': bool(row[3]) if row[3] is not None else True,
                'created_at': created_at
            })()
            recent_users.append(user)
    except Exception as e:
        print(f"获取最近用户失败: {e}")
        recent_users = []
    
    # 获取活跃订阅 - 也直接查询
    try:
        result = db.session.execute(
            db.text("""
                SELECT s.id, s.keywords, s.created_at, u.email 
                FROM subscription s 
                LEFT JOIN user u ON s.user_id = u.id 
                ORDER BY s.created_at DESC 
                LIMIT 10
            """)
        ).fetchall()
        
        active_subscriptions = []
        for row in result:
            # 处理创建时间
            created_at = row[2]
            if isinstance(created_at, str):
                from datetime import datetime
                try:
                    created_at = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                except:
                    created_at = None
            
            user = type('User', (), {'email': row[3]})() if row[3] else None
            subscription = type('Subscription', (), {
                'id': row[0],
                'keywords': row[1],
                'created_at': created_at,
                'user': user
            })()
            active_subscriptions.append(subscription)
    except Exception as e:
        print(f"获取活跃订阅失败: {e}")
        active_subscriptions = []
    
    template = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>管理员面板 - PubMed Literature Push</title>
        <link href="https://cdn.bootcdn.net/ajax/libs/bootstrap/5.1.3/css/bootstrap.min.css" rel="stylesheet">
    </head>
    <body>
        <nav class="navbar navbar-expand-lg navbar-dark bg-primary">
            <div class="container">
                <a class="navbar-brand" href="/">PubMed Literature Push</a>
                <div class="navbar-nav ms-auto">
                    <a class="nav-link" href="/">首页</a>
                    <a class="nav-link" href="/subscriptions">我的订阅</a>
                    <a class="nav-link active" href="/admin">管理员</a>
                    <a class="nav-link" href="/logout">退出</a>
                </div>
            </div>
        </nav>

        <div class="container mt-4">
            <h2>管理员仪表板</h2>
            <p class="text-muted">欢迎，{{ current_user.email }} (管理员)</p>
            
            <!-- 管理员消息显示 -->
            {% with messages = get_flashed_messages(category_filter=['admin']) %}
                {% if messages %}
                    {% for message in messages %}
                        <div class="alert alert-info alert-dismissible fade show" role="alert">
                            {{ message }}
                            <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
                        </div>
                    {% endfor %}
                {% endif %}
            {% endwith %}
            
            <!-- 统计信息 -->
            <div class="row mb-4">
                <div class="col-md-2">
                    <div class="card text-center">
                        <div class="card-body">
                            <h5 class="card-title">{{ stats.total_users }}</h5>
                            <p class="card-text">总用户数</p>
                        </div>
                    </div>
                </div>
                <div class="col-md-2">
                    <div class="card text-center">
                        <div class="card-body">
                            <h5 class="card-title">{{ stats.active_users }}</h5>
                            <p class="card-text">活跃用户</p>
                        </div>
                    </div>
                </div>
                <div class="col-md-2">
                    <div class="card text-center">
                        <div class="card-body">
                            <h5 class="card-title">{{ stats.admin_users }}</h5>
                            <p class="card-text">管理员</p>
                        </div>
                    </div>
                </div>
                <div class="col-md-3">
                    <div class="card text-center">
                        <div class="card-body">
                            <h5 class="card-title">{{ stats.total_subscriptions }}</h5>
                            <p class="card-text">总订阅数</p>
                        </div>
                    </div>
                </div>
                <div class="col-md-3">
                    <div class="card text-center">
                        <div class="card-body">
                            <h5 class="card-title">{{ stats.total_articles }}</h5>
                            <p class="card-text">文章总数</p>
                        </div>
                    </div>
                </div>
            </div>
            
            <!-- 管理功能 -->
            <div class="row">
                <div class="col-md-6">
                    <div class="card">
                        <div class="card-header">
                            <h5>最近注册用户</h5>
                        </div>
                        <div class="card-body">
                            {% if recent_users %}
                                <div class="table-responsive">
                                    <table class="table table-sm">
                                        <thead>
                                            <tr>
                                                <th>邮箱</th>
                                                <th>注册时间</th>
                                                <th>状态</th>
                                                <th>操作</th>
                                            </tr>
                                        </thead>
                                        <tbody>
                                            {% for user in recent_users %}
                                            <tr>
                                                <td>{{ user.email }}</td>
                                                <td>{{ user.created_at.strftime('%m-%d') if user.created_at else 'N/A' }}</td>
                                                <td>
                                                    {% if user.is_admin %}
                                                        <span class="badge bg-danger">管理员</span>
                                                    {% elif user.is_active %}
                                                        <span class="badge bg-success">活跃</span>
                                                    {% else %}
                                                        <span class="badge bg-secondary">禁用</span>
                                                    {% endif %}
                                                </td>
                                                <td>
                                                    <a href="/admin/users/{{ user.id }}" class="btn btn-sm btn-outline-primary">管理</a>
                                                </td>
                                            </tr>
                                            {% endfor %}
                                        </tbody>
                                    </table>
                                </div>
                                <a href="/admin/users" class="btn btn-primary btn-sm">查看所有用户</a>
                            {% else %}
                                <p class="text-muted">暂无用户数据</p>
                            {% endif %}
                        </div>
                    </div>
                </div>
                
                <div class="col-md-6">
                    <div class="card">
                        <div class="card-header">
                            <h5>活跃订阅</h5>
                        </div>
                        <div class="card-body">
                            {% if active_subscriptions %}
                                <div class="table-responsive">
                                    <table class="table table-sm">
                                        <thead>
                                            <tr>
                                                <th>关键词</th>
                                                <th>用户</th>
                                                <th>创建时间</th>
                                            </tr>
                                        </thead>
                                        <tbody>
                                            {% for sub in active_subscriptions %}
                                            <tr>
                                                <td>{{ sub.keywords[:30] }}{{ '...' if sub.keywords|length > 30 else '' }}</td>
                                                <td>{{ sub.user.email[:20] if sub.user else 'N/A' }}</td>
                                                <td>{{ sub.created_at.strftime('%m-%d') if sub.created_at else 'N/A' }}</td>
                                            </tr>
                                            {% endfor %}
                                        </tbody>
                                    </table>
                                </div>
                                <a href="/admin/subscriptions" class="btn btn-primary btn-sm">查看所有订阅</a>
                            {% else %}
                                <p class="text-muted">暂无订阅数据</p>
                            {% endif %}
                        </div>
                    </div>
                </div>
            </div>
            
            <!-- 快速操作 -->
            <div class="row mt-4">
                <div class="col-12">
                    <div class="card">
                        <div class="card-header">
                            <h5>快速操作</h5>
                        </div>
                        <div class="card-body">
                            <a href="/admin/users" class="btn btn-primary me-2">用户管理</a>
                            <a href="/admin/subscriptions" class="btn btn-success me-2">订阅管理</a>
                            <a href="/admin/push" class="btn btn-warning me-2">推送管理</a>
                            <a href="/admin/mail" class="btn btn-info me-2">邮箱管理</a>
                            <a href="/admin/ai" class="btn btn-info me-2">AI设置</a>
                            <a href="/admin/system" class="btn btn-info me-2">系统设置</a>
                            <a href="/admin/logs" class="btn btn-secondary">查看日志</a>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    </body>
    </html>
    """
    return render_template_string(template, stats=stats, recent_users=recent_users, active_subscriptions=active_subscriptions)

@app.route('/admin/users/add', methods=['GET', 'POST'])
@admin_required
def admin_add_user():
    """添加用户"""
    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '')
        is_admin = request.form.get('is_admin') == 'on'
        
        # 验证输入
        if not email or not password:
            flash('邮箱和密码不能为空', 'error')
            return redirect(url_for('admin_add_user'))
        
        if len(password) < 6:
            flash('密码长度至少6位', 'error')
            return redirect(url_for('admin_add_user'))
        
        # 检查邮箱是否已存在
        existing_user = User.query.filter_by(email=email).first()
        if existing_user:
            flash('该邮箱已被注册', 'error')
            return redirect(url_for('admin_add_user'))
        
        try:
            # 创建新用户
            new_user = User(
                email=email,
                is_admin=is_admin,
                is_active=True,
                push_method='email',
                push_time='09:00',
                push_frequency='daily',
                max_articles=10
            )
            new_user.set_password(password)
            
            db.session.add(new_user)
            db.session.commit()
            
            user_type = '管理员' if is_admin else '普通用户'
            log_activity('INFO', 'admin', f'管理员 {current_user.email} 创建了新{user_type}: {email}', current_user.id, request.remote_addr)
            flash(f'成功创建{user_type}: {email}', 'success')
            return redirect(url_for('admin_users'))
            
        except Exception as e:
            db.session.rollback()
            log_activity('ERROR', 'admin', f'创建用户失败: {email} - {str(e)}', current_user.id, request.remote_addr)
            flash(f'创建用户失败: {str(e)}', 'error')
            return redirect(url_for('admin_add_user'))
    
    # GET请求显示添加用户页面
    template = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>添加用户 - PubMed Literature Push</title>
        <meta charset="utf-8">
        <link href="https://cdn.bootcdn.net/ajax/libs/bootstrap/5.1.3/css/bootstrap.min.css" rel="stylesheet">
        <link href="https://cdn.bootcdn.net/ajax/libs/font-awesome/6.0.0/css/all.min.css" rel="stylesheet">
    </head>
    <body>
        <nav class="navbar navbar-expand-lg navbar-dark bg-primary">
            <div class="container">
                <a class="navbar-brand" href="/"><i class="fas fa-book-medical"></i> PubMed Literature Push</a>
                <div class="navbar-nav ms-auto">
                    <a class="nav-link" href="/">首页</a>
                    <a class="nav-link" href="/admin">管理后台</a>
                    <a class="nav-link" href="/admin/users">用户管理</a>
                    <a class="nav-link" href="/logout">退出</a>
                </div>
            </div>
        </nav>
        
        <div class="container mt-4">
            <nav aria-label="breadcrumb">
                <ol class="breadcrumb">
                    <li class="breadcrumb-item"><a href="/admin">管理后台</a></li>
                    <li class="breadcrumb-item"><a href="/admin/users">用户管理</a></li>
                    <li class="breadcrumb-item active">添加用户</li>
                </ol>
            </nav>
            
            <div class="row justify-content-center">
                <div class="col-md-6">
                    <div class="card">
                        <div class="card-header">
                            <h4><i class="fas fa-user-plus"></i> 添加新用户</h4>
                        </div>
                        <div class="card-body">
                            {% with messages = get_flashed_messages(with_categories=true) %}
                                {% if messages %}
                                    {% for category, message in messages %}
                                        <div class="alert alert-{{ 'danger' if category == 'error' else 'success' }} alert-dismissible fade show">
                                            {{ message }}
                                            <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
                                        </div>
                                    {% endfor %}
                                {% endif %}
                            {% endwith %}
                            
                            <form method="POST">
                                <div class="mb-3">
                                    <label for="email" class="form-label">
                                        <i class="fas fa-envelope"></i> 用户邮箱 *
                                    </label>
                                    <input type="email" class="form-control" id="email" name="email" required>
                                    <div class="form-text">用户的登录邮箱地址</div>
                                </div>
                                
                                <div class="mb-3">
                                    <label for="password" class="form-label">
                                        <i class="fas fa-lock"></i> 登录密码 *
                                    </label>
                                    <input type="password" class="form-control" id="password" name="password" required minlength="6">
                                    <div class="form-text">密码长度至少6位</div>
                                </div>
                                
                                <div class="mb-3">
                                    <div class="form-check">
                                        <input class="form-check-input" type="checkbox" id="is_admin" name="is_admin">
                                        <label class="form-check-label" for="is_admin">
                                            <i class="fas fa-crown text-warning"></i> 设为管理员
                                        </label>
                                        <div class="form-text text-warning">
                                            <i class="fas fa-exclamation-triangle"></i> 
                                            管理员拥有系统的完全访问权限，请谨慎授权
                                        </div>
                                    </div>
                                </div>
                                
                                <hr>
                                
                                <div class="mb-3">
                                    <h6><i class="fas fa-cog"></i> 默认推送设置</h6>
                                    <div class="row">
                                        <div class="col-6">
                                            <small class="text-muted">推送方式: 邮件</small>
                                        </div>
                                        <div class="col-6">
                                            <small class="text-muted">推送时间: 09:00</small>
                                        </div>
                                        <div class="col-6">
                                            <small class="text-muted">推送频率: 每日</small>
                                        </div>
                                        <div class="col-6">
                                            <small class="text-muted">最大文章数: 10篇</small>
                                        </div>
                                    </div>
                                    <small class="text-info">用户创建后可自行修改这些设置</small>
                                </div>
                                
                                <div class="d-grid gap-2">
                                    <button type="submit" class="btn btn-primary">
                                        <i class="fas fa-user-plus"></i> 创建用户
                                    </button>
                                    <a href="{{ url_for('admin_users') }}" class="btn btn-secondary">
                                        <i class="fas fa-arrow-left"></i> 返回用户列表
                                    </a>
                                </div>
                            </form>
                        </div>
                    </div>
                </div>
            </div>
        </div>
        
        <script src="https://cdn.bootcdn.net/ajax/libs/bootstrap/5.1.3/js/bootstrap.bundle.min.js"></script>
        <script>
            // 邮箱格式验证
            document.getElementById('email').addEventListener('blur', function() {
                const email = this.value;
                const emailRegex = /^[^\\s@]+@[^\\s@]+\\.[^\\s@]+$/;
                if (email && !emailRegex.test(email)) {
                    this.setCustomValidity('请输入有效的邮箱地址');
                    this.classList.add('is-invalid');
                } else {
                    this.setCustomValidity('');
                    this.classList.remove('is-invalid');
                }
            });
            
            // 密码强度提示
            document.getElementById('password').addEventListener('input', function() {
                const password = this.value;
                let strength = '弱';
                let className = 'text-danger';
                
                if (password.length >= 8) {
                    if (/(?=.*[a-z])(?=.*[A-Z])(?=.*\\d)/.test(password)) {
                        strength = '强';
                        className = 'text-success';
                    } else if (/(?=.*[a-zA-Z])(?=.*\\d)/.test(password)) {
                        strength = '中等';
                        className = 'text-warning';
                    }
                }
                
                let strengthDiv = document.getElementById('password-strength');
                if (!strengthDiv) {
                    strengthDiv = document.createElement('div');
                    strengthDiv.id = 'password-strength';
                    strengthDiv.className = 'form-text mt-1';
                    this.parentNode.appendChild(strengthDiv);
                }
                
                if (password.length > 0) {
                    strengthDiv.innerHTML = '<span class="' + className + '">密码强度: ' + strength + '</span>';
                } else {
                    strengthDiv.innerHTML = '';
                }
            });
        </script>
    </body>
    </html>
    """
    
    return render_template_string(template)

@app.route('/admin/users')
@admin_required
def admin_users():
    """用户管理页面"""
    users = User.query.order_by(User.created_at.desc()).all()
    
    template = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>用户管理 - PubMed Literature Push</title>
        <link href="https://cdn.bootcdn.net/ajax/libs/bootstrap/5.1.3/css/bootstrap.min.css" rel="stylesheet">
        <link href="https://cdn.bootcdn.net/ajax/libs/font-awesome/6.0.0/css/all.min.css" rel="stylesheet">
    </head>
    <body>
        <nav class="navbar navbar-expand-lg navbar-dark bg-primary">
            <div class="container">
                <a class="navbar-brand" href="/">PubMed Literature Push</a>
                <div class="navbar-nav ms-auto">
                    <a class="nav-link" href="/">首页</a>
                    <a class="nav-link" href="/admin">管理员</a>
                    <a class="nav-link" href="/logout">退出</a>
                </div>
            </div>
        </nav>

        <div class="container mt-4">
            <div class="d-flex justify-content-between align-items-center mb-3">
                <div>
                    <h2>用户管理</h2>
                    <p class="text-muted mb-0">管理系统中的所有用户账户</p>
                </div>
                <div>
                    <a href="/admin/users/add" class="btn btn-primary">
                        <i class="fas fa-user-plus"></i> 添加用户
                    </a>
                </div>
            </div>
            
            <!-- 管理员消息显示 -->
            {% with messages = get_flashed_messages(category_filter=['admin']) %}
                {% if messages %}
                    {% for message in messages %}
                        <div class="alert alert-info alert-dismissible fade show" role="alert">
                            {{ message }}
                            <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
                        </div>
                    {% endfor %}
                {% endif %}
            {% endwith %}
            
            <div class="card">
                <div class="card-body">
                    <div class="table-responsive">
                        <table class="table">
                            <thead>
                                <tr>
                                    <th>ID</th>
                                    <th>邮箱</th>
                                    <th>注册时间</th>
                                    <th>状态</th>
                                    <th>权限</th>
                                    <th>订阅权限</th>
                                    <th>操作</th>
                                </tr>
                            </thead>
                            <tbody>
                                {% for user in users %}
                                <tr>
                                    <td>{{ user.id }}</td>
                                    <td>{{ user.email }}</td>
                                    <td>{{ user.created_at.strftime('%Y-%m-%d %H:%M') if user.created_at else 'N/A' }}</td>
                                    <td>
                                        {% if user.is_active %}
                                            <span class="badge bg-success">活跃</span>
                                        {% else %}
                                            <span class="badge bg-secondary">禁用</span>
                                        {% endif %}
                                    </td>
                                    <td>
                                        {% if user.is_admin %}
                                            <span class="badge bg-danger">管理员</span>
                                        {% else %}
                                            <span class="badge bg-primary">普通用户</span>
                                        {% endif %}
                                    </td>
                                    <td>
                                        <div class="d-flex flex-column">
                                            <small class="text-muted">订阅数: {{ user.get_subscription_limit_info()['current'] }}/{{ user.max_subscriptions }}</small>
                                            <small class="text-muted">频率: {{ user.get_allowed_frequencies()|join(', ') }}</small>
                                        </div>
                                    </td>
                                    <td>
                                        <div class="btn-group" role="group">
                                            {% if not user.is_admin %}
                                                <a href="/admin/users/{{ user.id }}/promote" class="btn btn-sm btn-warning" 
                                                   onclick="return confirm('确定提升为管理员？')">提升管理员</a>
                                            {% else %}
                                                <a href="/admin/users/{{ user.id }}/demote" class="btn btn-sm btn-secondary" 
                                                   onclick="return confirm('确定撤销管理员权限？')">撤销管理员</a>
                                            {% endif %}
                                        </div>
                                        <div class="btn-group mt-1" role="group">
                                            {% if user.is_active %}
                                                {% if user.is_admin %}
                                                    <a href="/admin/users/{{ user.id }}/disable" class="btn btn-sm btn-outline-warning" 
                                                       onclick="return confirm('警告：您正在禁用管理员账户！\\n\\n如果这是最后一个活跃管理员，操作将被拒绝。\\n\\n确定要禁用管理员 {{ user.email }} 吗？')">禁用</a>
                                                {% else %}
                                                    <a href="/admin/users/{{ user.id }}/disable" class="btn btn-sm btn-outline-warning" 
                                                       onclick="return confirm('确定禁用用户 {{ user.email }} 吗？')">禁用</a>
                                                {% endif %}
                                            {% else %}
                                                <a href="/admin/users/{{ user.id }}/enable" class="btn btn-sm btn-outline-success">启用</a>
                                            {% endif %}
                                            
                                            <a href="/admin/users/{{ user.id }}/reset-password" class="btn btn-sm btn-outline-info" 
                                               title="重置用户密码">
                                                <i class="fas fa-key"></i> 重置密码
                                            </a>
                                            
                                            <a href="/admin/users/{{ user.id }}/subscription-settings" class="btn btn-sm btn-outline-primary" 
                                               title="设置订阅权限">
                                                <i class="fas fa-cog"></i> 订阅权限
                                            </a>
                                            
                                            {% if user.id != current_user.id %}
                                                <a href="/admin/users/{{ user.id }}/delete" class="btn btn-sm btn-outline-danger" 
                                                   onclick="return confirm('警告：删除用户将同时删除其所有订阅！\\n\\n确定要删除用户 {{ user.email }} 吗？')">删除</a>
                                            {% else %}
                                                <button class="btn btn-sm btn-outline-secondary" disabled title="不能删除自己">删除</button>
                                            {% endif %}
                                        </div>
                                    </td>
                                </tr>
                                {% endfor %}
                            </tbody>
                        </table>
                    </div>
                </div>
            </div>
            
            <div class="mt-3">
                <a href="/admin" class="btn btn-secondary">
                    <i class="fas fa-arrow-left"></i> 返回仪表板
                </a>
            </div>
        </div>
    </body>
    </html>
    """
    return render_template_string(template, users=users)

@app.route('/admin/users/<int:user_id>')
@admin_required
def admin_user_detail(user_id):
    """用户详情页面"""
    try:
        # 查询用户信息
        result = db.session.execute(
            db.text("SELECT id, email, is_admin, is_active, created_at FROM user WHERE id = :user_id"),
            {'user_id': user_id}
        ).fetchone()
        
        if not result:
            flash('用户不存在', 'admin')
            return redirect(url_for('admin_users'))
        
        # 创建用户对象
        user = type('User', (), {
            'id': result[0],
            'email': result[1],
            'is_admin': bool(result[2]),
            'is_active': bool(result[3]),
            'created_at': result[4]
        })()
        
        # 查询用户的订阅数量
        sub_count = db.session.execute(
            db.text("SELECT COUNT(*) FROM subscription WHERE user_id = :user_id"),
            {'user_id': user_id}
        ).scalar()
        
        template = """
        <!DOCTYPE html>
        <html>
        <head>
            <title>用户详情 - {{ user.email }}</title>
            <link href="https://cdn.bootcdn.net/ajax/libs/bootstrap/5.1.3/css/bootstrap.min.css" rel="stylesheet">
        </head>
        <body>
            <nav class="navbar navbar-expand-lg navbar-dark bg-primary">
                <div class="container">
                    <a class="navbar-brand" href="/">PubMed Literature Push</a>
                    <div class="navbar-nav ms-auto">
                        <a class="nav-link" href="/admin">管理员</a>
                        <a class="nav-link" href="/admin/users">用户管理</a>
                        <a class="nav-link" href="/logout">退出</a>
                    </div>
                </div>
            </nav>

            <div class="container mt-4">
                <h2>用户详情</h2>
                <nav aria-label="breadcrumb">
                    <ol class="breadcrumb">
                        <li class="breadcrumb-item"><a href="/admin">管理员面板</a></li>
                        <li class="breadcrumb-item"><a href="/admin/users">用户管理</a></li>
                        <li class="breadcrumb-item active">{{ user.email }}</li>
                    </ol>
                </nav>
                
                <!-- 管理员消息显示 -->
                {% with messages = get_flashed_messages(category_filter=['admin']) %}
                    {% if messages %}
                        {% for message in messages %}
                            <div class="alert alert-info alert-dismissible fade show" role="alert">
                                {{ message }}
                                <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
                            </div>
                        {% endfor %}
                    {% endif %}
                {% endwith %}
                
                <div class="row">
                    <div class="col-md-8">
                        <div class="card">
                            <div class="card-header">
                                <h5>基本信息</h5>
                            </div>
                            <div class="card-body">
                                <dl class="row">
                                    <dt class="col-sm-3">用户ID:</dt>
                                    <dd class="col-sm-9">{{ user.id }}</dd>
                                    
                                    <dt class="col-sm-3">邮箱地址:</dt>
                                    <dd class="col-sm-9">{{ user.email }}</dd>
                                    
                                    <dt class="col-sm-3">注册时间:</dt>
                                    <dd class="col-sm-9">{{ user.created_at if user.created_at else 'N/A' }}</dd>
                                    
                                    <dt class="col-sm-3">账户状态:</dt>
                                    <dd class="col-sm-9">
                                        {% if user.is_active %}
                                            <span class="badge bg-success">活跃</span>
                                        {% else %}
                                            <span class="badge bg-secondary">已禁用</span>
                                        {% endif %}
                                    </dd>
                                    
                                    <dt class="col-sm-3">用户权限:</dt>
                                    <dd class="col-sm-9">
                                        {% if user.is_admin %}
                                            <span class="badge bg-danger">管理员</span>
                                        {% else %}
                                            <span class="badge bg-primary">普通用户</span>
                                        {% endif %}
                                    </dd>
                                    
                                    <dt class="col-sm-3">订阅数量:</dt>
                                    <dd class="col-sm-9">{{ sub_count }} 个</dd>
                                </dl>
                            </div>
                        </div>
                    </div>
                    
                    <div class="col-md-4">
                        <div class="card">
                            <div class="card-header">
                                <h5>管理操作</h5>
                            </div>
                            <div class="card-body">
                                <div class="d-grid gap-2">
                                    {% if not user.is_admin %}
                                        <a href="/admin/users/{{ user.id }}/promote" class="btn btn-warning" 
                                           onclick="return confirm('确定提升为管理员？')">提升管理员</a>
                                    {% else %}
                                        <a href="/admin/users/{{ user.id }}/demote" class="btn btn-secondary" 
                                           onclick="return confirm('确定撤销管理员权限？')">撤销管理员</a>
                                    {% endif %}
                                    
                                    {% if user.is_active %}
                                        {% if user.is_admin %}
                                            <a href="/admin/users/{{ user.id }}/disable" class="btn btn-outline-warning" 
                                               onclick="return confirm('警告：您正在禁用管理员账户！\\n\\n如果这是最后一个活跃管理员，操作将被拒绝。\\n\\n确定要禁用管理员 {{ user.email }} 吗？')">禁用账户</a>
                                        {% else %}
                                            <a href="/admin/users/{{ user.id }}/disable" class="btn btn-outline-warning" 
                                               onclick="return confirm('确定禁用用户 {{ user.email }} 吗？')">禁用账户</a>
                                        {% endif %}
                                    {% else %}
                                        <a href="/admin/users/{{ user.id }}/enable" class="btn btn-outline-success">启用账户</a>
                                    {% endif %}
                                    
                                    {% if user.id != current_user.id %}
                                        <a href="/admin/users/{{ user.id }}/delete" class="btn btn-outline-danger" 
                                           onclick="return confirm('警告：删除用户将同时删除其所有订阅！\\n\\n确定要删除用户 {{ user.email }} 吗？')">删除用户</a>
                                    {% else %}
                                        <button class="btn btn-outline-secondary" disabled>不能删除自己</button>
                                    {% endif %}
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
                
                <div class="mt-3">
                    <a href="/admin/users" class="btn btn-secondary">返回用户列表</a>
                </div>
            </div>
        </body>
        </html>
        """
        return render_template_string(template, user=user, sub_count=sub_count)
        
    except Exception as e:
        flash(f'获取用户信息失败: {str(e)}', 'admin')
        return redirect(url_for('admin_users'))

@app.route('/admin/users/<int:user_id>/promote')
@admin_required
def promote_user(user_id):
    """提升用户为管理员"""
    try:
        # 检查用户是否已经是管理员
        user_is_admin = db.session.execute(
            db.text("SELECT is_admin FROM user WHERE id = :user_id"),
            {'user_id': user_id}
        ).scalar()
        
        if user_is_admin is None:
            flash('操作失败：用户不存在', 'admin')
        elif user_is_admin:
            flash('操作失败：用户已经是管理员', 'admin')
        else:
            # 提升为管理员
            result = db.session.execute(
                db.text("UPDATE user SET is_admin = 1 WHERE id = :user_id"),
                {'user_id': user_id}
            )
            db.session.commit()
            
            if result.rowcount > 0:
                log_activity('INFO', 'admin', f'用户 {user_id} 已提升为管理员', current_user.id, request.remote_addr)
                flash('用户已提升为管理员', 'admin')
            else:
                flash('操作失败：用户不存在', 'admin')
    except Exception as e:
        db.session.rollback()
        log_activity('ERROR', 'admin', f'用户提升操作失败: {str(e)}', current_user.id, request.remote_addr)
        flash(f'操作失败: {str(e)}', 'admin')
    
    return redirect(url_for('admin_users'))

@app.route('/admin/users/<int:user_id>/demote')
@admin_required
def demote_user(user_id):
    """撤销管理员权限"""
    try:
        # 检查是否是最后一个管理员
        admin_count = db.session.execute(
            db.text("SELECT COUNT(*) FROM user WHERE is_admin = 1")
        ).scalar()
        
        if admin_count > 1:
            # 撤销管理员权限
            result = db.session.execute(
                db.text("UPDATE user SET is_admin = 0 WHERE id = :user_id"),
                {'user_id': user_id}
            )
            db.session.commit()
            
            if result.rowcount > 0:
                flash('已撤销管理员权限', 'admin')
            else:
                flash('操作失败：用户不存在', 'admin')
        else:
            flash('操作失败：不能撤销最后一个管理员', 'admin')
    except Exception as e:
        db.session.rollback()
        flash(f'操作失败: {str(e)}', 'admin')
    
    return redirect(url_for('admin_users'))

@app.route('/admin/users/<int:user_id>/toggle')
@admin_required
def toggle_user(user_id):
    """切换用户状态"""
    if toggle_user_status(user_id):
        flash('用户状态已更新', 'admin')
    else:
        flash('操作失败', 'admin')
    return redirect(url_for('admin_users'))

@app.route('/admin/users/<int:user_id>/disable')
@admin_required
def disable_user(user_id):
    """禁用用户"""
    try:
        # 检查是否试图禁用管理员
        user_info = db.session.execute(
            db.text("SELECT email, is_admin, is_active FROM user WHERE id = :user_id"),
            {'user_id': user_id}
        ).fetchone()
        
        if not user_info:
            flash('操作失败：用户不存在', 'admin')
            return redirect(url_for('admin_users'))
        
        email, is_admin, is_active = user_info
        
        # 如果是管理员，检查是否是最后一个活跃管理员
        if is_admin:
            active_admin_count = db.session.execute(
                db.text("SELECT COUNT(*) FROM user WHERE is_admin = 1 AND is_active = 1")
            ).scalar()
            
            if active_admin_count <= 1:
                flash('操作失败：不能禁用最后一个活跃管理员，这会导致系统无法管理', 'admin')
                return redirect(url_for('admin_users'))
            
            flash(f'警告：正在禁用管理员账户 {email}', 'admin')
        
        # 检查用户当前状态并禁用
        result = db.session.execute(
            db.text("UPDATE user SET is_active = 0 WHERE id = :user_id AND is_active = 1"),
            {'user_id': user_id}
        )
        db.session.commit()
        
        if result.rowcount > 0:
            flash(f'用户 {email} 已禁用', 'admin')
        else:
            flash('操作失败：用户不存在或已被禁用', 'admin')
    except Exception as e:
        db.session.rollback()
        flash(f'操作失败: {str(e)}', 'admin')
    
    return redirect(url_for('admin_users'))

@app.route('/admin/users/<int:user_id>/enable')
@admin_required
def enable_user(user_id):
    """启用用户"""
    try:
        # 检查用户当前状态并启用
        result = db.session.execute(
            db.text("UPDATE user SET is_active = 1 WHERE id = :user_id AND is_active = 0"),
            {'user_id': user_id}
        )
        db.session.commit()
        
        if result.rowcount > 0:
            flash('用户已启用', 'admin')
        else:
            flash('操作失败：用户不存在或已被启用', 'admin')
    except Exception as e:
        db.session.rollback()
        flash(f'操作失败: {str(e)}', 'admin')
    
    return redirect(url_for('admin_users'))

@app.route('/admin/users/<int:user_id>/delete')
@admin_required
def delete_user(user_id):
    """删除用户"""
    try:
        # 检查是否是当前登录用户
        if user_id == current_user.id:
            flash('不能删除自己的账户', 'admin')
            return redirect(url_for('admin_users'))
        
        # 检查是否是最后一个管理员
        admin_count = db.session.execute(
            db.text("SELECT COUNT(*) FROM user WHERE is_admin = 1")
        ).scalar()
        
        user_is_admin = db.session.execute(
            db.text("SELECT is_admin FROM user WHERE id = :user_id"),
            {'user_id': user_id}
        ).scalar()
        
        if user_is_admin and admin_count <= 1:
            flash('不能删除最后一个管理员', 'admin')
            return redirect(url_for('admin_users'))
        
        # 先删除相关的订阅
        db.session.execute(
            db.text("DELETE FROM subscription WHERE user_id = :user_id"),
            {'user_id': user_id}
        )
        
        # 删除用户
        result = db.session.execute(
            db.text("DELETE FROM user WHERE id = :user_id"),
            {'user_id': user_id}
        )
        
        db.session.commit()
        
        if result.rowcount > 0:
            flash('用户删除成功', 'admin')
        else:
            flash('操作失败：用户不存在', 'admin')
            
    except Exception as e:
        db.session.rollback()
        flash(f'删除失败: {str(e)}', 'admin')
    
    return redirect(url_for('admin_users'))

@app.route('/admin/users/<int:user_id>/reset-password', methods=['GET', 'POST'])
@admin_required
def admin_reset_user_password(user_id):
    """管理员重置用户密码"""
    try:
        # 查找目标用户
        target_user = User.query.get_or_404(user_id)
        
        if request.method == 'POST':
            new_password = request.form.get('new_password')
            confirm_password = request.form.get('confirm_password')
            
            # 验证新密码
            if len(new_password) < 6:
                flash('新密码长度至少6位', 'admin')
                return redirect(url_for('admin_reset_user_password', user_id=user_id))
            
            if new_password != confirm_password:
                flash('两次输入的新密码不一致', 'admin')
                return redirect(url_for('admin_reset_user_password', user_id=user_id))
            
            # 更新密码
            target_user.set_password(new_password)
            db.session.commit()
            
            # 记录操作日志
            log_activity('INFO', 'admin', f'管理员 {current_user.email} 重置了用户 {target_user.email} 的密码', current_user.id, request.remote_addr)
            flash(f'用户 {target_user.email} 的密码重置成功', 'admin')
            return redirect(url_for('admin_users'))
        
        # GET请求显示重置密码页面
        template = """
        <!DOCTYPE html>
        <html>
        <head>
            <title>重置用户密码 - PubMed Literature Push</title>
            <meta charset="utf-8">
            <link href="https://cdn.bootcdn.net/ajax/libs/bootstrap/5.1.3/css/bootstrap.min.css" rel="stylesheet">
            <link href="https://cdn.bootcdn.net/ajax/libs/font-awesome/6.0.0/css/all.min.css" rel="stylesheet">
        </head>
        <body>
            <nav class="navbar navbar-expand-lg navbar-dark bg-primary">
                <div class="container">
                    <a class="navbar-brand" href="/">📚 PubMed Literature Push</a>
                    <div class="navbar-nav ms-auto">
                        <a class="nav-link" href="/">首页</a>
                        <a class="nav-link" href="/subscriptions">我的订阅</a>
                        <a class="nav-link" href="/profile">个人设置</a>
                        <a class="nav-link active" href="/admin">
                            <i class="fas fa-cogs"></i> 管理后台
                        </a>
                        <a class="nav-link" href="/logout">退出 ({{current_user.email}})</a>
                    </div>
                </div>
            </nav>
            
            <div class="container mt-4">
                <div class="row justify-content-center">
                    <div class="col-md-6">
                        <div class="card">
                            <div class="card-header d-flex justify-content-between align-items-center">
                                <h4><i class="fas fa-key"></i> 重置用户密码</h4>
                                <small class="text-muted">目标用户: {{ target_user.email }}</small>
                            </div>
                            <div class="card-body">
                                {% with messages = get_flashed_messages(with_categories=true) %}
                                    {% if messages %}
                                        {% for category, message in messages %}
                                            <div class="alert alert-{{ 'danger' if category == 'admin' else 'success' }}">
                                                {{ message }}
                                            </div>
                                        {% endfor %}
                                    {% endif %}
                                {% endwith %}
                                
                                <div class="alert alert-warning">
                                    <i class="fas fa-exclamation-triangle"></i>
                                    <strong>管理员操作警告</strong><br>
                                    您正在为用户 <strong>{{ target_user.email }}</strong> 重置密码。
                                    用户将需要使用新密码重新登录。
                                </div>
                                
                                <form method="POST">
                                    <div class="mb-3">
                                        <label for="new_password" class="form-label">新密码</label>
                                        <input type="password" class="form-control" id="new_password" name="new_password" required minlength="6">
                                        <div class="form-text">密码长度至少6位</div>
                                    </div>
                                    <div class="mb-3">
                                        <label for="confirm_password" class="form-label">确认新密码</label>
                                        <input type="password" class="form-control" id="confirm_password" name="confirm_password" required minlength="6">
                                    </div>
                                    
                                    <div class="d-grid gap-2">
                                        <button type="submit" class="btn btn-warning" onclick="return confirm('确定要重置用户 {{ target_user.email }} 的密码吗？\\\\n\\\\n用户将需要使用新密码重新登录。')">
                                            <i class="fas fa-key"></i> 重置密码
                                        </button>
                                        <a href="{{ url_for('admin_users') }}" class="btn btn-secondary">取消</a>
                                    </div>
                                </form>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
            
            <script src="https://cdn.bootcdn.net/ajax/libs/bootstrap/5.1.3/js/bootstrap.bundle.min.js"></script>
        </body>
        </html>
        """
        
        return render_template_string(template, target_user=target_user)
        
    except Exception as e:
        log_activity('ERROR', 'admin', f'管理员 {current_user.email} 重置用户密码失败: {str(e)}', current_user.id, request.remote_addr)
        flash(f'密码重置失败: {str(e)}', 'admin')
        return redirect(url_for('admin_users'))

@app.route('/admin/users/<int:user_id>/subscription-settings', methods=['GET', 'POST'])
@admin_required
def admin_user_subscription_settings(user_id):
    """管理员设置用户订阅权限"""
    try:
        # 查找目标用户
        target_user = User.query.get_or_404(user_id)
        
        if request.method == 'POST':
            max_subscriptions = request.form.get('max_subscriptions', type=int)
            allowed_frequencies = request.form.getlist('allowed_frequencies')
            
            # 验证输入
            if max_subscriptions is None or max_subscriptions < 0:
                flash('最大订阅数必须是非负整数', 'admin')
                return redirect(url_for('admin_user_subscription_settings', user_id=user_id))
            
            if not allowed_frequencies:
                flash('必须至少选择一种推送频率', 'admin')
                return redirect(url_for('admin_user_subscription_settings', user_id=user_id))
            
            # 更新订阅权限
            target_user.max_subscriptions = max_subscriptions
            target_user.set_allowed_frequencies(allowed_frequencies)
            db.session.commit()
            
            # 记录操作日志
            log_activity('INFO', 'admin', f'管理员 {current_user.email} 更新了用户 {target_user.email} 的订阅权限: 最大订阅数={max_subscriptions}, 允许频率={",".join(allowed_frequencies)}', current_user.id, request.remote_addr)
            flash(f'用户 {target_user.email} 的订阅权限更新成功', 'admin')
            return redirect(url_for('admin_users'))
        
        # GET请求显示订阅权限设置页面
        current_subscriptions = Subscription.query.filter_by(user_id=target_user.id).count()
        limit_info = target_user.get_subscription_limit_info()
        
        template = """
        <!DOCTYPE html>
        <html>
        <head>
            <title>设置订阅权限 - PubMed Literature Push</title>
            <meta charset="utf-8">
            <link href="https://cdn.bootcdn.net/ajax/libs/bootstrap/5.1.3/css/bootstrap.min.css" rel="stylesheet">
            <link href="https://cdn.bootcdn.net/ajax/libs/font-awesome/6.0.0/css/all.min.css" rel="stylesheet">
        </head>
        <body>
            <nav class="navbar navbar-expand-lg navbar-dark bg-primary">
                <div class="container">
                    <a class="navbar-brand" href="/">📚 PubMed Literature Push</a>
                    <div class="navbar-nav ms-auto">
                        <a class="nav-link" href="/">首页</a>
                        <a class="nav-link" href="/subscriptions">我的订阅</a>
                        <a class="nav-link" href="/profile">个人设置</a>
                        <a class="nav-link active" href="/admin">
                            <i class="fas fa-cogs"></i> 管理后台
                        </a>
                        <a class="nav-link" href="/logout">退出 ({{current_user.email}})</a>
                    </div>
                </div>
            </nav>
            
            <div class="container mt-4">
                <div class="row justify-content-center">
                    <div class="col-md-8">
                        <div class="card">
                            <div class="card-header d-flex justify-content-between align-items-center">
                                <h4><i class="fas fa-cog"></i> 设置订阅权限</h4>
                                <small class="text-muted">目标用户: {{ target_user.email }}</small>
                            </div>
                            <div class="card-body">
                                {% with messages = get_flashed_messages(with_categories=true) %}
                                    {% if messages %}
                                        {% for category, message in messages %}
                                            <div class="alert alert-{{ 'danger' if category == 'admin' else 'success' }}">
                                                {{ message }}
                                            </div>
                                        {% endfor %}
                                    {% endif %}
                                {% endwith %}
                                
                                <!-- 当前状态显示 -->
                                <div class="row mb-4">
                                    <div class="col-md-6">
                                        <div class="card bg-light">
                                            <div class="card-body text-center">
                                                <div class="fs-4 fw-bold text-primary">{{ current_subscriptions }}</div>
                                                <small class="text-muted">当前订阅数</small>
                                            </div>
                                        </div>
                                    </div>
                                    <div class="col-md-6">
                                        <div class="card bg-light">
                                            <div class="card-body text-center">
                                                <div class="fs-4 fw-bold text-info">{{ target_user.max_subscriptions }}</div>
                                                <small class="text-muted">最大订阅数</small>
                                            </div>
                                        </div>
                                    </div>
                                </div>
                                
                                <form method="POST">
                                    <div class="mb-4">
                                        <label for="max_subscriptions" class="form-label">最大订阅数量</label>
                                        <input type="number" class="form-control" id="max_subscriptions" name="max_subscriptions" 
                                               value="{{ target_user.max_subscriptions }}" min="0" required>
                                        <div class="form-text">设置用户最多可以创建的订阅数量（当前已有 {{ current_subscriptions }} 个订阅）</div>
                                    </div>
                                    
                                    <div class="mb-4">
                                        <label class="form-label">允许的推送频率</label>
                                        <div class="row">
                                            {% set user_frequencies = target_user.get_allowed_frequencies() %}
                                            <div class="col-md-4">
                                                <div class="form-check">
                                                    <input class="form-check-input" type="checkbox" name="allowed_frequencies" value="daily" id="freq_daily"
                                                           {% if 'daily' in user_frequencies %}checked{% endif %}>
                                                    <label class="form-check-label" for="freq_daily">
                                                        <i class="fas fa-calendar-day"></i> 每日推送
                                                    </label>
                                                </div>
                                            </div>
                                            <div class="col-md-4">
                                                <div class="form-check">
                                                    <input class="form-check-input" type="checkbox" name="allowed_frequencies" value="weekly" id="freq_weekly"
                                                           {% if 'weekly' in user_frequencies %}checked{% endif %}>
                                                    <label class="form-check-label" for="freq_weekly">
                                                        <i class="fas fa-calendar-week"></i> 每周推送
                                                    </label>
                                                </div>
                                            </div>
                                            <div class="col-md-4">
                                                <div class="form-check">
                                                    <input class="form-check-input" type="checkbox" name="allowed_frequencies" value="monthly" id="freq_monthly"
                                                           {% if 'monthly' in user_frequencies %}checked{% endif %}>
                                                    <label class="form-check-label" for="freq_monthly">
                                                        <i class="fas fa-calendar-alt"></i> 每月推送
                                                    </label>
                                                </div>
                                            </div>
                                        </div>
                                        <div class="form-text">选择用户可以使用的推送频率选项</div>
                                    </div>
                                    
                                    <div class="alert alert-info">
                                        <i class="fas fa-info-circle"></i>
                                        <strong>权限设置说明</strong><br>
                                        • 如果当前订阅数超过新设置的最大数量，现有订阅不会被删除，但用户无法创建新订阅<br>
                                        • 推送频率限制只影响新创建的订阅，现有订阅的频率不会自动修改<br>
                                        • 管理员账户不受这些限制约束
                                    </div>
                                    
                                    <div class="d-grid gap-2">
                                        <button type="submit" class="btn btn-primary">
                                            <i class="fas fa-save"></i> 保存设置
                                        </button>
                                        <a href="{{ url_for('admin_users') }}" class="btn btn-secondary">取消</a>
                                    </div>
                                </form>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
            
            <script src="https://cdn.bootcdn.net/ajax/libs/bootstrap/5.1.3/js/bootstrap.bundle.min.js"></script>
        </body>
        </html>
        """
        
        return render_template_string(template, target_user=target_user, current_subscriptions=current_subscriptions)
        
    except Exception as e:
        log_activity('ERROR', 'admin', f'管理员 {current_user.email} 设置用户订阅权限失败: {str(e)}', current_user.id, request.remote_addr)
        flash(f'设置订阅权限失败: {str(e)}', 'admin')
        return redirect(url_for('admin_users'))

@app.route('/admin/subscriptions')
@admin_required
def admin_subscriptions():
    """订阅管理页面"""
    # 直接查询订阅数据，避免AdminUtils导入问题
    try:
        result = db.session.execute(
            db.text("""
                SELECT s.id, s.keywords, s.created_at, u.email 
                FROM subscription s 
                LEFT JOIN user u ON s.user_id = u.id 
                ORDER BY s.created_at DESC 
                LIMIT 50
            """)
        ).fetchall()
        
        subscriptions = []
        for row in result:
            # 处理创建时间
            created_at = row[2]
            if isinstance(created_at, str):
                from datetime import datetime
                try:
                    created_at = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                except:
                    created_at = None
            
            # 创建用户对象
            user = type('User', (), {'email': row[3]})() if row[3] else None
            
            # 创建订阅对象
            subscription = type('Subscription', (), {
                'id': row[0],
                'keywords': row[1],
                'created_at': created_at,
                'user': user
            })()
            subscriptions.append(subscription)
            
    except Exception as e:
        print(f"获取订阅数据失败: {e}")
        import traceback
        traceback.print_exc()
        subscriptions = []
    
    template = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>订阅管理 - PubMed Literature Push</title>
        <link href="https://cdn.bootcdn.net/ajax/libs/bootstrap/5.1.3/css/bootstrap.min.css" rel="stylesheet">
    </head>
    <body>
        <nav class="navbar navbar-expand-lg navbar-dark bg-primary">
            <div class="container">
                <a class="navbar-brand" href="/">PubMed Literature Push</a>
                <div class="navbar-nav ms-auto">
                    <a class="nav-link" href="/">首页</a>
                    <a class="nav-link" href="/admin">管理员</a>
                    <a class="nav-link" href="/logout">退出</a>
                </div>
            </div>
        </nav>

        <div class="container mt-4">
            <h2>订阅管理</h2>
            <p class="text-muted">管理系统中的所有文献订阅</p>
            
            <!-- 管理员消息显示 -->
            {% with messages = get_flashed_messages(category_filter=['admin']) %}
                {% if messages %}
                    {% for message in messages %}
                        <div class="alert alert-info alert-dismissible fade show" role="alert">
                            {{ message }}
                            <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
                        </div>
                    {% endfor %}
                {% endif %}
            {% endwith %}
            
            <div class="card">
                <div class="card-body">
                    {% if subscriptions %}
                    <div class="table-responsive">
                        <table class="table">
                            <thead>
                                <tr>
                                    <th>ID</th>
                                    <th>关键词</th>
                                    <th>用户邮箱</th>
                                    <th>创建时间</th>
                                    <th>操作</th>
                                </tr>
                            </thead>
                            <tbody>
                                {% for sub in subscriptions %}
                                <tr>
                                    <td>{{ sub.id }}</td>
                                    <td>
                                        <span class="badge bg-primary">{{ sub.keywords }}</span>
                                    </td>
                                    <td>{{ sub.user.email if sub.user else '未知用户' }}</td>
                                    <td>{{ sub.created_at.strftime('%Y-%m-%d %H:%M') if sub.created_at else '未知' }}</td>
                                    <td>
                                        <button class="btn btn-sm btn-danger" onclick="if(confirm('确定删除此订阅吗？')) location.href='/admin/subscriptions/{{ sub.id }}/delete'">删除</button>
                                    </td>
                                </tr>
                                {% endfor %}
                            </tbody>
                        </table>
                    </div>
                    {% else %}
                    <div class="text-center py-4">
                        <i class="fas fa-inbox fa-3x text-muted"></i>
                        <h4 class="mt-3 text-muted">暂无订阅</h4>
                        <p class="text-muted">当用户创建订阅后会在这里显示</p>
                    </div>
                    {% endif %}
                </div>
            </div>
            
            <div class="mt-3">
                <a href="/admin" class="btn btn-secondary">返回仪表板</a>
            </div>
        </div>
    </body>
    </html>
    """
    return render_template_string(template, subscriptions=subscriptions)

@app.route('/admin/subscriptions/<int:sub_id>/delete')
@admin_required  
def admin_delete_subscription(sub_id):
    """管理员删除订阅"""
    try:
        # 使用原生SQL删除订阅
        result = db.session.execute(
            db.text("DELETE FROM subscription WHERE id = :sub_id"),
            {'sub_id': sub_id}
        )
        db.session.commit()
        if result.rowcount > 0:
            flash('订阅删除成功', 'admin')
        else:
            flash('订阅不存在', 'admin')
    except Exception as e:
        db.session.rollback()
        flash(f'删除失败: {str(e)}', 'admin')
    return redirect(url_for('admin_subscriptions'))

@app.route('/profile')
@login_required
def profile():
    """用户个人资料页面"""
    # 获取统计信息
    active_subscriptions = db.session.query(Subscription).filter_by(user_id=current_user.id, is_active=True).count()
    total_articles = db.session.query(UserArticle).filter_by(user_id=current_user.id).count()
    
    # 本月推送统计（简化计算）
    from datetime import datetime, timedelta
    month_ago = beijing_now() - timedelta(days=30)
    monthly_articles = db.session.query(UserArticle).filter(
        UserArticle.user_id == current_user.id,
        UserArticle.push_date >= month_ago
    ).count()
    
    # 获取系统最大文章数限制
    system_max_articles = int(SystemSetting.get_setting('push_max_articles', '10'))
    
    # 获取用户订阅限制信息
    subscription_limit_info = current_user.get_subscription_limit_info()
    allowed_frequencies = current_user.get_allowed_frequencies()
    
    # 获取用户的所有订阅（用于显示分订阅设置）
    user_subscriptions = Subscription.query.filter_by(user_id=current_user.id).order_by(Subscription.created_at.desc()).all()
    
    # 个人资料模板
    profile_template = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>个人设置 - PubMed Literature Push</title>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <link href="https://cdn.bootcdn.net/ajax/libs/bootstrap/5.1.3/css/bootstrap.min.css" rel="stylesheet">
        <link href="https://cdn.bootcdn.net/ajax/libs/font-awesome/6.0.0/css/all.min.css" rel="stylesheet">
    </head>
    <body>
        <nav class="navbar navbar-expand-lg navbar-dark bg-primary">
            <div class="container">
                <a class="navbar-brand" href="/">📚 PubMed Literature Push</a>
                <div class="navbar-nav ms-auto">
                    <a class="nav-link" href="/">首页</a>
                    <a class="nav-link" href="/subscriptions">我的订阅</a>
                    <a class="nav-link active" href="/profile">个人设置</a>
                    {% if current_user.is_admin %}
                        <a class="nav-link" href="/admin">
                            <i class="fas fa-cogs"></i> 管理后台
                        </a>
                    {% endif %}
                    <a class="nav-link" href="/logout">退出 ({{current_user.email}})</a>
                </div>
            </div>
        </nav>
        
        <div class="container mt-4">
            <div class="d-flex justify-content-between align-items-center mb-4">
                <div>
                    <h2><i class="fas fa-user-cog"></i> 个人设置</h2>
                    <p class="text-muted mb-0">管理您的账户信息和推送偏好设置</p>
                </div>
            </div>
            
            {% with messages = get_flashed_messages(with_categories=true) %}
                {% if messages %}
                    {% for category, message in messages %}
                        <div class="alert alert-{{ 'danger' if category == 'error' else 'success' if category == 'success' else 'info' }} alert-dismissible">
                            {{ message }}
                            <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
                        </div>
                    {% endfor %}
                {% endif %}
            {% endwith %}
            
            <div class="row">
                <!-- 用户信息卡片 -->
                <div class="col-md-4 mb-4">
                    <div class="card">
                        <div class="card-body text-center">
                            <div class="mb-3">
                                <i class="fas fa-user-circle fa-5x text-primary"></i>
                            </div>
                            <h5 class="card-title">{{ current_user.email }}</h5>
                            <p class="text-muted small">
                                注册时间: {{ current_user.created_at.strftime('%Y-%m-%d') if current_user.created_at else 'N/A' }}
                            </p>
                            <div class="row text-center">
                                <div class="col-4">
                                    <div class="border-end">
                                        <div class="fs-4 fw-bold text-primary">{{ active_subscriptions }}</div>
                                        <small class="text-muted">活跃订阅</small>
                                        {% if not current_user.is_admin %}
                                            <div class="small text-warning">
                                                限制: {{ subscription_limit_info['current'] }}/{{ subscription_limit_info['max'] }}
                                            </div>
                                        {% endif %}
                                    </div>
                                </div>
                                <div class="col-4">
                                    <div class="border-end">
                                        <div class="fs-4 fw-bold text-success">{{ monthly_articles }}</div>
                                        <small class="text-muted">本月推送</small>
                                    </div>
                                </div>
                                <div class="col-4">
                                    <div class="fs-4 fw-bold text-info">{{ total_articles }}</div>
                                    <small class="text-muted">总推送</small>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
                
                <!-- 设置选项卡 -->
                <div class="col-md-8">
                    <div class="card">
                        <div class="card-header">
                            <ul class="nav nav-tabs card-header-tabs" id="settingsTabs" role="tablist">
                                <li class="nav-item" role="presentation">
                                    <button class="nav-link active" id="push-tab" data-bs-toggle="tab" 
                                            data-bs-target="#push" type="button" role="tab">
                                        <i class="fas fa-bell"></i> 推送设置
                                    </button>
                                </li>
                                <li class="nav-item" role="presentation">
                                    <button class="nav-link" id="account-tab" data-bs-toggle="tab" 
                                            data-bs-target="#account" type="button" role="tab">
                                        <i class="fas fa-user"></i> 账户信息
                                    </button>
                                </li>
                                <li class="nav-item" role="presentation">
                                    <button class="nav-link" id="system-tab" data-bs-toggle="tab" 
                                            data-bs-target="#system" type="button" role="tab">
                                        <i class="fas fa-info-circle"></i> 系统信息
                                    </button>
                                </li>
                            </ul>
                        </div>
                        <div class="card-body">
                            <div class="tab-content" id="settingsTabContent">
                                <!-- 推送设置选项卡 -->
                                <div class="tab-pane fade show active" id="push" role="tabpanel">
                                    <form method="POST">
                                        <h5 class="mb-3"><i class="fas fa-cog"></i> 默认推送偏好</h5>
                                        <p class="text-muted small mb-4">这些设置将作为新创建订阅的默认值，您可以在"我的订阅"中为每个订阅单独调整</p>
                                        
                                        <div class="row mb-3">
                                            <div class="col-md-6">
                                                <label class="form-label">默认推送时间</label>
                                                <input type="time" class="form-control" name="push_time" 
                                                       value="{{ current_user.push_time or '09:00' }}" required>
                                                <small class="form-text text-muted">新订阅的默认推送时间</small>
                                            </div>
                                            <div class="col-md-6">
                                                <label class="form-label">默认推送频率</label>
                                                <select class="form-select" name="push_frequency" id="pushFrequency" required>
                                                    {% set allowed_freqs = current_user.get_allowed_frequencies() %}
                                                    {% if current_user.is_admin or 'daily' in allowed_freqs %}
                                                        <option value="daily" {{ 'selected' if current_user.push_frequency == 'daily' else '' }}>每日推送</option>
                                                    {% endif %}
                                                    {% if current_user.is_admin or 'weekly' in allowed_freqs %}
                                                        <option value="weekly" {{ 'selected' if current_user.push_frequency == 'weekly' else '' }}>每周推送</option>
                                                    {% endif %}
                                                    {% if current_user.is_admin or 'monthly' in allowed_freqs %}
                                                        <option value="monthly" {{ 'selected' if current_user.push_frequency == 'monthly' else '' }}>每月推送</option>
                                                    {% endif %}
                                                </select>
                                                <small class="form-text text-muted">
                                                    新订阅的默认推送频率
                                                    {% if not current_user.is_admin %}
                                                        <span class="text-warning">（受权限限制）</span>
                                                    {% endif %}
                                                </small>
                                            </div>
                                        </div>
                                        
                                        <!-- 每周推送设置 -->
                                        <div class="mb-3" id="weeklySettings" style="display: {{ 'block' if current_user.push_frequency == 'weekly' else 'none' }};">
                                            <label class="form-label">默认每周推送日</label>
                                            <select class="form-select" name="push_day">
                                                <option value="monday" {{ 'selected' if current_user.push_day == 'monday' else '' }}>周一</option>
                                                <option value="tuesday" {{ 'selected' if current_user.push_day == 'tuesday' else '' }}>周二</option>
                                                <option value="wednesday" {{ 'selected' if current_user.push_day == 'wednesday' else '' }}>周三</option>
                                                <option value="thursday" {{ 'selected' if current_user.push_day == 'thursday' else '' }}>周四</option>
                                                <option value="friday" {{ 'selected' if current_user.push_day == 'friday' else '' }}>周五</option>
                                                <option value="saturday" {{ 'selected' if current_user.push_day == 'saturday' else '' }}>周六</option>
                                                <option value="sunday" {{ 'selected' if current_user.push_day == 'sunday' else '' }}>周日</option>
                                            </select>
                                        </div>
                                        
                                        <!-- 每月推送设置 -->
                                        <div class="mb-3" id="monthlySettings" style="display: {{ 'block' if current_user.push_frequency == 'monthly' else 'none' }};">
                                            <label class="form-label">默认每月推送日</label>
                                            <select class="form-select" name="push_month_day">
                                                {% for i in range(1, 29) %}
                                                <option value="{{ i }}" {{ 'selected' if current_user.push_month_day == i else '' }}>{{ i }}号</option>
                                                {% endfor %}
                                            </select>
                                        </div>
                                        
                                        <div class="mb-3">
                                            <label class="form-label">推送方式</label>
                                            <div class="form-control-plaintext">
                                                <span class="badge bg-info"><i class="fas fa-envelope"></i> 邮件推送</span>
                                                <small class="text-muted d-block">目前只支持邮件推送方式</small>
                                            </div>
                                        </div>
                                        
                                        <div class="d-grid">
                                            <button type="submit" class="btn btn-primary">
                                                <i class="fas fa-save"></i> 保存推送设置
                                            </button>
                                        </div>
                                    </form>
                                </div>
                                
                                <!-- 账户信息选项卡 -->
                                <div class="tab-pane fade" id="account" role="tabpanel">
                                    <h5 class="mb-3"><i class="fas fa-user-edit"></i> 账户信息</h5>
                                    
                                    <div class="row mb-4">
                                        <div class="col-sm-3">
                                            <strong>邮箱地址</strong>
                                        </div>
                                        <div class="col-sm-9">
                                            <span class="text-muted">{{ current_user.email }}</span>
                                            <small class="text-muted d-block">用于接收推送邮件和系统通知</small>
                                        </div>
                                    </div>
                                    
                                    <div class="row mb-4">
                                        <div class="col-sm-3">
                                            <strong>账户状态</strong>
                                        </div>
                                        <div class="col-sm-9">
                                            {% if current_user.is_active %}
                                                <span class="badge bg-success"><i class="fas fa-check-circle"></i> 活跃</span>
                                            {% else %}
                                                <span class="badge bg-secondary"><i class="fas fa-ban"></i> 已停用</span>
                                            {% endif %}
                                        </div>
                                    </div>
                                    
                                    <div class="row mb-4">
                                        <div class="col-sm-3">
                                            <strong>用户权限</strong>
                                        </div>
                                        <div class="col-sm-9">
                                            {% if current_user.is_admin %}
                                                <span class="badge bg-danger"><i class="fas fa-crown"></i> 管理员</span>
                                            {% else %}
                                                <span class="badge bg-primary"><i class="fas fa-user"></i> 普通用户</span>
                                            {% endif %}
                                        </div>
                                    </div>
                                    
                                    <!-- 账户操作 -->
                                    <div class="mt-4">
                                        <h6 class="mb-3"><i class="fas fa-tools"></i> 账户操作</h6>
                                        <div class="d-grid gap-2 d-md-block">
                                            <a href="/change_password" class="btn btn-outline-primary">
                                                <i class="fas fa-key"></i> 修改密码
                                            </a>
                                        </div>
                                        <small class="text-muted mt-2 d-block">
                                            <i class="fas fa-shield-alt"></i> 为了您的账户安全，建议定期更换密码
                                        </small>
                                    </div>
                                </div>
                                
                                <!-- 系统信息选项卡 -->
                                <div class="tab-pane fade" id="system" role="tabpanel">
                                    <h5 class="mb-3"><i class="fas fa-server"></i> 系统信息</h5>
                                    
                                    <div class="row mb-3">
                                        <div class="col-sm-4">
                                            <div class="card text-center">
                                                <div class="card-body">
                                                    <div class="fs-4 fw-bold text-warning">{{ system_max_articles }}</div>
                                                    <small class="text-muted">每次推送上限</small>
                                                </div>
                                            </div>
                                        </div>
                                        <div class="col-sm-4">
                                            <div class="card text-center">
                                                <div class="card-body">
                                                    <div class="fs-4 fw-bold text-info">30天</div>
                                                    <small class="text-muted">数据保留期</small>
                                                </div>
                                            </div>
                                        </div>
                                        <div class="col-sm-4">
                                            <div class="card text-center">
                                                <div class="card-body">
                                                    <div class="fs-4 fw-bold text-success">5000</div>
                                                    <small class="text-muted">每次搜索上限</small>
                                                </div>
                                            </div>
                                        </div>
                                    </div>
                                    
                                    <div class="table-responsive">
                                        <table class="table table-sm">
                                            <tbody>
                                                <tr>
                                                    <td><strong>系统名称</strong></td>
                                                    <td>PubMed Literature Push</td>
                                                </tr>
                                                <tr>
                                                    <td><strong>推送时间检查</strong></td>
                                                    <td><span class="badge bg-success">每小时</span></td>
                                                </tr>
                                                <tr>
                                                    <td><strong>数据源</strong></td>
                                                    <td>PubMed + JCR + 中科院分区</td>
                                                </tr>
                                                <tr>
                                                    <td><strong>推送方式</strong></td>
                                                    <td><span class="badge bg-info">邮件</span></td>
                                                </tr>
                                            </tbody>
                                        </table>
                                    </div>
                                    
                                    <div class="alert alert-light">
                                        <h6><i class="fas fa-lightbulb"></i> 使用提示</h6>
                                        <ul class="mb-0">
                                            <li>在"我的订阅"页面可以为每个订阅设置不同的推送参数</li>
                                            <li>推送时间基于北京时间(UTC+8)</li>
                                            <li>期刊质量筛选支持JCR分区和中科院分区</li>
                                            <li>搜索天数会根据推送频率自动调整</li>
                                        </ul>
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </div>
        
        <script>
            // 根据推送频率显示/隐藏相关选项
            document.addEventListener('DOMContentLoaded', function() {
                const pushFrequency = document.getElementById('pushFrequency');
                const weeklySettings = document.getElementById('weeklySettings');
                const monthlySettings = document.getElementById('monthlySettings');
                
                function toggleSettings() {
                    if (pushFrequency.value === 'weekly') {
                        weeklySettings.style.display = 'block';
                        monthlySettings.style.display = 'none';
                    } else if (pushFrequency.value === 'monthly') {
                        weeklySettings.style.display = 'none';
                        monthlySettings.style.display = 'block';
                    } else {
                        weeklySettings.style.display = 'none';
                        monthlySettings.style.display = 'none';
                    }
                }
                
                pushFrequency.addEventListener('change', toggleSettings);
                toggleSettings(); // 初始化显示状态
            });
        </script>
        <script src="https://cdn.bootcdn.net/ajax/libs/bootstrap/5.1.3/js/bootstrap.bundle.min.js"></script>
    </body>
    </html>
    """
    
    return render_template_string(profile_template, 
                                active_subscriptions=active_subscriptions,
                                total_articles=total_articles,
                                monthly_articles=monthly_articles,
                                system_max_articles=system_max_articles,
                                user_subscriptions=user_subscriptions,
                                subscription_limit_info=subscription_limit_info,
                                allowed_frequencies=allowed_frequencies)

@app.route('/profile', methods=['POST'])
@login_required
def update_profile():
    """更新用户个人资料"""
    try:
        current_user.push_method = 'email'  # 固定为邮件推送
        current_user.push_time = request.form.get('push_time', '09:00')
        current_user.push_frequency = request.form.get('push_frequency', 'daily')
        current_user.push_day = request.form.get('push_day', 'monday')
        current_user.push_month_day = int(request.form.get('push_month_day', 1))
        
        db.session.commit()
        flash('推送设置更新成功！', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'更新失败: {str(e)}', 'error')
    
    return redirect(url_for('profile'))

@app.route('/change_password', methods=['GET', 'POST'])
@login_required
def change_password():
    """修改密码"""
    if request.method == 'POST':
        current_password = request.form.get('current_password')
        new_password = request.form.get('new_password')
        confirm_password = request.form.get('confirm_password')
        
        # 验证当前密码
        if not current_user.check_password(current_password):
            flash('当前密码错误', 'error')
            return redirect(url_for('change_password'))
        
        # 验证新密码
        if len(new_password) < 6:
            flash('新密码长度至少6位', 'error')
            return redirect(url_for('change_password'))
        
        if new_password != confirm_password:
            flash('两次输入的新密码不一致', 'error')
            return redirect(url_for('change_password'))
        
        if current_password == new_password:
            flash('新密码不能与当前密码相同', 'error')
            return redirect(url_for('change_password'))
        
        try:
            # 更新密码
            current_user.set_password(new_password)
            db.session.commit()
            
            log_activity('INFO', 'auth', f'用户 {current_user.email} 修改密码成功', current_user.id, request.remote_addr)
            flash('密码修改成功！', 'success')
            return redirect(url_for('profile'))
            
        except Exception as e:
            db.session.rollback()
            log_activity('ERROR', 'auth', f'用户 {current_user.email} 修改密码失败: {str(e)}', current_user.id, request.remote_addr)
            flash(f'密码修改失败: {str(e)}', 'error')
            return redirect(url_for('change_password'))
    
    # GET请求显示修改密码页面
    template = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>修改密码 - PubMed Literature Push</title>
        <meta charset="utf-8">
        <link href="https://cdn.bootcdn.net/ajax/libs/bootstrap/5.1.3/css/bootstrap.min.css" rel="stylesheet">
    </head>
    <body>
        <nav class="navbar navbar-expand-lg navbar-dark bg-primary">
            <div class="container">
                <a class="navbar-brand" href="/">📚 PubMed Literature Push</a>
                <div class="navbar-nav ms-auto">
                    <a class="nav-link" href="/">首页</a>
                    <a class="nav-link" href="/subscriptions">我的订阅</a>
                    <a class="nav-link" href="/profile">个人设置</a>
                    {% if current_user.is_admin %}
                        <a class="nav-link" href="/admin">
                            <i class="fas fa-cogs"></i> 管理后台
                        </a>
                    {% endif %}
                    <a class="nav-link" href="/logout">退出 ({{current_user.email}})</a>
                </div>
            </div>
        </nav>
        
        <div class="container mt-4">
            <div class="row justify-content-center">
                <div class="col-md-6">
                    <div class="card">
                        <div class="card-header">
                            <h4>修改密码</h4>
                        </div>
                        <div class="card-body">
                            {% with messages = get_flashed_messages(with_categories=true) %}
                                {% if messages %}
                                    {% for category, message in messages %}
                                        <div class="alert alert-{{ 'danger' if category == 'error' else 'success' }}">
                                            {{ message }}
                                        </div>
                                    {% endfor %}
                                {% endif %}
                            {% endwith %}
                            
                            <form method="POST">
                                <div class="mb-3">
                                    <label for="current_password" class="form-label">当前密码</label>
                                    <input type="password" class="form-control" id="current_password" name="current_password" required>
                                </div>
                                <div class="mb-3">
                                    <label for="new_password" class="form-label">新密码</label>
                                    <input type="password" class="form-control" id="new_password" name="new_password" required minlength="6">
                                    <div class="form-text">密码长度至少6位</div>
                                </div>
                                <div class="mb-3">
                                    <label for="confirm_password" class="form-label">确认新密码</label>
                                    <input type="password" class="form-control" id="confirm_password" name="confirm_password" required minlength="6">
                                </div>
                                
                                <div class="d-grid gap-2">
                                    <button type="submit" class="btn btn-primary">修改密码</button>
                                    <a href="{{ url_for('profile') }}" class="btn btn-secondary">取消</a>
                                </div>
                            </form>
                        </div>
                    </div>
                </div>
            </div>
        </div>
        
        <script>
            // 密码强度检查
            document.getElementById('new_password').addEventListener('input', function() {
                const password = this.value;
                let strength = '弱';
                let className = 'text-danger';
                
                if (password.length >= 8) {
                    if (/(?=.*[a-z])(?=.*[A-Z])(?=.*\\d)/.test(password)) {
                        strength = '强';
                        className = 'text-success';
                    } else if (/(?=.*[a-zA-Z])(?=.*\\d)/.test(password)) {
                        strength = '中';
                        className = 'text-warning';
                    }
                }
                
                let strengthDiv = document.getElementById('password-strength');
                if (!strengthDiv) {
                    strengthDiv = document.createElement('div');
                    strengthDiv.id = 'password-strength';
                    strengthDiv.className = 'form-text';
                    this.parentNode.appendChild(strengthDiv);
                }
                strengthDiv.innerHTML = '<span class="' + className + '">密码强度: ' + strength + '</span>';
            });
            
            // 确认密码匹配检查
            document.getElementById('confirm_password').addEventListener('input', function() {
                const password = document.getElementById('new_password').value;
                const confirm = this.value;
                
                let matchDiv = document.getElementById('password-match');
                if (!matchDiv) {
                    matchDiv = document.createElement('div');
                    matchDiv.id = 'password-match';
                    matchDiv.className = 'form-text';
                    this.parentNode.appendChild(matchDiv);
                }
                
                if (confirm === '') {
                    matchDiv.innerHTML = '';
                } else if (password === confirm) {
                    matchDiv.innerHTML = '<span class="text-success">密码匹配</span>';
                } else {
                    matchDiv.innerHTML = '<span class="text-danger">密码不匹配</span>';
                }
            });
        </script>
    </body>
    </html>
    """
    
    return render_template_string(template)

@app.route('/admin/push')
@admin_required
def admin_push():
    """推送管理页面"""
    # 获取调度器状态
    scheduler_status = {
        'running': scheduler.running,
        'jobs': len(scheduler.get_jobs()) if scheduler.running else 0,
        'lock_file_exists': os.path.exists('/app/data/scheduler.lock'),
        'current_pid': os.getpid()
    }
    
    # 如果锁文件存在，读取PID
    if scheduler_status['lock_file_exists']:
        try:
            with open('/app/data/scheduler.lock', 'r') as f:
                scheduler_status['scheduler_pid'] = int(f.read().strip())
        except:
            scheduler_status['scheduler_pid'] = None
    else:
        scheduler_status['scheduler_pid'] = None
    
    # 获取下次执行时间
    if scheduler.running:
        jobs = scheduler.get_jobs()
        if jobs:
            next_run_time = jobs[0].next_run_time
            scheduler_status['next_run'] = next_run_time.strftime('%Y-%m-%d %H:%M:%S') if next_run_time else '未知'
        else:
            scheduler_status['next_run'] = '无任务'
    else:
        scheduler_status['next_run'] = '调度器未运行'
    
    # 获取推送统计
    stats = {
        'total_users': User.query.filter_by(is_active=True).count(),
        'active_subscriptions': Subscription.query.filter_by(is_active=True).count(),
        'total_articles': Article.query.count(),
        'recent_pushes': SystemLog.query.filter_by(module='push').order_by(SystemLog.timestamp.desc()).limit(10).all()
    }
    
    template = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>推送管理 - PubMed Literature Push</title>
        <meta charset="utf-8">
        <link href="https://cdn.bootcdn.net/ajax/libs/bootstrap/5.1.3/css/bootstrap.min.css" rel="stylesheet">
        <link href="https://cdn.bootcdn.net/ajax/libs/font-awesome/6.0.0/css/all.min.css" rel="stylesheet">
    </head>
    <body>
        <nav class="navbar navbar-expand-lg navbar-dark bg-primary">
            <div class="container">
                <a class="navbar-brand" href="/">
                    <i class="fas fa-microscope"></i> PubMed Literature Push
                </a>
                <div class="navbar-nav ms-auto">
                    <a class="nav-link" href="/">首页</a>
                    <a class="nav-link" href="/admin">管理员</a>
                    <a class="nav-link" href="/logout">退出</a>
                </div>
            </div>
        </nav>

        <div class="container mt-4">
            <div class="d-flex justify-content-between align-items-center mb-4">
                <div>
                    <h2><i class="fas fa-rocket"></i> 推送管理</h2>
                    <p class="text-muted">管理文献推送服务和监控推送状态</p>
                </div>
                <a href="/admin" class="btn btn-secondary">
                    <i class="fas fa-arrow-left"></i> 返回管理员
                </a>
            </div>
            
            {% with messages = get_flashed_messages(category_filter=['admin']) %}
                {% if messages %}
                    {% for message in messages %}
                        <div class="alert alert-success alert-dismissible fade show">
                            {{ message }}
                            <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
                        </div>
                    {% endfor %}
                {% endif %}
            {% endwith %}
            
            <!-- 统计概览 -->
            <div class="row mb-4">
                <div class="col-md-3">
                    <div class="card text-center">
                        <div class="card-body">
                            <h5 class="card-title text-primary">{{ stats.total_users }}</h5>
                            <p class="card-text">活跃用户</p>
                        </div>
                    </div>
                </div>
                <div class="col-md-3">
                    <div class="card text-center">
                        <div class="card-body">
                            <h5 class="card-title text-success">{{ stats.active_subscriptions }}</h5>
                            <p class="card-text">活跃订阅</p>
                        </div>
                    </div>
                </div>
                <div class="col-md-3">
                    <div class="card text-center">
                        <div class="card-body">
                            <h5 class="card-title text-info">{{ stats.total_articles }}</h5>
                            <p class="card-text">文章总数</p>
                        </div>
                    </div>
                </div>
                <div class="col-md-3">
                    <div class="card text-center">
                        <div class="card-body">
                            {% if scheduler_status['running'] %}
                                <h5 class="card-title text-success">
                                    <i class="fas fa-check-circle"></i> 运行中
                                </h5>
                                <p class="card-text">调度器状态</p>
                            {% else %}
                                <h5 class="card-title text-danger">
                                    <i class="fas fa-times-circle"></i> 未运行
                                </h5>
                                <p class="card-text">调度器状态</p>
                            {% endif %}
                        </div>
                    </div>
                </div>
            </div>
            
            <!-- 调度器详细状态 -->
            <div class="card mb-4">
                <div class="card-header">
                    <h5>
                        <i class="fas fa-cogs"></i> 调度器状态详情
                        {% if scheduler_status['running'] %}
                            <span class="badge bg-success ms-2">运行中</span>
                        {% else %}
                            <span class="badge bg-danger ms-2">未运行</span>
                        {% endif %}
                    </h5>
                </div>
                <div class="card-body">
                    <div class="row">
                        <div class="col-md-6">
                            <table class="table table-sm">
                                <tr>
                                    <td><strong>运行状态:</strong></td>
                                    <td>
                                        {% if scheduler_status['running'] %}
                                            <span class="text-success"><i class="fas fa-check-circle"></i> 运行中</span>
                                        {% else %}
                                            <span class="text-danger"><i class="fas fa-times-circle"></i> 未运行</span>
                                        {% endif %}
                                    </td>
                                </tr>
                                <tr>
                                    <td><strong>任务数量:</strong></td>
                                    <td>{{ scheduler_status['jobs'] }} 个</td>
                                </tr>
                                <tr>
                                    <td><strong>下次执行:</strong></td>
                                    <td>{{ scheduler_status['next_run'] }}</td>
                                </tr>
                            </table>
                        </div>
                        <div class="col-md-6">
                            <table class="table table-sm">
                                <tr>
                                    <td><strong>当前进程PID:</strong></td>
                                    <td>{{ scheduler_status['current_pid'] }}</td>
                                </tr>
                                <tr>
                                    <td><strong>调度器进程PID:</strong></td>
                                    <td>
                                        {% if scheduler_status['scheduler_pid'] %}
                                            {{ scheduler_status['scheduler_pid'] }}
                                            {% if scheduler_status['scheduler_pid'] == scheduler_status['current_pid'] %}
                                                <span class="text-success">(本进程)</span>
                                            {% else %}
                                                <span class="text-info">(其他进程)</span>
                                            {% endif %}
                                        {% else %}
                                            <span class="text-muted">无锁文件</span>
                                        {% endif %}
                                    </td>
                                </tr>
                                <tr>
                                    <td><strong>锁文件状态:</strong></td>
                                    <td>
                                        {% if scheduler_status['lock_file_exists'] %}
                                            <span class="text-success"><i class="fas fa-lock"></i> 存在</span>
                                        {% else %}
                                            <span class="text-warning"><i class="fas fa-unlock"></i> 不存在</span>
                                        {% endif %}
                                    </td>
                                </tr>
                            </table>
                        </div>
                    </div>
                </div>
            </div>
            
            <!-- 推送操作 -->
            <div class="card mb-4">
                <div class="card-header">
                    <h5>推送操作</h5>
                </div>
                <div class="card-body">
                    <div class="row">
                        <div class="col-md-6">
                            <h6>立即推送</h6>
                            <p class="text-muted">为所有活跃用户立即执行推送检查，查找并发送新文献</p>
                            <form method="POST" action="/admin/push/trigger" style="display: inline;">
                                <button type="submit" class="btn btn-primary" 
                                        onclick="return confirm('确定为所有用户执行推送吗？这可能需要一些时间。')">
                                    <i class="fas fa-rocket"></i> 立即推送
                                </button>
                            </form>
                        </div>
                        <div class="col-md-6">
                            <h6>调度器测试</h6>
                            <p class="text-muted">测试定时推送调度器功能，验证自动推送机制</p>
                            <form method="POST" action="/admin/push/test" style="display: inline;">
                                <button type="submit" class="btn btn-outline-info">
                                    <i class="fas fa-clock"></i> 测试调度器
                                </button>
                            </form>
                            <small class="text-muted d-block">模拟定时任务执行，检查推送设置和时间判断</small>
                        </div>
                    </div>
                    
                    <hr class="my-4">
                    
                    <!-- 测试和维护功能 -->
                    <div class="row">
                        <div class="col-md-4">
                            <h6 class="text-warning">清除推送记录</h6>
                            <p class="text-muted">清除所有用户的推送记录，用于测试时重新推送相同文章</p>
                            <form method="POST" action="/admin/push/clear-all" style="display: inline;">
                                <button type="submit" class="btn btn-warning" 
                                        onclick="return confirm('⚠️ 警告：这将清除所有用户的推送记录！\\n\\n清除后，之前推送过的文章会重新推送给用户。\\n\\n确定要继续吗？')">
                                    <i class="fas fa-trash-alt"></i> 清除所有记录
                                </button>
                            </form>
                            <small class="text-warning d-block">仅用于测试环境，生产环境请谨慎使用</small>
                        </div>
                        <div class="col-md-4">
                            <h6>按用户清除</h6>
                            <p class="text-muted">清除指定用户的推送记录，可以重新为该用户推送文章</p>
                            <div class="input-group mb-2">
                                <input type="email" class="form-control" id="userEmail" placeholder="输入用户邮箱">
                                <button type="button" class="btn btn-outline-warning" onclick="clearUserRecords()">
                                    <i class="fas fa-user-times"></i> 清除用户记录
                                </button>
                            </div>
                            <small class="text-muted">输入用户邮箱后点击按钮清除该用户的推送记录</small>
                        </div>
                        <div class="col-md-4">
                            <h6 class="text-danger">清理全部文章</h6>
                            <p class="text-muted">清除数据库中所有文章数据，用于测试环境重置</p>
                            <form method="POST" action="/admin/articles/clear-all" style="display: inline;">
                                <button type="submit" class="btn btn-danger" 
                                        onclick="return confirm('🚨 危险操作：这将删除数据库中所有文章！\\n\\n包括：\\n- Article表中的所有文章数据\\n- UserArticle表中的所有推送记录\\n\\n此操作不可恢复！\\n\\n确定要继续吗？')">
                                    <i class="fas fa-database"></i> 清空文章库
                                </button>
                            </form>
                            <small class="text-danger d-block">危险操作！仅用于测试环境，将删除所有文章数据</small>
                        </div>
                    </div>
                </div>
            </div>
            
            <!-- 最近推送记录 -->
            <div class="card">
                <div class="card-header">
                    <h5>最近推送记录</h5>
                </div>
                <div class="card-body">
                    {% if stats.recent_pushes %}
                    <div class="table-responsive">
                        <table class="table table-sm">
                            <thead>
                                <tr>
                                    <th>时间</th>
                                    <th>消息</th>
                                    <th>用户</th>
                                </tr>
                            </thead>
                            <tbody>
                                {% for push in stats.recent_pushes %}
                                <tr>
                                    <td class="text-nowrap">{{ push.timestamp.strftime('%Y-%m-%d %H:%M') if push.timestamp else 'N/A' }}</td>
                                    <td>{{ push.message }}</td>
                                    <td>{{ push.user.email if push.user else 'System' }}</td>
                                </tr>
                                {% endfor %}
                            </tbody>
                        </table>
                    </div>
                    {% else %}
                    <div class="text-center py-4">
                        <i class="fas fa-bell-slash fa-3x text-muted"></i>
                        <h4 class="mt-3 text-muted">暂无推送记录</h4>
                        <p class="text-muted">推送活动将在这里显示</p>
                    </div>
                    {% endif %}
                </div>
            </div>
        </div>
        
        <script src="https://cdn.bootcdn.net/ajax/libs/bootstrap/5.1.3/js/bootstrap.bundle.min.js"></script>
        <script>
        function clearUserRecords() {
            const email = document.getElementById('userEmail').value.trim();
            if (!email) {
                alert('请输入用户邮箱');
                return;
            }
            
            if (!email.includes('@')) {
                alert('请输入有效的邮箱地址');
                return;
            }
            
            if (confirm(`确定要清除用户 ${email} 的推送记录吗？\\n\\n清除后该用户会重新收到之前推送过的文章。`)) {
                // 创建临时表单提交
                const form = document.createElement('form');
                form.method = 'POST';
                form.action = '/admin/push/clear-user';
                
                const emailInput = document.createElement('input');
                emailInput.type = 'hidden';
                emailInput.name = 'email';
                emailInput.value = email;
                
                form.appendChild(emailInput);
                document.body.appendChild(form);
                form.submit();
            }
        }
        </script>
    </body>
    </html>
    """
    return render_template_string(template, stats=stats, scheduler_status=scheduler_status)

@app.route('/admin/logs')
@admin_required
def admin_logs():
    """系统日志页面"""
    # 获取真实的日志数据，按时间降序排列，限制最近100条
    try:
        logs = SystemLog.query.order_by(SystemLog.timestamp.desc()).limit(100).all()
        
        # 统计各级别日志数量
        log_stats = {
            'INFO': SystemLog.query.filter_by(level='INFO').count(),
            'WARNING': SystemLog.query.filter_by(level='WARNING').count(), 
            'ERROR': SystemLog.query.filter_by(level='ERROR').count()
        }
    except Exception as e:
        logs = []
        log_stats = {'INFO': 0, 'WARNING': 0, 'ERROR': 0}
        log_activity('ERROR', 'system', f'获取日志失败: {str(e)}')
    
    template = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>系统日志 - PubMed Literature Push</title>
        <meta charset="utf-8">
        <link href="https://cdn.bootcdn.net/ajax/libs/bootstrap/5.1.3/css/bootstrap.min.css" rel="stylesheet">
        <link href="https://cdn.bootcdn.net/ajax/libs/font-awesome/6.0.0/css/all.min.css" rel="stylesheet">
    </head>
    <body>
        <nav class="navbar navbar-expand-lg navbar-dark bg-primary">
            <div class="container">
                <a class="navbar-brand" href="/">
                    <i class="fas fa-microscope"></i> PubMed Literature Push
                </a>
                <div class="navbar-nav ms-auto">
                    <a class="nav-link" href="/">首页</a>
                    <a class="nav-link" href="/admin">管理员</a>
                    <a class="nav-link" href="/logout">退出</a>
                </div>
            </div>
        </nav>

        <div class="container mt-4">
            <div class="d-flex justify-content-between align-items-center mb-4">
                <div>
                    <h2><i class="fas fa-list-alt"></i> 系统日志</h2>
                    <p class="text-muted">查看系统运行日志和操作记录</p>
                </div>
                <a href="/admin" class="btn btn-secondary">
                    <i class="fas fa-arrow-left"></i> 返回管理员
                </a>
            </div>
            
            <!-- 管理员消息显示 -->
            {% with messages = get_flashed_messages(category_filter=['admin']) %}
                {% if messages %}
                    {% for message in messages %}
                        <div class="alert alert-info alert-dismissible fade show" role="alert">
                            {{ message }}
                            <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
                        </div>
                    {% endfor %}
                {% endif %}
            {% endwith %}
            
            <!-- 日志统计 -->
            <div class="row mb-4">
                <div class="col-md-3">
                    <div class="card text-center">
                        <div class="card-body">
                            <h5 class="card-title text-info">{{ log_stats.INFO }}</h5>
                            <p class="card-text">信息日志</p>
                        </div>
                    </div>
                </div>
                <div class="col-md-3">
                    <div class="card text-center">
                        <div class="card-body">
                            <h5 class="card-title text-warning">{{ log_stats.WARNING }}</h5>
                            <p class="card-text">警告日志</p>
                        </div>
                    </div>
                </div>
                <div class="col-md-3">
                    <div class="card text-center">
                        <div class="card-body">
                            <h5 class="card-title text-danger">{{ log_stats.ERROR }}</h5>
                            <p class="card-text">错误日志</p>
                        </div>
                    </div>
                </div>
                <div class="col-md-3">
                    <div class="card text-center">
                        <div class="card-body">
                            <h5 class="card-title text-secondary">{{ logs|length }}</h5>
                            <p class="card-text">显示记录</p>
                        </div>
                    </div>
                </div>
            </div>
            
            <div class="card">
                <div class="card-header d-flex justify-content-between">
                    <h5 class="mb-0">系统日志 (最近100条)</h5>
                    <div>
                        <button class="btn btn-sm btn-outline-primary" onclick="location.reload()">
                            <i class="fas fa-sync"></i> 刷新
                        </button>
                        <form method="POST" action="/admin/logs/clear" style="display: inline;">
                            <button type="submit" class="btn btn-sm btn-outline-danger" 
                                    onclick="return confirm('确定清空所有日志吗？此操作不可恢复！')">
                                <i class="fas fa-trash"></i> 清空日志
                            </button>
                        </form>
                    </div>
                </div>
                <div class="card-body">
                    {% if logs %}
                    <div class="table-responsive">
                        <table class="table table-sm">
                            <thead>
                                <tr>
                                    <th>时间</th>
                                    <th>级别</th>
                                    <th>模块</th>
                                    <th>用户</th>
                                    <th>消息</th>
                                    <th>IP地址</th>
                                </tr>
                            </thead>
                            <tbody>
                                {% for log in logs %}
                                <tr>
                                    <td class="text-nowrap">{{ log.timestamp.strftime('%Y-%m-%d %H:%M:%S') if log.timestamp else 'N/A' }}</td>
                                    <td>
                                        {% if log.level == 'ERROR' %}
                                            <span class="badge bg-danger">{{ log.level }}</span>
                                        {% elif log.level == 'WARNING' %}
                                            <span class="badge bg-warning">{{ log.level }}</span>
                                        {% else %}
                                            <span class="badge bg-info">{{ log.level }}</span>
                                        {% endif %}
                                    </td>
                                    <td><span class="badge bg-secondary">{{ log.module }}</span></td>
                                    <td>{{ log.user.email if log.user else 'System' }}</td>
                                    <td>{{ log.message }}</td>
                                    <td class="text-muted">{{ log.ip_address or 'N/A' }}</td>
                                </tr>
                                {% endfor %}
                            </tbody>
                        </table>
                    </div>
                    {% else %}
                    <div class="text-center py-4">
                        <i class="fas fa-file-alt fa-3x text-muted"></i>
                        <h4 class="mt-3 text-muted">暂无日志记录</h4>
                        <p class="text-muted">系统日志将在这里显示</p>
                    </div>
                    {% endif %}
                </div>
            </div>
        </div>
        
        <script src="https://cdn.bootcdn.net/ajax/libs/bootstrap/5.1.3/js/bootstrap.bundle.min.js"></script>
    </body>
    </html>
    """
    return render_template_string(template, logs=logs, log_stats=log_stats)

@app.route('/admin/logs/clear', methods=['POST'])
@admin_required
def clear_logs():
    """清空系统日志"""
    try:
        log_activity('INFO', 'admin', f'管理员 {current_user.email} 清空系统日志', current_user.id, request.remote_addr)
        
        # 清空所有日志
        SystemLog.query.delete()
        db.session.commit()
        
        flash('系统日志已清空', 'admin')
        
    except Exception as e:
        db.session.rollback()
        flash(f'清空日志失败: {str(e)}', 'admin')
    
    return redirect(url_for('admin_logs'))

@app.route('/admin/system', methods=['GET', 'POST'])
@admin_required
def admin_system():
    """系统设置页面"""
    if request.method == 'POST':
        try:
            # 保存PubMed配置
            if 'pubmed_config' in request.form:
                SystemSetting.set_setting('pubmed_max_results', request.form.get('pubmed_max_results', '20'), 'PubMed每次最大检索数量', 'pubmed')
                SystemSetting.set_setting('pubmed_timeout', request.form.get('pubmed_timeout', '30'), 'PubMed请求超时时间(秒)', 'pubmed')
                SystemSetting.set_setting('pubmed_api_key', request.form.get('pubmed_api_key', ''), 'PubMed API Key', 'pubmed')
                flash('PubMed配置已保存', 'admin')
            
            # 保存推送配置  
            elif 'push_config' in request.form:
                SystemSetting.set_setting('push_daily_time', request.form.get('push_daily_time', '09:00'), '默认每日推送时间', 'push')
                SystemSetting.set_setting('push_max_articles', request.form.get('push_max_articles', '50'), '每次推送最大文章数', 'push')
                SystemSetting.set_setting('push_check_frequency', request.form.get('push_check_frequency', '1'), '定时推送检查频率(小时)', 'push')
                SystemSetting.set_setting('push_enabled', request.form.get('push_enabled', 'true'), '启用自动推送', 'push')
                
                # 重新初始化调度器以应用新的检查频率
                if scheduler.running:
                    scheduler.remove_job('push_check')
                    init_scheduler()
                    
                flash('推送配置已保存，调度器已更新', 'admin')
            
            
            # 保存系统配置
            elif 'system_config' in request.form:
                SystemSetting.set_setting('system_name', request.form.get('system_name', 'PubMed Literature Push'), '系统名称', 'system')
                SystemSetting.set_setting('log_retention_days', request.form.get('log_retention_days', '30'), '日志保留天数', 'system')
                SystemSetting.set_setting('max_articles_limit', request.form.get('max_articles_limit', '1000'), '文章数量上限', 'system')
                SystemSetting.set_setting('cleanup_articles_count', request.form.get('cleanup_articles_count', '100'), '单次清理文章数量', 'system')
                SystemSetting.set_setting('user_registration_enabled', request.form.get('user_registration_enabled', 'true'), '允许用户注册', 'system')
                flash('系统配置已保存', 'admin')
                
            log_activity('INFO', 'admin', f'管理员 {current_user.email} 更新系统设置', current_user.id, request.remote_addr)
            
        except Exception as e:
            flash(f'保存设置失败: {str(e)}', 'admin')
            log_activity('ERROR', 'admin', f'系统设置保存失败: {str(e)}', current_user.id, request.remote_addr)
        
        return redirect(url_for('admin_system'))
    
    # 获取当前设置
    settings = {
        # PubMed配置
        'pubmed_max_results': SystemSetting.get_setting('pubmed_max_results', '200'),
        'pubmed_timeout': SystemSetting.get_setting('pubmed_timeout', '10'),
        'pubmed_api_key': SystemSetting.get_setting('pubmed_api_key', ''),
        
        # 推送配置
        'push_daily_time': SystemSetting.get_setting('push_daily_time', '09:00'),
        'push_max_articles': SystemSetting.get_setting('push_max_articles', '50'),
        'push_check_frequency': SystemSetting.get_setting('push_check_frequency', '1'),
        'push_enabled': SystemSetting.get_setting('push_enabled', 'true'),
        
        # 系统配置
        'system_name': SystemSetting.get_setting('system_name', 'PubMed Literature Push'),
        'log_retention_days': SystemSetting.get_setting('log_retention_days', '30'),
        'max_articles_limit': SystemSetting.get_setting('max_articles_limit', '1000'),
        'cleanup_articles_count': SystemSetting.get_setting('cleanup_articles_count', '100'),
        'user_registration_enabled': SystemSetting.get_setting('user_registration_enabled', 'true'),
    }
    
    # 获取缓存信息
    cache_info = journal_cache.get_cache_info()
    
    template = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>系统设置 - PubMed Literature Push</title>
        <meta charset="utf-8">
        <link href="https://cdn.bootcdn.net/ajax/libs/bootstrap/5.1.3/css/bootstrap.min.css" rel="stylesheet">
        <link href="https://cdn.bootcdn.net/ajax/libs/font-awesome/6.0.0/css/all.min.css" rel="stylesheet">
    </head>
    <body>
        <nav class="navbar navbar-expand-lg navbar-dark bg-primary">
            <div class="container">
                <a class="navbar-brand" href="/">
                    <i class="fas fa-microscope"></i> PubMed Literature Push
                </a>
                <div class="navbar-nav ms-auto">
                    <a class="nav-link" href="/">首页</a>
                    <a class="nav-link" href="/admin">管理员</a>
                    <a class="nav-link" href="/logout">退出</a>
                </div>
            </div>
        </nav>

        <div class="container mt-4">
            <div class="d-flex justify-content-between align-items-center mb-4">
                <div>
                    <h2><i class="fas fa-cog"></i> 系统设置</h2>
                    <p class="text-muted">管理系统配置和参数</p>
                </div>
                <a href="/admin" class="btn btn-secondary">
                    <i class="fas fa-arrow-left"></i> 返回管理员
                </a>
            </div>
            
            {% with messages = get_flashed_messages(category_filter=['admin']) %}
                {% if messages %}
                    {% for message in messages %}
                        <div class="alert alert-success alert-dismissible fade show">
                            {{ message }}
                            <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
                        </div>
                    {% endfor %}
                {% endif %}
            {% endwith %}
            
            <div class="row">
                <div class="col-md-6">
                    <div class="card mb-4">
                        <div class="card-header">
                            <h5><i class="fas fa-search"></i> PubMed API 配置</h5>
                        </div>
                        <div class="card-body">
                            <form method="POST">
                                <input type="hidden" name="pubmed_config" value="1">
                                <div class="mb-3">
                                    <label class="form-label">每次检索最大条数</label>
                                    <input type="number" class="form-control" name="pubmed_max_results" 
                                           value="{{ settings.pubmed_max_results }}" min="1" max="10000" required>
                                    <div class="form-text">单次搜索返回的最大文章数量 (1-10000)</div>
                                </div>
                                <div class="mb-3">
                                    <label class="form-label">请求超时 (秒)</label>
                                    <input type="number" class="form-control" name="pubmed_timeout" 
                                           value="{{ settings.pubmed_timeout }}" min="10" max="120" required>
                                    <div class="form-text">单个请求的最大等待时间</div>
                                </div>
                                <div class="mb-3">
                                    <label class="form-label">API Key (可选)</label>
                                    <input type="text" class="form-control" name="pubmed_api_key" 
                                           value="{{ settings.pubmed_api_key }}" placeholder="留空使用默认限制">
                                    <div class="form-text">NCBI API Key，可提高请求限制从3/秒到10/秒</div>
                                </div>
                                <button type="submit" class="btn btn-primary">
                                    <i class="fas fa-save"></i> 保存PubMed配置
                                </button>
                            </form>
                        </div>
                    </div>
                </div>
                
                <div class="col-md-6">
                    <div class="card mb-4">
                        <div class="card-header">
                            <h5><i class="fas fa-paper-plane"></i> 推送配置</h5>
                        </div>
                        <div class="card-body">
                            <form method="POST">
                                <input type="hidden" name="push_config" value="1">
                                <div class="mb-3">
                                    <label class="form-label">默认推送时间</label>
                                    <input type="time" class="form-control" name="push_daily_time" 
                                           value="{{ settings.push_daily_time }}" required>
                                    <div class="form-text">新用户的默认推送时间</div>
                                </div>
                                <div class="mb-3">
                                    <label class="form-label">系统最大文章数限制</label>
                                    <input type="number" class="form-control" name="push_max_articles" 
                                           value="{{ settings.push_max_articles }}" min="1" max="100" required>
                                    <div class="form-text">
                                        <strong>系统级限制</strong>：即使用户设置更高值，也不会超过此限制<br>
                                        实际推送数 = min(用户设置, 系统限制)
                                    </div>
                                </div>
                                <div class="mb-3">
                                    <label class="form-label">定时推送检查频率 (小时)</label>
                                    <select class="form-control" name="push_check_frequency" required>
                                        <option value="1" {% if settings.push_check_frequency == '1' %}selected{% endif %}>每1小时检查一次</option>
                                        <option value="2" {% if settings.push_check_frequency == '2' %}selected{% endif %}>每2小时检查一次</option>
                                        <option value="4" {% if settings.push_check_frequency == '4' %}selected{% endif %}>每4小时检查一次</option>
                                        <option value="6" {% if settings.push_check_frequency == '6' %}selected{% endif %}>每6小时检查一次</option>
                                        <option value="12" {% if settings.push_check_frequency == '12' %}selected{% endif %}>每12小时检查一次</option>
                                        <option value="24" {% if settings.push_check_frequency == '24' %}selected{% endif %}>每24小时检查一次</option>
                                    </select>
                                    <div class="form-text">系统自动检查推送任务的频率，修改后自动重启调度器</div>
                                </div>
                                <div class="mb-3">
                                    <div class="form-check">
                                        <input class="form-check-input" type="checkbox" name="push_enabled" value="true"
                                               {{ 'checked' if settings.push_enabled == 'true' else '' }}>
                                        <label class="form-check-label">
                                            启用自动推送功能
                                        </label>
                                    </div>
                                    <div class="form-text">关闭后将停止所有自动推送</div>
                                </div>
                                <button type="submit" class="btn btn-primary">
                                    <i class="fas fa-save"></i> 保存推送配置
                                </button>
                            </form>
                        </div>
                    </div>
                </div>
                
                <div class="col-md-6">
                    <div class="card mb-4">
                        <div class="card-header">
                            <h5><i class="fas fa-server"></i> 系统配置</h5>
                        </div>
                        <div class="card-body">
                            <form method="POST">
                                <input type="hidden" name="system_config" value="1">
                                <div class="mb-3">
                                    <label class="form-label">系统名称</label>
                                    <input type="text" class="form-control" name="system_name" 
                                           value="{{ settings.system_name }}" required>
                                    <div class="form-text">显示在页面标题和导航栏中</div>
                                </div>
                                <div class="mb-3">
                                    <label class="form-label">日志保留天数</label>
                                    <input type="number" class="form-control" name="log_retention_days" 
                                           value="{{ settings.log_retention_days }}" min="1" max="365" required>
                                    <div class="form-text">超过此天数的日志将被自动清理</div>
                                </div>
                                <div class="mb-3">
                                    <label class="form-label">文章存储上限</label>
                                    <input type="number" class="form-control" name="max_articles_limit" 
                                           value="{{ settings.max_articles_limit }}" min="100" max="10000" required>
                                    <div class="form-text">超过此数量时自动清理最早的文章，建议1000-5000篇</div>
                                </div>
                                <div class="mb-3">
                                    <label class="form-label">单次清理数量</label>
                                    <input type="number" class="form-control" name="cleanup_articles_count" 
                                           value="{{ settings.cleanup_articles_count }}" min="10" max="500" required>
                                    <div class="form-text">每次自动清理时删除的最早文章数量，建议50-200篇</div>
                                </div>
                                <div class="mb-3">
                                    <div class="form-check">
                                        <input class="form-check-input" type="checkbox" name="user_registration_enabled" value="true"
                                               {{ 'checked' if settings.user_registration_enabled == 'true' else '' }}>
                                        <label class="form-check-label">
                                            允许用户注册
                                        </label>
                                    </div>
                                    <div class="form-text">关闭后新用户无法注册</div>
                                </div>
                                <button type="submit" class="btn btn-primary">
                                    <i class="fas fa-save"></i> 保存系统配置
                                </button>
                            </form>
                        </div>
                    </div>
                </div>
                
                <div class="col-md-6">
                    <div class="card mb-4">
                        <div class="card-header">
                            <h5><i class="fas fa-info-circle"></i> 系统信息</h5>
                        </div>
                        <div class="card-body">
                            <div class="row">
                                <div class="col-md-6 mb-3">
                                    <strong>应用版本:</strong><br>
                                    <span class="text-muted">v2.0.0</span>
                                </div>
                                <div class="col-md-6 mb-3">
                                    <strong>Flask版本:</strong><br>
                                    <span class="text-muted">2.3.3</span>
                                </div>
                                <div class="col-md-6 mb-3">
                                    <strong>数据库:</strong><br>
                                    <span class="text-muted">SQLite</span>
                                </div>
                                <div class="col-md-6 mb-3">
                                    <strong>运行状态:</strong><br>
                                    <span class="badge bg-success">正常运行</span>
                                </div>
                                <div class="col-md-6 mb-3">
                                    <strong>PubMed API:</strong><br>
                                    <span class="badge bg-success">已连接</span>
                                </div>
                                <div class="col-md-12 mb-3">
                                    <strong>期刊数据缓存:</strong><br>
                                    <small class="text-muted">
                                        JCR数据: {{ cache_info.jcr_count }}条 | 
                                        中科院数据: {{ cache_info.zky_count }}条<br>
                                        加载时间: {{ cache_info.last_loaded.strftime('%Y-%m-%d %H:%M:%S') if cache_info.last_loaded else '未加载' }}
                                    </small>
                                    <div class="mt-2">
                                        <form method="POST" action="/admin/cache/reload" style="display: inline;">
                                            <button type="submit" class="btn btn-sm btn-outline-info">
                                                <i class="fas fa-refresh"></i> 重新加载缓存
                                            </button>
                                        </form>
                                    </div>
                                </div>
                                <div class="col-md-6 mb-3">
                                    <strong>推送服务:</strong><br>
                                    <span class="badge bg-{{ 'success' if settings.push_enabled == 'true' else 'warning' }}">
                                        {{ '已启用' if settings.push_enabled == 'true' else '已禁用' }}
                                    </span>
                                </div>
                            </div>
                            
                            <hr>
                            <h6><i class="fas fa-envelope"></i> 邮箱配置状态</h6>
                            <p class="text-muted small">多邮箱配置请前往 <a href="/admin/mail" class="text-primary">邮箱管理</a> 页面设置</p>
                            <div class="text-info">
                                <i class="fas fa-info-circle"></i> 
                                系统现已支持多邮箱轮询发送，请在邮箱管理中配置多个邮箱以提高发送成功率
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </div>
        
        <script src="https://cdn.bootcdn.net/ajax/libs/bootstrap/5.1.3/js/bootstrap.bundle.min.js"></script>
    </body>
    </html>
    """
    return render_template_string(template, settings=settings, cache_info=cache_info)

@app.route('/admin/cache/reload', methods=['POST'])
@admin_required
def reload_journal_cache():
    """重新加载期刊数据缓存"""
    try:
        log_activity('INFO', 'admin', f'管理员 {current_user.email} 重新加载期刊数据缓存', current_user.id, request.remote_addr)
        
        # 重新加载缓存
        start_time = time.time()
        journal_cache.reload_data()
        load_time = time.time() - start_time
        
        cache_info = journal_cache.get_cache_info()
        
        log_activity('INFO', 'admin', 
                   f'期刊缓存重新加载完成: JCR({cache_info["jcr_count"]})条, 中科院({cache_info["zky_count"]})条, 耗时{load_time:.2f}秒', 
                   current_user.id, request.remote_addr)
        
        flash(f'期刊数据缓存重新加载成功：JCR({cache_info["jcr_count"]})条, 中科院({cache_info["zky_count"]})条, 耗时{load_time:.2f}秒', 'admin')
        
    except Exception as e:
        log_activity('ERROR', 'admin', f'重新加载期刊缓存失败: {str(e)}', current_user.id, request.remote_addr)
        flash(f'重新加载期刊缓存失败: {str(e)}', 'admin')
    
    return redirect(url_for('admin_system'))

@app.route('/admin/scheduler/status')
@admin_required  
def scheduler_status():
    """查看调度器状态"""
    try:
        jobs = []
        if scheduler.running:
            for job in scheduler.get_jobs():
                jobs.append({
                    'id': job.id,
                    'name': job.name,
                    'next_run': job.next_run_time.strftime('%Y-%m-%d %H:%M:%S') if job.next_run_time else '未设置',
                    'trigger': str(job.trigger)
                })
        
        status = {
            'running': scheduler.running,
            'jobs': jobs
        }
        
        return jsonify(status)
    except Exception as e:
        return jsonify({'error': str(e)})

@app.route('/admin/push/test', methods=['POST'])
@admin_required
def admin_test_scheduler():
    """测试调度器推送功能"""
    try:
        # 记录测试调用
        app.logger.info(f"[管理员] {current_user.email} 触发手动调度器测试")
        log_activity('INFO', 'admin', f'管理员 {current_user.email} 手动测试调度器', current_user.id, request.remote_addr)
        
        # 检查调度器状态
        if not scheduler.running:
            flash('调度器未运行，正在尝试初始化...', 'admin')
            try:
                init_scheduler()
                if scheduler.running:
                    flash('调度器初始化成功', 'admin')
                else:
                    flash('调度器初始化失败', 'admin')
                    return redirect(url_for('admin_push'))
            except Exception as e:
                flash(f'调度器初始化失败: {str(e)}', 'admin')
                return redirect(url_for('admin_push'))
        
        # 立即执行一次推送检查（模拟调度器触发）
        current_time = beijing_now()
        app.logger.info(f"[手动测试] 开始推送检查 - {current_time.strftime('%Y-%m-%d %H:%M:%S')}")
        
        with app.app_context():
            check_and_push_articles()
        
        flash('调度器测试执行完成，请查看日志了解详细结果。如有用户符合推送条件会立即推送。', 'admin')
        app.logger.info("[手动测试] 推送检查执行完成")
        
    except Exception as e:
        app.logger.error(f"[手动测试] 调度器测试失败: {e}")
        flash(f'调度器测试失败: {str(e)}', 'admin')
    
    return redirect(url_for('admin_push'))

@app.route('/admin/push/clear-all', methods=['POST'])
@admin_required
def clear_all_push_records():
    """清除所有推送记录"""
    try:
        # 记录操作日志
        log_activity('INFO', 'admin', f'管理员 {current_user.email} 开始清除所有推送记录', current_user.id, request.remote_addr)
        
        # 删除所有UserArticle记录
        deleted_count = UserArticle.query.count()
        UserArticle.query.delete()
        db.session.commit()
        
        log_activity('INFO', 'admin', f'成功清除 {deleted_count} 条推送记录', current_user.id, request.remote_addr)
        flash(f'成功清除所有推送记录（共 {deleted_count} 条）', 'admin')
        
    except Exception as e:
        db.session.rollback()
        log_activity('ERROR', 'admin', f'清除所有推送记录失败: {str(e)}', current_user.id, request.remote_addr)
        flash(f'清除推送记录失败: {str(e)}', 'admin')
    
    return redirect(url_for('admin_push'))

@app.route('/admin/push/clear-user', methods=['POST'])
@admin_required
def clear_user_push_records():
    """清除指定用户的推送记录"""
    try:
        email = request.form.get('email', '').strip()
        if not email:
            flash('请提供用户邮箱', 'admin')
            return redirect(url_for('admin_push'))
        
        # 查找用户
        user = User.query.filter_by(email=email).first()
        if not user:
            flash(f'未找到邮箱为 {email} 的用户', 'admin')
            return redirect(url_for('admin_push'))
        
        # 记录操作日志
        log_activity('INFO', 'admin', f'管理员 {current_user.email} 开始清除用户 {email} 的推送记录', current_user.id, request.remote_addr)
        
        # 删除该用户的所有UserArticle记录
        deleted_count = UserArticle.query.filter_by(user_id=user.id).count()
        UserArticle.query.filter_by(user_id=user.id).delete()
        db.session.commit()
        
        log_activity('INFO', 'admin', f'成功清除用户 {email} 的 {deleted_count} 条推送记录', current_user.id, request.remote_addr)
        flash(f'成功清除用户 {email} 的推送记录（共 {deleted_count} 条）', 'admin')
        
    except Exception as e:
        db.session.rollback()
        log_activity('ERROR', 'admin', f'清除用户推送记录失败: {str(e)}', current_user.id, request.remote_addr)
        flash(f'清除用户推送记录失败: {str(e)}', 'admin')
    
    return redirect(url_for('admin_push'))

@app.route('/admin/articles/clear-all', methods=['POST'])
@admin_required
def clear_all_articles():
    """清理所有文章数据（测试用）"""
    try:
        # 记录操作日志
        log_activity('INFO', 'admin', f'管理员 {current_user.email} 开始清理所有文章数据', current_user.id, request.remote_addr)
        
        # 统计删除前的数据
        article_count = Article.query.count()
        user_article_count = UserArticle.query.count()
        
        # 先删除UserArticle表（外键关联）
        UserArticle.query.delete()
        
        # 再删除Article表
        Article.query.delete()
        
        db.session.commit()
        
        log_activity('INFO', 'admin', 
                   f'成功清理所有文章数据: {article_count}篇文章, {user_article_count}条推送记录', 
                   current_user.id, request.remote_addr)
        
        flash(f'成功清理所有文章数据：删除了 {article_count} 篇文章和 {user_article_count} 条推送记录', 'admin')
        
    except Exception as e:
        db.session.rollback()
        log_activity('ERROR', 'admin', f'清理所有文章数据失败: {str(e)}', current_user.id, request.remote_addr)
        flash(f'清理文章数据失败: {str(e)}', 'admin')
    
    return redirect(url_for('admin_push'))

@app.route('/admin/push/trigger', methods=['POST'])
@admin_required
def trigger_push():
    """手动触发推送"""
    try:
        log_activity('INFO', 'admin', f'管理员 {current_user.email} 手动触发推送', current_user.id, request.remote_addr)
        
        # 执行推送
        results = push_service.process_user_subscriptions()
        
        success_count = sum(1 for r in results if r.get('success'))
        total_articles = sum(r.get('articles_found', 0) for r in results if r.get('success'))
        
        flash(f'推送完成：处理了 {len(results)} 个用户，成功 {success_count} 个，共找到 {total_articles} 篇新文章', 'admin')
        
    except Exception as e:
        log_activity('ERROR', 'admin', f'手动推送失败: {str(e)}', current_user.id, request.remote_addr)
        flash(f'推送失败: {str(e)}', 'admin')
    
    return redirect(url_for('admin_push'))

# 邮箱管理路由
@app.route('/admin/mail')
@admin_required
def admin_mail():
    """邮箱管理页面"""
    configs = MailConfig.query.all()
    stats = mail_sender.get_mail_stats()
    
    template = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>邮箱管理 - PubMed Literature Push</title>
        <meta charset="utf-8">
        <link href="https://cdn.bootcdn.net/ajax/libs/bootstrap/5.1.3/css/bootstrap.min.css" rel="stylesheet">
        <link href="https://cdn.bootcdn.net/ajax/libs/font-awesome/6.0.0/css/all.min.css" rel="stylesheet">
    </head>
    <body>
        <nav class="navbar navbar-expand-lg navbar-dark bg-primary">
            <div class="container">
                <a class="navbar-brand" href="/"><i class="fas fa-microscope"></i> PubMed Literature Push</a>
                <div class="navbar-nav ms-auto">
                    <a class="nav-link" href="/">首页</a>
                    <a class="nav-link" href="/admin">管理员</a>
                    <a class="nav-link" href="/logout">退出</a>
                </div>
            </div>
        </nav>

        <div class="container mt-4">
            <div class="d-flex justify-content-between align-items-center mb-4">
                <div>
                    <h2><i class="fas fa-envelope-open"></i> 邮箱管理</h2>
                    <p class="text-muted">管理多个发送邮箱配置，支持轮询发送</p>
                </div>
                <div>
                    <a href="/admin/mail/add" class="btn btn-success">
                        <i class="fas fa-plus"></i> 添加邮箱
                    </a>
                    <a href="/admin" class="btn btn-secondary">
                        <i class="fas fa-arrow-left"></i> 返回管理员
                    </a>
                </div>
            </div>
            
            {% with messages = get_flashed_messages(category_filter=['admin']) %}
                {% if messages %}
                    {% for message in messages %}
                        <div class="alert alert-success alert-dismissible fade show">
                            {{ message }}
                            <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
                        </div>
                    {% endfor %}
                {% endif %}
            {% endwith %}
            
            <!-- 邮箱统计 -->
            <div class="row mb-4">
                <div class="col-md-3">
                    <div class="card text-center">
                        <div class="card-body">
                            <h5 class="card-title text-primary">{{ configs|length }}</h5>
                            <p class="card-text">总邮箱数</p>
                        </div>
                    </div>
                </div>
                <div class="col-md-3">
                    <div class="card text-center">
                        <div class="card-body">
                            <h5 class="card-title text-success">{{ stats|selectattr('available')|list|length }}</h5>
                            <p class="card-text">可用邮箱</p>
                        </div>
                    </div>
                </div>
                <div class="col-md-3">
                    <div class="card text-center">
                        <div class="card-body">
                            <h5 class="card-title text-info">{{ stats|sum(attribute='current_count') }}</h5>
                            <p class="card-text">今日发送总数</p>
                        </div>
                    </div>
                </div>
                <div class="col-md-3">
                    <div class="card text-center">
                        <div class="card-body">
                            <h5 class="card-title text-warning">{{ stats|sum(attribute='daily_limit') }}</h5>
                            <p class="card-text">日发送上限</p>
                        </div>
                    </div>
                </div>
            </div>
            
            <!-- 邮箱列表 -->
            <div class="card">
                <div class="card-header">
                    <h5>邮箱配置列表</h5>
                </div>
                <div class="card-body">
                    {% if configs %}
                    <div class="table-responsive">
                        <table class="table">
                            <thead>
                                <tr>
                                    <th>名称</th>
                                    <th>邮箱地址</th>
                                    <th>SMTP服务器</th>
                                    <th>状态</th>
                                    <th>今日使用</th>
                                    <th>最后使用</th>
                                    <th>操作</th>
                                </tr>
                            </thead>
                            <tbody>
                                {% for config in configs %}
                                <tr>
                                    <td><strong>{{ config.name }}</strong></td>
                                    <td>{{ config.username }}</td>
                                    <td>{{ config.smtp_server }}:{{ config.smtp_port }}</td>
                                    <td>
                                        {% if config.is_active %}
                                            {% if config.can_send() %}
                                                <span class="badge bg-success">可用</span>
                                            {% else %}
                                                <span class="badge bg-warning">已达限制</span>
                                            {% endif %}
                                        {% else %}
                                            <span class="badge bg-secondary">已禁用</span>
                                        {% endif %}
                                    </td>
                                    <td>
                                        <div class="progress" style="height: 20px;">
                                            <div class="progress-bar" role="progressbar" 
                                                 style="width: {{ (config.current_count / config.daily_limit * 100) if config.daily_limit > 0 else 0 }}%">
                                                {{ config.current_count }}/{{ config.daily_limit }}
                                            </div>
                                        </div>
                                    </td>
                                    <td>{{ config.last_used.strftime('%m-%d %H:%M') if config.last_used else '从未使用' }}</td>
                                    <td>
                                        <div class="btn-group" role="group">
                                            <a href="/admin/mail/edit/{{ config.id }}" class="btn btn-sm btn-outline-primary">
                                                <i class="fas fa-edit"></i> 编辑
                                            </a>
                                            <a href="/admin/mail/test/{{ config.id }}" class="btn btn-sm btn-outline-info">
                                                <i class="fas fa-paper-plane"></i> 测试
                                            </a>
                                            {% if config.is_active %}
                                                <a href="/admin/mail/disable/{{ config.id }}" class="btn btn-sm btn-outline-warning">
                                                    <i class="fas fa-pause"></i> 禁用
                                                </a>
                                            {% else %}
                                                <a href="/admin/mail/enable/{{ config.id }}" class="btn btn-sm btn-outline-success">
                                                    <i class="fas fa-play"></i> 启用
                                                </a>
                                            {% endif %}
                                            <a href="/admin/mail/delete/{{ config.id }}" class="btn btn-sm btn-outline-danger" 
                                               onclick="return confirm('确定删除此邮箱配置吗？')">
                                                <i class="fas fa-trash"></i> 删除
                                            </a>
                                        </div>
                                    </td>
                                </tr>
                                {% endfor %}
                            </tbody>
                        </table>
                    </div>
                    {% else %}
                    <div class="text-center py-4">
                        <i class="fas fa-envelope fa-3x text-muted"></i>
                        <h4 class="mt-3 text-muted">暂无邮箱配置</h4>
                        <p class="text-muted">添加邮箱配置以启用邮件推送功能</p>
                        <a href="/admin/mail/add" class="btn btn-primary">
                            <i class="fas fa-plus"></i> 添加第一个邮箱
                        </a>
                    </div>
                    {% endif %}
                </div>
            </div>
        </div>
        
        <script src="https://cdn.bootcdn.net/ajax/libs/bootstrap/5.1.3/js/bootstrap.bundle.min.js"></script>
    </body>
    </html>
    """
    return render_template_string(template, configs=configs, stats=stats)

@app.route('/admin/mail/add', methods=['GET', 'POST'])
@admin_required
def admin_mail_add():
    """添加邮箱配置"""
    if request.method == 'POST':
        try:
            config = MailConfig(
                name=request.form.get('name'),
                smtp_server=request.form.get('smtp_server'),
                smtp_port=int(request.form.get('smtp_port', 465)),
                username=request.form.get('username'),
                password=request.form.get('password'),
                use_tls=bool(request.form.get('use_tls')),
                daily_limit=int(request.form.get('daily_limit', 100))
            )
            
            db.session.add(config)
            db.session.commit()
            
            log_activity('INFO', 'admin', f'管理员 {current_user.email} 添加邮箱配置: {config.name}', current_user.id, request.remote_addr)
            flash(f'邮箱配置 "{config.name}" 添加成功', 'admin')
            return redirect(url_for('admin_mail'))
            
        except Exception as e:
            db.session.rollback()
            flash(f'添加失败: {str(e)}', 'admin')
    
    template = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>添加邮箱 - PubMed Literature Push</title>
        <meta charset="utf-8">
        <link href="https://cdn.bootcdn.net/ajax/libs/bootstrap/5.1.3/css/bootstrap.min.css" rel="stylesheet">
    </head>
    <body>
        <nav class="navbar navbar-expand-lg navbar-dark bg-primary">
            <div class="container">
                <a class="navbar-brand" href="/"><i class="fas fa-microscope"></i> PubMed Literature Push</a>
                <div class="navbar-nav ms-auto">
                    <a class="nav-link" href="/admin">管理员</a>
                    <a class="nav-link" href="/admin/mail">邮箱管理</a>
                    <a class="nav-link" href="/logout">退出</a>
                </div>
            </div>
        </nav>

        <div class="container mt-4">
            <h2>添加邮箱配置</h2>
            <nav aria-label="breadcrumb">
                <ol class="breadcrumb">
                    <li class="breadcrumb-item"><a href="/admin">管理员面板</a></li>
                    <li class="breadcrumb-item"><a href="/admin/mail">邮箱管理</a></li>
                    <li class="breadcrumb-item active">添加邮箱</li>
                </ol>
            </nav>
            
            {% with messages = get_flashed_messages(category_filter=['admin']) %}
                {% if messages %}
                    {% for message in messages %}
                        <div class="alert alert-danger alert-dismissible fade show">
                            {{ message }}
                            <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
                        </div>
                    {% endfor %}
                {% endif %}
            {% endwith %}
            
            <div class="card">
                <div class="card-body">
                    <form method="POST">
                        <div class="row">
                            <div class="col-md-6">
                                <div class="mb-3">
                                    <label class="form-label">配置名称 *</label>
                                    <input type="text" class="form-control" name="name" required 
                                           placeholder="例如：QQ邮箱1">
                                    <div class="form-text">用于识别不同的邮箱配置</div>
                                </div>
                                <div class="mb-3">
                                    <label class="form-label">SMTP服务器 *</label>
                                    <input type="text" class="form-control" name="smtp_server" required 
                                           placeholder="smtp.qq.com">
                                    <div class="form-text">邮件服务商的SMTP服务器地址</div>
                                </div>
                                <div class="mb-3">
                                    <label class="form-label">SMTP端口 *</label>
                                    <input type="number" class="form-control" name="smtp_port" value="465" required>
                                    <div class="form-text">通常为465(SSL)或587(TLS)，推荐465</div>
                                </div>
                                <div class="mb-3">
                                    <label class="form-label">邮箱地址 *</label>
                                    <input type="email" class="form-control" name="username" required 
                                           placeholder="your-email@qq.com">
                                    <div class="form-text">用于发送邮件的邮箱地址</div>
                                </div>
                            </div>
                            <div class="col-md-6">
                                <div class="mb-3">
                                    <label class="form-label">邮箱密码 *</label>
                                    <input type="password" class="form-control" name="password" required>
                                    <div class="form-text">邮箱密码或应用专用密码</div>
                                </div>
                                <div class="mb-3">
                                    <label class="form-label">每日发送限制</label>
                                    <input type="number" class="form-control" name="daily_limit" value="100" min="1" required>
                                    <div class="form-text">每天最多发送的邮件数量</div>
                                </div>
                                <div class="mb-3">
                                    <div class="form-check">
                                        <input class="form-check-input" type="checkbox" name="use_tls" checked>
                                        <label class="form-check-label">启用TLS加密</label>
                                    </div>
                                    <div class="form-text">推荐启用以提高安全性</div>
                                </div>
                            </div>
                        </div>
                        
                        <hr>
                        <div class="d-flex justify-content-between">
                            <a href="/admin/mail" class="btn btn-secondary">取消</a>
                            <button type="submit" class="btn btn-primary">添加邮箱配置</button>
                        </div>
                    </form>
                </div>
            </div>
            
            <!-- 常用邮箱设置参考 -->
            <div class="card mt-4">
                <div class="card-header">
                    <h6>常用邮箱SMTP设置参考</h6>
                </div>
                <div class="card-body">
                    <div class="row">
                        <div class="col-md-4">
                            <strong>QQ邮箱</strong><br>
                            服务器: smtp.qq.com<br>
                            端口: 465 (SSL) 或 587 (TLS)<br>
                            <small class="text-muted">需要开启SMTP服务并使用授权码</small>
                        </div>
                        <div class="col-md-4">
                            <strong>其他邮箱</strong><br>
                            请查阅邮箱服务商<br>
                            的SMTP设置文档<br>
                            <small class="text-muted">常用端口: 465(SSL) 或 587(TLS)，推荐465</small>
                        </div>
                        <div class="col-md-4">
                            <strong>Gmail</strong><br>
                            服务器: smtp.gmail.com<br>
                            端口: 465 (SSL) 或 587 (TLS)<br>
                            <small class="text-muted">需要使用应用专用密码</small>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    </body>
    </html>
    """
    return render_template_string(template)

@app.route('/admin/mail/edit/<int:config_id>', methods=['GET', 'POST'])
@admin_required
def admin_mail_edit(config_id):
    """编辑邮箱配置"""
    config = MailConfig.query.get_or_404(config_id)
    
    if request.method == 'POST':
        try:
            config.name = request.form.get('name')
            config.smtp_server = request.form.get('smtp_server')
            config.smtp_port = int(request.form.get('smtp_port', 465))
            config.username = request.form.get('username')
            if request.form.get('password'):  # 只有输入新密码时才更新
                config.password = request.form.get('password')
            config.use_tls = bool(request.form.get('use_tls'))
            config.daily_limit = int(request.form.get('daily_limit', 100))
            
            db.session.commit()
            
            log_activity('INFO', 'admin', f'管理员 {current_user.email} 编辑邮箱配置: {config.name}', current_user.id, request.remote_addr)
            flash(f'邮箱配置 "{config.name}" 更新成功', 'admin')
            return redirect(url_for('admin_mail'))
            
        except Exception as e:
            db.session.rollback()
            flash(f'更新失败: {str(e)}', 'admin')
    
    template = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>编辑邮箱 - {{ config.name }}</title>
        <meta charset="utf-8">
        <link href="https://cdn.bootcdn.net/ajax/libs/bootstrap/5.1.3/css/bootstrap.min.css" rel="stylesheet">
    </head>
    <body>
        <nav class="navbar navbar-expand-lg navbar-dark bg-primary">
            <div class="container">
                <a class="navbar-brand" href="/">PubMed Literature Push</a>
                <div class="navbar-nav ms-auto">
                    <a class="nav-link" href="/admin">管理员</a>
                    <a class="nav-link" href="/admin/mail">邮箱管理</a>
                    <a class="nav-link" href="/logout">退出</a>
                </div>
            </div>
        </nav>

        <div class="container mt-4">
            <h2>编辑邮箱配置</h2>
            <nav aria-label="breadcrumb">
                <ol class="breadcrumb">
                    <li class="breadcrumb-item"><a href="/admin">管理员面板</a></li>
                    <li class="breadcrumb-item"><a href="/admin/mail">邮箱管理</a></li>
                    <li class="breadcrumb-item active">{{ config.name }}</li>
                </ol>
            </nav>
            
            {% with messages = get_flashed_messages(category_filter=['admin']) %}
                {% if messages %}
                    {% for message in messages %}
                        <div class="alert alert-danger alert-dismissible fade show">
                            {{ message }}
                            <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
                        </div>
                    {% endfor %}
                {% endif %}
            {% endwith %}
            
            <div class="card">
                <div class="card-body">
                    <form method="POST">
                        <div class="row">
                            <div class="col-md-6">
                                <div class="mb-3">
                                    <label class="form-label">配置名称 *</label>
                                    <input type="text" class="form-control" name="name" value="{{ config.name }}" required>
                                </div>
                                <div class="mb-3">
                                    <label class="form-label">SMTP服务器 *</label>
                                    <input type="text" class="form-control" name="smtp_server" value="{{ config.smtp_server }}" required>
                                </div>
                                <div class="mb-3">
                                    <label class="form-label">SMTP端口 *</label>
                                    <input type="number" class="form-control" name="smtp_port" value="{{ config.smtp_port }}" required>
                                </div>
                                <div class="mb-3">
                                    <label class="form-label">邮箱地址 *</label>
                                    <input type="email" class="form-control" name="username" value="{{ config.username }}" required>
                                </div>
                            </div>
                            <div class="col-md-6">
                                <div class="mb-3">
                                    <label class="form-label">邮箱密码</label>
                                    <input type="password" class="form-control" name="password" 
                                           placeholder="留空表示不修改密码">
                                    <div class="form-text">留空表示保持原密码不变</div>
                                </div>
                                <div class="mb-3">
                                    <label class="form-label">每日发送限制</label>
                                    <input type="number" class="form-control" name="daily_limit" value="{{ config.daily_limit }}" min="1" required>
                                </div>
                                <div class="mb-3">
                                    <div class="form-check">
                                        <input class="form-check-input" type="checkbox" name="use_tls" 
                                               {{ 'checked' if config.use_tls else '' }}>
                                        <label class="form-check-label">启用TLS加密</label>
                                    </div>
                                </div>
                                <div class="mb-3">
                                    <label class="form-label">使用状态</label>
                                    <div>
                                        {% if config.is_active %}
                                            <span class="badge bg-success">已启用</span>
                                        {% else %}
                                            <span class="badge bg-secondary">已禁用</span>
                                        {% endif %}
                                    </div>
                                    <small class="text-muted">
                                        今日已发送: {{ config.current_count }}/{{ config.daily_limit }}
                                    </small>
                                </div>
                            </div>
                        </div>
                        
                        <hr>
                        <div class="d-flex justify-content-between">
                            <a href="/admin/mail" class="btn btn-secondary">取消</a>
                            <button type="submit" class="btn btn-primary">保存更改</button>
                        </div>
                    </form>
                </div>
            </div>
        </div>
    </body>
    </html>
    """
    return render_template_string(template, config=config)

@app.route('/admin/mail/delete/<int:config_id>')
@admin_required
def admin_mail_delete(config_id):
    """删除邮箱配置"""
    try:
        config = MailConfig.query.get_or_404(config_id)
        name = config.name
        
        db.session.delete(config)
        db.session.commit()
        
        log_activity('INFO', 'admin', f'管理员 {current_user.email} 删除邮箱配置: {name}', current_user.id, request.remote_addr)
        flash(f'邮箱配置 "{name}" 删除成功', 'admin')
        
    except Exception as e:
        db.session.rollback()
        flash(f'删除失败: {str(e)}', 'admin')
    
    return redirect(url_for('admin_mail'))

@app.route('/admin/mail/enable/<int:config_id>')
@admin_required
def admin_mail_enable(config_id):
    """启用邮箱配置"""
    try:
        config = MailConfig.query.get_or_404(config_id)
        config.is_active = True
        db.session.commit()
        
        flash(f'邮箱配置 "{config.name}" 已启用', 'admin')
        
    except Exception as e:
        db.session.rollback()
        flash(f'启用失败: {str(e)}', 'admin')
    
    return redirect(url_for('admin_mail'))

@app.route('/admin/mail/disable/<int:config_id>')
@admin_required
def admin_mail_disable(config_id):
    """禁用邮箱配置"""
    try:
        config = MailConfig.query.get_or_404(config_id)
        config.is_active = False
        db.session.commit()
        
        flash(f'邮箱配置 "{config.name}" 已禁用', 'admin')
        
    except Exception as e:
        db.session.rollback()
        flash(f'禁用失败: {str(e)}', 'admin')
    
    return redirect(url_for('admin_mail'))

@app.route('/admin/mail/test/<int:config_id>')
@admin_required
def admin_mail_test(config_id):
    """测试邮箱配置"""
    try:
        config = MailConfig.query.get_or_404(config_id)
        
        # 发送测试邮件
        test_subject = "PubMed Literature Push - 邮箱配置测试"
        test_content = f"""
        <h3>邮箱配置测试</h3>
        <p>这是一封测试邮件，用于验证邮箱配置是否正确。</p>
        <ul>
            <li><strong>配置名称:</strong> {config.name}</li>
            <li><strong>发送邮箱:</strong> {config.username}</li>
            <li><strong>SMTP服务器:</strong> {config.smtp_server}:{config.smtp_port}</li>
            <li><strong>测试时间:</strong> {beijing_now().strftime('%Y-%m-%d %H:%M:%S')}</li>
        </ul>
        <p>如果您收到此邮件，说明邮箱配置正常工作。</p>
        """
        
        # 临时设置邮件配置进行测试
        from flask_mail import Message, Mail
        
        app.config['MAIL_SERVER'] = config.smtp_server
        app.config['MAIL_PORT'] = config.smtp_port
        app.config['MAIL_USERNAME'] = config.username
        app.config['MAIL_PASSWORD'] = config.password
        
        # 根据端口设置正确的加密方式（与发送邮件逻辑保持一致）
        if config.smtp_port == 465:
            # 465端口使用SSL，不使用TLS
            app.config['MAIL_USE_SSL'] = True
            app.config['MAIL_USE_TLS'] = False
        elif config.smtp_port == 587:
            # 587端口使用TLS，不使用SSL
            app.config['MAIL_USE_SSL'] = False
            app.config['MAIL_USE_TLS'] = True
        else:
            # 其他端口按配置设置
            app.config['MAIL_USE_TLS'] = config.use_tls
            app.config['MAIL_USE_SSL'] = False
        
        mail = Mail(app)
        
        msg = Message(
            subject=test_subject,
            sender=config.username,
            recipients=[current_user.email]  # 发送给当前管理员
        )
        msg.html = test_content
        
        mail.send(msg)
        
        # 标记配置为已测试
        config.last_used = beijing_now()
        db.session.commit()
        
        log_activity('INFO', 'admin', f'管理员 {current_user.email} 测试邮箱配置成功: {config.name}', current_user.id, request.remote_addr)
        flash(f'测试邮件已发送到 {current_user.email}，请检查邮箱', 'success')
        
    except Exception as e:
        error_msg = str(e)
        log_activity('ERROR', 'admin', f'邮箱配置测试失败: {config.name} - {error_msg}', current_user.id, request.remote_addr)
        
        # 提供详细的错误诊断信息
        if 'STARTTLS extension not supported' in error_msg:
            flash(f'STARTTLS错误：服务器 {config.smtp_server}:{config.smtp_port} 不支持STARTTLS。解决方案：1) 尝试端口465(SSL) 2) 检查服务器地址 3) 确认邮箱服务商设置', 'error')
        elif 'Connection unexpectedly closed' in error_msg:
            flash(f'连接意外关闭：1) 检查用户名密码 2) 确认邮箱已开启SMTP 3) 尝试不同端口(25/465/587) 4) 检查网络连接', 'error')
        elif 'Authentication failed' in error_msg or 'Login failed' in error_msg:
            flash(f'认证失败：请检查用户名和密码（应用专用密码）是否正确', 'error')
        elif 'Connection refused' in error_msg or 'timeout' in error_msg.lower():
            flash(f'连接失败：无法连接到 {config.smtp_server}:{config.smtp_port}。请检查服务器地址和端口', 'error')
        elif 'SSL' in error_msg and config.smtp_port == 587:
            flash(f'SSL/TLS错误：端口587应使用STARTTLS，尝试检查服务器是否支持', 'error')
        else:
            flash(f'邮件测试失败: {error_msg}', 'error')
    
    return redirect(url_for('admin_mail'))

# ========== AI管理相关路由 ==========

@app.route('/admin/ai')
@admin_required
def admin_ai():
    """AI管理页面"""
    providers = AISetting.query.all()
    # 获取AI相关的系统设置
    ai_settings = {
        'ai_query_builder_enabled': SystemSetting.get_setting('ai_query_builder_enabled', 'false'),
        'ai_translation_enabled': SystemSetting.get_setting('ai_translation_enabled', 'false'),
        'ai_translation_batch_size': SystemSetting.get_setting('ai_translation_batch_size', '5'),
        'ai_translation_batch_delay': SystemSetting.get_setting('ai_translation_batch_delay', '3'),
        # 添加已保存的提供商和模型配置
        'ai_query_builder_provider_id': SystemSetting.get_setting('ai_query_builder_provider_id', ''),
        'ai_query_builder_model_id': SystemSetting.get_setting('ai_query_builder_model_id', ''),
        'ai_translation_provider_id': SystemSetting.get_setting('ai_translation_provider_id', ''),
        'ai_translation_model_id': SystemSetting.get_setting('ai_translation_model_id', ''),
    }
    
    return render_template_string(get_ai_management_template(), 
                                providers=providers, 
                                ai_settings=ai_settings)

@app.route('/admin/ai/provider/add', methods=['GET', 'POST'])
@admin_required
def admin_ai_provider_add():
    """添加AI提供商"""
    if request.method == 'POST':
        try:
            provider_name = request.form.get('provider_name', '').strip()
            base_url = request.form.get('base_url', '').strip()
            api_key = request.form.get('api_key', '').strip()
            
            if not all([provider_name, base_url, api_key]):
                flash('所有字段都必须填写', 'error')
                return render_template_string(get_ai_provider_form_template())
            
            # 测试连接
            success, message = ai_service.test_connection(base_url, api_key)
            if not success:
                flash(f'连接测试失败: {message}', 'error')
                return render_template_string(get_ai_provider_form_template())
            
            # 保存提供商
            provider = AISetting(
                provider_name=provider_name,
                base_url=base_url,
                is_active=True
            )
            provider.set_encrypted_api_key(api_key)
            
            db.session.add(provider)
            db.session.commit()
            
            # 获取并保存模型列表
            models = ai_service.fetch_models(provider)
            for model_info in models:
                model = AIModel(
                    provider_id=provider.id,
                    model_name=model_info['name'],
                    model_id=model_info['id'],
                    model_type='general',  # 默认类型
                    is_available=True
                )
                db.session.add(model)
            
            db.session.commit()
            
            log_activity('INFO', 'admin', f'管理员 {current_user.email} 添加AI提供商: {provider_name}', current_user.id, request.remote_addr)
            flash(f'AI提供商添加成功，发现 {len(models)} 个模型', 'success')
            return redirect(url_for('admin_ai'))
            
        except Exception as e:
            db.session.rollback()
            log_activity('ERROR', 'admin', f'添加AI提供商失败: {str(e)}', current_user.id, request.remote_addr)
            flash(f'添加失败: {str(e)}', 'error')
    
    return render_template_string(get_ai_provider_form_template())

@app.route('/admin/ai/provider/<int:provider_id>/delete', methods=['POST'])
@admin_required
def admin_ai_provider_delete(provider_id):
    """删除AI提供商"""
    try:
        provider = AISetting.query.get_or_404(provider_id)
        provider_name = provider.provider_name
        
        db.session.delete(provider)
        db.session.commit()
        
        log_activity('INFO', 'admin', f'管理员 {current_user.email} 删除AI提供商: {provider_name}', current_user.id, request.remote_addr)
        flash('AI提供商删除成功', 'success')
    except Exception as e:
        db.session.rollback()
        log_activity('ERROR', 'admin', f'删除AI提供商失败: {str(e)}', current_user.id, request.remote_addr)
        flash(f'删除失败: {str(e)}', 'error')
    
    return redirect(url_for('admin_ai'))

@app.route('/admin/ai/provider/<int:provider_id>/toggle', methods=['POST'])
@admin_required
def admin_ai_provider_toggle(provider_id):
    """切换AI提供商状态"""
    try:
        provider = AISetting.query.get_or_404(provider_id)
        
        # 如果要激活此提供商，先禁用其他提供商
        if not provider.is_active:
            AISetting.query.update({AISetting.is_active: False})
            provider.is_active = True
        else:
            provider.is_active = False
        
        db.session.commit()
        
        status = "激活" if provider.is_active else "禁用"
        log_activity('INFO', 'admin', f'管理员 {current_user.email} {status}AI提供商: {provider.provider_name}', current_user.id, request.remote_addr)
        flash(f'AI提供商已{status}', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'操作失败: {str(e)}', 'error')
    
    return redirect(url_for('admin_ai'))

@app.route('/admin/ai/model/<int:model_id>/set-type', methods=['POST'])
@admin_required
def admin_ai_model_set_type(model_id):
    """设置模型类型"""
    try:
        model = AIModel.query.get_or_404(model_id)
        model_type = request.form.get('model_type', 'general')
        
        if model_type not in ['query_builder', 'translator', 'general']:
            flash('无效的模型类型', 'error')
            return redirect(url_for('admin_ai'))
        
        model.model_type = model_type
        db.session.commit()
        
        log_activity('INFO', 'admin', f'管理员 {current_user.email} 设置模型类型: {model.model_name} -> {model_type}', current_user.id, request.remote_addr)
        flash('模型类型设置成功', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'设置失败: {str(e)}', 'error')
    
    return redirect(url_for('admin_ai'))

@app.route('/admin/ai/settings', methods=['POST'])
@admin_required
def admin_ai_settings():
    """保存AI功能设置"""
    try:
        # 保存AI功能开关
        SystemSetting.set_setting('ai_query_builder_enabled', request.form.get('ai_query_builder_enabled', 'false'), '启用AI检索式生成', 'ai')
        SystemSetting.set_setting('ai_translation_enabled', request.form.get('ai_translation_enabled', 'false'), '启用AI摘要翻译', 'ai')
        
        # 保存批量翻译设置
        batch_size = request.form.get('ai_translation_batch_size', '5')
        batch_delay = request.form.get('ai_translation_batch_delay', '3')
        
        try:
            batch_size = max(1, min(20, int(batch_size)))
            batch_delay = max(1, min(60, int(batch_delay)))
        except ValueError:
            flash('批量设置参数无效，使用默认值', 'warning')
            batch_size = 5
            batch_delay = 3
        
        SystemSetting.set_setting('ai_translation_batch_size', str(batch_size), '每批翻译数量', 'ai')
        SystemSetting.set_setting('ai_translation_batch_delay', str(batch_delay), '批次间隔时间(秒)', 'ai')
        
        log_activity('INFO', 'admin', f'管理员 {current_user.email} 更新AI功能设置', current_user.id, request.remote_addr)
        flash('AI设置保存成功', 'success')
    except Exception as e:
        log_activity('ERROR', 'admin', f'AI设置保存失败: {str(e)}', current_user.id, request.remote_addr)
        flash(f'保存失败: {str(e)}', 'error')
    
    return redirect(url_for('admin_ai'))

@app.route('/admin/ai/config/query-builder', methods=['POST'])
@admin_required
def admin_ai_config_query_builder():
    """配置检索式生成"""
    try:
        # 保存功能开关
        enabled = request.form.get('enabled', 'false')
        SystemSetting.set_setting('ai_query_builder_enabled', enabled, '启用AI检索式生成', 'ai')
        
        # 保存提供商和模型选择
        provider_id = request.form.get('provider_id', '').strip()
        model_id = request.form.get('model_id', '').strip()
        
        if provider_id and model_id:
            SystemSetting.set_setting('ai_query_builder_provider_id', provider_id, '检索式生成提供商ID', 'ai')
            SystemSetting.set_setting('ai_query_builder_model_id', model_id, '检索式生成模型ID', 'ai')
        
        log_activity('INFO', 'admin', f'管理员 {current_user.email} 更新检索式生成配置', current_user.id, request.remote_addr)
        flash('检索式生成配置保存成功', 'success')
    except Exception as e:
        log_activity('ERROR', 'admin', f'检索式生成配置保存失败: {str(e)}', current_user.id, request.remote_addr)
        flash(f'配置保存失败: {str(e)}', 'error')
    
    return redirect(url_for('admin_ai'))

@app.route('/admin/ai/config/translator', methods=['POST'])
@admin_required
def admin_ai_config_translator():
    """配置摘要翻译"""
    try:
        # 保存功能开关
        enabled = request.form.get('enabled', 'false')
        SystemSetting.set_setting('ai_translation_enabled', enabled, '启用AI摘要翻译', 'ai')
        
        # 保存提供商和模型选择
        provider_id = request.form.get('provider_id', '').strip()
        model_id = request.form.get('model_id', '').strip()
        
        if provider_id and model_id:
            SystemSetting.set_setting('ai_translation_provider_id', provider_id, '翻译提供商ID', 'ai')
            SystemSetting.set_setting('ai_translation_model_id', model_id, '翻译模型ID', 'ai')
        
        # 保存批量翻译设置
        batch_size = request.form.get('batch_size', '5')
        batch_delay = request.form.get('batch_delay', '3')
        
        try:
            batch_size = max(1, min(20, int(batch_size)))
            batch_delay = max(1, min(60, int(batch_delay)))
        except ValueError:
            flash('批量设置参数无效，使用默认值', 'warning')
            batch_size = 5
            batch_delay = 3
        
        SystemSetting.set_setting('ai_translation_batch_size', str(batch_size), '每批翻译数量', 'ai')
        SystemSetting.set_setting('ai_translation_batch_delay', str(batch_delay), '批次间隔时间(秒)', 'ai')
        
        log_activity('INFO', 'admin', f'管理员 {current_user.email} 更新翻译配置', current_user.id, request.remote_addr)
        flash('翻译配置保存成功', 'success')
    except Exception as e:
        log_activity('ERROR', 'admin', f'翻译配置保存失败: {str(e)}', current_user.id, request.remote_addr)
        flash(f'配置保存失败: {str(e)}', 'error')
    
    return redirect(url_for('admin_ai'))

@app.route('/admin/ai/test/query', methods=['POST'])
@admin_required
def admin_ai_test_query():
    """测试AI检索式生成"""
    try:
        keywords = request.form.get('keywords', '').strip()
        if not keywords:
            return jsonify({'success': False, 'message': '请输入关键词'})
        
        # 临时启用AI检索式生成进行测试
        original_setting = SystemSetting.get_setting('ai_query_builder_enabled', 'false')
        SystemSetting.set_setting('ai_query_builder_enabled', 'true', '启用AI检索式生成', 'ai')
        
        try:
            query = ai_service.build_pubmed_query(keywords)
            app.logger.info(f"测试生成的检索式长度: {len(query)} 字符")
            return jsonify({
                'success': True, 
                'query': query,
                'message': f'测试成功。原关键词: {keywords}',
                'debug_info': f'生成的检索式长度: {len(query)} 字符'
            })
        finally:
            # 恢复原设置
            SystemSetting.set_setting('ai_query_builder_enabled', original_setting, '启用AI检索式生成', 'ai')
            
    except Exception as e:
        return jsonify({'success': False, 'message': f'测试失败: {str(e)}'})

@app.route('/admin/ai/test/translation', methods=['POST'])
@admin_required
def admin_ai_test_translation():
    """测试AI翻译功能"""
    try:
        abstract = request.form.get('abstract', '').strip()
        if not abstract:
            return jsonify({'success': False, 'message': '请输入英文摘要'})
        
        # 临时启用AI翻译进行测试
        original_setting = SystemSetting.get_setting('ai_translation_enabled', 'false')
        SystemSetting.set_setting('ai_translation_enabled', 'true', '启用AI摘要翻译', 'ai')
        
        try:
            translation = ai_service.translate_abstract(abstract)
            return jsonify({
                'success': True, 
                'translation': translation,
                'message': f'翻译成功。原文长度: {len(abstract)} 字符'
            })
        finally:
            # 恢复原设置
            SystemSetting.set_setting('ai_translation_enabled', original_setting, '启用AI摘要翻译', 'ai')
            
    except Exception as e:
        return jsonify({'success': False, 'message': f'翻译失败: {str(e)}'})

@app.route('/admin/ai/prompts')
@admin_required
def admin_ai_prompts():
    """AI提示词管理"""
    query_prompts = AIPromptTemplate.query.filter_by(template_type='query_builder').all()
    translator_prompts = AIPromptTemplate.query.filter_by(template_type='translator').all()
    
    return render_template_string(get_ai_prompts_template(), 
                                query_prompts=query_prompts,
                                translator_prompts=translator_prompts)

@app.route('/admin/ai/prompt/save', methods=['POST'])
@admin_required
def admin_ai_prompt_save():
    """保存AI提示词"""
    try:
        template_type = request.form.get('template_type')
        prompt_content = request.form.get('prompt_content', '').strip()
        
        if not template_type or not prompt_content:
            flash('提示词类型和内容不能为空', 'error')
            return redirect(url_for('admin_ai_prompts'))
        
        if template_type not in ['query_builder', 'translator']:
            flash('无效的提示词类型', 'error')
            return redirect(url_for('admin_ai_prompts'))
        
        # 先将该类型的所有提示词设为非默认
        AIPromptTemplate.query.filter_by(template_type=template_type).update({
            AIPromptTemplate.is_default: False
        })
        
        # 创建新的默认提示词
        new_template = AIPromptTemplate(
            template_type=template_type,
            prompt_content=prompt_content,
            is_default=True
        )
        db.session.add(new_template)
        db.session.commit()
        
        log_activity('INFO', 'admin', f'管理员 {current_user.email} 更新了 {template_type} 提示词模板', current_user.id, request.remote_addr)
        flash('提示词模板保存成功', 'success')
        
    except Exception as e:
        db.session.rollback()
        log_activity('ERROR', 'admin', f'提示词模板保存失败: {str(e)}', current_user.id, request.remote_addr)
        flash(f'保存失败: {str(e)}', 'error')
    
    return redirect(url_for('admin_ai_prompts'))

@app.route('/admin/ai/prompt/<int:template_id>/delete', methods=['POST'])
@admin_required
def admin_ai_prompt_delete(template_id):
    """删除AI提示词"""
    try:
        template = AIPromptTemplate.query.get_or_404(template_id)
        
        # 防止删除最后一个默认模板
        if template.is_default:
            other_templates = AIPromptTemplate.query.filter_by(
                template_type=template.template_type
            ).filter(AIPromptTemplate.id != template_id).all()
            
            if not other_templates:
                flash('不能删除最后一个模板', 'error')
                return redirect(url_for('admin_ai_prompts'))
            
            # 如果删除的是默认模板，将最新的一个设为默认
            if other_templates:
                other_templates[-1].is_default = True
        
        db.session.delete(template)
        db.session.commit()
        
        log_activity('INFO', 'admin', f'管理员 {current_user.email} 删除了提示词模板 {template_id}', current_user.id, request.remote_addr)
        flash('提示词模板删除成功', 'success')
        
    except Exception as e:
        db.session.rollback()
        log_activity('ERROR', 'admin', f'删除提示词模板失败: {str(e)}', current_user.id, request.remote_addr)
        flash(f'删除失败: {str(e)}', 'error')
    
    return redirect(url_for('admin_ai_prompts'))

@app.route('/admin/ai/prompt/<int:template_id>/set-default', methods=['POST'])
@admin_required
def admin_ai_prompt_set_default(template_id):
    """设置默认提示词"""
    try:
        template = AIPromptTemplate.query.get_or_404(template_id)
        
        # 先将同类型的所有提示词设为非默认
        AIPromptTemplate.query.filter_by(template_type=template.template_type).update({
            AIPromptTemplate.is_default: False
        })
        
        # 设置当前为默认
        template.is_default = True
        db.session.commit()
        
        log_activity('INFO', 'admin', f'管理员 {current_user.email} 设置提示词模板 {template_id} 为默认', current_user.id, request.remote_addr)
        flash('默认提示词设置成功', 'success')
        
    except Exception as e:
        db.session.rollback()
        log_activity('ERROR', 'admin', f'设置默认提示词失败: {str(e)}', current_user.id, request.remote_addr)
        flash(f'设置失败: {str(e)}', 'error')
    
    return redirect(url_for('admin_ai_prompts'))

# 为模板添加is_admin函数
@app.context_processor
def inject_admin_check():
    return dict(is_admin=is_admin)

# 编辑订阅参数
@app.route('/edit_subscription/<int:subscription_id>')
@login_required
def edit_subscription(subscription_id):
    """编辑订阅参数页面"""
    subscription = Subscription.query.filter_by(id=subscription_id, user_id=current_user.id).first()
    if not subscription:
        flash('订阅不存在', 'error')
        return redirect(url_for('profile'))
    
    edit_template = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>编辑订阅 - PubMed Literature Push</title>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <link href="https://cdn.bootcdn.net/ajax/libs/bootstrap/5.1.3/css/bootstrap.min.css" rel="stylesheet">
        <link href="https://cdn.bootcdn.net/ajax/libs/font-awesome/6.0.0/css/all.min.css" rel="stylesheet">
    </head>
    <body>
        <nav class="navbar navbar-expand-lg navbar-dark bg-primary">
            <div class="container">
                <a class="navbar-brand" href="/">📚 PubMed Literature Push</a>
                <div class="navbar-nav ms-auto">
                    <a class="nav-link" href="/">首页</a>
                    <a class="nav-link" href="/subscriptions">我的订阅</a>
                    <a class="nav-link" href="/profile">个人设置</a>
                    <a class="nav-link" href="/logout">退出</a>
                </div>
            </div>
        </nav>
        
        <div class="container mt-4">
            <div class="row">
                <div class="col-md-8 mx-auto">
                    <div class="card">
                        <div class="card-header">
                            <h5><i class="fas fa-edit"></i> 编辑订阅推送设置</h5>
                            <p class="mb-0 text-muted">修改订阅"{{ subscription.keywords }}"的推送参数</p>
                        </div>
                        <div class="card-body">
                            {% with messages = get_flashed_messages(with_categories=true) %}
                                {% if messages %}
                                    {% for category, message in messages %}
                                        <div class="alert alert-{{ 'danger' if category == 'error' else 'success' }} alert-dismissible">
                                            {{ message }}
                                            <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
                                        </div>
                                    {% endfor %}
                                {% endif %}
                            {% endwith %}
                            
                            <form method="POST" action="/update_subscription/{{ subscription.id }}">
                                <!-- 基本信息 -->
                                <div class="mb-3">
                                    <label class="form-label">关键词 (只读)</label>
                                    <input type="text" class="form-control" value="{{ subscription.keywords }}" readonly>
                                </div>
                                
                                <hr>
                                
                                <!-- 搜索参数 -->
                                <h6><i class="fas fa-search"></i> 搜索参数</h6>
                                
                                <div class="row mb-3">
                                    <div class="col-md-6">
                                        <label class="form-label">最大结果数</label>
                                        <select class="form-control" name="max_results" required>
                                            <option value="50" {{ 'selected' if subscription.max_results == 50 else '' }}>50篇</option>
                                            <option value="100" {{ 'selected' if subscription.max_results == 100 else '' }}>100篇</option>
                                            <option value="200" {{ 'selected' if subscription.max_results == 200 else '' }}>200篇</option>
                                            <option value="500" {{ 'selected' if subscription.max_results == 500 else '' }}>500篇</option>
                                        </select>
                                    </div>
                                    <div class="col-md-6">
                                        <label class="form-label">搜索天数</label>
                                        <div class="form-control-plaintext">
                                            <span class="badge bg-info">
                                                {{ subscription.days_back }}天
                                                ({{ '每日推送' if subscription.push_frequency == 'daily' else '每周推送' if subscription.push_frequency == 'weekly' else '每月推送' }}自动设置)
                                            </span>
                                        </div>
                                    </div>
                                </div>
                                
                                <div class="mb-3">
                                    <div class="form-check">
                                        <input class="form-check-input" type="checkbox" name="exclude_no_issn" 
                                               {{ 'checked' if subscription.exclude_no_issn else '' }}>
                                        <label class="form-check-label">排除无ISSN信息的文献</label>
                                    </div>
                                </div>
                                
                                <hr>
                                
                                <!-- 期刊质量筛选 -->
                                <h6><i class="fas fa-filter"></i> 期刊质量筛选</h6>
                                
                                <!-- JCR筛选 -->
                                <div class="mb-3">
                                    <label class="form-label">JCR分区筛选</label>
                                    <div class="row">
                                        {% set current_jcr = subscription.get_jcr_quartiles() %}
                                        {% for quartile in ['Q1', 'Q2', 'Q3', 'Q4'] %}
                                        <div class="col-6">
                                            <div class="form-check">
                                                <input class="form-check-input" type="checkbox" name="jcr_quartile" value="{{ quartile }}"
                                                       {{ 'checked' if quartile in current_jcr else '' }}>
                                                <label class="form-check-label">{{ quartile }}</label>
                                            </div>
                                        </div>
                                        {% endfor %}
                                    </div>
                                </div>
                                
                                <div class="mb-3">
                                    <label class="form-label">最小影响因子</label>
                                    <input type="number" class="form-control" name="min_if" step="0.1" 
                                           value="{{ subscription.min_impact_factor or '' }}" placeholder="如 1.5">
                                </div>
                                
                                <!-- 中科院筛选 -->
                                <div class="mb-3">
                                    <label class="form-label">中科院分区筛选</label>
                                    <div class="row">
                                        {% set current_cas = subscription.get_cas_categories() %}
                                        {% for category in ['1', '2', '3', '4'] %}
                                        <div class="col-6">
                                            <div class="form-check">
                                                <input class="form-check-input" type="checkbox" name="cas_category" value="{{ category }}"
                                                       {{ 'checked' if category in current_cas else '' }}>
                                                <label class="form-check-label">{{ category }}区</label>
                                            </div>
                                        </div>
                                        {% endfor %}
                                    </div>
                                </div>
                                
                                <div class="mb-3">
                                    <div class="form-check">
                                        <input class="form-check-input" type="checkbox" name="cas_top_only"
                                               {{ 'checked' if subscription.cas_top_only else '' }}>
                                        <label class="form-check-label">只显示Top期刊</label>
                                    </div>
                                </div>
                                
                                <hr>
                                
                                <!-- 推送设置 -->
                                <h6><i class="fas fa-bell"></i> 推送设置</h6>
                                
                                <div class="row mb-3">
                                    <div class="col-md-6">
                                        <label class="form-label">推送频率</label>
                                        <select class="form-control" name="push_frequency" id="pushFrequency" required>
                                            {% set allowed_freqs = current_user.get_allowed_frequencies() %}
                                            {% if current_user.is_admin or 'daily' in allowed_freqs %}
                                                <option value="daily" {{ 'selected' if subscription.push_frequency == 'daily' else '' }}>每日推送</option>
                                            {% endif %}
                                            {% if current_user.is_admin or 'weekly' in allowed_freqs %}
                                                <option value="weekly" {{ 'selected' if subscription.push_frequency == 'weekly' else '' }}>每周推送</option>
                                            {% endif %}
                                            {% if current_user.is_admin or 'monthly' in allowed_freqs %}
                                                <option value="monthly" {{ 'selected' if subscription.push_frequency == 'monthly' else '' }}>每月推送</option>
                                            {% endif %}
                                        </select>
                                        {% if not current_user.is_admin %}
                                            <small class="form-text text-warning">推送频率受管理员权限限制</small>
                                        {% endif %}
                                    </div>
                                    <div class="col-md-6">
                                        <label class="form-label">推送时间</label>
                                        <input type="time" class="form-control" name="push_time" 
                                               value="{{ subscription.push_time or '09:00' }}" required>
                                    </div>
                                </div>
                                
                                <!-- 每周推送设置 -->
                                <div class="row mb-3" id="weeklySettings" style="display: {{ 'block' if subscription.push_frequency == 'weekly' else 'none' }}">
                                    <div class="col-md-12">
                                        <label class="form-label">每周推送日</label>
                                        <select class="form-control" name="push_day">
                                            <option value="monday" {{ 'selected' if subscription.push_day == 'monday' else '' }}>周一</option>
                                            <option value="tuesday" {{ 'selected' if subscription.push_day == 'tuesday' else '' }}>周二</option>
                                            <option value="wednesday" {{ 'selected' if subscription.push_day == 'wednesday' else '' }}>周三</option>
                                            <option value="thursday" {{ 'selected' if subscription.push_day == 'thursday' else '' }}>周四</option>
                                            <option value="friday" {{ 'selected' if subscription.push_day == 'friday' else '' }}>周五</option>
                                            <option value="saturday" {{ 'selected' if subscription.push_day == 'saturday' else '' }}>周六</option>
                                            <option value="sunday" {{ 'selected' if subscription.push_day == 'sunday' else '' }}>周日</option>
                                        </select>
                                    </div>
                                </div>
                                
                                <!-- 每月推送设置 -->
                                <div class="row mb-3" id="monthlySettings" style="display: {{ 'block' if subscription.push_frequency == 'monthly' else 'none' }}">
                                    <div class="col-md-12">
                                        <label class="form-label">每月推送日</label>
                                        <select class="form-control" name="push_month_day">
                                            {% for i in range(1, 29) %}
                                            <option value="{{ i }}" {{ 'selected' if subscription.push_month_day == i else '' }}>{{ i }}号</option>
                                            {% endfor %}
                                        </select>
                                        <small class="text-muted">为避免月末日期问题，最多选择28号</small>
                                    </div>
                                </div>
                                
                                <div class="mb-3">
                                    <div class="form-check">
                                        <input class="form-check-input" type="checkbox" name="is_active"
                                               {{ 'checked' if subscription.is_active else '' }}>
                                        <label class="form-check-label">启用此订阅</label>
                                    </div>
                                </div>
                                
                                <hr>
                                
                                <div class="d-flex justify-content-between">
                                    <a href="/profile" class="btn btn-secondary">
                                        <i class="fas fa-arrow-left"></i> 返回
                                    </a>
                                    <button type="submit" class="btn btn-primary">
                                        <i class="fas fa-save"></i> 保存设置
                                    </button>
                                </div>
                            </form>
                        </div>
                    </div>
                </div>
            </div>
        </div>
        
        <script>
            // 根据推送频率显示/隐藏相关选项
            document.addEventListener('DOMContentLoaded', function() {
                const pushFrequency = document.getElementById('pushFrequency');
                const weeklySettings = document.getElementById('weeklySettings');
                const monthlySettings = document.getElementById('monthlySettings');
                
                function toggleSettings() {
                    if (pushFrequency.value === 'weekly') {
                        weeklySettings.style.display = 'block';
                        monthlySettings.style.display = 'none';
                    } else if (pushFrequency.value === 'monthly') {
                        weeklySettings.style.display = 'none';
                        monthlySettings.style.display = 'block';
                    } else {
                        weeklySettings.style.display = 'none';
                        monthlySettings.style.display = 'none';
                    }
                }
                
                pushFrequency.addEventListener('change', toggleSettings);
                toggleSettings(); // 初始化显示状态
            });
        </script>
        <script src="https://cdn.bootcdn.net/ajax/libs/bootstrap/5.1.3/js/bootstrap.bundle.min.js"></script>
    </body>
    </html>
    """
    
    return render_template_string(edit_template, subscription=subscription)

@app.route('/update_subscription/<int:subscription_id>', methods=['POST'])
@login_required
def update_subscription(subscription_id):
    """更新订阅参数"""
    subscription = Subscription.query.filter_by(id=subscription_id, user_id=current_user.id).first()
    if not subscription:
        flash('订阅不存在', 'error')
        return redirect(url_for('profile'))
    
    try:
        # 更新搜索参数
        subscription.max_results = int(request.form.get('max_results', 200))
        subscription.exclude_no_issn = request.form.get('exclude_no_issn') == 'on'
        
        # 更新JCR筛选参数
        jcr_quartiles = request.form.getlist('jcr_quartile')
        if jcr_quartiles:
            subscription.set_jcr_quartiles(jcr_quartiles)
        else:
            subscription.jcr_quartiles = None
        
        min_if = request.form.get('min_if', '').strip()
        if min_if:
            subscription.min_impact_factor = float(min_if)
        else:
            subscription.min_impact_factor = None
        
        # 更新中科院筛选参数
        cas_categories = request.form.getlist('cas_category')
        if cas_categories:
            subscription.set_cas_categories(cas_categories)
        else:
            subscription.cas_categories = None
        
        subscription.cas_top_only = request.form.get('cas_top_only') == 'on'
        
        # 更新推送设置
        subscription.push_frequency = request.form.get('push_frequency', 'daily')
        subscription.push_time = request.form.get('push_time', '09:00')
        subscription.push_day = request.form.get('push_day', 'monday')
        subscription.push_month_day = int(request.form.get('push_month_day', 1))
        subscription.is_active = request.form.get('is_active') == 'on'
        
        # 根据新的推送频率更新搜索天数
        subscription.days_back = get_search_days_by_frequency(subscription.push_frequency)
        
        db.session.commit()
        log_activity('INFO', 'subscription', f'用户 {current_user.email} 更新订阅设置: {subscription.keywords}', current_user.id, request.remote_addr)
        flash('订阅设置更新成功！', 'success')
        
    except Exception as e:
        db.session.rollback()
        flash(f'更新失败: {str(e)}', 'error')
    
    return redirect(url_for('edit_subscription', subscription_id=subscription_id))

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        
        # 检查并修复数据库表结构
        def check_and_fix_database_schema():
            """检查并修复数据库表结构与模型定义的一致性"""
            try:
                from sqlalchemy import inspect, text
                
                inspector = inspect(db.engine)
                
                # 检查User表是否缺少字段
                user_columns = {col['name'] for col in inspector.get_columns('user')}
                
                # User模型应有的字段
                expected_user_fields = {
                    'push_month_day': 'INTEGER DEFAULT 1',
                    'last_push': 'DATETIME',
                    'max_subscriptions': 'INTEGER DEFAULT 3',
                    'allowed_frequencies': 'TEXT DEFAULT "weekly"'
                }
                
                # 检查缺失的User字段
                missing_user_fields = []
                for field_name, field_def in expected_user_fields.items():
                    if field_name not in user_columns:
                        missing_user_fields.append((field_name, field_def))
                
                if missing_user_fields:
                    print(f"发现User表缺失 {len(missing_user_fields)} 个字段，正在修复...")
                    
                    # 使用原生SQL添加字段
                    for field_name, field_def in missing_user_fields:
                        try:
                            with db.engine.connect() as conn:
                                conn.execute(text(f'ALTER TABLE user ADD COLUMN {field_name} {field_def}'))
                                conn.commit()
                            print(f"已添加User字段: {field_name}")
                        except Exception as e:
                            if 'duplicate column name' not in str(e):
                                print(f"添加User字段 {field_name} 失败: {e}")
                    
                    print("User表结构修复完成")
                else:
                    print("User表结构检查通过")
                
                # 检查Subscription表是否缺少字段
                subscription_columns = {col['name'] for col in inspector.get_columns('subscription')}
                
                # Subscription模型应有的字段
                expected_subscription_fields = {
                    'max_results': 'INTEGER DEFAULT 10000',
                    'days_back': 'INTEGER DEFAULT 30',
                    'exclude_no_issn': 'BOOLEAN DEFAULT 1',
                    'jcr_quartiles': 'TEXT',
                    'min_impact_factor': 'FLOAT',
                    'cas_categories': 'TEXT',
                    'cas_top_only': 'BOOLEAN DEFAULT 0',
                    'push_frequency': 'VARCHAR(20) DEFAULT "daily"',
                    'push_time': 'VARCHAR(5) DEFAULT "09:00"',
                    'push_day': 'VARCHAR(10) DEFAULT "monday"',
                    'push_month_day': 'INTEGER DEFAULT 1'
                }
                
                # 检查缺失的Subscription字段
                missing_subscription_fields = []
                for field_name, field_def in expected_subscription_fields.items():
                    if field_name not in subscription_columns:
                        missing_subscription_fields.append((field_name, field_def))
                
                if missing_subscription_fields:
                    print(f"发现Subscription表缺失 {len(missing_subscription_fields)} 个字段，正在修复...")
                    
                    # 使用原生SQL添加字段
                    for field_name, field_def in missing_subscription_fields:
                        try:
                            with db.engine.connect() as conn:
                                conn.execute(text(f'ALTER TABLE subscription ADD COLUMN {field_name} {field_def}'))
                                conn.commit()
                            print(f"已添加Subscription字段: {field_name}")
                        except Exception as e:
                            if 'duplicate column name' not in str(e):
                                print(f"添加Subscription字段 {field_name} 失败: {e}")
                    
                    print("Subscription表结构修复完成")
                else:
                    print("Subscription表结构检查通过")
                    
            except Exception as e:
                print(f"数据库表结构检查失败: {e}")
                # 如果检查失败，尝试重新创建表
                try:
                    print("尝试重新创建数据库表...")
                    db.drop_all()
                    db.create_all()
                    print("数据库表重新创建完成")
                except Exception as recreate_error:
                    print(f"重新创建表失败: {recreate_error}")
        
        # 执行表结构检查和修复
        check_and_fix_database_schema()
        
        # 初始化系统设置
        if not SystemSetting.query.first():
            # 从环境变量读取默认值，如果没有则使用硬编码默认值
            default_settings = [
                ('pubmed_max_results', os.environ.get('PUBMED_MAX_RESULTS', '10000'), 'PubMed每次最大检索数量', 'pubmed'),
                ('pubmed_timeout', os.environ.get('PUBMED_TIMEOUT', '10'), 'PubMed请求超时时间(秒)', 'pubmed'),
                ('pubmed_api_key', os.environ.get('PUBMED_API_KEY', ''), 'PubMed API Key', 'pubmed'),
                ('push_frequency', 'daily', '默认推送频率', 'push'),
                ('push_time', '09:00', '默认推送时间', 'push'),
                ('push_day', 'monday', '默认每周推送日(周几)', 'push'),
                ('push_month_day', '1', '默认每月推送日(几号)', 'push'),
                ('push_daily_time', '09:00', '默认每日推送时间', 'push'),
                ('push_max_articles', '50', '每次推送最大文章数', 'push'),
                ('push_check_frequency', '1', '定时推送检查频率(小时)', 'push'),
                ('push_enabled', 'true', '启用自动推送', 'push'),
                ('mail_server', 'smtp.gmail.com', 'SMTP服务器地址', 'mail'),
                ('mail_port', '587', 'SMTP端口', 'mail'),
                ('mail_username', '', '发送邮箱', 'mail'),
                ('mail_password', '', '邮箱密码/应用密码', 'mail'),
                ('mail_use_tls', 'true', '启用TLS加密', 'mail'),
                ('system_name', 'PubMed Literature Push', '系统名称', 'system'),
                ('log_retention_days', '30', '日志保留天数', 'system'),
                ('user_registration_enabled', 'true', '允许用户注册', 'system'),
                ('max_articles_limit', '1000', '文章数量上限', 'system'),
                ('cleanup_articles_count', '100', '单次清理文章数量', 'system'),
                # AI功能设置
                ('ai_query_builder_enabled', 'true', '启用AI检索式生成', 'ai'),
                ('ai_translation_enabled', 'true', '启用AI摘要翻译', 'ai'),
                ('ai_translation_batch_size', '20', '每批翻译数量', 'ai'),
                ('ai_translation_batch_delay', '5', '批次间隔时间(秒)', 'ai'),
            ]
            
            for key, value, desc, category in default_settings:
                SystemSetting.set_setting(key, value, desc, category)
        
        # 每次启动时同步环境变量到数据库（如果环境变量有设置）
        env_sync_settings = {
            'pubmed_api_key': os.environ.get('PUBMED_API_KEY'),
            'pubmed_max_results': os.environ.get('PUBMED_MAX_RESULTS'),
            'pubmed_timeout': os.environ.get('PUBMED_TIMEOUT'),
        }
        
        for key, env_value in env_sync_settings.items():
            if env_value:  # 只有环境变量有值时才更新
                current_value = SystemSetting.get_setting(key)
                if current_value != env_value:
                    desc_map = {
                        'pubmed_api_key': 'PubMed API Key',
                        'pubmed_max_results': 'PubMed每次最大检索数量',
                        'pubmed_timeout': 'PubMed请求超时时间(秒)',
                    }
                    SystemSetting.set_setting(key, env_value, desc_map.get(key, ''), 'pubmed')
                    app.logger.info(f"已从环境变量同步配置: {key} = {env_value}")
        
        # 创建默认管理员用户
        if not User.query.filter_by(is_admin=True).first():
            import hashlib
            
            # 创建多个默认管理员账户以提高可用性
            default_admins = [
                ('admin@pubmed.com', 'admin123'),
                ('backup-admin@pubmed.com', 'admin123'),
            ]
            
            for email, password in default_admins:
                # 检查是否已存在
                if not User.query.filter_by(email=email).first():
                    admin_user = User(
                        email=email,
                        is_admin=True,
                        is_active=True,
                        push_method='email',
                        push_time='09:00',
                        push_frequency='daily',
                        max_articles=10
                    )
                    admin_user.password_hash = hashlib.sha256(password.encode()).hexdigest()
                    db.session.add(admin_user)
            
            db.session.commit()
            print("默认管理员用户已创建")
        
        # 初始化默认AI提示词模板
        if not AIPromptTemplate.query.first():
            default_prompts = [
                {
                    'template_type': 'query_builder',
                    'prompt_content': """你是一个专业的医学文献检索专家。请根据用户提供的关键词，生成规范的PubMed检索式。

要求：
1. 使用适当的MeSH术语和自由词
2. 合理使用布尔运算符(AND, OR)
3. 使用[Title/Abstract]字段限定
4. 考虑同义词和相关术语
5. 只返回检索式，不要其他解释

用户关键词: {keywords}
检索式:""",
                    'is_default': True
                },
                {
                    'template_type': 'translator',
                    'prompt_content': """请将以下英文医学摘要准确翻译成中文，要求：
1. 保持专业术语的准确性
2. 语句通顺自然
3. 保持原文的逻辑结构
4. 只返回翻译结果，不要其他内容

英文摘要: {abstract}
中文译文:""",
                    'is_default': True
                }
            ]
            
            for prompt_data in default_prompts:
                template = AIPromptTemplate(
                    template_type=prompt_data['template_type'],
                    prompt_content=prompt_data['prompt_content'],
                    is_default=prompt_data['is_default']
                )
                db.session.add(template)
            
            db.session.commit()
            print("默认AI提示词模板已初始化")
        
        # 检查并处理期刊数据文件
        check_and_process_journal_data()
        
        # 只在主进程中显示启动信息（避免Flask reloader重复显示）
        if os.environ.get('WERKZEUG_RUN_MAIN') != 'true':
            print("=== PubMed Literature Push Web Application ===")
            print("Starting server...")
            print("URL: http://127.0.0.1:5003")
            print("Default admin accounts: admin@pubmed.com / admin123, backup-admin@pubmed.com / admin123")
            print("注意：如使用自定义设置，请使用您设置的账号密码")
            print("Press Ctrl+C to stop server")
            print("=" * 50)
        
        # 启动定时推送任务
        init_scheduler()
        print("定时推送任务已启动")
        
        # 初始化限流器
        init_rate_limiter()
        print("PubMed API限流器已初始化")
        
        try:
            debug_mode = os.environ.get('FLASK_DEBUG', 'True').lower() == 'true'
            # 禁用开发服务器警告（仅用于个人项目）
            import warnings
            warnings.filterwarnings("ignore", message=".*development server.*")
            app.run(host='127.0.0.1', port=5003, debug=debug_mode)
        except KeyboardInterrupt:
            print("\\n服务器已停止")
        finally:
            if scheduler.running:
                scheduler.shutdown()
                print("定时任务已停止")
