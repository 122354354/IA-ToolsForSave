# warp_generate.py
# 生成临时/自定义 WARP 节点
# 【重要】所有对进程表的操作都通过 warp_config.WIREPROXY_PROCESSES 模块属性访问
# 【P0-22 修复 - 2026-11】M 协议生成时 netdns=cgo → netdns=go
# 【P0-23 诊断 - 2026-11】加详细日志
# 【🔴 G-39 修复】stop_warp 误杀用户已有 W 节点（_TEMP_PROXY_PORTS 追踪）
# 【🔴 G-40 修复】get_available_proxy 返回失效 M 节点做跳板（跳过 M 节点，只探测 W）
# 【🔴 G-41 修复 - 2026-11】requests.post 自动读环境变量 http_proxy → 用失效代理注册
#                          修复：所有 requests 调用显式传 proxies={}（空 dict 禁用环境变量）
# 【G-40 改进】W 节点探测失败时尝试下一个 W 节点
#
# 【⏱️ 延迟与超时精细控制 - 2026-11 第 20b/20 步】
# 接入 4 个配置项（共享 cfg 快照缓存，5 秒 TTL）：
#   warp_register_timeout / warp_direct_timeout / warp_probe_timeout / warp_startup_wait
#
# 【🔴 B 级第 7 次生成 - 2026-11（已完成）】
# 新增 3 个配置项：warp_usque_startup_timeout / dns_check_timeout / warp_reg_request_timeout
#
# 【🔴 R273 修复 - 2026-11（本轮）】
#   warp_reg_request_timeout 默认值从 10 改为 15。
#   原因：
#     - 原 get_available_proxy 临时节点 requests.post(timeout=10) = 10 秒
#     - 原 start_custom_warp W 协议 requests.post(timeout=15) = 15 秒
#     - 第 7 次统一为默认 10 → W 协议从 15 秒变 10 秒（慢网络用户可感知退步）
#   修复：默认 15 → W 协议与原完全一致；临时节点原 10 → 15（多等 5 秒，成功概率更高，无副作用）
#
# 【默认值对比（与原代码）】
#   - warp_register_timeout: 120 → 120  ✅ 完全一致
#   - warp_direct_timeout  : 30  → 30   ✅ 完全一致
#   - warp_probe_timeout   : 10  → 10   ✅ 完全一致
#   - warp_startup_wait    : 1.0 → 1.0  ✅ 完全一致（W 协议启动等待）
#   - warp_usque_startup_timeout: 15 → 15 ✅ 完全一致（30 × 0.5 = 15 秒）
#   - dns_check_timeout    : 5   → 5    ✅ 完全一致
#   - warp_reg_request_timeout: 15 → 15 ⚠️ 临时节点原 10 → 15（多等 5 秒，成功概率更高）

import os
import json
import time
import random
import base64
import socket
import logging
import subprocess

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

try:
    import certifi
except ImportError:
    certifi = None

from warp_crypto import x25519, generate_random_device, get_best_warp_ips
import warp_config
from warp_config import WARP_DIR, get_config, save_config, is_port_open, is_port_available
from warp_binaries import get_singbox_binary, get_usque_binary
from warp_process import stop_warp


# ========== 【⏱️ 延迟与超时精细控制】warp_generate 7 个配置读取函数 ==========
# 共享 cfg 快照缓存（5 秒 TTL），避免 7 个 getter 各读一次 config
_WARP_CFG_CACHE = {'cfg': None, 'at': 0.0}
_CFG_CACHE_TTL = 5.0


def _get_warp_cfg():
    """返回 config dict 快照（5 秒 TTL 缓存）。"""
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


def _get_warp_register_timeout():
    """返回 usque 走代理注册超时（秒），默认 120，范围 30-600。"""
    try:
        return max(30, min(int(_get_warp_cfg().get('warp_register_timeout', 120)), 600))
    except Exception:
        return 120


def _get_warp_direct_timeout():
    """返回 usque 直连注册超时（秒），默认 30，范围 10-300。"""
    try:
        return max(10, min(int(_get_warp_cfg().get('warp_direct_timeout', 30)), 300))
    except Exception:
        return 30


def _get_warp_probe_timeout():
    """返回代理探测 cloudflare API 超时（秒），默认 10，范围 3-60。"""
    try:
        return max(3, min(int(_get_warp_cfg().get('warp_probe_timeout', 10)), 60))
    except Exception:
        return 10


def _get_warp_startup_wait():
    """返回 sing-box 启动后等待监听（秒），默认 1.0，范围 0.3-10.0。"""
    try:
        return max(0.3, min(float(_get_warp_cfg().get('warp_startup_wait', 1.0)), 10.0))
    except Exception:
        return 1.0


def _get_warp_usque_startup_timeout():
    """返回 usque 启动检测总超时（秒），默认 15，范围 5-120。"""
    try:
        return max(5, min(int(_get_warp_cfg().get('warp_usque_startup_timeout', 15)), 120))
    except Exception:
        return 15


def _get_dns_check_timeout():
    """返回 DNS 预检超时（秒），默认 5，范围 1-60。"""
    try:
        return max(1, min(int(_get_warp_cfg().get('dns_check_timeout', 5)), 60))
    except Exception:
        return 5


def _get_warp_reg_request_timeout():
    """返回 WARP 设备注册请求超时（秒），默认 15，范围 5-60。

    【R273 修复 - 2026-11】默认值从 10 改为 15。
    原因：
      - 原 get_available_proxy 临时节点 requests.post(timeout=10) = 10 秒
      - 原 start_custom_warp W 协议 requests.post(timeout=15) = 15 秒
      - 第 7 次统一为默认 10 → W 协议从 15 秒变 10 秒（慢网络用户可感知退步）
    修复：
      - 默认 15 → W 协议与原完全一致
      - 临时节点原 10 → 15（多等 5 秒，成功概率更高，无副作用）
    """
    try:
        return max(5, min(int(_get_warp_cfg().get('warp_reg_request_timeout', 15)), 60))
    except Exception:
        return 15


_TEMP_PROXY_PORTS = set()


def _log_env_proxy():
    """【G-41】打印当前环境变量里的代理，便于诊断。"""
    keys = ['http_proxy', 'https_proxy', 'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY']
    env_proxy = {k: os.environ.get(k, '') for k in keys if os.environ.get(k)}
    if env_proxy:
        logging.warning(f"[环境变量] 检测到系统级代理（会被 requests 自动使用，可能是残留）: {env_proxy}")
    else:
        logging.info(f"[环境变量] 无系统级代理污染 ✅")


def _check_dns_resolution(host="api.cloudflareclient.com", timeout=None):
    """DNS 预检。timeout 默认 None → 内部读 dns_check_timeout 配置（默认 5 秒）。"""
    if timeout is None:
        timeout = _get_dns_check_timeout()
    try:
        old_timeout = socket.getdefaulttimeout()
        socket.setdefaulttimeout(timeout)
        try:
            ip = socket.gethostbyname(host)
            return True, ip
        finally:
            socket.setdefaulttimeout(old_timeout)
    except Exception as e:
        return False, str(e)


def _probe_api_via_proxy(proxy_url, timeout=None):
    """通过代理 HEAD 请求 api.cloudflareclient.com，验证代理能否访问 API。

    timeout 默认 None → 内部读 warp_probe_timeout 配置（默认 10 秒）。
    """
    if timeout is None:
        timeout = _get_warp_probe_timeout()
    try:
        proxies = {"http": proxy_url, "https": proxy_url}
        r = requests.head(
            "https://api.cloudflareclient.com/",
            proxies=proxies,
            timeout=timeout,
            verify=False,
            allow_redirects=False,
        )
        return True, f"HTTP {r.status_code}"
    except Exception as e:
        return False, f"{type(e).__name__}: {str(e)[:200]}"


def get_available_proxy():
    """返回可用代理 URL（只返回 W 节点，跳过 M 节点）。

    【G-41 修复】requests.post 传 proxies={}，禁用环境变量污染。
    【R273 修复】临时节点注册请求超时默认 15 秒（原 10 → 15，多等 5 秒但更稳）。
    """
    _log_env_proxy()
    cfg = get_config()

    # ========== 1. 收集候选 W 节点 ==========
    candidates = []
    active_url = (cfg.get('proxy') or '').strip()

    if active_url and '127.0.0.1' in active_url:
        if active_url.startswith('http://'):
            candidates.append(('用户启用的 W 节点', active_url))
        elif active_url.startswith('socks5://'):
            logging.info(f"[get_available_proxy] 用户启用的节点是 M 节点（socks5://），跳过: {active_url}")

    for p in cfg.get('proxies', []):
        url = p.get('url', '')
        if not ('127.0.0.1' in url):
            continue
        if not url.startswith('http://'):
            logging.info(f"[get_available_proxy] 跳过 M 节点（用 W 节点做跳板更稳）: {url}")
            continue
        if url == active_url:
            continue
        candidates.append(('W 节点', url))

    # ========== 2. 逐个探测候选 ==========
    for label, url in candidates:
        try:
            port = int(url.split(':')[-1]) - 1
            if not is_port_open(port):
                logging.info(f"[get_available_proxy] {label} socks 端口 {port} 未监听，跳过: {url}")
                continue

            logging.info(f"[get_available_proxy] 探测 {label}: {url}")
            ok, result = _probe_api_via_proxy(url)
            if ok:
                logging.info(f"[get_available_proxy] ✅ {label} {url} 可用: {result}")
                return url
            else:
                logging.info(f"[get_available_proxy] ❌ {label} {url} 探测失败: {result}（尝试下一个）")
        except Exception as e:
            logging.warning(f"[get_available_proxy] {label} {url} 异常: {e}")

    # ========== 3. 所有 W 节点都失败 → 临时生成 ==========
    logging.info("🔄 无可用 W 节点，正在注册全新 WARP 节点...")
    bin_path = get_singbox_binary()
    if not bin_path:
        return None

    temp_port = random.randint(41000, 42000)
    while not is_port_available(temp_port) or not is_port_available(temp_port + 1):
        temp_port = random.randint(41000, 42000)

    priv_key_bytes = os.urandom(32)
    base_point = b'\x09' + b'\x00' * 31
    pub_key_bytes = x25519(priv_key_bytes, base_point)
    priv_key_b64 = base64.b64encode(priv_key_bytes).decode('utf-8')
    pub_key_b64 = base64.b64encode(pub_key_bytes).decode('utf-8')

    model, os_ver, ua, install_id, fcm_token = generate_random_device()
    req_data = {
        "key": pub_key_b64, "install_id": install_id, "fcm_token": fcm_token,
        "tos": "2021-09-14T10:27:44.000+08:00", "model": model,
        "type": "Android", "locale": "en_US"
    }
    req_headers = {"User-Agent": ua, "Content-Type": "application/json"}

    try:
        # 【G-41 核心】proxies={} 禁用环境变量污染
        # 【R273 修复】timeout=10 → _get_warp_reg_request_timeout()（默认 15 秒）
        reg_timeout = _get_warp_reg_request_timeout()
        logging.info(f"[get_available_proxy] 注册临时 WARP 设备（proxies={{}} 禁用环境变量，timeout={reg_timeout}s）...")
        resp = requests.post(
            "https://api.cloudflareclient.com/v0a2158/reg",
            json=req_data, headers=req_headers, timeout=reg_timeout, verify=False,
            proxies={}  # ← 关键：禁用环境变量
        )
        res = resp.json()
        if "config" not in res:
            logging.error(f"[get_available_proxy] 临时 WARP 注册被拒: {res}")
            return None

        peer_pub = res['config']['peers'][0]['public_key']
        client_v4 = res['config']['interface']['addresses']['v4']
        client_id = res['config']['client_id']
        reserved_bytes = [int(b) for b in base64.b64decode(client_id)[:3]]

        cf_ips = ["162.159.192.1", "162.159.193.10", "188.114.99.1", "162.159.192.5"]

        wg_outbound = {
            "type": "wireguard", "tag": "warp-out",
            "server": random.choice(cf_ips), "server_port": 2408,
            "local_address": [f"{client_v4}/32"], "private_key": priv_key_b64,
            "peer_public_key": peer_pub, "reserved": reserved_bytes,
            "mtu": 1280, "domain_strategy": "prefer_ipv4"
        }

        config = {
            "log": {"level": "error"},
            "dns": {"servers": [{"tag": "cf", "address": "1.1.1.1", "detour": "warp-out"}]},
            "inbounds": [
                {"type": "socks", "tag": "socks-in", "listen": "127.0.0.1", "listen_port": temp_port},
                {"type": "http", "tag": "http-in", "listen": "127.0.0.1", "listen_port": temp_port + 1}
            ],
            "outbounds": [wg_outbound, {"type": "direct", "tag": "direct"}],
            "route": {"rules": [{"protocol": "dns", "outbound": "warp-out"}], "final": "warp-out"}
        }
        conf_path = os.path.join(WARP_DIR, f"dl_{temp_port}.json")
        with open(conf_path, "w") as f:
            json.dump(config, f, indent=4)

        proc = subprocess.Popen(
            [bin_path, "run", "-c", conf_path],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=WARP_DIR
        )
        time.sleep(_get_warp_startup_wait())

        if is_port_open(temp_port):
            warp_config.WIREPROXY_PROCESSES[temp_port] = proc
            _TEMP_PROXY_PORTS.add(temp_port + 1)
            temp_url = f"http://127.0.0.1:{temp_port + 1}"
            logging.info(f"[get_available_proxy] 临时 W 节点已生成: {temp_url}")

            ok, result = _probe_api_via_proxy(temp_url)
            if ok:
                logging.info(f"[get_available_proxy] ✅ 临时节点探测成功: {result}")
                return temp_url
            else:
                logging.error(f"[get_available_proxy] ❌ 临时节点探测失败: {result}")
                try:
                    stop_warp(temp_port)
                    _TEMP_PROXY_PORTS.discard(temp_port + 1)
                except:
                    pass
                return None
        else:
            proc.terminate()
            return None

    except Exception as e:
        logging.error(f"生成临时节点失败: {e}")
        return None


def start_custom_warp(protocol, transport, endpoint_ip, endpoint_port, sni, keepalive):
    """生成自定义 WARP 节点（W 或 M 协议）。

    【R273 修复】W 协议注册请求超时默认 15 秒（与原硬编码 15 秒完全一致）。
    """
    _log_env_proxy()
    cfg = get_config()

    try:
        target_port = int(endpoint_port) if endpoint_port else (443 if protocol == 'M' else 2408)
    except:
        target_port = 443 if protocol == 'M' else 2408

    if protocol == 'W':
        bin_path = get_singbox_binary()
        if not bin_path:
            return False, "sing-box 核心未就绪，请手动上传"

        priv_key_bytes = os.urandom(32)
        base_point = b'\x09' + b'\x00' * 31
        pub_key_bytes = x25519(priv_key_bytes, base_point)
        priv_key_b64 = base64.b64encode(priv_key_bytes).decode('utf-8')
        pub_key_b64 = base64.b64encode(pub_key_bytes).decode('utf-8')

        try:
            model, os_ver, ua, install_id, fcm_token = generate_random_device()
            req_data = {
                "key": pub_key_b64, "install_id": install_id, "fcm_token": fcm_token,
                "tos": "2021-09-14T10:27:44.000+08:00", "model": model,
                "type": "Android", "locale": "en_US"
            }
            req_headers = {"User-Agent": ua, "Content-Type": "application/json"}
            # 【G-41】proxies={} 禁用环境变量污染
            # 【R273 修复】timeout=15 → _get_warp_reg_request_timeout()（默认 15，与原完全一致）
            reg_timeout = _get_warp_reg_request_timeout()
            logging.info(f"[W 生成] WARP 设备注册（timeout={reg_timeout}s）...")
            resp = requests.post(
                "https://api.cloudflareclient.com/v0a2158/reg",
                json=req_data, headers=req_headers, timeout=reg_timeout, verify=False,
                proxies={}
            )
            res = resp.json()
            if "config" not in res:
                return False, "WARP 注册被拒绝，网络异常"

            peer_pub = res['config']['peers'][0]['public_key']
            client_v4 = res['config']['interface']['addresses']['v4']
            client_v6 = res['config']['interface']['addresses'].get('v6', '')
            client_id = res['config']['client_id']
            reserved_bytes = [int(b) for b in base64.b64decode(client_id)[:3]]
        except Exception as e:
            return False, f"注册异常: {e}"

        WARP_PORTS = [2408, 500, 854, 1701, 443]
        if endpoint_port and int(endpoint_port) not in WARP_PORTS:
            WARP_PORTS.insert(0, int(endpoint_port))

        if not endpoint_ip:
            best_ips = get_best_warp_ips()
            host = best_ips[0] if best_ips else "162.159.192.1"
        else:
            host = endpoint_ip
            if not host.replace('.', '').isdigit():
                try:
                    host = socket.gethostbyname(host)
                except:
                    host = "162.159.192.1"

        wg_outbound = {
            "type": "wireguard", "tag": "warp-out", "server": host,
            "server_port": target_port,
            "local_address": [f"{client_v4}/32"], "private_key": priv_key_b64,
            "peer_public_key": peer_pub, "reserved": reserved_bytes,
            "mtu": 1280, "domain_strategy": "prefer_ipv4"
        }
        if client_v6:
            wg_outbound["local_address"].append(f"{client_v6}/128")

        success_started = False
        err_msg = ""

        for try_attempt in range(3):
            port = random.randint(40000, 55000)
            retries = 0
            while not is_port_available(port) or not is_port_available(port + 1):
                port = random.randint(40000, 55000)
                retries += 1
                if retries > 50:
                    break

            config = {
                "log": {"level": "warn"},
                "dns": {"servers": [{"tag": "cf", "address": "1.1.1.1", "detour": "warp-out"}]},
                "inbounds": [
                    {"type": "socks", "tag": "socks-in", "listen": "127.0.0.1", "listen_port": port},
                    {"type": "http", "tag": "http-in", "listen": "127.0.0.1", "listen_port": port + 1}
                ],
                "outbounds": [wg_outbound, {"type": "direct", "tag": "direct"}],
                "route": {"rules": [{"protocol": "dns", "outbound": "warp-out"}], "final": "warp-out"}
            }

            for try_port in WARP_PORTS:
                wg_outbound["server_port"] = try_port
                config["outbounds"][0] = wg_outbound
                conf_path = os.path.join(WARP_DIR, f"warp_{port}.json")
                with open(conf_path, "w") as f:
                    json.dump(config, f, indent=4)
                cmd = [bin_path, "run", "-c", conf_path]

                proc = subprocess.Popen(
                    cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True, cwd=WARP_DIR
                )
                time.sleep(_get_warp_startup_wait())
                if is_port_open(port):
                    success_started = True
                    target_port = try_port
                    break
                else:
                    proc.terminate()
                    try:
                        _, stderr = proc.communicate(timeout=1)
                        err_msg = stderr
                    except:
                        err_msg = "启动失败"

            if success_started:
                break

        if not success_started:
            if "address already in use" in err_msg:
                return False, "生成失败：内部端口被占用，请稍后再试。"
            return False, f"sing-box 启动失败: {err_msg[-150:]}"

        env = None
        warp_config.WIREPROXY_PROCESSES[port] = proc

    else:  # protocol == 'M'
        bin_path = get_usque_binary()
        if not bin_path:
            return False, "usque 核心未就绪，请手动上传"

        port = random.randint(40000, 55000)
        retries = 0
        while not is_port_available(port):
            port = random.randint(40000, 55000)
            retries += 1
            if retries > 50:
                break

        conf_file = os.path.join(WARP_DIR, "config.json")
        key_file = os.path.join(WARP_DIR, "key.json")

        dns_ok, dns_result = _check_dns_resolution("api.cloudflareclient.com")
        if not dns_ok:
            return False, (
                f"❌ DNS 解析失败：{dns_result}\n\n"
                f"请检查：\n"
                f"1. Termux 的 $PREFIX/etc/resolv.conf 是否包含 nameserver 1.1.1.1\n"
                f"2. termux-exec 是否已安装（pkg install termux-exec）\n"
                f"3. Android 系统 DNS（getprop net.dns1 应该有 IP）"
            )
        logging.info(f"[DNS 检查] api.cloudflareclient.com → {dns_result}")

        env = os.environ.copy()
        env["GODEBUG"] = "netdns=go"
        env["RUST_LOG"] = "info"

        env.pop("http_proxy", None)
        env.pop("https_proxy", None)
        env.pop("HTTP_PROXY", None)
        env.pop("HTTPS_PROXY", None)
        env.pop("ALL_PROXY", None)

        if certifi is not None:
            try:
                env["SSL_CERT_FILE"] = certifi.where()
                env["SSL_CERT_DIR"] = os.path.dirname(certifi.where())
                logging.info(f"[SSL] SSL_CERT_FILE = {env['SSL_CERT_FILE']}")
            except Exception as se:
                logging.warning(f"[SSL] certifi.where() 失败: {se}")
        else:
            logging.warning(f"[SSL] certifi 未安装，跳过 SSL_CERT_FILE 设置")

        if os.path.exists(conf_file):
            try:
                os.remove(conf_file)
            except:
                pass
        if os.path.exists(key_file):
            try:
                os.remove(key_file)
            except:
                pass

        reg_cmd = [bin_path, "register", "--accept-tos"]
        reg_success = False
        direct_err = ""

        logging.info(f"[M 生成] ===== 开始生成 M 节点 =====")
        logging.info(f"[M 生成] 参数: transport={transport}, endpoint_ip={endpoint_ip}, endpoint_port={endpoint_port}, sni={sni}")
        logging.info(f"[M 生成] 直连尝试 usque register（无代理）...")

        direct_timeout = _get_warp_direct_timeout()
        try:
            reg_proc = subprocess.run(
                reg_cmd, cwd=WARP_DIR, env=env,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, timeout=direct_timeout
            )
            logging.info(f"[M 生成] 直连返回码: {reg_proc.returncode}")
            logging.info(f"[M 生成] 直连输出:\n{(reg_proc.stdout or '')[:500]}")
            if reg_proc.returncode == 0:
                reg_success = True
            else:
                direct_err = (reg_proc.stdout or '').strip()
        except subprocess.TimeoutExpired:
            logging.warning(f"[M 生成] 直连 usque register 超时 {direct_timeout} 秒")
            direct_err = f"直连超时 {direct_timeout} 秒（可能 DNS 解析失败）"
        except Exception as e:
            logging.warning(f"[M 生成] 直连异常: {e}")
            direct_err = str(e)

        temp_url = None
        is_temp = False

        if not reg_success:
            logging.info(f"[M 生成] 直连失败，尝试 W 跳板...")
            temp_url = get_available_proxy()
            logging.info(f"[M 生成] get_available_proxy 返回: {temp_url}")

            if not temp_url:
                logging.error(f"[M 生成] 无可用代理")
                return False, (
                    f"注册失败：直连不可用，且无可用 W 节点做跳板。\n"
                    f"直连错误: {direct_err[-200:]}\n\n"
                    f"建议：\n"
                    f"1. 在「🌐 代理」生成一个 W 节点\n"
                    f"2. 或删除失效的 M 节点释放端口\n"
                    f"3. 查看终端日志了解详情"
                )

            try:
                http_port = int(temp_url.split(':')[-1])
                is_temp = http_port in _TEMP_PROXY_PORTS
            except:
                is_temp = False
            logging.info(f"[M 生成] 代理类型: {'临时生成' if is_temp else '用户已有'}")

            logging.info(f"[M 生成] 通过代理 {temp_url} 探测 api.cloudflareclient.com...")
            probe_ok, probe_result = _probe_api_via_proxy(temp_url)
            logging.info(f"[M 生成] 代理探测结果: {'成功' if probe_ok else '失败'} - {probe_result}")

            if not probe_ok:
                if is_temp:
                    try:
                        stop_warp(http_port - 1)
                        _TEMP_PROXY_PORTS.discard(http_port)
                    except:
                        pass
                return False, (
                    f"注册失败：代理 {temp_url} 无法访问 Cloudflare API。\n"
                    f"探测错误: {probe_result}\n\n"
                    f"原因：WARP 出口访问 Cloudflare 自己的 API 可能被 RST（防滥用）\n"
                    f"建议：\n"
                    f"1. 在「🌐 代理」中删除该节点\n"
                    f"2. 重新生成一个 W 节点（新 WARP 设备 → 新出口 IP）\n"
                    f"3. 再试生成 M 节点"
                )

            env["http_proxy"] = temp_url
            env["https_proxy"] = temp_url
            env["HTTP_PROXY"] = temp_url
            env["HTTPS_PROXY"] = temp_url
            reg_timeout = _get_warp_register_timeout()
            logging.info(f"[M 生成] 通过代理执行 usque register（timeout={reg_timeout}s）...")

            try:
                reg_proc = subprocess.run(
                    reg_cmd, cwd=WARP_DIR, env=env,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, timeout=reg_timeout
                )
                logging.info(f"[M 生成] 跳板返回码: {reg_proc.returncode}")
                logging.info(f"[M 生成] 跳板输出:\n{(reg_proc.stdout or '')[:500]}")
                if reg_proc.returncode != 0:
                    raise Exception((reg_proc.stdout or '').strip())
            except subprocess.TimeoutExpired:
                logging.error(f"[M 生成] 跳板 usque register 超时 {reg_timeout} 秒")
                if is_temp:
                    try:
                        stop_warp(http_port - 1)
                        _TEMP_PROXY_PORTS.discard(http_port)
                    except:
                        pass
                return False, (
                    f"注册超时：通过代理 {temp_url} 执行 usque register 超过 {reg_timeout} 秒。\n"
                    f"代理探测成功（{probe_result}），但 usque 走代理注册失败。\n\n"
                    f"建议：查看终端日志中的 [M 生成] 输出"
                )
            except Exception as proxy_err:
                logging.error(f"[M 生成] 跳板失败: {proxy_err}")
                if is_temp:
                    try:
                        stop_warp(http_port - 1)
                        _TEMP_PROXY_PORTS.discard(http_port)
                    except:
                        pass
                return False, (
                    f"注册失败：直连和代理均不可用。\n"
                    f"直连错误: {direct_err[-100:]}\n"
                    f"代理错误: {str(proxy_err)[-200:]}"
                )
            finally:
                env.pop("http_proxy", None)
                env.pop("https_proxy", None)
                env.pop("HTTP_PROXY", None)
                env.pop("HTTPS_PROXY", None)
                if is_temp and http_port:
                    try:
                        logging.info(f"[M 生成] 清理临时 W 节点端口 {http_port - 1}")
                        stop_warp(http_port - 1)
                        _TEMP_PROXY_PORTS.discard(http_port)
                    except:
                        pass

        transport_flag = []
        if transport == 'h2':
            transport_flag = ["--transport", "h2"]
        elif transport == 'h3':
            transport_flag = ["--transport", "h3"]

        cmd = [
            bin_path, "socks",
            "-c", conf_file,
            "-b", "127.0.0.1",
            "-p", str(port),
            "-s", sni or "www.speedtest.net",
            "--connect-port", str(target_port)
        ] + transport_flag

    try:
        if protocol == 'M':
            logging.info(f"[M 生成] 启动 usque socks: {' '.join(cmd)}")
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, cwd=WARP_DIR, env=env
            )
            ready = False
            err_msg = "生成超时"
            _usque_startup_timeout = _get_warp_usque_startup_timeout()
            _iterations = max(1, int(_usque_startup_timeout * 2))
            logging.info(f"[M 生成] usque 启动检测超时: {_usque_startup_timeout} 秒（{_iterations} 次 × 0.5 秒）")
            for i in range(_iterations):
                time.sleep(0.5)
                if proc.poll() is not None:
                    err_msg = (proc.stdout.read() or '').strip()
                    logging.error(f"[M 生成] usque 进程提前退出，输出:\n{err_msg[:500]}")
                    break
                if is_port_open(port):
                    ready = True
                    logging.info(f"[M 生成] usque 端口 {port} 已监听（{i*0.5:.1f}s）")
                    break

            if proc.poll() is not None or not ready:
                return False, f"生成失败: {err_msg[-200:] if err_msg else '无响应'}"

            warp_config.WIREPROXY_PROCESSES[port] = proc

        if protocol == 'W':
            proxy_url = f"http://127.0.0.1:{port + 1}"
            name_suffix = f"{protocol}_{transport}_{port + 1}"
            result_msg = f"{name_suffix} 节点生成成功！\n(本地监听: {port + 1} | 远端连接: {target_port})"
        else:
            proxy_url = f"socks5://127.0.0.1:{port}"
            name_suffix = f"{protocol}_{transport}_{port}"
            result_msg = f"{name_suffix} 节点生成成功！\n(本地监听: {port} | 远端连接: {target_port})"

        new_proxy = {"name": f"WARP_{name_suffix}", "url": proxy_url}
        cfg['proxies'].append(new_proxy)
        save_config(cfg)
        logging.info(f"[M 生成] ✅ 成功: {new_proxy}")
        return True, result_msg
    except Exception as e:
        logging.error(f"[M 生成] 异常: {e}")
        return False, f"生成异常: {e}"