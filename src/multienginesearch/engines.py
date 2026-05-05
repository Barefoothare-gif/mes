"""
搜索引擎接口和实现 - 饕餮进化 R15 版本
修复: Python 3.11+ 兼容, ddgs 替代 duckduckgo-search
新增: 熔断器模式 (Circuit Breaker), 并行搜索降级
"""

from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional, Tuple
from ddgs import DDGS  # Fixed: ddgs instead of duckduckgo_search
import json
import os
import time
import requests
from datetime import datetime, timedelta
from pathlib import Path
import pytz


# ============ Circuit Breaker Implementation ============

class CircuitOpenError(Exception):
    """电路开着，跳过执行"""
    pass


class CircuitBreaker:
    """
    熔断器模式三状态实现
    
    States:
        CLOSED: 正常，流量通过
        OPEN: 熔断，流量跳过
        HALF_OPEN: 半开，尝试恢复
    """
    
    def __init__(self, name: str, failure_threshold: int = 3, recovery_timeout: float = 60.0):
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.failures = 0
        self.last_failure_time: Optional[float] = None
        self.state = "CLOSED"
    
    def call(self, func, *args, **kwargs):
        """执行函数，失败时触发熔断"""
        if self.state == "OPEN":
            if self._should_attempt_recovery():
                self.state = "HALF_OPEN"
            else:
                raise CircuitOpenError(f"{self.name} circuit OPEN, skipping")
        
        try:
            result = func(*args, **kwargs)
            self._on_success()
            return result
        except Exception as e:
            self._on_failure()
            raise
    
    def _should_attempt_recovery(self) -> bool:
        if self.last_failure_time is None:
            return True
        return (time.time() - self.last_failure_time) > self.recovery_timeout
    
    def _on_success(self):
        self.failures = 0
        if self.state == "HALF_OPEN":
            self.state = "CLOSED"
    
    def _on_failure(self):
        self.failures += 1
        self.last_failure_time = time.time()
        if self.failures >= self.failure_threshold:
            self.state = "OPEN"
    
    def get_status(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "state": self.state,
            "failures": self.failures,
            "last_failure": self.last_failure_time,
            "recovery_timeout": self.recovery_timeout,
        }


# 全局熔断器注册表
SEARCH_CIRCUIT_BREAKERS = {
    "duckduckgo": CircuitBreaker("duckduckgo", failure_threshold=3, recovery_timeout=60.0),
    "google": CircuitBreaker("google", failure_threshold=2, recovery_timeout=120.0),
    "bing": CircuitBreaker("bing", failure_threshold=3, recovery_timeout=60.0),
}


# ============ Data Classes ============

class SearchResult:
    """搜索结果数据类"""
    
    def __init__(self, title: str, url: str, description: str, engine: str):
        self.title = title
        self.url = url
        self.description = description
        self.engine = engine
    
    def to_dict(self) -> Dict[str, str]:
        return {
            "title": self.title,
            "url": self.url,
            "description": self.description,
            "engine": self.engine,
        }


class SearchResponse:
    """搜索响应数据类，包含搜索结果和元数据"""
    
    def __init__(
        self,
        results: List[SearchResult],
        rate_limit_info: Optional[Dict[str, Any]] = None,
        circuit_status: Optional[Dict[str, Any]] = None,
    ):
        self.results = results
        self.rate_limit_info = rate_limit_info
        self.circuit_status = circuit_status
    
    def to_dict(self) -> Dict[str, Any]:
        data = {
            "results": [result.to_dict() for result in self.results],
            "count": len(self.results),
        }
        if self.rate_limit_info:
            data["rate_limit"] = self.rate_limit_info
        if self.circuit_status:
            data["circuit_breakers"] = self.circuit_status
        return data


# ============ Abstract Base Class ============

class SearchEngine(ABC):
    """搜索引擎抽象基类"""
    
    @abstractmethod
    def search(
        self, query: str, limit: int = 10, time_filter: Optional[str] = None
    ) -> SearchResponse:
        """执行搜索并返回结果"""
        pass
    
    @property
    @abstractmethod
    def name(self) -> str:
        """搜索引擎名称"""
        pass


# ============ DuckDuckGo Engine ============

class DuckDuckGoEngine(SearchEngine):
    """DuckDuckGo 搜索引擎实现 - 修复: ddgs"""
    
    def __init__(self, region: str = "wt-wt", safesearch: str = "moderate"):
        self.region = region
        self.safesearch = safesearch
    
    @property
    def name(self) -> str:
        return "duckduckgo"
    
    def search(
        self, query: str, limit: int = 10, time_filter: Optional[str] = None
    ) -> SearchResponse:
        """使用 DuckDuckGo 执行搜索"""
        try:
            with DDGS() as ddgs:
                # Fixed: use ddgs.text() instead of ddgs.text()
                results = list(ddgs.text(
                    query=query,
                    region=self.region,
                    safesearch=self.safesearch,
                    timelimit=time_filter,
                    max_results=limit,
                ))
            
            search_results = []
            for result in results:
                search_results.append(SearchResult(
                    title=result.get("title", ""),
                    url=result.get("href", ""),
                    description=result.get("body", ""),
                    engine=self.name,
                ))
            
            return SearchResponse(search_results)
        
        except Exception as e:
            # 熔断器记录失败
            cb = SEARCH_CIRCUIT_BREAKERS.get("duckduckgo")
            if cb:
                cb._on_failure()
            # 网络错误可能是临时的，不一定触发熔断
            error_str = str(e).lower()
            if 'network' in error_str or 'timeout' in error_str or 'connection' in error_str:
                print(f"DuckDuckGo 网络错误 (暂不熔断): {e}")
            else:
                print(f"DuckDuckGo 搜索出错: {e}")
            return SearchResponse([])


# ============ Google Engine ============

class GoogleEngine(SearchEngine):
    """Google Custom Search API 搜索引擎实现"""
    
    def __init__(self):
        self.api_key = os.getenv("MES_GOOGLE_API_KEY")
        self.search_engine_id = os.getenv("MES_GOOGLE_SEARCH_ENGINE_ID")
        self.daily_limit = 100
        self._init_quota_tracking()
        
        if not self.api_key or not self.search_engine_id:
            raise ValueError(
                "Google Search API 需要设置环境变量: "
                "MES_GOOGLE_API_KEY 和 MES_GOOGLE_SEARCH_ENGINE_ID"
            )
    
    def _init_quota_tracking(self):
        home_dir = Path.home()
        self.quota_file = home_dir / ".mes_google_quota.json"
        self._load_or_reset_quota()
    
    def _get_pacific_time(self) -> datetime:
        pacific_tz = pytz.timezone("US/Pacific")
        return datetime.now(pacific_tz)
    
    def _get_next_reset_time(self) -> datetime:
        pacific_tz = pytz.timezone("US/Pacific")
        now_pacific = self._get_pacific_time()
        next_reset = (now_pacific + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        return next_reset
    
    def _load_or_reset_quota(self):
        pacific_now = self._get_pacific_time()
        today_pacific = pacific_now.date().isoformat()
        
        default_quota = {
            "date": today_pacific,
            "requests_used": 0,
            "daily_limit": self.daily_limit,
            "reset_time": self._get_next_reset_time().isoformat(),
            "timezone": "US/Pacific",
        }
        
        try:
            if self.quota_file.exists():
                with open(self.quota_file, "r", encoding="utf-8") as f:
                    quota_data = json.load(f)
                if quota_data.get("date") != today_pacific:
                    quota_data = default_quota
                    self._save_quota(quota_data)
                else:
                    quota_data["reset_time"] = self._get_next_reset_time().isoformat()
                    quota_data["timezone"] = "US/Pacific"
                    self._save_quota(quota_data)
            else:
                quota_data = default_quota
                self._save_quota(quota_data)
            self.quota_data = quota_data
        except (json.JSONDecodeError, KeyError, IOError):
            self.quota_data = default_quota
            self._save_quota(default_quota)
    
    def _save_quota(self, quota_data):
        try:
            with open(self.quota_file, "w", encoding="utf-8") as f:
                json.dump(quota_data, f, indent=2, ensure_ascii=False)
        except IOError:
            pass
    
    def _update_quota_usage(self):
        self.quota_data["requests_used"] += 1
        self._save_quota(self.quota_data)
    
    def _get_quota_info(self) -> Dict[str, Any]:
        requests_used = self.quota_data["requests_used"]
        daily_limit = self.quota_data["daily_limit"]
        requests_remaining = max(0, daily_limit - requests_used)
        
        return {
            "daily_limit": daily_limit,
            "requests_used": requests_used,
            "requests_remaining": requests_remaining,
            "limit_exceeded": requests_used >= daily_limit,
            "reset_time": self._get_next_reset_time().isoformat(),
            "timezone": "US/Pacific",
            "source": "persistent_tracking",
        }
    
    @property
    def name(self) -> str:
        return "google"
    
    def _build_payload(
        self,
        query: str,
        start: int = 1,
        num: int = 10,
        date_restrict: Optional[str] = None,
    ) -> Dict[str, Any]:
        payload = {
            "key": self.api_key,
            "q": query,
            "cx": self.search_engine_id,
            "start": start,
            "num": num,
        }
        
        if date_restrict:
            time_mapping = {
                "d": "d1",
                "w": "w1",
                "m": "m1",
                "y": "y1",
            }
            payload["dateRestrict"] = time_mapping.get(date_restrict, date_restrict)
        
        return payload
    
    def _make_request(
        self, payload: Dict[str, Any]
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        if self.quota_data["requests_used"] >= self.quota_data["daily_limit"]:
            rate_limit_info = self._get_quota_info()
            raise Exception(
                f"Google API 配额已达到每日限制 {self.quota_data['daily_limit']} 次。"
                f"将在 {rate_limit_info['reset_time']} 重置。"
            )
        
        response = requests.get(
            "https://www.googleapis.com/customsearch/v1", params=payload
        )
        
        if response.status_code != 200:
            raise Exception(
                f"Google Search API 请求失败，状态码: {response.status_code}"
            )
        
        self._update_quota_usage()
        rate_limit_info = self._get_quota_info()
        return response.json(), rate_limit_info
    
    def search(
        self, query: str, limit: int = 10, time_filter: Optional[str] = None
    ) -> SearchResponse:
        try:
            search_results = []
            rate_limit_info = None
            
            pages_needed = (limit - 1) // 10 + 1
            
            for page in range(pages_needed):
                start_index = page * 10 + 1
                
                if page == pages_needed - 1:
                    remaining = limit - len(search_results)
                    num_results = min(10, remaining)
                else:
                    num_results = 10
                
                if num_results <= 0:
                    break
                
                payload = self._build_payload(
                    query=query,
                    start=start_index,
                    num=num_results,
                    date_restrict=time_filter,
                )
                
                response_data, current_rate_limit = self._make_request(payload)
                rate_limit_info = current_rate_limit
                
                items = response_data.get("items", [])
                if not items:
                    break
                
                for item in items:
                    if len(search_results) >= limit:
                        break
                    search_results.append(SearchResult(
                        title=item.get("title", ""),
                        url=item.get("link", ""),
                        description=item.get("snippet", ""),
                        engine=self.name,
                    ))
                
                if len(items) < num_results:
                    break
            
            return SearchResponse(search_results, rate_limit_info)
        
        except Exception as e:
            cb = SEARCH_CIRCUIT_BREAKERS.get("google")
            if cb:
                cb._on_failure()
            print(f"Google 搜索出错: {e}")
            return SearchResponse([])


# ============ Bing Engine (New!) ============

class BingEngine(SearchEngine):
    """Bing Search API 搜索引擎实现"""
    
    def __init__(self):
        self.api_key = os.getenv("MES_BING_API_KEY")
        self.endpoint = "https://api.bing.microsoft.com/v7.0/search"
        
        if not self.api_key:
            raise ValueError(
                "Bing Search API 需要设置环境变量: MES_BING_API_KEY"
            )
    
    @property
    def name(self) -> str:
        return "bing"
    
    def search(
        self, query: str, limit: int = 10, time_filter: Optional[str] = None
    ) -> SearchResponse:
        try:
            headers = {"Ocp-Apim-Subscription-Key": self.api_key}
            params = {
                "q": query,
                "count": min(limit, 50),
                "offset": 0,
            }
            
            if time_filter:
                time_mapping = {
                    "d": "dy",  # day
                    "w": "wk",  # week
                    "m": "m",   # month
                    "y": "yyyy", # year
                }
                params["freshness"] = time_mapping.get(time_filter, "dy")
            
            response = requests.get(self.endpoint, headers=headers, params=params)
            
            if response.status_code != 200:
                raise Exception(f"Bing API 失败: {response.status_code}")
            
            data = response.json()
            web_pages = data.get("webPages", {}).get("value", [])
            
            search_results = []
            for item in web_pages[:limit]:
                search_results.append(SearchResult(
                    title=item.get("name", ""),
                    url=item.get("url", ""),
                    description=item.get("snippet", ""),
                    engine=self.name,
                ))
            
            return SearchResponse(search_results)
        
        except Exception as e:
            cb = SEARCH_CIRCUIT_BREAKERS.get("bing")
            if cb:
                cb._on_failure()
            print(f"Bing 搜索出错: {e}")
            return SearchResponse([])


# ============ Factory with Circuit Breaker Support ============

class SearchEngineFactory:
    """搜索引擎工厂类 - 支持熔断器"""
    
    _engines = {
        "duckduckgo": DuckDuckGoEngine,
        "google": GoogleEngine,
        "bing": BingEngine,
    }
    
    @classmethod
    def create_engine(cls, engine_name: str) -> Optional[SearchEngine]:
        if engine_name.lower() in cls._engines:
            try:
                # 检查熔断器
                cb = SEARCH_CIRCUIT_BREAKERS.get(engine_name.lower())
                if cb and cb.state == "OPEN":
                    if not cb._should_attempt_recovery():
                        print(f"⚠️ {engine_name} 熔断器 OPEN，跳过创建")
                        return None
                return cls._engines[engine_name.lower()]()
            except Exception as e:
                print(f"创建搜索引擎 {engine_name} 失败: {e}")
                return None
        return None
    
    @classmethod
    def get_available_engines(cls) -> List[str]:
        return list(cls._engines.keys())
    
    @classmethod
    def register_engine(cls, name: str, engine_class: type):
        cls._engines[name.lower()] = engine_class
    
    @classmethod
    def get_circuit_status(cls) -> Dict[str, Dict[str, Any]]:
        return {name: cb.get_status() for name, cb in SEARCH_CIRCUIT_BREAKERS.items()}
    
    @classmethod
    def reset_circuit(cls, name: str):
        """手动重置熔断器"""
        if name in SEARCH_CIRCUIT_BREAKERS:
            SEARCH_CIRCUIT_BREAKERS[name].state = "CLOSED"
            SEARCH_CIRCUIT_BREAKERS[name].failures = 0
            print(f"✅ {name} 熔断器已重置")


# ============ Parallel Search with Fallback ============

def search_with_fallback(
    query: str,
    engines: Optional[List[str]] = None,
    limit: int = 10,
    time_filter: Optional[str] = None,
) -> SearchResponse:
    """
    并行搜索 + 熔断降级
    
    按顺序尝试每个引擎，第一个成功返回结果。
    熔断器跳过的引擎会记录但不阻塞。
    """
    if engines is None:
        engines = ["duckduckgo", "bing"]  # 默认顺序
    
    all_results = []
    errors = []
    circuit_states = {}
    
    for engine_name in engines:
        # 检查熔断器
        cb = SEARCH_CIRCUIT_BREAKERS.get(engine_name)
        if cb:
            circuit_states[engine_name] = cb.state
            if cb.state == "OPEN" and not cb._should_attempt_recovery():
                errors.append(f"{engine_name}: circuit OPEN, skipped")
                continue
        
        try:
            engine = SearchEngineFactory.create_engine(engine_name)
            if engine is None:
                errors.append(f"{engine_name}: unavailable (circuit open or init failed)")
                continue
            
            response = engine.search(query, limit, time_filter)
            
            if response.results:
                # 成功，返回结果
                response.circuit_status = circuit_states
                return response
            else:
                errors.append(f"{engine_name}: no results")
                
        except CircuitOpenError as e:
            errors.append(f"{engine_name}: {e}")
        except Exception as e:
            errors.append(f"{engine_name}: {e}")
            if cb:
                cb._on_failure()
    
    # 所有引擎都失败
    print(f"⚠️ 所有引擎失败: {errors}")
    return SearchResponse([], circuit_status=circuit_states)


# ============ Formatters ============

def format_results(response: SearchResponse, output_format: str = "simple") -> str:
    """格式化搜索结果"""
    if not response.results:
        return "❌ 没有找到搜索结果"
    
    if output_format == "json":
        return json.dumps(response.to_dict(), ensure_ascii=False, indent=2)
    
    # Simple format
    output = []
    output.append(f"🔍 找到 {len(response.results)} 个搜索结果:\n")
    
    for i, result in enumerate(response.results, 1):
        output.append(f"{i:2d}. {result.title}")
        output.append(f"    🔗 {result.url}")
        output.append(f"    📄 {result.description}")
        output.append(f"    🔍 来源: {result.engine}")
        output.append("")
    
    # 熔断器状态
    if response.circuit_status:
        output.append("⚡ 熔断器状态:")
        for name, state in response.circuit_status.items():
            output.append(f"    • {name}: {state}")
        output.append("")
    
    # API限额
    if response.rate_limit_info:
        output.append("📊 API 使用情况:")
        rate_info = response.rate_limit_info
        output.append(f"    • 每日限额: {rate_info['daily_limit']} 次")
        output.append(f"    • 已使用: {rate_info['requests_used']} 次")
        output.append(f"    • 剩余: {rate_info['requests_remaining']} 次")
        if rate_info.get("reset_time"):
            output.append(f"    • 重置时间: {rate_info['reset_time']}")
        if rate_info.get("limit_exceeded"):
            output.append("    ⚠️ 警告: 已达到每日限额!")
        output.append("")
    
    return "\n".join(output)
