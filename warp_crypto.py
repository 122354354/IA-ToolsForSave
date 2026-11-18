# warp_crypto.py
import os
import base64
import random
import string
import socket
import time
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

from warp_config import get_config

# 【⏱️ 延迟与超时精细控制 - 2026-11 第 20a/20 步】
# 接入 4 个配置项（共享 cfg 快照缓存，5 秒 TTL）：
#   1. warp_ip_concurrency (默认 20，1-100)    → IP 优选并发线程数
#   2. warp_ip_timeout     (默认 1.5，0.5-10s) → 单个 IP TCP 连接超时
#   3. warp_ip_threshold   (默认 1.5，0.5-10s) → IP 优选阈值（超过此值丢弃）
#   4. warp_ip_keep        (默认 5，1-20)      → 保留前 N 个最优 IP
#
# 【🔴 核心原则：默认行为与原来完全一致 + 共享 cfg 快照避免 IO 放大】
#   - 原代码硬编码：并发 20 / 超时 1.5 秒 / 阈值 1.5 秒 / 保留前 5 个
#   - 新默认值：完全相同
#   - 20 个并发子线程各自调 _get_warp_ip_timeout()，但共享 _WARP_CFG_CACHE
#     + get_best_warp_ips 开头预热 → 整个流程只读 1 次 config.json
#
# 【默认值对比（与原代码）】
#   - warp_ip_concurrency: 20 → 20   ✅ 完全一致
#   - warp_ip_timeout    : 1.5 → 1.5 ✅ 完全一致
#   - warp_ip_threshold  : 1.5 → 1.5 ✅ 完全一致
#   - warp_ip_keep       : 5 → 5     ✅ 完全一致
#
# 【test_ip_tcp 签名兼容性】
#   - 原签名：test_ip_tcp(ip, port=443, timeout=1.5)
#   - 新签名：test_ip_tcp(ip, port=443, timeout=None) —— None 时内部读配置
#   - 外部调用 test_ip_tcp('1.2.3.4') 仍可用（默认读配置，默认值 1.5）
#   - 外部调用 test_ip_tcp('1.2.3.4', 443, 3.0) 仍可用（显式覆盖）
#
# 【设计说明】
#   - 4 个 getter 共享 _get_warp_cfg() 返回的 cfg dict 快照（5 秒 TTL）
#   - config.json 损坏 / 非法值 → 回退默认值
#   - get_best_warp_ips 开头预热 cfg 缓存 → 20 个并发子线程全部命中 → 0 次额外 IO
#   - 用户改配置后最多 5 秒生效（下次 get_best_warp_ips 调用时）
#
# 【不接入的参数（用户未提出）】
#   - test_ip_tcp 的默认端口 443（硬编码）
#   - 返回的 999.0 延迟哨兵值（硬编码，表示超时失败）
#   - x25519 / generate_random_device / modp_inv / cswap 等密码学函数（与本配置无关）


# ========== 【⏱️ 延迟与超时精细控制】warp_crypto 4 个配置读取函数 ==========
# 共享 cfg 快照缓存（5 秒 TTL），避免 4 个 getter 各读一次 config
_WARP_CFG_CACHE = {'cfg': None, 'at': 0.0}
_CFG_CACHE_TTL = 5.0


def _get_warp_cfg():
    """返回 config dict 快照（5 秒 TTL 缓存）。

    4 个 getter 共享此缓存 → 一次读 config 服务多次读取。
    避免 get_best_warp_ips 里 20 个并发子线程各自读 config.json。
    """
    now = time.time()
    if _WARP_CFG_CACHE['cfg'] is not None and now - _WARP_CFG_CACHE['at'] < _CFG_CACHE_TTL:
        return _WARP_CFG_CACHE['cfg']
    try:
        cfg = get_config()
    except Exception:
        cfg = {}
    _WARP_CFG_CACHE['cfg'] = cfg
    _WARP_CFG_CACHE['at'] = now
    return cfg


def _get_warp_ip_concurrency():
    """返回 IP 优选并发线程数，默认 20，范围 1-100。

    语义说明：
      - 控制 ThreadPoolExecutor 的 max_workers
      - 默认 20：与原 ThreadPoolExecutor(max_workers=20) 完全一致
      - 用户填 1 → 串行测（慢但稳定，极端保守）
      - 用户填 100 → 100 个线程并发测（快但可能 CPU 高）
    """
    try:
        return max(1, min(int(_get_warp_cfg().get('warp_ip_concurrency', 20)), 100))
    except Exception:
        return 20


def _get_warp_ip_timeout():
    """返回单个 IP TCP 连接超时（秒），默认 1.5，范围 0.5-10。

    语义说明：
      - 用于 socket.create_connection 的 timeout
      - 默认 1.5 秒：与原 test_ip_tcp(timeout=1.5) 完全一致
      - 用户填 0.5 → 极快失败（可能漏掉可用的慢 IP）
      - 用户填 10 → 等待 10 秒（极慢网络下更稳健）
    """
    try:
        return max(0.5, min(float(_get_warp_cfg().get('warp_ip_timeout', 1.5)), 10.0))
    except Exception:
        return 1.5


def _get_warp_ip_threshold():
    """返回 IP 优选阈值（秒），默认 1.5，范围 0.5-10。

    语义说明：
      - 延迟 >= 阈值的 IP 被丢弃
      - 默认 1.5 秒：与原 if latency < 1.5 完全一致
      - 通常应 <= warp_ip_timeout（超过 timeout 的 IP 都会返回 999.0）
      - 用户填更严格的值（如 1.0）→ 只保留极快 IP
      - 用户填更宽松的值（如 5.0）→ 保留更多 IP，但注意 > timeout 时无效
    """
    try:
        return max(0.5, min(float(_get_warp_cfg().get('warp_ip_threshold', 1.5)), 10.0))
    except Exception:
        return 1.5


def _get_warp_ip_keep():
    """返回保留的最优 IP 数量，默认 5，范围 1-20。

    语义说明：
      - 测速完成后保留前 N 个最优 IP
      - 默认 5：与原 results[:5] 完全一致
      - 用户填 1 → 只保留最快 1 个（最精简）
      - 用户填 20 → 保留前 20 个（容错性强，可能包含较慢的 IP）
      - 注意：如果实际可用的 IP 少于 N 个，保留实际数量（不会补空）
    """
    try:
        return max(1, min(int(_get_warp_cfg().get('warp_ip_keep', 5)), 20))
    except Exception:
        return 5


CF_OPTIMAL_IPS = [
    "162.159.192.1", "162.159.192.2", "162.159.192.3", "162.159.192.4", "162.159.192.5",
    "162.159.192.6", "162.159.192.7", "162.159.192.8", "162.159.192.9", "162.159.192.10",
    "162.159.193.1", "162.159.193.2", "162.159.193.3", "162.159.193.4", "162.159.193.5",
    "162.159.193.10", "162.159.193.11", "162.159.193.12", "162.159.193.13", "162.159.193.14",
    "188.114.96.1", "188.114.96.2", "188.114.96.3", "188.114.96.4", "188.114.96.5",
    "188.114.97.1", "188.114.97.2", "188.114.97.3", "188.114.98.1", "188.114.98.2",
    "188.114.99.1", "188.114.99.2", "188.114.99.3", "188.114.99.4", "188.114.99.5",
    "162.159.195.1", "162.159.195.2", "162.159.195.3", "162.159.195.4"
]

P = 2**255 - 19
def modp_inv(x): return pow(x, P-2, P)
def cswap(c, x, y): t = c * (x ^ y); return x ^ t, y ^ t

def x25519(k, u):
    k = bytearray(k); k[0] &= 248; k[31] &= 127; k[31] |= 64
    u_bytes = bytearray(u); u_bytes[31] &= 127
    scalar = int.from_bytes(k, 'little'); u = int.from_bytes(u_bytes, 'little')
    x_1 = u; x_2, z_2 = 1, 0; x_3, z_3 = u, 1; swap = 0
    for t in range(254, -1, -1):
        k_t = (scalar >> t) & 1; swap ^= k_t
        x_2, x_3 = cswap(swap, x_2, x_3); z_2, z_3 = cswap(swap, z_2, z_3); swap = k_t
        A = (x_2 + z_2) % P; AA = (A * A) % P; B = (x_2 - z_2) % P; BB = (B * B) % P
        E = (AA - BB) % P; C = (x_3 + z_3) % P; D = (x_3 - z_3) % P; DA = (D * A) % P; CB = (C * B) % P
        x_3 = pow((DA + CB) % P, 2, P); z_3 = (x_1 * pow((DA - CB) % P, 2, P)) % P
        x_2 = (AA * BB) % P; z_2 = (E * ((AA + (121665 * E)) % P)) % P
    x_2, x_3 = cswap(swap, x_2, x_3); z_2, z_3 = cswap(swap, z_2, z_3)
    return (x_2 * modp_inv(z_2) % P).to_bytes(32, 'little')

def generate_random_device():
    models = ["iPhone 15 Pro", "iPhone 14", "Pixel 8 Pro", "SM-S918B", "MacBookPro18,3", "Redmi K60", "OnePlus 11"]
    os_versions = ["iOS 17.2", "Android 14", "Windows 11", "macOS 14.1"]
    uas = [
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Mobile/15E148 Safari/604.1",
        "Mozilla/5.0 (Linux; Android 14; Pixel 8 Pro) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "okhttp/3.12.1"
    ]
    model = random.choice(models)
    os_ver = random.choice(os_versions)
    ua = random.choice(uas)
    install_id = ''.join(random.choices(string.ascii_letters + string.digits, k=22))
    fcm_token = f"{install_id}:APA91b" + ''.join(random.choices(string.ascii_letters + string.digits + '-_', k=100))
    return model, os_ver, ua, install_id, fcm_token

def test_ip_tcp(ip, port=443, timeout=None):
    """测试单个 IP 的 TCP 连接延迟。

    【⏱️ 延迟与超时精细控制】timeout 默认 None → 内部读 warp_ip_timeout 配置
    外部调用也可以显式传 timeout 覆盖（向后兼容）：
      - test_ip_tcp('1.2.3.4')              → timeout 用配置（默认 1.5）
      - test_ip_tcp('1.2.3.4', 443, 3.0)    → timeout=3.0
    """
    if timeout is None:
        timeout = _get_warp_ip_timeout()
    start = time.time()
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return ip, time.time() - start
    except:
        return ip, 999.0

def get_best_warp_ips():
    """并测 CF_OPTIMAL_IPS，返回最优 IP 列表。

    【⏱️ 延迟与超时精细控制】并发数 / 阈值 / 保留数全部可配
    【性能优化】开头预热 cfg 缓存，避免 20 个并发子线程各自读 config.json
    """
    # 预热 cfg 缓存（避免 20 个并发子线程同时读 config.json）
    _get_warp_cfg()

    concurrency = _get_warp_ip_concurrency()
    threshold = _get_warp_ip_threshold()
    keep = _get_warp_ip_keep()

    logging.info(f"🔍 正在并发展开 Cloudflare IP 测速(优选)（并发 {concurrency}，阈值 {threshold}s，保留前 {keep} 个）...")
    results = []
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = [executor.submit(test_ip_tcp, ip) for ip in CF_OPTIMAL_IPS]
        for future in as_completed(futures):
            ip, latency = future.result()
            if latency < threshold:
                results.append((ip, latency))

    results.sort(key=lambda x: x[1])

    if not results:
        logging.warning("⚠️ 所有优选 IP 均连接超时，将退回到默认 IP")
        return ["162.159.192.1"]

    best_ips = [ip for ip, _ in results[:keep]]
    logging.info(f"✅ 测速完成，最快IP: {best_ips[0]} (延迟 {results[0][1]*1000:.0f}ms)")
    return best_ips