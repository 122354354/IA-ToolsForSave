# netdisk_routes_views_index.py
# 首页 / 文件夹视图 / 视图切换
#
# 【⏱️ 延迟与超时精细控制 - 2026-11 R136 追加修复】
# 首页/文件夹视图的 file_cache 拉取（前端 pages_index_main.py 的 refreshPicker / loadFiles）
# 也有 AbortController 15000ms 硬编码，需要接 detail_fetch_timeout_ms。
# 原第 16 个文件 netdisk_routes_views_manage.py 只对详情页 item_detail 传参，
# 首页 folder_view 未传 → 用户改配置后首页不生效。
# 本文件追加传参，与详情页保持配置一致。
#
# 【🔴 R215 追加修复 - 2026-11（本轮）】
# 首页 ZIP 解压浏览（前端 pages_index_main.py 的 previewZip）
# 也有 AbortController 30000ms 硬编码，需要接 detail_zip_timeout_ms。
# 第 3 次生成 pages_index_main.py 时 previewZip 已用
# `{{ detail_zip_timeout_ms|default(30000) }}` 引用，但因本文件未传参 → 走 default(30000) 兜底
# → 用户改 detail_zip_timeout_ms 后首页 ZIP 预览不生效。
# 本轮追加传参，与详情页保持配置一致。
#
# 【参数说明】
#   detail_fetch_timeout_ms (默认 15000，1000-120000ms)
#   → 首页 file_cache 拉取 / force_refresh 请求的 AbortController 超时
#   detail_zip_timeout_ms   (默认 30000，3000-300000ms)
#   → 首页 ZIP 结构解析请求的 AbortController 超时
#
# 【默认值对比（与原前端硬编码）】
#   - detail_fetch_timeout_ms: 15000 → 15000 ✅ 完全一致
#   - detail_zip_timeout_ms: 30000 → 30000 ✅ 完全一致
#
# 【中间状态兼容性】
#   本文件同时传 detail_fetch_timeout_ms 和 detail_zip_timeout_ms。
#   前端 pages_index_main.py 的 refreshPicker / previewZip 已引用这两个参数：
#     - refreshPicker 用 {{ detail_fetch_timeout_ms }}（无 default）
#     - previewZip 用 {{ detail_zip_timeout_ms|default(30000) }}（有 default）
#   本轮补充传参后，两个参数均正常生效 ✅
#
# 【设计说明】
#   - 2 个 getter 独立实现（不导入 netdisk_routes_views_manage 的），避免跨文件耦合
#   - 各自 5 秒 TTL 缓存，用户改配置后最多 5 秒生效
#   - config.json 损坏 / 非法值 → 回退默认值

import os
import time
from flask import request, render_template_string, redirect, url_for, session

from netdisk_blueprints import views_bp
from netdisk_core import (
    get_db, cfg_get, get_folder_tree_options,
)
from templates_module import STYLE, get_navbar
from pages_module import INDEX_HTML

import netdisk_core as core


# ========== 【⏱️ 延迟与超时精细控制】detail_fetch_timeout_ms 5 秒 TTL 缓存 ==========
_DETAIL_FETCH_TIMEOUT_MS_CACHE = {'value': 15000, 'at': 0.0}
# ========== 【⏱️ 延迟与超时精细控制】detail_zip_timeout_ms 5 秒 TTL 缓存 ==========
_DETAIL_ZIP_TIMEOUT_MS_CACHE = {'value': 30000, 'at': 0.0}
_CFG_CACHE_TTL = 5.0


def _get_detail_fetch_timeout_ms():
    """返回首页/详情页 file_cache 拉取超时（毫秒），默认 15000，范围 1000-120000。

    语义说明：
      - 与 netdisk_routes_views_manage.py 的同名 getter 读同一配置键
      - 默认 15000ms：与原 pages_index_main.py 的 AbortController 15000 完全一致
      - 用户填 1000 → 1 秒超时（快速失败）
      - 用户填 120000 → 2 分钟超时（慢网络）
    """
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
    """返回首页 ZIP 结构解析超时（毫秒），默认 30000，范围 3000-300000。

    语义说明：
      - 与 netdisk_routes_views_manage.py 的同名 getter 读同一配置键
      - 默认 30000ms：与原 pages_index_main.py 的 AbortController 30000 完全一致
      - 用户填 3000 → 3 秒超时（快速失败）
      - 用户填 300000 → 5 分钟超时（ZIP 目录极大或慢网络）
    """
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


@views_bp.route('/')
def index():
    return folder_view(0)


@views_bp.route('/folder/<int:folder_id>')
def folder_view(folder_id):
    conn = get_db()
    owner = session.get('owner')
    sort_by = request.args.get('sort', 'time_desc')
    order_sql = "ORDER BY i.created_at DESC" if sort_by == 'time_desc' else "ORDER BY i.title ASC" if sort_by == 'title_asc' else "ORDER BY i.title DESC"
    page, page_size = request.args.get('page', type=int, default=1), 20
    offset = (page - 1) * page_size
    if folder_id == 0:
        current_folder = (0, "🏠 根目录", 0)
    else:
        current_folder = conn.execute("SELECT id, name, parent_id FROM folders WHERE id=? AND owner=?", (folder_id, owner)).fetchone()
        if not current_folder:
            conn.close()
            return redirect(url_for('views.index'))
    crumbs, cid = [], folder_id
    while cid != 0:
        f = conn.execute("SELECT id, name, parent_id FROM folders WHERE id=?", (cid,)).fetchone()
        if not f: break
        crumbs.insert(0, f)
        cid = f[2]
    sub_folders = conn.execute("SELECT id, name FROM folders WHERE parent_id=? AND owner=? ORDER BY name ASC", (folder_id, owner)).fetchall()
    total_items = conn.execute("SELECT COUNT(*) FROM items WHERE folder_id=? AND owner=? AND status IN ('done', 'cloud_missing') AND deleted_at IS NULL", (folder_id, owner)).fetchone()[0]
    items = conn.execute(f"SELECT i.id, i.identifier, i.title, i.direct_url, i.file_name, (SELECT COUNT(*) FROM file_cache fc WHERE fc.identifier = i.identifier) as f_count FROM items i WHERE i.folder_id=? AND i.owner=? AND i.status IN ('done', 'cloud_missing') AND i.deleted_at IS NULL {order_sql} LIMIT ? OFFSET ?", (folder_id, owner, page_size, offset)).fetchall()
    folder_ids = [f[0] for f in sub_folders]
    folder_sizes = {fid: 0 for fid in folder_ids}
    if folder_ids:
        placeholders = ','.join(['?'] * len(folder_ids))
        size_rows = conn.execute(
            f"SELECT i.folder_id, SUM(CAST(fc.size AS INTEGER)) FROM items i LEFT JOIN file_cache fc ON fc.identifier = i.identifier WHERE i.folder_id IN ({placeholders}) AND i.owner=? GROUP BY i.folder_id",
            (*folder_ids, owner)
        ).fetchall()
        for r in size_rows:
            folder_sizes[r[0]] = r[1] or 0
    all_folders = get_folder_tree_options(owner)
    conn.close()
    def fmt(b):
        if not b: return "0 B"
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if b < 1024: return f"{b:.1f} {unit}"
            b /= 1024
        return f"{b:.1f} PB"
    # 【R136 追加修复】传入 detail_fetch_timeout_ms（供 pages_index_main.py 后续引用）
    # 【R215 追加修复】传入 detail_zip_timeout_ms（供 pages_index_main.py 的 previewZip 引用）
    return render_template_string(
        INDEX_HTML, style=STYLE,
        navbar=get_navbar(folder_id != 0, current_folder[2], active_page='index'),
        cf=current_folder, crumbs=crumbs, sf=sub_folders, items=items,
        all_folders=all_folders, sort_by=sort_by, page=page, total_items=total_items,
        folder_sizes=folder_sizes, fmt=fmt, view_mode=session.get('view_mode', 'card'),
        auto_load_thumb=cfg_get('auto_load_thumb', False),
        proxy_dl=cfg_get('download_via_proxy', True),
        has_thumb=lambda ident: os.path.exists(os.path.join(core.THUMB_DIR, f"{ident}.jpg")),
        single_download_default=cfg_get('single_download_default', 'ask'),
        batch_download_default=cfg_get('batch_download_default', 'ask'),
        # 【R136 追加修复】首页 file_cache 拉取超时（默认 15000，与原硬编码一致）
        detail_fetch_timeout_ms=_get_detail_fetch_timeout_ms(),
        # 【R215 追加修复】首页 ZIP 解压浏览超时（默认 30000，与原硬编码一致）
        detail_zip_timeout_ms=_get_detail_zip_timeout_ms(),
    )


@views_bp.route('/toggle_view')
def toggle_view():
    session['view_mode'] = 'list' if session.get('view_mode', 'card') == 'card' else 'card'
    return redirect(request.referrer or url_for('views.index'))