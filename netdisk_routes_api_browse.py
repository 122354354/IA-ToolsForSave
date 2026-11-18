# netdisk_routes_api_browse.py
# 浏览/预览/下载代理/ID检查/拉取刷新/统计 等 API
#
# 【⏱️ 延迟与超时精细控制 - 2026-11 第 8/20 步】
# 接入 5 个原配置项：
#   proxy_dl_ip_limit / proxy_dl_timeout_connect / proxy_dl_timeout_read
#   proxy_dl_retry / preview_timeout_connect / preview_timeout_read / stream_chunk_size
#
# 【🔴 B 级第 3 次生成 - 2026-11（已完成）】
# 新增 proxy_dl_ip_window（默认 60，10-3600 秒）→ 替换 PROXY_DL_IP_LOG 的 60 秒窗口
#
# 【🔴 R279 接入 - 2026-11（本轮）】
# 新增 _get_id_check_ip_window()（默认 60，10-3600 秒，5 秒 TTL 缓存）
#   → 替换 api_check_identifier 里 IP_ID_CHECK 的 60 秒滑动窗口硬编码
#   与第 1/8 次 _NEW_SETTINGS_INT 的 ('id_check_ip_window', 60, 10, 3600) 一致
#
# 【🔴 R84/R250 修复 - 2026-11（本轮）】
#   api_download_proxy 里 `if len(PROXY_DL_IP_LOG[ip]) > ip_limit:` → `>= ip_limit:`
#   原因：`>` 实际允许 ip_limit + 1 次（用户填 200 实际 201 次），
#        而 thumb_ip_limit / id_check_ip_limit 用的是 `>=`（填 N 允许 N 次）。
#        用户视角「填 200 = 最多 200 次」更合理。
#   行为变化：用户填 200 → 之前允许 201 次，现在允许 200 次（严格 1 次）。
#   说明：原代码 off-by-one bug，本轮修复使三处限流参数语义一致。
#
# 【默认值对比（与原代码）】
#   - id_check_ip_window: 60 → 60 ✅（与原硬编码 60 秒一致）
#   - proxy_dl_ip_limit 语义: > → >= ⚠️（修复 off-by-one，严格 1 次）
#
# 【不接入的参数（有意保留硬编码）】
#   - api_check_identifier 的 ia_get(timeout=10)：故意短超时，后续可选接入
#   - api_force_refresh 内部 _fetch_and_cache_ia_files 的 timeout=15：外部函数
#   - api_user_stats / api_zip_content / api_magnet 内部超时：外部函数
#   - time.sleep(1) 在 api_download_proxy 重试间隔：用户未提出配置项

import os
import re
import time
import requests
from flask import request, jsonify, session, Response, stream_with_context

from netdisk_blueprints import api_bp
from netdisk_app import _is_allowed_download_host, PROXY_DL_IP_LOG
from netdisk_core import (
    get_config, get_db, cfg_get,
    ID_CHECK_CACHE, ID_CHECK_LOCK, IP_ID_CHECK,
    REFRESH_COOLDOWN_MAP, REFRESH_LOCK,
    ia_get, get_ssl_verify, get_req_proxies,
    _fetch_and_cache_ia_files, compute_magnet,
    fetch_zip_structure, fetch_user_stats,
)


# ========== 【⏱️ 延迟与超时精细控制】6 个带 5 秒 TTL 缓存的读取函数 ==========
_PROXY_DL_TIMEOUT_CACHE = {'value': (5, 15), 'at': 0.0}
_PROXY_DL_RETRY_CACHE = {'value': 3, 'at': 0.0}
_PROXY_DL_IP_LIMIT_CACHE = {'value': 200, 'at': 0.0}
_PROXY_DL_IP_WINDOW_CACHE = {'value': 60, 'at': 0.0}
_PREVIEW_TIMEOUT_CACHE = {'value': (10, 60), 'at': 0.0}
_STREAM_CHUNK_SIZE_CACHE = {'value': 65536, 'at': 0.0}
# 【R279 新增】id_check_ip_window 5 秒 TTL 缓存
_ID_CHECK_IP_WINDOW_CACHE = {'value': 60, 'at': 0.0}
_CFG_CACHE_TTL = 5.0


def _get_proxy_dl_timeout():
    """返回代理下载 (connect_timeout, read_timeout)，默认 (5, 15)，范围 connect 1-60 / read 5-300。"""
    now = time.time()
    if now - _PROXY_DL_TIMEOUT_CACHE['at'] < _CFG_CACHE_TTL:
        return _PROXY_DL_TIMEOUT_CACHE['value']
    try:
        c = max(1, min(int(cfg_get('proxy_dl_timeout_connect', 5)), 60))
        r = max(5, min(int(cfg_get('proxy_dl_timeout_read', 15)), 300))
    except Exception:
        c, r = 5, 15
    _PROXY_DL_TIMEOUT_CACHE['value'] = (c, r)
    _PROXY_DL_TIMEOUT_CACHE['at'] = now
    return (c, r)


def _get_proxy_dl_retry():
    """返回代理下载最大尝试次数（含首次），默认 3，范围 0-10。

    语义说明：0 → 调用方 max(1, ...) 兜底为 1 次尝试（不重试），避免 0 次尝试假成功。
    """
    now = time.time()
    if now - _PROXY_DL_RETRY_CACHE['at'] < _CFG_CACHE_TTL:
        return _PROXY_DL_RETRY_CACHE['value']
    try:
        v = max(0, min(int(cfg_get('proxy_dl_retry', 3)), 10))
    except Exception:
        v = 3
    _PROXY_DL_RETRY_CACHE['value'] = v
    _PROXY_DL_RETRY_CACHE['at'] = now
    return v


def _get_proxy_dl_ip_limit():
    """返回代理下载每 IP 每窗口最大请求数，默认 200，范围 1-10000。

    【R84/R250 修复 - 2026-11】配合 `>=` 判定，用户填 200 实际允许 200 次。
    """
    now = time.time()
    if now - _PROXY_DL_IP_LIMIT_CACHE['at'] < _CFG_CACHE_TTL:
        return _PROXY_DL_IP_LIMIT_CACHE['value']
    try:
        v = max(1, min(int(cfg_get('proxy_dl_ip_limit', 200)), 10000))
    except Exception:
        v = 200
    _PROXY_DL_IP_LIMIT_CACHE['value'] = v
    _PROXY_DL_IP_LIMIT_CACHE['at'] = now
    return v


def _get_proxy_dl_ip_window():
    """返回代理下载 IP 限流滑动窗口（秒），默认 60，范围 10-3600。"""
    now = time.time()
    if now - _PROXY_DL_IP_WINDOW_CACHE['at'] < _CFG_CACHE_TTL:
        return _PROXY_DL_IP_WINDOW_CACHE['value']
    try:
        v = max(10, min(int(cfg_get('proxy_dl_ip_window', 60)), 3600))
    except Exception:
        v = 60
    _PROXY_DL_IP_WINDOW_CACHE['value'] = v
    _PROXY_DL_IP_WINDOW_CACHE['at'] = now
    return v


def _get_preview_timeout():
    """返回预览 (connect_timeout, read_timeout)，默认 (10, 60)，范围 connect 1-60 / read 5-600。"""
    now = time.time()
    if now - _PREVIEW_TIMEOUT_CACHE['at'] < _CFG_CACHE_TTL:
        return _PREVIEW_TIMEOUT_CACHE['value']
    try:
        c = max(1, min(int(cfg_get('preview_timeout_connect', 10)), 60))
        r = max(5, min(int(cfg_get('preview_timeout_read', 60)), 600))
    except Exception:
        c, r = 10, 60
    _PREVIEW_TIMEOUT_CACHE['value'] = (c, r)
    _PREVIEW_TIMEOUT_CACHE['at'] = now
    return (c, r)


def _get_stream_chunk_size():
    """返回流式块大小，默认 65536，范围 4096-1048576。代理下载 + 预览共用。"""
    now = time.time()
    if now - _STREAM_CHUNK_SIZE_CACHE['at'] < _CFG_CACHE_TTL:
        return _STREAM_CHUNK_SIZE_CACHE['value']
    try:
        v = max(4096, min(int(cfg_get('stream_chunk_size', 65536)), 1048576))
    except Exception:
        v = 65536
    _STREAM_CHUNK_SIZE_CACHE['value'] = v
    _STREAM_CHUNK_SIZE_CACHE['at'] = now
    return v


def _get_id_check_ip_window():
    """【R279 新增】返回 ID 预检 IP 限流滑动窗口（秒），默认 60，范围 10-3600。

    语义说明：
      - 与 id_check_ip_limit（在「底层性能」details 里，默认 6）配合使用
      - 窗口内请求数 >= limit 时返回 "请求过于频繁"
      - 默认 60 秒：与原代码 `now - t < 60` 完全一致
      - 用户填 10 → 10 秒窗口（更严格）
      - 用户填 3600 → 1 小时窗口（更宽松）
    """
    now = time.time()
    if now - _ID_CHECK_IP_WINDOW_CACHE['at'] < _CFG_CACHE_TTL:
        return _ID_CHECK_IP_WINDOW_CACHE['value']
    try:
        v = max(10, min(int(cfg_get('id_check_ip_window', 60)), 3600))
    except Exception:
        v = 60
    _ID_CHECK_IP_WINDOW_CACHE['value'] = v
    _ID_CHECK_IP_WINDOW_CACHE['at'] = now
    return v


@api_bp.route('/api/browse_dir')
def api_browse_dir():
    path = request.args.get('path', '').strip()
    if not path:
        if os.path.exists('/sdcard'): path = '/sdcard'
        elif os.path.exists('/storage/emulated/0'): path = '/storage/emulated/0'
        else: path = os.path.expanduser('~')
    path = os.path.abspath(path)
    if not os.path.exists(path) or not os.path.isdir(path):
        return jsonify({"status": "error", "msg": "目录不存在或无权访问，请确保已授予本地存储权限"})
    try:
        dirs = []; files = []
        with os.scandir(path) as it:
            for entry in it:
                if entry.name.startswith('.'): continue
                try:
                    if entry.is_dir(): dirs.append({"name": entry.name, "path": entry.path})
                    elif entry.is_file():
                        stat = entry.stat()
                        files.append({"name": entry.name, "path": entry.path, "size": stat.st_size})
                except: pass
        dirs.sort(key=lambda x: x["name"].lower()); files.sort(key=lambda x: x["name"].lower())
        parent = os.path.dirname(path)
        if parent == path: parent = None
        return jsonify({"status": "success", "current_path": path, "parent_path": parent, "dirs": dirs, "files": files})
    except Exception as e:
        return jsonify({"status": "error", "msg": str(e)})


@api_bp.route('/api/download_proxy')
def api_download_proxy():
    """代理下载 archive.org 文件。

    【⏱️ 延迟与超时精细控制】
      - IP 限流：proxy_dl_ip_limit（默认 200 次/窗口）
      - IP 窗口：proxy_dl_ip_window（默认 60 秒）
      - 超时：proxy_dl_timeout_connect / proxy_dl_timeout_read（默认 5/15 秒）
      - 重试：proxy_dl_retry（默认 3，0 兜底为 1）
      - 流块：stream_chunk_size（默认 65536）
    【R84/R250 修复 - 2026-11】判定从 `>` 改为 `>=`（用户填 N 实际允许 N 次）。
    """
    url = request.args.get('url')
    if not url or not (url.startswith('https://') or url.startswith('http://')):
        return 'Invalid URL', 400
    if not _is_allowed_download_host(url):
        return '仅允许 archive.org 域名', 403

    ip = request.remote_addr
    now = time.time()
    # 【⏱️ 延迟与超时精细控制】200 → _get_proxy_dl_ip_limit()
    ip_limit = _get_proxy_dl_ip_limit()
    ip_window = _get_proxy_dl_ip_window()
    PROXY_DL_IP_LOG[ip] = [t for t in PROXY_DL_IP_LOG.get(ip, []) if now - t < ip_window]
    # 【R84/R250 修复】`>` → `>=`（统一与 thumb_ip_limit / id_check_ip_limit 语义）
    #   原：`> ip_limit` → 用户填 200 实际允许 201 次（off-by-one bug）
    #   新：`>= ip_limit` → 用户填 200 实际允许 200 次
    if len(PROXY_DL_IP_LOG[ip]) >= ip_limit:
        return '下载请求过于频繁，请稍后再试', 429
    PROXY_DL_IP_LOG[ip].append(now)

    cfg = get_config()
    if not cfg.get('download_via_proxy', True):
        req_proxies = None
    else:
        req_proxies = None
        proxy_url = cfg.get('proxy', '').strip()
        if proxy_url:
            proxy_h = proxy_url.replace("socks5://", "socks5h://") if proxy_url.startswith("socks5://") else proxy_url
            req_proxies = {"http": proxy_h, "https": proxy_h}
    m = re.search(r'archive\.org/(?:download|details)/([^/]+)', url)
    referer = f"https://archive.org/details/{m.group(1)}" if m else "https://archive.org/"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Encoding": "identity",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Connection": "keep-alive",
        "Referer": referer
    }
    try:
        resp = None
        max_attempts = max(1, _get_proxy_dl_retry())
        dl_timeout = _get_proxy_dl_timeout()
        for attempt in range(max_attempts):
            try:
                resp = requests.get(url, stream=True, timeout=dl_timeout, proxies=req_proxies, verify=get_ssl_verify(), allow_redirects=True, headers=headers)
                if resp.status_code == 200: break
                if resp.status_code in (502, 503, 504):
                    if attempt == max_attempts - 1: return f'代理节点无法连接 IA 服务器 (HTTP {resp.status_code})，请更换节点或稍后再试', 500
                    time.sleep(1)
                else: return f'下载失败: HTTP {resp.status_code}', 500
            except requests.exceptions.Timeout:
                if attempt == max_attempts - 1: return '连接超时：代理节点已失效，请更换节点', 500
                time.sleep(1)
            except Exception as e:
                if attempt == max_attempts - 1: raise e
                time.sleep(1)
        if resp.status_code != 200: return f'下载失败: HTTP {resp.status_code}', 500
        stream_chunk_size = _get_stream_chunk_size()
        def generate():
            try:
                for chunk in resp.iter_content(chunk_size=stream_chunk_size):
                    if chunk: yield chunk
            except Exception: pass
        import urllib.parse
        raw_filename = url.split("/")[-1].split("?")[0]
        safe_filename = urllib.parse.unquote(raw_filename)
        safe_filename = safe_filename.replace('/', '_').replace('\\', '_')
        if not safe_filename: safe_filename = 'download_file'
        res_headers = {
            'Content-Disposition': f"attachment; filename*=UTF-8''{urllib.parse.quote(safe_filename)}",
            'Content-Type': 'application/octet-stream',
            'Cache-Control': 'no-cache',
            'Accept-Ranges': 'bytes'
        }
        if 'Content-Length' in resp.headers: res_headers['Content-Length'] = resp.headers['Content-Length']
        return Response(stream_with_context(generate()), headers=res_headers)
    except Exception as e:
        return f'下载失败: {str(e)}', 500


@api_bp.route('/api/preview')
def api_preview():
    """在线预览文件。

    【⏱️ 延迟与超时精细控制】
      - 超时：preview_timeout_connect / preview_timeout_read（默认 10/60 秒）
      - 流块：stream_chunk_size（默认 65536）
    """
    identifier = request.args.get('identifier')
    filename = request.args.get('filename')
    if not identifier or not filename: return 'Missing parameters', 400

    import urllib.parse
    encoded_filename = urllib.parse.quote(filename, safe='/')
    url = f"https://archive.org/download/{identifier}/{encoded_filename}"

    if not _is_allowed_download_host(url):
        return '仅允许 archive.org 域名', 403

    cfg = get_config()
    if not cfg.get('download_via_proxy', True):
        req_proxies = None
    else:
        req_proxies = None
        proxy_url = cfg.get('proxy', '').strip()
        if proxy_url:
            proxy_h = proxy_url.replace("socks5://", "socks5h://") if proxy_url.startswith("socks5://") else proxy_url
            req_proxies = {"http": proxy_h, "https": proxy_h}

    range_header = request.headers.get('Range')
    headers = {"User-Agent": "Mozilla/5.0"}
    if range_header: headers['Range'] = range_header

    try:
        preview_timeout = _get_preview_timeout()
        r = requests.get(url, headers=headers, stream=True, proxies=req_proxies, verify=get_ssl_verify(), timeout=preview_timeout)
        stream_chunk_size = _get_stream_chunk_size()
        def generate():
            for chunk in r.iter_content(chunk_size=stream_chunk_size):
                if chunk: yield chunk

        res_headers = {}
        for h in ['Content-Type', 'Content-Length', 'Content-Range', 'Accept-Ranges', 'Last-Modified', 'ETag']:
            if h in r.headers: res_headers[h] = r.headers[h]

        return Response(stream_with_context(generate()), status=r.status_code, headers=res_headers)
    except Exception as e:
        return str(e), 500


@api_bp.route('/api/zip_content', methods=['POST'])
def api_zip_content():
    data = request.get_json()
    identifier = data.get('identifier')
    filename = data.get('filename')
    if not identifier or not filename:
        return jsonify({"status": "error", "msg": "参数缺失"})
    res = fetch_zip_structure(identifier, filename)
    return jsonify(res)


@api_bp.route('/api/magnet/<identifier>')
def api_magnet(identifier):
    owner = session.get('owner')
    conn = get_db()
    exists = conn.execute("SELECT magnet FROM items WHERE identifier=? AND owner=?", (identifier, owner)).fetchone()
    conn.close()
    if not exists:
        return jsonify({'error': 'not found'}), 404
    if exists[0]:
        return jsonify({'magnet': exists[0], 'cached': True})
    m = compute_magnet(identifier, owner=owner)
    if m:
        return jsonify({'magnet': m, 'cached': False})
    return jsonify({'error': 'failed'}), 400


@api_bp.route('/api/check_identifier')
def api_check_identifier():
    """ID 预检。

    【R279 修复 - 2026-11】IP 滑动窗口 60 秒硬编码 → _get_id_check_ip_window()（默认 60，10-3600 秒）
    """
    ident = request.args.get('identifier', '').strip()
    owner = session.get('owner')
    if not ident:
        return jsonify({'exists': False, 'source': 'none'})
    ip, now = request.remote_addr, time.time()
    # 【R279 修复】60 → _get_id_check_ip_window()
    id_check_ip_window = _get_id_check_ip_window()
    IP_ID_CHECK[ip] = [t for t in IP_ID_CHECK[ip] if now - t < id_check_ip_window]
    if len(IP_ID_CHECK[ip]) >= cfg_get('id_check_ip_limit', 6):
        return jsonify({'exists': True, 'source': 'unknown', 'msg': '请求过于频繁，请稍后'})
    IP_ID_CHECK[ip].append(now)
    try:
        incoming_size = int(request.args.get('size', '0'))
    except:
        incoming_size = 0

    conn = get_db()
    row = conn.execute("SELECT id, status FROM items WHERE owner=? AND identifier=? AND deleted_at IS NULL", (owner, ident)).fetchone()
    if row:
        if row[1] == 'done':
            fc = conn.execute("SELECT SUM(CAST(size AS INTEGER)), COUNT(*) FROM file_cache WHERE identifier=?", (ident,)).fetchone()
            if fc and fc[1] > 0:
                if incoming_size > 0 and fc[0] and abs(int(fc[0]) - incoming_size) < 1024 * 1024:
                    conn.close()
                    return jsonify({'exists': True, 'source': 'local_done', 'msg': '文件大小一致，可直接秒传'})
                conn.close()
                return jsonify({'exists': True, 'source': 'local', 'msg': '本地已有该 ID，但文件可能不一致'})
        conn.close()
        return jsonify({'exists': True, 'source': 'local', 'msg': '本地已有该 ID（未完成或文件不一致）'})
    conn.close()

    ttl = cfg_get('id_check_ttl', 300)
    with ID_CHECK_LOCK:
        cached = ID_CHECK_CACHE.get(ident)
        if cached and now - cached[1] < ttl:
            return jsonify(cached[0])

    try:
        resp = ia_get(f"https://archive.org/metadata/{ident}", timeout=10)
        if resp is None:
            return jsonify({'exists': True, 'source': 'unknown', 'msg': '网络超时，无法确认 ID 是否存在'})

        if resp.status_code == 200:
            j = resp.json()
            if j.get('metadata') or j.get('files'):
                result = {'exists': True, 'source': 'ia', 'msg': 'IA 官方已存在'}
                with ID_CHECK_LOCK: ID_CHECK_CACHE[ident] = (result, time.time())
                return jsonify(result)
    except: pass

    result = {'exists': False, 'source': 'none'}
    with ID_CHECK_LOCK: ID_CHECK_CACHE[ident] = (result, time.time())
    return jsonify(result)


@api_bp.route('/api/force_refresh/<identifier>')
def api_force_refresh(identifier):
    owner = session.get('owner')
    conn = get_db()
    exists = conn.execute("SELECT id FROM items WHERE identifier=? AND owner=?", (identifier, owner)).fetchone()
    conn.close()
    if not exists:
        return jsonify({"status": "error", "msg": "条目不存在"})
    now = time.time()
    cd = cfg_get('refresh_cooldown', 10)
    with REFRESH_LOCK:
        if now - REFRESH_COOLDOWN_MAP.get(identifier, 0) < cd:
            return jsonify({"status": "cooldown", "msg": f"冷却中，请 {int(cd)} 秒后重试"})
        REFRESH_COOLDOWN_MAP[identifier] = now
    success = _fetch_and_cache_ia_files(identifier, force=True)
    if success:
        return jsonify({"status": "success"})
    return jsonify({"status": "error", "msg": "拉取失败，请检查网络或稍后重试"})


@api_bp.route('/api/user_stats')
def api_user_stats():
    owner = session.get('owner')
    if not owner:
        return jsonify({"status": "error", "msg": "未登录"})
    res = fetch_user_stats(owner)
    return jsonify(res)