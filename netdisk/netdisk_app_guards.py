# netdisk_app_guards.py
# 下载域名白名单、异常处理、登录守卫 + IP 限流
# 【Bug 修复】handle_exception 里：
#   1. /favicon.ico 返回空 204（双保险）
#   2. 扩展 JSON 处理到 /append_files/ 前缀
# 【新增】require_login 的 allowed_endpoints 加 'favicon'，避免未登录时 favicon 被重定向
#
# 【🔴 R379 修复 - 2026-11（netdisk_app_guards 补 R379 - 第 1 轮）】
#   背景：netdisk_app.py 是拆分 netdisk_app 的"半成品"，目前运行时实际使用的是
#        netdisk_app.py 里的 app / require_login / handle_exception。本文件是
#        拆分后的目标子文件，等 netdisk_app.py 改造为汇总文件后才生效。
#   问题：netdisk_app.py 的 handle_exception 已加 R379（/upload 和 /append_files/
#        区分 GET/POST），但本文件（netdisk_app_guards.py）还是旧版：
#        - /upload 和 /append_files/ 所有请求（含 GET）都走 JSON
#        - GET 页面渲染出错时返回纯文本 JSON，用户看不懂
#   修复：/upload 和 /append_files/ 前缀判断加 `and request.method != 'GET'`：
#     - GET（页面渲染出错）→ return str(e), 500（HTML 错误页）
#     - POST（表单提交出错）→ JSON（前端 xhr.onload 能正确显示 res.msg）
#   与 netdisk_app.py 的 R379 完全一致。
#
# 【📝 保留记录 - 2026-11，低优先，以后修】
#   R466（本轮记录）：本文件与 netdisk_app.py 的 require_login / handle_exception
#        当前完全一致（除 handle_exception 的 /upload、/append_files/ 之外）。
#        改造 netdisk_app.py 为汇总文件后，本文件就是唯一实现，避免分叉。
#
# 【📝 同源问题记录 - 其他文件，后续统一修】
#   - 无

import time
from urllib.parse import urlparse

from flask import request, jsonify, session, redirect, url_for

from netdisk_app_core import app
from netdisk_core import get_config, IP_REQUEST_LOG


def _is_allowed_download_host(url):
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or '').lower()
        if host == 'archive.org' or host.endswith('.archive.org'): return True
        cfg_cdn = (get_config().get('cdn_prefix', '') or '').strip()
        if cfg_cdn and url.startswith(cfg_cdn): return True
        return False
    except: return False


@app.errorhandler(Exception)
def handle_exception(e):
    # 【Bug 修复】/favicon.ico 返回空 204（双保险，core 里也有路由）
    if request.path == '/favicon.ico':
        return '', 204
    # 【Bug 修复】扩展 JSON 处理到 /append_files/ 前缀
    # 【R379 修复 - 2026-11】/upload 和 /append_files/ 区分 GET/POST：
    #   - GET（页面渲染出错，如 Jinja 变量漏传）→ return str(e), 500（HTML 错误页，
    #     用户能看到错误原因，比 JSON 字符串更友好）
    #   - POST（表单提交出错）→ JSON（前端能正确显示 res.msg）
    if (request.path.startswith('/api/')
            or (request.path == '/upload' and request.method != 'GET')
            or (request.path.startswith('/append_files/') and request.method != 'GET')
            or request.headers.get('Content-Type') == 'application/json'):
        return jsonify({"status": "error", "msg": str(e)}), 200
    return str(e), 500


@app.before_request
def require_login():
    # 【新增】allowed_endpoints 加 'favicon'，避免未登录时 favicon 被重定向
    allowed_endpoints = ['login', 'static', 'local_auth', 'warp_page', 'serve_thumb', 'api_download_proxy', 'api_preview', 'api_install_deps', 'favicon']
    allowed_prefixes = ['/thumb/', '/api/proxies', '/api/proxy/', '/api/warp/']

    ep = request.endpoint or ''
    ep_name = ep.split('.')[-1]

    if ep_name in allowed_endpoints or any(request.path.startswith(p) for p in allowed_prefixes): return

    cfg = get_config()
    local_pwd = cfg.get('local_pwd', '')
    is_local = request.remote_addr in ['127.0.0.1', '::1']
    ip = request.remote_addr
    now = time.time()
    IP_REQUEST_LOG[ip] = [t for t in IP_REQUEST_LOG[ip] if now - t < 1]
    if len(IP_REQUEST_LOG[ip]) > 20: return "请求过于频繁", 429
    IP_REQUEST_LOG[ip].append(now)
    if local_pwd and not is_local:
        if not session.get('local_authed'): return redirect(url_for('views.local_auth'))
    if not session.get('logged_in'):
        if cfg.get("current") and cfg.get("current") in cfg.get("accounts", {}):
            session['logged_in'] = True; session['owner'] = cfg.get("current")
        else: return redirect(url_for('views.login'))