# netdisk_routes_views_manage.py
# 历史 / 队列 / 回收站 / 搜索 / 详情
#
# 【⏱️ 延迟与超时精细控制 - 2026-11 第 16/20 步】
# 新增 5 个带 5 秒 TTL 缓存的 getter，渲染 queue / item_detail 页时传递给前端模板：
#   1. queue_poll_interval_ms      (默认 500，200-10000 ms)    → 队列页轮询间隔
#   2. queue_refresh_timeout_ms    (默认 10000，2000-60000 ms)  → 队列刷新 AbortController 超时
#   3. queue_stuck_timeout_ms      (默认 15000，3000-120000 ms) → _refreshInFlight 卡住兜底解锁
#   4. detail_fetch_timeout_ms     (默认 15000，1000-120000 ms) → 详情页/首页 file_cache 拉取超时
#   5. detail_zip_timeout_ms       (默认 30000，3000-300000 ms) → 详情页 ZIP 结构解析超时
#
# 【🔴 与前端模板的配合】
#   - 第 16 个文件（本文件）：路由层读取配置，通过 render_template_string 传入
#   - 第 19 个文件 pages_queue_js.py：模板使用 {{ queue_poll_interval_ms }} 等变量
#   - 第 17 个文件 pages_upload.py 已处理过 upload/append 页
#   - pages_detail_item.py 是详情页模板，需要独立处理（不在本次 20 个文件清单里，
#     但传参不会报错，前端暂不引用就继续用原硬编码）
#   - pages_index_main.py 是首页模板，也用 file_cache 拉取，需要独立处理
#     （不在本次 20 个文件清单里，但路由 netdisk_routes_views_index.py 在下次可能改）
#
# 【默认值对比（与原前端硬编码）】
#   - queue_poll_interval_ms      : 500    → 500    ✅ 完全一致（原 setInterval(refresh, 500)）
#   - queue_refresh_timeout_ms    : 10000  → 10000  ✅ 完全一致（原 setTimeout(..., 10000)）
#   - queue_stuck_timeout_ms      : 15000  → 15000  ✅ 完全一致（原 _refreshStartTime > 15000）
#   - detail_fetch_timeout_ms     : 15000  → 15000  ✅ 完全一致（原 AbortController 15000）
#   - detail_zip_timeout_ms       : 30000  → 30000  ✅ 完全一致（原 AbortController 30000）
#
# 【不接入的参数（有意保留硬编码）】
#   - history_view / trash_view / search_view：不涉及前端超时，不需要传参
#   - item_detail 的 auto_fetch_detail / proxy_dl / dl_dir / has_thumb 等原有参数保留
#
# 【设计说明】
#   - 5 个 getter 都带 5 秒 TTL 缓存，用户改配置后最多 5 秒生效
#   - config.json 损坏 / 非法值 → 回退默认值
#   - 前端不改代码时（中间状态），传参不会报错，也不影响现有功能
#   - queue_view() 用 style=STYLE，item_detail() 用 s=STYLE（原文件风格），保持原样

import os
import re
import time
from flask import request, render_template_string, session

from netdisk_blueprints import views_bp
from netdisk_core import (
    get_db, get_folder_tree_options, cfg_get,
)
from templates_module import STYLE, get_navbar
from pages_module import (
    HISTORY_HTML, QUEUE_HTML, TRASH_HTML, SEARCH_HTML, ITEM_DETAIL_HTML,
)

import netdisk_core as core


# ========== 【⏱️ 延迟与超时精细控制】5 个带 5 秒 TTL 缓存的读取函数 ==========
_QUEUE_POLL_INTERVAL_MS_CACHE = {'value': 500, 'at': 0.0}
_QUEUE_REFRESH_TIMEOUT_MS_CACHE = {'value': 10000, 'at': 0.0}
_QUEUE_STUCK_TIMEOUT_MS_CACHE = {'value': 15000, 'at': 0.0}
_DETAIL_FETCH_TIMEOUT_MS_CACHE = {'value': 15000, 'at': 0.0}
_DETAIL_ZIP_TIMEOUT_MS_CACHE = {'value': 30000, 'at': 0.0}
_CFG_CACHE_TTL = 5.0


def _get_queue_poll_interval_ms():
    """返回队列页轮询间隔（毫秒），默认 500，范围 200-10000。"""
    now = time.time()
    if now - _QUEUE_POLL_INTERVAL_MS_CACHE['at'] < _CFG_CACHE_TTL:
        return _QUEUE_POLL_INTERVAL_MS_CACHE['value']
    try:
        v = max(200, min(int(cfg_get('queue_poll_interval_ms', 500)), 10000))
    except Exception:
        v = 500
    _QUEUE_POLL_INTERVAL_MS_CACHE['value'] = v
    _QUEUE_POLL_INTERVAL_MS_CACHE['at'] = now
    return v


def _get_queue_refresh_timeout_ms():
    """返回队列刷新 AbortController 超时（毫秒），默认 10000，范围 2000-60000。"""
    now = time.time()
    if now - _QUEUE_REFRESH_TIMEOUT_MS_CACHE['at'] < _CFG_CACHE_TTL:
        return _QUEUE_REFRESH_TIMEOUT_MS_CACHE['value']
    try:
        v = max(2000, min(int(cfg_get('queue_refresh_timeout_ms', 10000)), 60000))
    except Exception:
        v = 10000
    _QUEUE_REFRESH_TIMEOUT_MS_CACHE['value'] = v
    _QUEUE_REFRESH_TIMEOUT_MS_CACHE['at'] = now
    return v


def _get_queue_stuck_timeout_ms():
    """返回队列 _refreshInFlight 卡住兜底解锁（毫秒），默认 15000，范围 3000-120000。"""
    now = time.time()
    if now - _QUEUE_STUCK_TIMEOUT_MS_CACHE['at'] < _CFG_CACHE_TTL:
        return _QUEUE_STUCK_TIMEOUT_MS_CACHE['value']
    try:
        v = max(3000, min(int(cfg_get('queue_stuck_timeout_ms', 15000)), 120000))
    except Exception:
        v = 15000
    _QUEUE_STUCK_TIMEOUT_MS_CACHE['value'] = v
    _QUEUE_STUCK_TIMEOUT_MS_CACHE['at'] = now
    return v


def _get_detail_fetch_timeout_ms():
    """返回详情页/首页 file_cache 拉取超时（毫秒），默认 15000，范围 1000-120000。"""
    now = time.time()
    if now - _DETAIL_FETCH_TIMEOUT_MS_CACHE['at'] < _CFG_CACHE_TTL:
        return _DETAIL_FETCH_TIMEOUT_MS_CACHE['value']
    try:
        v = max(1000, min(int(cfg_get('detail_fetch_timeout_ms', 15000)), 120000))
    except Exception:
        v = 15000
    _DETAIL_FETCH_TIMEOUT_MS_CACHE['value'] = v
    _DETAIL_FETCH_TIMEOUT_MS_CACHE['at'] = now
    return v


def _get_detail_zip_timeout_ms():
    """返回详情页 ZIP 结构解析超时（毫秒），默认 30000，范围 3000-300000。"""
    now = time.time()
    if now - _DETAIL_ZIP_TIMEOUT_MS_CACHE['at'] < _CFG_CACHE_TTL:
        return _DETAIL_ZIP_TIMEOUT_MS_CACHE['value']
    try:
        v = max(3000, min(int(cfg_get('detail_zip_timeout_ms', 30000)), 300000))
    except Exception:
        v = 30000
    _DETAIL_ZIP_TIMEOUT_MS_CACHE['value'] = v
    _DETAIL_ZIP_TIMEOUT_MS_CACHE['at'] = now
    return v


@views_bp.route('/history')
def history_view():
    conn = get_db()
    owner = session.get('owner')
    items = conn.execute("SELECT id, identifier, title, status, error_msg, (SELECT COUNT(*) FROM file_cache fc WHERE fc.identifier = i.identifier) FROM items i WHERE deleted_at IS NULL AND owner=? ORDER BY created_at DESC", (owner,)).fetchall()
    conn.close()
    return render_template_string(HISTORY_HTML, style=STYLE, navbar=get_navbar(False), items=items)


@views_bp.route('/queue')
def queue_view():
    conn = get_db()
    owner = session.get('owner')
    items = conn.execute("SELECT id, title, status, error_msg FROM items WHERE status IN ('pending', 'error') AND deleted_at IS NULL AND owner=?", (owner,)).fetchall()
    conn.close()
    # 【⏱️ 延迟与超时精细控制】传入 3 个队列轮询参数（供 pages_queue_js.py 使用）
    return render_template_string(
        QUEUE_HTML, style=STYLE,
        navbar=get_navbar(False, active_page='queue'),
        items=items,
        conc_max=core.CONCURRENCY_LIMITER.status()['max'],
        queue_poll_interval_ms=_get_queue_poll_interval_ms(),
        queue_refresh_timeout_ms=_get_queue_refresh_timeout_ms(),
        queue_stuck_timeout_ms=_get_queue_stuck_timeout_ms(),
    )


@views_bp.route('/trash')
def trash_view():
    conn = get_db()
    owner = session.get('owner')
    items = conn.execute("SELECT id, identifier, title, deleted_at FROM items WHERE deleted_at IS NOT NULL AND owner=?", (owner,)).fetchall()
    conn.close()
    return render_template_string(TRASH_HTML, style=STYLE, navbar=get_navbar(False), items=items)


@views_bp.route('/search')
def search_view():
    q = request.args.get('q', '').strip()
    mode = request.args.get('mode', 'include')
    scope = request.args.get('scope', 'global')
    folder_id = request.args.get('folder_id', type=int, default=0)
    owner = session.get('owner')
    items = []
    all_folders = get_folder_tree_options(owner)
    conn = get_db()
    if q:
        sql = """SELECT i.id, i.identifier, i.title, i.status, i.error_msg, (SELECT COUNT(*) FROM file_cache fc WHERE fc.identifier = i.identifier) as f_count FROM items i WHERE i.owner=? AND i.deleted_at IS NULL"""
        params = [owner]
        if mode == 'include':
            sql += " AND (COALESCE(i.title,'') LIKE ? OR COALESCE(i.identifier,'') LIKE ? OR COALESCE(i.description,'') LIKE ? OR COALESCE(i.tags,'') LIKE ?)"
            kw = f"%{q}%"
            params.extend([kw, kw, kw, kw])
        elif mode == 'not_include':
            sql += " AND (COALESCE(i.title,'') NOT LIKE ? AND COALESCE(i.identifier,'') NOT LIKE ? AND COALESCE(i.description,'') NOT LIKE ? AND COALESCE(i.tags,'') NOT LIKE ?)"
            kw = f"%{q}%"
            params.extend([kw, kw, kw, kw])
        elif mode == 'startswith':
            sql += " AND (COALESCE(i.title,'') LIKE ? OR COALESCE(i.identifier,'') LIKE ? OR COALESCE(i.description,'') LIKE ? OR COALESCE(i.tags,'') LIKE ?)"
            kw = f"{q}%"
            params.extend([kw, kw, kw, kw])
        elif mode == 'endswith':
            sql += " AND (COALESCE(i.title,'') LIKE ? OR COALESCE(i.identifier,'') LIKE ? OR COALESCE(i.description,'') LIKE ? OR COALESCE(i.tags,'') LIKE ?)"
            kw = f"%{q}"
            params.extend([kw, kw, kw, kw])
        elif mode == 'exact':
            sql += " AND (COALESCE(i.title,'') = ? OR COALESCE(i.identifier,'') = ? OR COALESCE(i.description,'') = ? OR COALESCE(i.tags,'') = ?)"
            params.extend([q, q, q, q])
        elif mode == 'regex':
            try:
                re.compile(q, re.IGNORECASE)
            except Exception as e:
                conn.close()
                return f"❌ 正则语法错误: {e}"
            sql += " AND (COALESCE(i.title,'') REGEXP ? OR COALESCE(i.identifier,'') REGEXP ? OR COALESCE(i.description,'') REGEXP ? OR COALESCE(i.tags,'') REGEXP ?)"
            params.extend([q, q, q, q])
        if scope == 'folder':
            sql += " AND i.folder_id = ?"
            params.append(folder_id)
        sql += " ORDER BY i.created_at DESC LIMIT 1000"
        try:
            items = conn.execute(sql, params).fetchall()
        except Exception as e:
            conn.close()
            return f"❌ 搜索出错: {e}"
    conn.close()
    return render_template_string(
        SEARCH_HTML, style=STYLE,
        navbar=get_navbar(True, active_page='search'),
        items=items, all_folders=all_folders,
        q=q, mode=mode, scope=scope, folder_id=folder_id
    )


@views_bp.route('/item_detail/<item_id>')
def item_detail(item_id):
    conn = get_db()
    owner = session.get('owner')
    item = conn.execute("SELECT id, title, direct_url, identifier, file_name, tags FROM items WHERE identifier=? AND owner=?", (item_id, owner)).fetchone()
    conn.close()
    if not item:
        return "❌ 未找到或无权访问"
    db_id, title, direct_url, identifier, file_name, tags = item
    has_thumb_val = os.path.exists(os.path.join(core.THUMB_DIR, f"{identifier}.jpg"))
    # 【⏱️ 延迟与超时精细控制】传入 2 个详情页参数（供 pages_detail_item.py 使用）
    #   注意：原文件此处用 s=STYLE（不是 style=STYLE），保持原样不改
    return render_template_string(
        ITEM_DETAIL_HTML, s=STYLE,
        n=get_navbar(False), t=title, i=identifier, tags=tags,
        auto_fetch_detail=cfg_get('auto_fetch_on_detail', False),
        proxy_dl=cfg_get('download_via_proxy', True),
        dl_dir=cfg_get('download_dir', '/sdcard/Download'),
        has_thumb=has_thumb_val,
        single_download_default=cfg_get('single_download_default', 'ask'),
        batch_download_default=cfg_get('batch_download_default', 'ask'),
        # 【⏱️ 延迟与超时精细控制】详情页超时参数
        detail_fetch_timeout_ms=_get_detail_fetch_timeout_ms(),
        detail_zip_timeout_ms=_get_detail_zip_timeout_ms(),
    )