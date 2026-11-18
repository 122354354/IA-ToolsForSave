# netdisk_workers_rename.py
# 批量重命名工作线程
# 同步修复：
#   Bug 2 类 - UPLOAD_TASKS[task_id] 判空
#   Bug 3 类 - _worker_token 闭包比较
#   Bug 14 类 - 风控/普通重试等待循环 token 检查
#   Bug 17 类 - acquire 后重新检查 token
#   Bug 21 类 - acquire 后只切 uploading 条件为 "仍是 waiting"
#   Bug 31 类 - 暂停循环只在 paused 时等
#   原代码改进 - 风控 5 次失败时 err_msg 不为空
#   精简 import
#   【Bug 22 - 6/7】for 循环内 acquire 传 should_stop 回调
#   【Bug 65】for 循环改为 while + idx
#   【Bug 10】raw_line 从字符串累积改为 deque + _append_log 单行截断
#   【Bug 82】deque 初始化后立即塞入 "准备重命名 N 个文件..." 初始行
#   【改进点 80 - 5/6】删除本地 _append_log 定义，改为 from netdisk_utils import _append_log
#
# 【⏱️ 延迟与超时精细控制 - 2026-11 第 13/20 步】
# 接入 worker_max_retries（默认 5，0-20）→ max(1, _get_worker_max_retries()) 兜底
#
# 【🔴 R108 同源修复 - 2026-11（本轮）】
#   问题：rename worker 的 attempt 循环内两个等待循环（风控 60 秒 / 普通重试 5 秒）
#        只检查 `_t is None` 和 `_worker_token`，**不响应暂停**。
#        用户暂停后 worker 会继续等完风控/重试，最终 5 次失败标 error。
#        用户感知：暂停没生效 + 最终失败。
#   触发路径：
#     1. 用户点重命名 → attempt=1 → 触发风控 403/429/503 → 60 秒冷却循环
#     2. 用户点「暂停」→ status='paused' → 但等待循环不检查 → 继续等 60 秒
#     3. 60 秒后 → attempt += 1 → 继续 attempt 循环（用户已暂停但 worker 未响应）
#     4. 5 次失败后 → 标 error（用户困惑：我明明暂停了）
#   修复：两个等待循环内加 `if _t.get('status') == 'paused'` 检查：
#     - 检测到 paused → 保持 paused 状态 + msg「已暂停，点击「继续」或「重试」可恢复」
#     - return（worker 退出，finally 释放 slot）
#     - 用户点「继续」→ api_toggle_pause 检测 worker 已死 + task_type != upload
#       → 标 error + "请点「🔄 重试」重新排队"
#     - 用户点「重试」→ api_retry_task 重新启动 rename_files_worker（从头开始）
#   说明：rename 无断点续传机制（不像 upload/append 有 S3 分片断点），
#        暂停后必须从头重试（原代码行为，非本轮新增）。
#
# 【📝 保留记录 - 2026-11，低优先，以后修】
#   R108 已修（meta + rename）
#   R304：append worker 是否有类似问题需第 7/8 次生成时检查
#   R305：rename 暂停后无法断点续传（原设计限制），用户需从头重试

import time
import uuid
import collections

import requests

from netdisk_state import (
    UPLOAD_TASKS, DB_LOCK,
    get_db, cfg_get,
)
from netdisk_utils import (
    CONCURRENCY_LIMITER, IA_LIMITER,
    _append_log,  # 【改进点 80 - 5/6】改为从 netdisk_utils 导入
    _fetch_and_cache_ia_files, get_ssl_verify, get_req_proxies,
    save_task_to_db, _get_s3_credentials,
)


# ========== 【⏱️ 延迟与超时精细控制】worker_max_retries 5 秒 TTL 缓存 ==========
_WORKER_MAX_RETRIES_CACHE = {'value': 5, 'at': 0.0}
_CFG_CACHE_TTL = 5.0


def _get_worker_max_retries():
    """返回推送元数据 / 重命名 worker 最大尝试次数（含首次），默认 5，范围 0-20。

    语义说明（重要）：
      - 这是「最大尝试次数」而非「重试次数」
      - 0 → 调用方 max(1, ...) 兜底为 1 次尝试（不重试），避免 while 循环不执行
      - 5 → 5 次尝试 = 1 次初始 + 4 次重试（原默认行为）
    """
    now = time.time()
    if now - _WORKER_MAX_RETRIES_CACHE['at'] < _CFG_CACHE_TTL:
        return _WORKER_MAX_RETRIES_CACHE['value']
    try:
        v = max(0, min(int(cfg_get('worker_max_retries', 5)), 20))
    except Exception:
        v = 5
    _WORKER_MAX_RETRIES_CACHE['value'] = v
    _WORKER_MAX_RETRIES_CACHE['at'] = now
    return v


def _safe_update(task_id, patch):
    """【Bug 2 类修复】安全更新任务状态，任务被删除时静默跳过"""
    t = UPLOAD_TASKS.get(task_id)
    if t is None:
        return False
    t.update(patch)
    return True


def rename_files_worker(task_id, identifier, rename_list):
    if not rename_list:
        _safe_update(task_id, {'status': 'error', 'msg': '❌ 没有可重命名的文件'})
        save_task_to_db(task_id)
        return

    # 【Bug 2 类修复】任务可能在 API 侧被删除后 worker 才真正开始
    if task_id not in UPLOAD_TASKS:
        return

    total_files_count = len(rename_list)

    # 【Bug 3 类修复】生成本 worker 唯一的 token
    my_worker_token = uuid.uuid4().hex

    # 【Bug 16 类修复】先 .get() 持有引用
    _existing_at_entry = UPLOAD_TASKS.get(task_id)
    _created_at_val = _existing_at_entry.get('created_at', time.time()) if _existing_at_entry else time.time()

    _safe_update(task_id, {
        'status': 'waiting', 'msg': '等待并发槽位...',
        'task_type': 'rename',
        'title': f'[批量重命名] {identifier} ({total_files_count} 个文件)',
        'percent': 0, 'speed': '0.0',
        'current': '0', 'total': str(total_files_count),
        'total_files_count': total_files_count,
        'total_size_bytes': 0,
        'files_uploaded': 0, 'uploaded_bytes': 0,
        'created_at': _created_at_val,
        'raw_line': f'准备重命名 {total_files_count} 个文件...',
        '_worker_token': my_worker_token
    })
    save_task_to_db(task_id)

    with DB_LOCK:
        conn = get_db()
        conn.execute("DELETE FROM sub_tasks WHERE task_id=?", (task_id,))
        for r in rename_list:
            conn.execute(
                "INSERT OR REPLACE INTO sub_tasks (task_id, file_path, file_size, status, uploaded_bytes) VALUES (?, ?, 0, 'waiting', 0)",
                (task_id, f"{r['old_name']} -> {r['new_name']}")
            )
        conn.commit(); conn.close()

    slot_held = False
    rename_success = False
    try:
        if not UPLOAD_TASKS.get(task_id): return
        CONCURRENCY_LIMITER.acquire(timeout=86400)
        slot_held = True

        _safe_update(task_id, {'status': 'uploading', 'msg': '开始重命名...'})
        save_task_to_db(task_id)

        failed_files = []
        idx = 0
        total_files = len(rename_list)
        # 【Bug 10】raw_line 改为 deque + 可配置 maxlen
        history_lines = collections.deque(maxlen=max(10, cfg_get('raw_line_max_lines', 500)))
        # 【Bug 82 修复】塞入初始行，避免被后续日志覆盖
        _append_log(history_lines, f'准备重命名 {total_files_count} 个文件...')

        # 【Bug 65 修复】while + idx 替代 for 循环
        while idx < total_files:
            r = rename_list[idx]
            old_name = r['old_name']
            new_name = r['new_name']
            sub_key = f"{old_name} -> {new_name}"

            # 【Bug 65 修复】等待 + acquire 循环：直到持有槽位且 status=uploading 才处理
            while True:
                _t = UPLOAD_TASKS.get(task_id)
                if _t is None:
                    return
                if _t.get('_worker_token') != my_worker_token:
                    return
                status_now = _t.get('status')

                if status_now == 'paused':
                    if slot_held:
                        CONCURRENCY_LIMITER.release()
                        slot_held = False
                    time.sleep(1)
                    continue

                if status_now in ('error', 'done'):
                    if slot_held:
                        CONCURRENCY_LIMITER.release()
                        slot_held = False
                    return

                if not slot_held:
                    # 【Bug 22 - 6/7】should_stop 回调
                    def _should_stop_waiting():
                        _tt = UPLOAD_TASKS.get(task_id)
                        return _tt is None or _tt.get('_worker_token') != my_worker_token
                    try:
                        CONCURRENCY_LIMITER.acquire(timeout=86400, should_stop=_should_stop_waiting)
                        slot_held = True
                        _t2 = UPLOAD_TASKS.get(task_id)
                        if _t2 is None or _t2.get('_worker_token') != my_worker_token:
                            CONCURRENCY_LIMITER.release()
                            slot_held = False
                            return
                        status_after = _t2.get('status')
                        if status_after == 'waiting':
                            _t2['status'] = 'uploading'
                            _t2['msg'] = '▶️ 已恢复，继续重命名...'
                            save_task_to_db(task_id)
                            break
                        elif status_after == 'uploading':
                            break
                        elif status_after == 'paused':
                            # 【Bug 65】用户又暂停了：释放槽位，回到 while 顶部重新等待
                            CONCURRENCY_LIMITER.release()
                            slot_held = False
                            continue
                        else:
                            CONCURRENCY_LIMITER.release()
                            slot_held = False
                            return
                    except:
                        if slot_held:
                            CONCURRENCY_LIMITER.release()
                            slot_held = False
                        return
                else:
                    if status_now == 'waiting':
                        _t['status'] = 'uploading'
                        _t['msg'] = '▶️ 继续重命名...'
                        save_task_to_db(task_id)
                    break

            # 处理当前文件
            with DB_LOCK:
                conn = get_db()
                conn.execute("UPDATE sub_tasks SET status='uploading' WHERE task_id=? AND file_path=?", (task_id, sub_key))
                conn.commit(); conn.close()

            # 【⏱️ 延迟与超时精细控制】max_attempts = 5 → max(1, _get_worker_max_retries())
            max_attempts = max(1, _get_worker_max_retries())
            success = False
            err_msg = ""
            attempt = 0
            while attempt < max_attempts:
                try:
                    IA_LIMITER.acquire()
                    _t = UPLOAD_TASKS.get(task_id)
                    if _t is None:
                        return
                    if _t.get('_worker_token') != my_worker_token:
                        return

                    url = "https://archive.org/services/tasks.php"
                    payload = {"cmd": "rename.php", "identifier": identifier, "oldname": old_name, "newname": new_name, "access": cfg_get('access_key', ''), "secret": cfg_get('secret_key', '')}

                    ak, sk = _get_s3_credentials()
                    if not ak:
                        raise Exception("无法获取 S3 Token，请检查登录状态")
                    payload['access'] = ak
                    payload['secret'] = sk

                    req_proxies = get_req_proxies()
                    headers = {"User-Agent": "Mozilla/5.0", "Content-Type": "application/json", "Accept": "application/json"}
                    resp = requests.post(url, json=payload, headers=headers, timeout=cfg_get('ia_timeout', 30), verify=get_ssl_verify(), proxies=req_proxies)

                    if resp.status_code in (200, 201, 202):
                        success = True
                        break
                    elif resp.status_code in (403, 429, 503):
                        err_msg = f"HTTP {resp.status_code}: 触发风控"
                        _safe_update(task_id, {'msg': f'⚠️ 触发风控，60秒后重试 ({attempt+1}/{max_attempts})...'})
                        save_task_to_db(task_id)
                        # 【R108 修复】风控 60 秒循环内加 paused 检查
                        for _ in range(60):
                            _t = UPLOAD_TASKS.get(task_id)
                            if _t is None:
                                return
                            if _t.get('_worker_token') != my_worker_token:
                                return
                            # 【R108 修复】检测到 paused → 保持 paused → 退出 worker
                            if _t.get('status') == 'paused':
                                _safe_update(task_id, {
                                    'status': 'paused',
                                    'msg': '⏸️ 已暂停（重命名未完成，点击「继续」或「重试」可恢复）'
                                })
                                save_task_to_db(task_id)
                                return
                            time.sleep(1)
                    else:
                        err_msg = f"HTTP {resp.status_code}: {resp.text[:100]}"
                        break
                except Exception as e:
                    err_msg = str(e)
                    if attempt < max_attempts - 1:
                        # 【R108 修复】普通重试 5 秒循环内加 paused 检查
                        for _ in range(5):
                            _t = UPLOAD_TASKS.get(task_id)
                            if _t is None:
                                return
                            if _t.get('_worker_token') != my_worker_token:
                                return
                            # 【R108 修复】检测到 paused → 保持 paused → 退出 worker
                            if _t.get('status') == 'paused':
                                _safe_update(task_id, {
                                    'status': 'paused',
                                    'msg': '⏸️ 已暂停（重命名未完成，点击「继续」或「重试」可恢复）'
                                })
                                save_task_to_db(task_id)
                                return
                            time.sleep(1)
                    else:
                        break
                attempt += 1

            if success:
                with DB_LOCK:
                    conn = get_db()
                    conn.execute("UPDATE sub_tasks SET status='done' WHERE task_id=? AND file_path=?", (task_id, sub_key))
                    conn.commit(); conn.close()
            else:
                failed_files.append(sub_key)
                if not err_msg:
                    err_msg = '重试次数耗尽'
                with DB_LOCK:
                    conn = get_db()
                    conn.execute("UPDATE sub_tasks SET status='error', error_msg=? WHERE task_id=? AND file_path=?", (err_msg, task_id, sub_key))
                    conn.commit(); conn.close()

            pct = round(((idx + 1) / total_files) * 100, 1)
            log_line = f"✅ [{idx+1}/{total_files}] {sub_key}" if success else f"❌ [{idx+1}/{total_files}] {sub_key} - {err_msg}"
            _append_log(history_lines, log_line)
            _t = UPLOAD_TASKS.get(task_id)
            if _t is not None:
                _t.update({
                    'percent': pct,
                    'files_uploaded': idx + 1,
                    'current': str(idx + 1),
                    'raw_line': "\n".join(history_lines)
                })
            save_task_to_db(task_id)

            idx += 1

        if failed_files:
            raise Exception(f"有 {len(failed_files)} 个文件重命名失败，请展开查看")
        rename_success = True

    except Exception as e:
        _safe_update(task_id, {'status': 'error', 'msg': f'❌ 失败: {e}'})
        save_task_to_db(task_id)
    finally:
        if rename_success:
            _safe_update(task_id, {'percent': 100, 'status': 'done', 'msg': '✅ 批量重命名完成'})
            save_task_to_db(task_id)
            _fetch_and_cache_ia_files(identifier, force=True)
        if slot_held:
            try: CONCURRENCY_LIMITER.release()
            except: pass