"""反爬虫工具集：UA轮换、请求伪装、随机延迟、重试退避、代理管理"""

import asyncio
import logging
import random
import time
from functools import wraps

import aiohttp
import requests
from fake_useragent import UserAgent

from config import MAX_RETRIES, MIN_DELAY, MAX_DELAY, PROXY_LIST, RETRY_BACKOFF, REQUEST_TIMEOUT

logger = logging.getLogger(__name__)

_ua = UserAgent(fallback="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                         "AppleWebKit/537.36 (KHTML, like Gecko) "
                         "Chrome/120.0.0.0 Safari/537.36")


def random_ua() -> str:
    return _ua.random


def build_headers(referer: str | None = None) -> dict:
    headers = {
        "User-Agent": random_ua(),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "DNT": "1",
    }
    if referer:
        headers["Referer"] = referer
    return headers


async def random_delay(min_s: float = MIN_DELAY, max_s: float = MAX_DELAY):
    delay = random.uniform(min_s, max_s)
    await asyncio.sleep(delay)


def sync_random_delay(min_s: float = MIN_DELAY, max_s: float = MAX_DELAY):
    time.sleep(random.uniform(min_s, max_s))


def get_proxy() -> dict | None:
    if not PROXY_LIST:
        return None
    proxy = random.choice(PROXY_LIST)
    return {"http": proxy, "https": proxy}


def get_aio_proxy() -> str | None:
    if not PROXY_LIST:
        return None
    return random.choice(PROXY_LIST)


def sync_request(url: str, method: str = "GET", **kwargs) -> requests.Response | None:
    """带重试和反爬的同步请求"""
    for attempt in range(MAX_RETRIES):
        try:
            kwargs.setdefault("headers", build_headers(kwargs.pop("referer", None)))
            kwargs.setdefault("timeout", REQUEST_TIMEOUT)
            proxy = get_proxy()
            if proxy:
                kwargs.setdefault("proxies", proxy)

            resp = requests.request(method, url, **kwargs)
            resp.raise_for_status()
            return resp
        except requests.RequestException as e:
            wait = RETRY_BACKOFF ** attempt + random.uniform(0, 1)
            logger.warning(f"请求失败 [{attempt+1}/{MAX_RETRIES}] {url}: {e}, {wait:.1f}s 后重试")
            time.sleep(wait)
    logger.error(f"请求最终失败: {url}")
    return None


async def async_request(session: aiohttp.ClientSession, url: str,
                        method: str = "GET", **kwargs) -> tuple[int, bytes] | None:
    """带重试和反爬的异步请求"""
    for attempt in range(MAX_RETRIES):
        try:
            kwargs.setdefault("headers", build_headers(kwargs.pop("referer", None)))
            kwargs.setdefault("timeout", aiohttp.ClientTimeout(total=REQUEST_TIMEOUT))
            proxy = get_aio_proxy()
            if proxy:
                kwargs.setdefault("proxy", proxy)

            async with session.request(method, url, **kwargs) as resp:
                resp.raise_for_status()
                data = await resp.read()
                return resp.status, data
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            wait = RETRY_BACKOFF ** attempt + random.uniform(0, 1)
            logger.warning(f"异步请求失败 [{attempt+1}/{MAX_RETRIES}] {url}: {e}, {wait:.1f}s 后重试")
            await asyncio.sleep(wait)
    logger.error(f"异步请求最终失败: {url}")
    return None


class RateLimiter:
    """令牌桶限速器"""

    def __init__(self, rate: float = 1.0, burst: int = 5):
        self.rate = rate
        self.burst = burst
        self._tokens = burst
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self):
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_refill
            self._tokens = min(self.burst, self._tokens + elapsed * self.rate)
            self._last_refill = now

            if self._tokens < 1:
                wait = (1 - self._tokens) / self.rate
                await asyncio.sleep(wait)
                self._tokens = 0
            else:
                self._tokens -= 1
