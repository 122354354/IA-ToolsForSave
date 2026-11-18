# netdisk_workers_delete.py
# 删除文件工作线程 / 删除进度更新
# 同步修复（与 netdisk_workers_upload.py / _append.py / _download.py 对齐）：
#   Bug 2 类 - UPLOAD_TASKS[task_id] 判空
#   Bug 3 类 - _worker_token 闭包比较
#   Bug 14 类 - _del_sub_task 暂停/sleep 循环 token 检查
#   Bug 17 类 - acquire 后重新检查 token
#   Bug 21 类 - acquire 后只切 deleting 条件为 "仍是 waiting"
#   【Bug 22 - 5/7】主循环 waiting 分支的 acquire 传 should_stop 回调

import uuid
import time
from concurrent.futures import ThreadPoolExecutor

from netdisk_state import (
    UPLOAD_TASKS, DB_LOCK,
    get_db, cfg_get
)
from netdisk_utils import (
    CONCURRENCY_LIMITER,
    _fetch_and_cache_ia_files, _delete_ia_file,
    save_task_to_db,
)


def _safe_update(task_id, patch):
    """【Bug 2 类修复】安全更新任务状态，任务被删除时静默跳过"""
    t = UPLOAD_TASKS.get(task_id)
    if t is None:
        return False
    t.update(patch)
    return True


def _update_parent_delete_progress(task_id):
    with DB_LOCK:
        conn = get_db()
        rows = conn.execute("SELECT status FROM sub_tasks WHERE task_id=?", (task_id,)).fetchall()
        conn.close()
    if not rows: return
    total_cnt = len(rows)
    done_cnt = sum(1 for r in rows if r[0] == 'done')
    has_err = any(r[0] == 'error' for r in rows)
    pct = round((done_cnt / total_cnt) * 100, 1) if total_cnt > 0 else 0
    # 【Bug 2 类修复】.get() 判空，防 in 检查后 [] 访问的竞态
    _t = UPLOAD_TASKS.get(task_id)
    if _t is not None:
        _t.update({
            'percent': pct,
            'current': str(done_cnt),
            'total': str(total_cnt),
            'files_uploaded': done_cnt,
            'total_files_count': total_cnt,
        })
        if has_err:
            _t['msg'] = '⚠️ 部分文件删除失败，展开查看'
        save_task_to_db(task_id)


def delete_files_worker(task_id, identifier, filenames, db_id):
    if not filenames:
        _safe_update(task_id, {'status': 'error', 'msg': '❌ 没有可删除的文件'})
        save_task_to_db(task_id)
        return

    # 【Bug 2 类修复】任务可能在 API 侧被删除后 worker 才真正开始
    if task_id not in UPLOAD_TASKS:
        return

    title_str = filenames[0] if len(filenames) == 1 else f'{identifier} ({len(filenames)} 个文件)'

    # 【Bug 3 类修复】生成本 worker 唯一的 token
    my_worker_token = uuid.uuid4().hex

    # 【Bug 16 类修复】先 .get() 持有引用
    _existing_at_entry = UPLOAD_TASKS.get(task_id)
    _created_at_val = _existing_at_entry.get('created_at', time.time()) if _existing_at_entry else time.time()

    _safe_update(task_id, {
        'status': 'waiting', 'msg': '等待并发槽位...',
        'task_type': 'delete',
        'title': f'[批量删除] {title_str}',
        'percent': 0, 'speed': '0.0',
        'current': '0', 'total': str(len(filenames)),
        'total_files_count': len(filenames),
        'total_size_bytes': 0,
        'files_uploaded': 0, 'uploaded_bytes': 0,
        'created_at': _created_at_val,
        'raw_line': f'准备删除 {len(filenames)} 个文件...',
        '_worker_token': my_worker_token
    })
    save_task_to_db(task_id)

    with DB_LOCK:
        conn = get_db()
        existing = {r[0] for r in conn.execute("SELECT file_path FROM sub_tasks WHERE task_id=?", (task_id,)).fetchall()}
        for fn in filenames:
            if fn not in existing:
                conn.execute(
                    "INSERT OR REPLACE INTO sub_tasks (task_id, file_path, file_size, status, uploaded_bytes) VALUES (?, ?, 0, 'waiting', 0)",
                    (task_id, fn)
                )
        conn.commit()

        tasks_to_do = [r[0] for r in conn.execute("SELECT file_path FROM sub_tasks WHERE task_id=? AND status NOT IN ('done', 'skipped')", (task_id,)).fetchall()]
        conn.close()

    if not tasks_to_do:
        _safe_update(task_id, {'status': 'done', 'msg': f'✅ 已删除 {len(filenames)} 个文件', 'percent': 100})
        save_task_to_db(task_id)
        return

    slot_held = False
    try:
        if not UPLOAD_TASKS.get(task_id): return
        CONCURRENCY_LIMITER.acquire(timeout=86400)
        slot_held = True

        conc_limit = max(1, min(int(cfg_get('delete_concurrency', 1)), 10))
        _safe_update(task_id, {'status': 'deleting', 'msg': f'开始删除 (并发 {conc_limit})...'})
        save_task_to_db(task_id)

        def _del_sub_task(fn):
            # 【Bug 3/14 类修复】暂停循环检查 token
            while True:
                _t = UPLOAD_TASKS.get(task_id)
                if _t is None:
                    return
                if _t.get('_worker_token') != my_worker_token:
                    return
                if _t.get('status') != 'paused':
                    break
                time.sleep(0.5)

            with DB_LOCK:
                conn = get_db()
                conn.execute("UPDATE sub_tasks SET status='uploading' WHERE task_id=? AND file_path=?", (task_id, fn))
                conn.commit(); conn.close()

            ok = _delete_ia_file(identifier, fn)

            with DB_LOCK:
                conn = get_db()
                if ok:
                    conn.execute("UPDATE sub_tasks SET status='done', uploaded_bytes=file_size WHERE task_id=? AND file_path=?", (task_id, fn))
                else:
                    conn.execute("UPDATE sub_tasks SET status='error', error_msg='删除失败（IA 返回错误）' WHERE task_id=? AND file_path=?", (task_id, fn))
                conn.commit(); conn.close()

            # 【Bug 14 类修复】逐文件删除间隔循环也检查 token
            for _ in range(int(cfg_get('delete_file_sleep', 2.0))):
                _t = UPLOAD_TASKS.get(task_id)
                if _t is None:
                    return
                if _t.get('_worker_token') != my_worker_token:
                    return
                if _t.get('status') == 'paused':
                    break
                time.sleep(1)

        executor = ThreadPoolExecutor(max_workers=conc_limit)
        futures = {executor.submit(_del_sub_task, fn): fn for fn in tasks_to_do}

        while True:
            # 【Bug 2 类修复】主循环判空 + token 检查
            _t = UPLOAD_TASKS.get(task_id)
            if _t is None:
                executor.shutdown(wait=False)
                return
            if _t.get('_worker_token') != my_worker_token:
                executor.shutdown(wait=False)
                return

            status_now = _t.get('status')
            if status_now == 'paused':
                if slot_held:
                    CONCURRENCY_LIMITER.release()
                    slot_held = False
                    _safe_update(task_id, {'msg': '⏸️ 已暂停（槽位已释放）'})
                    save_task_to_db(task_id)
                time.sleep(0.5)
                continue
            else:
                if not slot_held:
                    _safe_update(task_id, {'status': 'waiting', 'msg': '▶️ 恢复中，等待并发槽位...'})
                    save_task_to_db(task_id)

                    # 【Bug 22 - 5/7】should_stop 回调：任务被删/替换时立即中止等待
                    def _should_stop_waiting():
                        _tt = UPLOAD_TASKS.get(task_id)
                        return _tt is None or _tt.get('_worker_token') != my_worker_token
                    try:
                        CONCURRENCY_LIMITER.acquire(timeout=86400, should_stop=_should_stop_waiting)
                        slot_held = True
                        # 【Bug 17 类修复】acquire 后重新检查 token
                        _t2 = UPLOAD_TASKS.get(task_id)
                        if _t2 is None or _t2.get('_worker_token') != my_worker_token:
                            CONCURRENCY_LIMITER.release()
                            slot_held = False
                            executor.shutdown(wait=False)
                            return
                        # 【Bug 21 类修复】只切 deleting 条件为 "仍是 waiting"
                        if _t2.get('status') == 'waiting':
                            _t2['status'] = 'deleting'
                            _t2['msg'] = '▶️ 已恢复，继续删除...'
                            save_task_to_db(task_id)
                        else:
                            CONCURRENCY_LIMITER.release()
                            slot_held = False
                            continue
                    except:
                        # 【Bug 22 - 5/7】acquire 被取消/超时：安全退出
                        if slot_held:
                            CONCURRENCY_LIMITER.release()
                            slot_held = False
                        executor.shutdown(wait=False)
                        return

            all_done = all(f.done() for f in futures.keys())
            _update_parent_delete_progress(task_id)
            if all_done: break
            time.sleep(1)

        executor.shutdown(wait=True)

        with DB_LOCK:
            conn = get_db()
            conn.execute("DELETE FROM file_cache WHERE identifier=?", (identifier,))
            conn.execute("UPDATE items SET status='done', error_msg=NULL WHERE id=?", (db_id,))
            has_err = conn.execute("SELECT COUNT(*) FROM sub_tasks WHERE task_id=? AND status='error'", (task_id,)).fetchone()[0] > 0
            conn.commit(); conn.close()

        _fetch_and_cache_ia_files(identifier, force=True)

        if has_err:
            _safe_update(task_id, {'status': 'error', 'msg': '⚠️ 部分文件删除失败，展开队列查看详情'})
        else:
            _safe_update(task_id, {'status': 'done', 'msg': f'✅ 已删除 {len(filenames)} 个文件', 'percent': 100})
        save_task_to_db(task_id)

    except Exception as e:
        _safe_update(task_id, {'status': 'error', 'msg': f'❌ 失败: {e}'})
        save_task_to_db(task_id)
    finally:
        if slot_held:
            try: CONCURRENCY_LIMITER.release()
            except: pass