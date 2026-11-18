# netdisk_routes_api_download.py
# 下载/拉取/重试上传 相关 API
# 【改进点 A1】api_retry_single 先查 items.status：
#   - deleted_at 非空（在回收站）→ 拒绝
#   - status='done' → 拒绝
#   - 其它状态 → 正常走原逻辑
# 【修复 A1-边缘】原子 UPDATE 加状态条件 + rowcount 检查
# 【修复 A1-边缘2】孤儿 uploading 检测 + 重置为 error 后可重试
# 【改进点 EE1】list(UPLOAD_TASKS.values()) 快照防 RuntimeError
# 【改进点 EE2】str() 归一化类型比较
# 【改进点 EE3】if not owner 前置检查
# 【修复 F1】把 幂等检查 + 孤儿检测 + 原子 UPDATE + task 写入 全部放进 DB_LOCK 内串行，
#            避免并发重试创建多个 task（修复 A1-边缘2 引入的 uploading 允许集合窗口）
#            save_task_to_db 移到锁外（内部有 DB_LOCK，锁内调用会重入死锁）
# 【修复 F2】孤儿检测 UPDATE 加 status='uploading' 条件，防把已完成条目打回 error
#
# 【⏱️ 延迟与超时精细控制 - 2026-11 第 9/20 步】
# 接入 1 个新配置项（cfg_get 读取 + 5 秒 TTL 缓存）：
#   virtual_task_ttl (默认 0 = 永不自动清理，60-86400 秒)
#
# 【兼容性说明（重要）】
#   原代码虚拟任务 expires_at 硬编码 time.time() + 1800（30 分钟后自动清理）
#   新代码由 _get_virtual_task_expires_at() 生成：
#     - ttl <= 0（默认）→ expires_at = time.time() + 100 * 365 * 86400（约 100 年后）
#       实现「永不自动清理」效果，**兼容当前 netdisk_app_discover.py 的
#       _clean_virtual_tasks_worker 判定逻辑 `expires_at < now`**，不需要修改该文件
#     - ttl > 0 → expires_at = time.time() + ttl（到点清理）
#
#   为什么不直接用 0 作为哨兵值：
#     当前 _clean_virtual_tasks_worker（在 netdisk_app_discover.py，不在本次 20 个
#     文件清单里）判定是 `expires_at < now`，0 < now 永为 True → 立即删除。
#     用 100 年后的时间戳可以在不修改该文件的情况下实现「永不清理」。
#     后续若用户提议修复 netdisk_app_discover.py 加哨兵值判定，
#     本函数可简化为返回 0。
#
# 【默认行为对比】
#   原代码：浏览器下载虚拟任务 30 分钟后自动清理
#   新默认（ttl=0）：永不自动清理（符合用户"默认不自动"原则）
#   用户想恢复 30 分钟行为：在设置页填 1800 即可
#
# 【设计说明】
#   - _get_virtual_task_expires_at 带 5 秒 TTL 缓存，用户改配置后最多 5 秒生效
#   - config.json 损坏 / 非法值 → 回退默认值（永不清理）
#   - api_retry_single 里对虚拟任务的处理保留原样（虚拟任务不支持重试）
#   - api_pull_from_ia 无时间相关硬编码

import json
import uuid
import threading
import time
from flask import request, jsonify, session

import netdisk_core as core
from netdisk_blueprints import api_bp
from netdisk_core import (
    UPLOAD_TASKS, DB_LOCK, get_db, get_config, cfg_get,
    download_folder_worker, pull_from_ia_worker, upload_worker,
)


# ========== 【⏱️ 延迟与超时精细控制】virtual_task_ttl 5 秒 TTL 缓存 ==========
_VIRTUAL_TASK_TTL_CACHE = {'value': 0, 'at': 0.0}
_CFG_CACHE_TTL = 5.0


def _get_virtual_task_ttl():
    """返回虚拟任务过期秒数，默认 0（永不自动清理），范围 0-86400。"""
    now = time.time()
    if now - _VIRTUAL_TASK_TTL_CACHE['at'] < _CFG_CACHE_TTL:
        return _VIRTUAL_TASK_TTL_CACHE['value']
    try:
        v = max(0, min(int(cfg_get('virtual_task_ttl', 0)), 86400))
    except Exception:
        v = 0
    _VIRTUAL_TASK_TTL_CACHE['value'] = v
    _VIRTUAL_TASK_TTL_CACHE['at'] = now
    return v


def _get_virtual_task_expires_at():
    """返回虚拟任务的 expires_at 时间戳。

    【兼容性说明】
    - ttl <= 0（默认）→ time.time() + 100 * 365 * 86400（约 100 年后）
      当前 _clean_virtual_tasks_worker 判定 `expires_at < now`，100 年后永远
      不会小于 now → 永不清理。用户不需要改 netdisk_app_discover.py。
    - ttl > 0 → time.time() + ttl（到点清理）

    为什么不用 0 作为哨兵值：当前清理 worker 的 `0 < now` 永为 True →
    会立即删除。100 年后的时间戳是兼容写法，功能等价于"永不清理"。
    """
    ttl = _get_virtual_task_ttl()
    if ttl <= 0:
        # 100 年后（约 2123 年），远超系统时间，永不清理
        return time.time() + 100 * 365 * 86400
    return time.time() + ttl


@api_bp.route('/api/download_batch', methods=['POST'])
def api_download_batch():
    data = request.get_json() or {}
    items = data.get('items', [])
    mode = data.get('mode', 'termux')
    if not items:
        return jsonify({'status': 'error', 'msg': '没有可下载的内容'})

    cfg = get_config()
    dl_dir = cfg.get('download_dir', '/sdcard/Download')
    task_ids = []

    # 【⏱️ 延迟与超时精细控制】browser 分支的 expires_at 一次性读取（避免循环里多次读 config）
    #   - ttl <= 0（默认）→ 约 100 年后（永不清理）
    #   - ttl > 0 → 到点清理
    virtual_expires_at = _get_virtual_task_expires_at()

    for it in items:
        it_type = it.get('type', 'file')
        it_name = it.get('name', 'unnamed')
        files = it.get('files', [])
        if not files: continue

        tid = str(uuid.uuid4())[:8]
        total_size = sum(int(f.get('size', 0) or 0) for f in files)

        if mode == 'browser':
            UPLOAD_TASKS[tid] = {
                'status': 'done', 'msg': f'📤 已发送 {len(files)} 个文件到浏览器',
                'task_type': 'download_browser', 'is_virtual': True,
                'title': f'[浏览器下载] {it_name} ({len(files)} 个文件)',
                'percent': 100, 'speed': '0.0', 'current': str(len(files)), 'total': str(len(files)),
                'total_files_count': len(files), 'total_size_bytes': total_size,
                'files_uploaded': len(files), 'uploaded_bytes': total_size,
                'raw_line': '浏览器下载：由浏览器接管，队列仅记录任务信息。',
                'created_at': time.time(),
                # 【⏱️ 延迟与超时精细控制】原 time.time() + 1800 → _get_virtual_task_expires_at()
                'expires_at': virtual_expires_at,
            }
            core.save_task_to_db(tid)
            task_ids.append(tid)
            continue

        if it_type == 'folder':
            folder_name = it_name
            task_title = f'[下载文件夹] {it_name} ({len(files)} 个文件)'
            task_type = 'download_folder'
        else:
            folder_name = ''
            if len(files) == 1:
                fn = files[0]['filename'].split('/')[-1]
                task_title = f'[下载] {fn}'
            else:
                task_title = f'[下载] {it_name} ({len(files)} 个文件)'
            task_type = 'download'

        extra = json.dumps({'folder_name': folder_name, 'files_list': files, 'dl_dir': dl_dir})

        UPLOAD_TASKS[tid] = {
            'status': 'waiting', 'msg': '准备中...', 'task_type': task_type,
            'title': task_title, 'percent': 0, 'speed': '0.0',
            'current': '0', 'total': str(len(files)),
            'total_files_count': len(files), 'total_size_bytes': total_size,
            'files_uploaded': 0, 'uploaded_bytes': 0,
            'raw_line': '准备中...', 'created_at': time.time(), 'extra_data': extra,
        }
        core.save_task_to_db(tid)
        t = threading.Thread(target=download_folder_worker, args=(tid, folder_name, files, dl_dir), daemon=True)
        UPLOAD_TASKS[tid]['_thread_obj'] = t
        t.start()
        task_ids.append(tid)

    return jsonify({'status': 'success', 'task_ids': task_ids, 'count': len(task_ids), 'mode': mode})


@api_bp.route('/api/pull_from_ia', methods=['POST'])
def api_pull_from_ia():
    owner = session.get('owner')
    if not owner:
        return jsonify({"status": "error", "msg": "未登录"})

    tid = str(uuid.uuid4())[:8]
    extra_data = {'owner': owner, 'current_page': 1}

    UPLOAD_TASKS[tid] = {
        "status": "waiting", "title": f"[全量拉取] {owner}", "task_type": "pull",
        "created_at": time.time(), "extra_data": json.dumps(extra_data)
    }
    core.save_task_to_db(tid)

    t = threading.Thread(target=pull_from_ia_worker, args=(tid, owner, 1), daemon=True)
    UPLOAD_TASKS[tid]['_thread_obj'] = t
    t.start()

    return jsonify({"status": "success", "msg": "拉取任务已提交至队列"})


@api_bp.route('/api/retry_single', methods=['POST'])
def api_retry_single():
    owner = session.get('owner')
    if not owner:
        return jsonify({'status': 'error', 'msg': '未登录'})

    data = request.get_json() or {}
    item_id = data.get('id')
    if not item_id:
        return jsonify({'status': 'error', 'msg': '参数缺失'})

    # 【A1】先查 items.status 做友好拒绝
    with DB_LOCK:
        conn = get_db()
        try:
            row = conn.execute(
                "SELECT status, deleted_at FROM items WHERE id=? AND owner=?",
                (item_id, owner)
            ).fetchone()
        finally:
            conn.close()

    if not row:
        return jsonify({'status': 'error', 'msg': '任务不存在或无权访问'})

    item_status = row[0] or ''
    item_deleted_at = row[1]

    if item_deleted_at is not None:
        return jsonify({'status': 'error', 'msg': '该条目在回收站中，请先从回收站「♻️ 恢复」后再重试'})

    if item_status == 'done':
        return jsonify({
            'status': 'error',
            'msg': '该条目已上传完成，无需重试。如需重新上传，请使用「✏️ 编辑」修改元数据或「🗑️ 删除」后重新创建。'
        })

    # 【修复 F1】把幂等检查 + 孤儿检测 + 原子 UPDATE + task 写入全部放进 DB_LOCK 内串行
    tid = None
    item = None
    with DB_LOCK:
        conn = get_db()
        try:
            # 1) 幂等检查（在锁内，与其它并发重试串行）
            for t in list(UPLOAD_TASKS.values()):
                if (t.get('status') in ('waiting', 'uploading', 'paused')
                        and t.get('item_data')
                        and str(t['item_data'][0]) == str(item_id)):
                    return jsonify({'status': 'success'})

            # 2) 孤儿 uploading 检测（items.status='uploading' 但无活跃 task）
            #    【修复 F2】UPDATE 加 status='uploading' 条件，防把已完成条目打回 error
            if item_status == 'uploading':
                cur_reset = conn.execute(
                    "UPDATE items SET status='error', error_msg='检测到孤儿 uploading 状态，已自动重置，请点击重试' WHERE id=? AND owner=? AND status='uploading' AND deleted_at IS NULL",
                    (item_id, owner)
                )
                if cur_reset.rowcount > 0:
                    conn.commit()
                    item_status = 'error'

            # 3) 原子 UPDATE（不含 uploading，避免 F1 并发场景）
            cur = conn.execute(
                "UPDATE items SET status='uploading' WHERE id=? AND owner=? AND deleted_at IS NULL AND status IN ('pending','error','meta_pending','cloud_missing')",
                (item_id, owner)
            )
            if cur.rowcount == 0:
                conn.rollback()
                return jsonify({
                    'status': 'error',
                    'msg': '条目状态已变化（可能已上传完成或被删除），请刷新页面后重试'
                })
            conn.commit()

            # 4) 拿完整 item + 写入 UPLOAD_TASKS（锁内，与幂等检查串行，防 F1 并发窗口）
            item = conn.execute("SELECT id, folder_id, identifier, title, creator, description, date, mediatype, collection, language, license, tags, file_path, file_name, custom_date FROM items WHERE id=?", (item_id,)).fetchone()
            if item:
                tid = str(uuid.uuid4())[:8]
                UPLOAD_TASKS[tid] = {
                    "status": "waiting", "msg": "排队中...", "title": item[3], "item_data": item,
                    "task_type": "upload", "percent": 0, "speed": "0.0", "current": "", "total": "",
                    "eta": "", "raw_line": "", "batch_total": 1, "batch_done": 0, "created_at": time.time()
                }
        finally:
            conn.close()

    if not item or not tid:
        return jsonify({'status': 'error', 'msg': '任务不存在'})

    # 【修复 F1】save_task_to_db 在锁外（内部有 DB_LOCK，锁内调用会重入死锁）
    core.save_task_to_db(tid)
    opts = {'generate_torrent': cfg_get('default_generate_torrent', False), 'access_level': 'public'}
    t = threading.Thread(target=upload_worker, args=(tid, item, None, opts, 1, 0), daemon=True)
    UPLOAD_TASKS[tid]['_thread_obj'] = t
    t.start()
    return jsonify({'status': 'success'})