# netdisk_utils_net.py
# 网络请求、限流器、并发闸门、代理、CLI 环境、S3 凭证
# 【Bug 22 - 1/7】ConcurrencyLimiter.acquire 加 should_stop 参数
#                实现改为 0.5 秒轮询 + 每轮检查回调，避免长时间阻塞无法响应取消
#                同时修复原实现 cv.wait(timeout) 每次都等完整 60 秒的隐患
#                向下兼容：默认 should_stop=None 时行为与原来等价

import os
import sys
import subprocess
import logging
import threading
import collections
import time
import urllib.parse

import requests
import urllib3
import internetarchive as ia

try:
    import certifi
    CERTIFI_AVAILABLE = True
except ImportError:
    certifi = None
    CERTIFI_AVAILABLE = False

from netdisk_state import (
    get_config, cfg_get, get_active_account, TEMP_UPLOAD_DIR,
)

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


# ========== SSL / 代理 ==========

def get_ssl_verify():
    return cfg_get('verify_ssl', True)


def get_req_proxies():
    cfg = get_config()
    if not cfg.get('download_via_proxy', True): return None
    proxy_url = cfg.get('proxy', '').strip()
    if proxy_url:
        proxy_h = proxy_url.replace("socks5://", "socks5h://") if proxy_url.startswith("socks5://") else proxy_url
        return {"http": proxy_h, "https": proxy_h}
    return None


# ========== IA 限流器 ==========

class IARateLimiter:
    def __init__(self):
        self.history = collections.deque()
        self.lock = threading.Lock()
        self.cooldown_until = 0.0
    def acquire(self, timeout=60):
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self.lock:
                now = time.time()
                if now < self.cooldown_until:
                    wait = self.cooldown_until - now
                else:
                    max_n = cfg_get('ia_rate_max', 10)
                    window = cfg_get('ia_rate_window', 60)
                    while self.history and now - self.history[0] > window:
                        self.history.popleft()
                    if len(self.history) < max_n:
                        self.history.append(now)
                        return True
                    wait = window - (now - self.history[0]) + 0.1
            time.sleep(min(wait, 1.0))
        return False
    def trigger_429(self):
        with self.lock:
            self.cooldown_until = time.time() + cfg_get('ia_429_backoff', 60)
IA_LIMITER = IARateLimiter()


def ia_get(url, timeout=None, headers=None, stream=False, _retries=0):
    if not IA_LIMITER.acquire():
        logging.warning(f"IA 限流等待超时，放弃请求: {url[:80]}")
        return None
    to = timeout or cfg_get('ia_timeout', 30)
    h = headers or {"User-Agent": "Mozilla/5.0"}

    req_proxies = get_req_proxies()

    try:
        r = requests.get(url, timeout=to, headers=h, stream=stream, verify=get_ssl_verify(), proxies=req_proxies)
        if r.status_code == 429:
            IA_LIMITER.trigger_429()
            max_r = cfg_get('ia_429_max_retries', 1)
            if _retries < max_r:
                logging.warning(f"IA 429 on {url[:80]}, retry {_retries+1}")
                time.sleep(cfg_get('ia_429_backoff', 60))
                return ia_get(url, timeout, headers, stream, _retries+1)
        return r
    except requests.exceptions.InvalidSchema:
        logging.error("❌ 缺少 PySocks 依赖，无法使用 Socks5 代理。请在终端执行: pip install PySocks")
        return None
    except requests.exceptions.SSLError as e:
        logging.error(f"❌ SSL 证书验证失败: {e}")
        return None
    except Exception as e:
        logging.warning(f"IA GET 失败 {url[:80]}: {e}")
        return None


# ========== 任务并发闸门 ==========

class ConcurrencyLimiter:
    def __init__(self, max_concurrent=1):
        self.max_concurrent = max(1, int(max_concurrent))
        self.current = 0
        self.lock = threading.Lock()
        self.cv = threading.Condition(self.lock)

    def set_max(self, n):
        with self.lock:
            try: n = int(n)
            except: n = 1
            self.max_concurrent = max(1, min(n, 10))
            self.cv.notify_all()

    def acquire(self, timeout=60, should_stop=None):
        """【Bug 22 修复】获取并发槽位。

        should_stop: 可选的 0 参回调，每次 0.5 秒轮询时调用。
                     返回 True 表示外部希望中止等待（任务被删/替换/暂停）。
                     None（默认）表示不检查（向后兼容，行为与原来等价）。

        实现：0.5 秒轮询 current < max_concurrent，避免长时间 cv.wait 无法响应取消。
        超时行为：超过 timeout 仍无槽位 → 抛 Exception（与原来一致）。
        取消行为：should_stop() 返回 True → 抛 Exception("任务已被取消")。
        """
        deadline = time.time() + timeout
        while True:
            # 【Bug 22】每轮先检查外部取消信号
            if should_stop is not None:
                try:
                    if should_stop():
                        raise Exception("任务已被取消")
                except Exception:
                    # should_stop 本身抛异常也视为要停止（保守处理）
                    raise Exception("任务已被取消")

            with self.lock:
                if self.current < self.max_concurrent:
                    self.current += 1
                    return True
                remaining = deadline - time.time()
                wait_time = min(0.5, max(0.0, remaining))

            if time.time() >= deadline:
                raise Exception("等待并发槽位超时，请清理其他卡死的任务后重试。")

            if wait_time > 0:
                time.sleep(wait_time)

    def release(self):
        with self.lock:
            if self.current > 0: self.current -= 1
            self.cv.notify_all()

    def status(self):
        with self.lock: return {'current': self.current, 'max': self.max_concurrent}
CONCURRENCY_LIMITER = ConcurrencyLimiter(1)


# ========== IA 配置文件 / CLI 环境 / S3 凭证 ==========

def ensure_ia_config():
    acc = get_active_account()
    if not acc: return False
    ia_dir = os.path.join(os.path.expanduser("~"), ".config", "internetarchive")
    os.makedirs(ia_dir, exist_ok=True)
    ini_file = os.path.join(ia_dir, "ia.ini")
    method = acc.get("method", "s3")
    if method == "s3" and acc.get("s3_access") and acc.get("s3_secret"):
        with open(ini_file, 'w', encoding='utf-8') as f: f.write(f"[s3]\naccess = {acc['s3_access']}\nsecret = {acc['s3_secret']}\n")
        return True
    elif method == "email" and acc.get("email") and acc.get("password"):
        try:
            ia.configure(acc["email"], acc["password"])
            return True
        except: return False
    return False


def get_cli_env(proxy_url=None):
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env["COLUMNS"] = "500"
    env["TERM"] = "xterm-256color"
    cfg = get_config()

    if get_ssl_verify():
        if CERTIFI_AVAILABLE:
            env["REQUESTS_CA_BUNDLE"] = certifi.where()
            env["CURL_CA_BUNDLE"] = certifi.where()
            env["SSL_CERT_FILE"] = certifi.where()
        env.pop("PYTHONHTTPSVERIFY", None)
        env.pop("IA_IGNORE_SSL", None)
    else:
        env["REQUESTS_CA_BUNDLE"] = ""; env["CURL_CA_BUNDLE"] = ""; env["SSL_CERT_FILE"] = ""
        env["PYTHONHTTPSVERIFY"] = "0"; env["IA_IGNORE_SSL"] = "1"

    if not cfg.get('download_via_proxy', True):
        env.pop("http_proxy", None)
        env.pop("https_proxy", None)
        env.pop("HTTP_PROXY", None)
        env.pop("HTTPS_PROXY", None)
        env.pop("ALL_PROXY", None)
        return env

    if proxy_url is None:
        proxy_url = cfg.get('proxy', '').strip()
    if proxy_url:
        proxy_h = proxy_url.replace("socks5://", "socks5h://") if proxy_url.startswith("socks5://") else proxy_url
        env["http_proxy"] = proxy_h; env["https_proxy"] = proxy_h
        env["HTTP_PROXY"] = proxy_h; env["HTTPS_PROXY"] = proxy_h
        env["ALL_PROXY"] = proxy_h
        sc_path = os.path.join(TEMP_UPLOAD_DIR, "sitecustomize.py")
        if not os.path.exists(sc_path):
            try:
                with open(sc_path, "w") as f:
                    f.write("import ssl\nssl._create_default_https_context = ssl._create_unverified_context\n")
            except: pass
        env["PYTHONPATH"] = TEMP_UPLOAD_DIR + os.pathsep + env.get("PYTHONPATH", "")
    return env


def _get_s3_credentials():
    acc = get_active_account()
    if not acc: return None, None
    if acc.get("s3_access") and acc.get("s3_secret"): return acc["s3_access"], acc["s3_secret"]
    elif acc.get("email") and acc.get("password"):
        try:
            ia.configure(acc["email"], acc["password"])
            s3_cfg = ia.get_s3_keys()
            return s3_cfg[0], s3_cfg[1]
        except: return None, None
    return None, None