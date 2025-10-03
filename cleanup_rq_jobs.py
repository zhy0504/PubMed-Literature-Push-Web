#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
RQ任务清理工具
清除已删除订阅的遗留任务
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from rq_config import redis_conn, high_priority_queue, default_queue, low_priority_queue
from rq.registry import ScheduledJobRegistry
from rq.job import Job
from flask import Flask
from flask_sqlalchemy import SQLAlchemy

# 创建应用和数据库连接
app = Flask(__name__)

# 支持Docker和本地环境
if os.path.exists('/app/data'):
    # Docker环境
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:////app/data/pubmed_app.db'
else:
    # 本地环境
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///pubmed_app.db'

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# 订阅模型（简化版）
class Subscription(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, nullable=False)
    keywords = db.Column(db.String(500), nullable=False)

def cleanup_orphaned_jobs():
    """清理已删除订阅的遗留任务"""
    with app.app_context():
        print("=== RQ任务清理工具 ===\n")

        # 获取所有有效的订阅ID
        valid_subscription_ids = set()
        subscriptions = Subscription.query.all()
        for sub in subscriptions:
            valid_subscription_ids.add(sub.id)

        print(f"当前有效订阅数: {len(valid_subscription_ids)}")
        print(f"有效订阅ID: {sorted(valid_subscription_ids)}\n")

        removed_count = 0
        all_jobs = []

        # 扫描所有队列的scheduled任务
        print("【扫描调度任务】")
        for queue_name, queue in [('高优先级', high_priority_queue), ('默认', default_queue), ('低优先级', low_priority_queue)]:
            registry = ScheduledJobRegistry(queue=queue)
            job_ids = list(registry.get_job_ids())
            print(f"  {queue_name}队列: {len(job_ids)} 个调度任务")

            for job_id in job_ids:
                # 解析任务ID中的订阅ID
                if job_id.startswith('push_subscription_'):
                    try:
                        parts = job_id.split('_')
                        if len(parts) >= 3:
                            subscription_id = int(parts[2])
                            job = Job.fetch(job_id, connection=redis_conn)

                            all_jobs.append({
                                'id': job_id,
                                'subscription_id': subscription_id,
                                'queue': queue_name,
                                'valid': subscription_id in valid_subscription_ids
                            })

                            # 如果订阅已删除，清理任务
                            if subscription_id not in valid_subscription_ids:
                                job.cancel()
                                registry.remove(job_id)
                                removed_count += 1
                                print(f"    ✗ 已删除: {job_id} (订阅{subscription_id}不存在)")
                    except Exception as e:
                        print(f"    ! 处理任务 {job_id} 失败: {e}")

        print(f"\n【清理结果】")
        print(f"  扫描任务总数: {len(all_jobs)}")
        print(f"  有效任务: {sum(1 for j in all_jobs if j['valid'])}")
        print(f"  已清理遗留任务: {removed_count}")

        # 显示剩余任务详情
        if all_jobs:
            print(f"\n【剩余任务详情】")
            valid_jobs = [j for j in all_jobs if j['valid']]
            for job in valid_jobs:
                print(f"  订阅{job['subscription_id']}: {job['id']} ({job['queue']}队列)")

        # 按订阅ID分组统计
        if valid_jobs:
            print(f"\n【按订阅统计】")
            from collections import Counter
            counter = Counter(j['subscription_id'] for j in valid_jobs)
            for sub_id, count in sorted(counter.items()):
                print(f"  订阅 {sub_id}: {count} 个任务")

        print(f"\n[OK] 清理完成!")

if __name__ == '__main__':
    cleanup_orphaned_jobs()
