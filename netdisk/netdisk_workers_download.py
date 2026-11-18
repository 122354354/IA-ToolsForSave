# netdisk_workers_download.py
# 下载单文件核心 / 下载文件夹 / 下载进度更新
# 同步修复（与 netdisk_workers_upload.py / _append.py 对齐）：
#   Bug 2 类 - UPLOAD_TASKS[task_id] 判空
#   Bug 3 类 - _worker_token 闭包比较
#   Bug 14 类 - 主循环 token 检查
#   Bug 17 类 - acquire 后重新检查 token
#   Bug 21 类 - acquire 后只切 downloading 条件为 "仍是 waiting"
#   Bug 19 - _do_download_one_file 新增 expected_worker_token 参数
#   【改进点 44】_update_parent_download_progress 里 new_status 判定加 'waiting'
#   【Bug 22 - 4/7】主循环 waiting 分支的 acquire 传 should_stop 回调
#
# 【⏱️ 延迟与超时精细控制 - 2026-11 第 7/20 步】
# 接入 4 个新配置项（全部 cfg_get 读取 + 5 秒 TTL 缓存）：
#   1. download_timeout_connect    (默认 15，5-120 秒)   → requests.get 的 connect timeout
#   2. download_timeout_read       (默认 15，5-300 秒)   → requests.get 的 read timeout
#   3. download_progress_interval  (默认 1.0，0.1-10 秒) → 进度写 DB 频率
#   4. stream_chunk_size           (默认 65536，4096-1MB) → iter_content 每次读取字节数
#   5. download_retry_sleep        (默认 5.0，0-60 秒)   → 重试前总等待时长
#
# 【🔴 核心原则：默认行为与原代码完全一致，正常下载路径无任何新增 sleep】
#
# 【🔴 BUG 修复 - 2026-11】download_retry_sleep 语义从「每次 sleep 时长 × 5」
#   改为「总等待时长」
#
# 【🔴 R413 修复 - 2026-11（高危 - 并发下载同一文件导致文件损坏）】
#   问题：max_concurrent > 1 时，两个 download_folder_worker 并发下同一文件：
#     - 两个 worker 都检测到 .part 不存在（initial_size=0）
#       → 都 mode='wb' → 交替写同一 .part → 文件损坏
#     - 或第二个检测到 .part 存在 → mode='ab' append → 文件内容重复
#     - 两个都 os.replace(temp_path, target_path) → 一个成功一个 FileNotFoundError
#   修复：引入模块级 per-target_path 锁 _DOWNLOAD_PATH_LOCKS
#     1. _do_download_one_file 开头获取 target_path 对应的锁
#        （0.5 秒轮询 + 检查暂停/删除，响应 ≤ 0.5 秒）
#     2. 拿到锁后重新检测 target_path 是否已存在（前一个 worker 可能已完成 → 秒过）
#     3. 所有 return 走 try/finally 释放锁
#     4. 不同路径的锁互不影响 → 保持 concurrency 优势（不同文件并行下载）
#     5. 锁永不清理（每个 Lock < 200 字节，10000 个下载 2MB）
#
# 【🔴 R420 修复 - 2026-11（低危 - R413 等待期间 UX 改进 - 本轮）】
#   问题：R413 修复后，path_lock.acquire(timeout=0.5) 等待循环只检查暂停/删除，
#        不更新 raw_line。用户快速点两次下载同一文件时，第二个 task 在等待第一个
#        完成（可能 10 分钟），期间 raw_line 一直是"⏳ 正在下载: xxx (0 B / 100.0 MB)"
#        不动 → 用户误以为卡死。
#   修复：等待循环里每 5 秒更新一次 raw_line 为
#        "⏳ 等待前一个任务完成下载: {filename}（已等待 N 秒）" + save_task_to_db
#        （try/except 兜底，DB 失败不影响下载）。
#   为什么每 5 秒：0.5 秒更新太频繁（写 DB 压力），5 秒足够让用户看到进度。
#   为什么用 save_task_to_db：raw_line 需要持久化（用户刷新页面后还能看到）。
#   为什么锁释放后不用清理：外层 download_folder_worker 调用
#        _update_parent_download_progress 会覆盖 raw_line 为正常下载 msg。
#   为什么 save_task_to_db 加 try/except：DB 异常时不能中断等待循环（否则
#        锁永远不释放，其他 worker 也卡死）。
#
# 【📝 保留记录 - 2026-11，低优先，以后修】
#   R416：_DOWNLOAD_PATH_LOCKS 永不清理，每个 Lock < 200 字节，10000 个 2MB
#   R417：_do_download_one_file 里的"等待后重新检测"直接用 os.path.getsize，
#        若此时文件被外部删除（用户手动删）→ FileNotFoundError → 被 except 兜底
#   R418：path_lock 只防同一 target_path。两个 task 下同一 identifier 的不同文件
#        （文件名不同）→ target_path 不同 → 不冲突。正确。
#   R419：前端"下载"按钮没有 btn.disabled（R413 未能覆盖）。
#        当前由后端 per-path 锁兜底（并发请求会串行化 + 秒过），
#        但前端体验可优化。记录待前端统一修时改。
#   R421：B 等待 A 时间无上限。但 A 有 download_timeout_connect/read + 5 次重试
#        （总时长有限），B 最多等 A 的生命周期上限 → 不会无限等
#   R422：B 等待后 size_hint 不匹配时重新下载 200MB（浪费流量），保守设计
#   R423：并发下载同一文件时 A 失败 + B 成功，用户看到 A 的"下载失败"可能困惑
#        （可加 msg 说明"该文件已被另一任务完成"）
#
# 【📝 同源问题记录 - 其他文件，后续统一修】
#   - R414（中危）：netdisk_routes_views_upload.py 的 upload() / execute_all()
#     和 netdisk_routes_api_upload.py 的 api_batch_append 未做"同 identifier 活跃任务"检查
#     → 已修复（R414 第 1 / 2 个 + R428）
#   - R415（低危）：netdisk_routes_api_tasks.py 的 api_tasks_clean 未分批
#     → 已修复

import os
import time
import threading
import logging
import re
import requests
import uuid
import collections

import netdisk_state_locks
from netdisk_state import (
    UPLOAD_TASKS, DB_LOCK, TEMP_UPLOAD_DIR,
    get_db, get_config, cfg_get
)
from netdisk_utils import (
    CONCURRENCY_LIMITER,
    save_task_to_db, cleanup_temp_dir, format_bytes,
    get_ssl_verify, get_req_proxies,
)


# ========== 【R413 修复 - 2026-11】下载路径级锁（防并发下同一文件损坏） ==========
# 结构：{target_path: threading.Lock}
# 用途：max_concurrent > 1 时两个 worker 下同一文件 → 交替写同一 .part → 文件损坏
# 为什么永不清理：每个 Lock < 200 字节，10000 个下载 2MB（见 R416 记录）
_DOWNLOAD_PATH_LOCKS = {}
_DOWNLOAD_PATH_LOCKS_GUARD = threading.Lock()


def _get_download_path_lock(target_path):
    """获取指定 target_path 的 per-path 锁（线程安全创建）。"""
    with _DOWNLOAD_PATH_LOCKS_GUARD:
        lk = _DOWNLOAD_PATH_LOCKS.get(target_path)
        if lk is None:
            lk = threading.Lock()
            _DOWNLOAD_PATH_LOCKS[target_path] = lk
        return lk


# ========== 【⏱️ 延迟与超时精细控制】4 个带 5 秒 TTL 缓存的读取函数 ==========
_DOWNLOAD_TIMEOUT_CACHE = {'value': (15, 15), 'at': 0.0}
_DOWNLOAD_PROGRESS_INTERVAL_CACHE = {'value': 1.0, 'at': 0.0}
_DOWNLOAD_RETRY_SLEEP_CACHE = {'value': 5.0, 'at': 0.0}
_STREAM_CHUNK_SIZE_CACHE = {'value': 65536, 'at': 0.0}
_CFG_CACHE_TTL = 5.0


def _get_download_timeout():
    """返回 (connect_timeout, read_timeout)，默认 (15, 15)，范围 connect 5-120 / read 5-300。"""
    now = time.time()
    if now - _DOWNLOAD_TIMEOUT_CACHE['at'] < _CFG_CACHE_TTL:
        return _DOWNLOAD_TIMEOUT_CACHE['value']
    try:
        c = max(5, min(int(cfg_get('download_timeout_connect', 15)), 120))
        r = max(5, min(int(cfg_get('download_timeout_read', 15)), 300))
    except Exception:
        c, r = 15, 15
    _DOWNLOAD_TIMEOUT_CACHE['value'] = (c, r)
    _DOWNLOAD_TIMEOUT_CACHE['at'] = now
    return (c, r)


def _get_download_progress_interval():
    """返回下载进度写 DB 频率（秒），默认 1.0，范围 0.1-10。"""
    now = time.time()
    if now - _DOWNLOAD_PROGRESS_INTERVAL_CACHE['at'] < _CFG_CACHE_TTL:
        return _DOWNLOAD_PROGRESS_INTERVAL_CACHE['value']
    try:
        v = max(0.1, min(float(cfg_get('download_progress_interval', 1.0)), 10.0))
    except Exception:
        v = 1.0
    _DOWNLOAD_PROGRESS_INTERVAL_CACHE['value'] = v
    _DOWNLOAD_PROGRESS_INTERVAL_CACHE['at'] = now
    return v


def _get_download_retry_sleep():
    """返回重试前的【总等待时长】（秒），默认 5.0，范围 0-60。

    语义说明（重要）：
      - 这是「两次重试之间的总等待时长」而非「每次 sleep 的时长」
      - 默认 5.0：与原来 `for _ in range(5): time.sleep(1)` 的 5 秒完全一致
      - 0：立即重试（仍先检查一次暂停，保证暂停能立即响应）
      - 60：60 秒总等待（不是 5 × 60 = 300 秒）
    实现：期间每 0.5 秒检查一次暂停/删除/cancelled，暂停响应时间 ≤ 0.5 秒
    """
    now = time.time()
    if now - _DOWNLOAD_RETRY_SLEEP_CACHE['at'] < _CFG_CACHE_TTL:
        return _DOWNLOAD_RETRY_SLEEP_CACHE['value']
    try:
        v = max(0.0, min(float(cfg_get('download_retry_sleep', 5.0)), 60.0))
    except Exception:
        v = 5.0
    _DOWNLOAD_RETRY_SLEEP_CACHE['value'] = v
    _DOWNLOAD_RETRY_SLEEP_CACHE['at'] = now
    return v


def _get_stream_chunk_size():
    """返回 iter_content 每次读取字节数，默认 65536，范围 4096-1048576。"""
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


def _safe_update(task_id, patch):
    """【Bug 2 类修复】安全更新任务状态，任务被删除时静默跳过"""
    t = UPLOAD_TASKS.get(task_id)
    if t is None:
        return False
    t.update(patch)
    return True


def _wait_retry_or_pause(parent_task_id, expected_worker_token, retry_sleep):
    """【⏱️ 延迟与超时精细控制】等待 retry_sleep 秒，期间每 0.5 秒检查暂停/删除/cancelled。

    返回 True = 等待完成可以重试
    返回 False = 应中止（任务被删除/替换/暂停/cancelled）

    关键点：
      - 即使 retry_sleep=0 也会先检查一次暂停（保证用户点暂停能立即响应）
      - 暂停响应时间 ≤ 0.5 秒（原代码是 1 秒）
      - retry_sleep=60 时执行 120 次检查（每秒 2 次，开销可忽略）
    """
    # 先检查一次（即使 retry_sleep=0）
    _t = UPLOAD_TASKS.get(parent_task_id)
    if _t is None:
        return False
    if expected_worker_token is not None and _t.get('_worker_token') != expected_worker_token:
        return False
    if _t.get('status') in ('paused', 'waiting', 'cancelled'):
        return False

    # 时间片等待
    waited = 0.0
    while waited < retry_sleep:
        step = min(0.5, retry_sleep - waited)
        time.sleep(step)
        waited += step
        _t = UPLOAD_TASKS.get(parent_task_id)
        if _t is None:
            return False
        if expected_worker_token is not None and _t.get('_worker_token') != expected_worker_token:
            return False
        if _t.get('status') in ('paused', 'waiting', 'cancelled'):
            return False
    return True


def _do_download_one_file(parent_task_id, url, target_path, sub_task_filename, size_hint=0, expected_worker_token=None):
    """下载单个文件（支持断点续传）。

    【⏱️ 延迟与超时精细控制】接入 download_timeout_connect/read、download_progress_interval、
    stream_chunk_size、download_retry_sleep。默认值与原来一致，正常下载路径无新增 sleep。

    【R413 修复 - 2026-11】获取 per-target_path 锁，防两个 worker 并发下同一文件
    → 交替写 .part → 文件损坏。详见文件头注释。

    【R420 修复 - 2026-11】等待 path_lock 期间每 5 秒更新 raw_line，让用户知道在排队。
    """
    temp_path = target_path + '.part'
    try:
        os.makedirs(os.path.dirname(target_path) if os.path.dirname(target_path) else '.', exist_ok=True)
    except Exception as e:
        return False, f'创建目录失败: {e}'

    # ========== 【R413 修复】获取 per-path 锁 ==========
    # 0.5 秒轮询 + 每轮检查暂停/删除 → 用户点暂停响应 ≤ 0.5 秒
    # 【R420 修复】每 5 秒更新一次 raw_line，让用户看到"等待中"而非误以为卡死
    path_lock = _get_download_path_lock(target_path)
    _lock_acquired = False
    try:
        _lock_wait_start = time.time()
        _last_msg_update = 0.0
        while True:
            if path_lock.acquire(timeout=0.5):
                _lock_acquired = True
                break
            # 等待期间检查任务状态（响应暂停/删除）
            _t = UPLOAD_TASKS.get(parent_task_id)
            if _t is None:
                return False, 'paused'
            if expected_worker_token is not None and _t.get('_worker_token') != expected_worker_token:
                return False, 'paused'
            if _t.get('status') in ('paused', 'waiting', 'cancelled'):
                return False, 'paused'
            # 【R420 修复】每 5 秒更新 raw_line 提示"等待前一个任务完成下载"
            _now = time.time()
            if _now - _last_msg_update >= 5.0:
                _last_msg_update = _now
                try:
                    _waited_secs = int(_now - _lock_wait_start)
                    _t['raw_line'] = f"⏳ 等待前一个任务完成下载: {sub_task_filename}（已等待 {_waited_secs} 秒）"
                    save_task_to_db(parent_task_id)
                except Exception as _e:
                    # DB 异常时不能中断等待循环（否则锁永远不释放）
                    logging.warning(f"[R420] 更新等待 raw_line 失败: {_e}")

        # ========== 【R413 修复】拿到锁后重新检测（前一个 worker 可能已完成） ==========
        try:
            if os.path.exists(target_path) and os.path.isfile(target_path):
                _existing_sz = os.path.getsize(target_path)
                if size_hint == 0 or _existing_sz == size_hint:
                    with DB_LOCK:
                        conn = get_db()
                        try:
                            conn.execute(
                                "UPDATE sub_tasks SET status='done', uploaded_bytes=file_size WHERE task_id=? AND file_path=?",
                                (parent_task_id, sub_task_filename)
                            )
                            conn.commit()
                        finally:
                            conn.close()
                    logging.info(f"[R413] 前一个 worker 已完成下载，跳过: {target_path}")
                    return True, ''
        except Exception as e:
            logging.warning(f"[R413] 重新检测已完成文件失败（继续下载）: {e}")

        # ========== 原有下载逻辑 ==========
        max_attempts = 5
        m = re.search(r'archive\.org/(?:download|details)/([^/]+)', url)
        referer = f"https://archive.org/details/{m.group(1)}" if m else "https://archive.org/"
        base_headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "*/*",
            "Referer": referer,
            "Accept-Encoding": "identity",
        }

        # 【⏱️ 延迟与超时精细控制】一次性读取，供整个下载流程使用
        dl_timeout = _get_download_timeout()
        progress_interval = _get_download_progress_interval()
        stream_chunk_size = _get_stream_chunk_size()
        retry_sleep = _get_download_retry_sleep()

        for attempt in range(max_attempts):
            # 【Bug 19】attempt 循环开头检查 token / status
            _t = UPLOAD_TASKS.get(parent_task_id)
            if _t is None:
                return False, 'paused'
            if expected_worker_token is not None and _t.get('_worker_token') != expected_worker_token:
                return False, 'paused'
            if _t.get('status') in ('paused', 'waiting'):
                return False, 'paused'
            if _t.get('status') == 'cancelled':
                return False, '已取消'

            initial_size = os.path.getsize(temp_path) if os.path.exists(temp_path) else 0
            headers = dict(base_headers)
            if initial_size > 0:
                headers['Range'] = f'bytes={initial_size}-'

            req_proxies = get_req_proxies()

            try:
                # 【⏱️ 延迟与超时精细控制】timeout=(15, 15) → _get_download_timeout()
                resp = requests.get(url, headers=headers, stream=True, timeout=dl_timeout,
                                    proxies=req_proxies, verify=get_ssl_verify(), allow_redirects=True)
                if resp.status_code in (429, 502, 503, 504):
                    raise Exception(f'HTTP {resp.status_code}')
                if resp.status_code not in (200, 206):
                    if resp.status_code == 404:
                        with DB_LOCK:
                            conn = get_db()
                            conn.execute("UPDATE sub_tasks SET status='done', uploaded_bytes=file_size WHERE task_id=? AND file_path=?", (parent_task_id, sub_task_filename))
                            conn.commit(); conn.close()
                        return True, ''
                    raise Exception(f'HTTP {resp.status_code}')

                content_type = resp.headers.get('Content-Type', '')
                if 'text/html' in content_type.lower():
                    resp.close()
                    raise Exception('服务器返回网页拦截（代理被封禁或文件不存在）')

                real_size = 0
                if resp.status_code == 200:
                    cl = resp.headers.get('Content-Length')
                    if cl:
                        try: real_size = int(cl)
                        except: pass
                elif resp.status_code == 206:
                    cr = resp.headers.get('Content-Range')
                    if cr and '/' in cr:
                        try: real_size = int(cr.split('/')[-1])
                        except: pass
                if real_size > 0 and real_size != size_hint:
                    with DB_LOCK:
                        conn = get_db()
                        conn.execute("UPDATE sub_tasks SET file_size=? WHERE task_id=? AND file_path=?",
                                     (real_size, parent_task_id, sub_task_filename))
                        conn.commit(); conn.close()
                    size_hint = real_size

                mode = 'ab' if (resp.status_code == 206 and initial_size > 0) else 'wb'
                if mode == 'wb':
                    initial_size = 0

                downloaded = initial_size
                last_update = 0.0

                with open(temp_path, mode) as f:
                    # 【⏱️ 延迟与超时精细控制】chunk_size=65536 → _get_stream_chunk_size()
                    for chunk in resp.iter_content(chunk_size=stream_chunk_size):
                        if not chunk:
                            continue

                        # 【Bug 19】chunk 循环内也检查 token / status
                        _t = UPLOAD_TASKS.get(parent_task_id)
                        if _t is None:
                            resp.close()
                            return False, 'paused'
                        if expected_worker_token is not None and _t.get('_worker_token') != expected_worker_token:
                            resp.close()
                            return False, 'paused'
                        if _t.get('status') in ('paused', 'waiting'):
                            resp.close()
                            return False, 'paused'
                        if _t.get('status') == 'cancelled':
                            raise Exception('已取消')

                        f.write(chunk)
                        downloaded += len(chunk)

                        now = time.time()
                        # 【⏱️ 延迟与超时精细控制】> 1.0 → > _get_download_progress_interval()
                        if now - last_update > progress_interval:
                            with DB_LOCK:
                                conn = get_db()
                                conn.execute("UPDATE sub_tasks SET uploaded_bytes=? WHERE task_id=? AND file_path=?",
                                             (downloaded, parent_task_id, sub_task_filename))
                                conn.commit(); conn.close()
                            # 【Bug 2 类修复】.get() 判空，防 in 检查后 [] 访问的微秒级竞态
                            _t2 = UPLOAD_TASKS.get(parent_task_id)
                            if _t2 is not None:
                                sz_str = format_bytes(downloaded)
                                tot_str = format_bytes(size_hint) if size_hint else "未知"
                                _t2['raw_line'] = f"⏳ 正在下载: {sub_task_filename} ({sz_str} / {tot_str})"
                                try: _update_parent_download_progress(parent_task_id)
                                except: pass
                            last_update = now

                os.replace(temp_path, target_path)
                with DB_LOCK:
                    conn = get_db()
                    conn.execute("UPDATE sub_tasks SET status='done', uploaded_bytes=? WHERE task_id=? AND file_path=?",
                                 (downloaded, parent_task_id, sub_task_filename))
                    conn.commit(); conn.close()
                return True, ''

            except requests.exceptions.ReadTimeout as e:
                if attempt < max_attempts - 1:
                    # 【⏱️ 延迟与超时精细控制】总等待 retry_sleep 秒（默认 5 秒），期间每 0.5 秒检查暂停
                    if not _wait_retry_or_pause(parent_task_id, expected_worker_token, retry_sleep):
                        return False, 'paused'
                    continue
                err = str(e)
                with DB_LOCK:
                    conn = get_db()
                    conn.execute("UPDATE sub_tasks SET status='error', error_msg=? WHERE task_id=? AND file_path=?",
                                 (err, parent_task_id, sub_task_filename))
                    conn.commit(); conn.close()
                return False, err

            except Exception as e:
                err = str(e)
                if 'HTTP 429' in err or 'HTTP 502' in err or 'HTTP 503' in err or 'HTTP 504' in err:
                    if attempt < max_attempts - 1:
                        wait_s = cfg_get('ia_429_backoff', 60)
                        _safe_update(parent_task_id, {'msg': f'⚠️ 触发风控({err})，等待 {wait_s} 秒后重试 ({attempt+1}/{max_attempts})...'})
                        save_task_to_db(parent_task_id)
                        interrupted = False
                        # 【保留硬编码】风控冷却每秒检查一次暂停（wait_s 已是配置的总时长，
                        # 不再拆分给 download_retry_sleep）
                        for _ in range(wait_s):
                            _t = UPLOAD_TASKS.get(parent_task_id)
                            if _t is None or (expected_worker_token is not None and _t.get('_worker_token') != expected_worker_token):
                                return False, 'paused'
                            if _t.get('status') in ('paused', 'waiting', 'cancelled'):
                                interrupted = True
                                break
                            time.sleep(1)
                        if interrupted:
                            return False, 'paused'
                        continue
                else:
                    if attempt < max_attempts - 1:
                        # 【⏱️ 延迟与超时精细控制】总等待 retry_sleep 秒（默认 5 秒），期间每 0.5 秒检查暂停
                        if not _wait_retry_or_pause(parent_task_id, expected_worker_token, retry_sleep):
                            return False, 'paused'
                        continue

                with DB_LOCK:
                    conn = get_db()
                    conn.execute("UPDATE sub_tasks SET status='error', error_msg=? WHERE task_id=? AND file_path=?",
                                 (err, parent_task_id, sub_task_filename))
                    conn.commit(); conn.close()
                return False, err

        return False, '重试次数耗尽'
    finally:
        # 【R413 修复】释放 per-path 锁（只在确实拿到的情况下）
        if _lock_acquired:
            try:
                path_lock.release()
            except Exception:
                pass


def _update_parent_download_progress(task_id):
    with DB_LOCK:
        conn = get_db()
        rows = conn.execute("SELECT status, file_size, uploaded_bytes FROM sub_tasks WHERE task_id=?", (task_id,)).fetchall()
        conn.close()
    if not rows: return
    total_sz = sum(int(r[1] or 0) for r in rows)
    done_sz = sum(int(r[2] or 0) for r in rows)
    done_cnt = sum(1 for r in rows if r[0] in ('done', 'skipped'))
    total_cnt = len(rows)
    has_err = any(r[0] == 'error' for r in rows)

    pct = round((done_sz / total_sz) * 100, 1) if total_sz > 0 else 0
    # 【Bug 2 类修复】.get() 判空，防 in 检查后 [] 访问的竞态
    _t = UPLOAD_TASKS.get(task_id)
    if _t is not None:
        speed_str = '0.0'
        if done_sz > 0:
            active_elapsed = float(_t.get('_active_elapsed', 0.0) or 0.0)
            started_at = _t.get('_download_started_at')
            now_ts = time.time()
            elapsed = active_elapsed + ((now_ts - started_at) if started_at else 0.0)
            if elapsed <= 0:
                elapsed = 1.0
            speed_str = f"{done_sz / elapsed / 1024 / 1024:.2f} MB/s"

        if total_sz > 0:
            _t['total_size_bytes'] = total_sz

        current_status = _t.get('status')
        # 【改进点 44】waiting 保持 waiting，让主循环重新 acquire 槽位
        new_status = current_status if current_status in ('paused', 'error', 'waiting') else 'downloading'

        _t.update({
            'percent': pct,
            'uploaded_bytes': done_sz,
            'files_uploaded': done_cnt,
            'total_files_count': total_cnt,
            'current': str(done_cnt),
            'total': str(total_cnt),
            'speed': speed_str,
            'status': new_status
        })
        if has_err:
            _t['msg'] = '⚠️ 部分文件下载失败，展开查看'
        save_task_to_db(task_id)


def download_folder_worker(task_id, folder_name, files_list, dl_dir):
    if not files_list:
        _safe_update(task_id, {'status': 'error', 'msg': '❌ 没有可下载的文件'})
        save_task_to_db(task_id)
        return

    # 【Bug 2 类修复】任务可能在 API 侧被删除
    if task_id not in UPLOAD_TASKS:
        return

    # 【P2-11 修复】folder_name 来自前端 it_name，未经清洗可构造 ../ 做路径穿越
    if folder_name:
        folder_name = str(folder_name).replace('/', '_').replace('\\', '_').replace('..', '_').strip()
        if not folder_name:
            folder_name = 'folder'

    total_size = sum(int(f.get('size', 0) or 0) for f in files_list)
    if folder_name:
        task_title = f'[下载文件夹] {folder_name} ({len(files_list)} 个文件)'
        task_type = 'download_folder'
    else:
        if len(files_list) == 1:
            fn = files_list[0]['filename'].split('/')[-1]
            task_title = f'[下载] {fn}'
            task_type = 'download'
        else:
            task_title = f'[下载] {len(files_list)} 个文件'
            task_type = 'download'

    # 【Bug 3 类修复】生成本 worker 唯一的 token，后续所有比较都用这个闭包变量
    my_worker_token = uuid.uuid4().hex

    # 【Bug 16 类修复】先 .get() 持有引用
    _existing_at_entry = UPLOAD_TASKS.get(task_id)
    _created_at_val = _existing_at_entry.get('created_at', time.time()) if _existing_at_entry else time.time()

    _safe_update(task_id, {
        'status': 'waiting', 'msg': '等待并发槽位...',
        'task_type': task_type,
        'title': task_title,
        'percent': 0, 'speed': '0.0',
        'current': '0', 'total': str(len(files_list)),
        'total_files_count': len(files_list),
        'total_size_bytes': total_size,
        'files_uploaded': 0, 'uploaded_bytes': 0,
        'created_at': _created_at_val,
        'raw_line': f'准备下载 {len(files_list)} 个文件...',
        '_worker_token': my_worker_token
    })
    save_task_to_db(task_id)

    with DB_LOCK:
        conn = get_db()
        conn.execute("DELETE FROM sub_tasks WHERE task_id=?", (task_id,))
        for f in files_list:
            conn.execute(
                "INSERT OR REPLACE INTO sub_tasks (task_id, file_path, file_size, status, uploaded_bytes) VALUES (?, ?, ?, 'waiting', 0)",
                (task_id, f['filename'], int(f.get('size', 0) or 0))
            )
        conn.commit(); conn.close()

    slot_held = False
    idx = 0
    try:
        if not UPLOAD_TASKS.get(task_id): return
        CONCURRENCY_LIMITER.acquire(timeout=86400)
        slot_held = True

        _safe_update(task_id, {'status': 'downloading', 'msg': '开始下载...'})
        save_task_to_db(task_id)
        _t = UPLOAD_TASKS.get(task_id)
        if _t is not None:
            _t['_download_started_at'] = time.time()
            _t['_active_elapsed'] = 0.0

        while idx < len(files_list):
            f = files_list[idx]
            while True:
                # 【Bug 2/3 类修复】主循环判空 + token 检查
                _t = UPLOAD_TASKS.get(task_id)
                if _t is None:
                    return
                if _t.get('_worker_token') != my_worker_token:
                    return
                status_now = _t.get('status')

                if status_now == 'paused':
                    _start = _t.get('_download_started_at')
                    if _start:
                        _t['_active_elapsed'] = float(_t.get('_active_elapsed', 0.0) or 0.0) + (time.time() - _start)
                        _t['_download_started_at'] = None
                    if slot_held:
                        CONCURRENCY_LIMITER.release()
                        slot_held = False
                        _t['msg'] = '⏸️ 已暂停（槽位已释放，排队中的任务可接替）'
                        save_task_to_db(task_id)
                    time.sleep(0.5)
                else:
                    if not slot_held:
                        _t['status'] = 'waiting'
                        _t['msg'] = '▶️ 恢复中，等待并发槽位...'
                        save_task_to_db(task_id)
                        # 【Bug 22 - 4/7】should_stop 回调：任务被删/替换时立即中止等待
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
                                return
                            # 【Bug 21 类修复】只切 downloading 条件为 "仍是 waiting"
                            if _t2.get('status') == 'waiting':
                                _t2['status'] = 'downloading'
                                _t2['msg'] = '▶️ 已恢复，继续下载...'
                                save_task_to_db(task_id)
                            else:
                                CONCURRENCY_LIMITER.release()
                                slot_held = False
                                continue
                            _t = _t2
                        except:
                            # 【Bug 22 - 4/7】acquire 被取消/超时：安全退出
                            if slot_held:
                                CONCURRENCY_LIMITER.release()
                                slot_held = False
                            return
                    if _t.get('_download_started_at') is None:
                        _t['_download_started_at'] = time.time()
                    break

            url = f['url']
            filename = f['filename']
            size = int(f.get('size', 0) or 0)

            safe_rel = filename.replace('\\', '/').lstrip('/')
            safe_rel = '/'.join(p for p in safe_rel.split('/') if p and p != '..')
            if not safe_rel:
                safe_rel = 'unnamed_file'

            if folder_name:
                target_path = os.path.join(dl_dir, folder_name, safe_rel)
            else:
                target_path = os.path.join(dl_dir, safe_rel)

            if os.path.exists(target_path) and os.path.isfile(target_path):
                local_size = os.path.getsize(target_path)
                if size == 0 or local_size == size:
                    with DB_LOCK:
                        conn = get_db()
                        conn.execute("UPDATE sub_tasks SET status='skipped', uploaded_bytes=file_size WHERE task_id=? AND file_path=?", (task_id, filename))
                        conn.commit(); conn.close()
                    _update_parent_download_progress(task_id)
                    idx += 1
                    continue

            with DB_LOCK:
                conn = get_db()
                conn.execute("UPDATE sub_tasks SET status='uploading' WHERE task_id=? AND file_path=?",
                             (task_id, filename))
                conn.commit(); conn.close()

            # 【Bug 19】传入 my_worker_token，chunk 循环内 token 被替换时立即中止
            # 【R413 修复】_do_download_one_file 内部获取 per-path 锁，防并发下同一文件损坏
            # 【R420 修复】等待 path_lock 期间每 5 秒更新 raw_line，让用户知道在排队
            ok, msg = _do_download_one_file(task_id, url, target_path, filename, size, expected_worker_token=my_worker_token)
            if msg == 'paused':
                if slot_held:
                    CONCURRENCY_LIMITER.release()
                    slot_held = False
                    _t = UPLOAD_TASKS.get(task_id)
                    if _t is not None:
                        if _t.get('status') == 'paused':
                            _t['msg'] = '⏸️ 已暂停（槽位已释放）'
                        elif _t.get('status') == 'waiting':
                            _t['msg'] = '▶️ 恢复中，等待并发槽位...'
                        save_task_to_db(task_id)
                continue

            _update_parent_download_progress(task_id)
            idx += 1

        with DB_LOCK:
            conn = get_db()
            has_err = conn.execute("SELECT COUNT(*) FROM sub_tasks WHERE task_id=? AND status='error'", (task_id,)).fetchone()[0] > 0
            conn.close()

        if has_err:
            _safe_update(task_id, {'status': 'error', 'msg': '⚠️ 部分文件下载失败，展开队列查看详情'})
        else:
            _safe_update(task_id, {'status': 'done', 'msg': '✅ 下载完成', 'percent': 100})
        save_task_to_db(task_id)

    except Exception as e:
        _safe_update(task_id, {'status': 'error', 'msg': f'❌ 失败: {e}'})
        save_task_to_db(task_id)
    finally:
        if slot_held:
            try: CONCURRENCY_LIMITER.release()
            except: pass


def download_file_worker(task_id, url, filename, dl_dir):
    download_folder_worker(task_id, '', [{'filename': filename, 'url': url, 'size': 0}], dl_dir)