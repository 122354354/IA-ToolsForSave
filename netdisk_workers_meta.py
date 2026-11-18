# netdisk_workers_meta.py
# 推送元数据工作线程
# 同步修复：
#   Bug 2 类 - UPLOAD_TASKS[task_id] 判空
#   Bug 3 类 - _worker_token 闭包比较
#   Bug 14 类 - 暂停/风控/普通重试 3 处等待循环 token 检查
#   Bug 17 类 - acquire 后重新检查 token
#   Bug 21 类 - acquire 后只切 uploading 条件为 "仍是 waiting"
#   【Bug 31】暂停等待循环条件改为 != 'paused'，避免 paused→waiting 卡死
#   【Bug 32】attempt 计数移到推送前，避免 interrupted + 风控交替时死循环
#   【Bug 30】while 循环结束仍未成功时 raise，让外层 except 标记 error
#   【Bug 22 - 7/7】首次 acquire + 重试循环内 acquire 传 should_stop 回调
#
# 【⏱️ 延迟与超时精细控制 - 2026-11 第 12/20 步】
# 接入 worker_max_retries（默认 5，0-20）→ max(1, _get_worker_max_retries()) 兜底
#
# 【🔴 R108 修复 - 2026-11（本轮）】
#   问题：用户主动暂停导致 attempt 达到 max_attempts 时，任务被误标 error。
#   触发路径：
#     1. 用户点推送 → attempt=1 → 推送中用户暂停 → interrupted=True → continue
#     2. 外层 while 条件：attempt(1) >= max_attempts(1) → 退出 while
#     3. 执行 Bug 30 的 raise Exception("推送元数据失败：1 次重试后仍未成功")
#     4. 外层 except → 标 error（但用户从未真正失败，是主动暂停）
#   用户填 worker_max_retries=0 时概率最高（1 次中断就 error）。
#   修复：while 循环结束后，检查当前 status：
#     - 'paused' → 保持 paused（推送未完成，用户可点继续恢复）
#     - 'waiting' / 'done' → 保持现状（用户操作的其他状态）
#     - 其他 → 执行原 raise（真正的失败）
#   注意：raise 前检查不影响正常失败（不暂停直接 5 次失败时 status='uploading'，仍 raise）
#
# 【📝 保留记录 - 2026-11，低优先，以后修】
#   R108 已修 ✅
#   注：append/rename worker 也有类似「用户暂停后可能误标 error」的潜在问题，
#      但它们的暂停/中断路径不同，本轮只修 meta（R299 记录）

import time
import uuid

import internetarchive as ia

from netdisk_state import (
    UPLOAD_TASKS, DB_LOCK,
    get_db, cfg_get,
)
from netdisk_utils import (
    CONCURRENCY_LIMITER, IA_LIMITER,
    ensure_ia_config, save_task_to_db,
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
      - 20 → 20 次尝试 = 1 次初始 + 19 次重试
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


def push_metadata_worker(task_id, db_id):
    # 【Bug 2 类修复】任务可能在 API 侧被删除后 worker 才真正开始
    if task_id not in UPLOAD_TASKS:
        return

    if not ensure_ia_config():
        _safe_update(task_id, {'status': 'error', 'msg': '❌ 配置文件生成失败'})
        save_task_to_db(task_id)
        return

    # 【Bug 3 类修复】生成本 worker 唯一的 token
    my_worker_token = uuid.uuid4().hex

    # 【Bug 22 - 7/7】should_stop 回调：任务被删/替换时立即中止等待
    def _should_stop_waiting():
        _tt = UPLOAD_TASKS.get(task_id)
        return _tt is None or _tt.get('_worker_token') != my_worker_token

    slot_held = False
    try:
        conn = get_db()
        row = conn.execute("SELECT identifier, title, creator, description, date, mediatype, collection, language, license, tags, custom_date FROM items WHERE id=?", (db_id,)).fetchone()
        conn.close()
        if not row:
            raise Exception("条目不存在")

        identifier, title, creator, desc, date, media, coll, lang, lic, tags, custom_date = row

        _safe_update(task_id, {
            'status': 'waiting', 'msg': '等待并发槽位...',
            'task_type': 'push_meta',
            'title': f"[更新元数据] {title}",
            '_worker_token': my_worker_token
        })
        save_task_to_db(task_id)

        # 【Bug 22 - 7/7】首次 acquire 传 should_stop
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
        # 【Bug 21 类修复】只切 uploading 条件为 "仍是 waiting"
        if _t.get('status') == 'waiting':
            _t['status'] = 'uploading'
            _t['msg'] = '正在向 IA 推送元数据...'
            save_task_to_db(task_id)
        else:
            CONCURRENCY_LIMITER.release()
            slot_held = False
            # 用户 acquire 期间已暂停，回到下面的重试循环开头重新处理

        meta_dict = {}
        if title: meta_dict['title'] = title
        if media: meta_dict['mediatype'] = media
        if coll: meta_dict['collection'] = coll
        if creator: meta_dict['creator'] = creator
        if desc: meta_dict['description'] = desc
        if date: meta_dict['date'] = date
        if custom_date: meta_dict['x-archive-meta-date'] = custom_date
        if lang: meta_dict['language'] = lang
        if lic: meta_dict['licenseurl'] = lic
        if tags: meta_dict['subject'] = tags

        # 【⏱️ 延迟与超时精细控制】max_attempts = 5 → max(1, _get_worker_max_retries())
        #   max(1, ...) 兜底防用户填 0 时 while 循环完全不执行
        max_attempts = max(1, _get_worker_max_retries())
        attempt = 0
        while attempt < max_attempts:
            # 【Bug 31 修复】暂停等待循环：只有 paused 才等，waiting 表示用户已恢复
            while True:
                _t = UPLOAD_TASKS.get(task_id)
                if _t is None:
                    return
                if _t.get('_worker_token') != my_worker_token:
                    return
                if _t.get('status') != 'paused':
                    break
                if slot_held:
                    CONCURRENCY_LIMITER.release()
                    slot_held = False
                time.sleep(1)

            # 恢复时重新 acquire
            if not slot_held:
                # 【Bug 22 - 7/7】重试循环内 acquire 传 should_stop
                try:
                    CONCURRENCY_LIMITER.acquire(timeout=86400, should_stop=_should_stop_waiting)
                    slot_held = True
                except:
                    if slot_held:
                        CONCURRENCY_LIMITER.release()
                        slot_held = False
                    return
                # 【Bug 17 类修复】acquire 后重新检查 token
                _t2 = UPLOAD_TASKS.get(task_id)
                if _t2 is None or _t2.get('_worker_token') != my_worker_token:
                    CONCURRENCY_LIMITER.release()
                    slot_held = False
                    return
                # 【Bug 21 类修复】只切 uploading 条件为 "仍是 waiting"
                if _t2.get('status') == 'waiting':
                    _t2['status'] = 'uploading'
                    _t2['msg'] = '▶️ 已恢复，继续推送...'
                    save_task_to_db(task_id)
                else:
                    # 用户又暂停了，回到 while 开头重新处理
                    CONCURRENCY_LIMITER.release()
                    slot_held = False
                    continue

            # 【Bug 32 修复】推送前增加 attempt，确保 eventually 结束（防 interrupted 死循环）
            attempt += 1

            try:
                IA_LIMITER.acquire()
                # 【Bug 3 类修复】IA_LIMITER.acquire() 阻塞期间可能被替换/删除，推送前再检查
                _t3 = UPLOAD_TASKS.get(task_id)
                if _t3 is None or _t3.get('_worker_token') != my_worker_token:
                    return

                ia.modify_metadata(identifier, metadata=meta_dict, verbose=False)

                with DB_LOCK:
                    conn = get_db(); conn.execute("UPDATE items SET status='done', error_msg=NULL WHERE id=?", (db_id,)); conn.commit(); conn.close()
                _safe_update(task_id, {'status': 'done', 'msg': '✅ 元数据更新成功', 'percent': 100})
                save_task_to_db(task_id)
                return
            except Exception as e:
                err_msg = str(e).lower()
                is_rate_limit = any(x in err_msg for x in ["reduce your request rate", "too many requests", "403", "429", "503"])
                if is_rate_limit:
                    _safe_update(task_id, {'msg': f'⚠️ 触发IA风控，60秒后重试 ({attempt}/{max_attempts})...'})
                    save_task_to_db(task_id)
                    interrupted = False
                    for _ in range(60):
                        _t = UPLOAD_TASKS.get(task_id)
                        if _t is None:
                            return
                        if _t.get('_worker_token') != my_worker_token:
                            return
                        if _t.get('status') in ('paused', 'waiting'):
                            interrupted = True
                            break
                        time.sleep(1)
                    if interrupted:
                        continue  # attempt 已增加，回到 while 开头处理暂停
                else:
                    if attempt >= max_attempts:
                        raise e  # 改为 >=
                    interrupted = False
                    for _ in range(5):
                        _t = UPLOAD_TASKS.get(task_id)
                        if _t is None:
                            return
                        if _t.get('_worker_token') != my_worker_token:
                            return
                        if _t.get('status') in ('paused', 'waiting'):
                            interrupted = True
                            break
                        time.sleep(1)
                    if interrupted:
                        continue  # attempt 已增加

        # 【R108 修复 - 2026-11】while 循环结束后检查是否用户主动暂停导致退出
        #   原 Bug 30 修复直接 raise，会在以下场景误标 error：
        #     - 用户推送中暂停 → interrupted=True → continue
        #     - 外层 while 条件 attempt >= max_attempts → 退出 while
        #     - 执行 Bug 30 的 raise → 外层 except 标 error（但用户从未真正失败）
        #   修复：检查 status：
        #     - 'paused' → 保持 paused（推送未完成，用户可点继续恢复）
        #     - 'waiting' / 'done' → 保持现状（用户操作的其他状态）
        #     - 其他（uploading / error 等）→ 执行原 raise（真正的失败）
        _t_final = UPLOAD_TASKS.get(task_id)
        if _t_final is not None:
            _final_status = _t_final.get('status')
            if _final_status == 'paused':
                # 用户主动暂停 → 保持 paused 状态，不标 error
                _safe_update(task_id, {
                    'status': 'paused',
                    'msg': '⏸️ 已暂停（推送未完成，点击「继续」可恢复）'
                })
                save_task_to_db(task_id)
                return
            if _final_status in ('waiting', 'done'):
                # 用户操作的其他状态 → 保持现状
                return
            # 注：'uploading' / 'error' 等 → 继续执行下方 raise

        # 【Bug 30 修复】while 循环结束仍未成功，raise 让外层 except 标记 error
        raise Exception(f"推送元数据失败：{max_attempts} 次重试后仍未成功")

    except Exception as e:
        _safe_update(task_id, {'status': 'error', 'msg': f'❌ 失败: {e}'})
        save_task_to_db(task_id)
        try:
            with DB_LOCK:
                conn = get_db(); conn.execute("UPDATE items SET status='error', error_msg=? WHERE id=?", (str(e), db_id)); conn.commit(); conn.close()
        except: pass
    finally:
        if slot_held:
            try: CONCURRENCY_LIMITER.release()
            except: pass