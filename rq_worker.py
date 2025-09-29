#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RQ Worker 启动脚本
用于启动RQ任务处理进程
"""

import os
import sys
import logging
from rq import Worker, Queue, Connection
from rq_config import redis_conn, high_priority_queue, default_queue, low_priority_queue

# 添加当前目录到Python路径
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)

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
        
        # 创建Worker
        with Connection(redis_conn):
            worker = Worker(queues, name=worker_name)
            logger.info(f"Worker {worker_name} 启动成功")
            
            # 开始工作循环
            worker.work(with_scheduler=True)
            
    except KeyboardInterrupt:
        logger.info("收到中断信号，正在关闭Worker...")
        
    except Exception as e:
        logger.error(f"Worker启动失败: {e}")
        sys.exit(1)

if __name__ == '__main__':
    main()