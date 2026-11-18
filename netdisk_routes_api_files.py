# netdisk_routes_api_files.py
# 文件缓存/重命名/删除/自定义直链/标签/缩略图 相关 API
#
# 【⏱️ 延迟与超时精细控制 - 2026-11 第 10/20 步】
# 接入 3 个新配置项（全部 cfg_get 读取 + 5 秒 TTL 缓存）：
#   thumb_timeout / thumb_manual_timeout / thumb_fail_cache_ttl
#
# 【🔴 B 级第 4 次生成 - 2026-11】
# 新增 1 个配置项（cfg_get 读取 + 5 秒 TTL 缓存）：
#   thumb_ip_window (默认 60，10-3600 秒)
#   → 替换 serve_thumb 里 THUMB_IP_LOG 的 60 秒滑动窗口硬编码
#
# 【默认值对比（与原代码）】
#   - thumb_ip_window: 60 → 60 ✅ 完全一致
#
# 【接入位置（1 处）】
#   serve_thumb 里：
#     THUMB_IP_LOG[ip] = [t for t in THUMB_IP_LOG[ip] if now - t < 60]
#   → 改为：
#     ip_window = _get_thumb_ip_window()
#     THUMB_IP_LOG[ip] = [t for t in THUMB_IP_LOG[ip] if now - t < ip_window]
#
# 【性能优化】
#   把 ip_window 在请求开头读一次（而非列表推导里反复读），
#   5 秒 TTL 缓存内 0 次 IO，用户改配置后最多 5 秒生效
#
# 【不接入的参数（有意保留硬编码）】
#   - THUMB_IP_LOG 是 defaultdict(list)，无自动清理 → R249 记录
#   - serve_thumb 里 `for chunk in resp.iter_content(8192): f.write(chunk)` 的 8192 块大小：
#     缩略图很小（<1MB），8192 足够快，用户未提出配置项 → 保留
#   - api_thumb_refresh 的 `iter_content(8192)` 同上 → 保留
#
# 【🔴 R345 修复 - 2026-11（同源类问题统一修复第 1/5 个文件 - 第 1 轮）】
#   R335 同源（netdisk_routes_api_tasks.py 的 api_retry_task 并发原子抢占修复），
#   本文件有 3 处"先改状态 → 启动 worker"缺原子抢占，会并发启动多个 worker：
#   - api_rename_file：两个并发重命名同 identifier → IA 端 rename.php 冲突 →
#     其中一个 worker 误报"文件不存在"error，用户困惑
#   - api_delete_file：两个并发删除同文件 → 第二个 ia.delete 返回 404 被当成功（浪费 API）
#   - api_delete_batch：同一 group 并发提交两次 → 两个 worker 处理同一批文件（重复删除）
#   修复：
#     - 新增模块级 _FILE_OPS_LOCK = threading.Lock()
#     - 锁外先查 item 存在（快速拒绝）
#     - 锁内：创建 task + 启动 worker
#     - save_task_to_db 移到锁外（内部有 DB_LOCK，避免锁内交叉）
#     - api_rename_file 额外检查"同 identifier 是否已有活跃 rename 任务"
#
#   R346 修复 - 2026-11：
#   api_set_custom_link / api_update_tags 的 UPDATE 影响 0 行时不报错，
#   静默返回 success，用户以为成功但实际未生效。修复：
#   - api_set_custom_link：UPDATE file_cache rowcount=0 → 返回"文件不存在"
#   - api_update_tags：UPDATE items rowcount=0 → 返回"本地记录已被删除"
#   同时 data['identifier'] 改为 data.get() 防 KeyError 500。
#
# 【🔴 R351 修复 - 2026-11（同源类问题统一修复第 1/5 个文件 - 第 2 轮）】
#   问题：R345 第一版修复把 DB 二次检查放在 _FILE_OPS_LOCK 锁内。
#         而 get_db() 内部执行 `conn.execute("PRAGMA journal_mode=WAL;")`，
#         在 DB 繁忙时（如 worker 大批量写 sub_tasks）会阻塞至 db_timeout（默认 30 秒）。
#         此时主线程持 _FILE_OPS_LOCK 等 get_db()，导致所有并发的
#         rename / delete / delete_batch 请求全部阻塞 → 全局串行化风险。
#   触发场景：
#     - 用户上传 1000 个文件（upload_worker 在 DB_LOCK 内批写 sub_tasks）
#     - 同时点"删除选中" → api_delete_batch 拿 _FILE_OPS_LOCK → get_db() 等 WAL PRAGMA
#     - 期间用户又点"删除其他文件" → 全部排队等 _FILE_OPS_LOCK
#   修复：
#     - api_rename_file / api_delete_file：锁内**移除** DB 二次检查
#     - api_delete_batch：锁外一次 get_db() 收集有效 group 的 db_id → 锁内纯内存创建
#   为什么锁外的"先查 item 存在"已经够用：
#     - 锁外查到锁内创建之间的时间窗口是纯内存操作（微秒级）
#     - 即使这期间 item 被并发删除（需用户点 /trash_item 或 /purge_item，另一个请求），
#       worker 内部会兜底：
#         rename_files_worker → _rename_ia_file 走 IA API，404 → error，用户看到失败
#         delete_files_worker → _delete_ia_file 走 IA API，404 被当成功（用户本来就想删）
#
# 【🔴 R356 修复 - 2026-11（同源类问题统一修复第 1/5 个文件 - 第 3 轮）】
#   问题：items 表用 deleted_at 软删除标记（NULL = 归档，NOT NULL = 回收站）。
#         本文件 4 处 SELECT 未过滤 deleted_at IS NULL，导致对回收站中的条目
#         仍会执行 IA 侧的 delete / rename / update_tags 操作，造成数据安全问题：
#   - api_delete_file / api_delete_batch：items 在回收站，但 IA 文件被真的删除，
#     用户从回收站"恢复"后发现文件已丢失 → **数据丢失**
#   - api_rename_file：items 在回收站，但 IA 文件被改名，items.file_name 未同步更新
#   - api_update_tags：items 在回收站，但 IA tags 被更新，本地 items 可能已删除
#   触发场景（0.01% 概率但后果严重）：
#     - 用户在 tab1 详情页点"删除选中"（触发 delete_batch）
#     - 同时在 tab2 把同一条目"移到回收站"（触发 /trash_item，更新 deleted_at）
#     - delete_batch 的 SELECT 不检查 deleted_at → item 存在 → 启动 worker
#     - worker 真的调用 ia.delete 删除 IA 上的文件 → 用户恢复后数据丢失
#   修复：4 处 SELECT 都加 `AND deleted_at IS NULL`。
#   为什么修复无回归：
#     - 归档区（首页/详情页）操作的都是 deleted_at IS NULL 的条目 ✅
#     - 回收站操作走 netdisk_routes_views_folder.py 的
#       restore_item / purge_item / empty_trash，不走本文件 ✅
#     - 语义更严格（防误操作），符合用户预期
#
# 【🔴 R361 修复 - 2026-11（同源类问题统一修复第 1/5 个文件 - 第 4 轮 - 本轮）】
#   问题：api_set_custom_link 的 exists 查询未过滤 deleted_at IS NULL，与 R356 同源。
#         影响：items 在回收站时，用户通过旧 tab / 第三方 API / 手动构造请求，
#              仍能修改 items.direct_url 和 file_cache.custom_direct_url。
#         后果：用户从回收站恢复后看到"奇怪"的直链（自己没设过）→ 轻微困惑。
#              不像 R356 的 delete/rename/tags 那样会操作 IA 造成数据丢失，
#              但属于同一类状态一致性问题。
#   修复：exists 查询加 `AND deleted_at IS NULL`（1 行 SQL，无副作用）
#
# 【📝 保留记录 - 2026-11，低优先，以后修】
#   R347：_FILE_OPS_LOCK 全局锁，不同 identifier 的并发重命名会串行化
#   R348：api_rename_file 检查"同 identifier 活跃 rename"依赖 extra_data 的 json 解析
#   R349：api_delete_batch 锁外一次 DB 查询，groups 多时锁外 DB 时间线性增长
#   R350：api_delete_batch 锁内启动 worker 失败时进 skipped 列表
#   R352：api_set_custom_link / api_update_tags 在 with DB_LOCK 内 return 时未显式
#         conn.rollback()（sqlite3 默认 conn.close() 会 rollback，行为安全）
#   R353：api_update_tags 锁外查 exists 后推送 IA，若期间 items 被并发完全删除
#         （DB 层面 DELETE），用户看到"本地记录已被删除"可能困惑
#   R354：api_rename_file 锁内遍历 UPLOAD_TASKS 字典，活跃任务多时锁持有时间变长
#   R355：api_delete_batch 锁外一次 get_db 后到锁内启动 worker 之间 item 被并发
#         完全删除（DB 层面 DELETE），worker 内部 IA API 404 兜底
#   R360：api_delete_batch 全部 group 都被跳过时返回 {status:'success', count:0, skipped:[...]}，
#         前端会显示"已提交 0 个删除任务"，有些误导，记录待前端配合
#
# 【📝 同源问题记录 - 其他文件，后续统一修】
#   - netdisk_routes_api_tasks.py 的 api_retry_file（R340）→ 待第 2 个文件修复
#   - netdisk_app.py / netdisk_app_discover.py 的 _clean_virtual_tasks_worker（R320）
#     → 待第 3-4 个文件修复
#   - netdisk_workers_upload.py 的 retry_single_file_worker（R321）→ 待第 5 个文件修复

import os
import json
import uuid
import threading
import time
import logging
from flask import request, jsonify, session, send_file, make_response

import netdisk_core as core
from netdisk_blueprints import api_bp
from netdisk_core import (
    DB_LOCK, get_db, cfg_get, UPLOAD_TASKS,
    ia_get,
    THUMB_FAIL_CACHE, THUMB_IP_LOG,
    _push_tags_update,
    delete_files_worker, rename_files_worker,
)


# ========== 【R345 修复 - 2026-11】文件操作并发原子抢占锁 ==========
# 用途：防止两个并发请求同时创建 task 并启动 worker（"先改状态 → 启动 worker"竞态）。
#
# 【R351 修复 - 2026-11】锁内严禁调用 get_db()！
#   get_db() 内部执行 `PRAGMA journal_mode=WAL`，DB 繁忙时会阻塞至 db_timeout
#   （默认 30 秒），导致 _FILE_OPS_LOCK 持锁时间不可控，所有并发的
#   rename / delete / delete_batch 请求全部阻塞（全局串行化）。
#   设计原则（参考 R335）：锁内只做纯内存操作 + nt.start()（立即返回）。
#   DB 检查移到锁外，worker 内部 IA API 404 兜底。
#
# 为什么不复用其他锁：
#   - DB_LOCK：锁内会启动 worker（worker 内部调 DB_LOCK）→ 死锁风险
#   - _RETRY_TASK_LOCK（在 netdisk_routes_api_tasks.py）：只锁 api_retry_task，
#     跨模块互相 import 会循环，本文件独立
#   - CONFIG_LOCK：只保护 config.json 写入
#   - APPEND_CWD_LOCK / UPLOAD_COOLDOWN_LOCK：专用于各自场景
_FILE_OPS_LOCK = threading.Lock()


# ========== 【⏱️ 延迟与超时精细控制】3 个带 5 秒 TTL 缓存的读取函数 ==========
_THUMB_TIMEOUT_CACHE = {'value': 4, 'at': 0.0}
_THUMB_MANUAL_TIMEOUT_CACHE = {'value': 15, 'at': 0.0}
_THUMB_FAIL_CACHE_TTL_CACHE = {'value': 300, 'at': 0.0}
# 【B 级新增】thumb_ip_window 5 秒 TTL 缓存
_THUMB_IP_WINDOW_CACHE = {'value': 60, 'at': 0.0}
_CFG_CACHE_TTL = 5.0


def _get_thumb_timeout():
    """返回自动拉缩略图的超时（秒），默认 4，范围 1-60。"""
    now = time.time()
    if now - _THUMB_TIMEOUT_CACHE['at'] < _CFG_CACHE_TTL:
        return _THUMB_TIMEOUT_CACHE['value']
    try:
        v = max(1, min(int(cfg_get('thumb_timeout', 4)), 60))
    except Exception:
        v = 4
    _THUMB_TIMEOUT_CACHE['value'] = v
    _THUMB_TIMEOUT_CACHE['at'] = now
    return v


def _get_thumb_manual_timeout():
    """返回手动刷新缩略图 / download 分支的超时（秒），默认 15，范围 3-120。"""
    now = time.time()
    if now - _THUMB_MANUAL_TIMEOUT_CACHE['at'] < _CFG_CACHE_TTL:
        return _THUMB_MANUAL_TIMEOUT_CACHE['value']
    try:
        v = max(3, min(int(cfg_get('thumb_manual_timeout', 15)), 120))
    except Exception:
        v = 15
    _THUMB_MANUAL_TIMEOUT_CACHE['value'] = v
    _THUMB_MANUAL_TIMEOUT_CACHE['at'] = now
    return v


def _get_thumb_fail_cache_ttl():
    """返回缩略图失败缓存 TTL（秒），默认 300，范围 0-3600。

    语义说明：
      - 0：不缓存失败（每次请求都重新尝试拉取）
      - 300（默认）：失败后 5 分钟内不再尝试拉取
    """
    now = time.time()
    if now - _THUMB_FAIL_CACHE_TTL_CACHE['at'] < _CFG_CACHE_TTL:
        return _THUMB_FAIL_CACHE_TTL_CACHE['value']
    try:
        v = max(0, min(int(cfg_get('thumb_fail_cache_ttl', 300)), 3600))
    except Exception:
        v = 300
    _THUMB_FAIL_CACHE_TTL_CACHE['value'] = v
    _THUMB_FAIL_CACHE_TTL_CACHE['at'] = now
    return v


def _get_thumb_ip_window():
    """【B 级新增】返回缩略图 IP 限流滑动窗口（秒），默认 60，范围 10-3600。

    语义说明：
      - 与 thumb_ip_limit 配合使用：窗口内请求数 >= limit 时返回 429
      - 默认 60 秒：与原代码 `now - t < 60` 完全一致
      - 用户填 10 → 10 秒窗口（更严格，突发流量更容易触发限流）
      - 用户填 3600 → 1 小时窗口（更宽松，长时间累计才触发）
    """
    now = time.time()
    if now - _THUMB_IP_WINDOW_CACHE['at'] < _CFG_CACHE_TTL:
        return _THUMB_IP_WINDOW_CACHE['value']
    try:
        v = max(10, min(int(cfg_get('thumb_ip_window', 60)), 3600))
    except Exception:
        v = 60
    _THUMB_IP_WINDOW_CACHE['value'] = v
    _THUMB_IP_WINDOW_CACHE['at'] = now
    return v


@api_bp.route('/api/file_cache/<ident>')
def api_file_cache(ident):
    owner = session.get('owner')
    conn = get_db()
    exists = conn.execute("SELECT id FROM items WHERE identifier=? AND owner=?", (ident, owner)).fetchone()
    if not exists:
        conn.close()
        return jsonify({"cached": False, "files": []})
    rows = conn.execute("SELECT filename, size, torrent_filename, custom_direct_url FROM file_cache WHERE identifier=?", (ident,)).fetchall()
    conn.close()
    return jsonify({"cached": bool(rows), "files": [
        {"filename": r[0], "size": r[1], "torrent_filename": r[2], "custom_direct_url": r[3]} for r in rows
    ]})


@api_bp.route('/api/rename_file', methods=['POST'])
def api_rename_file():
    """【R345 + R351 + R356 修复 - 2026-11】

    加 _FILE_OPS_LOCK 原子抢占 + 同 identifier 串行检查。

    【R351 修复】锁内移除 DB 二次检查。
      原因：get_db() 的 `PRAGMA journal_mode=WAL` 在 DB 繁忙时会阻塞至
      db_timeout（默认 30 秒），持 _FILE_OPS_LOCK 期间导致所有并发 rename/delete 阻塞。

    【R356 修复】exists 检查加 `AND deleted_at IS NULL`。
      原因：items 表用 deleted_at 软删除标记，回收站中的条目（deleted_at NOT NULL）
      不应再执行 IA 侧 rename 操作（否则 IA 文件被改名但 items 名字不同步）。

    锁内操作：检查同 identifier 活跃 rename + 创建 task + 启动 worker
    锁外操作：save_task_to_db（内部有 DB_LOCK，避免锁内交叉）
    """
    data = request.get_json() or {}
    owner = session.get('owner')
    ident = data.get('identifier')
    rename_list = data.get('rename_list', [])

    if not ident:
        return jsonify({"status": "error", "msg": "缺少 identifier"})
    if not rename_list:
        return jsonify({"status": "error", "msg": "没有可重命名的文件"})

    # 锁外先查 item 存在（快速拒绝，正常用户无额外锁开销）
    # 【R356 修复】加 deleted_at IS NULL（防对回收站条目执行 rename）
    conn = get_db()
    try:
        exists = conn.execute(
            "SELECT id FROM items WHERE identifier=? AND owner=? AND deleted_at IS NULL",
            (ident, owner)
        ).fetchone()
    finally:
        conn.close()
    if not exists:
        return jsonify({"status": "error", "msg": "无权操作（条目不存在或已在回收站）"})

    tid = str(uuid.uuid4())[:8]
    extra_data = {'identifier': ident, 'rename_list': rename_list}

    with _FILE_OPS_LOCK:
        # 【R351 修复】锁内只做内存操作（不调 get_db）
        # 【R345 修复】检查同 identifier 是否已有活跃 rename 任务
        # IA 官方 rename.php 是串行接口，同 identifier 并发会触发 429 / "file not found"
        for k, v in list(UPLOAD_TASKS.items()):
            if (v.get('task_type') == 'rename'
                    and v.get('status') in ('waiting', 'uploading', 'paused')):
                try:
                    existing_extra = json.loads(v.get('extra_data') or '{}')
                except Exception:
                    existing_extra = {}
                if isinstance(existing_extra, dict) and existing_extra.get('identifier') == ident:
                    return jsonify({
                        "status": "error",
                        "msg": f"该条目已有重命名任务进行中（{v.get('title', '')}），请等待完成后再试"
                    })

        UPLOAD_TASKS[tid] = {
            "status": "waiting", "title": f"[批量重命名] {ident}", "task_type": "rename",
            "created_at": time.time(), "extra_data": json.dumps(extra_data)
        }
        try:
            t = threading.Thread(target=rename_files_worker, args=(tid, ident, rename_list), daemon=True)
            UPLOAD_TASKS[tid]['_thread_obj'] = t
            t.start()
        except Exception as e:
            UPLOAD_TASKS.pop(tid, None)
            logging.error(f"[api_rename_file] 启动 worker 失败: {e}")
            return jsonify({"status": "error", "msg": f"启动 worker 失败: {e}"})

    # 锁外持久化（内部有 DB_LOCK）
    core.save_task_to_db(tid)
    return jsonify({"status": "success", "msg": "重命名任务已提交至队列"})


@api_bp.route('/api/delete_file', methods=['POST'])
def api_delete_file():
    """【R345 + R351 + R356 修复 - 2026-11】

    加 _FILE_OPS_LOCK 原子抢占，防止两个并发删除请求同时启动 delete_files_worker
    （第二个 ia.delete 返回 404 被当成功，浪费一次 API）。

    【R351 修复】锁内移除 DB 二次检查（原因同 api_rename_file）。

    【R356 修复】item 检查加 `AND deleted_at IS NULL`。
      原因：items 在回收站（deleted_at NOT NULL）时，仍调用 ia.delete 真的
      删除 IA 文件 → 用户从回收站"恢复"后发现文件已丢失 → 数据丢失。
      修复后：对回收站条目返回错误，不会真的删除 IA 文件。
    """
    data = request.get_json() or {}
    owner = session.get('owner')
    ident = data.get('identifier')
    filenames = data.get('filenames', [])
    if not ident or not filenames:
        return jsonify({"status": "error", "msg": "参数缺失"})

    # 锁外先查 item 存在
    # 【R356 修复】加 deleted_at IS NULL（防对回收站条目真的删除 IA 文件）
    conn = get_db()
    try:
        item = conn.execute(
            "SELECT id FROM items WHERE identifier=? AND owner=? AND deleted_at IS NULL",
            (ident, owner)
        ).fetchone()
    finally:
        conn.close()
    if not item:
        return jsonify({"status": "error", "msg": "无权操作或条目不存在（可能在回收站中，请先恢复）"})

    tid = str(uuid.uuid4())[:8]
    title_str = filenames[0] if len(filenames) == 1 else f"{len(filenames)} 个文件"
    extra = json.dumps({'identifier': ident, 'filenames': filenames, 'db_id': item[0]})

    with _FILE_OPS_LOCK:
        # 【R351 修复】锁内只做内存操作（不调 get_db）
        UPLOAD_TASKS[tid] = {
            "status": "waiting", "title": f"[批量删除] {title_str}", "task_type": "delete",
            "created_at": time.time(), "extra_data": extra
        }
        try:
            t = threading.Thread(target=delete_files_worker, args=(tid, ident, filenames, item[0]), daemon=True)
            UPLOAD_TASKS[tid]['_thread_obj'] = t
            t.start()
        except Exception as e:
            UPLOAD_TASKS.pop(tid, None)
            logging.error(f"[api_delete_file] 启动 worker 失败: {e}")
            return jsonify({"status": "error", "msg": f"启动 worker 失败: {e}"})

    # 锁外持久化（内部有 DB_LOCK）
    core.save_task_to_db(tid)
    return jsonify({"status": "success", "msg": "删除任务已提交", "task_id": tid})


@api_bp.route('/api/delete_batch', methods=['POST'])
def api_delete_batch():
    """【R345 + R351 + R356 修复 - 2026-11】

    加 _FILE_OPS_LOCK 原子抢占，防止同一 group 并发提交两次创建两个 tid
    （uuid 不同 → 两个 worker 处理同一批文件）。

    【R351 修复】改为锁外一次 get_db() 收集有效 group 的 db_id，
    锁内纯内存创建 task + 启动 worker。
    原因：原代码每个 group 一次 get_db()，groups 多时锁内 DB 时间线性增长；
    且 get_db() 的 WAL PRAGMA 在 DB 繁忙时可能阻塞至 db_timeout（默认 30 秒）。

    【R356 修复】SELECT 加 `AND deleted_at IS NULL`。
      原因：items 在回收站时不应真的删除 IA 文件（用户恢复后数据丢失）。
      回收站中的 group 直接进 skipped 列表，不启动 worker。
    """
    data = request.get_json() or {}
    groups = data.get('groups', [])
    owner = session.get('owner')
    if not groups:
        return jsonify({'status': 'error', 'msg': '没有可删除的文件'})

    # 【R351 修复】锁外一次 DB 查询，收集所有有效 group 的 db_id
    # 【R356 修复】加 deleted_at IS NULL
    # 无 item / 在回收站的 group → 直接进 skipped，不进锁
    valid_groups = []  # [(identifier, title, filenames, db_id), ...]
    skipped = []

    conn = get_db()
    try:
        for g in groups:
            identifier = g.get('identifier', '')
            title = g.get('title', identifier)
            filenames = g.get('filenames', [])
            if not identifier or not filenames:
                continue

            row = conn.execute(
                "SELECT id FROM items WHERE identifier=? AND owner=? AND deleted_at IS NULL",
                (identifier, owner)
            ).fetchone()
            if not row:
                skipped.append(identifier)
                continue

            valid_groups.append((identifier, title, filenames, row[0]))
    finally:
        conn.close()

    if not valid_groups:
        return jsonify({'status': 'success', 'task_ids': [], 'count': 0, 'skipped': skipped})

    # 【R351 修复】锁内纯内存创建 task + 启动 worker（不调 get_db）
    task_ids = []
    with _FILE_OPS_LOCK:
        for identifier, title, filenames, db_id in valid_groups:
            tid = str(uuid.uuid4())[:8]
            extra = json.dumps({'identifier': identifier, 'filenames': filenames, 'db_id': db_id})

            UPLOAD_TASKS[tid] = {
                'status': 'waiting', 'msg': '准备中...', 'task_type': 'delete',
                'title': f'[批量删除] {title} ({len(filenames)} 个文件)',
                'percent': 0, 'speed': '0.0', 'current': '0', 'total': str(len(filenames)),
                'total_files_count': len(filenames), 'total_size_bytes': 0,
                'files_uploaded': 0, 'uploaded_bytes': 0,
                'raw_line': '准备中...', 'created_at': time.time(), 'extra_data': extra,
            }
            try:
                t = threading.Thread(target=delete_files_worker, args=(tid, identifier, filenames, db_id), daemon=True)
                UPLOAD_TASKS[tid]['_thread_obj'] = t
                t.start()
            except Exception as e:
                UPLOAD_TASKS.pop(tid, None)
                logging.error(f"[api_delete_batch] 启动 worker 失败 ({identifier}): {e}")
                skipped.append(identifier)
                continue
            task_ids.append(tid)

    # 锁外持久化（内部有 DB_LOCK）
    for tid in task_ids:
        core.save_task_to_db(tid)

    return jsonify({'status': 'success', 'task_ids': task_ids, 'count': len(task_ids), 'skipped': skipped})


@api_bp.route('/api/set_custom_link', methods=['POST'])
def api_set_custom_link():
    """【R346 + R361 修复 - 2026-11】检查 rowcount + data.get() 防 KeyError。

    原代码 UPDATE file_cache 影响 0 行时（filename 不存在，用户旧页面/并发删除）
    静默返回 success，用户以为成功但实际未生效。
    现在明确返回"文件不存在，请刷新页面"。

    【R361 修复】exists 检查加 `AND deleted_at IS NULL`，与 R356 完全对齐。
      原因：items 在回收站（deleted_at NOT NULL）时不应再修改直链
      （否则用户恢复后看到"奇怪"的直链）。
    """
    data = request.get_json() or {}
    owner = session.get('owner')
    ident = data.get('identifier')
    filename = data.get('filename')
    url = data.get('url', '')

    if not ident or not filename:
        return jsonify({"status": "error", "msg": "参数缺失"})

    with DB_LOCK:
        conn = get_db()
        try:
            # 【R361 修复】加 deleted_at IS NULL
            exists = conn.execute(
                "SELECT id FROM items WHERE identifier=? AND owner=? AND deleted_at IS NULL",
                (ident, owner)
            ).fetchone()
            if not exists:
                return jsonify({"status": "error", "msg": "无权操作（条目不存在或已在回收站）"})

            cur = conn.execute(
                "UPDATE file_cache SET custom_direct_url=? WHERE identifier=? AND filename=?",
                (url, ident, filename)
            )
            if cur.rowcount == 0:
                # 【R346 修复】filename 不存在（用户旧页面/并发删除/前端 bug）
                return jsonify({
                    "status": "error",
                    "msg": "文件不存在（可能已被重命名或删除），请刷新页面"
                })

            row = conn.execute("SELECT file_name FROM items WHERE identifier=? AND owner=?", (ident, owner)).fetchone()
            if row and row[0] == filename:
                conn.execute("UPDATE items SET direct_url=? WHERE identifier=? AND owner=?", (url, ident, owner))
            conn.commit()
        finally:
            conn.close()
    return jsonify({"status": "success"})


@api_bp.route('/api/update_tags', methods=['POST'])
def api_update_tags():
    """【R346 + R356 修复 - 2026-11】检查 rowcount + deleted_at 过滤。

    原代码 _push_tags_update 成功后 UPDATE items，若 items 在此期间被并发删除
    （用户在另一个 tab/设备操作），rowcount=0，但返回 success，
    用户以为本地已同步，实际 IA 已更新但本地未同步。
    现在明确返回"本地记录已被删除（IA 已更新，但本地未同步）"。

    【R356 修复】exists 检查加 `AND deleted_at IS NULL`。
      原因：items 在回收站（deleted_at NOT NULL）时不应再推送 tags 到 IA
      （IA 更新成功但本地 items 可能已删除/已软删，状态不一致）。
    """
    data = request.get_json() or {}
    owner = session.get('owner')
    ident = data.get('identifier')
    tags = data.get('tags')
    if not ident:
        return jsonify({"status": "error", "msg": "缺少 ID"})

    # 锁外查 item 存在
    # 【R356 修复】加 deleted_at IS NULL
    conn = get_db()
    try:
        exists = conn.execute(
            "SELECT id FROM items WHERE identifier=? AND owner=? AND deleted_at IS NULL",
            (ident, owner)
        ).fetchone()
    finally:
        conn.close()
    if not exists:
        return jsonify({"status": "error", "msg": "无权操作（条目不存在或已在回收站）"})

    ok, msg = _push_tags_update(ident, tags)
    if ok:
        with DB_LOCK:
            conn = get_db()
            try:
                cur = conn.execute("UPDATE items SET tags=? WHERE identifier=? AND owner=?", (tags, ident, owner))
                if cur.rowcount == 0:
                    # 【R346 修复】items 在 _push_tags_update 期间被并发删除
                    return jsonify({
                        "status": "error",
                        "msg": "本地记录已被删除（IA 已更新，但本地未同步）"
                    })
                conn.commit()
            finally:
                conn.close()
        return jsonify({"status": "success"})
    return jsonify({"status": "error", "msg": msg})


@api_bp.route('/thumb/<identifier>')
def serve_thumb(identifier):
    """缩略图路由。

    【⏱️ 延迟与超时精细控制】
      - download 分支（用户主动点「💾 保存图片」）→ thumb_manual_timeout（默认 15 秒）
      - 自动拉缩略图（首页/详情页加载时触发）→ thumb_timeout（默认 4 秒）
      - 失败缓存 TTL → thumb_fail_cache_ttl（默认 300 秒）
    【B 级新增】
      - IP 限流滑动窗口 → thumb_ip_window（默认 60 秒）
    """
    thumb_path = os.path.join(core.THUMB_DIR, f"{identifier}.jpg")
    ip, now = request.remote_addr, time.time()
    force = request.args.get('force') == '1'
    download = request.args.get('download') == '1'
    if download:
        # 【⏱️ 延迟与超时精细控制】timeout=15 → thumb_manual_timeout
        resp = ia_get(f"https://archive.org/services/img/{identifier}", timeout=_get_thumb_manual_timeout(), stream=True)
        if resp and resp.status_code == 200:
            return send_file(resp.raw, mimetype='image/jpeg', as_attachment=True, download_name=f"{identifier}.jpg")
        return '', 404
    if os.path.exists(thumb_path):
        resp = make_response(send_file(thumb_path, mimetype='image/jpeg'))
        resp.headers['Cache-Control'] = 'public, max-age=31536000'
        return resp
    if not force and not cfg_get('auto_load_thumb', False):
        return '', 404
    # 【B 级新增】60 → _get_thumb_ip_window()
    thumb_ip_window = _get_thumb_ip_window()
    THUMB_IP_LOG[ip] = [t for t in THUMB_IP_LOG[ip] if now - t < thumb_ip_window]
    if len(THUMB_IP_LOG[ip]) >= cfg_get('thumb_ip_limit', 30):
        return '', 429
    THUMB_IP_LOG[ip].append(now)
    t = THUMB_FAIL_CACHE.get(identifier)
    # 【⏱️ 延迟与超时精细控制】300 → thumb_fail_cache_ttl
    if t and now - t < _get_thumb_fail_cache_ttl() and not force:
        return '', 404
    # 【⏱️ 延迟与超时精细控制】timeout=4 → thumb_timeout
    resp = ia_get(f"https://archive.org/services/img/{identifier}", timeout=_get_thumb_timeout(), stream=True)
    if resp and resp.status_code == 200:
        tmp_path = thumb_path + f".tmp_{uuid.uuid4().hex[:4]}"
        try:
            with open(tmp_path, 'wb') as f:
                for chunk in resp.iter_content(8192): f.write(chunk)
            os.replace(tmp_path, thumb_path)
            resp_file = make_response(send_file(thumb_path, mimetype='image/jpeg'))
            resp_file.headers['Cache-Control'] = 'public, max-age=31536000'
            return resp_file
        except:
            try: os.remove(tmp_path)
            except: pass
    else:
        THUMB_FAIL_CACHE[identifier] = now
    return '', 404


@api_bp.route('/api/thumb/refresh/<identifier>', methods=['POST'])
def api_thumb_refresh(identifier):
    """手动刷新缩略图（用户主动点击「🔄 重新获取」）。

    【⏱️ 延迟与超时精细控制】timeout=15 → thumb_manual_timeout（默认 15 秒）
    """
    thumb_path = os.path.join(core.THUMB_DIR, f"{identifier}.jpg")
    THUMB_FAIL_CACHE.pop(identifier, None)
    # 【⏱️ 延迟与超时精细控制】timeout=15 → thumb_manual_timeout
    resp = ia_get(f"https://archive.org/services/img/{identifier}", timeout=_get_thumb_manual_timeout(), stream=True)
    if resp and resp.status_code == 200:
        tmp_path = thumb_path + f".tmp_{uuid.uuid4().hex[:4]}"
        try:
            with open(tmp_path, 'wb') as f:
                for chunk in resp.iter_content(8192): f.write(chunk)
            os.replace(tmp_path, thumb_path)
            return jsonify({"status": "success"})
        except:
            try: os.remove(tmp_path)
            except: pass
    return jsonify({"status": "error", "msg": "下载失败，已保留原图"})


@api_bp.route('/api/thumb/custom/<identifier>', methods=['POST'])
def api_thumb_custom(identifier):
    file = request.files.get('file')
    if not file:
        return jsonify({"status": "error", "msg": "未收到文件"})
    thumb_path = os.path.join(core.THUMB_DIR, f"{identifier}.jpg")
    file.save(thumb_path)
    if request.form.get('sync_ia') == 'yes':
        try:
            ok, msg = core.upload_custom_thumb_py(identifier, thumb_path)
            if not ok:
                return jsonify({"status": "success", "msg": f"本地已保存，但同步官方失败: {msg}"})
        except Exception as e:
            return jsonify({"status": "success", "msg": f"本地已保存，但同步官方失败: {str(e)}"})
    return jsonify({"status": "success"})