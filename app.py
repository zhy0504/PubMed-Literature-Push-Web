
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
import signal
# RQ相关导入
from rq_config import RQConfig, get_queue_info, get_failed_jobs, redis_conn
# 搜索缓存服务导入
from search_cache_service import search_cache_service
# 延迟导入 tasks 避免循环导入
# from tasks import batch_schedule_all_subscriptions, immediate_push_subscription
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

# ============================================================================
# 高级查询构建器
# ============================================================================

class FilterQueryBuilder:
    """
    高级筛选查询构建器
    支持深层嵌套的 AND/OR 逻辑组合
    """

    # 预设模板
    TEMPLATES = {
        'high_quality': {
            'name': '高质量期刊',
            'description': '中科院1区或JCR Q1，且为Top期刊',
            'icon': '⭐',
            'filter': {
                'type': 'group',
                'operator': 'AND',
                'children': [
                    {
                        'type': 'group',
                        'operator': 'OR',
                        'children': [
                            {'type': 'condition', 'field': 'cas_partition', 'operator': 'in', 'values': ['1']},
                            {'type': 'condition', 'field': 'jcr_quartile', 'operator': 'in', 'values': ['Q1']}
                        ]
                    },
                    {'type': 'condition', 'field': 'cas_top', 'operator': 'eq', 'value': True}
                ]
            }
        },
        'medium_quality': {
            'name': '中等质量期刊',
            'description': '中科院1-2区或JCR Q1-Q2',
            'icon': '📚',
            'filter': {
                'type': 'group',
                'operator': 'OR',
                'children': [
                    {'type': 'condition', 'field': 'cas_partition', 'operator': 'in', 'values': ['1', '2']},
                    {'type': 'condition', 'field': 'jcr_quartile', 'operator': 'in', 'values': ['Q1', 'Q2']}
                ]
            }
        },
        'high_impact': {
            'name': '高影响因子',
            'description': '影响因子≥5且为1-2区',
            'icon': '📈',
            'filter': {
                'type': 'group',
                'operator': 'AND',
                'children': [
                    {'type': 'condition', 'field': 'impact_factor', 'operator': 'gte', 'value': 5.0},
                    {
                        'type': 'group',
                        'operator': 'OR',
                        'children': [
                            {'type': 'condition', 'field': 'cas_partition', 'operator': 'in', 'values': ['1', '2']},
                            {'type': 'condition', 'field': 'jcr_quartile', 'operator': 'in', 'values': ['Q1', 'Q2']}
                        ]
                    }
                ]
            }
        },
        'top_journals_only': {
            'name': '仅Top期刊',
            'description': '中科院Top期刊，不限分区',
            'icon': '🏆',
            'filter': {
                'type': 'condition',
                'field': 'cas_top',
                'operator': 'eq',
                'value': True
            }
        },
        'basic_quality': {
            'name': '基础质量筛选',
            'description': '排除无ISSN，1-3区或Q1-Q3',
            'icon': '📋',
            'filter': {
                'type': 'group',
                'operator': 'AND',
                'children': [
                    {'type': 'condition', 'field': 'exclude_no_issn', 'operator': 'eq', 'value': True},
                    {
                        'type': 'group',
                        'operator': 'OR',
                        'children': [
                            {'type': 'condition', 'field': 'cas_partition', 'operator': 'in', 'values': ['1', '2', '3']},
                            {'type': 'condition', 'field': 'jcr_quartile', 'operator': 'in', 'values': ['Q1', 'Q2', 'Q3']}
                        ]
                    }
                ]
            }
        }
    }

    # 字段定义
    FIELD_DEFINITIONS = {
        'cas_partition': {'label': '中科院分区', 'type': 'multi_select', 'options': ['1', '2', '3', '4']},
        'cas_top': {'label': '中科院Top期刊', 'type': 'boolean'},
        'jcr_quartile': {'label': 'JCR分区', 'type': 'multi_select', 'options': ['Q1', 'Q2', 'Q3', 'Q4']},
        'impact_factor': {'label': '影响因子', 'type': 'number'},
        'exclude_no_issn': {'label': '排除无ISSN', 'type': 'boolean'}
    }

    def __init__(self, filter_config):
        """
        初始化查询构建器
        Args:
            filter_config: JSON配置或字典
        """
        if isinstance(filter_config, str):
            self.config = json.loads(filter_config)
        else:
            self.config = filter_config

    def evaluate(self, article, quality_info):
        """
        评估文章是否满足筛选条件
        Args:
            article: 文章字典
            quality_info: 期刊质量信息字典
        Returns:
            bool: 是否通过筛选
        """
        if not self.config:
            return True

        return self._evaluate_node(self.config, article, quality_info)

    def _evaluate_node(self, node, article, quality_info):
        """递归评估节点"""
        if node['type'] == 'condition':
            return self._evaluate_condition(node, article, quality_info)
        elif node['type'] == 'group':
            return self._evaluate_group(node, article, quality_info)
        else:
            raise ValueError(f"Unknown node type: {node['type']}")

    def _evaluate_group(self, group, article, quality_info):
        """评估组节点"""
        operator = group['operator']
        children = group['children']

        results = [self._evaluate_node(child, article, quality_info) for child in children]

        if operator == 'AND':
            return all(results)
        elif operator == 'OR':
            return any(results)
        else:
            raise ValueError(f"Unknown operator: {operator}")

    def _evaluate_condition(self, condition, article, quality_info):
        """评估条件节点"""
        field = condition['field']
        operator = condition['operator']

        # 获取实际值
        if field == 'cas_partition':
            actual_value = quality_info.get('zky_category', '')
        elif field == 'cas_top':
            actual_value = quality_info.get('zky_top', '') == '是'
        elif field == 'jcr_quartile':
            actual_value = quality_info.get('jcr_quartile', '')
        elif field == 'impact_factor':
            try:
                actual_value = float(quality_info.get('jcr_if', 0))
            except (ValueError, TypeError):
                actual_value = 0.0
        elif field == 'exclude_no_issn':
            has_issn = bool(article.get('issn') or article.get('eissn'))
            # exclude_no_issn 为 True 时，要求有ISSN
            if condition.get('value', True):
                return has_issn
            else:
                return True  # 不排除时总是通过
        else:
            return True  # 未知字段默认通过

        # 执行比较
        if operator == 'eq':
            return actual_value == condition['value']
        elif operator == 'ne':
            return actual_value != condition['value']
        elif operator == 'in':
            return actual_value in condition.get('values', [])
        elif operator == 'not_in':
            return actual_value not in condition.get('values', [])
        elif operator == 'gte':
            return actual_value >= condition['value']
        elif operator == 'lte':
            return actual_value <= condition['value']
        elif operator == 'gt':
            return actual_value > condition['value']
        elif operator == 'lt':
            return actual_value < condition['value']
        elif operator == 'between':
            min_val, max_val = condition['value']
            return min_val <= actual_value <= max_val
        else:
            return True  # 未知操作符默认通过

    def to_human_readable(self):
        """转换为人类可读的字符串"""
        if not self.config:
            return "无筛选条件"
        return self._node_to_string(self.config)

    def _node_to_string(self, node, depth=0):
        """递归转换节点为字符串"""
        indent = "  " * depth

        if node['type'] == 'condition':
            return self._condition_to_string(node)
        elif node['type'] == 'group':
            operator = " 且 " if node['operator'] == 'AND' else " 或 "
            children_str = operator.join([
                f"({self._node_to_string(child, depth + 1)})"
                for child in node['children']
            ])
            return children_str
        return ""

    def _condition_to_string(self, condition):
        """条件节点转字符串"""
        field_def = self.FIELD_DEFINITIONS.get(condition['field'], {})
        field_label = field_def.get('label', condition['field'])
        operator = condition['operator']

        if operator == 'in':
            values = condition.get('values', [])
            if condition['field'] == 'cas_partition':
                return f"{field_label}: {' 或 '.join([v+'区' for v in values])}"
            elif condition['field'] == 'jcr_quartile':
                return f"{field_label}: {' 或 '.join(values)}"
        elif operator == 'eq' and condition['field'] == 'cas_top':
            return "中科院Top期刊"
        elif operator in ['gte', 'lte', 'gt', 'lt']:
            op_str = {'gte': '≥', 'lte': '≤', 'gt': '>', 'lt': '<'}[operator]
            return f"{field_label} {op_str} {condition['value']}"
        elif operator == 'between':
            min_val, max_val = condition['value']
            return f"{field_label}: {min_val} ~ {max_val}"

        return f"{field_label}"

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
            
            print(f"期刊数据缓存加载完成: JCR({len(self.jcr_data)}条) + 中科院({len(self.zky_data)}条), 耗时 {load_time:.2f}秒")
            
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
# 时区配置 - 支持环境变量配置
DEFAULT_TIMEZONE = 'Asia/Shanghai'  # 默认时区
# 优先使用标准的 TZ 环境变量，如果没有则使用默认值
SYSTEM_TIMEZONE = os.environ.get('TZ', DEFAULT_TIMEZONE)

try:
    import pytz
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

def get_current_utc_time():
    """获取当前UTC时间，转换为系统时区"""
    return datetime.now(APP_TIMEZONE)

# 为了向后兼容，保留原有函数名但使用新的时区配置
def beijing_now():
    """获取当前时间（使用配置的时区，兼容原函数名）"""
    return datetime.now(APP_TIMEZONE)

def beijing_utcnow():
    """获取当前时间（使用配置的时区，兼容原函数名）"""
    return datetime.now(APP_TIMEZONE)

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
app = Flask(__name__)
app.config.from_object(Config)

# 初始化RQ配置
RQConfig.init_app(app)

# 配置日志
import logging
from logging.handlers import RotatingFileHandler

# 从环境变量获取日志级别和文件路径
log_level_name = os.environ.get('LOG_LEVEL', 'INFO').upper()
log_file = os.environ.get('LOG_FILE', '/app/logs/app.log')

# 设置日志级别
log_level = getattr(logging, log_level_name, logging.INFO)

# 移除Flask默认的处理器（它们可能有不同的日志级别）
if app.logger.hasHandlers():
    app.logger.handlers.clear()

# 设置app.logger的日志级别
app.logger.setLevel(log_level)

# 同时设置根日志记录器的级别（确保所有处理器都生效）
logging.getLogger().setLevel(log_level)

# 配置控制台处理器（确保DEBUG日志也输出到控制台）
console_handler = logging.StreamHandler()
console_handler.setLevel(log_level)
console_formatter = logging.Formatter(
    '[%(asctime)s] %(levelname)s in %(module)s: %(message)s'
)
console_handler.setFormatter(console_formatter)
app.logger.addHandler(console_handler)

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
        app.logger.info(f"应用启动，日志级别: {log_level_name}, 日志文件: {log_file}")

        # 输出调试信息验证配置
        if log_level == logging.DEBUG:
            app.logger.debug("DEBUG日志级别已启用 - 这是一条测试DEBUG消息")
            app.logger.debug(f"日志处理器数量: {len(app.logger.handlers)}")
            app.logger.debug(f"根日志记录器级别: {logging.getLogger().level}")
    except PermissionError:
        # 如果无法写入日志文件，只使用控制台输出
        print(f"[警告] 无权限写入日志文件: {log_file}，仅使用控制台输出")
    except Exception as e:
        print(f"[警告] 日志文件配置失败: {e}，仅使用控制台输出")

# 初始化扩展
db = SQLAlchemy(app)
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
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
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
    allowed_frequencies = db.Column(db.Text, default='daily,weekly,monthly')  # 允许的推送频率，逗号分隔
    
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
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
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

    # 高级查询构建器配置（新增）
    filter_config = db.Column(db.Text)  # JSON格式存储查询构建器的完整配置
    use_advanced_filter = db.Column(db.Boolean, default=False)  # 是否使用高级筛选器
    
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
    # AI增强字段
    abstract_cn = db.Column(db.Text)  # 中文翻译
    brief_intro = db.Column(db.Text)  # AI生成的简介（一句话总结）
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

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
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
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
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    expires_at = db.Column(db.DateTime, nullable=False)
    used = db.Column(db.Boolean, default=False)
    
    user = db.relationship('User', backref='password_reset_tokens')
    
    def is_expired(self):
        return beijing_now() > self.expires_at
    
    def mark_as_used(self):
        self.used = True
        db.session.commit()

# 邀请码模型
class InviteCode(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(50), unique=True, nullable=False)
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    expires_at = db.Column(db.DateTime, nullable=True)
    max_uses = db.Column(db.Integer, default=1)  # 最大使用次数
    used_count = db.Column(db.Integer, default=0)  # 已使用次数
    is_active = db.Column(db.Boolean, default=True)

    creator = db.relationship('User', backref='created_invite_codes', foreign_keys=[created_by])

    def is_expired(self):
        """检查是否已过期"""
        if self.expires_at:
            return beijing_now() > self.expires_at
        return False

    def can_be_used(self):
        """检查是否可用"""
        return (self.is_active and
                not self.is_expired() and
                self.used_count < self.max_uses)

    def mark_as_used(self):
        """标记为已使用一次"""
        self.used_count += 1
        if self.used_count >= self.max_uses:
            self.is_active = False
        db.session.commit()

# 邀请码使用记录模型
class InviteCodeUsage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    invite_code_id = db.Column(db.Integer, db.ForeignKey('invite_code.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    used_at = db.Column(db.DateTime, default=datetime.utcnow)

    invite_code = db.relationship('InviteCode', backref='usage_records')
    user = db.relationship('User', backref='invite_code_usage')

# 系统设置模型
class SystemSetting(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(100), unique=True, nullable=False)
    value = db.Column(db.Text, nullable=False)
    description = db.Column(db.String(200), nullable=True)
    category = db.Column(db.String(50), nullable=False, default='general')
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
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
    username = db.Column(db.String(100), nullable=False)  # SMTP登录用户名
    password = db.Column(db.String(200), nullable=False)
    from_email = db.Column(db.String(120), nullable=True)  # 发件人邮箱地址(可选,为空时使用username)
    use_tls = db.Column(db.Boolean, default=True)
    is_active = db.Column(db.Boolean, default=True)
    daily_limit = db.Column(db.Integer, default=100)  # 每日发送限制
    current_count = db.Column(db.Integer, default=0)  # 今日已发送数量
    last_used = db.Column(db.DateTime)  # 最后使用时间
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
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
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
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
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

# AI提示词模板表
class AIPromptTemplate(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    template_type = db.Column(db.String(20), nullable=False)  # query_builder, translator
    prompt_content = db.Column(db.Text, nullable=False)
    is_default = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

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
            # 使用from_email字段(如果有),否则使用username
            sender_email = config.from_email or config.username
            msg = Message(
                subject=subject,
                sender=('PubMed Literature Push', sender_email),
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
    
    def process_single_subscription(self, subscription_id):
        """处理单个订阅的推送逻辑"""
        try:
            # 获取订阅信息
            subscription = Subscription.query.get(subscription_id)
            if not subscription or not subscription.is_active:
                return {
                    'subscription_id': subscription_id,
                    'success': False,
                    'error': 'Subscription not found or inactive'
                }
            
            # 获取用户信息
            user = subscription.user
            if not user or not user.is_active:
                return {
                    'subscription_id': subscription_id,
                    'success': False,
                    'error': 'User not found or inactive'
                }
            
            log_activity('INFO', 'scheduler', f'开始处理订阅 {subscription_id} (用户: {user.email}, 关键词: {subscription.keywords})')
            
            # 使用订阅的个人参数设置
            filter_params = subscription.get_filter_params()
            
            # 搜索新文章
            api = PubMedAPI()
            
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
            if fetch_result.get('filtered_count', 0) == 0:
                log_activity('INFO', 'scheduler', f'订阅 {subscription_id} 无新文章')
                # 更新订阅的最后搜索时间
                subscription.last_search = beijing_now()
                db.session.commit()
                return {
                    'subscription_id': subscription_id,
                    'user_email': user.email,
                    'keywords': subscription.keywords,
                    'success': True,
                    'articles_found': 0,
                    'message': 'No new articles found'
                }
            
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
                    user_id=user.id, 
                    article_id=article.id,
                    subscription_id=subscription.id
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
            
            # 更新订阅的最后搜索时间
            subscription.last_search = beijing_now()
            db.session.commit()
            
            if not new_articles:
                log_activity('INFO', 'scheduler', f'订阅 {subscription_id} 无新文章（筛选后）')
                return {
                    'subscription_id': subscription_id,
                    'user_email': user.email,
                    'keywords': subscription.keywords,
                    'success': True,
                    'articles_found': 0,
                    'message': 'No new articles after filtering'
                }
            
            # 使用AI翻译摘要（如果启用）
            if SystemSetting.get_setting('ai_translation_enabled', 'false') == 'true':
                try:
                    log_activity('INFO', 'push', f'开始为订阅 {subscription_id} 的 {len(new_articles)} 篇文章进行AI翻译')
                    ai_service.batch_translate_abstracts(new_articles)
                    log_activity('INFO', 'push', f'订阅 {subscription_id} 的文章AI翻译完成')
                except Exception as e:
                    log_activity('WARNING', 'push', f'订阅 {subscription_id} 的AI翻译失败: {str(e)}')
            
            # 使用AI生成文献简介（如果启用）
            if SystemSetting.get_setting('ai_brief_intro_enabled', 'false') == 'true':
                try:
                    log_activity('INFO', 'push', f'开始为订阅 {subscription_id} 的 {len(new_articles)} 篇文章生成AI简介')
                    ai_service.batch_generate_brief_intros(new_articles)
                    log_activity('INFO', 'push', f'订阅 {subscription_id} 的文章AI简介生成完成')
                except Exception as e:
                    log_activity('WARNING', 'push', f'订阅 {subscription_id} 的AI简介生成失败: {str(e)}')
            
            # 发送邮件通知
            articles_by_subscription = {subscription.keywords: new_articles}
            self._send_email_notification(user, new_articles, articles_by_subscription)
            
            # 更新用户最后推送时间（按订阅级别，用户可能有多个订阅在不同时间推送）
            user.last_push = beijing_now()
            db.session.commit()
            
            log_activity('INFO', 'scheduler', f'订阅 {subscription_id} 推送完成：发送了 {len(new_articles)} 篇新文章给用户 {user.email}')
            
            return {
                'subscription_id': subscription_id,
                'user_email': user.email,
                'keywords': subscription.keywords,
                'success': True,
                'articles_found': len(new_articles),
                'message': f'Sent {len(new_articles)} new articles'
            }
            
        except Exception as e:
            error_msg = f'处理订阅 {subscription_id} 失败: {str(e)}'
            log_activity('ERROR', 'scheduler', error_msg)
            return {
                'subscription_id': subscription_id,
                'success': False,
                'error': error_msg
            }
    
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
                
                # 使用AI生成文献简介（如果启用）
                if SystemSetting.get_setting('ai_brief_intro_enabled', 'false') == 'true':
                    try:
                        log_activity('INFO', 'push', f'开始为用户 {user.email} 的关键词 "{keywords}" 的 {len(articles)} 篇文章生成AI简介')
                        ai_service.batch_generate_brief_intros(articles)
                        log_activity('INFO', 'push', f'用户 {user.email} 关键词 "{keywords}" 的文章AI简介生成完成')
                    except Exception as e:
                        log_activity('WARNING', 'push', f'用户 {user.email} 关键词 "{keywords}" 的AI简介生成失败: {str(e)}')
                
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
            # 获取当前日期
            from datetime import datetime
            current_date = datetime.now().strftime('%Y年%m月%d日')

            # 生成邮件主题，包含关键词信息
            if articles_by_subscription and len(articles_by_subscription) == 1:
                # 获取关键词（现在总是只有一个）
                keywords = list(articles_by_subscription.keys())[0]
                subject = f"{current_date} {keywords}文献推送-您有{len(articles)}篇新文献"
            else:
                # 备用格式
                subject = f"{current_date} PubMed文献推送-您有{len(articles)}篇新文献"
            
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
                
                /* 简介汇总样式 */
                .brief-summary {{
                    background: linear-gradient(135deg, #fff3cd 0%, #ffeaa7 100%);
                    border: 1px solid #ffeaa7;
                    border-radius: 12px;
                    padding: 20px;
                    margin-bottom: 30px;
                    box-shadow: 0 3px 6px rgba(255, 193, 7, 0.1);
                }}
                .summary-title {{
                    font-size: 18px;
                    font-weight: 600;
                    color: #856404;
                    margin-bottom: 15px;
                    text-align: center;
                }}
                .summary-content {{
                    font-size: 14px;
                    line-height: 1.8;
                    color: #6c5f00;
                    text-align: left;
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
                    .brief-summary {{ padding: 15px; margin-bottom: 20px; }}
                    .summary-title {{ font-size: 16px; }}
                    .summary-content {{ font-size: 13px; line-height: 1.6; }}
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
        
        # 添加文献简介汇总部分
        brief_intros = []
        for i, article in enumerate(articles, 1):
            title = getattr(article, 'title', '未知标题')
            brief_intro = getattr(article, 'brief_intro', '')
            if brief_intro:
                # 使用醒目的编号样式，不使用href链接（避免邮件客户端转换）
                brief_intros.append(f'''
                    <div style="padding: 12px 0; border-bottom: 1px solid #ffeaa7;">
                        <div style="margin-bottom: 8px;">
                            <span style="display: inline-block; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; font-weight: bold; padding: 4px 12px; border-radius: 6px; margin-right: 10px; font-size: 14px; min-width: 30px; text-align: center;">第{i}篇</span>
                            <span style="color: #2c3e50; font-size: 14px; font-weight: 600;">{title}</span>
                        </div>
                        <div style="color: #495057; font-size: 15px; line-height: 1.6; margin-left: 0px; padding-left: 0px;">
                            {brief_intro}
                        </div>
                    </div>
                ''')

        if brief_intros:
            html_content += f"""
                    <div class="brief-summary">
                        <div class="summary-title">📋 文献速览（按序号查看下方详情）</div>
                        <div class="summary-content">
                            {''.join(brief_intros)}
                        </div>
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
                            <div class="abstract-title">📝 中文摘要</div>
                            <div class="abstract-content chinese-abstract">{article.abstract_translation}</div>
                        </div>
                    '''
                
            
            # 获取发表日期
            pub_date_html = ""
            if hasattr(article, 'publish_date') and article.publish_date:
                pub_date_html = f'<div style="color: #6c757d; font-size: 13px; margin-top: 5px;">📅 发表日期: {article.publish_date.strftime("%Y-%m-%d")}</div>'
            elif hasattr(article, 'pub_date') and article.pub_date:
                pub_date_html = f'<div style="color: #6c757d; font-size: 13px; margin-top: 5px;">📅 发表日期: {article.pub_date}</div>'
            
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
                    <div class="article" id="article-{i}">
                        <div class="article-header">
                            <div class="article-number">第{i}篇</div>
                            <h3 class="title">
                                <a href="{getattr(article, 'pubmed_url', '#')}" target="_blank">
                                    {getattr(article, 'title', '未知标题')}
                                </a>
                            </h3>
                        </div>

                        <div class="journal-info">
                            <div class="journal-name">
                                📖 {getattr(article, 'journal', '未知期刊')}
                            </div>
                            {pub_date_html}
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
        
        # 添加文献简介汇总部分
        brief_intros = []
        for i, article in enumerate(articles, 1):
            title = getattr(article, 'title', '未知标题')
            brief_intro = getattr(article, 'brief_intro', '')
            if brief_intro:
                brief_intros.append(f"{i}、{title}：{brief_intro}")
        
        if brief_intros:
            content += "📋 今日推送文献简介\\n"
            content += "=" * 40 + "\\n"
            for brief in brief_intros:
                content += f"{brief}\\n\\n"
            content += "=" * 40 + "\\n\\n"
        
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
                content += f"   英文摘要: {article.abstract}\\n"
                
                # 添加中文摘要（如果有）
                if hasattr(article, 'abstract_translation') and article.abstract_translation:
                    content += f"   中文摘要: {article.abstract_translation}\\n"
                
            
            content += "\\n"
        
        content += "此邮件由 PubMed Literature Push 自动发送，请勿回复。\\n"
        
        return content

# 全局推送服务实例
push_service = SimpleLiteraturePushService()

# 初始化调度器
# 初始化调度器（使用配置的时区）
scheduler = BackgroundScheduler(timezone=APP_TIMEZONE)

def shutdown_scheduler_safely():
    """安全关闭调度器，防止线程池关闭异常"""
    try:
        if scheduler.running:
            print("正在关闭调度器...")
            # 先移除所有任务，防止在关闭时继续提交
            scheduler.remove_all_jobs()
            # 停止调度器，不等待正在执行的任务
            scheduler.shutdown(wait=False)
            print("调度器已关闭")
    except Exception as e:
        print(f"关闭调度器时出现异常: {e}")

# 注册应用退出时的清理函数
atexit.register(shutdown_scheduler_safely)

# 信号处理函数
def signal_handler(signum, frame):
    """处理系统信号，确保优雅关闭"""
    print(f"\\n收到信号 {signum}，正在优雅关闭...")
    shutdown_scheduler_safely()
    exit(0)

# 注册信号处理器
signal.signal(signal.SIGTERM, signal_handler)
signal.signal(signal.SIGINT, signal_handler)

def init_scheduler():
    """初始化RQ调度器（替代APScheduler）"""
    try:
        print("初始化RQ推送调度器...")

        # 检查Redis连接
        redis_conn.ping()
        print("[OK] Redis连接正常")

        # RQ原生调度已通过Worker --with-scheduler启用
        # 不再需要单独的调度器对象
        print("[OK] RQ原生调度器通过Worker --with-scheduler运行")

        # 注意: 批量调度不在这里执行,避免循环导入
        # 需要手动执行: python /app/init_rq_schedules.py
        print("💡 提示: 首次部署请执行 python /app/init_rq_schedules.py 进行批量调度")

        # 可选：保留APScheduler作为备用调度器（仅用于RQ监控）
        if not scheduler.running:
            # 添加RQ调度器监控任务
            scheduler.add_job(
                func=monitor_rq_scheduler,
                trigger=CronTrigger(minute='*/10'),  # 每10分钟检查一次
                id='rq_monitor',
                name='RQ调度器监控',
                replace_existing=True,
                max_instances=1
            )
            scheduler.start()
            print("[OK] APScheduler监控任务已启动")
            
    except Exception as e:
        print(f"[ERROR] RQ调度器初始化失败: {e}")
        # 降级到原APScheduler
        fallback_to_apscheduler()

def monitor_rq_scheduler():
    """监控RQ调度器状态并自动恢复丢失的调度任务"""
    try:
        # 检查调度器执行器状态，避免在关闭时提交任务
        if not scheduler.running:
            return

        # 检查执行器线程池是否已关闭
        if hasattr(scheduler, '_executors'):
            for executor in scheduler._executors.values():
                if hasattr(executor, '_pool') and hasattr(executor._pool, '_shutdown'):
                    if executor._pool._shutdown:
                        return  # 执行器已关闭，停止执行

        # 检查Redis连接
        redis_conn.ping()

        # 检查RQ队列状态
        queue_info = get_queue_info()
        total_scheduled = queue_info.get('total_scheduled', 0)

        # 记录队列状态
        log_activity('INFO', 'rq_monitor',
            f'RQ队列状态 - 高优先级:{queue_info["high"]["length"]}, '
            f'默认:{queue_info["default"]["length"]}, '
            f'低优先级:{queue_info["low"]["length"]}, '
            f'定时任务:{total_scheduled}')

        # 核心改进：检查调度任务丢失或不一致情况
        active_subscription_count = Subscription.query.filter_by(is_active=True).join(User).filter_by(is_active=True).count()

        # 检测三种异常情况：
        # 1. 有订阅但无调度任务（全部丢失）
        # 2. 订阅数 > 调度任务数（部分新增订阅未调度）
        # 3. 调度任务数 > 订阅数（有冗余任务，需要清理）
        needs_recovery = False
        recovery_reason = ""

        if active_subscription_count > 0 and total_scheduled == 0:
            needs_recovery = True
            recovery_reason = f"{active_subscription_count}个活跃订阅但无调度任务（全部丢失）"
        elif active_subscription_count > total_scheduled:
            needs_recovery = True
            recovery_reason = f"订阅数({active_subscription_count}) > 调度任务数({total_scheduled})，有{active_subscription_count - total_scheduled}个订阅未调度"
        elif total_scheduled > active_subscription_count and active_subscription_count > 0:
            # 仅记录警告，暂不自动清理（避免误删除即将执行的任务）
            log_activity('WARNING', 'rq_monitor',
                f'调度任务数({total_scheduled}) > 订阅数({active_subscription_count})，可能存在冗余任务')
            print(f"[RQ监控] 警告: 调度任务数({total_scheduled}) > 订阅数({active_subscription_count})")

        if needs_recovery:
            log_activity('WARNING', 'rq_monitor', f'检测到调度任务异常: {recovery_reason}，开始自动恢复')
            print(f"[RQ监控] 警告: {recovery_reason}，触发自动恢复")

            # 清理标记文件并触发批量调度
            rq_schedule_flag_file = '/app/data/rq_schedule_init_done'
            if os.path.exists(rq_schedule_flag_file):
                os.remove(rq_schedule_flag_file)
                print(f"[RQ监控] 已清理过期的调度标记文件")

            # 触发批量调度任务（标记文件将在任务成功后由Worker创建）
            from tasks import batch_schedule_all_subscriptions
            from rq_config import enqueue_job
            job = enqueue_job(batch_schedule_all_subscriptions, priority='high')

            log_activity('INFO', 'rq_monitor', f'自动恢复批量调度任务已排队: {job.id}')
            print(f"[RQ监控] 自动恢复批量调度任务已排队: {job.id}")

        # 检查失败任务数量
        failed_jobs = get_failed_jobs()
        if len(failed_jobs) > 0:
            log_activity('WARNING', 'rq_monitor', f'发现 {len(failed_jobs)} 个失败任务')

    except (RuntimeError, AttributeError):
        # 调度器正在关闭，静默返回
        return
    except Exception as e:
        log_activity('ERROR', 'rq_monitor', f'RQ监控异常: {e}')
        print(f"[RQ监控] 异常: {e}")
        import traceback
        traceback.print_exc()

def fallback_to_apscheduler():
    """降级到原APScheduler调度"""
    print("[WARN] 降级到APScheduler调度...")
    try:
        if scheduler.running:
            print("APScheduler已运行，跳过重复初始化")
            return
        
        print("初始化APScheduler定时推送调度器...")
        
        # 获取推送检查频率设置
        check_frequency = float(SystemSetting.get_setting('push_check_frequency', '1'))
        
        # 添加定时任务
        if check_frequency == 0.25:
            trigger = CronTrigger(minute='*/15')  # 每15分钟执行
            job_name = '每15分钟推送检查'
        elif check_frequency == 0.5:
            trigger = CronTrigger(minute='*/30')  # 每30分钟执行  
            job_name = '每30分钟推送检查'
        elif check_frequency == 1:
            trigger = CronTrigger(minute=0)  # 每小时的0分执行
            job_name = '每小时推送检查'
        else:
            trigger = CronTrigger(minute=0, hour=f'*/{int(check_frequency)}')
            job_name = f'每{int(check_frequency)}小时推送检查'
        
        scheduler.add_job(
            func=check_and_push_articles,
            trigger=trigger,
            id='push_check',
            name=job_name,
            replace_existing=True,
            max_instances=1
        )
        
        # 添加调度器心跳监控任务（多worker环境下确保调度器持续运行）
        scheduler.add_job(
            id='scheduler_heartbeat',
            func=update_scheduler_heartbeat,
            trigger=CronTrigger(minute='*'),  # 每分钟更新心跳
            name='调度器心跳更新',
            max_instances=1,
            coalesce=True,
            replace_existing=True
        )
        
        # 启动调度器
        if not scheduler.running:
            scheduler.start()
            print(f"[OK] APScheduler启动成功: {job_name}")
            print("[OK] 调度器心跳监控已启动")
        
    except Exception as e:
        print(f"[ERROR] APScheduler降级失败: {e}")
        import traceback
        traceback.print_exc()
        
# 删除：单worker环境下不再需要心跳机制

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
            
            # 获取所有活跃订阅（按订阅推送的新逻辑）
            subscriptions = Subscription.query.filter_by(is_active=True).join(User).filter_by(is_active=True).all()
            
            # 统计订阅分布信息
            frequency_counts = {}
            user_subscription_counts = {}
            
            for sub in subscriptions:
                # 统计频率分布
                freq = sub.push_frequency or 'daily'
                frequency_counts[freq] = frequency_counts.get(freq, 0) + 1
                
                # 统计用户订阅数
                user_email = sub.user.email
                user_subscription_counts[user_email] = user_subscription_counts.get(user_email, 0) + 1
            
            app.logger.info(f"[调度器] 找到 {len(subscriptions)} 个活跃订阅 (涉及 {len(user_subscription_counts)} 个用户)")
            app.logger.info(f"[调度器] 订阅频率分布: {frequency_counts}")
            print(f"[调度器] 找到 {len(subscriptions)} 个活跃订阅，涉及 {len(user_subscription_counts)} 个用户")
            
            push_count = 0
            successful_pushes = 0
            failed_pushes = 0
            
            for subscription in subscriptions:
                # 添加调试信息
                app.logger.info(f"[调度器调试] 检查订阅 {subscription.id} (用户: {subscription.user.email}, 关键词: {subscription.keywords}): 推送时间={subscription.push_time}, 频率={subscription.push_frequency}, 推送日={subscription.push_day}")
                
                if should_push_subscription_now(subscription, hour, minute, weekday, day_of_month):
                    try:
                        app.logger.info(f"[调度器] 开始为订阅 {subscription.id} 推送文章 (用户: {subscription.user.email}, 推送时间: {subscription.push_time}, 频率: {subscription.push_frequency})")
                        print(f"[调度器] 开始为订阅 {subscription.id} 推送文章 (用户: {subscription.user.email})")
                        
                        # 按订阅推送单个订阅
                        result = push_service.process_single_subscription(subscription.id)
                        push_count += 1
                        
                        if result and result.get('success'):
                            articles_count = result.get('articles_found', 0)
                            successful_pushes += 1
                            if articles_count > 0:
                                log_activity('INFO', 'push', f'订阅推送成功: {subscription.keywords} -> {subscription.user.email}, 文章数: {articles_count}')
                                app.logger.info(f"[调度器] 订阅 {subscription.id} 推送成功: {articles_count} 篇文章")
                                print(f"[调度器] 订阅 {subscription.id} 推送成功: {articles_count} 篇文章")
                            else:
                                log_activity('INFO', 'push', f'订阅无新文章: {subscription.keywords} -> {subscription.user.email}')
                                app.logger.info(f"[调度器] 订阅 {subscription.id} 无新文章推送")
                                
                        else:
                            failed_pushes += 1
                            error_msg = result.get('error', '未知错误') if result else '推送服务返回空结果'
                            log_activity('ERROR', 'push', f'订阅推送失败: {subscription.keywords} -> {subscription.user.email}, 错误: {error_msg}')
                            app.logger.error(f"[调度器] 订阅 {subscription.id} 推送失败: {error_msg}")
                            print(f"[调度器] 订阅 {subscription.id} 推送失败: {error_msg}")
                            
                    except Exception as e:
                        failed_pushes += 1
                        log_activity('ERROR', 'push', f'订阅推送异常: {subscription.keywords} -> {subscription.user.email}, 错误: {str(e)}')
                        app.logger.error(f"[调度器] 订阅 {subscription.id} 推送异常: {e}")
                        print(f"[调度器] 订阅 {subscription.id} 推送异常: {e}")
                else:
                    # 详细日志：记录为什么不推送
                    if subscription.push_time:
                        app.logger.debug(f"[调度器] 订阅 {subscription.id} 时间不匹配 (设定: {subscription.push_time}, 当前: {hour:02d}:{minute:02d})")
            
            if push_count > 0:
                app.logger.info(f"[调度器] 本次检查完成，处理了 {push_count} 个订阅 (成功: {successful_pushes}, 失败: {failed_pushes})")
                print(f"[调度器] 本次检查完成，处理了 {push_count} 个订阅 (成功: {successful_pushes}, 失败: {failed_pushes})")
                log_activity('INFO', 'scheduler', f'调度器执行完成: 总订阅数={len(subscriptions)}, 触发推送={push_count}, 成功={successful_pushes}, 失败={failed_pushes}')
            else:
                app.logger.debug(f"[调度器] 本次检查完成，无订阅需要推送")
                log_activity('INFO', 'scheduler', f'调度器执行完成: 总订阅数={len(subscriptions)}, 无触发推送')
                        
        except Exception as e:
            log_activity('ERROR', 'push', f'推送检查任务失败: {str(e)}')
            app.logger.error(f"[调度器] 推送检查任务失败: {e}")
            print(f"[调度器] 推送检查任务失败: {e}")

def should_push_subscription_now(subscription, current_hour, current_minute, current_weekday, current_day):
    """判断订阅是否应该在当前时间推送"""
    app.logger.info(f"[调度器调试] should_push_subscription_now: 订阅={subscription.id}, 用户={subscription.user.email}, 当前时间={current_hour}:{current_minute}, 当前星期={current_weekday}")
    
    # 检查推送时间
    if subscription.push_time:
        try:
            push_hour, push_minute = map(int, subscription.push_time.split(':'))
            
            # 智能时间匹配：允许补推错过的时间
            current_total_minutes = current_hour * 60 + current_minute
            push_total_minutes = push_hour * 60 + push_minute
            
            # 情况1：精确匹配（±1分钟）
            time_match = (current_hour == push_hour and abs(current_minute - push_minute) <= 1)
            
            # 情况2：补推逻辑 - 当前时间已过推送时间，但在同一小时内的后续检查中补推
            if not time_match and current_hour == push_hour and current_minute > push_minute:
                time_match = True  # 同一小时内的补推
                app.logger.info(f"[补推] 订阅 {subscription.id} 补推逻辑触发：设定时间 {push_hour}:{push_minute:02d}，当前时间 {current_hour}:{current_minute:02d}")
            
            # 情况3：跨小时补推 - 推送时间已过且在1小时内
            elif not time_match and current_total_minutes > push_total_minutes and current_total_minutes - push_total_minutes <= 60:
                time_match = True  # 1小时内的跨小时补推
                app.logger.info(f"[跨小时补推] 订阅 {subscription.id} 跨小时补推：设定时间 {push_hour}:{push_minute:02d}，当前时间 {current_hour}:{current_minute:02d}")
            
            app.logger.info(f"[调度器调试] should_push_subscription_now: 订阅 {subscription.id} 设置时间 {push_hour}:{push_minute}, 时间匹配: {time_match}")
            if not time_match:
                return False
        except:
            app.logger.error(f"[调度器调试] should_push_subscription_now: 订阅 {subscription.id} 推送时间格式错误: {subscription.push_time}")
            return False
    else:
        # 默认推送时间8:00
        default_time_match = (current_hour == 8 and current_minute <= 1)
        app.logger.info(f"[调度器调试] should_push_subscription_now: 订阅 {subscription.id} 使用默认时间8:00, 匹配: {default_time_match}")
        if not default_time_match:
            return False
    
    # 检查推送频率
    if subscription.push_frequency == 'daily':
        return should_push_subscription_daily(subscription)
    elif subscription.push_frequency == 'weekly':
        return should_push_subscription_weekly(subscription, current_weekday)
    elif subscription.push_frequency == 'monthly':
        return should_push_subscription_monthly(subscription, current_day)
    
    return False

def should_push_subscription_daily(subscription):
    """检查订阅是否应该每日推送"""
    if not subscription.last_search:
        app.logger.info(f"[调度器调试] should_push_subscription_daily: 订阅 {subscription.id} 从未搜索过，返回True")
        return True
    
    # 统一时区格式进行比较（避免 offset-naive 和 offset-aware 时间混合）
    try:
        current_time = beijing_now()
        last_search_time = subscription.last_search
        
        # 如果 last_search 没有时区信息，假设它是北京时间
        if last_search_time.tzinfo is None:
            last_search_time = APP_TIMEZONE.localize(last_search_time)
        # 如果时区不同，转换为北京时间
        elif last_search_time.tzinfo != APP_TIMEZONE:
            last_search_time = last_search_time.astimezone(APP_TIMEZONE)
        
        time_since_last = current_time - last_search_time
        should_push = time_since_last.total_seconds() > 20 * 3600  # 20小时
        app.logger.info(f"[调度器调试] should_push_subscription_daily: 订阅 {subscription.id} 距离上次搜索 {time_since_last.total_seconds()/3600:.1f} 小时，应该推送: {should_push}")
        return should_push
        
    except Exception as e:
        app.logger.error(f"[调度器调试] should_push_subscription_daily: 订阅 {subscription.id} 时间比较异常: {e}")
        # 异常情况下默认允许推送
        return True

def should_push_subscription_weekly(subscription, current_weekday):
    """检查订阅是否应该每周推送"""
    app.logger.info(f"[调度器调试] should_push_subscription_weekly: 订阅={subscription.id}, 当前星期={current_weekday}, 设置星期={subscription.push_day}, 最后搜索={subscription.last_search}")
    
    if not subscription.last_search:
        app.logger.info(f"[调度器调试] should_push_subscription_weekly: 订阅 {subscription.id} 从未搜索过，返回True")
        return True
    
    # 检查今天是否是设置的推送日
    subscription_weekday = subscription.push_day or 'monday'
    if current_weekday != subscription_weekday:
        app.logger.info(f"[调度器调试] should_push_subscription_weekly: 订阅 {subscription.id} 今天不是推送日 ({current_weekday} != {subscription_weekday})，返回False")
        return False
    
    # 统一时区格式进行比较
    try:
        current_time = beijing_now()
        last_search_time = subscription.last_search
        
        # 如果 last_search 没有时区信息，假设它是北京时间
        if last_search_time.tzinfo is None:
            last_search_time = APP_TIMEZONE.localize(last_search_time)
        # 如果时区不同，转换为北京时间
        elif last_search_time.tzinfo != APP_TIMEZONE:
            last_search_time = last_search_time.astimezone(APP_TIMEZONE)
        
        time_since_last = current_time - last_search_time
        should_push = time_since_last.days >= 6
        app.logger.info(f"[调度器调试] should_push_subscription_weekly: 订阅 {subscription.id} 距离上次搜索 {time_since_last.days} 天，应该推送: {should_push}")
        return should_push
        
    except Exception as e:
        app.logger.error(f"[调度器调试] should_push_subscription_weekly: 订阅 {subscription.id} 时间比较异常: {e}")
        # 异常情况下默认允许推送
        return True

def should_push_subscription_monthly(subscription, current_day):
    """检查订阅是否应该每月推送"""
    if not subscription.last_search:
        return True
    
    # 检查今天是否是设置的推送日
    subscription_day = subscription.push_month_day or 1
    if current_day != subscription_day:
        return False
    
    # 统一时区格式进行比较（避免 offset-naive 和 offset-aware 时间混合）
    try:
        current_time = beijing_now()
        last_search_time = subscription.last_search
        
        # 如果 last_search 没有时区信息，假设它是北京时间
        if last_search_time.tzinfo is None:
            last_search_time = APP_TIMEZONE.localize(last_search_time)
        # 如果时区不同，转换为北京时间
        elif last_search_time.tzinfo != APP_TIMEZONE:
            last_search_time = last_search_time.astimezone(APP_TIMEZONE)
        
        time_since_last = current_time - last_search_time
        return time_since_last.days >= 25
        
    except Exception as e:
        app.logger.error(f"[调度器] 订阅 {subscription.id} 每月推送时间比较异常: {e}")
        # 异常情况下默认允许推送
        return True

def should_push_now(user, current_hour, current_minute, current_weekday, current_day):
    """判断用户是否应该在当前时间推送"""
    app.logger.info(f"[调度器调试] should_push_now: 用户={user.email}, 当前时间={current_hour}:{current_minute}, 当前星期={current_weekday}")
    
    # 检查推送时间
    if user.push_time:
        try:
            push_hour, push_minute = map(int, user.push_time.split(':'))
            # 允许1分钟误差
            time_match = (current_hour == push_hour and abs(current_minute - push_minute) <= 1)
            app.logger.info(f"[调度器调试] should_push_now: 用户 {user.email} 设置时间 {push_hour}:{push_minute}, 时间匹配: {time_match}")
            if not time_match:
                return False
        except:
            app.logger.error(f"[调度器调试] should_push_now: 用户 {user.email} 推送时间格式错误: {user.push_time}")
            return False
    else:
        # 默认推送时间8:00
        default_time_match = (current_hour == 8 and current_minute <= 1)
        app.logger.info(f"[调度器调试] should_push_now: 用户 {user.email} 使用默认时间8:00, 匹配: {default_time_match}")
        if not default_time_match:
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
    
    # 统一时区格式进行比较（避免 offset-naive 和 offset-aware 时间混合）
    try:
        current_time = beijing_now()
        last_push_time = user.last_push
        
        # 如果 last_push 没有时区信息，假设它是北京时间
        if last_push_time.tzinfo is None:
            last_push_time = APP_TIMEZONE.localize(last_push_time)
        # 如果时区不同，转换为北京时间
        elif last_push_time.tzinfo != APP_TIMEZONE:
            last_push_time = last_push_time.astimezone(APP_TIMEZONE)
        
        time_since_last = current_time - last_push_time
        return time_since_last.total_seconds() > 20 * 3600  # 20小时
        
    except Exception as e:
        app.logger.error(f"[调度器] 用户 {user.email} 时间比较异常: {e}")
        # 异常情况下默认允许推送
        return True

def should_push_weekly(user, current_weekday):
    """检查是否应该每周推送"""
    app.logger.info(f"[调度器调试] should_push_weekly: 用户={user.email}, 当前星期={current_weekday}, 用户设置星期={user.push_day}, 最后推送={user.last_push}")
    
    if not user.last_push:
        app.logger.info(f"[调度器调试] should_push_weekly: 用户 {user.email} 从未推送过，返回True")
        return True
    
    # 检查今天是否是用户设置的推送日
    user_weekday = user.push_day or 'monday'
    if current_weekday != user_weekday:
        app.logger.info(f"[调度器调试] should_push_weekly: 用户 {user.email} 今天不是推送日 ({current_weekday} != {user_weekday})，返回False")
        return False
    
    # 统一时区格式进行比较（避免 offset-naive 和 offset-aware 时间混合）
    try:
        current_time = beijing_now()
        last_push_time = user.last_push
        
        # 如果 last_push 没有时区信息，假设它是北京时间
        if last_push_time.tzinfo is None:
            last_push_time = APP_TIMEZONE.localize(last_push_time)
        # 如果时区不同，转换为北京时间
        elif last_push_time.tzinfo != APP_TIMEZONE:
            last_push_time = last_push_time.astimezone(APP_TIMEZONE)
        
        time_since_last = current_time - last_push_time
        should_push = time_since_last.days >= 6
        app.logger.info(f"[调度器调试] should_push_weekly: 用户 {user.email} 距离上次推送 {time_since_last.days} 天，应该推送: {should_push}")
        return should_push
        
    except Exception as e:
        app.logger.error(f"[调度器] 用户 {user.email} 时间比较异常: {e}")
        # 异常情况下默认允许推送
        return True

def should_push_monthly(user, current_day):
    """检查是否应该每月推送"""
    if not user.last_push:
        return True
    
    # 检查今天是否是用户设置的推送日
    user_day = user.push_month_day or 1
    if current_day != user_day:
        return False
    
    # 统一时区格式进行比较（避免 offset-naive 和 offset-aware 时间混合）
    try:
        current_time = beijing_now()
        last_push_time = user.last_push
        
        # 如果 last_push 没有时区信息，假设它是北京时间
        if last_push_time.tzinfo is None:
            last_push_time = APP_TIMEZONE.localize(last_push_time)
        # 如果时区不同，转换为北京时间
        elif last_push_time.tzinfo != APP_TIMEZONE:
            last_push_time = last_push_time.astimezone(APP_TIMEZONE)
        
        time_since_last = current_time - last_push_time
        return time_since_last.days >= 25
        
    except Exception as e:
        app.logger.error(f"[调度器] 用户 {user.email} 每月推送时间比较异常: {e}")
        # 异常情况下默认允许推送
        return True

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
        self.default_query_prompt = """# 任务：构建专业级PubMed文献检索式

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
    - **最终输出**: 你的最终回复应仅包含最终成果本身，不得包含任何步骤说明、分析或其他无关内容。"""

        self.default_translation_prompt = """请将以下英文医学摘要准确翻译成中文，要求：
1. 保持专业术语的准确性
2. 语句通顺自然
3. 保持原文的逻辑结构
4. 只返回中文翻译结果，不要任何额外说明、标题或格式
5. 不要包含"中文译文："等前缀

英文摘要: {abstract}"""

        # 默认文献简介提示词
        self.default_brief_intro_prompt = """请为以下医学文献生成一句话简介，要求：
1. 简洁明了，不超过50个中文字符
2. 突出文献的核心发现或方法
3. 使用通俗易懂的语言，避免过于复杂的医学术语
4. 只返回简介内容，不要其他文字

文献标题：{title}
摘要：{abstract}"""
    
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

    def get_brief_intro_model(self):
        """获取文献简介模型 - 优先独立配置，自动继承翻译配置"""
        # 1. 尝试获取专门的简介模型配置
        intro_provider_id = SystemSetting.get_setting('ai_brief_intro_provider_id')
        intro_model_id = SystemSetting.get_setting('ai_brief_intro_model_id')
        
        if intro_provider_id and intro_model_id:
            try:
                model = AIModel.query.filter_by(
                    id=int(intro_model_id),
                    provider_id=int(intro_provider_id),
                    is_available=True
                ).first()
                
                if model and model.provider.is_active:
                    app.logger.info(f"使用专门配置的简介模型: {model.provider.provider_name}/{model.model_id}")
                    return {
                        'provider': model.provider,
                        'model': model.model_id
                    }
            except (ValueError, AttributeError):
                app.logger.warning(f"简介模型配置无效，尝试继承翻译配置")
        
        # 2. 自动继承翻译模型配置
        app.logger.info("未配置专门的简介模型，继承翻译模型配置")
        translator_model = self.get_configured_model('translator')
        if translator_model:
            app.logger.info(f"继承翻译配置: 提供商={translator_model.provider.provider_name}, 模型={translator_model.model_id}")
            return {
                'provider': translator_model.provider,
                'model': translator_model.model_id
            }
            
        return None

    def get_brief_intro_prompt(self):
        """获取文献简介提示词模板"""
        # 从数据库获取简介提示词模板
        template = AIPromptTemplate.query.filter_by(
            template_type='brief_intro',
            is_default=True
        ).first()
        
        if template:
            return template.prompt_content
        else:
            # 使用默认提示词
            return self.default_brief_intro_prompt
    
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

            # 获取提示词模板（从数据库）
            prompt_template = AIPromptTemplate.get_default_prompt('translator')
            if not prompt_template:
                prompt_template = self.default_translation_prompt

            # 构建批量翻译的提示词
            abstracts_text = ""
            for i, article in enumerate(articles, 1):
                abstracts_text += f"[摘要{i}]\n{article.abstract}\n\n"

            batch_prompt = f"""你是一个专业的医学文献翻译专家。请将以下{len(articles)}篇英文摘要翻译成中文。

翻译要求：
{prompt_template}

输出格式要求：
1. 按照[摘要1]、[摘要2]的格式返回翻译结果
2. 每个翻译结果之间用"---"分隔

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

    def generate_brief_intro(self, title, abstract):
        """为文献生成一句话简介"""
        try:
            # 检查是否启用AI简介功能
            brief_intro_enabled = SystemSetting.get_setting('ai_brief_intro_enabled', 'false') == 'true'
            if not brief_intro_enabled:
                return None
            
            # 获取配置的简介模型
            intro_model = self.get_brief_intro_model()
            if not intro_model:
                app.logger.warning("未找到或未配置文献简介模型")
                return None
            
            # 如果没有摘要，只使用标题
            if not abstract:
                abstract = "无摘要"
            
            # 获取简介提示词模板
            prompt_template = self.get_brief_intro_prompt()
            prompt = prompt_template.format(title=title, abstract=abstract)
            
            # 调用AI生成简介
            client = self.create_openai_client(intro_model.provider)
            
            response = client.chat.completions.create(
                model=intro_model.model_name,
                messages=[
                    {"role": "system", "content": "你是一个专业的医学文献分析专家。"},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.3  # 稍微提高创造性以获得更生动的简介
            )
            
            # 提取简介结果
            brief_intro = response.choices[0].message.content.strip()
            
            app.logger.info(f"文献简介生成成功，标题长度: {len(title)}, 简介长度: {len(brief_intro)}")
            return brief_intro
            
        except Exception as e:
            app.logger.error(f"文献简介生成失败: {str(e)}")
            return None

    def batch_generate_brief_intros(self, articles):
        """批量生成文献简介 - 真正的批量API调用"""
        try:
            # 检查是否启用AI简介功能
            brief_intro_enabled = SystemSetting.get_setting('ai_brief_intro_enabled', 'false') == 'true'
            if not brief_intro_enabled:
                app.logger.info("AI文献简介功能未启用")
                return False
            
            # 获取配置的简介模型（继承翻译配置）
            intro_model = self.get_brief_intro_model()
            if not intro_model:
                app.logger.warning("未找到或未配置文献简介模型")
                return False
            
            # 筛选需要生成简介的文章
            articles_need_intro = [article for article in articles if not article.brief_intro]
            
            if not articles_need_intro:
                app.logger.info("没有需要生成简介的文献")
                return True
            
            # 继承翻译配置的批处理设置
            batch_size = int(SystemSetting.get_setting('ai_translation_batch_size', '20'))
            batch_delay = int(SystemSetting.get_setting('ai_translation_batch_delay', '3'))
            
            app.logger.info(f"开始批量生成 {len(articles_need_intro)} 篇文献简介，批次大小: {batch_size}, 间隔: {batch_delay}秒")
            
            # 分批处理 - 真正的批量API调用
            for i in range(0, len(articles_need_intro), batch_size):
                batch = articles_need_intro[i:i+batch_size]
                
                # 构建批量请求内容
                batch_content = []
                for idx, article in enumerate(batch):
                    abstract = article.abstract or "无摘要"
                    batch_content.append(f"文献{idx+1}:")
                    batch_content.append(f"标题：{article.title}")
                    batch_content.append(f"摘要：{abstract}")
                    batch_content.append("")  # 空行分隔
                
                # 获取简介提示词模板（从数据库）
                prompt_template = self.get_brief_intro_prompt()

                # 构建批量提示词，使用数据库模板的要求
                batch_articles_text = chr(10).join(batch_content)
                batch_prompt = f"""请为以下 {len(batch)} 篇医学文献分别生成简介。

简介要求：
{prompt_template}

输出格式要求：
- 按文献顺序生成 {len(batch)} 个简介
- 每个简介用 | 分隔（不要换行、不要序号）
- 格式示例：简介1内容|简介2内容|简介3内容
- 只输出简介内容，不要其他文字

文献列表：
{batch_articles_text}"""
                
                try:
                    # 调用AI API进行批量生成
                    client = self.create_openai_client(intro_model['provider'])
                    if not client:
                        app.logger.error("无法创建AI客户端")
                        continue
                    
                    response = client.chat.completions.create(
                        model=intro_model['model'],
                        messages=[
                            {"role": "system", "content": "你是一个专业的医学文献分析助手。"},
                            {"role": "user", "content": batch_prompt}
                        ],
                        temperature=0.3
                    )

                    batch_result = response.choices[0].message.content.strip()
                    app.logger.info(f"批次 {i//batch_size + 1} AI返回内容长度: {len(batch_result)}")
                    app.logger.debug(f"批次 {i//batch_size + 1} AI完整返回:\n{batch_result}")

                    # 解析批量结果
                    brief_intros = self._parse_batch_brief_intro_result(batch_result, len(batch))
                    
                    # 分配给对应的文章
                    for j, article in enumerate(batch):
                        if j < len(brief_intros) and brief_intros[j].strip():
                            article.brief_intro = brief_intros[j].strip()
                    
                    # 保存批次结果
                    db.session.commit()
                    app.logger.info(f"批次 {i//batch_size + 1} 简介生成完成，处理了 {len(batch)} 篇文献")
                    
                except Exception as e:
                    app.logger.error(f"批次 {i//batch_size + 1} 简介生成失败: {str(e)}")
                    # 失败时回退到单篇处理
                    for article in batch:
                        try:
                            brief_intro = self.generate_brief_intro(article.title, article.abstract)
                            if brief_intro:
                                article.brief_intro = brief_intro
                        except:
                            pass
                    db.session.commit()
                
                # 批次间延迟
                if i + batch_size < len(articles_need_intro):
                    time.sleep(batch_delay)
            
            app.logger.info(f"批量简介生成完成")
            return True
            
        except Exception as e:
            app.logger.error(f"批量简介生成失败: {str(e)}")
            return False
    
    def _parse_batch_brief_intro_result(self, result_text, expected_count):
        """解析批量简介生成结果"""
        try:
            app.logger.info(f"开始解析批量简介结果，原始文本长度: {len(result_text)}")
            app.logger.debug(f"原始返回内容前200字符: {result_text[:200]}")

            # 按|分隔
            intros = result_text.split('|')
            app.logger.info(f"按|分隔后得到 {len(intros)} 个片段，期望 {expected_count} 个")

            # 清理和验证结果
            cleaned_intros = []
            for idx, intro in enumerate(intros):
                intro = intro.strip()
                if intro:
                    # 移除多种可能的序号前缀格式
                    # 匹配: "简介1"、"简介1："、"1:"、"1."、"1、" 等
                    intro = re.sub(r'^[简介]*\d+[：:：\.\、]\s*', '', intro)
                    intro = re.sub(r'^简介\d+\s*$', '', intro)  # 移除纯占位符如"简介1"
                    intro = intro.strip()

                    # 只添加非空内容
                    if intro and not re.match(r'^简介\d+$', intro):
                        cleaned_intros.append(intro)
                        app.logger.debug(f"简介{idx+1}: {intro[:50]}...")
                    else:
                        app.logger.warning(f"跳过无效简介片段{idx+1}: '{intro}'")
                        cleaned_intros.append("")  # 添加空字符串占位

            app.logger.info(f"清理后得到 {len([x for x in cleaned_intros if x])} 个有效简介")

            # 确保返回期望数量的结果
            while len(cleaned_intros) < expected_count:
                cleaned_intros.append("")

            return cleaned_intros[:expected_count]

        except Exception as e:
            app.logger.error(f"解析批量简介结果失败: {str(e)}")
            return [""] * expected_count
    
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
        <link href="https://cdnjs.cloudflare.com/ajax/libs/bootstrap/5.3.8/css/bootstrap.min.css" rel="stylesheet">
        <link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/7.1.0/css/all.min.css" rel="stylesheet">
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
                
                <!-- 文献简介生成配置 -->
                <div class="col-md-12 mt-3">
                    <div class="card border-warning h-100">
                        <div class="card-header bg-warning text-dark">
                            <h6 class="mb-0"><i class="fas fa-file-alt"></i> 文献简介生成配置</h6>
                        </div>
                        <div class="card-body">
                            <form method="POST" action="/admin/ai/config/brief-intro">
                                <div class="mb-3">
                                    <div class="form-check form-switch">
                                        <input class="form-check-input" type="checkbox" id="briefIntroEnabled" 
                                               name="enabled" value="true" {{ 'checked' if ai_settings.ai_brief_intro_enabled == 'true' }}>
                                        <label class="form-check-label" for="briefIntroEnabled">
                                            <strong>启用文献简介生成功能</strong>
                                        </label>
                                    </div>
                                    <small class="text-muted">启用后在推送邮件中为每篇文献生成一句话简介</small>
                                </div>
                                
                                <div class="mb-3">
                                    <label class="form-label">选择提供商：</label>
                                    <select class="form-select" name="provider_id" id="briefIntroProviderSelect" onchange="updateBriefIntroModels()">
                                        <option value="">请选择提供商</option>
                                        {% for provider in providers %}
                                            {% if provider.is_active and provider.models %}
                                                <option value="{{ provider.id }}" data-provider-name="{{ provider.provider_name }}"
                                                        {{ 'selected' if ai_settings.ai_brief_intro_provider_id == provider.id|string }}>
                                                    {{ provider.provider_name }}
                                                </option>
                                            {% endif %}
                                        {% endfor %}
                                    </select>
                                </div>
                                
                                <div class="mb-3">
                                    <label class="form-label">选择模型：</label>
                                    <select class="form-select" name="model_id" id="briefIntroModelSelect"
                                            {{ 'disabled' if not ai_settings.ai_brief_intro_provider_id else '' }}>
                                        <option value="">{{ '请先选择提供商' if not ai_settings.ai_brief_intro_provider_id else '请选择模型' }}</option>
                                    </select>
                                </div>
                                
                                <button type="submit" class="btn btn-warning w-100 mb-3">
                                    <i class="fas fa-save"></i> 保存配置
                                </button>
                            </form>
                            
                            <!-- 测试功能 -->
                            <div class="border-top pt-3 mt-3">
                                <h6 class="small">测试文献简介生成：</h6>
                                <form id="testBriefIntroForm" onsubmit="event.preventDefault(); testBriefIntro();">
                                    <div class="mb-2">
                                        <input type="text" class="form-control form-control-sm" name="title" placeholder="输入文献标题..." required>
                                    </div>
                                    <div class="mb-2">
                                        <textarea class="form-control form-control-sm" name="abstract" rows="3" placeholder="输入文献摘要..." required></textarea>
                                    </div>
                                    <button type="submit" class="btn btn-outline-warning btn-sm">
                                        <i class="fas fa-play"></i> 测试生成
                                    </button>
                                </form>
                                <div id="briefIntroResult" class="mt-2"></div>
                            </div>
                            
                            <!-- 当前配置显示 -->
                            <div class="mt-3 p-3 bg-light rounded">
                                <h6 class="small mb-2">当前配置：</h6>
                                <p class="small mb-1">状态：
                                    {% if ai_settings.ai_brief_intro_enabled == 'true' %}
                                        <span class="badge bg-success">已启用</span>
                                    {% else %}
                                        <span class="badge bg-secondary">已禁用</span>
                                    {% endif %}
                                </p>
                                <p class="small mb-0">模型：
                                    {% if ai_settings.ai_brief_intro_provider_id and ai_settings.ai_brief_intro_model_id %}
                                        {% for provider in providers %}
                                            {% if provider.id|string == ai_settings.ai_brief_intro_provider_id %}
                                                {% for model in provider.models %}
                                                    {% if model.id|string == ai_settings.ai_brief_intro_model_id %}
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
        
        <script src="https://cdnjs.cloudflare.com/ajax/libs/bootstrap/5.3.8/js/bootstrap.bundle.min.js"></script>
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
                },
                briefIntro: {
                    providerId: "{{ ai_settings.ai_brief_intro_provider_id }}",
                    modelId: "{{ ai_settings.ai_brief_intro_model_id }}"
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
                
                // 初始化文献简介的模型选择
                if (savedConfig.briefIntro.providerId) {
                    updateBriefIntroModels();
                    if (savedConfig.briefIntro.modelId) {
                        setTimeout(() => {
                            document.getElementById('briefIntroModelSelect').value = savedConfig.briefIntro.modelId;
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
            
            // 更新文献简介生成的模型选择
            function updateBriefIntroModels() {
                const providerSelect = document.getElementById('briefIntroProviderSelect');
                const modelSelect = document.getElementById('briefIntroModelSelect');
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
                    if (savedConfig.briefIntro.modelId && providerId === savedConfig.briefIntro.providerId) {
                        modelSelect.value = savedConfig.briefIntro.modelId;
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
            
            function testBriefIntro() {
                const form = document.getElementById('testBriefIntroForm');
                const formData = new FormData(form);
                const resultDiv = document.getElementById('briefIntroResult');
                
                resultDiv.innerHTML = '<div class="alert alert-info"><i class="fas fa-spinner fa-spin"></i> 生成中...</div>';
                
                fetch('/admin/ai/test/brief-intro', {
                    method: 'POST',
                    body: formData
                })
                .then(response => response.json())
                .then(data => {
                    if (data.success) {
                        resultDiv.innerHTML = `
                            <div class="alert alert-success">
                                <strong>生成成功！</strong><br>
                                <small>${data.message}</small><br>
                                <strong>生成的简介：</strong><br>
                                <div class="border rounded p-2 mt-2">${data.brief_intro}</div>
                            </div>
                        `;
                    } else {
                        resultDiv.innerHTML = `<div class="alert alert-danger"><strong>生成失败：</strong> ${data.message}</div>`;
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
        <link href="https://cdnjs.cloudflare.com/ajax/libs/bootstrap/5.3.8/css/bootstrap.min.css" rel="stylesheet">
        <link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/7.1.0/css/all.min.css" rel="stylesheet">
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
        
        <script src="https://cdnjs.cloudflare.com/ajax/libs/bootstrap/5.3.8/js/bootstrap.bundle.min.js"></script>
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
        <link href="https://cdnjs.cloudflare.com/ajax/libs/bootstrap/5.3.8/css/bootstrap.min.css" rel="stylesheet">
        <link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/7.1.0/css/all.min.css" rel="stylesheet">
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
                
                <!-- 简介生成提示词 -->
                <div class="col-md-12 mt-4">
                    <div class="card">
                        <div class="card-header">
                            <h5><i class="fas fa-lightbulb"></i> 简介生成提示词</h5>
                        </div>
                        <div class="card-body">
                            <form method="POST" action="/admin/ai/prompt/save">
                                <input type="hidden" name="template_type" value="brief_intro">
                                <div class="mb-3">
                                    <label class="form-label">提示词内容</label>
                                    <textarea name="prompt_content" class="form-control" rows="8" placeholder="输入简介生成提示词...">{% for prompt in brief_intro_prompts %}{% if prompt.is_default %}{{ prompt.prompt_content }}{% endif %}{% endfor %}</textarea>
                                    <div class="form-text">使用 {title} 和 {abstract} 作为标题和摘要占位符</div>
                                </div>
                                <button type="submit" class="btn btn-primary">
                                    <i class="fas fa-save"></i> 保存简介提示词
                                </button>
                            </form>
                        </div>
                    </div>
                    
                    <!-- 历史版本 -->
                    {% if brief_intro_prompts|length > 1 %}
                    <div class="card mt-3">
                        <div class="card-header">
                            <h6><i class="fas fa-history"></i> 历史版本</h6>
                        </div>
                        <div class="card-body">
                            {% for prompt in brief_intro_prompts %}
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
        
        <script src="https://cdnjs.cloudflare.com/ajax/libs/bootstrap/5.3.8/js/bootstrap.bundle.min.js"></script>
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
        执行限流的请求 - 简化版本，直接在主线程执行避免死锁
        
        Args:
            request_func: 要执行的请求函数
            
        Returns:
            请求结果
        """
        # 简化版本：直接在主线程执行，避免复杂的线程间通信导致的卡死
        try:
            # 执行限流控制
            current_time = time.time()
            
            # 简单的限流逻辑：0.5秒间隔
            time_since_last = current_time - self._last_request_time
            min_interval = 0.5  # 固定0.5秒间隔，足够保守
            
            if time_since_last < min_interval:
                sleep_time = min_interval - time_since_last
                time.sleep(sleep_time)
            
            # 记录请求时间
            self._last_request_time = time.time()
            
            # 直接执行请求
            return request_func()
            
        except Exception as e:
            app.logger.error(f"PubMed API请求失败: {str(e)}")
            raise
    
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
                        
                        return datetime(year, month, day, tzinfo=APP_TIMEZONE)
                    except ValueError:
                        pass
            
            # 如果没有PubDate，尝试其他日期字段
            date_completed = article_elem.find('.//DateCompleted')
            if date_completed is not None:
                year_elem = date_completed.find('Year')
                if year_elem is not None and year_elem.text:
                    try:
                        return datetime(int(year_elem.text), 1, 1, tzinfo=APP_TIMEZONE)
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

        集成缓存优化:
        - 优先从缓存获取搜索结果
        - 缓存未命中时调用PubMed API
        - 自动缓存新搜索结果

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
        # 构建筛选参数字典(用于缓存键生成)
        filter_params = {
            'days_back': days_back,
            'max_results': max_results,
            'jcr_filter': jcr_filter,
            'zky_filter': zky_filter,
            'exclude_no_issn': exclude_no_issn
        }

        # 尝试从缓存获取
        cached_data = search_cache_service.get_cached_results(keywords, filter_params)

        if cached_data:
            # 缓存命中
            pmids = cached_data.get('pmids', [])
            articles = cached_data.get('articles', [])

            # 如果是宽松匹配,需要二次筛选
            if cached_data.get('requires_filtering', False):
                app.logger.info(f"[缓存-宽松匹配] 对 {len(articles)} 篇文章进行二次筛选")
                filtered_articles = self._apply_filters(
                    articles, jcr_filter, zky_filter, exclude_no_issn, max_results
                )
            else:
                # 精确匹配,直接使用缓存结果
                app.logger.info(f"[缓存-精确匹配] 直接使用 {len(articles)} 篇缓存文章")
                filtered_articles = articles[:max_results]

            excluded_no_issn = len(articles) - len(filtered_articles)

            return {
                'total_found': len(articles),
                'articles': filtered_articles,
                'filtered_count': len(filtered_articles),
                'excluded_no_issn': excluded_no_issn,
                'from_cache': True  # 标记来自缓存
            }

        # 缓存未命中,执行真实搜索
        app.logger.info(f"[缓存未命中] 调用PubMed API搜索: {keywords[:50]}")

        # 第一步：搜索获取PMID
        pmids = self.search_articles(keywords, max_results * 2, days_back, user_email)

        if not pmids:
            return {
                'total_found': 0,
                'articles': [],
                'filtered_count': 0,
                'excluded_no_issn': 0,
                'from_cache': False
            }

        # 第二步：获取详细信息
        articles = self.get_article_details(pmids)

        # 第三步：应用筛选条件
        filtered_articles = self._apply_filters(
            articles, jcr_filter, zky_filter, exclude_no_issn, max_results
        )

        excluded_no_issn = len(articles) - len(filtered_articles)

        # 缓存搜索结果(缓存完整的articles,而非筛选后的结果)
        try:
            search_cache_service.set_cached_results(
                keywords=keywords,
                filter_params=filter_params,
                pmids=pmids,
                articles=articles  # 缓存完整结果供后续宽松匹配使用
            )
            app.logger.info(f"[缓存写入] 已缓存 {len(articles)} 篇文章")
        except Exception as e:
            app.logger.error(f"[缓存写入失败] {e}")

        return {
            'total_found': len(articles),
            'articles': filtered_articles,
            'filtered_count': len(filtered_articles),
            'excluded_no_issn': excluded_no_issn,
            'from_cache': False  # 标记来自API
        }

    def _apply_filters(self, articles, jcr_filter, zky_filter, exclude_no_issn, max_results):
        """
        应用筛选条件到文章列表

        提取为独立方法供缓存宽松匹配时复用

        Args:
            articles: 文章列表
            jcr_filter: JCR筛选条件
            zky_filter: 中科院筛选条件
            exclude_no_issn: 是否排除无ISSN文章
            max_results: 最大结果数

        Returns:
            list: 筛选后的文章列表
        """
        filtered_articles = []

        for article in articles:
            # 检查是否有ISSN信息
            has_issn = bool(article.get('issn') or article.get('eissn'))

            if exclude_no_issn and not has_issn:
                continue

            # 如果没有ISSN但不排除，则保留文章但不应用期刊筛选
            if not has_issn:
                filtered_articles.append(article)
                if len(filtered_articles) >= max_results:
                    break
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

                if 'top' in zky_filter and zky_filter['top']:
                    # 只要求Top期刊时才筛选
                    is_top = zky_top == '是'
                    if not is_top:
                        continue

            filtered_articles.append(article)

            # 限制最终结果数量
            if len(filtered_articles) >= max_results:
                break

        return filtered_articles
    
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

            # 获取期刊质量信息(如果需要筛选)
            quality_info = None
            if jcr_filter or zky_filter:
                issn = article.get('issn', '')
                eissn = article.get('eissn', '')
                quality_info = self.get_journal_quality(issn, eissn)

            # 应用JCR筛选
            if jcr_filter:
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
                zky_category = quality_info.get('zky_category', '')
                zky_top = quality_info.get('zky_top', '')

                if 'category' in zky_filter:
                    if not zky_category or zky_category not in zky_filter['category']:
                        continue

                if 'top' in zky_filter and zky_filter['top']:
                    # 只要求Top期刊时才筛选
                    is_top = zky_top == '是'
                    if not is_top:
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
                        print(f"[同步] 已更新 {key}")
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
                    print(f"[同步] 已创建 OpenAI 配置: {openai_api_base}")
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
                            print(f"[同步] 自动创建了 {len(models)} 个AI模型")
                            app.logger.info(f"自动创建了 {len(models)} 个AI模型")
                        else:
                            print(f"[同步] [WARN] 未能获取到模型列表，请手动刷新")
                    except Exception as e:
                        print(f"[同步] [WARN] 自动获取模型失败: {e}")
                        app.logger.warning(f"自动获取AI模型失败: {e}")
                else:
                    print(f"[同步] - OpenAI 配置已存在，跳过创建")
            
            print(f"[Worker {worker_id}] [同步] 环境变量同步完成")
    except Exception as e:
        print(f"[同步] ✗ 同步失败: {e}")
        app.logger.error(f"同步环境变量失败: {e}")

# 使用文件锁确保多Worker环境下只执行一次
@app.before_request
def before_request_sync():
    """在第一个请求时同步环境变量(多Worker安全)"""
    # 如果正在初始化调度器，跳过同步避免嵌套触发
    if getattr(app, '_scheduler_initializing', False):
        return

    sync_flag_file = '/app/data/env_sync_done'
    lock_file = '/app/data/env_sync.lock'

    # 快速路径：检查是否已完成同步
    if os.path.exists(sync_flag_file):
        try:
            file_mtime = os.path.getmtime(sync_flag_file)
            if time.time() - file_mtime < 3600:  # 1小时内有效
                return
        except:
            pass

    # 使用文件锁防止并发执行
    lock_fd = None
    try:
        # 尝试创建锁文件(原子操作)
        import fcntl
        print(f"[Worker {os.getpid()}] [同步] 尝试获取同步锁...")
        lock_fd = os.open(lock_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        print(f"[Worker {os.getpid()}] [同步] 成功获取同步锁")

        # 获得锁后再次检查(双重检查锁定模式)
        if os.path.exists(sync_flag_file):
            try:
                file_mtime = os.path.getmtime(sync_flag_file)
                if time.time() - file_mtime < 3600:
                    print(f"[Worker {os.getpid()}] [同步] 标记文件已存在，跳过同步")
                    return
            except:
                pass

        # 立即创建标记文件(防止其他Worker在同步期间获取锁)
        with open(sync_flag_file, 'w') as f:
            f.write(f"{os.getpid()}_syncing")

        # 执行同步
        sync_env_to_database()

        # 更新标记文件为完成状态
        with open(sync_flag_file, 'w') as f:
            f.write(f"{os.getpid()}_done")

    except FileExistsError:
        # 其他Worker正在执行同步,等待完成
        print(f"[Worker {os.getpid()}] [同步] 锁文件已存在，等待其他Worker完成...")
        max_wait = 10  # 最多等待10秒
        waited = 0
        while waited < max_wait:
            if os.path.exists(sync_flag_file):
                # 同步已完成
                print(f"[Worker {os.getpid()}] [同步] 检测到同步已完成，跳过")
                return
            time.sleep(0.1)
            waited += 0.1
        print(f"[Worker {os.getpid()}] [同步] 等待超时，但标记文件仍不存在")

    except Exception as e:
        # 如果文件锁不可用,降级为进程级别的检查
        print(f"[Worker {os.getpid()}] [同步] 文件锁异常: {e}，使用降级方案")
        global _sync_done
        if not _sync_done:
            sync_env_to_database()
            _sync_done = True
    finally:
        # 清理锁文件
        if lock_fd is not None:
            try:
                os.close(lock_fd)
                os.remove(lock_file)
                print(f"[Worker {os.getpid()}] [同步] 已释放同步锁")
            except:
                pass

_sync_done = False  # 降级方案的备用标记

# 应用上下文中初始化调度器（Flask 2.0+兼容）
def initialize_scheduler_safely():
    """安全初始化调度器，避免重复初始化"""
    init_flag_file = '/app/data/scheduler_init_done'
    rq_schedule_flag_file = '/app/data/rq_schedule_init_done'

    try:
        # 检查是否已经初始化
        if scheduler.running:
            print(f"调度器已在PID {os.getpid()}中运行")
            # 即使调度器已运行，也检查是否需要批量调度订阅
            try:
                # 检查数据库是否存在（从配置中获取路径）
                db_uri = app.config.get('SQLALCHEMY_DATABASE_URI', '')
                if db_uri.startswith('sqlite:///'):
                    db_path = db_uri.replace('sqlite:///', '')
                    if not os.path.exists(db_path):
                        print("[RQ] 数据库不存在，跳过批量调度")
                        return

                # 检查RQ模式是否启用
                rq_mode = os.environ.get('RQ_MODE', 'enabled')
                if rq_mode != 'enabled':
                    print("[RQ] RQ模式未启用，跳过批量调度")
                    return

                # 改进的调度有效性检查：同时验证标记文件和Redis中的实际任务数量
                rq_schedule_valid = False
                if os.path.exists(rq_schedule_flag_file):
                    try:
                        # 检查标记文件的修改时间,如果超过5分钟则认为失效
                        file_mtime = os.path.getmtime(rq_schedule_flag_file)
                        if time.time() - file_mtime < 300:  # 5分钟内有效
                            # 进一步验证Redis中是否真的有调度任务
                            from rq_config import get_queue_info
                            queue_info = get_queue_info()
                            total_scheduled = queue_info.get('total_scheduled', 0)

                            if total_scheduled > 0:
                                rq_schedule_valid = True
                                print(f"[RQ] 已有 {total_scheduled} 个调度任务在队列中，跳过批量调度")
                            else:
                                print("[RQ] 标记文件存在但Redis无调度任务，将重新调度")
                                os.remove(rq_schedule_flag_file)
                        else:
                            print("[RQ] 调度标记文件已过期，将触发重新调度")
                            os.remove(rq_schedule_flag_file)
                    except Exception as check_error:
                        print(f"[RQ] 调度有效性检查失败: {check_error}，将重新调度")
                        if os.path.exists(rq_schedule_flag_file):
                            os.remove(rq_schedule_flag_file)

                # 检查是否已经调度过
                if not rq_schedule_valid:
                    print("[RQ] 检测到需要初始化订阅调度...")

                    # 检查是否有活跃订阅
                    subscription_count = Subscription.query.filter_by(is_active=True).count()
                    if subscription_count == 0:
                        print("[RQ] 没有活跃订阅，跳过批量调度")
                        # 创建标记文件以避免重复检查
                        with open(rq_schedule_flag_file, 'w') as f:
                            f.write(f"{os.getpid()}|{int(time.time())}")
                        return

                    from rq_config import enqueue_job
                    from tasks import batch_schedule_all_subscriptions
                    job = enqueue_job(batch_schedule_all_subscriptions, priority='high')
                    print(f"[RQ] 批量调度任务已排队: {job.id}")
                    print(f"[RQ] 将调度 {subscription_count} 个活跃订阅到队列")
                    # 注意：标记文件将由Worker在任务成功后创建（tasks.py:207-210）
            except Exception as e:
                print(f"[RQ] 批量调度订阅失败: {e}")
                import traceback
                traceback.print_exc()
            return

        if os.path.exists(init_flag_file):
            try:
                with open(init_flag_file, 'r') as f:
                    old_pid = int(f.read().strip())
                # 检查进程是否存在
                os.kill(old_pid, 0)
                print(f"调度器已在进程 {old_pid} 中初始化，跳过")
                return
            except (OSError, ValueError):
                # 进程不存在，删除标记文件
                os.remove(init_flag_file)

        # 初始化调度器
        print(f"进程 {os.getpid()} 开始初始化调度器...")
        init_scheduler()

        # 创建成功标记
        if scheduler.running:
            with open(init_flag_file, 'w') as f:
                f.write(str(os.getpid()))
            print(f"调度器初始化成功 (PID: {os.getpid()})")

            # 批量调度所有已有订阅到RQ队列（容器重启后自动恢复）
            try:
                # 检查数据库是否存在（避免初次使用时出错）
                db_uri = app.config.get('SQLALCHEMY_DATABASE_URI', '')
                if db_uri.startswith('sqlite:///'):
                    db_path = db_uri.replace('sqlite:///', '')
                    if not os.path.exists(db_path):
                        print("[RQ] 数据库不存在，跳过批量调度")
                        return

                # 检查是否使用RQ模式且Redis可用
                rq_mode = os.environ.get('RQ_MODE', 'enabled')
                if rq_mode == 'enabled':
                    print("[RQ] 开始批量调度已有订阅...")
                    from rq_config import redis_conn, enqueue_job, get_queue_info
                    from tasks import batch_schedule_all_subscriptions

                    # 测试Redis连接
                    redis_conn.ping()

                    # 改进的调度有效性检查：验证Redis中的实际任务数量
                    rq_schedule_valid = False
                    if os.path.exists(rq_schedule_flag_file):
                        try:
                            file_mtime = os.path.getmtime(rq_schedule_flag_file)
                            if time.time() - file_mtime < 300:  # 5分钟内有效
                                # 验证Redis中是否真的有调度任务
                                queue_info = get_queue_info()
                                total_scheduled = queue_info.get('total_scheduled', 0)

                                if total_scheduled > 0:
                                    rq_schedule_valid = True
                                    print(f"[RQ] 已有 {total_scheduled} 个调度任务在队列中，跳过批量调度")
                                else:
                                    print("[RQ] 标记文件存在但Redis无调度任务，将重新调度")
                                    os.remove(rq_schedule_flag_file)
                            else:
                                print("[RQ] 调度标记文件已过期，将触发重新调度")
                                os.remove(rq_schedule_flag_file)
                        except Exception as check_error:
                            print(f"[RQ] 调度有效性检查失败: {check_error}，将重新调度")
                            if os.path.exists(rq_schedule_flag_file):
                                os.remove(rq_schedule_flag_file)

                    if not rq_schedule_valid:
                        # 检查是否有订阅需要调度
                        subscription_count = Subscription.query.filter_by(is_active=True).count()
                        if subscription_count == 0:
                            print("[RQ] 没有活跃订阅，跳过批量调度")
                            # 创建标记文件以避免重复检查
                            with open(rq_schedule_flag_file, 'w') as f:
                                f.write(f"{os.getpid()}|{int(time.time())}")
                            return

                        # 提交批量调度任务（高优先级）
                        job = enqueue_job(batch_schedule_all_subscriptions, priority='high')
                        print(f"[RQ] 批量调度任务已排队: {job.id}")
                        print(f"[RQ] 将调度 {subscription_count} 个活跃订阅到队列")

                        # 注意：标记文件将由Worker在任务成功后创建（tasks.py:207-210）
                else:
                    print("[调度器] APScheduler降级模式，不需要批量调度")
            except Exception as e:
                print(f"[RQ] 批量调度订阅失败（非致命错误）: {e}")
                import traceback
                traceback.print_exc()

    except Exception as e:
        print(f"调度器初始化失败: {e}")
        if os.path.exists(init_flag_file):
            try:
                os.remove(init_flag_file)
            except:
                pass

# 在第一个请求时初始化调度器
@app.before_request
def ensure_scheduler_running():
    """确保调度器在第一个请求时运行"""
    if not hasattr(app, '_scheduler_init_attempted'):
        # 设置标记避免嵌套触发环境变量同步
        app._scheduler_initializing = True
        try:
            with app.app_context():
                initialize_scheduler_safely()
        finally:
            app._scheduler_initializing = False
        app._scheduler_init_attempted = True

# ==================== 健康检查端点 ====================
@app.route('/health')
def health_check():
    """Docker healthcheck专用端点，避免访问首页导致的副作用"""
    try:
        # 检查数据库连接
        db.session.execute(db.text('SELECT 1'))
        return {'status': 'healthy', 'timestamp': datetime.now(APP_TIMEZONE).isoformat()}, 200
    except Exception as e:
        return {'status': 'unhealthy', 'error': str(e)}, 503

# 路由
@app.route('/', methods=['GET', 'POST'])
def index():
    search_results = None
    test_subscription = None

    # 处理测试订阅请求(从URL参数) - 只在GET请求时处理
    if current_user.is_authenticated and request.method == 'GET':
        test_sub_id = request.args.get('test_subscription_id')
        if test_sub_id:
            # 使用时间戳防止短时间内重复加载（而不是永久标记）
            import time
            session_key = f'test_sub_loaded_{test_sub_id}_{current_user.id}'
            last_load_time = session.get(session_key, 0)
            current_time = time.time()

            # 如果在30秒内已经加载过，直接重定向清除URL参数
            if current_time - last_load_time < 30:
                app.logger.info(f"测试订阅 {test_sub_id} 在30秒内重复访问，重定向到首页")
                return redirect(url_for('index'))

            # 标记当前加载时间（30秒后自动失效）
            session[session_key] = current_time

            subscription_obj = Subscription.query.filter_by(
                id=int(test_sub_id),
                user_id=current_user.id
            ).first()

            # 转换为可序列化的字典
            if subscription_obj:
                test_subscription = {
                    'id': subscription_obj.id,
                    'keywords': subscription_obj.keywords,
                    'jcr_quartiles': subscription_obj.jcr_quartiles,  # JCR分区JSON字符串
                    'min_impact_factor': subscription_obj.min_impact_factor,
                    'cas_categories': subscription_obj.cas_categories,  # 中科院分区JSON字符串
                    'cas_top_only': subscription_obj.cas_top_only,
                    'exclude_no_issn': subscription_obj.exclude_no_issn,
                    'search_days': subscription_obj.days_back  # 注意字段名是days_back
                }

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

                # 调整时间窗口到30秒，防止重复搜索请求
                if current_time - last_search_time < 30:
                    app.logger.warning(f"重复搜索请求被拒绝: {keywords} (用户: {current_user.email}, 间隔: {current_time - last_search_time:.1f}秒)")
                    flash('请不要重复提交搜索请求，请等待上一次搜索完成', 'warning')
                    return render_template_string(get_index_template(), search_results=search_results, test_subscription=test_subscription)

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

    return render_template_string(get_index_template(), search_results=search_results, test_subscription=test_subscription)

def get_index_template():
    """获取主页模板"""
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <title>PubMed Literature Push</title>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <link href="https://cdnjs.cloudflare.com/ajax/libs/bootstrap/5.3.8/css/bootstrap.min.css" rel="stylesheet">
        <link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/7.1.0/css/all.min.css" rel="stylesheet">
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

                                <!-- 预设模板 -->
                                <div class="mb-3">
                                    <label class="form-label">快速选择模板</label>
                                    <div class="d-grid gap-2">
                                        <button type="button" class="btn btn-sm btn-outline-secondary text-start" onclick="applyTemplate('high_quality')">
                                            <span class="badge bg-warning text-dark me-2">⭐</span>
                                            <strong>高质量期刊</strong>
                                            <br><small class="text-muted ms-4">中科院1区或JCR Q1，且为Top期刊</small>
                                        </button>
                                        <button type="button" class="btn btn-sm btn-outline-secondary text-start" onclick="applyTemplate('medium_quality')">
                                            <span class="badge bg-info text-dark me-2">📚</span>
                                            <strong>中等质量期刊</strong>
                                            <br><small class="text-muted ms-4">中科院1-2区或JCR Q1-Q2</small>
                                        </button>
                                        <button type="button" class="btn btn-sm btn-outline-secondary text-start" onclick="applyTemplate('high_impact')">
                                            <span class="badge bg-success me-2">📈</span>
                                            <strong>高影响因子</strong>
                                            <br><small class="text-muted ms-4">影响因子≥5且为1-2区</small>
                                        </button>
                                        <button type="button" class="btn btn-sm btn-outline-secondary text-start" onclick="applyTemplate('top_journals_only')">
                                            <span class="badge bg-danger me-2">🏆</span>
                                            <strong>仅Top期刊</strong>
                                            <br><small class="text-muted ms-4">中科院Top期刊，不限分区</small>
                                        </button>
                                        <button type="button" class="btn btn-sm btn-outline-secondary text-start" onclick="applyTemplate('basic_quality')">
                                            <span class="badge bg-secondary me-2">📋</span>
                                            <strong>基础质量筛选</strong>
                                            <br><small class="text-muted ms-4">排除无ISSN，1-3区或Q1-Q3</small>
                                        </button>
                                        <button type="button" class="btn btn-sm btn-outline-danger" onclick="clearAllFilters()">
                                            <i class="fas fa-times"></i> 清除所有筛选
                                        </button>
                                    </div>
                                </div>

                                <div class="alert alert-info py-2 px-3 mb-3" style="font-size: 0.875rem;">
                                    <i class="fas fa-info-circle"></i> <strong>提示：</strong><br>
                                    • 点击模板快速应用，也可手动调整下方条件<br>
                                    • 同类分区多选为"或"关系，不同条件为"且"关系
                                </div>

                                <input type="hidden" name="use_advanced_filter" id="use_advanced_filter" value="false">
                                <input type="hidden" name="filter_config" id="filter_config_input">

                                <div class="mb-3">
                                    <div class="form-check">
                                        <input class="form-check-input" type="checkbox" name="exclude_no_issn" checked>
                                        <label class="form-check-label">排除无ISSN信息的文献</label>
                                    </div>
                                </div>

                                <!-- JCR筛选 -->
                                <div class="mb-3">
                                    <label class="form-label">JCR分区筛选 <small class="text-muted">(多选为"或"关系)</small></label>
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
                                    <label class="form-label">最小影响因子 <small class="text-muted">(与其他条件为"且"关系)</small></label>
                                    <input type="number" class="form-control" name="min_if" step="0.1"
                                           placeholder="如 1.5">
                                </div>

                                <!-- 中科院筛选 -->
                                <div class="mb-3">
                                    <label class="form-label">中科院分区筛选 <small class="text-muted">(多选为"或"关系)</small></label>
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
                                        <label class="form-check-label">只显示Top期刊 <small class="text-muted">(与其他条件为"且"关系)</small></label>
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

                                <!-- 智能订阅建议 -->
                                {% if search_results.count > 0 %}
                                <div class="alert alert-light border mt-3">
                                    <h6><i class="fas fa-lightbulb text-warning"></i> 智能订阅建议</h6>

                                    {% set reached_limit = search_results.count >= search_results.max_searched %}
                                    {% set near_limit = search_results.count >= search_results.max_searched * 0.8 %}

                                    {% if reached_limit %}
                                        <p class="mb-2"><strong class="text-danger">文献数量达到搜索上限 ({{ search_results.count }}+篇/月)</strong></p>
                                        <p class="mb-2"><i class="fas fa-info-circle"></i> 实际文献数可能更多，强烈建议增加筛选条件:</p>
                                        <ul class="mb-0 small">
                                            {% if not search_results.jcr_filter or not search_results.jcr_filter.get('quartile') %}
                                            <li>添加 JCR Q1/Q2 分区限制</li>
                                            {% endif %}
                                            {% if not search_results.jcr_filter or not search_results.jcr_filter.get('min_if') %}
                                            <li>设置最小影响因子(如 IF≥3)</li>
                                            {% endif %}
                                            {% if not search_results.zky_filter or not search_results.zky_filter.get('top') %}
                                            <li>勾选"仅中科院Top期刊"</li>
                                            {% endif %}
                                            <li>或缩小关键词范围</li>
                                        </ul>

                                    {% elif near_limit %}
                                        <p class="mb-2"><strong class="text-warning">文献数量较多 ({{ search_results.count }}篇/月)</strong></p>
                                        <p class="mb-2">接近搜索上限，建议增加筛选条件以获得更精准的推送:</p>
                                        <ul class="mb-0 small">
                                            {% if not search_results.jcr_filter or not search_results.jcr_filter.get('quartile') %}
                                            <li>添加 JCR Q1/Q2 分区限制</li>
                                            {% endif %}
                                            {% if not search_results.jcr_filter or not search_results.jcr_filter.get('min_if') %}
                                            <li>设置最小影响因子</li>
                                            {% endif %}
                                            <li>或优化关键词以缩小范围</li>
                                        </ul>

                                    {% elif search_results.count >= 50 %}
                                        <p class="mb-1"><i class="fas fa-check-circle text-success"></i> 文献数量适中，建议 <strong class="text-success">每日推送</strong></p>
                                        <small class="text-muted">预计平均每天推送 {{ "%.1f"|format(search_results.count / 30) }} 篇文献</small>

                                    {% elif search_results.count >= 25 %}
                                        <p class="mb-1"><i class="fas fa-check-circle text-success"></i> 文献数量适中，建议 <strong class="text-success">每周推送</strong></p>
                                        <small class="text-muted">预计平均每周推送 {{ (search_results.count * 7 / 30)|round|int }} 篇文献</small>

                                    {% elif search_results.count >= 10 %}
                                        <p class="mb-1"><i class="fas fa-check-circle text-success"></i> 文献数量适中，建议 <strong class="text-success">每月推送</strong></p>
                                        <small class="text-muted">预计每月推送 {{ search_results.count }} 篇文献</small>

                                    {% elif search_results.count >= 3 %}
                                        <p class="mb-2"><i class="fas fa-exclamation-triangle text-warning"></i> <strong class="text-warning">文献数量偏少 ({{ search_results.count }}篇/月)</strong></p>
                                        <p class="mb-1">建议: <strong>每月推送</strong> 或优化搜索策略</p>
                                        <ul class="mb-0 small">
                                            {% if search_results.jcr_filter and search_results.jcr_filter.get('min_if') %}
                                            <li>降低影响因子要求(当前 IF≥{{ search_results.jcr_filter.min_if }})</li>
                                            {% endif %}
                                            {% if search_results.jcr_filter and search_results.jcr_filter.get('quartile') %}
                                            <li>扩大JCR分区范围(当前仅 {{ ', '.join(search_results.jcr_filter.quartile) }})</li>
                                            {% endif %}
                                            <li>扩展关键词范围</li>
                                        </ul>

                                    {% else %}
                                        <p class="mb-2"><i class="fas fa-exclamation-circle text-danger"></i> <strong class="text-danger">文献数量过少 ({{ search_results.count }}篇/月)</strong></p>
                                        <p class="mb-1">建议优化搜索策略:</p>
                                        <ul class="mb-0 small">
                                            <li>更换更通用的主题词</li>
                                            <li>移除所有筛选条件重新搜索</li>
                                            <li>考虑扩大研究领域范围</li>
                                        </ul>
                                    {% endif %}
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
        var searchFormSubmitting = false;
        function disableSearchButton(button) {
            // 防止重复点击
            if (searchFormSubmitting) {
                return false;
            }
            searchFormSubmitting = true;

            button.disabled = true;
            button.innerHTML = '<i class="fas fa-spinner fa-spin"></i> 搜索中...';

            // 避免禁用按钮导致表单无法提交
            setTimeout(function() {
                button.closest('form').submit();
            }, 100);

            return false;
        }

        // 测试订阅功能 - 自动填充和提交表单
        {% if test_subscription %}
        document.addEventListener('DOMContentLoaded', function() {
            var form = document.getElementById('searchForm');
            var subscription = {{ test_subscription|tojson }};

            // 填充关键词
            var keywordsInput = form.querySelector('input[name="keywords"]');
            if (keywordsInput) {
                keywordsInput.value = subscription.keywords;
            }

            // 填充ISSN筛选
            if (subscription.exclude_no_issn) {
                var excludeNoIssnCheckbox = form.querySelector('input[name="exclude_no_issn"]');
                if (excludeNoIssnCheckbox) {
                    excludeNoIssnCheckbox.checked = true;
                }
            }

            // 填充JCR分区
            if (subscription.jcr_quartiles) {
                try {
                    var jcrQuartiles = JSON.parse(subscription.jcr_quartiles);
                    jcrQuartiles.forEach(function(quartile) {
                        var checkbox = form.querySelector('input[name="jcr_quartile"][value="' + quartile.trim() + '"]');
                        if (checkbox) {
                            checkbox.checked = true;
                        }
                    });
                } catch(e) {
                    // 兼容旧格式：逗号分隔的字符串
                    var jcrQuartiles = subscription.jcr_quartiles.split(',');
                    jcrQuartiles.forEach(function(quartile) {
                        var checkbox = form.querySelector('input[name="jcr_quartile"][value="' + quartile.trim() + '"]');
                        if (checkbox) {
                            checkbox.checked = true;
                        }
                    });
                }
            }

            // 填充最小影响因子
            if (subscription.min_impact_factor) {
                var minIfInput = form.querySelector('input[name="min_if"]');
                if (minIfInput) {
                    minIfInput.value = subscription.min_impact_factor;
                }
            }

            // 填充中科院分区
            if (subscription.cas_categories) {
                try {
                    var casCategories = JSON.parse(subscription.cas_categories);
                    casCategories.forEach(function(category) {
                        var checkbox = form.querySelector('input[name="zky_category"][value="' + category.trim() + '"]');
                        if (checkbox) {
                            checkbox.checked = true;
                        }
                    });
                } catch(e) {
                    // 兼容旧格式：逗号分隔的字符串
                    var casCategories = subscription.cas_categories.split(',');
                    casCategories.forEach(function(category) {
                        var checkbox = form.querySelector('input[name="zky_category"][value="' + category.trim() + '"]');
                        if (checkbox) {
                            checkbox.checked = true;
                        }
                    });
                }
            }

            // 填充Top期刊筛选
            if (subscription.cas_top_only) {
                var topOnlyCheckbox = form.querySelector('input[name="zky_top_only"]');
                if (topOnlyCheckbox) {
                    topOnlyCheckbox.checked = true;
                }
            }

            // 自动提交表单（服务器端已通过session防止重复）
            setTimeout(function() {
                var submitButton = form.querySelector('button[type="submit"]');
                if (submitButton) {
                    submitButton.click();
                }
            }, 500);
        });
        {% endif %}

        // ========== 预设模板功能 ==========
        const FILTER_TEMPLATES = {
            'high_quality': {
                cas_partition: ['1'],
                jcr_quartile: ['Q1'],
                cas_top: true,
                exclude_no_issn: true
            },
            'medium_quality': {
                cas_partition: ['1', '2'],
                jcr_quartile: ['Q1', 'Q2'],
                exclude_no_issn: true
            },
            'high_impact': {
                cas_partition: ['1', '2'],
                jcr_quartile: ['Q1', 'Q2'],
                min_if: 5.0,
                exclude_no_issn: true
            },
            'top_journals_only': {
                cas_top: true,
                exclude_no_issn: true
            },
            'basic_quality': {
                cas_partition: ['1', '2', '3'],
                jcr_quartile: ['Q1', 'Q2', 'Q3'],
                exclude_no_issn: true
            }
        };

        function applyTemplate(templateName) {
            const template = FILTER_TEMPLATES[templateName];
            if (!template) return;

            const form = document.getElementById('searchForm');

            // 先清除所有筛选
            clearAllFilters();

            // 应用模板配置
            if (template.exclude_no_issn !== undefined) {
                const checkbox = form.querySelector('input[name="exclude_no_issn"]');
                if (checkbox) checkbox.checked = template.exclude_no_issn;
            }

            if (template.jcr_quartile) {
                template.jcr_quartile.forEach(quartile => {
                    const checkbox = form.querySelector(`input[name="jcr_quartile"][value="${quartile}"]`);
                    if (checkbox) checkbox.checked = true;
                });
            }

            if (template.min_if !== undefined) {
                const input = form.querySelector('input[name="min_if"]');
                if (input) input.value = template.min_if;
            }

            if (template.cas_partition) {
                template.cas_partition.forEach(category => {
                    const checkbox = form.querySelector(`input[name="zky_category"][value="${category}"]`);
                    if (checkbox) checkbox.checked = true;
                });
            }

            if (template.cas_top !== undefined) {
                const checkbox = form.querySelector('input[name="zky_top_only"]');
                if (checkbox) checkbox.checked = template.cas_top;
            }

            // 视觉反馈
            showToast('已应用模板配置');
        }

        function clearAllFilters() {
            const form = document.getElementById('searchForm');

            // 清除所有checkbox（除了关键词输入框）
            form.querySelectorAll('input[type="checkbox"]').forEach(cb => {
                if (cb.name !== 'exclude_no_issn') {
                    cb.checked = false;
                } else {
                    cb.checked = true; // 默认排除无ISSN
                }
            });

            // 清除影响因子
            const minIfInput = form.querySelector('input[name="min_if"]');
            if (minIfInput) minIfInput.value = '';

            showToast('已清除所有筛选条件');
        }

        function showToast(message) {
            // 简单的Toast提示
            const toast = document.createElement('div');
            toast.className = 'alert alert-success alert-dismissible fade show position-fixed';
            toast.style.cssText = 'top: 20px; right: 20px; z-index: 9999; min-width: 250px;';
            toast.innerHTML = `
                ${message}
                <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
            `;
            document.body.appendChild(toast);
            setTimeout(() => toast.remove(), 3000);
        }

        </script>
        <script src="https://cdnjs.cloudflare.com/ajax/libs/bootstrap/5.3.8/js/bootstrap.bundle.min.js"></script>
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
    # 检查是否启用邀请码注册
    require_invite = SystemSetting.get_setting('require_invite_code', 'false') == 'true'

    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        confirm_password = request.form.get('confirm_password')
        invite_code = request.form.get('invite_code', '').strip()

        # 验证密码
        if not password or len(password) < 6:
            flash('密码长度至少6位')
            return redirect(url_for('register'))

        if password != confirm_password:
            flash('两次输入的密码不一致')
            return redirect(url_for('register'))

        if User.query.filter_by(email=email).first():
            flash('邮箱已存在')
            return redirect(url_for('register'))

        # 如果启用邀请码,则验证邀请码
        if require_invite:
            if not invite_code:
                flash('请输入邀请码')
                return redirect(url_for('register'))

            code_obj = InviteCode.query.filter_by(code=invite_code).first()
            if not code_obj:
                flash('邀请码不存在')
                log_activity('WARNING', 'auth', f'注册失败 - 邀请码不存在: {invite_code}', None, request.remote_addr)
                return redirect(url_for('register'))

            if not code_obj.can_be_used():
                if code_obj.is_expired():
                    flash('邀请码已过期')
                elif code_obj.used_count >= code_obj.max_uses:
                    flash('邀请码已达到最大使用次数')
                else:
                    flash('邀请码无效')
                log_activity('WARNING', 'auth', f'注册失败 - 邀请码无效: {invite_code}', None, request.remote_addr)
                return redirect(url_for('register'))

        try:
            user = User(email=email)
            user.set_password(password)
            db.session.add(user)
            db.session.flush()  # 获取user.id

            # 如果使用了邀请码,记录使用记录
            if require_invite and code_obj:
                code_obj.mark_as_used()
                usage = InviteCodeUsage(
                    invite_code_id=code_obj.id,
                    user_id=user.id
                )
                db.session.add(usage)

            db.session.commit()

            log_activity('INFO', 'auth', f'用户注册成功: {email}', user.id, request.remote_addr)
            flash('注册成功！请登录')
            return redirect(url_for('login'))
        except Exception as e:
            db.session.rollback()
            log_activity('ERROR', 'auth', f'注册失败: {email} - {str(e)}', None, request.remote_addr)
            flash(f'注册失败: {str(e)}')
            return redirect(url_for('register'))

    template = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>注册 - PubMed Push</title>
        <meta charset="utf-8">
        <link href="https://cdnjs.cloudflare.com/ajax/libs/bootstrap/5.3.8/css/bootstrap.min.css" rel="stylesheet">
    </head>
    <body>
        <div class="container mt-5">
            <div class="row justify-content-center">
                <div class="col-md-6">
                    <div class="card">
                        <div class="card-header"><h4>用户注册</h4></div>
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
                                    <input type="password" class="form-control" id="password" name="password" required minlength="6">
                                    <div class="form-text">密码长度至少6位</div>
                                </div>
                                <div class="mb-3">
                                    <label for="confirm_password" class="form-label">确认密码</label>
                                    <input type="password" class="form-control" id="confirm_password" name="confirm_password" required minlength="6">
                                </div>
                                {% if require_invite %}
                                <div class="mb-3">
                                    <label for="invite_code" class="form-label">邀请码 <span class="text-danger">*</span></label>
                                    <input type="text" class="form-control" id="invite_code" name="invite_code" required placeholder="请输入邀请码">
                                    <div class="form-text">本站需要邀请码才能注册</div>
                                </div>
                                {% endif %}
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
    return render_template_string(template, require_invite=require_invite)

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
        <link href="https://cdnjs.cloudflare.com/ajax/libs/bootstrap/5.3.8/css/bootstrap.min.css" rel="stylesheet">
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
        <link href="https://cdnjs.cloudflare.com/ajax/libs/bootstrap/5.3.8/css/bootstrap.min.css" rel="stylesheet">
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
        <link href="https://cdnjs.cloudflare.com/ajax/libs/bootstrap/5.3.8/css/bootstrap.min.css" rel="stylesheet">
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
        flash(f'搜索功能已集成到主页', 'info')
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

        # 调度订阅推送任务
        try:
            from tasks import schedule_next_push_for_subscription
            schedule_next_push_for_subscription(subscription)
            app.logger.info(f"已为订阅 {subscription.id} 创建RQ调度任务")
        except Exception as e:
            app.logger.warning(f"为订阅 {subscription.id} 创建RQ调度任务失败: {e}")

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
        flash(f'已取消订阅关键词: {keywords}', 'success')
    else:
        flash('您没有订阅此关键词', 'warning')

    # 重定向到订阅列表页
    return redirect(url_for('subscriptions'))

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
        <link href="https://cdnjs.cloudflare.com/ajax/libs/bootstrap/5.3.8/css/bootstrap.min.css" rel="stylesheet">
        <link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/7.1.0/css/all.min.css" rel="stylesheet">
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
        
        <script src="https://cdnjs.cloudflare.com/ajax/libs/bootstrap/5.3.8/js/bootstrap.bundle.min.js"></script>
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

        # 取消RQ Scheduler中的调度任务
        try:
            from rq_config import cancel_subscription_jobs
            cancel_subscription_jobs(sub_id)
            app.logger.info(f"已取消订阅 {sub_id} 的RQ调度任务")
        except Exception as e:
            app.logger.warning(f"取消订阅 {sub_id} 的RQ调度任务失败: {e}")

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
        # 直接重定向到主页,并通过URL参数传递订阅信息
        from urllib.parse import urlencode
        params = {
            'test_subscription_id': subscription.id,
            'keywords': subscription.keywords
        }
        return redirect(url_for('index') + '?' + urlencode(params))

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
        <link href="https://cdnjs.cloudflare.com/ajax/libs/bootstrap/5.3.8/css/bootstrap.min.css" rel="stylesheet">
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
                            <a href="/admin/invite-codes" class="btn btn-info me-2">邀请码管理</a>
                            <a href="/admin/push" class="btn btn-warning me-2">推送管理</a>
                            <a href="/admin/mail" class="btn btn-info me-2">邮箱管理</a>
                            <a href="/admin/cache" class="btn btn-info me-2">L1缓存管理</a>
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
        confirm_password = request.form.get('confirm_password', '')
        is_admin = request.form.get('is_admin') == 'on'

        # 验证输入
        if not email or not password:
            flash('邮箱和密码不能为空', 'error')
            return redirect(url_for('admin_add_user'))

        if len(password) < 6:
            flash('密码长度至少6位', 'error')
            return redirect(url_for('admin_add_user'))

        if password != confirm_password:
            flash('两次输入的密码不一致', 'error')
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
        <link href="https://cdnjs.cloudflare.com/ajax/libs/bootstrap/5.3.8/css/bootstrap.min.css" rel="stylesheet">
        <link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/7.1.0/css/all.min.css" rel="stylesheet">
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
                                    <label for="confirm_password" class="form-label">
                                        <i class="fas fa-lock"></i> 确认密码 *
                                    </label>
                                    <input type="password" class="form-control" id="confirm_password" name="confirm_password" required minlength="6">
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
        
        <script src="https://cdnjs.cloudflare.com/ajax/libs/bootstrap/5.3.8/js/bootstrap.bundle.min.js"></script>
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
        <link href="https://cdnjs.cloudflare.com/ajax/libs/bootstrap/5.3.8/css/bootstrap.min.css" rel="stylesheet">
        <link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/7.1.0/css/all.min.css" rel="stylesheet">
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
            <link href="https://cdnjs.cloudflare.com/ajax/libs/bootstrap/5.3.8/css/bootstrap.min.css" rel="stylesheet">
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
            <link href="https://cdnjs.cloudflare.com/ajax/libs/bootstrap/5.3.8/css/bootstrap.min.css" rel="stylesheet">
            <link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/7.1.0/css/all.min.css" rel="stylesheet">
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
            
            <script src="https://cdnjs.cloudflare.com/ajax/libs/bootstrap/5.3.8/js/bootstrap.bundle.min.js"></script>
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
            <link href="https://cdnjs.cloudflare.com/ajax/libs/bootstrap/5.3.8/css/bootstrap.min.css" rel="stylesheet">
            <link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/7.1.0/css/all.min.css" rel="stylesheet">
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
            
            <script src="https://cdnjs.cloudflare.com/ajax/libs/bootstrap/5.3.8/js/bootstrap.bundle.min.js"></script>
        </body>
        </html>
        """
        
        return render_template_string(template, target_user=target_user, current_subscriptions=current_subscriptions)
        
    except Exception as e:
        log_activity('ERROR', 'admin', f'管理员 {current_user.email} 设置用户订阅权限失败: {str(e)}', current_user.id, request.remote_addr)
        flash(f'设置订阅权限失败: {str(e)}', 'admin')
        return redirect(url_for('admin_users'))

# ==================== 邀请码管理路由 ====================
@app.route('/admin/invite-codes')
@admin_required
def admin_invite_codes():
    """邀请码管理页面"""
    invite_codes = InviteCode.query.order_by(InviteCode.created_at.desc()).all()

    # 统计信息
    stats = {
        'total': len(invite_codes),
        'active': len([c for c in invite_codes if c.can_be_used()]),
        'used': len([c for c in invite_codes if c.used_count > 0]),
        'expired': len([c for c in invite_codes if c.is_expired()])
    }

    template = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>邀请码管理 - 管理后台</title>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <link href="https://cdnjs.cloudflare.com/ajax/libs/bootstrap/5.3.8/css/bootstrap.min.css" rel="stylesheet">
        <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/7.1.0/css/all.min.css">
    </head>
    <body>
        <!-- 导航栏 -->
        <nav class="navbar navbar-expand-lg navbar-dark bg-primary">
            <div class="container-fluid">
                <a class="navbar-brand" href="/admin">
                    <i class="fas fa-user-shield"></i> PubMed Push - 管理后台
                </a>
                <button class="navbar-toggler" type="button" data-bs-toggle="collapse" data-bs-target="#navbarNav">
                    <span class="navbar-toggler-icon"></span>
                </button>
                <div class="collapse navbar-collapse" id="navbarNav">
                    <ul class="navbar-nav ms-auto">
                        <li class="nav-item">
                            <a class="nav-link" href="/">
                                <i class="fas fa-home"></i> 返回首页
                            </a>
                        </li>
                        <li class="nav-item">
                            <a class="nav-link" href="/logout">退出 ({{current_user.email}})</a>
                        </li>
                    </ul>
                </div>
            </div>
        </nav>

        <div class="container mt-4">
                    <nav aria-label="breadcrumb">
                        <ol class="breadcrumb">
                            <li class="breadcrumb-item"><a href="/admin">管理后台</a></li>
                            <li class="breadcrumb-item active">邀请码管理</li>
                        </ol>
                    </nav>

                    <div class="d-flex justify-content-between align-items-center mb-3">
                        <h2><i class="fas fa-ticket-alt"></i> 邀请码管理</h2>
                        <a href="/admin/invite-codes/create" class="btn btn-primary">
                            <i class="fas fa-plus"></i> 生成邀请码
                        </a>
                    </div>

                    {% with messages = get_flashed_messages(category_filter=['admin']) %}
                        {% if messages %}
                            {% for message in messages %}
                                <div class="alert alert-info alert-dismissible fade show">
                                    {{ message }}
                                    <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
                                </div>
                            {% endfor %}
                        {% endif %}
                    {% endwith %}

                    <!-- 统计卡片 -->
                    <div class="row mb-4">
                        <div class="col-md-3">
                            <div class="card bg-primary text-white">
                                <div class="card-body">
                                    <h6 class="card-title">总计</h6>
                                    <h3>{{ stats.total }}</h3>
                                </div>
                            </div>
                        </div>
                        <div class="col-md-3">
                            <div class="card bg-success text-white">
                                <div class="card-body">
                                    <h6 class="card-title">可用</h6>
                                    <h3>{{ stats.active }}</h3>
                                </div>
                            </div>
                        </div>
                        <div class="col-md-3">
                            <div class="card bg-warning text-white">
                                <div class="card-body">
                                    <h6 class="card-title">已使用</h6>
                                    <h3>{{ stats.used }}</h3>
                                </div>
                            </div>
                        </div>
                        <div class="col-md-3">
                            <div class="card bg-danger text-white">
                                <div class="card-body">
                                    <h6 class="card-title">已过期</h6>
                                    <h3>{{ stats.expired }}</h3>
                                </div>
                            </div>
                        </div>
                    </div>

                    <!-- 邀请码列表 -->
                    <div class="card">
                        <div class="card-header">
                            <h5><i class="fas fa-list"></i> 邀请码列表</h5>
                        </div>
                        <div class="card-body">
                            <div class="table-responsive">
                                <table class="table table-hover">
                                    <thead>
                                        <tr>
                                            <th>邀请码</th>
                                            <th>创建者</th>
                                            <th>创建时间</th>
                                            <th>过期时间</th>
                                            <th>使用情况</th>
                                            <th>状态</th>
                                            <th>操作</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {% for code in invite_codes %}
                                        <tr>
                                            <td><code>{{ code.code }}</code></td>
                                            <td>{{ code.creator.email }}</td>
                                            <td>{{ code.created_at.strftime('%Y-%m-%d %H:%M') if code.created_at else 'N/A' }}</td>
                                            <td>{{ code.expires_at.strftime('%Y-%m-%d %H:%M') if code.expires_at else '永久' }}</td>
                                            <td>{{ code.used_count }}/{{ code.max_uses }}</td>
                                            <td>
                                                {% if code.can_be_used() %}
                                                    <span class="badge bg-success">可用</span>
                                                {% elif code.is_expired() %}
                                                    <span class="badge bg-danger">已过期</span>
                                                {% elif code.used_count >= code.max_uses %}
                                                    <span class="badge bg-warning">已用完</span>
                                                {% else %}
                                                    <span class="badge bg-secondary">已禁用</span>
                                                {% endif %}
                                            </td>
                                            <td>
                                                <a href="/admin/invite-codes/{{ code.id }}/usage" class="btn btn-sm btn-info" title="查看使用记录">
                                                    <i class="fas fa-history"></i>
                                                </a>
                                                {% if code.is_active %}
                                                <a href="/admin/invite-codes/{{ code.id }}/disable" class="btn btn-sm btn-warning"
                                                   onclick="return confirm('确定要禁用此邀请码吗？')" title="禁用">
                                                    <i class="fas fa-ban"></i>
                                                </a>
                                                {% else %}
                                                <a href="/admin/invite-codes/{{ code.id }}/enable" class="btn btn-sm btn-success" title="启用">
                                                    <i class="fas fa-check"></i>
                                                </a>
                                                {% endif %}
                                                <a href="/admin/invite-codes/{{ code.id }}/delete" class="btn btn-sm btn-danger"
                                                   onclick="return confirm('确定要删除此邀请码吗？删除后无法恢复！')" title="删除">
                                                    <i class="fas fa-trash"></i>
                                                </a>
                                            </td>
                                        </tr>
                                        {% endfor %}
                                    </tbody>
                                </table>
                            </div>
                        </div>
                    </div>
        </div>

        <script src="https://cdnjs.cloudflare.com/ajax/libs/bootstrap/5.3.8/js/bootstrap.bundle.min.js"></script>
    </body>
    </html>
    """
    return render_template_string(template, invite_codes=invite_codes, stats=stats)

@app.route('/admin/invite-codes/create', methods=['GET', 'POST'])
@admin_required
def admin_create_invite_code():
    """生成邀请码"""
    if request.method == 'POST':
        try:
            import secrets
            from datetime import timedelta

            max_uses = int(request.form.get('max_uses', 1))
            expire_days = request.form.get('expire_days', '')

            # 生成邀请码
            code = secrets.token_urlsafe(12)

            # 计算过期时间
            expires_at = None
            if expire_days and int(expire_days) > 0:
                expires_at = beijing_now() + timedelta(days=int(expire_days))

            invite_code = InviteCode(
                code=code,
                created_by=current_user.id,
                max_uses=max_uses,
                expires_at=expires_at
            )
            db.session.add(invite_code)
            db.session.commit()

            log_activity('INFO', 'admin', f'管理员 {current_user.email} 创建邀请码: {code}', current_user.id, request.remote_addr)
            flash(f'邀请码创建成功: {code}', 'admin')
            return redirect(url_for('admin_invite_codes'))

        except Exception as e:
            db.session.rollback()
            log_activity('ERROR', 'admin', f'创建邀请码失败: {str(e)}', current_user.id, request.remote_addr)
            flash(f'创建邀请码失败: {str(e)}', 'admin')

    template = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>生成邀请码 - 管理后台</title>
        <meta charset="utf-8">
        <link href="https://cdnjs.cloudflare.com/ajax/libs/bootstrap/5.3.8/css/bootstrap.min.css" rel="stylesheet">
        <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/7.1.0/css/all.min.css">
    </head>
    <body>
        <nav class="navbar navbar-expand-lg navbar-dark bg-primary">
            <div class="container-fluid">
                <a class="navbar-brand" href="/admin">
                    <i class="fas fa-user-shield"></i> PubMed Push - 管理后台
                </a>
            </div>
        </nav>

        <div class="container mt-5">
            <div class="row justify-content-center">
                <div class="col-md-6">
                    <div class="card">
                        <div class="card-header">
                            <h4><i class="fas fa-plus"></i> 生成邀请码</h4>
                        </div>
                        <div class="card-body">
                            <form method="POST">
                                <div class="mb-3">
                                    <label for="max_uses" class="form-label">最大使用次数</label>
                                    <input type="number" class="form-control" id="max_uses" name="max_uses" value="1" min="1" required>
                                    <div class="form-text">此邀请码可被使用的最大次数</div>
                                </div>
                                <div class="mb-3">
                                    <label for="expire_days" class="form-label">有效天数</label>
                                    <input type="number" class="form-control" id="expire_days" name="expire_days" placeholder="留空表示永久有效" min="1">
                                    <div class="form-text">留空表示永久有效</div>
                                </div>
                                <div class="d-grid gap-2">
                                    <button type="submit" class="btn btn-primary">
                                        <i class="fas fa-check"></i> 生成邀请码
                                    </button>
                                    <a href="/admin/invite-codes" class="btn btn-secondary">
                                        <i class="fas fa-arrow-left"></i> 返回列表
                                    </a>
                                </div>
                            </form>
                        </div>
                    </div>
                </div>
            </div>
        </div>

        <script src="https://cdnjs.cloudflare.com/ajax/libs/bootstrap/5.3.8/js/bootstrap.bundle.min.js"></script>
    </body>
    </html>
    """
    return render_template_string(template)

@app.route('/admin/invite-codes/<int:code_id>/usage')
@admin_required
def admin_invite_code_usage(code_id):
    """查看邀请码使用记录"""
    invite_code = InviteCode.query.get_or_404(code_id)
    usage_records = InviteCodeUsage.query.filter_by(invite_code_id=code_id).order_by(InviteCodeUsage.used_at.desc()).all()

    template = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>邀请码使用记录 - 管理后台</title>
        <meta charset="utf-8">
        <link href="https://cdnjs.cloudflare.com/ajax/libs/bootstrap/5.3.8/css/bootstrap.min.css" rel="stylesheet">
        <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/7.1.0/css/all.min.css">
    </head>
    <body>
        <nav class="navbar navbar-expand-lg navbar-dark bg-primary">
            <div class="container-fluid">
                <a class="navbar-brand" href="/admin">
                    <i class="fas fa-user-shield"></i> PubMed Push - 管理后台
                </a>
            </div>
        </nav>

        <div class="container mt-5">
            <div class="row justify-content-center">
                <div class="col-md-8">
                    <div class="card">
                        <div class="card-header">
                            <h4><i class="fas fa-history"></i> 邀请码使用记录</h4>
                        </div>
                        <div class="card-body">
                            <dl class="row">
                                <dt class="col-sm-3">邀请码:</dt>
                                <dd class="col-sm-9"><code>{{ invite_code.code }}</code></dd>

                                <dt class="col-sm-3">创建者:</dt>
                                <dd class="col-sm-9">{{ invite_code.creator.email }}</dd>

                                <dt class="col-sm-3">使用情况:</dt>
                                <dd class="col-sm-9">{{ invite_code.used_count }}/{{ invite_code.max_uses }}</dd>
                            </dl>

                            <h5 class="mt-4">使用记录</h5>
                            {% if usage_records %}
                            <div class="table-responsive">
                                <table class="table table-hover">
                                    <thead>
                                        <tr>
                                            <th>用户邮箱</th>
                                            <th>使用时间</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {% for record in usage_records %}
                                        <tr>
                                            <td>{{ record.user.email }}</td>
                                            <td>{{ record.used_at.strftime('%Y-%m-%d %H:%M:%S') if record.used_at else 'N/A' }}</td>
                                        </tr>
                                        {% endfor %}
                                    </tbody>
                                </table>
                            </div>
                            {% else %}
                            <p class="text-muted">暂无使用记录</p>
                            {% endif %}

                            <div class="mt-3">
                                <a href="/admin/invite-codes" class="btn btn-secondary">
                                    <i class="fas fa-arrow-left"></i> 返回列表
                                </a>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </div>

        <script src="https://cdnjs.cloudflare.com/ajax/libs/bootstrap/5.3.8/js/bootstrap.bundle.min.js"></script>
    </body>
    </html>
    """
    return render_template_string(template, invite_code=invite_code, usage_records=usage_records)

@app.route('/admin/invite-codes/<int:code_id>/disable')
@admin_required
def admin_disable_invite_code(code_id):
    """禁用邀请码"""
    try:
        invite_code = InviteCode.query.get_or_404(code_id)
        invite_code.is_active = False
        db.session.commit()

        log_activity('INFO', 'admin', f'管理员 {current_user.email} 禁用邀请码: {invite_code.code}', current_user.id, request.remote_addr)
        flash(f'邀请码已禁用', 'admin')
    except Exception as e:
        db.session.rollback()
        log_activity('ERROR', 'admin', f'禁用邀请码失败: {str(e)}', current_user.id, request.remote_addr)
        flash(f'禁用失败: {str(e)}', 'admin')

    return redirect(url_for('admin_invite_codes'))

@app.route('/admin/invite-codes/<int:code_id>/enable')
@admin_required
def admin_enable_invite_code(code_id):
    """启用邀请码"""
    try:
        invite_code = InviteCode.query.get_or_404(code_id)
        invite_code.is_active = True
        db.session.commit()

        log_activity('INFO', 'admin', f'管理员 {current_user.email} 启用邀请码: {invite_code.code}', current_user.id, request.remote_addr)
        flash(f'邀请码已启用', 'admin')
    except Exception as e:
        db.session.rollback()
        log_activity('ERROR', 'admin', f'启用邀请码失败: {str(e)}', current_user.id, request.remote_addr)
        flash(f'启用失败: {str(e)}', 'admin')

    return redirect(url_for('admin_invite_codes'))

@app.route('/admin/invite-codes/<int:code_id>/delete')
@admin_required
def admin_delete_invite_code(code_id):
    """删除邀请码"""
    try:
        invite_code = InviteCode.query.get_or_404(code_id)
        code_str = invite_code.code

        # 先删除相关的使用记录
        InviteCodeUsage.query.filter_by(invite_code_id=code_id).delete()

        # 删除邀请码
        db.session.delete(invite_code)
        db.session.commit()

        log_activity('INFO', 'admin', f'管理员 {current_user.email} 删除邀请码: {code_str}', current_user.id, request.remote_addr)
        flash(f'邀请码已删除', 'admin')
    except Exception as e:
        db.session.rollback()
        log_activity('ERROR', 'admin', f'删除邀请码失败: {str(e)}', current_user.id, request.remote_addr)
        flash(f'删除失败: {str(e)}', 'admin')

    return redirect(url_for('admin_invite_codes'))

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
        <link href="https://cdnjs.cloudflare.com/ajax/libs/bootstrap/5.3.8/css/bootstrap.min.css" rel="stylesheet">
        <link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.7.2/css/all.min.css" rel="stylesheet">
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
            <h2><i class="fas fa-rss"></i> 订阅管理</h2>
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
                        <table class="table table-hover">
                            <thead>
                                <tr>
                                    <th>ID</th>
                                    <th>关键词</th>
                                    <th>用户邮箱</th>
                                    <th>创建时间</th>
                                    <th style="width: 200px;">操作</th>
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
                                        <a href="/admin/subscriptions/{{ sub.id }}/copy"
                                           class="btn btn-sm btn-success"
                                           title="追加给其他用户">
                                            <i class="fas fa-copy"></i> 追加
                                        </a>
                                        <button class="btn btn-sm btn-danger"
                                                onclick="if(confirm('确定删除此订阅吗？')) location.href='/admin/subscriptions/{{ sub.id }}/delete'"
                                                title="删除订阅">
                                            <i class="fas fa-trash"></i> 删除
                                        </button>
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
                <a href="/admin" class="btn btn-secondary">
                    <i class="fas fa-arrow-left"></i> 返回仪表板
                </a>
            </div>
        </div>
        <script src="https://cdnjs.cloudflare.com/ajax/libs/bootstrap/5.3.8/js/bootstrap.bundle.min.js"></script>
    </body>
    </html>
    """
    return render_template_string(template, subscriptions=subscriptions)

@app.route('/admin/subscriptions/<int:sub_id>/delete')
@admin_required
def admin_delete_subscription(sub_id):
    """管理员删除订阅"""
    try:
        # 取消RQ Scheduler中的调度任务
        try:
            from rq_config import cancel_subscription_jobs
            cancel_subscription_jobs(sub_id)
            app.logger.info(f"[管理员] 已取消订阅 {sub_id} 的RQ调度任务")
        except Exception as e:
            app.logger.warning(f"[管理员] 取消订阅 {sub_id} 的RQ调度任务失败: {e}")

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

@app.route('/admin/subscriptions/<int:sub_id>/copy', methods=['GET', 'POST'])
@admin_required
def admin_copy_subscription(sub_id):
    """管理员追加订阅给其他用户"""
    if request.method == 'GET':
        # 获取原始订阅信息
        original_sub = Subscription.query.get_or_404(sub_id)

        # 获取所有用户（排除原订阅用户）
        all_users = User.query.filter(User.id != original_sub.user_id).order_by(User.email).all()

        template = """
        <!DOCTYPE html>
        <html>
        <head>
            <title>追加订阅给其他用户 - PubMed Literature Push</title>
            <link href="https://cdnjs.cloudflare.com/ajax/libs/bootstrap/5.3.8/css/bootstrap.min.css" rel="stylesheet">
            <link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.7.2/css/all.min.css" rel="stylesheet">
            <style>
                .user-checkbox {
                    padding: 10px;
                    margin: 5px 0;
                    border: 1px solid #e0e0e0;
                    border-radius: 5px;
                    transition: background-color 0.2s;
                }
                .user-checkbox:hover {
                    background-color: #f8f9fa;
                }
                .user-checkbox input[type="checkbox"] {
                    margin-right: 10px;
                }
                .subscription-detail {
                    background-color: #f8f9fa;
                    padding: 15px;
                    border-radius: 5px;
                    margin-bottom: 20px;
                }
                .search-box {
                    margin-bottom: 15px;
                }
            </style>
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
                <h2><i class="fas fa-copy"></i> 追加订阅给其他用户</h2>
                <p class="text-muted">将订阅复制给其他用户，他们将获得相同的订阅配置</p>

                <!-- 订阅详情 -->
                <div class="subscription-detail">
                    <h5><i class="fas fa-info-circle"></i> 订阅详情</h5>
                    <p><strong>订阅ID:</strong> {{ sub.id }}</p>
                    <p><strong>关键词:</strong> <span class="badge bg-primary">{{ sub.keywords }}</span></p>
                    <p><strong>当前用户:</strong> {{ sub.user.email }}</p>
                    <p><strong>创建时间:</strong> {{ sub.created_at.strftime('%Y-%m-%d %H:%M') }}</p>
                    <p><strong>推送频率:</strong>
                        {% if sub.push_frequency == 'daily' %}每日
                        {% elif sub.push_frequency == 'weekly' %}每周
                        {% elif sub.push_frequency == 'monthly' %}每月
                        {% else %}{{ sub.push_frequency }}{% endif %}
                    </p>
                </div>

                <!-- 用户选择表单 -->
                <form method="POST" id="copyForm">
                    <div class="card">
                        <div class="card-header">
                            <h5><i class="fas fa-users"></i> 选择目标用户</h5>
                        </div>
                        <div class="card-body">
                            <!-- 搜索框 -->
                            <div class="search-box">
                                <input type="text" id="userSearch" class="form-control" placeholder="搜索用户邮箱...">
                            </div>

                            <!-- 全选 -->
                            <div class="mb-3">
                                <label class="user-checkbox">
                                    <input type="checkbox" id="selectAll">
                                    <strong>全选/取消全选</strong>
                                </label>
                            </div>

                            <!-- 用户列表 -->
                            <div id="userList">
                                {% if users %}
                                    {% for user in users %}
                                    <label class="user-checkbox user-item" data-email="{{ user.email }}">
                                        <input type="checkbox" name="user_ids" value="{{ user.id }}">
                                        {{ user.email }}
                                        <span class="text-muted">(ID: {{ user.id }})</span>
                                    </label>
                                    {% endfor %}
                                {% else %}
                                    <p class="text-muted text-center">没有其他用户可以选择</p>
                                {% endif %}
                            </div>
                        </div>
                    </div>

                    <div class="mt-3">
                        <button type="submit" class="btn btn-primary" id="submitBtn">
                            <i class="fas fa-copy"></i> 追加订阅
                        </button>
                        <a href="/admin/subscriptions" class="btn btn-secondary">取消</a>
                    </div>
                </form>
            </div>

            <script src="https://cdnjs.cloudflare.com/ajax/libs/bootstrap/5.3.8/js/bootstrap.bundle.min.js"></script>
            <script>
                // 搜索功能
                document.getElementById('userSearch').addEventListener('input', function() {
                    const searchText = this.value.toLowerCase();
                    const userItems = document.querySelectorAll('.user-item');

                    userItems.forEach(item => {
                        const email = item.getAttribute('data-email').toLowerCase();
                        if (email.includes(searchText)) {
                            item.style.display = '';
                        } else {
                            item.style.display = 'none';
                        }
                    });
                });

                // 全选功能
                document.getElementById('selectAll').addEventListener('change', function() {
                    const checkboxes = document.querySelectorAll('.user-item input[type="checkbox"]');
                    const visibleCheckboxes = Array.from(checkboxes).filter(cb =>
                        cb.closest('.user-item').style.display !== 'none'
                    );

                    visibleCheckboxes.forEach(cb => {
                        cb.checked = this.checked;
                    });
                });

                // 表单提交验证
                document.getElementById('copyForm').addEventListener('submit', function(e) {
                    const checkedBoxes = document.querySelectorAll('.user-item input[type="checkbox"]:checked');
                    if (checkedBoxes.length === 0) {
                        e.preventDefault();
                        alert('请至少选择一个用户');
                        return false;
                    }

                    const submitBtn = document.getElementById('submitBtn');
                    submitBtn.disabled = true;
                    submitBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> 处理中...';
                });
            </script>
        </body>
        </html>
        """
        return render_template_string(template, sub=original_sub, users=all_users)

    elif request.method == 'POST':
        # 处理追加订阅
        try:
            user_ids = request.form.getlist('user_ids')
            if not user_ids:
                flash('请至少选择一个用户', 'admin')
                return redirect(url_for('admin_copy_subscription', sub_id=sub_id))

            # 获取原始订阅
            original_sub = Subscription.query.get_or_404(sub_id)

            success_count = 0
            skip_count = 0
            error_users = []

            for user_id in user_ids:
                try:
                    user_id = int(user_id)
                    user = User.query.get(user_id)
                    if not user:
                        continue

                    # 检查用户是否已有相同关键词的订阅
                    existing = Subscription.query.filter_by(
                        user_id=user_id,
                        keywords=original_sub.keywords
                    ).first()

                    if existing:
                        skip_count += 1
                        continue

                    # 创建新订阅（复制所有配置）
                    new_sub = Subscription(
                        user_id=user_id,
                        keywords=original_sub.keywords,
                        is_active=original_sub.is_active,
                        max_results=original_sub.max_results,
                        days_back=original_sub.days_back,
                        exclude_no_issn=original_sub.exclude_no_issn,
                        jcr_quartiles=original_sub.jcr_quartiles,
                        min_impact_factor=original_sub.min_impact_factor,
                        cas_categories=original_sub.cas_categories,
                        cas_top_only=original_sub.cas_top_only,
                        filter_config=original_sub.filter_config,
                        use_advanced_filter=original_sub.use_advanced_filter,
                        push_frequency=original_sub.push_frequency,
                        push_time=original_sub.push_time,
                        push_day=original_sub.push_day,
                        push_month_day=original_sub.push_month_day
                    )
                    db.session.add(new_sub)
                    db.session.flush()

                    # 为新订阅创建RQ调度任务
                    try:
                        from tasks import calculate_next_push_time
                        from rq_config import schedule_subscription_push

                        next_push_time = calculate_next_push_time(new_sub)
                        if next_push_time:
                            schedule_subscription_push(new_sub.id, next_push_time)
                            app.logger.info(f"[管理员] 为用户 {user.email} 创建订阅调度任务: {new_sub.id}, 下次推送: {next_push_time}")
                        else:
                            app.logger.warning(f"[管理员] 无法计算订阅 {new_sub.id} 的下次推送时间")
                    except Exception as e:
                        app.logger.warning(f"[管理员] 创建调度任务失败: {e}")

                    success_count += 1

                except Exception as e:
                    error_users.append(f"用户ID {user_id}: {str(e)}")
                    continue

            db.session.commit()

            # 记录日志
            log_activity(
                'INFO', 'admin',
                f'管理员 {current_user.email} 追加订阅 {sub_id} 给 {success_count} 个用户',
                current_user.id, request.remote_addr
            )

            # 显示结果
            if success_count > 0:
                flash(f'成功追加订阅给 {success_count} 个用户', 'admin')
            if skip_count > 0:
                flash(f'{skip_count} 个用户已有相同订阅，已跳过', 'admin')
            if error_users:
                flash(f'部分用户追加失败: {"; ".join(error_users[:3])}', 'admin')

        except Exception as e:
            db.session.rollback()
            flash(f'追加失败: {str(e)}', 'admin')
            log_activity(
                'ERROR', 'admin',
                f'管理员 {current_user.email} 追加订阅 {sub_id} 失败: {str(e)}',
                current_user.id, request.remote_addr
            )

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
        <link href="https://cdnjs.cloudflare.com/ajax/libs/bootstrap/5.3.8/css/bootstrap.min.css" rel="stylesheet">
        <link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/7.1.0/css/all.min.css" rel="stylesheet">
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
        <script src="https://cdnjs.cloudflare.com/ajax/libs/bootstrap/5.3.8/js/bootstrap.bundle.min.js"></script>
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
        <link href="https://cdnjs.cloudflare.com/ajax/libs/bootstrap/5.3.8/css/bootstrap.min.css" rel="stylesheet">
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
    """推送管理页面 - RQ版本"""
    # 检查RQ调度器状态
    def check_rq_scheduler_status():
        """检查RQ调度器状态"""
        try:
            # 检查Redis连接
            redis_conn.ping()
            
            # 获取队列信息
            queue_info = get_queue_info()
            
            # 获取失败任务
            failed_jobs = get_failed_jobs()
            
            return {
                "redis_connected": True,
                "queue_info": queue_info,
                "failed_jobs_count": len(failed_jobs),
                "status": "running"
            }
        except Exception as e:
            return {
                "redis_connected": False,
                "error": str(e),
                "status": "error"
            }
    
    # 检查传统调度器状态（降级模式）
    def check_scheduler_running():
        """跨进程检查调度器是否真正运行"""
        import time
        import json
        
        # 首先检查本进程调度器状态
        if scheduler.running:
            return True
            
        # 检查锁文件状态
        lock_file_path = '/app/data/scheduler.lock'
        if not os.path.exists(lock_file_path):
            return False
            
        try:
            with open(lock_file_path, 'r') as f:
                lock_data = json.loads(f.read())
            
            last_heartbeat = lock_data.get('last_heartbeat', 0)
            current_time = time.time()
            heartbeat_age = current_time - last_heartbeat
            
            # 如果心跳在2分钟内，认为调度器运行中
            return heartbeat_age <= 120
        except:
            return False
    
    # 获取RQ调度器状态
    rq_status = check_rq_scheduler_status()
    
    # 使用跨进程状态检查（降级模式）
    scheduler_running = check_scheduler_running()
    
    # 构建状态信息
    if rq_status["status"] == "running":
        # RQ调度器运行中
        scheduler_status = {
            'mode': 'rq',
            'running': True,
            'redis_connected': rq_status['redis_connected'],
            'queue_info': rq_status['queue_info'],
            'failed_jobs_count': rq_status['failed_jobs_count'],
            'timezone': SYSTEM_TIMEZONE,
            'current_time': get_current_time().strftime('%Y-%m-%d %H:%M:%S %Z'),
            'next_run': f'动态调度中 ({rq_status["queue_info"]["total_scheduled"]} 个待执行任务)'
        }
    else:
        # 降级到APScheduler模式
        scheduler_status = {
            'mode': 'apscheduler',
            'running': scheduler_running,
            'jobs': len(scheduler.get_jobs()) if scheduler_running and scheduler.running else 0,
            'timezone': SYSTEM_TIMEZONE,
            'current_time': get_current_time().strftime('%Y-%m-%d %H:%M:%S %Z'),
            'rq_error': rq_status.get('error', 'Unknown')
        }
        
        # 获取下次执行时间（降级模式）
        if scheduler_running and scheduler.running:
            # 本进程调度器运行中，可以获取详细信息
            jobs = scheduler.get_jobs()
            if jobs:
                next_run_time = jobs[0].next_run_time
                if next_run_time:
                    # 确保时间显示使用应用程序时区
                    if next_run_time.tzinfo is None:
                        next_run_time = APP_TIMEZONE.localize(next_run_time)
                    elif next_run_time.tzinfo != APP_TIMEZONE:
                        next_run_time = next_run_time.astimezone(APP_TIMEZONE)
                    
                    # 自动检测时间异常：下次执行时间是否在过去
                    current_time = get_current_time()
                    if next_run_time < current_time:
                        app.logger.warning(f"[调度器自检] 检测到时间异常：下次执行时间 {next_run_time} 早于当前时间 {current_time}")
                        try:
                            # 自动重启调度器修复问题
                            app.logger.info("[调度器自检] 开始自动重启调度器")
                            shutdown_scheduler_safely()
                            init_scheduler()
                            
                            if scheduler.running:
                                app.logger.info("[调度器自检] 自动重启成功")
                                # 重新获取修复后的时间
                                updated_jobs = scheduler.get_jobs()
                                if updated_jobs:
                                    updated_next_run = updated_jobs[0].next_run_time
                                    if updated_next_run:
                                        if updated_next_run.tzinfo is None:
                                            updated_next_run = APP_TIMEZONE.localize(updated_next_run)
                                        elif updated_next_run.tzinfo != APP_TIMEZONE:
                                            updated_next_run = updated_next_run.astimezone(APP_TIMEZONE)
                                        scheduler_status['next_run'] = updated_next_run.strftime('%Y-%m-%d %H:%M:%S')
                                        scheduler_status['auto_fixed'] = True
                                    else:
                                        scheduler_status['next_run'] = '未知'
                                else:
                                    scheduler_status['next_run'] = '无任务'
                            else:
                                app.logger.error("[调度器自检] 自动重启失败")
                                scheduler_status['next_run'] = next_run_time.strftime('%Y-%m-%d %H:%M:%S') + ' (异常)'
                        except Exception as e:
                            app.logger.error(f"[调度器自检] 自动修复失败: {e}")
                            scheduler_status['next_run'] = next_run_time.strftime('%Y-%m-%d %H:%M:%S') + ' (异常)'
                    else:
                        scheduler_status['next_run'] = next_run_time.strftime('%Y-%m-%d %H:%M:%S')
                else:
                    scheduler_status['next_run'] = '未知'
            else:
                scheduler_status['next_run'] = '无任务'
        elif scheduler_running:
            # 跨进程检测到有调度器运行，但本进程调度器未运行
            scheduler_status['next_run'] = '其他进程运行中'
        else:
            # 调度器完全未运行，尝试自动启动
            app.logger.info("[管理页面] 检测到调度器未运行，尝试自动启动")
            try:
                init_scheduler()
                if scheduler.running:
                    app.logger.info("[管理页面] 调度器自动启动成功")
                    log_activity('INFO', 'system', '调度器通过管理页面自动启动', None, request.remote_addr)
                    # 重新获取状态
                    jobs = scheduler.get_jobs()
                    if jobs:
                        next_run_time = jobs[0].next_run_time
                        if next_run_time:
                            if next_run_time.tzinfo is None:
                                next_run_time = APP_TIMEZONE.localize(next_run_time)
                            elif next_run_time.tzinfo != APP_TIMEZONE:
                                next_run_time = next_run_time.astimezone(APP_TIMEZONE)
                            scheduler_status['next_run'] = next_run_time.strftime('%Y-%m-%d %H:%M:%S')
                            scheduler_status['auto_started'] = True
                        else:
                            scheduler_status['next_run'] = '未知'
                    else:
                        scheduler_status['next_run'] = '无任务'
                    scheduler_status['running'] = True
                    scheduler_status['jobs'] = len(jobs) if jobs else 0
                else:
                    scheduler_status['next_run'] = '自动启动失败'
            except Exception as e:
                app.logger.error(f"[管理页面] 调度器自动启动失败: {e}")
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
        <link href="https://cdnjs.cloudflare.com/ajax/libs/bootstrap/5.3.8/css/bootstrap.min.css" rel="stylesheet">
        <link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/7.1.0/css/all.min.css" rel="stylesheet">
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
            
            <!-- 调度器详细状态 - 简化版本 -->
            <div class="card mb-4">
                <div class="card-header">
                    <h5>
                        <i class="fas fa-cogs"></i> 调度器状态
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
                                    <td>
                                        {{ scheduler_status['next_run'] }}
                                        {% if scheduler_status.get('auto_fixed') %}
                                            <span class="badge bg-success ms-2">
                                                <i class="fas fa-check-circle"></i> 已自动修复
                                            </span>
                                        {% endif %}
                                        {% if scheduler_status.get('auto_started') %}
                                            <span class="badge bg-info ms-2">
                                                <i class="fas fa-play-circle"></i> 已自动启动
                                            </span>
                                        {% endif %}
                                    </td>
                                </tr>
                            </table>
                        </div>
                        <div class="col-md-6">
                            <table class="table table-sm">
                                <tr>
                                    <td><strong>系统时区:</strong></td>
                                    <td>
                                        <span class="text-info">{{ scheduler_status['timezone'] }}</span>
                                    </td>
                                </tr>
                                <tr>
                                    <td><strong>当前时间:</strong></td>
                                    <td>
                                        <span class="text-success">{{ scheduler_status['current_time'] }}</span>
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
                            <form method="POST" action="/admin/push/restart-scheduler" style="display: inline;" class="ms-2">
                                <button type="submit" class="btn btn-outline-info" onclick="return confirm('确定要重启调度器吗？这将重新加载调度器配置。')">
                                    <i class="fas fa-sync"></i> 重启调度器
                                </button>
                            </form>
                            <form method="POST" action="/admin/push/reset-scheduler" style="display: inline;" class="ms-2">
                                <button type="submit" class="btn btn-outline-warning" onclick="return confirm('确定要重置调度器吗？这将清理锁文件并重新启动调度器。')">
                                    <i class="fas fa-redo"></i> 重置调度器
                                </button>
                            </form>
                            <small class="text-muted d-block mt-2">测试：模拟定时任务执行 | 重启：重新加载配置 | 重置：清理锁文件并重启调度器</small>
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
                                        onclick="return confirm('[WARN] 警告：这将清除所有用户的推送记录！\\n\\n清除后，之前推送过的文章会重新推送给用户。\\n\\n确定要继续吗？')">
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
        
        <script src="https://cdnjs.cloudflare.com/ajax/libs/bootstrap/5.3.8/js/bootstrap.bundle.min.js"></script>
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
        <link href="https://cdnjs.cloudflare.com/ajax/libs/bootstrap/5.3.8/css/bootstrap.min.css" rel="stylesheet">
        <link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/7.1.0/css/all.min.css" rel="stylesheet">
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
        
        <script src="https://cdnjs.cloudflare.com/ajax/libs/bootstrap/5.3.8/js/bootstrap.bundle.min.js"></script>
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
                SystemSetting.set_setting('push_check_frequency', request.form.get('push_check_frequency', '0.0833'), 'RQ调度器扫描间隔(小时)', 'push')
                SystemSetting.set_setting('push_enabled', request.form.get('push_enabled', 'true'), '启用自动推送', 'push')

                # 记录配置变更
                new_freq = request.form.get('push_check_frequency', '0.0833')
                seconds = int(float(new_freq) * 3600)
                app.logger.info(f"RQ调度器扫描间隔已更新为: {seconds}秒 ({seconds/60:.1f}分钟)")

                flash(f'推送配置已保存！新的扫描间隔: {seconds}秒。请执行 docker compose restart scheduler 使配置生效。', 'admin')
            
            
            # 保存系统配置
            elif 'system_config' in request.form:
                SystemSetting.set_setting('system_name', request.form.get('system_name', 'PubMed Literature Push'), '系统名称', 'system')
                SystemSetting.set_setting('log_retention_days', request.form.get('log_retention_days', '30'), '日志保留天数', 'system')
                SystemSetting.set_setting('max_articles_limit', request.form.get('max_articles_limit', '1000'), '文章数量上限', 'system')
                SystemSetting.set_setting('cleanup_articles_count', request.form.get('cleanup_articles_count', '100'), '单次清理文章数量', 'system')
                SystemSetting.set_setting('user_registration_enabled', request.form.get('user_registration_enabled', 'true'), '允许用户注册', 'system')
                SystemSetting.set_setting('require_invite_code', request.form.get('require_invite_code', 'false'), '需要邀请码注册', 'system')
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
        'require_invite_code': SystemSetting.get_setting('require_invite_code', 'false'),
    }
    
    # 获取缓存信息
    cache_info = journal_cache.get_cache_info()
    
    template = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>系统设置 - PubMed Literature Push</title>
        <meta charset="utf-8">
        <link href="https://cdnjs.cloudflare.com/ajax/libs/bootstrap/5.3.8/css/bootstrap.min.css" rel="stylesheet">
        <link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/7.1.0/css/all.min.css" rel="stylesheet">
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
                                    <label class="form-label">RQ调度器扫描间隔</label>
                                    <select class="form-control" name="push_check_frequency" required>
                                        <option value="0.0167" {% if settings.push_check_frequency == '0.0167' %}selected{% endif %}>每1分钟 (60秒) - 最精确</option>
                                        <option value="0.05" {% if settings.push_check_frequency == '0.05' %}selected{% endif %}>每3分钟 (180秒)</option>
                                        <option value="0.0833" {% if settings.push_check_frequency == '0.0833' %}selected{% endif %}>每5分钟 (300秒) - 推荐</option>
                                        <option value="0.1667" {% if settings.push_check_frequency == '0.1667' %}selected{% endif %}>每10分钟 (600秒)</option>
                                        <option value="0.25" {% if settings.push_check_frequency == '0.25' %}selected{% endif %}>每15分钟 (900秒)</option>
                                        <option value="0.5" {% if settings.push_check_frequency == '0.5' %}selected{% endif %}>每30分钟 (1800秒)</option>
                                        <option value="1" {% if settings.push_check_frequency == '1' %}selected{% endif %}>每1小时 (3600秒)</option>
                                    </select>
                                    <div class="form-text">
                                        <div class="alert alert-info mt-2 mb-0">
                                            <strong><i class="fas fa-info-circle"></i> RQ Scheduler 工作原理：</strong><br>
                                            <ul class="mb-2 mt-2">
                                                <li><strong>精确调度</strong>：每个订阅有独立的触发时间（如 09:30）</li>
                                                <li><strong>扫描间隔</strong>：调度器每隔此间隔扫描Redis，将到期任务移入执行队列</li>
                                                <li><strong>推送延迟</strong>：最多延迟 = 扫描间隔（如5分钟 → 最多延迟5分钟）</li>
                                                <li><strong>性能影响</strong>：间隔越短越精确，但Redis扫描越频繁</li>
                                            </ul>
                                            <strong class="text-warning"><i class="fas fa-exclamation-triangle"></i> 重要：</strong> 修改此配置后需要<strong>重启调度器容器</strong>才能生效：<br>
                                            <code>docker compose restart scheduler</code>
                                        </div>
                                    </div>
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
                                <div class="mb-3">
                                    <div class="form-check">
                                        <input class="form-check-input" type="checkbox" name="require_invite_code" value="true"
                                               {{ 'checked' if settings.require_invite_code == 'true' else '' }}>
                                        <label class="form-check-label">
                                            需要邀请码注册
                                        </label>
                                    </div>
                                    <div class="form-text">开启后新用户注册需要提供有效的邀请码</div>
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
        
        <script src="https://cdnjs.cloudflare.com/ajax/libs/bootstrap/5.3.8/js/bootstrap.bundle.min.js"></script>
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
        # 跨进程调度器状态检查
        def check_scheduler_running():
            import time
            import json
            
            if scheduler.running:
                return True
                
            lock_file_path = '/app/data/scheduler.lock'
            if not os.path.exists(lock_file_path):
                return False
                
            try:
                with open(lock_file_path, 'r') as f:
                    lock_data = json.loads(f.read())
                
                last_heartbeat = lock_data.get('last_heartbeat', 0)
                current_time = time.time()
                heartbeat_age = current_time - last_heartbeat
                
                return heartbeat_age <= 120
            except:
                return False
        
        scheduler_running = check_scheduler_running()
        
        jobs = []
        if scheduler_running and scheduler.running:
            for job in scheduler.get_jobs():
                next_run_time = job.next_run_time
                next_run_str = '未设置'
                if next_run_time:
                    # 确保时间显示使用应用程序时区
                    if next_run_time.tzinfo is None:
                        next_run_time = APP_TIMEZONE.localize(next_run_time)
                    elif next_run_time.tzinfo != APP_TIMEZONE:
                        next_run_time = next_run_time.astimezone(APP_TIMEZONE)
                    next_run_str = next_run_time.strftime('%Y-%m-%d %H:%M:%S')
                
                jobs.append({
                    'id': job.id,
                    'name': job.name,
                    'next_run': next_run_str,
                    'trigger': str(job.trigger)
                })
        
        status = {
            'running': scheduler_running,
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
    """手动触发推送（异步执行）"""
    try:
        log_activity('INFO', 'admin', f'管理员 {current_user.email} 手动触发推送', current_user.id, request.remote_addr)

        # 使用RQ异步执行推送任务
        from rq_config import enqueue_job
        from tasks import batch_push_all_users

        job = enqueue_job(batch_push_all_users, priority='high')

        flash(f'推送任务已提交到队列（任务ID: {job.id}），请稍后查看推送记录', 'admin')

    except Exception as e:
        log_activity('ERROR', 'admin', f'手动推送任务提交失败: {str(e)}', current_user.id, request.remote_addr)
        flash(f'推送任务提交失败: {str(e)}', 'admin')

    return redirect(url_for('admin_push'))

@app.route('/admin/push/reset-scheduler', methods=['POST'])
@admin_required
def reset_scheduler():
    """重置调度器状态"""
    try:
        log_activity('INFO', 'admin', f'管理员 {current_user.email} 重置调度器状态', current_user.id, request.remote_addr)
        
        # 强制停止当前调度器
        if scheduler.running:
            try:
                shutdown_scheduler_safely()
                app.logger.info("[调度器重置] 已停止运行中的调度器")
            except Exception as e:
                app.logger.warning(f"[调度器重置] 停止调度器失败: {e}")
        
        # 清理所有锁文件和标记文件
        lock_files = [
            '/app/data/scheduler.lock',
            '/app/data/scheduler_init_done'
        ]
        
        for lock_file in lock_files:
            if os.path.exists(lock_file):
                try:
                    os.remove(lock_file)
                    app.logger.info(f"[调度器重置] 已删除锁文件: {lock_file}")
                except Exception as e:
                    app.logger.warning(f"[调度器重置] 删除锁文件失败 {lock_file}: {e}")
        
        # 重置应用标记
        if hasattr(app, '_scheduler_init_attempted'):
            delattr(app, '_scheduler_init_attempted')
        
        # 强制重新初始化调度器
        try:
            with app.app_context():
                initialize_scheduler_safely()
            
            if scheduler.running:
                flash('调度器重置成功，已重新启动', 'admin')
                app.logger.info("[调度器重置] 调度器重新启动成功")
            else:
                flash('调度器重置完成，但重新启动失败，请检查日志', 'admin')
                app.logger.error("[调度器重置] 调度器重新启动失败")
        except Exception as e:
            flash(f'调度器重新初始化失败: {str(e)}', 'admin')
            app.logger.error(f"[调度器重置] 重新初始化失败: {e}")
        
    except Exception as e:
        log_activity('ERROR', 'admin', f'重置调度器失败: {str(e)}', current_user.id, request.remote_addr)
        flash(f'重置调度器失败: {str(e)}', 'admin')
    
    return redirect(url_for('admin_push'))

@app.route('/admin/push/restart-scheduler', methods=['POST'])
@admin_required
def restart_scheduler():
    """简单重启调度器"""
    try:
        log_activity('INFO', 'admin', f'管理员 {current_user.email} 重启调度器', current_user.id, request.remote_addr)
        
        # 停止当前调度器
        if scheduler.running:
            try:
                shutdown_scheduler_safely()
                app.logger.info("[调度器重启] 已停止运行中的调度器")
            except Exception as e:
                app.logger.warning(f"[调度器重启] 停止调度器失败: {e}")
        
        # 重新初始化调度器
        try:
            with app.app_context():
                init_scheduler()
            
            if scheduler.running:
                flash('调度器重启成功', 'admin')
                app.logger.info("[调度器重启] 调度器重启成功")
            else:
                flash('调度器重启失败，请检查日志', 'admin')
                app.logger.error("[调度器重启] 调度器重启失败")
        except Exception as e:
            flash(f'调度器重启失败: {str(e)}', 'admin')
            app.logger.error(f"[调度器重启] 重启失败: {e}")
        
    except Exception as e:
        log_activity('ERROR', 'admin', f'重启调度器失败: {str(e)}', current_user.id, request.remote_addr)
        flash(f'重启调度器失败: {str(e)}', 'admin')
    
    return redirect(url_for('admin_push'))

# RQ管理路由
@app.route('/admin/rq/trigger-batch-schedule', methods=['POST'])
@admin_required
def admin_rq_trigger_batch_schedule():
    """触发批量调度所有订阅"""
    try:
        from rq_config import enqueue_job
        from tasks import batch_schedule_all_subscriptions
        job = enqueue_job(batch_schedule_all_subscriptions, priority='high')
        
        log_activity('INFO', 'admin', f'RQ批量调度已触发: {job.id}', current_user.id, request.remote_addr)
        flash(f'RQ批量调度任务已排队: {job.id}', 'admin')
        
    except Exception as e:
        log_activity('ERROR', 'admin', f'RQ批量调度失败: {str(e)}', current_user.id, request.remote_addr)
        flash(f'RQ批量调度失败: {str(e)}', 'admin')
    
    return redirect(url_for('admin_push'))

@app.route('/admin/rq/immediate-push/<int:subscription_id>', methods=['POST'])
@admin_required
def admin_rq_immediate_push(subscription_id):
    """立即推送指定订阅"""
    try:
        from tasks import immediate_push_subscription
        job = immediate_push_subscription(subscription_id)
        
        log_activity('INFO', 'admin', f'立即推送订阅 {subscription_id}: {job.id}', current_user.id, request.remote_addr)
        flash(f'订阅 {subscription_id} 立即推送任务已排队: {job.id}', 'admin')
        
    except Exception as e:
        log_activity('ERROR', 'admin', f'立即推送订阅 {subscription_id} 失败: {str(e)}', current_user.id, request.remote_addr)
        flash(f'立即推送订阅 {subscription_id} 失败: {str(e)}', 'admin')
    
    return redirect(url_for('admin_push'))

@app.route('/admin/rq/clear-failed', methods=['POST'])
@admin_required
def admin_rq_clear_failed():
    """清空失败任务"""
    try:
        from rq_config import clear_failed_jobs
        clear_failed_jobs()
        
        log_activity('INFO', 'admin', 'RQ失败任务已清空', current_user.id, request.remote_addr)
        flash('RQ失败任务已清空', 'admin')
        
    except Exception as e:
        log_activity('ERROR', 'admin', f'清空RQ失败任务失败: {str(e)}', current_user.id, request.remote_addr)
        flash(f'清空RQ失败任务失败: {str(e)}', 'admin')
    
    return redirect(url_for('admin_push'))

@app.route('/admin/rq/status')
@admin_required
def admin_rq_status():
    """RQ状态API"""
    try:
        queue_info = get_queue_info()
        failed_jobs = get_failed_jobs()
        
        return jsonify({
            'status': 'success',
            'queue_info': queue_info,
            'failed_jobs_count': len(failed_jobs),
            'failed_jobs': failed_jobs[:10]  # 只返回前10个失败任务
        })
        
    except Exception as e:
        return jsonify({
            'status': 'error',
            'error': str(e)
        }), 500

@app.route('/admin/rq/test', methods=['POST'])
@admin_required
def admin_rq_test():
    """RQ连接测试"""
    try:
        from rq_config import enqueue_job
        from tasks import test_rq_connection

        job = enqueue_job(test_rq_connection, priority='high')

        log_activity('INFO', 'admin', f'RQ连接测试已触发: {job.id}', current_user.id, request.remote_addr)
        flash(f'RQ连接测试任务已排队: {job.id}', 'admin')

    except Exception as e:
        log_activity('ERROR', 'admin', f'RQ连接测试失败: {str(e)}', current_user.id, request.remote_addr)
        flash(f'RQ连接测试失败: {str(e)}', 'admin')

    return redirect(url_for('admin_push'))

# ==================== 搜索缓存管理API ====================

@app.route('/admin/cache')
@admin_required
def admin_cache():
    """L1搜索缓存管理页面"""
    template = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>L1搜索缓存管理 - PubMed Literature Push</title>
        <meta charset="utf-8">
        <link href="https://cdnjs.cloudflare.com/ajax/libs/bootstrap/5.3.8/css/bootstrap.min.css" rel="stylesheet">
        <link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/7.1.0/css/all.min.css" rel="stylesheet">
        <style>
            .stat-card { transition: transform 0.2s; }
            .stat-card:hover { transform: translateY(-5px); }
            .metric-value { font-size: 2.5rem; font-weight: bold; }
            .metric-label { color: #6c757d; font-size: 0.9rem; }
            .badge-enabled { background-color: #28a745; }
            .badge-disabled { background-color: #dc3545; }
        </style>
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
                    <h2><i class="fas fa-server"></i> L1搜索缓存管理</h2>
                    <p class="text-muted">智能缓存优化PubMed API调用，提升70-90%响应速度</p>
                </div>
                <div>
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

            <!-- 缓存状态 -->
            <div class="card mb-4">
                <div class="card-header d-flex justify-content-between align-items-center">
                    <h5><i class="fas fa-info-circle"></i> 缓存状态</h5>
                    <span id="cache-status-badge" class="badge">加载中...</span>
                </div>
                <div class="card-body">
                    <div class="row text-center">
                        <div class="col-md-3">
                            <div class="stat-card p-3">
                                <div class="metric-value text-primary" id="hit-rate">-</div>
                                <div class="metric-label">缓存命中率</div>
                            </div>
                        </div>
                        <div class="col-md-3">
                            <div class="stat-card p-3">
                                <div class="metric-value text-success" id="total-hits">-</div>
                                <div class="metric-label">总命中次数</div>
                            </div>
                        </div>
                        <div class="col-md-3">
                            <div class="stat-card p-3">
                                <div class="metric-value text-warning" id="total-requests">-</div>
                                <div class="metric-label">总请求次数</div>
                            </div>
                        </div>
                        <div class="col-md-3">
                            <div class="stat-card p-3">
                                <div class="metric-value text-info" id="cache-count">-</div>
                                <div class="metric-label">当前缓存数</div>
                            </div>
                        </div>
                    </div>
                </div>
            </div>

            <!-- 命中详情 -->
            <div class="card mb-4">
                <div class="card-header">
                    <h5><i class="fas fa-chart-bar"></i> 命中详情</h5>
                </div>
                <div class="card-body">
                    <div class="row">
                        <div class="col-md-4">
                            <p><strong>精确匹配命中:</strong> <span id="exact-hits" class="text-success">-</span></p>
                        </div>
                        <div class="col-md-4">
                            <p><strong>宽松匹配命中:</strong> <span id="relaxed-hits" class="text-info">-</span></p>
                        </div>
                        <div class="col-md-4">
                            <p><strong>缓存未命中:</strong> <span id="total-misses" class="text-danger">-</span></p>
                        </div>
                    </div>
                    <div class="progress" style="height: 30px;">
                        <div id="exact-bar" class="progress-bar bg-success" role="progressbar" style="width: 0%">精确</div>
                        <div id="relaxed-bar" class="progress-bar bg-info" role="progressbar" style="width: 0%">宽松</div>
                        <div id="miss-bar" class="progress-bar bg-danger" role="progressbar" style="width: 0%">未命中</div>
                    </div>
                </div>
            </div>

            <!-- 缓存管理操作 -->
            <div class="card mb-4">
                <div class="card-header">
                    <h5><i class="fas fa-tools"></i> 缓存管理操作</h5>
                </div>
                <div class="card-body">
                    <div class="row">
                        <div class="col-md-6">
                            <h6>失效特定关键词缓存</h6>
                            <div class="input-group mb-3">
                                <input type="text" class="form-control" id="invalidate-keywords"
                                       placeholder="输入关键词（例如：cancer treatment）">
                                <button class="btn btn-warning" onclick="invalidateCache()">
                                    <i class="fas fa-eraser"></i> 失效缓存
                                </button>
                            </div>
                        </div>
                        <div class="col-md-6">
                            <h6>全局操作</h6>
                            <button class="btn btn-primary me-2" onclick="refreshStats()">
                                <i class="fas fa-sync"></i> 刷新统计
                            </button>
                            <button class="btn btn-info me-2" onclick="resetStats()">
                                <i class="fas fa-redo"></i> 重置统计
                            </button>
                            <button class="btn btn-danger" onclick="clearAllCache()">
                                <i class="fas fa-trash"></i> 清空所有缓存
                            </button>
                        </div>
                    </div>
                </div>
            </div>

            <!-- 缓存信息 -->
            <div class="card">
                <div class="card-header">
                    <h5><i class="fas fa-book"></i> 缓存说明</h5>
                </div>
                <div class="card-body">
                    <h6>核心优势</h6>
                    <ul>
                        <li>API调用节省: 相同关键词搜索可节省70-90%的PubMed API调用</li>
                        <li>响应速度提升: 缓存命中时响应时间从3-5秒降低到<100ms</li>
                        <li>智能降级: Redis不可用时自动回退到直接搜索</li>
                        <li>多级缓存策略: 精确匹配 → 宽松匹配 → 直接搜索</li>
                    </ul>
                    <h6>缓存策略</h6>
                    <p><strong>TTL范围:</strong> 30分钟 - 24小时（根据结果数量和时间因素动态调整）</p>
                    <p><strong>最后统计重置:</strong> <span id="last-reset">-</span></p>
                </div>
            </div>
        </div>

        <script src="https://cdnjs.cloudflare.com/ajax/libs/bootstrap/5.3.8/js/bootstrap.bundle.min.js"></script>
        <script>
            // 加载缓存统计
            async function refreshStats() {
                try {
                    const response = await fetch('/admin/cache/stats');
                    const data = await response.json();

                    if (data.success) {
                        const stats = data.stats;

                        // 更新状态徽章
                        const statusBadge = document.getElementById('cache-status-badge');
                        if (stats.enabled) {
                            statusBadge.className = 'badge badge-enabled';
                            statusBadge.textContent = '已启用';
                        } else {
                            statusBadge.className = 'badge badge-disabled';
                            statusBadge.textContent = '已禁用';
                        }

                        // 更新主要指标
                        document.getElementById('hit-rate').textContent = stats.hit_rate.toFixed(1) + '%';
                        document.getElementById('total-hits').textContent = stats.total_hits;
                        document.getElementById('total-requests').textContent = stats.total_requests;
                        document.getElementById('cache-count').textContent = stats.cache_count || 0;

                        // 更新命中详情
                        document.getElementById('exact-hits').textContent = stats.exact_hits;
                        document.getElementById('relaxed-hits').textContent = stats.relaxed_hits;
                        document.getElementById('total-misses').textContent = stats.total_misses;

                        // 更新进度条
                        const total = stats.total_requests || 1;
                        const exactPercent = (stats.exact_hits / total * 100).toFixed(1);
                        const relaxedPercent = (stats.relaxed_hits / total * 100).toFixed(1);
                        const missPercent = (stats.total_misses / total * 100).toFixed(1);

                        document.getElementById('exact-bar').style.width = exactPercent + '%';
                        document.getElementById('exact-bar').textContent = `精确 ${exactPercent}%`;
                        document.getElementById('relaxed-bar').style.width = relaxedPercent + '%';
                        document.getElementById('relaxed-bar').textContent = `宽松 ${relaxedPercent}%`;
                        document.getElementById('miss-bar').style.width = missPercent + '%';
                        document.getElementById('miss-bar').textContent = `未命中 ${missPercent}%`;

                        // 更新最后重置时间
                        document.getElementById('last-reset').textContent = stats.last_reset || '从未';
                    }
                } catch (error) {
                    console.error('加载统计失败:', error);
                    alert('加载统计失败: ' + error.message);
                }
            }

            // 失效特定关键词缓存
            async function invalidateCache() {
                const keywords = document.getElementById('invalidate-keywords').value.trim();
                if (!keywords) {
                    alert('请输入关键词');
                    return;
                }

                if (!confirm(`确定要失效关键词 "${keywords}" 的缓存吗?`)) {
                    return;
                }

                try {
                    const response = await fetch('/admin/cache/invalidate', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({keywords: keywords})
                    });
                    const data = await response.json();

                    if (data.success) {
                        alert('缓存失效成功');
                        document.getElementById('invalidate-keywords').value = '';
                        refreshStats();
                    } else {
                        alert('失效失败: ' + data.error);
                    }
                } catch (error) {
                    alert('操作失败: ' + error.message);
                }
            }

            // 重置统计
            async function resetStats() {
                if (!confirm('确定要重置缓存统计信息吗？这不会删除缓存数据。')) {
                    return;
                }

                try {
                    const response = await fetch('/admin/cache/reset-stats', {
                        method: 'POST'
                    });
                    const data = await response.json();

                    if (data.success) {
                        alert('统计信息已重置');
                        refreshStats();
                    } else {
                        alert('重置失败: ' + data.error);
                    }
                } catch (error) {
                    alert('操作失败: ' + error.message);
                }
            }

            // 清空所有缓存
            async function clearAllCache() {
                if (!confirm('警告：确定要清空所有搜索缓存吗？此操作不可撤销！')) {
                    return;
                }

                try {
                    const response = await fetch('/admin/cache/clear', {
                        method: 'POST'
                    });
                    const data = await response.json();

                    if (data.success) {
                        alert(`成功清空 ${data.deleted_count} 个缓存键`);
                        refreshStats();
                    } else {
                        alert('清空失败: ' + data.error);
                    }
                } catch (error) {
                    alert('操作失败: ' + error.message);
                }
            }

            // 页面加载时刷新统计
            refreshStats();

            // 每30秒自动刷新
            setInterval(refreshStats, 30000);
        </script>
    </body>
    </html>
    """
    return render_template_string(template)

@app.route('/admin/cache/stats')
@admin_required
def admin_cache_stats():
    """获取缓存统计信息API"""
    try:
        stats = search_cache_service.get_cache_stats()
        return jsonify({
            'success': True,
            'stats': stats
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/admin/cache/clear', methods=['POST'])
@admin_required
def admin_cache_clear():
    """清空所有搜索缓存"""
    try:
        deleted_count = search_cache_service.clear_all_cache()
        log_activity('INFO', 'admin', f'管理员 {current_user.email} 清空搜索缓存: {deleted_count}个键', current_user.id, request.remote_addr)
        flash(f'缓存清空成功，删除 {deleted_count} 个缓存键', 'admin')
        return jsonify({
            'success': True,
            'deleted_count': deleted_count
        })
    except Exception as e:
        log_activity('ERROR', 'admin', f'清空缓存失败: {str(e)}', current_user.id, request.remote_addr)
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/admin/cache/invalidate', methods=['POST'])
@admin_required
def admin_cache_invalidate():
    """手动失效指定关键词的缓存"""
    try:
        keywords = request.json.get('keywords')
        if not keywords:
            return jsonify({
                'success': False,
                'error': '关键词不能为空'
            }), 400

        success = search_cache_service.invalidate_cache(keywords)
        log_activity('INFO', 'admin', f'管理员 {current_user.email} 失效缓存: {keywords}', current_user.id, request.remote_addr)

        return jsonify({
            'success': success,
            'message': f'关键词 "{keywords}" 的缓存已失效' if success else '失效失败'
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/admin/cache/reset-stats', methods=['POST'])
@admin_required
def admin_cache_reset_stats():
    """重置缓存统计信息"""
    try:
        success = search_cache_service.reset_cache_stats()
        log_activity('INFO', 'admin', f'管理员 {current_user.email} 重置缓存统计', current_user.id, request.remote_addr)

        return jsonify({
            'success': success,
            'message': '缓存统计已重置'
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

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
        <link href="https://cdnjs.cloudflare.com/ajax/libs/bootstrap/5.3.8/css/bootstrap.min.css" rel="stylesheet">
        <link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/7.1.0/css/all.min.css" rel="stylesheet">
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
        
        <script src="https://cdnjs.cloudflare.com/ajax/libs/bootstrap/5.3.8/js/bootstrap.bundle.min.js"></script>
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
                from_email=request.form.get('from_email') or None,
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
        <link href="https://cdnjs.cloudflare.com/ajax/libs/bootstrap/5.3.8/css/bootstrap.min.css" rel="stylesheet">
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
                                    <label class="form-label">SMTP用户名 *</label>
                                    <input type="text" class="form-control" name="username" required
                                           placeholder="ls5B8XBWIx 或 your-email@qq.com">
                                    <div class="form-text">用于SMTP登录认证的用户名</div>
                                </div>
                                <div class="mb-3">
                                    <label class="form-label">发件人邮箱地址</label>
                                    <input type="email" class="form-control" name="from_email"
                                           placeholder="sender@example.com">
                                    <div class="form-text">显示为发件人的邮箱地址(留空时使用SMTP用户名)</div>
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
            config.from_email = request.form.get('from_email') or None
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
        <link href="https://cdnjs.cloudflare.com/ajax/libs/bootstrap/5.3.8/css/bootstrap.min.css" rel="stylesheet">
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
                                    <label class="form-label">SMTP用户名 *</label>
                                    <input type="text" class="form-control" name="username" value="{{ config.username }}" required>
                                    <div class="form-text">用于SMTP登录认证的用户名</div>
                                </div>
                                <div class="mb-3">
                                    <label class="form-label">发件人邮箱地址</label>
                                    <input type="email" class="form-control" name="from_email" value="{{ config.from_email or '' }}">
                                    <div class="form-text">显示为发件人的邮箱地址(留空时使用SMTP用户名)</div>
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

        # 使用from_email字段(如果有),否则使用username
        sender_email = config.from_email or config.username
        msg = Message(
            subject=test_subject,
            sender=sender_email,
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
        'ai_brief_intro_enabled': SystemSetting.get_setting('ai_brief_intro_enabled', 'false'),
        'ai_translation_batch_size': SystemSetting.get_setting('ai_translation_batch_size', '5'),
        'ai_translation_batch_delay': SystemSetting.get_setting('ai_translation_batch_delay', '3'),
        # 添加已保存的提供商和模型配置
        'ai_query_builder_provider_id': SystemSetting.get_setting('ai_query_builder_provider_id', ''),
        'ai_query_builder_model_id': SystemSetting.get_setting('ai_query_builder_model_id', ''),
        'ai_translation_provider_id': SystemSetting.get_setting('ai_translation_provider_id', ''),
        'ai_translation_model_id': SystemSetting.get_setting('ai_translation_model_id', ''),
        'ai_brief_intro_provider_id': SystemSetting.get_setting('ai_brief_intro_provider_id', ''),
        'ai_brief_intro_model_id': SystemSetting.get_setting('ai_brief_intro_model_id', ''),
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
        SystemSetting.set_setting('ai_brief_intro_enabled', request.form.get('ai_brief_intro_enabled', 'false'), '启用AI文献简介', 'ai')
        
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

@app.route('/admin/ai/config/brief-intro', methods=['POST'])
@admin_required
def admin_ai_config_brief_intro():
    """配置文献简介生成"""
    try:
        # 保存功能开关
        enabled = request.form.get('enabled', 'false')
        SystemSetting.set_setting('ai_brief_intro_enabled', enabled, '启用AI文献简介', 'ai')
        
        # 保存提供商和模型选择
        provider_id = request.form.get('provider_id', '').strip()
        model_id = request.form.get('model_id', '').strip()
        
        if provider_id and model_id:
            SystemSetting.set_setting('ai_brief_intro_provider_id', provider_id, '文献简介提供商ID', 'ai')
            SystemSetting.set_setting('ai_brief_intro_model_id', model_id, '文献简介模型ID', 'ai')
        
        log_activity('INFO', 'admin', f'管理员 {current_user.email} 更新文献简介配置', current_user.id, request.remote_addr)
        flash('文献简介配置保存成功', 'success')
    except Exception as e:
        log_activity('ERROR', 'admin', f'文献简介配置保存失败: {str(e)}', current_user.id, request.remote_addr)
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

@app.route('/admin/ai/test/brief-intro', methods=['POST'])
@admin_required
def admin_ai_test_brief_intro():
    """测试AI文献简介生成"""
    try:
        title = request.form.get('title', '').strip()
        abstract = request.form.get('abstract', '').strip()
        if not title or not abstract:
            return jsonify({'success': False, 'message': '请输入文献标题和摘要'})
        
        # 临时启用AI文献简介进行测试
        original_setting = SystemSetting.get_setting('ai_brief_intro_enabled', 'false')
        SystemSetting.set_setting('ai_brief_intro_enabled', 'true', '启用AI文献简介', 'ai')
        
        try:
            brief_intro = ai_service.generate_brief_intro(title, abstract)
            app.logger.info(f"测试生成的简介长度: {len(brief_intro)} 字符")
            return jsonify({
                'success': True, 
                'brief_intro': brief_intro,
                'message': f'测试成功。文献标题: {title[:50]}...',
                'debug_info': f'生成的简介长度: {len(brief_intro)} 字符'
            })
        finally:
            # 恢复原设置
            SystemSetting.set_setting('ai_brief_intro_enabled', original_setting, '启用AI文献简介', 'ai')
            
    except Exception as e:
        return jsonify({'success': False, 'message': f'生成失败: {str(e)}'})

@app.route('/admin/ai/prompts')
@admin_required
def admin_ai_prompts():
    """AI提示词管理"""
    query_prompts = AIPromptTemplate.query.filter_by(template_type='query_builder').all()
    translator_prompts = AIPromptTemplate.query.filter_by(template_type='translator').all()
    brief_intro_prompts = AIPromptTemplate.query.filter_by(template_type='brief_intro').all()
    
    return render_template_string(get_ai_prompts_template(), 
                                query_prompts=query_prompts,
                                translator_prompts=translator_prompts,
                                brief_intro_prompts=brief_intro_prompts)

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
        
        if template_type not in ['query_builder', 'translator', 'brief_intro']:
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
        return redirect(url_for('subscriptions'))
    
    edit_template = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>编辑订阅 - PubMed Literature Push</title>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <link href="https://cdnjs.cloudflare.com/ajax/libs/bootstrap/5.3.8/css/bootstrap.min.css" rel="stylesheet">
        <link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/7.1.0/css/all.min.css" rel="stylesheet">
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
                                    <a href="/subscriptions" class="btn btn-secondary">
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
        <script src="https://cdnjs.cloudflare.com/ajax/libs/bootstrap/5.3.8/js/bootstrap.bundle.min.js"></script>
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
        return redirect(url_for('subscriptions'))
    
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

        # 重新调度订阅推送任务（更新后的时间设置）
        try:
            from tasks import schedule_next_push_for_subscription
            schedule_next_push_for_subscription(subscription)
            app.logger.info(f"已为订阅 {subscription.id} 更新RQ调度任务")
        except Exception as e:
            app.logger.warning(f"为订阅 {subscription.id} 更新RQ调度任务失败: {e}")

        log_activity('INFO', 'subscription', f'用户 {current_user.email} 更新订阅设置: {subscription.keywords}', current_user.id, request.remote_addr)
        flash('订阅设置更新成功！', 'success')
        
    except Exception as e:
        db.session.rollback()
        flash(f'更新失败: {str(e)}', 'error')
    
    return redirect(url_for('edit_subscription', subscription_id=subscription_id))

if __name__ == '__main__':
    with app.app_context():
        # 只在直接运行时执行初始化，gunicorn环境跳过
        if os.environ.get('WERKZEUG_RUN_MAIN') != 'true':
            print("开始数据库初始化...")
            
            # 验证Article模型是否包含所有必需字段
            article_columns = [column.name for column in Article.__table__.columns]
            required_fields = ['abstract_cn', 'brief_intro', 'issn', 'eissn']
            missing_fields = [field for field in required_fields if field not in article_columns]
            
            if missing_fields:
                print(f"错误：Article模型缺少字段: {missing_fields}")
                print("请检查模型定义...")
            else:
                print("Article模型包含所有必需字段")
        
        # 删除现有数据库文件以确保完全重新创建
        import os
        db_path = 'pubmed_app.db'
        if os.path.exists(db_path):
            print(f"删除现有数据库文件: {db_path}")
            os.remove(db_path)
        
        # 创建所有表
        print("创建数据库表...")
        db.create_all()
        print("数据库表创建完成")
        
        # 验证创建的表结构
        from sqlalchemy import inspect
        inspector = inspect(db.engine)
        actual_columns = {col['name'] for col in inspector.get_columns('article')}
        
        print(f"Article表实际包含的字段: {sorted(actual_columns)}")
        
        for field in required_fields:
            if field in actual_columns:
                print(f"[OK] {field} 字段存在")
            else:
                print(f"✗ {field} 字段缺失")
        
        # 原有的结构检查和修复函数保持不变
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
                
                # 检查Article表是否缺少字段
                article_columns = {col['name'] for col in inspector.get_columns('article')}
                
                # Article模型应有的字段（AI增强字段）
                expected_article_fields = {
                    'abstract_cn': 'TEXT',  # 中文翻译
                    'brief_intro': 'TEXT',  # AI生成的简介（一句话总结）
                    'issn': 'VARCHAR(20)',  # ISSN字段
                    'eissn': 'VARCHAR(20)'  # 电子ISSN字段
                }
                
                # 检查缺失的Article字段
                missing_article_fields = []
                for field_name, field_def in expected_article_fields.items():
                    if field_name not in article_columns:
                        missing_article_fields.append((field_name, field_def))
                
                if missing_article_fields:
                    print(f"发现Article表缺失 {len(missing_article_fields)} 个字段，正在修复...")
                    
                    # 使用原生SQL添加字段
                    for field_name, field_def in missing_article_fields:
                        try:
                            with db.engine.connect() as conn:
                                conn.execute(text(f'ALTER TABLE article ADD COLUMN {field_name} {field_def}'))
                                conn.commit()
                            print(f"已添加Article字段: {field_name}")
                        except Exception as e:
                            if 'duplicate column name' not in str(e):
                                print(f"添加Article字段 {field_name} 失败: {e}")
                    
                    print("Article表结构修复完成")
                else:
                    print("Article表结构检查通过")
                    
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
        
        # 添加详细的表结构验证和调试输出
        print("\n" + "="*60)
        print("[数据库验证] 数据库表结构详细验证报告")
        print("="*60)
        
        try:
            from sqlalchemy import inspect
            inspector = inspect(db.engine)
            
            # 检查所有表
            tables = inspector.get_table_names()
            print(f"🗂️  已创建的表 ({len(tables)}): {', '.join(tables)}")
            
            # 重点检查Article表结构
            if 'article' in tables:
                print(f"\n📋 Article表详细结构分析:")
                article_columns = inspector.get_columns('article')
                print(f"   总字段数: {len(article_columns)}")
                print(f"   字段详情:")
                
                for i, col in enumerate(article_columns, 1):
                    col_type = str(col['type'])
                    nullable = "NULL" if col['nullable'] else "NOT NULL"
                    default_info = f", DEFAULT: {col['default']}" if col.get('default') else ""
                    print(f"     {i:2d}. {col['name']:15s} | {col_type:15s} | {nullable}{default_info}")
                
                # 验证关键AI字段
                actual_columns = {col['name'] for col in article_columns}
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
                        print(f"     [OK] {field:15s} : 存在 ({desc})")
                    else:
                        print(f"     [ERROR] {field:15s} : 缺失 ({desc})")
                        all_present = False
                        
                if all_present:
                    print(f"\n🎉 Article表结构完整！所有AI功能字段都存在")
                else:
                    print(f"\n[WARN]  Article表存在缺失字段，可能影响AI功能")
                    
            else:
                print("[ERROR] Article表未找到！")
            
            # 检查其他重要表的关键字段
            important_tables = {
                'user': ['email', 'password_hash', 'push_time', 'push_frequency'],
                'subscription': ['keywords', 'is_active', 'max_results'],
                'mail_config': ['smtp_server', 'username', 'is_active'],
                'ai_setting': ['provider_name', 'api_key', 'is_active']
            }
            
            for table_name, key_fields in important_tables.items():
                if table_name in tables:
                    columns = inspector.get_columns(table_name)
                    actual_fields = {col['name'] for col in columns}
                    print(f"\n📋 {table_name.capitalize()}表: {len(columns)} 个字段")
                    
                    for field in key_fields:
                        status = "[OK]" if field in actual_fields else "[ERROR]"
                        print(f"     {status} {field}")
                else:
                    print(f"\n[ERROR] {table_name}表未找到")
                    
        except Exception as e:
            print(f"[ERROR] 表结构验证失败: {e}")
            
        print("\n" + "="*60)
        print("[验证完成] 验证报告完成")
        print("="*60 + "\n")
        
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
                ('push_check_frequency', '0.0833', 'RQ调度器扫描间隔(小时)', 'push'),  # 默认5分钟
                ('push_enabled', 'true', '启用自动推送', 'push'),
                ('mail_server', 'smtp.gmail.com', 'SMTP服务器地址', 'mail'),
                ('mail_port', '587', 'SMTP端口', 'mail'),
                ('mail_username', '', '发送邮箱', 'mail'),
                ('mail_password', '', '邮箱密码/应用密码', 'mail'),
                ('mail_use_tls', 'true', '启用TLS加密', 'mail'),
                ('system_name', 'PubMed Literature Push', '系统名称', 'system'),
                ('log_retention_days', '30', '日志保留天数', 'system'),
                ('user_registration_enabled', 'true', '允许用户注册', 'system'),
                ('require_invite_code', 'false', '需要邀请码注册', 'system'),
                ('max_articles_limit', '1000', '文章数量上限', 'system'),
                ('cleanup_articles_count', '100', '单次清理文章数量', 'system'),
                # AI功能设置
                ('ai_query_builder_enabled', 'true', '启用AI检索式生成', 'ai'),
                ('ai_translation_enabled', 'true', '启用AI摘要翻译', 'ai'),
                ('ai_brief_intro_enabled', 'true', '启用AI文献简介', 'ai'),
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
                    'prompt_content': """# 任务：构建专业级PubMed文献检索式

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
    - **最终输出**: 你的最终回复应仅包含最终成果本身，不得包含任何步骤说明、分析或其他无关内容。""",
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
                },
                {
                    'template_type': 'brief_intro',
                    'prompt_content': """请为以下医学文献生成一句话简介，要求：
1. 突出文献的核心发现或主要贡献
2. 使用简洁明了的中文表达
3. 控制在30-50字以内
4. 只返回简介内容，不要其他文字

标题: {title}
摘要: {abstract}
简介:""",
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
            print("URL: http://127.0.0.1:5005")
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
            app.run(host='127.0.0.1', port=5005, debug=debug_mode)
        except KeyboardInterrupt:
            print("\\n服务器已停止")
        finally:
            shutdown_scheduler_safely()

# 应用初始化函数（多worker环境）
def initialize_app():
    """应用初始化函数，多worker环境下含调度器恢复机制"""
    # 多worker环境下，进行基本的数据库检查和调度器恢复
    with app.app_context():
        print("应用初始化...")
        
        # 获取实际数据库文件路径
        db_url = os.environ.get('DATABASE_URL')
        if not db_url:
            db_path = os.path.abspath("pubmed_app.db")
        else:
            if db_url.startswith('sqlite:///'):
                # 提取数据库文件路径
                if db_url.startswith('sqlite:////'):
                    # 绝对路径: sqlite:////app/data/pubmed_app.db
                    db_path = db_url.replace('sqlite:///', '')
                else:
                    # 相对路径: sqlite:///pubmed_app.db
                    db_path = db_url.replace('sqlite:///', '')
                    if not os.path.isabs(db_path):
                        db_path = os.path.abspath(db_path)
            else:
                print("[OK] 使用非SQLite数据库，跳过文件检查")
                return
        
        # 检查数据库是否存在
        if not os.path.exists(db_path):
            print(f"[WARN]  数据库不存在: {db_path}")
            print("[WARN]  请先运行初始化")
            return
        
        print(f"[OK] 数据库文件存在: {db_path}")
        
        # 多worker环境下的调度器恢复机制
        try:
            recover_scheduler_in_multiworker()
        except Exception as e:
            print(f"[WARN] 调度器恢复检查失败: {e}")

def recover_scheduler_in_multiworker():
    """多worker环境下的调度器恢复机制"""
    import time
    
    current_pid = os.getpid()
    lock_file_path = '/app/data/scheduler.lock'
    
    print(f"[Worker {current_pid}] 检查调度器状态...")
    
    # 检查当前调度器是否运行
    if scheduler.running:
        print(f"[Worker {current_pid}] 调度器已在本进程中运行")
        return
    
    # 检查锁文件
    if os.path.exists(lock_file_path):
        try:
            with open(lock_file_path, 'r') as f:
                content = f.read().strip()
                import json
                lock_data = json.loads(content)
                locked_pid = lock_data.get('pid')
                last_heartbeat = lock_data.get('last_heartbeat', 0)
                
            # 检查锁定进程是否还活着
            current_time = time.time()
            heartbeat_age = current_time - last_heartbeat
            
            if heartbeat_age > 90:  # 1.5分钟没有心跳，认为进程已死
                print(f"[Worker {current_pid}] 检测到僵死锁文件，PID:{locked_pid}，心跳超时:{heartbeat_age:.0f}秒")
                os.remove(lock_file_path)
                print(f"[Worker {current_pid}] 已清理僵死锁文件")
                # 同时清理RQ调度标记,确保重启后自动恢复订阅
                rq_schedule_flag_file = '/app/data/rq_schedule_init_done'
                if os.path.exists(rq_schedule_flag_file):
                    os.remove(rq_schedule_flag_file)
                    print(f"[Worker {current_pid}] 已清理RQ调度标记，重启后将自动恢复订阅")
            else:
                print(f"[Worker {current_pid}] 调度器运行在PID:{locked_pid}，心跳正常")
                return
                
        except (json.JSONDecodeError, KeyError, FileNotFoundError):
            print(f"[Worker {current_pid}] 锁文件格式异常，清理中...")
            try:
                os.remove(lock_file_path)
                # 同时清理RQ调度标记
                rq_schedule_flag_file = '/app/data/rq_schedule_init_done'
                if os.path.exists(rq_schedule_flag_file):
                    os.remove(rq_schedule_flag_file)
                    print(f"[Worker {current_pid}] 已清理RQ调度标记")
            except:
                pass
    
    # 尝试启动调度器
    try:
        print(f"[Worker {current_pid}] 尝试启动调度器...")
        init_scheduler()
        if scheduler.running:
            print(f"[Worker {current_pid}] [OK] 调度器启动成功")
            # 创建新的锁文件
            create_scheduler_lock(current_pid)
        else:
            print(f"[Worker {current_pid}] [ERROR] 调度器启动失败")
    except Exception as e:
        print(f"[Worker {current_pid}] 调度器启动异常: {e}")

def create_scheduler_lock(pid):
    """创建调度器锁文件"""
    import json
    import time
    import socket
    
    lock_file_path = '/app/data/scheduler.lock'
    
    # 确保目录存在
    os.makedirs('/app/data', exist_ok=True)
    
    lock_data = {
        'pid': pid,
        'start_time': time.time(),
        'last_heartbeat': time.time(),
        'hostname': socket.gethostname()
    }
    
    try:
        with open(lock_file_path, 'w') as f:
            json.dump(lock_data, f)
        print(f"[Worker {pid}] 已创建调度器锁文件")
    except Exception as e:
        print(f"[Worker {pid}] 创建锁文件失败: {e}")

# 添加定期心跳更新
def update_scheduler_heartbeat():
    """更新调度器心跳并执行自检"""
    import time
    import json
    
    # 检查调度器状态，防止在关闭过程中执行
    try:
        if not scheduler.running:
            return
            
        # 检查执行器是否已关闭
        if hasattr(scheduler._executors, '_executors'):
            for executor in scheduler._executors.values():
                if hasattr(executor, '_pool') and executor._pool._shutdown:
                    return  # 执行器已关闭，避免提交新任务
        
    except (AttributeError, RuntimeError):
        # 调度器正在关闭或已关闭
        return
        
    lock_file_path = '/app/data/scheduler.lock'
    current_pid = os.getpid()
    
    try:
        # 1. 更新心跳
        if os.path.exists(lock_file_path):
            with open(lock_file_path, 'r') as f:
                lock_data = json.loads(f.read())
            
            # 只有锁文件的PID是当前进程才更新心跳
            if lock_data.get('pid') == current_pid:
                lock_data['last_heartbeat'] = time.time()
                with open(lock_file_path, 'w') as f:
                    json.dump(lock_data, f)
        
        # 2. 每5分钟执行一次时间自检
        if time.time() % 300 < 60:  # 每5分钟内的第一分钟执行
            scheduler_health_check()
            
    except:
        pass  # 心跳更新失败不影响主要功能

def scheduler_health_check():
    """调度器健康检查和自动修复"""
    try:
        # 如果调度器未运行，尝试自动启动
        if not scheduler.running:
            app.logger.info("[调度器健康检查] 检测到调度器未运行，尝试自动启动")
            try:
                init_scheduler()
                if scheduler.running:
                    app.logger.info("[调度器健康检查] 调度器自动启动成功")
                    log_activity('INFO', 'system', '调度器自动启动成功', None, 'localhost')
                    return
                else:
                    app.logger.warning("[调度器健康检查] 调度器自动启动失败")
                    return
            except Exception as e:
                app.logger.error(f"[调度器健康检查] 调度器启动异常: {e}")
                return
            
        jobs = scheduler.get_jobs()
        if not jobs:
            return
            
        # 检查主推送任务的下次执行时间
        push_job = None
        for job in jobs:
            if job.id == 'push_check':
                push_job = job
                break
                
        if not push_job or not push_job.next_run_time:
            return
            
        next_run_time = push_job.next_run_time
        if next_run_time.tzinfo is None:
            next_run_time = APP_TIMEZONE.localize(next_run_time)
        elif next_run_time.tzinfo != APP_TIMEZONE:
            next_run_time = next_run_time.astimezone(APP_TIMEZONE)
            
        current_time = get_current_time()
        
        # 如果下次执行时间超过12小时前，认为是时间异常
        time_diff = (current_time - next_run_time).total_seconds()
        if time_diff > 43200:  # 12小时
            app.logger.warning(f"[调度器健康检查] 发现时间异常：下次执行时间落后 {time_diff/3600:.1f} 小时")
            
            # 自动重启调度器
            app.logger.info("[调度器健康检查] 执行自动修复")
            shutdown_scheduler_safely()
            
            # 稍等片刻再重启
            time.sleep(1)
            init_scheduler()
            
            if scheduler.running:
                app.logger.info("[调度器健康检查] 自动修复完成")
                # 记录修复事件
                log_activity('INFO', 'system', '调度器时间异常已自动修复', None, 'localhost')
            else:
                app.logger.error("[调度器健康检查] 自动修复失败")
                
    except Exception as e:
        app.logger.error(f"[调度器健康检查] 检查失败: {e}")

# 应用初始化执行
try:
    initialize_app()
except Exception as e:
    print(f"应用初始化警告: {e}")
