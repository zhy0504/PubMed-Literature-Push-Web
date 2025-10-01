# -*- coding: utf-8 -*-
"""
RQ (Redis Queue) 配置和任务队列管理
使用RQ 1.15+ 原生调度功能，无需rq-scheduler
支持Worker内置scheduler进行精确的定时任务调度
"""

import os
import redis
from rq import Queue, Worker, Connection
from rq.job import Job
from rq.registry import ScheduledJobRegistry
import datetime
import logging
from typing import Optional, List

# Redis连接配置
REDIS_URL = os.environ.get('REDIS_URL', 'redis://localhost:6379/0')
redis_conn = redis.from_url(REDIS_URL)

# 创建不同优先级的队列
high_priority_queue = Queue('high', connection=redis_conn)  # 高优先级：立即推送
default_queue = Queue('default', connection=redis_conn)     # 默认：定时推送
low_priority_queue = Queue('low', connection=redis_conn)    # 低优先级：统计分析

# 获取调度任务注册表
scheduled_registry = ScheduledJobRegistry(queue=default_queue)

logging.info("✅ 使用RQ原生调度功能（Worker --with-scheduler）")

def get_redis_connection():
    """获取Redis连接"""
    return redis_conn

def get_queue(priority='default'):
    """根据优先级获取队列"""
    queues = {
        'high': high_priority_queue,
        'default': default_queue,
        'low': low_priority_queue
    }
    return queues.get(priority, default_queue)

def enqueue_job(func, *args, priority='default', **kwargs):
    """将任务加入队列"""
    queue = get_queue(priority)
    return queue.enqueue(func, *args, **kwargs)

def enqueue_at(func, run_at: datetime.datetime, *args, priority='default', **kwargs):
    """在指定时间执行任务（使用RQ原生API）"""
    queue = get_queue(priority)
    return queue.enqueue_at(run_at, func, *args, **kwargs)

def enqueue_in(func, delay: int, *args, priority='default', **kwargs):
    """延迟指定秒数后执行任务"""
    queue = get_queue(priority)
    return queue.enqueue_in(delay, func, *args, **kwargs)

def schedule_subscription_push(subscription_id: int, run_at: datetime.datetime):
    """调度订阅推送任务"""
    # 动态导入避免循环依赖
    def _import_task():
        from tasks import process_subscription_push
        return process_subscription_push
    
    job_id = f'push_subscription_{subscription_id}_{run_at.strftime("%Y%m%d_%H%M")}'
    
    # 先取消已有的同类任务
    cancel_subscription_jobs(subscription_id)
    
    # 调度新任务
    task_func = _import_task()
    return enqueue_at(task_func, run_at, subscription_id, job_id=job_id)

def cancel_subscription_jobs(subscription_id: int):
    """取消订阅的所有待执行任务（使用RQ原生Registry）"""
    cancelled_count = 0

    try:
        # 1. 取消scheduled_job_registry中的调度任务
        for queue in [high_priority_queue, default_queue, low_priority_queue]:
            registry = ScheduledJobRegistry(queue=queue)
            for job_id in list(registry.get_job_ids()):
                if job_id.startswith(f'push_subscription_{subscription_id}_'):
                    try:
                        job = Job.fetch(job_id, connection=redis_conn)
                        job.cancel()
                        registry.remove(job)
                        cancelled_count += 1
                        logging.info(f"已取消调度任务: {job_id}")
                    except Exception as e:
                        logging.warning(f"取消调度任务 {job_id} 失败: {e}")

        # 2. 取消队列中的延迟任务
        for queue in [high_priority_queue, default_queue, low_priority_queue]:
            # 获取延迟任务注册表
            registry = queue.deferred_job_registry
            for job_id in list(registry.get_job_ids()):
                if job_id.startswith(f'push_subscription_{subscription_id}_'):
                    try:
                        job = Job.fetch(job_id, connection=redis_conn)
                        job.cancel()
                        cancelled_count += 1
                        logging.info(f"已取消延迟任务: {job_id}")
                    except Exception as e:
                        logging.warning(f"取消延迟任务 {job_id} 失败: {e}")

        logging.info(f"订阅 {subscription_id} 共取消 {cancelled_count} 个任务")
        return cancelled_count

    except Exception as e:
        logging.error(f"取消订阅 {subscription_id} 的任务时发生异常: {e}")
        return cancelled_count

def get_queue_info():
    """获取队列状态信息（使用RQ原生Registry）"""
    # 统计所有队列的scheduled任务
    scheduled_count = 0
    for queue in [high_priority_queue, default_queue, low_priority_queue]:
        registry = ScheduledJobRegistry(queue=queue)
        scheduled_count += len(registry)

    return {
        'high': {
            'length': len(high_priority_queue),
            'scheduled': len(ScheduledJobRegistry(queue=high_priority_queue)),
            'deferred': len(high_priority_queue.deferred_job_registry),
            'failed': len(high_priority_queue.failed_job_registry),
            'finished': len(high_priority_queue.finished_job_registry)
        },
        'default': {
            'length': len(default_queue),
            'scheduled': len(ScheduledJobRegistry(queue=default_queue)),
            'deferred': len(default_queue.deferred_job_registry),
            'failed': len(default_queue.failed_job_registry),
            'finished': len(default_queue.finished_job_registry)
        },
        'low': {
            'length': len(low_priority_queue),
            'scheduled': len(ScheduledJobRegistry(queue=low_priority_queue)),
            'deferred': len(low_priority_queue.deferred_job_registry),
            'failed': len(low_priority_queue.failed_job_registry),
            'finished': len(low_priority_queue.finished_job_registry)
        },
        'total_scheduled': scheduled_count
    }

def get_failed_jobs():
    """获取失败的任务"""
    failed_jobs = []
    for queue in [high_priority_queue, default_queue, low_priority_queue]:
        registry = queue.failed_job_registry
        for job_id in registry.get_job_ids():
            try:
                job = Job.fetch(job_id, connection=redis_conn)
                failed_jobs.append({
                    'id': job.id,
                    'func_name': job.func_name,
                    'args': job.args,
                    'created_at': job.created_at.isoformat() if job.created_at else None,
                    'failed_at': job.failed_at.isoformat() if job.failed_at else None,
                    'exc_info': str(job.exc_info) if job.exc_info else None
                })
            except:
                continue
    return failed_jobs

def get_deferred_jobs():
    """获取延迟任务列表"""
    deferred_jobs = []
    for queue in [high_priority_queue, default_queue, low_priority_queue]:
        registry = queue.deferred_job_registry
        for job_id in registry.get_job_ids():
            try:
                job = Job.fetch(job_id, connection=redis_conn)
                deferred_jobs.append({
                    'id': job.id,
                    'queue': queue.name,
                    'func_name': job.func_name,
                    'args': job.args,
                    'created_at': job.created_at.isoformat() if job.created_at else None,
                    'enqueued_at': job.enqueued_at.isoformat() if job.enqueued_at else None,
                })
            except:
                continue
    return deferred_jobs

def requeue_failed_job(job_id: str):
    """重新排队失败的任务"""
    try:
        job = Job.fetch(job_id, connection=redis_conn)
        job.requeue()
        return True
    except:
        return False

def clear_failed_jobs():
    """清空失败任务"""
    for queue in [high_priority_queue, default_queue, low_priority_queue]:
        registry = queue.failed_job_registry
        registry.requeue(*registry.get_job_ids())

class RQConfig:
    """RQ配置类"""
    REDIS_URL = REDIS_URL
    QUEUES = ['high', 'default', 'low']

    # Worker配置
    WORKER_CONNECTION_KWARGS = {'decode_responses': True}
    WORKER_TTL = 500  # 任务超时时间(秒)
    RESULT_TTL = 3600  # 结果保存时间(秒)

    # RQ原生调度器配置（Worker --with-scheduler）
    # 调度器每1秒自动检查scheduled_job_registry
    # 无需手动配置扫描间隔

    @classmethod
    def init_app(cls, app):
        """初始化Flask应用配置"""
        app.config.setdefault('RQ_REDIS_URL', cls.REDIS_URL)
        app.config.setdefault('RQ_QUEUES', cls.QUEUES)

if __name__ == '__main__':
    # 测试Redis连接
    try:
        redis_conn.ping()
        print("✅ Redis连接测试成功")
        print(f"Redis URL: {REDIS_URL}")
        print(f"队列信息: {get_queue_info()}")
    except Exception as e:
        print(f"❌ Redis连接测试失败: {e}")