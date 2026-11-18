# netdisk_workers_upload.py
# 上传主线程 / 单文件重试 / 上传父任务进度更新
# 【修复 Q1】delete_filenames 移到 fetch cloud 之前
# 【修复 Q2】_check_token_and_pause 只等 paused
# 【修复 R1-副作用】BI patch 从 not in ('paused','waiting','error') 改回 not in ('paused','error')
# 【修复 R1-兜底】主循环 waiting 分支加兜底：slot_held=True 时强制改 uploading
# 【改进点 AB】cloud_files 拉取 + local_md5_cache 计算从 acquire 内移到 acquire 前
# 【改进点 BG】upload_worker 主循环 ia_retries 改为 0
# 【改进点 BH】准备阶段（delete/fetch/md5）加 token 检查 + 暂停等待
# 【改进点 BI】跳过文件后的 _safe_update 加 paused/error 保护
# 【A1f】_NON_RETRYABLE_KEYWORDS 加 3 个不可重试关键词
# 【B2-后端】upload_worker 失败分支把 sub_tasks 的 waiting/uploading 标记为 error
# 【B17】finally 块加注释说明保留 f_path
# 【连接泄漏修复】主 except 分支 conn.close() 用 try/finally 保护
# 【🔴 P0-16】_upload_large_file_s3 调用传 metadata=meta_dict + 捕获 2 个异常类
# 【🔴 P0-17】暂停时 task 被误标记为「上传完成」
# 【🔴 P0-19】identifier 不合规时浪费 30 秒重试
# 【🔴 P0-22】主循环 1.5→0.5 + 速度滑动平均
# 【🔴 P0-23】retry_single_file_worker 进度条从头开始 + 跳度大
# 【🔴 P0-24】retry_single_file_worker 暂停后无法再重试（死代码，G-59）
# 【🔴 P0-25】多个 retry_single_file_worker 并发时进度条回退
# 【🔴 P0-26】upload_worker 主循环与 _progress_updater 交叉回退
# 【🔴 P0-27】_last_reported_bytes 始终更新 + _progress_updater 不覆盖 total
# 【🔴 P0-36 修复 - 2026-11】见历史
# 【🔴 G-94 + G-95 修复 - 2026-11】见历史
# 【🔴 G-162 修复 - 2026-11】见历史
# 【🔴 G-164 修复 - 2026-11】见历史
# 【🔴 G-165 已回滚 - 2026-11】见历史
# 【🔴 G-176 修复 - 2026-11】见历史
# 【🔴 G-59 第 2 轮修复 - 2026-11】见历史
#
# 【⏱️ 延迟与超时精细控制 - 2026-11 第 5/20 步】
# 接入 2 个新配置项（全部 cfg_get 读取 + 5 秒 TTL 缓存）：
#   1. upload_poll_interval (默认 0.5，0.1-10.0 秒) → 主循环 sleep / _progress_updater 轮询
#   2. upload_file_retry    (默认 5，0-20 次)       → upload_one_file 的 max_file_retries
#
# 【BUG 修复 - 2026-11】upload_file_retry = 0 时文件被"假成功"
#   修复：max_file_retries = max(1, _get_upload_file_retry())
#
# 【🔴 R321 修复 - 2026-11（同源类问题统一修复第 5/5 个文件 - 第 1 轮）】
#   retry_single_file_worker 在 os.path.isfile(real_fp_check) 检查之后、
#   CONCURRENCY_LIMITER.acquire() 之前，重新 os.path.getsize(real_fp_check)
#   取实时大小。若新大小 != 传入 sz，用新大小并记录 warning。
#
# 【🔴 R389 修复 - 2026-11（同源类问题统一修复第 5/5 个文件 - 第 2 轮）】
#   R321 更新 sz 后追加同步 sub_tasks.file_size = sz，让 _progress_updater /
#   _update_parent_task_progress 用新值算 pct，避免"100% 了还在传"。
#
# 【🔴 R407 修复 - 2026-11（R390 配套改进 - 本轮）】
#   问题：R390 在 netdisk_utils_upload.py 的 _upload_large_file_s3 引入文件大小
#        变更检查（2 处抛 Exception）。但 netdisk_workers_upload.py 的
#        _NON_RETRYABLE_KEYWORDS 未包含这两个错误关键词 → upload_worker 场景
#        （upload_one_file）走普通重试路径：
#          _is_non_retryable_error(e) = False → attempt += 1 → 重试
#        → 5 次尝试（4 次重试 × 3 秒等待 = 12 秒）后才标 error
#   用户视角：上传中修改了文件，第一次 attempt 立即失败，但前端"重试中"12 秒后
#            才显示 error（浪费用户时间 + 让用户误以为有网络问题）
#   修复：_NON_RETRYABLE_KEYWORDS 加 2 个关键词（精确匹配 R390 抛出文本的最独特片段）：
#     - '文件大小在上传前已变更'（R390 检查 1 抛出文本前半段）
#     - '文件可能在上传过程中被修改'（R390 检查 2 抛出文本后半段）
#   为什么不直接用 '文件分片'：_is_non_retryable_error 用 str(e).lower() 做
#        子串匹配，'文件分片' 太宽泛，可能误伤 S3 内部错误（"分片 N 重试 M 次仍失败"
#        虽然不含"文件分片"四个连字，但语义接近）。用 R390 消息里最独特的两段文本，
#        精确匹配。
#   影响范围：
#     - upload_worker 的 upload_one_file：遇到 R390 抛出的错误立即标 error（省 12 秒）
#     - retry_single_file_worker：本来就不检查 _is_non_retryable_error（直接标 error），
#       不受影响
#   为什么这是安全的：
#     - 关键词匹配失败（未来 R390 消息文案改了）→ 退回原行为（重试 5 次后标 error）
#       → 功能正确，只是慢 12 秒
#     - 关键词误匹配（其他错误消息含这 14 个字）→ 立即标 error
#       → 用户手动重试，不会丢数据（文件完整在本地）
#
# 【📝 保留记录 - 2026-11，低优先，以后修】
#   R387：upload_one_file 的 total_size_bytes 是 os.walk 快照，文件大小变更时
#        主卡片 pct 会 >100%（G-162 已加 min(100.0, ...) 兜底）
#   R388：_restart_upload_retries 抢占后 worker 启动失败无回滚（Thread.start() 几乎不失败）
#   R391：_progress_updater 的 total_sz 在 file_size 为 None 时当 0
#   R392：worker 拿到槽位后到 _upload_large_file_s3 真正开始之间文件仍可能被修改
#        （R390 已在内层检查）
#   R393：upload_worker 的 upload_one_file 的 G-95 未同步 sub_tasks.file_size
#        （与 R389 同源，但 upload_worker 的 pct 用 total_size_bytes 不受影响）
#   R408：_upload_large_file_s3 检查 2 的 _expected_len 计算隐式依赖 total_parts
#        和 offset 公式（当前注释已说明）
#   R409：R390 检查 1/2 抛普通 Exception 而非自定义 _FileSizeChangedException。
#        当前用消息文本匹配（_is_non_retryable_error），若未来文案更新需同步更新关键词。
#        改进方向：引入自定义异常类（跨文件改造，工作量大，暂不修）
#
# 【📝 同源问题记录 - 其他文件，后续统一修】
#   - 全部已知同源问题已修复完毕
#     （R340/R345/R351/R356/R361/R362/R321/R389/R320/R322/R334/R335/R376/R379/R390/R407）

import os
import re
import uuid
import threading
import time
import collections
import random
import hashlib
import logging
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
    _fetch_and_cache_ia_files, _delete_ia_file, _upload_large_file_s3,
    ProgressFileWrapper, _opts_to_metadata, get_ssl_verify,
    save_task_to_db, cleanup_temp_dir,
)
from netdisk_utils_upload import (
    _UploadPausedException,
    _UploadExpiredException,
)


# ========== 【⏱️ 延迟与超时精细控制】2 个带 5 秒 TTL 缓存的读取函数 ==========
# 背景：主循环每 0.5 秒读一次 poll_interval，进度线程也读。
#      避免每个 worker 每次循环都读 config.json，加 TTL 缓存。
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
    """返回上传单文件最大尝试次数（含首次尝试），默认 5，范围 0-20 次。

    语义说明（重要）：
      - 这是「最大尝试次数」而非「重试次数」
      - 0 → 调用方 max(1, ...) 兜底为 1 次尝试（不重试），避免完全不上传
      - 5 → 5 次尝试 = 1 次初始 + 4 次重试（原默认行为）
      - 20 → 20 次尝试 = 1 次初始 + 19 次重试
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


_IDENTIFIER_RE = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9_.-]{4,100}$')


def _is_valid_identifier(identifier):
    """检查 identifier 是否符合 IA 官方规则。"""
    if not identifier:
        return False
    try:
        return bool(_IDENTIFIER_RE.match(identifier))
    except Exception:
        return False


_NON_RETRYABLE_KEYWORDS = [
    'malware', 'virus', 'invalid identifier', 'already exists',
    'not authorized', 'access denied', 'too large', 'size limit',
    'unsupported', 'empty file', 'is not allowed', 'not permitted',
    'quota exceeded', 'identifier is already in use',
    'bucket must be in dns hostname',
    'malformedpostrequest',
    'signaturedoesnotmatch',
    'bucket is not valid',
    'specified bucket is not valid',
    'bucket names should be valid',
    'must be a valid identifier',
    # 【R407 修复 - 2026-11】R390 引入的文件大小变更错误，立即失败不重试
    #   精确匹配 netdisk_utils_upload.py 的 _upload_large_file_s3 抛出的文本
    '文件大小在上传前已变更',  # R390 检查 1
    '文件可能在上传过程中被修改',  # R390 检查 2
]
_RATE_LIMIT_HINTS = [
    "reduce your request rate", "bucket_tasks_queued",
    "too many requests", "429", "503", "slow down", "rate limit",
    "temporarily unavailable", "try again later",
]


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


def _make_ia_files_param(rel_path, pf, sz, md5_hex, sha1_hex):
    if md5_hex and sha1_hex and _IAFile is not None:
        try:
            f = _IAFile(rel_path, fileobj=pf, size=sz, md5=md5_hex, sha1=sha1_hex)
            return {rel_path: f}
        except Exception as ex:
            logging.warning(f"[上传] 构造 ia.File 失败，回退简单格式: {ex}")
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


def _safe_worker_mark_error(task_id, rel_path, err_msg):
    """【P0-36 / G-56 配套】worker 内部标记 sub_tasks error 的辅助函数，
    统一包 try/except，防 DB 异常崩溃 worker。"""
    try:
        with DB_LOCK:
            conn = get_db()
            try:
                conn.execute(
                    "UPDATE sub_tasks SET status='error', error_msg=? WHERE task_id=? AND file_path=?",
                    (err_msg, task_id, rel_path)
                )
                conn.commit()
            finally:
                conn.close()
    except Exception as e:
        logging.warning(f"[worker] 标记 sub_tasks error 失败 (task={task_id}, file={rel_path}): {e}")


def _safe_update_parent(task_id):
    """【P0-36 / G-56 配套】worker 内部调用 _update_parent_task_progress 的
    安全包装，防 DB 异常崩溃 worker。"""
    try:
        _update_parent_task_progress(task_id)
    except Exception as e:
        logging.warning(f"[worker] _update_parent_task_progress 异常 (task={task_id}): {e}")


def upload_worker(task_id, item, delete_filenames=None, opts=None, batch_total=1, batch_done=0):
    if opts is None: opts = {}

    if task_id not in UPLOAD_TASKS:
        return

    db_id, f_path, identifier = item[0], item[12], item[2]

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

    if not f_path or not os.path.exists(f_path):
        _safe_update(task_id, {'status': 'error', 'msg': '❌ 临时文件已被清理，请重新上传'})
        save_task_to_db(task_id)
        try:
            with DB_LOCK:
                conn = get_db()
                try:
                    conn.execute("UPDATE items SET status='error', error_msg=? WHERE id=?", ("临时文件已被清理", db_id))
                    conn.commit()
                finally:
                    conn.close()
        except: pass
        return

    total_files_count = 0
    total_size_bytes = 0
    file_sizes_map = {}
    file_paths = []
    if os.path.isdir(f_path):
        for root, dirs, fs in os.walk(f_path):
            for file in fs:
                fp = os.path.join(root, file)
                rel = os.path.relpath(fp, f_path).replace('\\', '/')
                try:
                    sz = os.path.getsize(fp)
                    total_files_count += 1
                    total_size_bytes += sz
                    file_sizes_map[rel] = sz
                    file_paths.append((fp, rel))
                except: pass
    elif os.path.isfile(f_path):
        total_files_count = 1
        try:
            total_size_bytes = os.path.getsize(f_path)
            file_sizes_map[os.path.basename(f_path)] = total_size_bytes
            file_paths.append((f_path, os.path.basename(f_path)))
        except: pass

    my_worker_token = uuid.uuid4().hex

    _existing_at_entry = UPLOAD_TASKS.get(task_id)
    _created_at_val = _existing_at_entry.get('created_at', time.time()) if _existing_at_entry else time.time()

    _safe_update(task_id, {
        'total_files_count': total_files_count,
        'total_size_bytes': total_size_bytes,
        'files_uploaded': 0,
        'uploaded_bytes': 0,
        'status': 'waiting', 'msg': '准备中...', 'percent': 0, 'speed': '0.0',
        'current': '0', 'total': str(total_files_count), 'eta': '', 'raw_line': '',
        'batch_total': batch_total, 'batch_done': batch_done,
        'created_at': _created_at_val,
        '_worker_token': my_worker_token,
        '_last_reported_bytes': 0,
    })
    save_task_to_db(task_id)

    slot_held = False
    upload_success = False
    try:
        if not UPLOAD_TASKS.get(task_id): return

        if len(item) > 14:
            db_id, f_id, identifier, title, creator, desc, date, media, coll, lang, lic, tags, f_path, f_name, custom_date = item[:15]
        else:
            db_id, f_id, identifier, title, creator, desc, date, media, coll, lang, lic, tags, f_path, f_name = item[:14]
            custom_date = ''

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

        if delete_filenames:
            _safe_update(task_id, {'msg': '官方引擎删除旧文件中...'})
            save_task_to_db(task_id)
            for fn in delete_filenames:
                if not _check_token_and_pause():
                    return
                _delete_ia_file(identifier, fn)
                time.sleep(cfg_get('delete_file_sleep', 2.0))

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

        local_md5_cache = {}
        if not opts.get('overwrite_same_file') and cloud_files:
            for fp, rel_path in file_paths:
                if not _check_token_and_pause():
                    return
                sz = file_sizes_map[rel_path]
                cloud_info = cloud_files.get(rel_path)
                if not cloud_info:
                    continue
                try:
                    cloud_sz = int(str(cloud_info.get('size', '') or '').strip() or 0)
                except Exception:
                    cloud_sz = 0
                cloud_md5 = (cloud_info.get('md5') or '').strip()
                if cloud_sz > 0 and cloud_sz == sz and cloud_md5 and '-' not in cloud_md5:
                    local_md5 = _compute_md5(fp)
                    if local_md5:
                        local_md5_cache[rel_path] = local_md5

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
                if batch_done == 0:
                    conn.execute("DELETE FROM sub_tasks WHERE task_id=?", (task_id,))
                for fp, rel_path in file_paths:
                    sz = file_sizes_map[rel_path]
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
                        row = conn.execute("SELECT status FROM sub_tasks WHERE task_id=? AND file_path=?", (task_id, rel_path)).fetchone()
                        if not row or row[0] in ('waiting', 'error'):
                            conn.execute('''INSERT OR REPLACE INTO sub_tasks (task_id, file_path, file_size, status, uploaded_bytes)
                                            VALUES (?, ?, ?, 'waiting', 0)''', (task_id, rel_path, sz))
                            files_to_upload.append((fp, rel_path))
                conn.commit()
            finally:
                conn.close()

        patch = {
            'files_uploaded': skipped_count,
            'uploaded_bytes': skipped_size,
            'percent': round((skipped_size / total_size_bytes) * 100, 1) if total_size_bytes > 0 else 0,
            'msg': f'跳过 {skipped_count} 个云端已有文件，开始上传...',
            '_last_reported_bytes': skipped_size,
        }
        t_now = UPLOAD_TASKS.get(task_id)
        if t_now is not None and t_now.get('status') not in ('paused', 'error'):
            patch['status'] = 'uploading'
        _safe_update(task_id, patch)
        save_task_to_db(task_id)

        if not files_to_upload:
            upload_success = True
        else:
            meta_dict = {"title": title, "mediatype": media or "software", "collection": coll or "opensource"}
            if creator: meta_dict["creator"] = creator
            if desc: meta_dict["description"] = desc
            if date: meta_dict["date"] = date
            if custom_date: meta_dict["x-archive-meta-date"] = custom_date
            if lang: meta_dict["language"] = lang
            if lic: meta_dict["licenseurl"] = lic
            if tags: meta_dict["subject"] = tags
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
            _append_log(history_lines, f"已启动 {conc_threads} 个并发上传线程...")
            failed_files = []

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

            def upload_one_file(fp, rel_path):
                nonlocal uploaded_bytes
                # 【⏱️ 延迟与超时精细控制】单文件最大尝试次数读配置（默认 5，0-20）
                # 【BUG 修复 - 2026-11】max(1, ...) 兜底：用户填 0 时至少尝试 1 次
                #   否则 while attempt < 0 循环不执行 → 文件"假成功" → items 误标 done
                max_file_retries = max(1, _get_upload_file_retry())
                # 【G-94 修复 - 2026-11】TOCTOU 防护（与 retry_single_file_worker 的 G-93 对齐）
                try:
                    real_fp = os.path.realpath(fp)
                except (OSError, ValueError) as e:
                    logging.warning(f"[上传] realpath 失败: fp={fp!r}, error={e}")
                    _safe_worker_mark_error(task_id, rel_path, f"路径解析失败: {e}")
                    return False

                if real_fp != fp:
                    logging.warning(f"[上传] 路径被篡改（TOCTOU）: fp={fp!r}, realpath={real_fp!r}")
                    _safe_worker_mark_error(task_id, rel_path, "路径被篡改（可能是符号链接攻击）")
                    return False

                if not os.path.isfile(real_fp):
                    logging.warning(f"[上传] 文件不存在或不是普通文件: {fp!r}")
                    _safe_worker_mark_error(task_id, rel_path, "文件不存在或不是普通文件")
                    return False

                # 【G-95 修复 - 2026-11】sz 重新取，防 file_sizes_map 缓存值过期
                try:
                    sz = os.path.getsize(real_fp)
                except (OSError, ValueError) as e:
                    logging.warning(f"[上传] getsize 失败: {real_fp!r}, error={e}")
                    _safe_worker_mark_error(task_id, rel_path, f"读取文件大小失败: {e}")
                    return False

                cached_sz = file_sizes_map.get(rel_path, 0)
                if cached_sz != sz:
                    logging.warning(f"[上传] 文件大小变更: {rel_path} {cached_sz}→{sz}（用最新值）")

                attempt = 0
                while attempt < max_file_retries:
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
                        if interrupted:
                            continue
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
                            _upload_large_file_s3(identifier, rel_path, real_fp, sz, opts, task_id,
                                                  expected_worker_token=my_worker_token,
                                                  progress_callback=_s3_progress_cb,
                                                  metadata=meta_dict)
                        else:
                            md5_hex = local_md5_cache.get(rel_path)
                            if md5_hex:
                                sha1_hex = _compute_sha1(real_fp)
                                if not sha1_hex:
                                    md5_hex, sha1_hex = _compute_md5_sha1(real_fp)
                            else:
                                md5_hex, sha1_hex = _compute_md5_sha1(real_fp)

                            with ProgressFileWrapper(real_fp, rel_path, sz, file_progress_map, lock) as pf:
                                files_param = _make_ia_files_param(rel_path, pf, sz, md5_hex, sha1_hex)
                                ia.upload(identifier, files=files_param, metadata=meta_dict, retries=ia_retries, verbose=False, verify=get_ssl_verify())

                        with lock:
                            file_progress_map.pop(rel_path, None)
                            uploaded_set.add(rel_path)
                            uploaded_bytes += sz
                            elapsed = time.time() - start_time
                            speed = uploaded_bytes / elapsed if elapsed > 0 else 0
                            # 【G-162 修复】pct 加 min(100, ...) 兜底
                            _pct_raw = (uploaded_bytes / total_size_bytes) * 100 if total_size_bytes > 0 else 0
                            pct = round(min(100.0, _pct_raw), 1)
                            log_line = f"✅ [{len(uploaded_set)+skipped_count}/{total_files_count}] {rel_path} ({sz/1024/1024:.1f}MB)"
                            _append_log(history_lines, log_line)
                            _snap_pct = pct
                            # 【G-165 已回滚】_snap_uploaded_bytes 恢复为原始值
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
                            _append_log(history_lines, f"▶️ [{rel_path}] 恢复上传，从断点继续...")
                            _safe_update(task_id, {'raw_line': "\n".join(history_lines)})
                        save_task_to_db(task_id)
                        continue

                    except _UploadExpiredException as e:
                        with lock:
                            _append_log(history_lines, f"❌ [{rel_path}] uploadId 失效: {e}")
                            failed_files.append(rel_path)
                            _safe_update(task_id, {'raw_line': "\n".join(history_lines)})
                        save_task_to_db(task_id)
                        _safe_worker_mark_error(task_id, rel_path, f"uploadId 失效: {str(e)[:200]}")
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
                            _safe_worker_mark_error(task_id, rel_path, f"不可重试: {str(e)[:200]}")
                            return False

                        err_msg = str(e).lower()
                        is_rate_limit = any(x in err_msg for x in ["reduce your request rate", "bucket_tasks_queued", "too many requests", "403", "429", "503"])
                        if is_rate_limit:
                            with UPLOAD_COOLDOWN_LOCK:
                                netdisk_state_locks.GLOBAL_UPLOAD_COOLDOWN = time.time() + cfg_get('ia_429_backoff', 60)
                            with lock:
                                _append_log(history_lines, f"⚠️ [{rel_path}] 触发IA风控，自动进入60秒冷却重试 ({attempt+1}/{max_file_retries})...")
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
                            if interrupted:
                                continue
                        else:
                            if attempt == max_file_retries - 1:
                                with lock:
                                    _append_log(history_lines, f"❌ [{rel_path}] 失败: {e}")
                                    failed_files.append(rel_path)
                                    _safe_update(task_id, {'raw_line': "\n".join(history_lines)})
                                    save_task_to_db(task_id)
                                _safe_worker_mark_error(task_id, rel_path, str(e))
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
                            if interrupted:
                                continue

                    attempt += 1
                return False

            _safe_update(task_id, {'raw_line': "\n".join(history_lines)})
            save_task_to_db(task_id)
            executor = ThreadPoolExecutor(max_workers=conc_threads)
            futures = {executor.submit(upload_one_file, fp, rel): rel for fp, rel in files_to_upload}

            while True:
                # 【⏱️ 延迟与超时精细控制】主循环 sleep 读配置
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
                                t['msg'] = '▶️ 已恢复，继续上传...'
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

                _last_rb = t.get('_last_reported_bytes', 0)
                cur_uploaded_now = t.get('uploaded_bytes', 0) or 0
                if real_uploaded_bytes < _last_rb:
                    real_uploaded_bytes = _last_rb
                if real_uploaded_bytes < cur_uploaded_now:
                    real_uploaded_bytes = cur_uploaded_now
                t['_last_reported_bytes'] = real_uploaded_bytes

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
                # 【G-162 修复】主循环 pct 加 min(100, ...) 兜底
                _pct_raw = (real_uploaded_bytes / total_size_bytes) * 100 if total_size_bytes > 0 else 0
                pct = round(min(100.0, _pct_raw), 1)
                active_str = ""
                if current_active:
                    parts = []
                    for i, (rel_path, (curr, total)) in enumerate(current_active.items()):
                        if i >= 3:
                            parts.append(f"等{len(current_active)}个文件")
                            break
                        name = os.path.basename(rel_path)
                        parts.append(f"{name} ({curr/1024/1024:.1f}MB/{total/1024/1024:.1f}MB)")
                    active_str = "⏳ 正在传: " + " | ".join(parts)
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
                raise Exception(f"有 {len(failed_files)} 个文件上传失败，请展开队列查看详情。")
            upload_success = True

        if upload_success:
            _safe_update(task_id, {
                'percent': 100,
                'msg': '✅ 上传完成（如标题/作者等元数据异常，请点「✏️ 编辑」重新保存）',
                'speed': '0.0'
            })
            save_task_to_db(task_id)
            with DB_LOCK:
                conn = get_db()
                try:
                    conn.execute("UPDATE items SET status='done', item_url=?, error_msg=NULL WHERE id=?", (f"https://archive.org/details/{identifier}", db_id))
                    conn.execute("DELETE FROM file_cache WHERE identifier=?", (identifier,))
                    conn.commit()
                finally:
                    conn.close()
            auto_mode = cfg_get('auto_fetch_after_upload', 'lazy')
            if auto_mode == 'immediate':
                _safe_update(task_id, {'msg': '等待 IA 处理完成后拉取文件列表...'})
                save_task_to_db(task_id)
                time.sleep(cfg_get('upload_post_sleep', 15))
                _fetch_and_cache_ia_files(identifier, force=False)
                _safe_update(task_id, {'status': 'done', 'msg': '✅ 全部完成！'})
            else:
                _safe_update(task_id, {'status': 'done', 'msg': '✅ 上传完成（点「📂文件」查看文件列表）'})
            save_task_to_db(task_id)

    except Exception as e:
        err_msg = str(e)
        _safe_update(task_id, {'status': 'error', 'msg': f'❌ 失败: {err_msg}'})
        save_task_to_db(task_id)
        try:
            with DB_LOCK:
                conn = get_db()
                try:
                    conn.execute(
                        "UPDATE sub_tasks SET status='error', error_msg=? WHERE task_id=? AND status IN ('waiting', 'uploading')",
                        (f"大卡片整体失败，该文件未完成: {err_msg[:200]}", task_id)
                    )
                    conn.execute("UPDATE items SET status='error', error_msg=? WHERE id=?", (err_msg, db_id))
                    conn.commit()
                finally:
                    conn.close()
        except: pass
    finally:
        if upload_success and f_path and os.path.abspath(f_path).startswith(os.path.abspath(TEMP_UPLOAD_DIR) + os.sep):
            try: cleanup_temp_dir(f_path)
            except: pass
        if slot_held:
            try: CONCURRENCY_LIMITER.release()
            except: pass


def retry_single_file_worker(task_id, identifier, fp, rel_path, sz, meta_dict, opts):
    """单文件重试：独立任务，不传 expected_worker_token（默认 None，不检查 token）。

    【P0-23】传 progress_callback 给 _upload_large_file_s3，起后台线程更新主卡片进度。
    【P0-25】_progress_updater 更新前对比 current_uploaded，防并发进度回退。
    【P0-27-2】_progress_updater 改用 _safe_update_monotonic + 不覆盖 total。
    【P0-36 / G-93】TOCTOU 防护：worker 开头做 realpath + isfile 双重检查
    【P0-36 / Bug A】_is_valid_identifier 分支里的 _update_parent_task_progress 加保护
    【G-59 第 2 轮 - 2026-11】_upload_large_file_s3 调用传 check_paused_task_id=task_id
    【G-164 - 2026-11】_progress_updater 的 pct 加 min(100.0, ...) 兜底

    【R321 修复 - 2026-11】重新 os.path.getsize(real_fp_check) 取实时大小。
      原因：调用方传入的 sz 可能已过期（用户改文件/断电重启后重试），
      导致 _upload_large_file_s3 按旧 sz 算 total_parts → 上传不完整或空分片。
      与 upload_worker 的 upload_one_file 的 G-95 修复对齐。

    【R389 修复 - 2026-11】R321 更新 sz 后同步 sub_tasks.file_size，让
      _progress_updater / _update_parent_task_progress 用新值算 pct，
      避免"100% 了还在传"的困惑。
    """
    if not _is_valid_identifier(identifier):
        err_msg = (
            f'identifier 不合规（当前值: {identifier!r}）：'
            f'需 5-102 字符，首字母或数字开头，只能包含字母、数字、_、.、-'
        )
        _safe_worker_mark_error(task_id, rel_path, err_msg)
        _safe_update_parent(task_id)
        return

    # 【P0-36 / G-93】TOCTOU 防护
    try:
        real_fp_check = os.path.realpath(fp)
    except (OSError, ValueError) as e:
        logging.warning(f"[单文件重试] realpath 失败: {fp}: {e}")
        _safe_worker_mark_error(task_id, rel_path, f"路径解析失败: {e}")
        _safe_update_parent(task_id)
        return

    if real_fp_check != fp:
        logging.warning(f"[单文件重试] 路径被篡改（TOCTOU）: fp={fp!r}, realpath={real_fp_check!r}")
        _safe_worker_mark_error(task_id, rel_path, "路径被篡改（可能是符号链接攻击）")
        _safe_update_parent(task_id)
        return

    if not os.path.isfile(real_fp_check):
        logging.warning(f"[单文件重试] 文件不存在或不是普通文件: {fp}")
        _safe_worker_mark_error(task_id, rel_path, "文件不存在或不是普通文件")
        _safe_update_parent(task_id)
        return

    # 【R321 修复 - 2026-11】重新取实时大小（防调用方传入的 sz 过期）
    # 场景：用户点"重试"后、worker 真正开始前修改了文件；
    #      _restart_upload_retries 抢占后用户改文件；
    #      断电重启后重试时 sz 是旧值。
    try:
        _real_sz = os.path.getsize(real_fp_check)
    except (OSError, ValueError) as e:
        logging.warning(f"[单文件重试] getsize 失败: {real_fp_check!r}, error={e}")
        _safe_worker_mark_error(task_id, rel_path, f"读取文件大小失败: {e}")
        _safe_update_parent(task_id)
        return
    if _real_sz != sz:
        logging.warning(
            f"[单文件重试] 文件大小变更: {rel_path} {sz}→{_real_sz}（用最新值）"
        )
        sz = _real_sz
        # 【R389 修复 - 2026-11】同步 sub_tasks.file_size，让 _progress_updater 的
        # total_sz 与实际上传大小一致（否则 pct 按旧值算，用户看到"100% 了还在传"）。
        # DB 失败只记 warning，不影响主流程（用新 sz 上传，pct 仍偏差但文件完整）。
        try:
            with DB_LOCK:
                conn = get_db()
                try:
                    conn.execute(
                        "UPDATE sub_tasks SET file_size=? WHERE task_id=? AND file_path=?",
                        (sz, task_id, rel_path)
                    )
                    conn.commit()
                finally:
                    conn.close()
        except Exception as e:
            logging.warning(f"[单文件重试] 同步 sub_tasks.file_size 失败 ({rel_path}): {e}")

    def _should_stop_waiting():
        return UPLOAD_TASKS.get(task_id) is None

    slot_held = False
    try:
        CONCURRENCY_LIMITER.acquire(timeout=86400, should_stop=_should_stop_waiting)
        slot_held = True
    except Exception:
        return

    stop_event = threading.Event()

    def _progress_updater():
        speed_samples = collections.deque(maxlen=20)
        while not stop_event.wait(_get_upload_poll_interval()):
            try:
                with DB_LOCK:
                    conn = get_db()
                    try:
                        rows = conn.execute(
                            "SELECT file_size, uploaded_bytes, status FROM sub_tasks WHERE task_id=?",
                            (task_id,)
                        ).fetchall()
                    finally:
                        conn.close()

                if not rows:
                    continue

                total_sz = sum((r[0] or 0) for r in rows)
                progress_sz = sum(
                    (r[1] or 0) if r[2] in ('done', 'skipped', 'uploading') else 0
                    for r in rows
                )
                done_cnt = sum(1 for r in rows if r[2] in ('done', 'skipped'))
                total_cnt = len(rows)

                t = UPLOAD_TASKS.get(task_id)
                if t is not None and t.get('status') not in ('paused', 'error'):
                    cur_uploaded = t.get('uploaded_bytes', 0) or 0
                    if progress_sz < cur_uploaded:
                        progress_sz = cur_uploaded
                    cur_done = t.get('files_uploaded', 0) or 0
                    if done_cnt < cur_done:
                        done_cnt = cur_done
                    # 【G-164 修复 - 2026-11】pct 加 min(100.0, ...) 兜底，与 G-162 对齐。
                    _pct_raw = (progress_sz / total_sz) * 100 if total_sz > 0 else 0
                    pct = round(min(100.0, _pct_raw), 1)

                    now_ts = time.time()
                    speed_samples.append((now_ts, progress_sz))
                    while speed_samples and now_ts - speed_samples[0][0] > 10:
                        speed_samples.popleft()
                    if len(speed_samples) >= 2:
                        ts0, b0 = speed_samples[0]
                        ts1, b1 = speed_samples[-1]
                        dt = ts1 - ts0
                        speed_mb = ((b1 - b0) / dt / 1024 / 1024) if dt > 0 and b1 >= b0 else 0.0
                    else:
                        speed_mb = 0.0

                    _safe_update_monotonic(task_id, {
                        'percent': pct,
                        'uploaded_bytes': progress_sz,
                        'files_uploaded': done_cnt,
                        'current': str(done_cnt),
                        'speed': f"{speed_mb:.2f} MB/s",
                    }, monotonic_key='uploaded_bytes')
            except Exception as e:
                logging.warning(f"[retry_single_file 进度线程] 异常: {e}")

    _progress_thread = threading.Thread(target=_progress_updater, daemon=True)
    _progress_thread.start()

    _last_db_write = [0.0]

    def _progress_cb(rel_p, uploaded_so_far, total_bytes):
        # 【硬编码保留】这是「写 DB 节流」，与「进度可视化刷新」解耦。
        # 若改为用户配置的 10 秒，断点续传时进度会大幅丢失。保留 0.5 秒硬编码。
        now = time.time()
        if now - _last_db_write[0] < 0.5:
            return
        _last_db_write[0] = now
        try:
            with DB_LOCK:
                conn = get_db()
                try:
                    conn.execute(
                        "UPDATE sub_tasks SET uploaded_bytes=? WHERE task_id=? AND file_path=?",
                        (uploaded_so_far, task_id, rel_p)
                    )
                    conn.commit()
                finally:
                    conn.close()
        except Exception as e:
            logging.warning(f"[retry_single_file progress_cb] 写 sub_tasks 失败: {e}")

    try:
        with DB_LOCK:
            conn = get_db()
            try:
                conn.execute("UPDATE sub_tasks SET status='uploading', error_msg=NULL WHERE task_id=? AND file_path=?", (task_id, rel_path))
                conn.commit()
            finally:
                conn.close()
        with UPLOAD_COOLDOWN_LOCK:
            cooldown = netdisk_state_locks.GLOBAL_UPLOAD_COOLDOWN
        now = time.time()
        if now < cooldown: time.sleep(cooldown - now + random.uniform(0.1, 1.0))
        chunk_threshold = opts.get('chunk_threshold', 200) * 1024 * 1024
        if sz >= chunk_threshold:
            # 【G-59 第 2 轮 修复 - 2026-11】传 check_paused_task_id=task_id
            _upload_large_file_s3(identifier, rel_path, fp, sz, opts, task_id,
                                  progress_callback=_progress_cb,
                                  check_paused_task_id=task_id)
        else:
            try:
                ia_retries = max(0, min(int(opts.get('retries') or 5), 10))
            except: ia_retries = 5
            md5_hex, sha1_hex = _compute_md5_sha1(fp)
            with open(fp, 'rb') as fh:
                files_param = _make_ia_files_param(rel_path, fh, sz, md5_hex, sha1_hex)
                ia.upload(identifier, files=files_param, metadata=meta_dict, retries=ia_retries, verbose=False, verify=get_ssl_verify())
        with DB_LOCK:
            conn = get_db()
            try:
                conn.execute("UPDATE sub_tasks SET status='done', uploaded_bytes=? WHERE task_id=? AND file_path=?", (sz, task_id, rel_path))
                conn.commit()
            finally:
                conn.close()
        _safe_update_parent(task_id)
    except _UploadPausedException:
        logging.info(f"[单文件重试] 用户暂停，改 sub_tasks.error 让用户可继续: {rel_path}")
        _safe_worker_mark_error(task_id, rel_path, "用户暂停，点击「🔄 单文件重试」可继续（已上传分片保留）")
        _safe_update_parent(task_id)
    except Exception as e:
        _safe_worker_mark_error(task_id, rel_path, str(e))
        _safe_update_parent(task_id)
    finally:
        stop_event.set()
        if slot_held:
            try: CONCURRENCY_LIMITER.release()
            except: pass


def _update_parent_task_progress(task_id):
    """更新父任务进度。

    【P0-36 / G-56】整函数 try/except 兜底，get_db() 异常时不再崩溃 worker
    【P0-36 / G-62】paused 优先
    【G-176 修复 - 2026-11】msg 后缀"（原任务暂停状态已解除）"只在
                           new_status not in ('paused', 'waiting') 时添加
    """
    try:
        with DB_LOCK:
            conn = get_db()
            try:
                rows = conn.execute("SELECT status, file_size, uploaded_bytes, error_msg, file_path FROM sub_tasks WHERE task_id=?", (task_id,)).fetchall()
            finally:
                conn.close()
        if not rows: return
        total_sz = sum(r[1] for r in rows)
        done_sz = sum(r[2] for r in rows if r[0] in ('done', 'skipped'))
        done_cnt = sum(1 for r in rows if r[0] in ('done', 'skipped'))
        has_err = any(r[0] == 'error' for r in rows)
        all_done = all(r[0] in ('done', 'skipped', 'error') for r in rows)
        pct = round((done_sz / total_sz) * 100, 1) if total_sz > 0 else 0

        _failed_details = []
        if has_err:
            for r in rows:
                if r[0] == 'error':
                    _fp = r[4] or ''
                    _em = (r[3] or '未知错误').strip()
                    _failed_details.append(f"{_fp}: {_em}" if _fp else _em)

        t = UPLOAD_TASKS.get(task_id)
        if t is not None:
            current_status = t.get('status')
            _was_paused_or_waiting = current_status in ('paused', 'waiting')

            if current_status == 'paused':
                new_status = 'paused'
            elif all_done and not has_err:
                new_status = 'done'
            elif all_done and has_err:
                new_status = 'error'
            elif current_status == 'waiting':
                new_status = 'waiting'
            else:
                new_status = 'uploading'

            if all_done:
                if not has_err:
                    msg = '✅ 单文件重试完成'
                else:
                    msg = '⚠️ 部分文件仍有错误'
                # 【G-176 修复】只在状态确实从 paused/waiting 变为 done/error/uploading
                # 时才提示"原任务暂停状态已解除"。
                if _was_paused_or_waiting and new_status not in ('paused', 'waiting'):
                    msg += '（原任务暂停状态已解除）'
            else:
                msg = '✅ 单文件重试完成' if not has_err else '部分文件仍有错误'

            t.update({
                'percent': pct,
                'files_uploaded': done_cnt,
                'uploaded_bytes': done_sz,
                'status': new_status,
                'msg': msg
            })
            save_task_to_db(task_id)

            if all_done and not has_err:
                item_data = t.get('item_data')
                if item_data and len(item_data) > 2:
                    _db_id = item_data[0]
                    _identifier = item_data[2]
                    try:
                        with DB_LOCK:
                            conn2 = get_db()
                            try:
                                if has_err:
                                    if _failed_details:
                                        _real_err = ' | '.join(_failed_details[:3])
                                        if len(_failed_details) > 3:
                                            _real_err += f" ...(共 {len(_failed_details)} 个失败)"
                                    else:
                                        _real_err = '部分文件上传失败，请展开队列查看'
                                    conn2.execute("UPDATE items SET status='error', error_msg=? WHERE id=?", (_real_err, _db_id))
                                else:
                                    conn2.execute("UPDATE items SET status='done', item_url=?, error_msg=NULL WHERE id=?", (f"https://archive.org/details/{_identifier}", _db_id))
                                conn2.commit()
                            finally:
                                conn2.close()
                    except Exception as e:
                        logging.warning(f"⚠️ [_update_parent_task_progress] 同步 items 表 status 失败 (db_id={_db_id}): {e}")
    except Exception as e:
        logging.warning(f"⚠️ [_update_parent_task_progress] 整体异常 (task_id={task_id}): {e}")