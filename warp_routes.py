# warp_routes.py
# WARP 相关路由（上传核心 / 生成节点 / 代理列表 / 增删查改 / 测速）
#
# 【⏱️ 延迟与超时精细控制 - 2026-11 第 20c/20 步】
# 接入 1 个配置项（共享 cfg 快照缓存，5 秒 TTL）：
#   proxy_test_timeout (默认 8，1-60s) → /api/proxy/test 的 requests.get 超时
#
# 【默认值对比（与原代码）】
#   - proxy_test_timeout: 8 → 8 ✅ 完全一致
#
# 【🔴 本轮修复 - 2026-11】latency 计算包含配置读取时间
#   原代码 `t0 = time.time()` 在 `_get_proxy_test_timeout()` 之前，
#   导致 latency 包含了读 config.json 的时间（首次 5-10ms，用户感知虚高）。
#   修复：把 t0 = time.time() 移到 requests.get 前一行（在 getter 调用之后）。
#
# 【设计说明】
#   - _get_warp_cfg() 返回 config dict 快照（5 秒 TTL），供 _get_proxy_test_timeout 使用
#   - 用户改配置后最多 5 秒生效（下次 /api/proxy/test 调用时）
#   - config.json 损坏 / 非法值 → 回退默认值
#   - 其余 6 个路由不涉及超时配置，保持原样
#
# 【不接入的参数（有意保留硬编码）】
#   - api_warp_generate 内部的 start_custom_warp 超时已在 warp_generate.py 中接入
#     （warp_register_timeout / warp_direct_timeout / warp_probe_timeout / warp_startup_wait）
#   - api_proxies 的 autostart_saved_proxies 内部逻辑不动
#   - upload_core / add / del / active 路由无超时需求
#
# 【📝 保留记录 - 2026-11，低优先，以后修】
#   R194：api_proxy_test 的测速 URL `http://www.gstatic.com/generate_204` 硬编码
#         （Google 测试点），用户未提出配置项，保留
#   R195：api_proxy_test 只测 latency 不测下载速度，用户未提出增强，保留
#   R199：api_proxy_test 的异常 msg 是技术性错误文本，用户可能困惑（原代码行为）

import os
import shutil
import time
import requests
from flask import Blueprint, request, jsonify
from warp_core import (
    get_config, save_config, install_core_file,
    start_custom_warp, stop_warp, autostart_saved_proxies, WARP_DIR, IS_WINDOWS
)

warp_bp = Blueprint('warp', __name__)


# ========== 【⏱️ 延迟与超时精细控制】warp_routes 配置读取函数 ==========
# 共享 cfg 快照缓存（5 秒 TTL）
_WARP_CFG_CACHE = {'cfg': None, 'at': 0.0}
_CFG_CACHE_TTL = 5.0


def _get_warp_cfg():
    """返回 config dict 快照（5 秒 TTL 缓存）。

    避免 /api/proxy/test 高频调用时每次都读 config.json。
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


def _get_proxy_test_timeout():
    """返回代理测速超时（秒），默认 8，范围 1-60。

    语义说明：
      - 仅用于 /api/proxy/test 的 requests.get timeout
      - 默认 8 秒：与原 requests.get(..., timeout=8) 完全一致
      - 用户填 1 → 1 秒超时（快速失败，可能误判慢节点）
      - 用户填 60 → 60 秒超时（极慢网络下的节点也能测出延迟）
    """
    try:
        return max(1, min(int(_get_warp_cfg().get('proxy_test_timeout', 8)), 60))
    except Exception:
        return 8


@warp_bp.route('/api/warp/upload_core', methods=['POST'])
def api_upload_core():
    if 'file' not in request.files: return jsonify({"status": "error", "msg": "未选择文件"})
    file = request.files['file']
    fname = file.filename.lower()

    target_name = "usque" if "usque" in fname else "sing-box"
    if IS_WINDOWS: target_name += ".exe"

    tmp_dir = os.path.join(WARP_DIR, "tmp_extract")
    if os.path.exists(tmp_dir): shutil.rmtree(tmp_dir)
    os.makedirs(tmp_dir, exist_ok=True)
    tmp_path = os.path.join(tmp_dir, fname)
    file.save(tmp_path)

    success, err_msg = install_core_file(tmp_path, fname, target_name)
    shutil.rmtree(tmp_dir)

    if success:
        return jsonify({"status": "success", "msg": f"{target_name} 核心安装成功！"})
    else:
        return jsonify({"status": "error", "msg": f"解压或安装失败: {err_msg}"})


@warp_bp.route('/api/warp/generate', methods=['POST'])
def api_warp_generate():
    data = request.get_json() or {}
    if data.get('default'):
        ok, msg = start_custom_warp('M', 'auto', '', '', 'www.speedtest.net', '25')
    else:
        proto = data.get('protocol', 'M')
        transport = data.get('transport', 'auto')
        ip = data.get('ip', '')
        port = data.get('port', '')
        sni = data.get('sni', '')
        keepalive = data.get('keepalive', '25')
        ok, msg = start_custom_warp(proto, transport, ip, port, sni, keepalive)

    if not ok:
        msg += "\n\n💡 提示：由于网络环境复杂或端口冲突，生成失败是正常现象。请点击“确定”后多尝试几次，或者换一种协议再试！"

    return jsonify({"status": "success" if ok else "error", "msg": msg})


@warp_bp.route('/api/proxies')
def api_proxies():
    autostart_saved_proxies()
    cfg = get_config()
    return jsonify({"proxies": cfg.get('proxies', []), "active": cfg.get('proxy', '')})


@warp_bp.route('/api/proxy/add', methods=['POST'])
def api_proxy_add():
    data = request.get_json()
    cfg = get_config()
    cfg['proxies'].append({"name": data['name'], "url": data['url']})
    save_config(cfg)
    return jsonify({"status": "success"})


@warp_bp.route('/api/proxy/del', methods=['POST'])
def api_proxy_del():
    idx = request.get_json().get('index')
    cfg = get_config()
    if 'proxies' in cfg and 0 <= idx < len(cfg['proxies']):
        if cfg.get('proxy') == cfg['proxies'][idx]['url']:
            cfg['proxy'] = ""
            try:
                port = int(cfg['proxies'][idx]['url'].split(':')[-1])
                if 'http://' in cfg['proxies'][idx]['url']: port -= 1
                stop_warp(port)
            except: pass
        del cfg['proxies'][idx]
        save_config(cfg)
    return jsonify({"status": "success"})


@warp_bp.route('/api/proxy/active', methods=['POST'])
def api_proxy_active():
    url = request.get_json().get('url', '')
    cfg = get_config()
    cfg['proxy'] = url
    save_config(cfg)
    return jsonify({"status": "success"})


@warp_bp.route('/api/proxy/test')
def api_proxy_test():
    """代理测速（HEAD/GET gstatic.com/generate_204）。

    【⏱️ 延迟与超时精细控制】timeout=8 → _get_proxy_test_timeout()（默认 8，1-60 秒）
    【本轮修复】t0 移到 requests.get 前一行，避免 latency 包含配置读取时间
    """
    url = request.args.get('url')
    if not url: return jsonify({"status": "error", "msg": "参数缺失"})

    test_url = url.replace("socks5://", "socks5h://") if url.startswith("socks5://") else url
    proxies = {"http": test_url, "https": test_url}

    try:
        # 【⏱️ 延迟与超时精细控制】先读配置，再计时（避免 latency 包含配置读取时间）
        proxy_test_timeout = _get_proxy_test_timeout()
        t0 = time.time()
        r = requests.get("http://www.gstatic.com/generate_204", proxies=proxies, timeout=proxy_test_timeout)
        latency = int((time.time() - t0) * 1000)
        if r.status_code in (200, 204): 
            return jsonify({"status": "success", "latency": latency})
        else: 
            return jsonify({"status": "error", "msg": f"HTTP {r.status_code}：节点被运营商阻断，请重新生成"})
    except Exception as e:
        return jsonify({"status": "error", "msg": str(e)})