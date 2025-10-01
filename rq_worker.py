#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RQ Worker 启动脚本
用于启动RQ任务处理进程
"""

import os
import sys
import signal
import logging
from rq import Worker, Queue, Connection
from rq_config import redis_conn, high_priority_queue, default_queue, low_priority_queue

# 全局变量用于优雅关闭
shutdown_requested = False

# 添加当前目录到Python路径
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)

def signal_handler(signum, frame):
    """处理关闭信号"""
    global shutdown_requested
    logger = logging.getLogger(__name__)
    logger.info(f"收到信号 {signum}，准备优雅关闭...")
    shutdown_requested = True

def setup_logging():
    """设置日志"""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler('/app/logs/rq_worker.log'),
            logging.StreamHandler(sys.stdout)
        ]
    )

def main():
    setup_logging()
    logger = logging.getLogger(__name__)

    # 注册信号处理器
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    # 获取工作进程参数
    worker_name = os.environ.get('RQ_WORKER_NAME', 'pubmed-worker')
    queues_to_listen = os.environ.get('RQ_QUEUES', 'high,default,low').split(',')
    
    logger.info(f"启动RQ Worker: {worker_name}")
    logger.info(f"监听队列: {queues_to_listen}")
    
    # 创建队列对象列表
    queues = []
    queue_map = {
        'high': high_priority_queue,
        'default': default_queue,
        'low': low_priority_queue
    }
    
    for queue_name in queues_to_listen:
        queue_name = queue_name.strip()
        if queue_name in queue_map:
            queues.append(queue_map[queue_name])
            logger.info(f"添加队列: {queue_name}")
    
    if not queues:
        logger.error("没有找到有效的队列，退出")
        sys.exit(1)
    
    try:
        # 测试Redis连接
        redis_conn.ping()
        logger.info("Redis连接正常")

        # 清理可能存在的同名 Worker 注册信息
        try:
            # 检查是否存在同名 Worker
            worker_key = f'rq:worker:{worker_name}'
            if redis_conn.exists(worker_key):
                logger.warning(f"检测到已存在的 Worker 注册: {worker_name}，正在清理...")
                # 从 workers 集合中移除
                redis_conn.srem('rq:workers', worker_key)
                # 删除相关键
                keys_to_delete = [
                    worker_key,
                    f'{worker_key}:birth',
                    f'{worker_key}:started',
                    f'{worker_key}:current_job'
                ]
                for key in keys_to_delete:
                    redis_conn.delete(key)
                logger.info(f"已清理旧的 Worker 注册信息: {worker_name}")
        except Exception as e:
            logger.warning(f"清理旧 Worker 注册失败（忽略）: {e}")

        # 创建Worker
        with Connection(redis_conn):
            worker = Worker(queues, name=worker_name)
            logger.info(f"Worker {worker_name} 启动成功")

            # 开始工作循环
            worker.work(with_scheduler=True)

    except KeyboardInterrupt:
        logger.info("收到键盘中断信号，正在关闭Worker...")

    except Exception as e:
        # 区分Redis连接错误和其他错误
        if "Connection refused" in str(e) or "Connection closed" in str(e):
            if shutdown_requested:
                logger.info("检测到优雅关闭信号，正常退出")
                sys.exit(0)
            else:
                logger.error(f"Redis连接失败: {e}")
                sys.exit(1)
        else:
            logger.error(f"Worker启动失败: {e}")
            sys.exit(1)

if __name__ == '__main__':
    main()