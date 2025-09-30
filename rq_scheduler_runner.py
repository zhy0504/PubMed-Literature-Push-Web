#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RQ Scheduler启动脚本
用于监控延迟任务并在指定时间将任务移入执行队列
"""

import os
import sys
import logging
from datetime import datetime

# 添加当前目录到Python路径
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)

def setup_logging():
    """设置日志"""
    log_file = os.environ.get('LOG_FILE', '/app/logs/rq_scheduler.log')
    log_level = os.environ.get('LOG_LEVEL', 'INFO')

    # 确保日志目录存在
    log_dir = os.path.dirname(log_file)
    if log_dir and not os.path.exists(log_dir):
        os.makedirs(log_dir, exist_ok=True)

    logging.basicConfig(
        level=getattr(logging, log_level.upper(), logging.INFO),
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler(log_file),
            logging.StreamHandler(sys.stdout)
        ]
    )

def get_scheduler_interval():
    """获取调度器扫描间隔配置

    优先级:
    1. 环境变量 RQ_SCHEDULER_INTERVAL (直接秒数)
    2. 数据库配置 push_check_frequency (小时数，转换为秒)
    3. 默认值 300秒 (5分钟)
    """
    # 1. 优先使用环境变量（保持向后兼容）
    env_interval = os.environ.get('RQ_SCHEDULER_INTERVAL')
    if env_interval:
        return int(env_interval)

    # 2. 尝试从数据库读取配置
    try:
        from app import app, SystemSetting
        with app.app_context():
            # push_check_frequency 存储的是小时数，需要转换为秒
            hours = float(SystemSetting.get_setting('push_check_frequency', '0.0833'))  # 默认5分钟 = 0.0833小时
            seconds = int(hours * 3600)
            # 限制范围：最小60秒，最大86400秒(24小时)
            return max(60, min(seconds, 86400))
    except Exception as e:
        logging.warning(f"无法从数据库读取配置: {e}，使用默认值")

    # 3. 默认值：300秒 (5分钟)
    return 300

def main():
    setup_logging()
    logger = logging.getLogger(__name__)

    # 获取调度器参数
    scheduler_name = os.environ.get('RQ_SCHEDULER_NAME', 'pubmed-scheduler')
    check_interval = get_scheduler_interval()

    logger.info("=" * 60)
    logger.info(f"启动RQ Scheduler: {scheduler_name}")
    logger.info(f"检查间隔: {check_interval}秒 ({check_interval/60:.1f}分钟)")
    logger.info(f"配置来源: {'环境变量' if os.environ.get('RQ_SCHEDULER_INTERVAL') else '数据库配置'}")
    logger.info(f"当前时间: {datetime.now()}")
    logger.info("=" * 60)

    try:
        # 导入RQ Scheduler
        from rq_scheduler import Scheduler
        from rq_config import redis_conn

        # 测试Redis连接
        redis_conn.ping()
        logger.info("✅ Redis连接正常")

        # 创建Scheduler实例
        scheduler = Scheduler(
            connection=redis_conn,
            interval=check_interval,  # 检查间隔（秒）
            name=scheduler_name
        )

        logger.info("✅ RQ Scheduler初始化成功")
        logger.info("开始监控延迟任务...")

        # 开始运行（阻塞）
        scheduler.run()

    except ImportError as e:
        logger.error(f"❌ 导入rq-scheduler失败: {e}")
        logger.error("请确保已安装: pip install rq-scheduler")
        sys.exit(1)

    except KeyboardInterrupt:
        logger.info("收到中断信号，正在关闭Scheduler...")

    except Exception as e:
        logger.error(f"❌ Scheduler运行失败: {e}", exc_info=True)
        sys.exit(1)

if __name__ == '__main__':
    main()