# netdisk_workers_append.py
# 批量追加文件工作线程
# 【修复 Q1】全部秒传时 finally 覆盖"全部秒传"msg，改用 all_skipped 标志区分 final_msg
# 【改进点 AB】cloud_files 拉取 + local_md5_cache 计算从 acquire 内移到 acquire 前
# 【改进点 BG】ia_retries 改为 0
# 【改进点 BH】准备阶段（fetch/md5）加 token 检查 + 暂停等待
# 【改进点 BI】跳过文件后的 _safe_update 加 paused/error 保护
# 【修复 Q2】_check_token_and_pause 只等 paused
# 【A1f 修复 - 2026-11】_NON_RETRYABLE_KEYWORDS 加 3 个不可重试关键词
# 【B2-后端 修复 - 2026-11】append_files_worker 失败分支把 sub_tasks 的 waiting/uploading 标记为 error
# 【B17 - 2026-11】finally 块加注释说明保留 f_path
# 【连接泄漏修复 - 2026-11】所有 conn.execute(...); conn.commit(); conn.close() 改成 try/finally
# 【🔴 P0-20】append_files_worker 开头加 identifier 校验
# 【🔴 G-27】_make_ia_files_param 改三级兼容
# 【🔴 P0-28 同步 - 2026-11】与 netdisk_workers_upload.py 的 P0-22/26/27-1 同步：
#                          1. 主循环 time.sleep(1.5) → time.sleep(0.5)（P0-22）
#                          2. 速度改为最近 10 秒滑动平均（P0-22，断点续传不再虚高）
#                          3. 主循环用 _last_reported_bytes + t['uploaded_bytes'] 双重下限（P0-26）
#                          4. _last_reported_bytes 始终更新，修复 else 配对错误（P0-27-1）
#
# 【⏱️ 延迟与超时精细控制 - 2026-11 第 6/20 步】
# 接入 2 个新配置项（与 netdisk_workers_upload.py 同源）：
#   upload_poll_interval (默认 0.5，0.1-10.0 秒) → 主循环 sleep
#   upload_file_retry    (默认 5，0-20 次)       → _append_one_file 的 max_file_retries
#
# 【🔴 R304 检查 - 2026-11（本轮）】
#   问题：R108（用户暂停导致 attempt 满时误标 error）在 append 中是否存在？
#   详细检查 _append_one_file 的 attempt 循环：
#     - attempt += 1 只在「非 interrupted 的正常失败路径」末尾执行
#     - 用户暂停 → interrupted=True → continue → **跳过 attempt += 1**
#     - 所以「用户暂停」不会消耗 attempt 次数
#     - 用户不暂停，5 次真正失败 → attempt 满 → return False → failed_files
#   结论：**append 无 R108 问题**，不需要修复。
#   用户暂停 → 阻塞在 while True 暂停循环 → 用户恢复 → 继续 → 用户不恢复 → 永久等待
#   （原设计，用户可点「删除」强制终止 → api_tasks_del → UPLOAD_TASKS.pop → worker 下次检查 t is None 退出）
#
# 【📝 保留记录 - 2026-11，低优先，以后修】
#   R304 已检查 ✅（append 无 R108 问题）
#   R309：append 暂停循环无日志（用户点暂停后 worker 静默等待，UI 状态由 task['msg'] 显示）—
#        原设计，可接受

import os
import uuid
import threading
import time
import logging
import collections
import random
import hashlib
from concurrent.futures import ThreadPoolExecutor, as_completed
import internetarchive as ia

try:
    from internetarchive import File as _IAFile
except ImportError:
    _IAFile = None

import netdisk_state_locks
from netdisk_state import (
    UPLOAD_TASKS, DB_LOCK, TEMP_UPLOAD_DIR, UPLOAD_COOLDOWN_LOCK,
    get_db, get_config, cfg_get
)
from netdisk_utils import (
    CONCURRENCY_LIMITER,
    _append_log,
    _fetch_and_cache_ia_files, _upload_large_file_s3,
    ProgressFileWrapper, _opts_to_metadata, get_ssl_verify,
    save_task_to_db, cleanup_temp_dir,
)
# 【P0-20】复用 upload_worker 的 identifier 校验和异常类
from netdisk_workers_upload import (
    _is_valid_identifier,
    _NON_RETRYABLE_KEYWORDS,
    _RATE_LIMIT_HINTS,
    _UploadPausedException,
    _UploadExpiredException,
)


# ========== 【⏱️ 延迟与超时精细控制】2 个带 5 秒 TTL 缓存的读取函数 ==========
# 与 netdisk_workers_upload.py 同源（避免重复导入，这里独立定义方便 append 自己控制）
_UPLOAD_POLL_INTERVAL_CACHE = {'value': 0.5, 'at': 0.0}
_UPLOAD_FILE_RETRY_CACHE = {'value': 5, 'at': 0.0}
_CFG_CACHE_TTL = 5.0


def _get_upload_poll_interval():
    """返回上传主循环刷新间隔，默认 0.5，范围 0.1-10.0 秒。"""
    now = time.time()
    if now - _UPLOAD_POLL_INTERVAL_CACHE['at'] < _CFG_CACHE_TTL:
        return _UPLOAD_POLL_INTERVAL_CACHE['value']
    try:
        v = max(0.1, min(float(cfg_get('upload_poll_interval', 0.5)), 10.0))
    except Exception:
        v = 0.5
    _UPLOAD_POLL_INTERVAL_CACHE['value'] = v
    _UPLOAD_POLL_INTERVAL_CACHE['at'] = now
    return v


def _get_upload_file_retry():
    """返回上传单文件最大尝试次数（含首次），默认 5，范围 0-20 次。

    语义说明（重要）：
      - 这是「最大尝试次数」而非「重试次数」
      - 0 → 调用方 max(1, ...) 兜底为 1 次尝试（不重试），避免完全不上传
      - 5 → 5 次尝试 = 1 次初始 + 4 次重试（原默认行为）
    """
    now = time.time()
    if now - _UPLOAD_FILE_RETRY_CACHE['at'] < _CFG_CACHE_TTL:
        return _UPLOAD_FILE_RETRY_CACHE['value']
    try:
        v = max(0, min(int(cfg_get('upload_file_retry', 5)), 20))
    except Exception:
        v = 5
    _UPLOAD_FILE_RETRY_CACHE['value'] = v
    _UPLOAD_FILE_RETRY_CACHE['at'] = now
    return v


def _is_non_retryable_error(e):
    try:
        msg = str(e).lower()
    except Exception:
        return False
    if any(x in msg for x in _RATE_LIMIT_HINTS):
        return False
    return any(k in msg for k in _NON_RETRYABLE_KEYWORDS)


def _compute_md5(fp):
    try:
        h = hashlib.md5()
        with open(fp, 'rb') as f:
            while True:
                chunk = f.read(1024 * 1024)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest()
    except Exception as e:
        logging.warning(f"计算 md5 失败 {fp}: {e}")
        return None


def _compute_md5_sha1(fp):
    try:
        md5 = hashlib.md5()
        sha1 = hashlib.sha1()
        with open(fp, 'rb') as f:
            while True:
                chunk = f.read(1024 * 1024)
                if not chunk:
                    break
                md5.update(chunk)
                sha1.update(chunk)
        return md5.hexdigest(), sha1.hexdigest()
    except Exception as e:
        logging.warning(f"计算 md5/sha1 失败 {fp}: {e}")
        return None, None


def _compute_sha1(fp):
    try:
        h = hashlib.sha1()
        with open(fp, 'rb') as f:
            while True:
                chunk = f.read(1024 * 1024)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest()
    except Exception as e:
        logging.warning(f"计算 sha1 失败 {fp}: {e}")
        return None


# 【G-27】_make_ia_files_param 改三级兼容
def _make_ia_files_param(rel_path, pf, sz, md5_hex, sha1_hex):
    """尝试用多种方式构造 internetarchive.File。失败时静默回退简单格式。"""
    if not (md5_hex and sha1_hex):
        return {rel_path: pf}
    if _IAFile is None:
        return {rel_path: pf}

    try:
        f = _IAFile(rel_path, fileobj=pf, size=sz, md5=md5_hex, sha1=sha1_hex)
        return {rel_path: f}
    except (TypeError, AttributeError, ValueError):
        pass
    except Exception:
        pass

    try:
        f = _IAFile(rel_path, file=pf, size=sz, md5=md5_hex, sha1=sha1_hex)
        return {rel_path: f}
    except (TypeError, AttributeError, ValueError):
        pass
    except Exception:
        pass

    try:
        f = _IAFile(name=rel_path, fileobj=pf, size=sz, md5=md5_hex, sha1=sha1_hex)
        return {rel_path: f}
    except (TypeError, AttributeError, ValueError):
        pass
    except Exception:
        pass

    try:
        pf.seek(0)
    except Exception:
        pass
    return {rel_path: pf}


def _safe_update(task_id, patch):
    t = UPLOAD_TASKS.get(task_id)
    if t is None:
        return False
    t.update(patch)
    return True


def _safe_update_monotonic(task_id, patch, monotonic_key='files_uploaded'):
    t = UPLOAD_TASKS.get(task_id)
    if t is None:
        return False
    cur_val = t.get(monotonic_key, 0)
    new_val = patch.get(monotonic_key, cur_val)
    if new_val < cur_val:
        return False
    t.update(patch)
    return True


def _is_task_canceled_error(e):
    msg = str(e)
    return "任务已被删除" in msg or "任务已被替换" in msg


def append_files_worker(task_id, identifier, file_infos, metadata, db_id, opts=None):
    if opts is None: opts = {}
    if not file_infos:
        _safe_update(task_id, {'status': 'error', 'msg': '❌ 没有可追加的文件'})
        save_task_to_db(task_id)
        return

    if task_id not in UPLOAD_TASKS:
        return

    # 【P0-20 核心】identifier 校验
    if not _is_valid_identifier(identifier):
        err_msg = (
            f'❌ identifier 不合规（当前值: {identifier!r}）：'
            f'需 5-102 字符，首字母或数字开头，只能包含字母、数字、_、.、-。'
            f'请点「✏️ 编辑」修改 identifier 后重试'
        )
        _safe_update(task_id, {'status': 'error', 'msg': err_msg})
        save_task_to_db(task_id)
        try:
            with DB_LOCK:
                conn = get_db()
                try:
                    conn.execute("UPDATE items SET status='error', error_msg=? WHERE id=?", (err_msg[:500], db_id))
                    conn.commit()
                finally:
                    conn.close()
        except: pass
        return

    total_files_count = len(file_infos)
    total_size_bytes = sum(f['sz'] for f in file_infos)
    title_str = metadata.get('title', identifier)

    my_worker_token = uuid.uuid4().hex

    _existing_at_entry = UPLOAD_TASKS.get(task_id)
    _created_at_val = _existing_at_entry.get('created_at', time.time()) if _existing_at_entry else time.time()

    _safe_update(task_id, {
        'status': 'waiting', 'msg': '准备中...',
        'task_type': 'append',
        'title': f'[批量追加] {title_str}',
        'percent': 0, 'speed': '0.0',
        'current': '0', 'total': str(total_files_count),
        'total_files_count': total_files_count,
        'total_size_bytes': total_size_bytes,
        'files_uploaded': 0, 'uploaded_bytes': 0,
        'created_at': _created_at_val,
        'raw_line': f'准备追加 {total_files_count} 个文件...',
        '_worker_token': my_worker_token,
        '_last_reported_bytes': 0,
    })
    save_task_to_db(task_id)

    slot_held = False
    append_success = False
    all_skipped = False
    skipped_count_final = 0
    try:
        if not UPLOAD_TASKS.get(task_id): return

        def _check_token_and_pause():
            while True:
                _t = UPLOAD_TASKS.get(task_id)
                if _t is None:
                    return False
                if _t.get('_worker_token') != my_worker_token:
                    return False
                if _t.get('status') != 'paused':
                    return True
                time.sleep(1)

        # 【AB】拉云端列表（无槽位）
        cloud_files = {}
        if not opts.get('overwrite_same_file'):
            if not _check_token_and_pause():
                return
            fetch_ok = _fetch_and_cache_ia_files(identifier, force=True)
            if not fetch_ok:
                logging.warning(f"[秒传] {identifier} 云端列表拉取失败，本次可能全部重新上传（消耗更多流量）")
            with DB_LOCK:
                conn = get_db()
                try:
                    rows = conn.execute("SELECT filename, size, md5 FROM file_cache WHERE identifier=?", (identifier,)).fetchall()
                finally:
                    conn.close()
                for r in rows:
                    cloud_files[r[0]] = {'size': r[1], 'md5': r[2]}

        # 【AB】算本地 md5（无槽位 + token 检查 + 暂停等待）
        local_md5_cache = {}
        if not opts.get('overwrite_same_file') and cloud_files:
            for f_info in file_infos:
                if not _check_token_and_pause():
                    return
                rel_path = f_info['rel']
                sz = f_info['sz']
                cloud_info = cloud_files.get(rel_path)
                if not cloud_info:
                    continue
                try:
                    cloud_sz = int(str(cloud_info.get('size', '') or '').strip() or 0)
                except Exception:
                    cloud_sz = 0
                cloud_md5 = (cloud_info.get('md5') or '').strip()
                if cloud_sz > 0 and cloud_sz == sz and cloud_md5 and '-' not in cloud_md5:
                    local_md5 = _compute_md5(f_info['fp'])
                    if local_md5:
                        local_md5_cache[rel_path] = local_md5

        # ========== 正式上传阶段（占并发槽位） ==========
        def _should_stop_waiting():
            _tt = UPLOAD_TASKS.get(task_id)
            return _tt is None or _tt.get('_worker_token') != my_worker_token

        try:
            CONCURRENCY_LIMITER.acquire(timeout=86400, should_stop=_should_stop_waiting)
            slot_held = True
        except Exception:
            if slot_held:
                CONCURRENCY_LIMITER.release()
                slot_held = False
            return

        files_to_upload = []
        skipped_count = 0
        skipped_size = 0
        chunk_threshold = opts.get('chunk_threshold', 200) * 1024 * 1024
        with DB_LOCK:
            conn = get_db()
            try:
                conn.execute("DELETE FROM sub_tasks WHERE task_id=?", (task_id,))
                for f_info in file_infos:
                    rel_path = f_info['rel']
                    sz = f_info['sz']
                    is_skip = False
                    if rel_path in cloud_files:
                        cloud_info = cloud_files[rel_path]
                        try:
                            cloud_sz = int(str(cloud_info.get('size', '') or '').strip() or 0)
                        except Exception:
                            cloud_sz = 0
                        cloud_md5 = (cloud_info.get('md5') or '').strip()
                        if cloud_sz > 0 and cloud_sz == sz:
                            if cloud_md5 and '-' not in cloud_md5:
                                local_md5 = local_md5_cache.get(rel_path)
                                if local_md5 and local_md5 == cloud_md5:
                                    is_skip = True
                            else:
                                is_skip = True
                                logging.info(f"[秒传] {rel_path} size 匹配但云端无标准 md5，跳过")
                    if is_skip:
                        skipped_count += 1
                        skipped_size += sz
                        conn.execute('''INSERT OR REPLACE INTO sub_tasks (task_id, file_path, file_size, status, uploaded_bytes)
                                        VALUES (?, ?, ?, 'skipped', ?)''', (task_id, rel_path, sz, sz))
                    else:
                        conn.execute('''INSERT OR REPLACE INTO sub_tasks (task_id, file_path, file_size, status, uploaded_bytes)
                                        VALUES (?, ?, ?, 'waiting', 0)''', (task_id, rel_path, sz))
                        files_to_upload.append(f_info)
                conn.commit()
            finally:
                conn.close()

        # 【BI】patch：waiting→uploading，paused/error 保持
        patch = {
            'files_uploaded': skipped_count,
            'uploaded_bytes': skipped_size,
            'percent': round((skipped_size / total_size_bytes) * 100, 1) if total_size_bytes > 0 else 0,
            'msg': f'跳过 {skipped_count} 个云端已有文件，开始追加...',
            '_last_reported_bytes': skipped_size,
        }
        t_now = UPLOAD_TASKS.get(task_id)
        if t_now is not None and t_now.get('status') not in ('paused', 'error'):
            patch['status'] = 'uploading'
        _safe_update(task_id, patch)
        save_task_to_db(task_id)

        # 【修复 Q1】记录状态供 finally 判断
        skipped_count_final = skipped_count

        if not files_to_upload:
            # 【BA + 修复 Q1】全部秒传，明确提示
            all_skipped = True
            _safe_update(task_id, {'msg': f'✅ 全部 {skipped_count} 个文件已在云端（秒传完成，无需实际上传）'})
            save_task_to_db(task_id)
            append_success = True
        else:
            meta_dict = dict(metadata)
            if 'collection' not in meta_dict or not meta_dict['collection']:
                meta_dict['collection'] = 'opensource'
            meta_dict.update(_opts_to_metadata(opts))

            cfg = get_config()
            if cfg.get('download_via_proxy', True) and cfg.get('proxy', '').strip():
                proxy_h = cfg.get('proxy', '').strip()
                proxy_h = proxy_h.replace("socks5://", "socks5h://") if proxy_h.startswith("socks5://") else proxy_h
                os.environ['http_proxy'] = proxy_h
                os.environ['https_proxy'] = proxy_h
            else:
                os.environ.pop('http_proxy', None)
                os.environ.pop('https_proxy', None)

            # 【BG】ia 内部重试改为 0
            ia_retries = 0

            uploaded_set = set()
            file_progress_map = {}
            uploaded_bytes = skipped_size
            lock = threading.Lock()
            start_time = time.time()
            conc_threads = 8
            try:
                conc_threads = max(1, min(int(opts.get('concurrency', 8)), 16))
            except: conc_threads = 8
            history_lines = collections.deque(maxlen=max(10, cfg_get('raw_line_max_lines', 500)))
            _append_log(history_lines, f"已启动 {conc_threads} 个并发追加线程...")
            failed_files = []

            # 【P0-28】速度滑动平均样本（每 0.5 秒一个样本，maxlen=20 覆盖 10 秒）
            speed_samples = collections.deque(maxlen=20)

            def _wait_for_resume_or_abort():
                while True:
                    time.sleep(1)
                    _t = UPLOAD_TASKS.get(task_id)
                    if _t is None:
                        return False
                    if _t.get('_worker_token') != my_worker_token:
                        return False
                    if _t.get('status') == 'waiting':
                        return True
                    if _t.get('status') not in ('paused',):
                        return False

            def _append_one_file(f_info):
                nonlocal uploaded_bytes
                fp = f_info['fp']
                rel_path = f_info['rel']
                sz = f_info['sz']
                # 【⏱️ 延迟与超时精细控制】单文件最大尝试次数读配置（默认 5，0-20）
                # 【BUG 修复】max(1, ...) 兜底：用户填 0 时至少尝试 1 次
                max_file_retries = max(1, _get_upload_file_retry())
                attempt = 0

                while attempt < max_file_retries:
                    # 【R304 检查】attempt += 1 在「非 interrupted 的正常失败路径」末尾
                    #   用户暂停 → interrupted=True → continue → 跳过 attempt += 1
                    #   所以 append 无 R108 问题（用户暂停不消耗 attempt 次数）
                    while True:
                        t = UPLOAD_TASKS.get(task_id)
                        if t is None:
                            return False
                        if t.get('_worker_token') != my_worker_token:
                            return False
                        if t.get('status') not in ('paused', 'waiting'):
                            break
                        time.sleep(1)

                    with UPLOAD_COOLDOWN_LOCK:
                        cooldown = netdisk_state_locks.GLOBAL_UPLOAD_COOLDOWN
                    now = time.time()
                    if now < cooldown:
                        wait_time = cooldown - now
                        with lock:
                            _append_log(history_lines, f"❄️ 全局冷却中... 剩余 {wait_time:.0f}s")
                            _safe_update(task_id, {'raw_line': "\n".join(history_lines)})
                            save_task_to_db(task_id)
                        interrupted = False
                        for _ in range(int(wait_time)):
                            t = UPLOAD_TASKS.get(task_id)
                            if t is None:
                                return False
                            if t.get('_worker_token') != my_worker_token:
                                return False
                            if t.get('status') in ('paused', 'waiting'):
                                interrupted = True
                                break
                            time.sleep(1)
                        if interrupted: continue
                        time.sleep(random.uniform(0.1, 1.0))
                        continue

                    try:
                        with DB_LOCK:
                            conn = get_db()
                            try:
                                conn.execute("UPDATE sub_tasks SET status='uploading' WHERE task_id=? AND file_path=?", (task_id, rel_path))
                                conn.commit()
                            finally:
                                conn.close()

                        if sz >= chunk_threshold:
                            def _s3_progress_cb(rel_p, uploaded_so_far, total_bytes):
                                with lock:
                                    file_progress_map[rel_p] = (uploaded_so_far, total_bytes)
                            _upload_large_file_s3(identifier, rel_path, fp, sz, opts, task_id,
                                                  expected_worker_token=my_worker_token,
                                                  progress_callback=_s3_progress_cb,
                                                  metadata=meta_dict)
                        else:
                            md5_hex = local_md5_cache.get(rel_path)
                            if md5_hex:
                                sha1_hex = _compute_sha1(fp)
                                if not sha1_hex:
                                    md5_hex, sha1_hex = _compute_md5_sha1(fp)
                            else:
                                md5_hex, sha1_hex = _compute_md5_sha1(fp)

                            with ProgressFileWrapper(fp, rel_path, sz, file_progress_map, lock) as pf:
                                files_param = _make_ia_files_param(rel_path, pf, sz, md5_hex, sha1_hex)
                                ia.upload(identifier, files=files_param, metadata=meta_dict, retries=ia_retries, verbose=False, verify=get_ssl_verify())

                        with lock:
                            file_progress_map.pop(rel_path, None)
                            uploaded_set.add(rel_path)
                            uploaded_bytes += sz
                            elapsed = time.time() - start_time
                            speed = uploaded_bytes / elapsed if elapsed > 0 else 0
                            pct = round((uploaded_bytes / total_size_bytes) * 100, 1) if total_size_bytes > 0 else 0
                            log_line = f"✅ [{len(uploaded_set)+skipped_count}/{total_files_count}] {rel_path} ({sz/1024/1024:.1f}MB)"
                            _append_log(history_lines, log_line)
                            _snap_pct = pct
                            _snap_uploaded_bytes = uploaded_bytes
                            _snap_done = len(uploaded_set) + skipped_count
                            _snap_speed = speed
                            _snap_raw = "\n".join(history_lines)

                        t = UPLOAD_TASKS.get(task_id)
                        if t is not None:
                            cur_done = t.get('files_uploaded', 0)
                            if _snap_done >= cur_done:
                                st = t.get('status')
                                new_st = st if st in ('paused', 'error', 'waiting') else 'uploading'
                                t.update({
                                    'percent': _snap_pct,
                                    'uploaded_bytes': _snap_uploaded_bytes,
                                    'files_uploaded': _snap_done,
                                    'current': str(_snap_done),
                                    'speed': f"{_snap_speed/1024/1024:.2f} MB/s",
                                    'raw_line': _snap_raw,
                                    'status': new_st
                                })
                        save_task_to_db(task_id)
                        with DB_LOCK:
                            conn = get_db()
                            try:
                                conn.execute("UPDATE sub_tasks SET status='done', uploaded_bytes=? WHERE task_id=? AND file_path=?", (sz, task_id, rel_path))
                                conn.commit()
                            finally:
                                conn.close()
                        return True

                    except _UploadPausedException:
                        with lock:
                            _append_log(history_lines, f"⏸️ [{rel_path}] 用户暂停，等待恢复（已上传分片会保留）")
                            _safe_update(task_id, {'raw_line': "\n".join(history_lines), 'msg': f'⏸️ 已暂停（{rel_path} 断点已保留）'})
                        save_task_to_db(task_id)
                        ok = _wait_for_resume_or_abort()
                        if not ok:
                            return False
                        with lock:
                            _append_log(history_lines, f"▶️ [{rel_path}] 恢复追加，从断点继续...")
                            _safe_update(task_id, {'raw_line': "\n".join(history_lines)})
                        save_task_to_db(task_id)
                        continue

                    except _UploadExpiredException as e:
                        with lock:
                            _append_log(history_lines, f"❌ [{rel_path}] uploadId 失效: {e}")
                            failed_files.append(rel_path)
                            _safe_update(task_id, {'raw_line': "\n".join(history_lines)})
                        save_task_to_db(task_id)
                        with DB_LOCK:
                            conn = get_db()
                            try:
                                conn.execute(
                                    "UPDATE sub_tasks SET status='error', error_msg=? WHERE task_id=? AND file_path=?",
                                    (f"uploadId 失效: {str(e)[:200]}", task_id, rel_path)
                                )
                                conn.commit()
                            finally:
                                conn.close()
                        return False

                    except Exception as e:
                        with lock:
                            file_progress_map.pop(rel_path, None)

                        if _is_task_canceled_error(e):
                            return False

                        if _is_non_retryable_error(e):
                            with lock:
                                _append_log(history_lines, f"❌ [{rel_path}] 不可重试错误，放弃: {e}")
                                failed_files.append(rel_path)
                                _safe_update(task_id, {'raw_line': "\n".join(history_lines)})
                                save_task_to_db(task_id)
                            with DB_LOCK:
                                conn = get_db()
                                try:
                                    conn.execute(
                                        "UPDATE sub_tasks SET status='error', error_msg=? WHERE task_id=? AND file_path=?",
                                        (f"不可重试: {str(e)[:200]}", task_id, rel_path)
                                    )
                                    conn.commit()
                                finally:
                                    conn.close()
                            return False

                        err_msg = str(e).lower()
                        is_rate_limit = any(x in err_msg for x in ["reduce your request rate", "bucket_tasks_queued", "too many requests", "403", "429", "503"])
                        if is_rate_limit:
                            with UPLOAD_COOLDOWN_LOCK:
                                netdisk_state_locks.GLOBAL_UPLOAD_COOLDOWN = time.time() + cfg_get('ia_429_backoff', 60)
                            with lock:
                                _append_log(history_lines, f"⚠️ [{rel_path}] 触发IA并发风控，自动进入60秒冷却重试 ({attempt+1}/{max_file_retries})...")
                                _safe_update(task_id, {'raw_line': "\n".join(history_lines)})
                                save_task_to_db(task_id)
                            interrupted = False
                            for _ in range(cfg_get('ia_429_backoff', 60)):
                                t = UPLOAD_TASKS.get(task_id)
                                if t is None:
                                    return False
                                if t.get('_worker_token') != my_worker_token:
                                    return False
                                if t.get('status') in ('paused', 'waiting'):
                                    interrupted = True
                                    break
                                time.sleep(1)
                            if interrupted: continue
                        else:
                            if attempt == max_file_retries - 1:
                                with lock:
                                    _append_log(history_lines, f"❌ [{rel_path}] 失败: {e}")
                                    failed_files.append(rel_path)
                                    _safe_update(task_id, {'raw_line': "\n".join(history_lines)})
                                    save_task_to_db(task_id)
                                    with DB_LOCK:
                                        conn = get_db()
                                        try:
                                            conn.execute("UPDATE sub_tasks SET status='error', error_msg=? WHERE task_id=? AND file_path=?", (str(e), task_id, rel_path))
                                            conn.commit()
                                        finally:
                                            conn.close()
                                return False
                            interrupted = False
                            for _ in range(3):
                                t = UPLOAD_TASKS.get(task_id)
                                if t is None:
                                    return False
                                if t.get('_worker_token') != my_worker_token:
                                    return False
                                if t.get('status') in ('paused', 'waiting'):
                                    interrupted = True
                                    break
                                time.sleep(1)
                            if interrupted: continue

                    attempt += 1
                return False

            _safe_update(task_id, {'raw_line': "\n".join(history_lines)})
            save_task_to_db(task_id)
            executor = ThreadPoolExecutor(max_workers=conc_threads)
            futures = {executor.submit(_append_one_file, f): f['rel'] for f in files_to_upload}

            # 【P0-28 / ⏱️ 延迟与超时精细控制】主循环 time.sleep(1.5) → 0.5 → 可配置
            while True:
                time.sleep(_get_upload_poll_interval())
                all_done = all(f.done() for f in futures.keys())
                with lock:
                    done_count = len(uploaded_set) + skipped_count
                    active_bytes = sum(curr for curr, total in file_progress_map.values())
                    real_uploaded_bytes = uploaded_bytes + active_bytes
                    current_active = dict(file_progress_map)
                if all_done:
                    t_check = UPLOAD_TASKS.get(task_id)
                    if t_check is None:
                        executor.shutdown(wait=False)
                        return
                    if t_check.get('_worker_token') != my_worker_token:
                        executor.shutdown(wait=False)
                        return
                    if t_check.get('status') == 'paused':
                        _safe_update(task_id, {'msg': f'⏸️ 已暂停（{done_count}/{total_files_count} 个文件已完成，点击继续恢复）'})
                        save_task_to_db(task_id)
                        if slot_held:
                            CONCURRENCY_LIMITER.release()
                            slot_held = False
                        return
                    break

                if current_active:
                    with DB_LOCK:
                        conn = get_db()
                        try:
                            for rel_p, (curr_b, tot_b) in current_active.items():
                                conn.execute("UPDATE sub_tasks SET uploaded_bytes=? WHERE task_id=? AND file_path=?", (curr_b, task_id, rel_p))
                            conn.commit()
                        finally:
                            conn.close()

                t = UPLOAD_TASKS.get(task_id)
                if t is None:
                    executor.shutdown(wait=False)
                    return
                if t.get('_worker_token') != my_worker_token:
                    executor.shutdown(wait=False)
                    return

                status_now = t.get('status')
                if status_now == 'paused':
                    if slot_held:
                        CONCURRENCY_LIMITER.release()
                        slot_held = False
                    continue
                elif status_now == 'waiting':
                    if not slot_held:
                        t['msg'] = '▶️ 恢复中，等待并发槽位...'
                        save_task_to_db(task_id)
                        def _should_stop_waiting2():
                            _tt = UPLOAD_TASKS.get(task_id)
                            return _tt is None or _tt.get('_worker_token') != my_worker_token
                        try:
                            CONCURRENCY_LIMITER.acquire(timeout=86400, should_stop=_should_stop_waiting2)
                            slot_held = True
                            t2 = UPLOAD_TASKS.get(task_id)
                            if t2 is None or t2.get('_worker_token') != my_worker_token:
                                CONCURRENCY_LIMITER.release()
                                slot_held = False
                                executor.shutdown(wait=False)
                                return
                            t = t2
                            if t.get('status') == 'waiting':
                                t['status'] = 'uploading'
                                t['msg'] = '▶️ 已恢复，继续追加...'
                                save_task_to_db(task_id)
                            else:
                                CONCURRENCY_LIMITER.release()
                                slot_held = False
                                continue
                        except:
                            if slot_held:
                                CONCURRENCY_LIMITER.release()
                                slot_held = False
                            executor.shutdown(wait=False)
                            return
                    else:
                        t['status'] = 'uploading'
                        save_task_to_db(task_id)

                # 【P0-28 / P0-27-1】双重下限 + _last_reported_bytes 始终更新
                _last_rb = t.get('_last_reported_bytes', 0)
                cur_uploaded_now = t.get('uploaded_bytes', 0) or 0
                if real_uploaded_bytes < _last_rb:
                    real_uploaded_bytes = _last_rb
                if real_uploaded_bytes < cur_uploaded_now:
                    real_uploaded_bytes = cur_uploaded_now
                t['_last_reported_bytes'] = real_uploaded_bytes

                # 【P0-28】速度改用最近 10 秒滑动平均
                now_ts = time.time()
                speed_samples.append((now_ts, real_uploaded_bytes))
                while speed_samples and now_ts - speed_samples[0][0] > 10:
                    speed_samples.popleft()
                if len(speed_samples) >= 2:
                    ts0, b0 = speed_samples[0]
                    ts1, b1 = speed_samples[-1]
                    dt = ts1 - ts0
                    if dt > 0 and b1 >= b0:
                        speed_bytes_per_sec = (b1 - b0) / dt
                        speed_mb = speed_bytes_per_sec / 1024 / 1024
                    else:
                        speed_mb = 0.0
                else:
                    speed_mb = 0.0

                elapsed = now_ts - start_time
                pct = round((real_uploaded_bytes / total_size_bytes) * 100, 1) if total_size_bytes > 0 else 0
                active_str = ""
                if current_active:
                    parts = []
                    for i, (rel_path, (curr, total)) in enumerate(current_active.items()):
                        if i >= 3:
                            parts.append(f"等{len(current_active)}个文件")
                            break
                        name = os.path.basename(rel_path)
                        parts.append(f"{name} ({curr/1024/1024:.1f}MB/{total/1024/1024:.1f}MB)")
                    active_str = "⏳ 正在追加: " + " | ".join(parts)
                else:
                    active_str = "⏳ 正在分配任务或重试等待..."
                tail = f"{active_str} \n总进度: {real_uploaded_bytes/1024/1024:.1f}MB/{total_size_bytes/1024/1024:.1f}MB | {speed_mb:.2f}MB/s | 耗时:{elapsed:.0f}s"
                _safe_update_monotonic(task_id, {
                    'percent': pct,
                    'files_uploaded': done_count,
                    'uploaded_bytes': real_uploaded_bytes,
                    'current': str(done_count),
                    'speed': f"{speed_mb:.2f} MB/s",
                    'raw_line': tail + "\n" + "\n".join(history_lines)
                }, monotonic_key='files_uploaded')
                save_task_to_db(task_id)

            for future in as_completed(futures):
                try: future.result()
                except Exception as e:
                    with lock: _append_log(history_lines, f"线程异常: {e}")
            executor.shutdown(wait=True)

            if failed_files:
                raise Exception(f"有 {len(failed_files)} 个文件追加失败，请展开队列查看详情。")
            append_success = True

    except Exception as e:
        err_msg = str(e)
        _safe_update(task_id, {'status': 'error', 'msg': f'❌ 失败: {err_msg}'})
        save_task_to_db(task_id)
        # 【B2-后端 修复】把剩余未完成的子任务标记为 error
        try:
            with DB_LOCK:
                conn = get_db()
                try:
                    conn.execute(
                        "UPDATE sub_tasks SET status='error', error_msg=? WHERE task_id=? AND status IN ('waiting', 'uploading')",
                        (f"大卡片整体失败，该文件未完成: {err_msg[:200]}", task_id)
                    )
                    conn.commit()
                finally:
                    conn.close()
        except: pass
    finally:
        if append_success:
            # 【修复 Q1】区分"全部秒传"和"部分/全部上传"
            if all_skipped:
                final_msg = f'✅ 全部 {skipped_count_final} 个文件已在云端（秒传完成）'
            else:
                final_msg = '✅ 追加完成'
            _safe_update(task_id, {'percent': 100, 'status': 'done', 'msg': final_msg, 'speed': '0.0'})
            save_task_to_db(task_id)
            auto_mode = cfg_get('auto_fetch_after_upload', 'lazy')
            if auto_mode == 'immediate':
                _fetch_and_cache_ia_files(identifier, force=False)

            # 【B17 - 2026-11】成功时清理 f_path（释放磁盘）
            try:
                if file_infos:
                    fp = os.path.abspath(file_infos[0]['fp'])
                    temp_root = os.path.abspath(TEMP_UPLOAD_DIR)
                    if fp.startswith(temp_root + os.sep):
                        rel_to_root = os.path.relpath(fp, temp_root)
                        top_dir_name = rel_to_root.split(os.sep)[0]
                        target_clean = os.path.join(temp_root, top_dir_name)
                        if target_clean != temp_root and os.path.isdir(target_clean):
                            cleanup_temp_dir(target_clean)
            except Exception as e:
                logging.warning(f"追加完成后清理临时文件失败: {e}")

        if slot_held:
            try: CONCURRENCY_LIMITER.release()
            except: pass