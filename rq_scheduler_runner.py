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

def main():
    setup_logging()
    logger = logging.getLogger(__name__)

    # 获取调度器参数
    scheduler_name = os.environ.get('RQ_SCHEDULER_NAME', 'pubmed-scheduler')
    check_interval = int(os.environ.get('RQ_SCHEDULER_INTERVAL', '10'))  # 默认10秒

    logger.info("=" * 60)
    logger.info(f"启动RQ Scheduler: {scheduler_name}")
    logger.info(f"检查间隔: {check_interval}秒")
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