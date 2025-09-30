#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
一次性初始化脚本 - 批量调度所有活跃订阅到RQ Scheduler
适用于Gunicorn部署环境,在应用启动后手动执行一次
"""

import os
import sys

# 添加当前目录到Python路径
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)

def main():
    print("=" * 60)
    print("RQ调度器初始化脚本")
    print("=" * 60)

    try:
        # 导入Flask应用上下文
        from app import app
        from rq_config import redis_conn, enqueue_job
        from tasks import batch_schedule_all_subscriptions

        # 测试Redis连接
        print("1. 检查Redis连接...")
        redis_conn.ping()
        print("   ✅ Redis连接正常")

        # 触发批量调度
        print("\n2. 触发批量调度所有活跃订阅...")
        with app.app_context():
            job = enqueue_job(batch_schedule_all_subscriptions, priority='high')
            print(f"   ✅ 批量调度任务已排队: {job.id}")

            # 等待任务执行
            print("\n3. 等待任务执行...")
            import time
            max_wait = 30  # 最多等待30秒
            elapsed = 0

            while elapsed < max_wait:
                job.refresh()
                status = job.get_status()

                if status == 'finished':
                    result = job.result
                    print(f"   ✅ 批量调度完成!")
                    print(f"   - 总订阅数: {result.get('total', 0)}")
                    print(f"   - 已调度数: {result.get('scheduled', 0)}")
                    break
                elif status == 'failed':
                    print(f"   ❌ 任务执行失败: {job.exc_info}")
                    return 1
                else:
                    print(f"   ⏳ 任务状态: {status} (已等待{elapsed}秒)")
                    time.sleep(2)
                    elapsed += 2

            if elapsed >= max_wait:
                print(f"   ⚠️ 任务仍在执行中,请稍后通过RQ Dashboard查看结果")

        print("\n" + "=" * 60)
        print("初始化完成!")
        print("=" * 60)
        return 0

    except ImportError as e:
        print(f"❌ 导入失败: {e}")
        print("请确保在应用启动后执行此脚本")
        return 1

    except Exception as e:
        print(f"❌ 初始化失败: {e}")
        import traceback
        traceback.print_exc()
        return 1

if __name__ == '__main__':
    sys.exit(main())