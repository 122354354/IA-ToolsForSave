# netdisk_workers_pull.py
# 从 IA 全量拉取条目工作线程
# 同步修复：
#   Bug 2 类 - UPLOAD_TASKS[task_id] 判空
#   Bug 3 类 - _worker_token 闭包比较
#   Bug 14 类 - 暂停循环 / page_sleep / ia_get 阻塞后 token 检查
#   Bug 17 类 - acquire 后重新检查 token
#   Bug 21 类 - acquire 后只切 downloading 条件为 "仍是 waiting"
#   Bug 35 - for 循环内 while 分离 paused / waiting 语义
#   【Bug 22 - 8/7】首次 acquire + for 循环内 acquire 传 should_stop 回调
#   【Bug 10 - 4/4】raw_line 从字符串累积改为 deque + _append_log 单行截断
#   【改进点 80 - 6/6】删除本地 _append_log 定义，改为 from netdisk_utils import _append_log

import time
import uuid
import sqlite3
import collections

from netdisk_state import (
    UPLOAD_TASKS, DB_LOCK,
    get_db, get_config, cfg_get,
)
from netdisk_utils import (
    CONCURRENCY_LIMITER, ia_get,
    _append_log,  # 【改进点 80 - 6/6】改为从 netdisk_utils 导入
    save_task_to_db,
)


def _safe_update(task_id, patch):
    """【Bug 2 类修复】安全更新任务状态，任务被删除时静默跳过"""
    t = UPLOAD_TASKS.get(task_id)
    if t is None:
        return False
    t.update(patch)
    return True


def pull_from_ia_worker(task_id, owner, current_page=1):
    cfg = get_config()
    page_size = cfg_get('pull_page_size', 100)
    max_pages = cfg_get('pull_max_pages', 50)
    page_sleep = cfg_get('pull_page_sleep', 0.5)

    # 【Bug 2 类修复】任务可能在 API 侧被删除后 worker 才真正开始
    if task_id not in UPLOAD_TASKS:
        return

    # 【Bug 3 类修复】生成本 worker 唯一的 token
    my_worker_token = uuid.uuid4().hex

    # 【Bug 22 - 8/7】should_stop 回调
    def _should_stop_waiting():
        _tt = UPLOAD_TASKS.get(task_id)
        return _tt is None or _tt.get('_worker_token') != my_worker_token

    # 【Bug 16 类修复】先 .get() 持有引用
    _existing_at_entry = UPLOAD_TASKS.get(task_id)
    _created_at_val = _existing_at_entry.get('created_at', time.time()) if _existing_at_entry else time.time()

    # 【Bug 10 - 4/4】raw_line 改为 deque + 初始行
    history_lines = collections.deque(maxlen=max(10, cfg_get('raw_line_max_lines', 500)))
    _append_log(history_lines, '正在初始化...')

    _safe_update(task_id, {
        'status': 'waiting', 'msg': '准备拉取...', 'task_type': 'pull',
        'title': f'[全量拉取] {owner}',
        'percent': 0, 'speed': '0.0', 'current': '0', 'total': '?',
        'raw_line': "\n".join(history_lines), 'created_at': _created_at_val,
        '_worker_token': my_worker_token
    })
    save_task_to_db(task_id)

    slot_held = False
    try:
        if not UPLOAD_TASKS.get(task_id): return

        # 【Bug 22 - 8/7】首次 acquire 传 should_stop
        try:
            CONCURRENCY_LIMITER.acquire(timeout=86400, should_stop=_should_stop_waiting)
            slot_held = True
        except:
            if slot_held:
                CONCURRENCY_LIMITER.release()
                slot_held = False
            return

        # 【Bug 17 类修复】acquire 后重新检查 token
        _t = UPLOAD_TASKS.get(task_id)
        if _t is None or _t.get('_worker_token') != my_worker_token:
            CONCURRENCY_LIMITER.release()
            slot_held = False
            return
        # 【Bug 21 类修复】只切 downloading 条件为 "仍是 waiting"
        if _t.get('status') == 'waiting':
            _t['status'] = 'downloading'
            _t['msg'] = '正在从 IA 拉取...'
            save_task_to_db(task_id)
        else:
            CONCURRENCY_LIMITER.release()
            slot_held = False

        fl = "&".join(f"fl[]={k}" for k in ["identifier","title","creator","description","date","mediatype","collection","language","licenseurl","subject","x-archive-meta-date"])
        all_docs, full_success = [], True

        for page in range(current_page, max_pages + 1):
            # 【Bug 35 修复】分离 paused / waiting 语义
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
                        _t['msg'] = '⏸️ 已暂停（槽位已释放）'
                        save_task_to_db(task_id)
                    time.sleep(1)
                    continue

                if status_now == 'waiting':
                    if not slot_held:
                        # 【Bug 22 - 8/7】for 循环内 acquire 传 should_stop
                        try:
                            CONCURRENCY_LIMITER.acquire(timeout=86400, should_stop=_should_stop_waiting)
                            slot_held = True
                        except:
                            if slot_held:
                                CONCURRENCY_LIMITER.release()
                                slot_held = False
                            return
                        _t2 = UPLOAD_TASKS.get(task_id)
                        if _t2 is None or _t2.get('_worker_token') != my_worker_token:
                            CONCURRENCY_LIMITER.release()
                            slot_held = False
                            return
                        # 【Bug 21 类修复】只有仍是 waiting 才切 downloading
                        if _t2.get('status') == 'waiting':
                            _t2['status'] = 'downloading'
                            _t2['msg'] = '▶️ 已恢复，继续拉取...'
                            save_task_to_db(task_id)
                        else:
                            CONCURRENCY_LIMITER.release()
                            slot_held = False
                            continue
                    break

                break

            url = f"https://archive.org/advancedsearch.php?q=uploader:{owner}&{fl}&rows={page_size}&page={page}&output=json"
            resp = ia_get(url, timeout=15)

            # 【Bug 3 类修复】ia_get 阻塞期间可能被删除/替换
            _t = UPLOAD_TASKS.get(task_id)
            if _t is None:
                return
            if _t.get('_worker_token') != my_worker_token:
                return

            if not resp or resp.status_code != 200:
                full_success = False
                _append_log(history_lines, f"❌ 第 {page} 页拉取失败，停止")
                _safe_update(task_id, {'raw_line': "\n".join(history_lines)})
                save_task_to_db(task_id)
                break
            try:
                docs = resp.json().get('response', {}).get('docs', [])
            except:
                full_success = False
                break
            if not docs: break

            all_docs.extend(docs)
            if len(docs) < page_size: break

            _append_log(history_lines, f"拉取了第 {page} 页 ({len(docs)} 个条目)")
            _safe_update(task_id, {
                'percent': round((page / max_pages) * 100, 1) if page < max_pages else 99,
                'current': str(len(all_docs)),
                'msg': f'已拉取第 {page} 页，共 {len(all_docs)} 个条目...',
                'raw_line': "\n".join(history_lines)
            })
            save_task_to_db(task_id)

            # 【Bug 14 类修复】page_sleep 分段 0.1 秒 sleep，每段检查 token
            if page_sleep > 0:
                sleep_steps = max(1, int(page_sleep / 0.1))
                step_duration = page_sleep / sleep_steps
                for _ in range(sleep_steps):
                    _t = UPLOAD_TASKS.get(task_id)
                    if _t is None:
                        return
                    if _t.get('_worker_token') != my_worker_token:
                        return
                    time.sleep(step_duration)

        if not all_docs and not full_success:
            raise Exception("未能拉取到任何条目，请检查网络或账号")

        _safe_update(task_id, {'msg': '正在写入本地数据库...'})
        save_task_to_db(task_id)

        with DB_LOCK:
            conn = get_db()
            existing_ids = {r[0] for r in conn.execute("SELECT identifier FROM items WHERE owner=?", (owner,)).fetchall()}
            if full_success and all_docs and len(all_docs) < max_pages * page_size:
                cloud_ids = {d.get('identifier') for d in all_docs}
                missing = [i for i in existing_ids if i not in cloud_ids]
                if missing:
                    conn.execute(f"UPDATE items SET status='cloud_missing' WHERE owner=? AND identifier IN ({','.join(['?']*len(missing))})", [owner] + missing)

            new_count = 0
            def get_val(val):
                if not val: return ""
                if isinstance(val, list): return ", ".join(map(str, val))
                return str(val)

            for doc in all_docs:
                ident = doc.get('identifier')
                if not ident or ident in existing_ids: continue
                try:
                    conn.execute('''INSERT INTO items (owner, folder_id, identifier, title, creator, description, date, mediatype, collection, language, license, tags, status, item_url, custom_date) VALUES (?, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'done', ?, ?)''', (owner, ident, get_val(doc.get('title', ident)), get_val(doc.get('creator')), get_val(doc.get('description')), get_val(doc.get('date')), get_val(doc.get('mediatype')) or 'software', get_val(doc.get('collection')) or 'opensource', get_val(doc.get('language')), get_val(doc.get('licenseurl')), get_val(doc.get('subject')), f"https://archive.org/details/{ident}", get_val(doc.get('x-archive-meta-date'))))
                    new_count += 1
                except sqlite3.IntegrityError: pass
            conn.commit(); conn.close()

        _append_log(history_lines, f"✅ 写入完成，新增 {new_count} 个条目。")
        _t = UPLOAD_TASKS.get(task_id)
        if _t is not None:
            _t.update({
                'status': 'done', 'msg': f'✅ 拉取完成！新增 {new_count} 个条目',
                'percent': 100, 'raw_line': "\n".join(history_lines)
            })
        save_task_to_db(task_id)

    except Exception as e:
        _safe_update(task_id, {'status': 'error', 'msg': f'❌ 失败: {e}'})
        save_task_to_db(task_id)
    finally:
        if slot_held:
            try: CONCURRENCY_LIMITER.release()
            except: pass