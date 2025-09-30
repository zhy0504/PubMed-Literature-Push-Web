# -*- coding: utf-8 -*-
"""
RQ (Redis Queue) 配置和任务队列管理
用于替代APScheduler的定时推送功能
支持RQ Scheduler进行精确的定时任务调度
"""

import os
import redis
from rq import Queue, Worker, Connection
from rq.job import Job
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

# 尝试导入RQ Scheduler（如果已安装）
try:
    from rq_scheduler import Scheduler
    USE_RQ_SCHEDULER = True
    scheduler = Scheduler(connection=redis_conn)
    logging.info("✅ 使用RQ Scheduler进行任务调度")
except ImportError:
    USE_RQ_SCHEDULER = False
    logging.warning("⚠️ rq-scheduler未安装，使用简化调度器。建议安装: pip install rq-scheduler")

    # 简化版调度器作为备用
    class SimpleScheduler:
        """简化版RQ调度器（备用）"""
        def __init__(self, connection):
            self.connection = connection
            self.scheduled_jobs = []

        def enqueue_at(self, run_at: datetime.datetime, func, *args, job_id=None, **kwargs):
            """在指定时间执行任务"""
            delay = (run_at - datetime.datetime.now()).total_seconds()
            if delay > 0:
                queue = get_queue('default')
                return queue.enqueue_in(int(delay), func, *args, job_id=job_id, **kwargs)
            else:
                # 立即执行
                queue = get_queue('high')
                return queue.enqueue(func, *args, job_id=job_id, **kwargs)

        def get_jobs(self):
            """获取调度任务（简化实现）"""
            return self.scheduled_jobs

    scheduler = SimpleScheduler(redis_conn)

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
    """在指定时间执行任务"""
    return scheduler.enqueue_at(run_at, func, *args, **kwargs)

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
    """取消订阅的所有待执行任务"""
    # 简化实现：遍历队列查找相关任务
    try:
        for queue in [high_priority_queue, default_queue, low_priority_queue]:
            # 获取延迟任务注册表
            registry = queue.deferred_job_registry
            for job_id in registry.get_job_ids():
                if job_id.startswith(f'push_subscription_{subscription_id}_'):
                    try:
                        job = Job.fetch(job_id, connection=redis_conn)
                        job.cancel()
                    except:
                        pass
    except Exception:
        pass

def get_queue_info():
    """获取队列状态信息"""
    # 获取调度任务数量（兼容生成器）
    scheduled_jobs = scheduler.get_jobs()
    scheduled_count = len(list(scheduled_jobs)) if hasattr(scheduled_jobs, '__iter__') else 0

    return {
        'high': {
            'length': len(high_priority_queue),
            'deferred': len(high_priority_queue.deferred_job_registry),
            'failed': len(high_priority_queue.failed_job_registry),
            'finished': len(high_priority_queue.finished_job_registry)
        },
        'default': {
            'length': len(default_queue),
            'deferred': len(default_queue.deferred_job_registry),
            'failed': len(default_queue.failed_job_registry),
            'finished': len(default_queue.finished_job_registry)
        },
        'low': {
            'length': len(low_priority_queue),
            'deferred': len(low_priority_queue.deferred_job_registry),
            'failed': len(low_priority_queue.failed_job_registry),
            'finished': len(low_priority_queue.finished_job_registry)
        },
        'scheduled': scheduled_count
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
    
    # 调度器配置
    SCHEDULER_INTERVAL = 60  # 调度器检查间隔(秒)
    
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