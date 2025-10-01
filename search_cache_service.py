# -*- coding: utf-8 -*-
"""
搜索结果缓存服务
用于优化相同主题词多用户订阅的PubMed API调用
支持精确匹配和宽松匹配两级缓存策略
"""

import hashlib
import json
import time
import logging
from typing import Optional, Dict, List, Tuple, Any
from datetime import datetime, timedelta

# 延迟导入避免循环依赖
try:
    from rq_config import redis_conn
except ImportError:
    redis_conn = None
    logging.warning("Redis连接未初始化,缓存服务将降级为无缓存模式")


class SearchCacheService:
    """
    PubMed搜索结果缓存服务

    设计原则:
    1. 零侵入: 在PubMedAPI层透明接入,业务逻辑无感知
    2. 智能降级: Redis不可用时自动回退到直接搜索
    3. 多级策略: 精确匹配 → 宽松匹配 → 直接搜索
    """

    # 缓存键前缀
    CACHE_PREFIX = "pubmed:search_cache"

    # 缓存配置
    DEFAULT_TTL = 3600  # 默认1小时
    MAX_TTL = 86400     # 最大24小时
    MIN_TTL = 1800      # 最小30分钟

    # 统计键
    STATS_KEY = "pubmed:cache_stats"

    def __init__(self, redis_connection=None):
        """
        初始化缓存服务

        Args:
            redis_connection: Redis连接实例,默认使用rq_config中的连接
        """
        self.redis = redis_connection or redis_conn
        self.enabled = self.redis is not None

        if not self.enabled:
            logging.warning("SearchCacheService: Redis未配置,缓存功能已禁用")
        else:
            logging.info("SearchCacheService: 初始化成功,缓存功能已启用")

    def generate_cache_key(
        self,
        keywords: str,
        filter_params: Dict[str, Any],
        include_filters: bool = True
    ) -> str:
        """
        生成缓存键

        Args:
            keywords: 搜索关键词
            filter_params: 筛选参数字典
            include_filters: 是否包含筛选参数(False用于宽松匹配)

        Returns:
            str: 缓存键的哈希值
        """
        # 标准化关键词(去除多余空格,转小写)
        normalized_keywords = ' '.join(keywords.lower().strip().split())

        # 构建缓存键组成部分
        key_parts = [normalized_keywords]

        if include_filters and filter_params:
            # 提取核心筛选参数(影响搜索结果的参数)
            core_params = {
                'days_back': filter_params.get('days_back', 30),
                'max_results': filter_params.get('max_results', 10000),
                'jcr_filter': filter_params.get('jcr_filter'),
                'zky_filter': filter_params.get('zky_filter'),
                'exclude_no_issn': filter_params.get('exclude_no_issn', True)
            }

            # 序列化为稳定的JSON字符串
            params_json = json.dumps(core_params, sort_keys=True, ensure_ascii=False)
            key_parts.append(params_json)

        # 生成MD5哈希
        key_string = '|'.join(key_parts)
        hash_digest = hashlib.md5(key_string.encode('utf-8')).hexdigest()

        return f"{self.CACHE_PREFIX}:{hash_digest}"

    def get_cached_results(
        self,
        keywords: str,
        filter_params: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """
        获取缓存的搜索结果

        尝试顺序:
        1. 精确匹配(包含所有筛选参数)
        2. 宽松匹配(仅关键词,忽略筛选参数)

        Args:
            keywords: 搜索关键词
            filter_params: 筛选参数

        Returns:
            Optional[Dict]: 缓存数据或None
        """
        if not self.enabled:
            return None

        try:
            # 1. 尝试精确匹配
            exact_key = self.generate_cache_key(keywords, filter_params, include_filters=True)
            cached_data = self._get_from_redis(exact_key)

            if cached_data:
                logging.info(f"[缓存命中-精确] 关键词: {keywords[:50]}")
                self._record_hit(cache_type='exact')
                return cached_data

            # 2. 尝试宽松匹配
            relaxed_key = self.generate_cache_key(keywords, filter_params, include_filters=False)
            cached_data = self._get_from_redis(relaxed_key)

            if cached_data:
                logging.info(f"[缓存命中-宽松] 关键词: {keywords[:50]}, 需二次筛选")
                self._record_hit(cache_type='relaxed')
                # 注意: 宽松匹配返回的数据需要调用方进行二次筛选
                cached_data['requires_filtering'] = True
                return cached_data

            # 3. 缓存未命中
            logging.info(f"[缓存未命中] 关键词: {keywords[:50]}")
            self._record_miss()
            return None

        except Exception as e:
            logging.error(f"缓存读取失败: {e}", exc_info=True)
            return None

    def set_cached_results(
        self,
        keywords: str,
        filter_params: Dict[str, Any],
        pmids: List[str],
        articles: List[Dict[str, Any]],
        ttl: Optional[int] = None
    ) -> bool:
        """
        设置缓存结果

        Args:
            keywords: 搜索关键词
            filter_params: 筛选参数
            pmids: PMID列表
            articles: 文章详细信息列表
            ttl: 缓存时效(秒),默认使用智能计算

        Returns:
            bool: 是否设置成功
        """
        if not self.enabled:
            return False

        try:
            # 生成精确匹配的缓存键
            cache_key = self.generate_cache_key(keywords, filter_params, include_filters=True)

            # 计算智能TTL(根据结果数量和当前时间)
            if ttl is None:
                ttl = self._calculate_dynamic_ttl(keywords, len(pmids))

            # 构建缓存数据
            cache_data = {
                'pmids': pmids,
                'articles': articles,
                'created_at': datetime.now().isoformat(),
                'keywords': keywords,
                'filter_params': filter_params,
                'hit_count': 0,
                'result_count': len(pmids)
            }

            # 序列化并存储
            cache_value = json.dumps(cache_data, ensure_ascii=False)
            self.redis.setex(cache_key, ttl, cache_value)

            logging.info(f"[缓存设置] 关键词: {keywords[:50]}, 结果数: {len(pmids)}, TTL: {ttl}秒")
            return True

        except Exception as e:
            logging.error(f"缓存写入失败: {e}", exc_info=True)
            return False

    def invalidate_cache(self, keywords: str, filter_params: Dict[str, Any] = None) -> bool:
        """
        手动失效缓存

        Args:
            keywords: 关键词
            filter_params: 筛选参数(None时删除该关键词的所有缓存)

        Returns:
            bool: 是否成功
        """
        if not self.enabled:
            return False

        try:
            if filter_params is None:
                # 删除该关键词的所有缓存(精确+宽松)
                exact_key = self.generate_cache_key(keywords, {}, include_filters=True)
                relaxed_key = self.generate_cache_key(keywords, {}, include_filters=False)
                deleted = self.redis.delete(exact_key, relaxed_key)
            else:
                # 删除特定参数的缓存
                cache_key = self.generate_cache_key(keywords, filter_params, include_filters=True)
                deleted = self.redis.delete(cache_key)

            logging.info(f"[缓存失效] 关键词: {keywords[:50]}, 删除: {deleted}个键")
            return deleted > 0

        except Exception as e:
            logging.error(f"缓存失效失败: {e}", exc_info=True)
            return False

    def get_cache_stats(self) -> Dict[str, Any]:
        """
        获取缓存统计信息

        Returns:
            Dict: 统计数据
        """
        if not self.enabled:
            return {
                'enabled': False,
                'message': 'Redis缓存未启用'
            }

        try:
            stats_data = self.redis.get(self.STATS_KEY)
            if stats_data:
                stats = json.loads(stats_data)
            else:
                stats = {
                    'total_hits': 0,
                    'exact_hits': 0,
                    'relaxed_hits': 0,
                    'total_misses': 0,
                    'last_reset': datetime.now().isoformat()
                }

            # 计算命中率
            total_requests = stats['total_hits'] + stats['total_misses']
            hit_rate = (stats['total_hits'] / total_requests * 100) if total_requests > 0 else 0

            stats['hit_rate'] = round(hit_rate, 2)
            stats['total_requests'] = total_requests
            stats['enabled'] = True

            return stats

        except Exception as e:
            logging.error(f"获取统计信息失败: {e}", exc_info=True)
            return {
                'enabled': True,
                'error': str(e)
            }

    def reset_cache_stats(self) -> bool:
        """重置统计信息"""
        if not self.enabled:
            return False

        try:
            stats = {
                'total_hits': 0,
                'exact_hits': 0,
                'relaxed_hits': 0,
                'total_misses': 0,
                'last_reset': datetime.now().isoformat()
            }
            self.redis.set(self.STATS_KEY, json.dumps(stats))
            logging.info("[缓存统计] 已重置")
            return True
        except Exception as e:
            logging.error(f"重置统计失败: {e}", exc_info=True)
            return False

    def clear_all_cache(self) -> int:
        """
        清空所有搜索缓存

        Returns:
            int: 删除的缓存键数量
        """
        if not self.enabled:
            return 0

        try:
            # 使用SCAN遍历所有匹配的键(避免KEYS阻塞)
            deleted_count = 0
            cursor = 0

            while True:
                cursor, keys = self.redis.scan(
                    cursor=cursor,
                    match=f"{self.CACHE_PREFIX}:*",
                    count=100
                )

                if keys:
                    deleted_count += self.redis.delete(*keys)

                if cursor == 0:
                    break

            logging.info(f"[缓存清空] 删除 {deleted_count} 个缓存键")
            return deleted_count

        except Exception as e:
            logging.error(f"清空缓存失败: {e}", exc_info=True)
            return 0

    # ==================== 私有辅助方法 ====================

    def _get_from_redis(self, key: str) -> Optional[Dict[str, Any]]:
        """从Redis获取并反序列化数据"""
        try:
            cached_value = self.redis.get(key)
            if cached_value:
                # 增加命中计数
                self.redis.incr(f"{key}:hits")
                return json.loads(cached_value)
            return None
        except Exception as e:
            logging.error(f"Redis读取失败: {e}")
            return None

    def _calculate_dynamic_ttl(self, keywords: str, result_count: int) -> int:
        """
        智能计算缓存TTL

        策略:
        - 结果数多: 更长TTL(可能是热门关键词)
        - 工作时间: 较短TTL(更新频繁)
        - 夜间/周末: 较长TTL(更新较少)

        Args:
            keywords: 关键词
            result_count: 结果数量

        Returns:
            int: TTL秒数
        """
        base_ttl = self.DEFAULT_TTL

        # 根据结果数调整(结果越多,可能越热门,缓存时间越长)
        if result_count > 100:
            base_ttl = int(base_ttl * 1.5)
        elif result_count > 50:
            base_ttl = int(base_ttl * 1.2)
        elif result_count < 10:
            base_ttl = int(base_ttl * 0.8)

        # 根据时间调整
        now = datetime.now()
        hour = now.hour
        weekday = now.weekday()

        # 工作时间(9-18点,工作日): 较短TTL
        if 9 <= hour <= 18 and weekday < 5:
            base_ttl = int(base_ttl * 0.8)
        # 夜间/周末: 较长TTL
        elif hour < 6 or hour > 22 or weekday >= 5:
            base_ttl = int(base_ttl * 1.5)

        # 限制在合理范围
        ttl = max(self.MIN_TTL, min(self.MAX_TTL, base_ttl))

        return ttl

    def _record_hit(self, cache_type: str = 'exact') -> None:
        """记录缓存命中"""
        try:
            stats_data = self.redis.get(self.STATS_KEY)
            if stats_data:
                stats = json.loads(stats_data)
            else:
                stats = {
                    'total_hits': 0,
                    'exact_hits': 0,
                    'relaxed_hits': 0,
                    'total_misses': 0,
                    'last_reset': datetime.now().isoformat()
                }

            stats['total_hits'] += 1
            if cache_type == 'exact':
                stats['exact_hits'] += 1
            elif cache_type == 'relaxed':
                stats['relaxed_hits'] += 1

            self.redis.set(self.STATS_KEY, json.dumps(stats))
        except Exception as e:
            logging.error(f"记录命中失败: {e}")

    def _record_miss(self) -> None:
        """记录缓存未命中"""
        try:
            stats_data = self.redis.get(self.STATS_KEY)
            if stats_data:
                stats = json.loads(stats_data)
            else:
                stats = {
                    'total_hits': 0,
                    'exact_hits': 0,
                    'relaxed_hits': 0,
                    'total_misses': 0,
                    'last_reset': datetime.now().isoformat()
                }

            stats['total_misses'] += 1
            self.redis.set(self.STATS_KEY, json.dumps(stats))
        except Exception as e:
            logging.error(f"记录未命中失败: {e}")


# 全局缓存服务实例
search_cache_service = SearchCacheService()


# 便捷函数(供外部直接调用)
def get_cached_search(keywords: str, filter_params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """获取缓存的搜索结果(便捷函数)"""
    return search_cache_service.get_cached_results(keywords, filter_params)


def cache_search_results(
    keywords: str,
    filter_params: Dict[str, Any],
    pmids: List[str],
    articles: List[Dict[str, Any]]
) -> bool:
    """缓存搜索结果(便捷函数)"""
    return search_cache_service.set_cached_results(keywords, filter_params, pmids, articles)


def invalidate_search_cache(keywords: str, filter_params: Dict[str, Any] = None) -> bool:
    """失效缓存(便捷函数)"""
    return search_cache_service.invalidate_cache(keywords, filter_params)
