#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RQ Scheduler启动脚本
用于监控延迟任务并在指定时间将任务移入执行队列
支持动态重载配置，无需重启容器
"""

import os
import sys
import logging
import time
import signal
from datetime import datetime
from threading import Thread, Event

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

    # 全局状态
    shutdown_event = Event()
    current_interval = {'value': get_scheduler_interval()}

    def signal_handler(signum, frame):
        """处理退出信号"""
        logger.info(f"收到信号 {signum}，准备优雅关闭...")
        shutdown_event.set()

    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    def config_monitor():
        """配置监控线程：每60秒检查配置变化"""
        last_check = time.time()

        while not shutdown_event.is_set():
            try:
                # 每60秒检查一次配置
                time.sleep(min(60, max(1, 60 - (time.time() - last_check))))
                last_check = time.time()

                # 重新读取配置
                new_interval = get_scheduler_interval()

                if new_interval != current_interval['value']:
                    logger.warning("=" * 60)
                    logger.warning(f"⚠️  检测到配置变化！")
                    logger.warning(f"旧值: {current_interval['value']}秒 ({current_interval['value']/60:.1f}分钟)")
                    logger.warning(f"新值: {new_interval}秒 ({new_interval/60:.1f}分钟)")
                    logger.warning("配置已更新，将在下次扫描周期生效")
                    logger.warning("=" * 60)
                    current_interval['value'] = new_interval

            except Exception as e:
                logger.error(f"配置监控异常: {e}", exc_info=True)

    # 启动配置监控线程
    monitor_thread = Thread(target=config_monitor, daemon=True, name="ConfigMonitor")
    monitor_thread.start()
    logger.info("✅ 配置动态监控已启动（每60秒检查）")

    # 获取调度器参数
    scheduler_name = os.environ.get('RQ_SCHEDULER_NAME', 'pubmed-scheduler')

    logger.info("=" * 60)
    logger.info(f"启动RQ Scheduler: {scheduler_name}")
    logger.info(f"初始检查间隔: {current_interval['value']}秒 ({current_interval['value']/60:.1f}分钟)")
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

        # 创建自定义Scheduler支持动态间隔
        class DynamicScheduler(Scheduler):
            def __init__(self, *args, interval_getter=None, **kwargs):
                self.interval_getter = interval_getter
                super().__init__(*args, **kwargs)

            def run(self, burst=False):
                """重写run方法支持动态间隔"""
                self.log.info('Scheduler starting...')
                self._install_signal_handlers()

                try:
                    while True:
                        if self.interval_getter:
                            # 动态获取最新间隔
                            self.interval = self.interval_getter()

                        self.enqueue_jobs()
                        self.clean_registries()

                        if burst:
                            break

                        if shutdown_event.is_set():
                            self.log.info('收到关闭信号')
                            break

                        time.sleep(self.interval)

                except KeyboardInterrupt:
                    self.log.info('Scheduler interrupted')

        # 创建Scheduler实例
        scheduler = DynamicScheduler(
            connection=redis_conn,
            interval=current_interval['value'],
            interval_getter=lambda: current_interval['value'],  # 动态获取当前配置
            name=scheduler_name
        )

        logger.info("✅ RQ Scheduler初始化成功（支持动态配置）")
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

    finally:
        shutdown_event.set()
        logger.info("Scheduler已关闭")

if __name__ == '__main__':
    main()