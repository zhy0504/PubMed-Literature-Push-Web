# -*- coding: utf-8 -*-
"""
RQ任务定义模块
包含所有异步任务的具体实现
"""

import os
import sys
import datetime
from typing import Optional

# 确保app模块可以被导入
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)

# 在任务执行时需要Flask应用上下文
from flask import Flask
from app import app, db, User, Subscription, beijing_now
# 延迟导入避免循环导入问题
from app import log_activity, SystemSetting, push_service
import logging

def process_subscription_push(subscription_id: int):
    """
    处理单个订阅推送任务
    这是RQ任务队列中执行的核心函数
    """
    with app.app_context():  # 确保有Flask应用上下文
        try:
            # 获取订阅信息
            subscription = Subscription.query.get(subscription_id)
            if not subscription:
                error_msg = f"订阅 {subscription_id} 不存在"
                logging.error(f"[RQ任务] {error_msg}")
                return {"status": "error", "message": error_msg}

            if not subscription.is_active:
                info_msg = f"订阅 {subscription_id} 已禁用，跳过推送"
                logging.info(f"[RQ任务] {info_msg}")
                return {"status": "skipped", "message": info_msg}

            user = subscription.user
            if not user or not user.is_active:
                error_msg = f"订阅 {subscription_id} 的用户不存在或已禁用"
                logging.error(f"[RQ任务] {error_msg}")
                return {"status": "error", "message": error_msg}

            start_time = datetime.datetime.now()
            logging.info(f"[RQ任务] 开始处理订阅 {subscription_id} (用户: {user.email})")

            # 调用推送服务处理订阅
            result = push_service.process_single_subscription(subscription_id)

            end_time = datetime.datetime.now()
            duration = (end_time - start_time).total_seconds()

            if result and result.get('success'):
                articles_count = result.get('articles_found', 0)
                if articles_count > 0:
                    success_msg = f"订阅 {subscription_id} 推送成功: {articles_count} 篇文章"
                    log_activity('INFO', 'rq_push', success_msg)
                    logging.info(f"[RQ任务] {success_msg} (耗时: {duration:.2f}秒)")
                else:
                    info_msg = f"订阅 {subscription_id} 无新文章"
                    logging.info(f"[RQ任务] {info_msg} (耗时: {duration:.2f}秒)")

                # 调度下次推送
                schedule_next_push_for_subscription(subscription)

                return {
                    "status": "success",
                    "subscription_id": subscription_id,
                    "articles_count": articles_count,
                    "duration": duration
                }
            else:
                error_msg = result.get('error', '推送服务返回失败') if result else '推送服务返回空结果'
                log_activity('ERROR', 'rq_push', f"订阅 {subscription_id} 推送失败: {error_msg}")
                logging.error(f"[RQ任务] 订阅 {subscription_id} 推送失败: {error_msg} (耗时: {duration:.2f}秒)")

                # 推送失败也要调度下次推送，避免订阅停止
                schedule_next_push_for_subscription(subscription)

                return {
                    "status": "error",
                    "subscription_id": subscription_id,
                    "message": error_msg,
                    "duration": duration
                }
                
        except Exception as e:
            error_msg = f"订阅 {subscription_id} 推送异常: {str(e)}"
            log_activity('ERROR', 'rq_push', error_msg)
            logging.error(f"[RQ任务] {error_msg}")
            
            # 推送失败也要调度下次推送，避免订阅停止
            try:
                subscription = Subscription.query.get(subscription_id)
                if subscription:
                    schedule_next_push_for_subscription(subscription)
            except:
                pass
                
            return {"status": "error", "message": error_msg}

def schedule_next_push_for_subscription(subscription):
    """为订阅调度下次推送任务"""
    try:
        from rq_config import schedule_subscription_push
        
        next_push_time = calculate_next_push_time(subscription)
        if next_push_time:
            schedule_subscription_push(subscription.id, next_push_time)
            logging.info(f"[RQ调度] 订阅 {subscription.id} 下次推送时间: {next_push_time}")
        else:
            logging.warning(f"[RQ调度] 订阅 {subscription.id} 无法计算下次推送时间")
            
    except Exception as e:
        logging.error(f"[RQ调度] 为订阅 {subscription.id} 调度下次推送失败: {e}")

def calculate_next_push_time(subscription) -> Optional[datetime.datetime]:
    """计算订阅的下次推送时间"""
    try:
        current_time = beijing_now()
        
        # 解析推送时间
        if not subscription.push_time:
            push_hour, push_minute = 9, 0  # 默认9:00
        else:
            try:
                push_hour, push_minute = map(int, subscription.push_time.split(':'))
            except:
                push_hour, push_minute = 9, 0
        
        # 根据推送频率计算下次时间
        if subscription.push_frequency == 'daily':
            next_time = current_time.replace(hour=push_hour, minute=push_minute, second=0, microsecond=0)
            if next_time <= current_time:
                next_time += datetime.timedelta(days=1)
            return next_time
            
        elif subscription.push_frequency == 'weekly':
            # 获取目标星期几
            weekday_map = {
                'monday': 0, 'tuesday': 1, 'wednesday': 2, 'thursday': 3,
                'friday': 4, 'saturday': 5, 'sunday': 6
            }
            target_weekday = weekday_map.get(subscription.push_day or 'monday', 0)
            
            next_time = current_time.replace(hour=push_hour, minute=push_minute, second=0, microsecond=0)
            days_ahead = target_weekday - current_time.weekday()
            
            if days_ahead <= 0 or (days_ahead == 0 and next_time <= current_time):
                days_ahead += 7
                
            next_time += datetime.timedelta(days=days_ahead)
            return next_time
            
        elif subscription.push_frequency == 'monthly':
            # 每月指定日期
            target_day = subscription.push_month_day or 1
            
            next_time = current_time.replace(day=target_day, hour=push_hour, minute=push_minute, second=0, microsecond=0)
            if next_time <= current_time:
                # 下个月的同一天
                if current_time.month == 12:
                    next_time = next_time.replace(year=current_time.year + 1, month=1)
                else:
                    try:
                        next_time = next_time.replace(month=current_time.month + 1)
                    except ValueError:
                        # 处理月份天数不足的情况（如31号到2月）
                        next_time = next_time.replace(month=current_time.month + 1, day=1)
                        
            return next_time
        
        return None
        
    except Exception as e:
        logging.error(f"计算订阅 {subscription.id} 下次推送时间失败: {e}")
        return None

def batch_schedule_all_subscriptions():
    """批量调度所有活跃订阅"""
    with app.app_context():
        try:
            subscriptions = Subscription.query.filter_by(is_active=True).join(User).filter_by(is_active=True).all()
            scheduled_count = 0

            for subscription in subscriptions:
                try:
                    next_push_time = calculate_next_push_time(subscription)
                    if next_push_time:
                        from rq_config import schedule_subscription_push
                        schedule_subscription_push(subscription.id, next_push_time)
                        scheduled_count += 1
                except Exception as e:
                    logging.error(f"调度订阅 {subscription.id} 失败: {e}")

            log_activity('INFO', 'rq_schedule', f'批量调度完成: {scheduled_count}/{len(subscriptions)} 个订阅')
            logging.info(f"[RQ批量调度] 成功调度 {scheduled_count}/{len(subscriptions)} 个订阅")

            # 批量调度成功后创建标记文件
            try:
                import time
                rq_schedule_flag_file = '/app/data/rq_schedule_init_done'
                with open(rq_schedule_flag_file, 'w') as f:
                    f.write(f"{os.getpid()}|{int(time.time())}")
                logging.info(f"[RQ批量调度] 已创建调度标记文件")
            except Exception as e:
                logging.warning(f"[RQ批量调度] 创建标记文件失败: {e}")

            return {
                "status": "success",
                "total": len(subscriptions),
                "scheduled": scheduled_count
            }

        except Exception as e:
            error_msg = f"批量调度失败: {str(e)}"
            log_activity('ERROR', 'rq_schedule', error_msg)
            logging.error(f"[RQ批量调度] {error_msg}")
            return {"status": "error", "message": error_msg}

def immediate_push_subscription(subscription_id: int):
    """立即推送指定订阅（高优先级任务）"""
    from rq_config import enqueue_job
    return enqueue_job(process_subscription_push, subscription_id, priority='high')

def test_rq_connection():
    """测试RQ连接和任务执行"""
    with app.app_context():
        current_time = datetime.datetime.now()
        log_activity('INFO', 'rq_test', f'RQ连接测试任务执行成功: {current_time}')
        return {"status": "success", "time": current_time.isoformat()}

def batch_push_all_users():
    """批量推送所有用户订阅（异步任务）"""
    with app.app_context():
        try:
            start_time = datetime.datetime.now()
            logging.info(f"[RQ批量推送] 开始批量推送所有用户订阅")

            # 调用推送服务处理所有用户
            results = push_service.process_user_subscriptions()

            success_count = sum(1 for r in results if r.get('success'))
            total_articles = sum(r.get('articles_found', 0) for r in results if r.get('success'))

            end_time = datetime.datetime.now()
            duration = (end_time - start_time).total_seconds()

            success_msg = f'批量推送完成: 处理 {len(results)} 个用户，成功 {success_count} 个，共找到 {total_articles} 篇新文章'
            log_activity('INFO', 'rq_batch_push', success_msg)
            logging.info(f"[RQ批量推送] {success_msg} (耗时: {duration:.2f}秒)")

            return {
                "status": "success",
                "total_users": len(results),
                "success_count": success_count,
                "total_articles": total_articles,
                "duration": duration
            }

        except Exception as e:
            error_msg = f"批量推送失败: {str(e)}"
            log_activity('ERROR', 'rq_batch_push', error_msg)
            logging.error(f"[RQ批量推送] {error_msg}")
            return {"status": "error", "message": error_msg}

if __name__ == '__main__':
    # 测试任务定义
    print("RQ任务模块加载成功")
    print("可用任务:")
    print("- process_subscription_push: 处理订阅推送")
    print("- batch_schedule_all_subscriptions: 批量调度订阅")
    print("- batch_push_all_users: 批量推送所有用户")
    print("- immediate_push_subscription: 立即推送")
    print("- test_rq_connection: 连接测试")