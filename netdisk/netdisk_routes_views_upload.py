# netdisk_routes_views_upload.py
# 上传页 / 追加页 / 一键重试全部
# 【本次 - 任务4.1】upload() 支持 local_paths JSON 数组（兼容旧 local_path 单值）
# 【修复 18/25】is_multi 由用户意图决定
# 【改进点 B1/B6/B7】日志警告 + 全部无效列出前 5 个
# 【修复 Q】fn=None（空文件夹）主动拦截
# 【修复 R】IO 全部移出 DB_LOCK
# 【修复 R2/R3】item=None / vf 全过滤时清理 td
# 【修复 S】_safe_cleanup_td 兼容文件和目录
# 【改进点 B12】_parse_local_paths 加 os.path.expanduser 展开 ~
# 【改进点 B10】新增 _filter_nested_paths()，过滤被包含的子路径
# 【改进点 B5】upload() 多路径时 fn = "[N 个路径]"
# 【改进点 B9】append_files() 单路径文件夹展开（与 upload 一致），多路径保留顶层
# 【改进点 B18】upload() 锁内 DB 事务加 try/except
# 【改进点 B19】is_unzip 后重新扫描 fn，出空时清理
# 【改进点 B20】t.start() 包 try/except
# 【改进点 B21】is_unzip 解压后删除 zip
# 【修复 Q1】is_unzip 解压失败时不删除 zip（保留原始压缩包作为数据上传）
# 【修复 Q2】upload() 里 is_multi 用过滤后 len(valid_paths)
# 【修复 R1】_extract_zip_safe 返回 (ok, extracted_file_count)，只统计文件（不含目录）
#
# 【⏱️ 延迟与超时精细控制 - 2026-11 第 15/20 步】
# 新增 5 个带 5 秒 TTL 缓存的 getter，渲染 upload / append 页时传递给前端模板：
#   frontend_chunk_threshold_mb / frontend_chunk_size_mb / frontend_chunk_retries
#   frontend_chunk_retry_sleep_ms / upload_xhr_timeout_ms
#
# 【🔴 B 级第 5 次生成 - 2026-11】
# 新增 1 个 getter：_get_id_check_timeout()
#
# 【🔴 R414 修复 - 2026-11（中危 - 并发上传同一 identifier 冲突）】见历史
# 【🔴 R428 修复 - 2026-11（R414 第 1 个文件追加修复）】见历史
#
# 【🔴 R427 修复 - 2026-11（数据完整性 - 秒传覆盖正在上传的 items 记录 - 本轮）】
#   问题：upload() 的秒传分支（is_skip_upload and not ceid）用
#        `INSERT OR REPLACE INTO items ...` 覆盖同 identifier 的记录。
#        若该 identifier 正在上传中（items.status='uploading'，
#        file_path='/temp_xxx'，upload_worker 正在跑），会被秒传覆盖：
#        - INSERT OR REPLACE 先 DELETE 旧记录再 INSERT → file_path 丢失（NULL）
#        - tab1 的 upload_worker 用闭包旧 item_data 引用继续上传（文件上传完成）
#        - tab1 完成后 UPDATE items SET status='done' → 更新成功（identifier 一致）
#        - 但 items.file_path 保持 NULL → 该条目无法断点续传
#        - 用户重试该条目走"秒传"分支（因为 items.status='done'）→ 再次覆盖
#        若 tab1 上传到一半失败（items.status 被 except 分支改回 error）：
#        - 用户看到 error 想重试 → items.file_path 已是 NULL → 无法重试
#    触发场景（低概率但数据完整性隐患）：
#        - tab1：用户正常上传 identifier=X（启动 upload_worker）
#        - tab2：用户秒传同一 identifier=X（INSERT OR REPLACE 覆盖）
#        - 或：用户上传到一半失败后去秒传同一 identifier（也被覆盖）
#   修复：秒传分支开头加双重冲突检查：
#        1. UPLOAD_TASKS 里同 identifier 活跃 upload 任务（waiting/uploading/paused）
#           → 返回"已有上传任务进行中，不能秒传覆盖"
#        2. items 表里同 identifier 记录 status='uploading'
#           → 返回"数据库中有正在上传的记录（上次上传未完成），不能秒传覆盖"
#   为什么只拒绝 'uploading' 不拒绝 'done'/'error'/'pending'/'meta_pending'：
#        - 'done'：正常场景，秒传覆盖是用户预期行为（重新同步云端文件）
#        - 'error'：用户上传失败后秒传，也是预期（换用云端已有文件）
#        - 'pending'：未开始上传，秒传覆盖无数据风险
#        - 'meta_pending'：files 已上传完成，秒传覆盖会丢失 file_path 但 IA 文件已在云端
#          → 实际影响小。但为保险起见可考虑也拒绝。当前暂不拒绝（概率极低）。
#        - 'uploading'：文件正在上传，覆盖会导致 file_path 丢失 + upload_worker 与
#          秒传结果冲突 → 必须拒绝
#
# 【📝 保留记录 - 2026-11，低优先，以后修】
#   R424：R414 检查有微秒级竞态窗口（检查时无冲突 → INSERT 前另一个请求
#        刚好 INSERT 了同一 identifier）。UNIQUE 约束兜底，概率 <0.01%。
#   R425：R414 检查不覆盖"编辑模式 ceid 有值"场景（已有 items.status 检查兜底）。
#   R426：R414 检查不覆盖"秒传"分支（R427 本轮修复）。
#   R429：R414 冲突时 _safe_cleanup_td(td) 会清理 _finalize_chunk_dir
#        （分片上传暂存），用户重试需重新上传分片。设计权衡，可接受。
#   R430：R428 的检查有微秒级竞态窗口（同 R424）。
#   R431：R428 检查不覆盖"append + upload"同 identifier 并发（记录待后续扩展）。
#   R448（本轮记录）：R427 检查有微秒级竞态窗口（检查时无冲突 → 秒传 INSERT OR REPLACE
#        前另一个请求刚好 INSERT 了同一 identifier）。但 INSERT OR REPLACE 会
#        覆盖，不会报错 → 实际上这个窗口导致"秒传胜出"（items 被改为 done），
#        upload_worker 继续上传但最终 file_path 丢失 → 与 R427 修复前一致。
#        概率 <0.01%（需两个用户同一秒操作）。记录。
#   R449（本轮记录）：R427 检查不拒绝 items.status='meta_pending'（files 已上传完成，
#        file_path 稳定）。秒传覆盖 meta_pending 会导致 file_path 丢失但 IA 文件在云端。
#        实际影响小（用户能看到文件，只是无法断点续传）。概率极低（需用户在元数据
#        推送中时秒传同 identifier）。记录待评估。
#
# 【📝 同源问题记录 - 其他文件，后续统一修】
#   - R414 第 2 个文件：netdisk_routes_api_upload.py 的 api_batch_append 已修
#   - R415：netdisk_routes_api_tasks.py 的 api_tasks_clean 未分批 → 已修

import os
import re
import json
import uuid
import shutil
import threading
import time
import logging
import zipfile
from flask import request, render_template_string, redirect, url_for, jsonify, session

from netdisk_blueprints import views_bp
from netdisk_core import (
    get_db, DB_LOCK, UPLOAD_TASKS,
    LANG_OPTIONS, LICENSE_OPTIONS, MEDIA_OPTIONS, COLLECTION_OPTIONS,
    cleanup_temp_dir, _collect_meta_form, _build_upload_opts,
    upload_worker, push_metadata_worker, append_files_worker,
    cfg_get, get_max_concurrent,
)
from netdisk_utils import _merge_part_files
from templates_module import STYLE, get_navbar
from pages_module import UPLOAD_HTML, APPEND_HTML

import netdisk_core as core


# ========== 【⏱️ 延迟与超时精细控制】5 个带 5 秒 TTL 缓存的读取函数 ==========
_FE_CHUNK_THRESHOLD_CACHE = {'value': 50, 'at': 0.0}
_FE_CHUNK_SIZE_CACHE = {'value': 5, 'at': 0.0}
_FE_CHUNK_RETRIES_CACHE = {'value': 3, 'at': 0.0}
_FE_CHUNK_RETRY_SLEEP_MS_CACHE = {'value': 800, 'at': 0.0}
_UPLOAD_XHR_TIMEOUT_MS_CACHE = {'value': 600000, 'at': 0.0}
# 【B 级新增】id_check_timeout 5 秒 TTL 缓存
_ID_CHECK_TIMEOUT_CACHE = {'value': 20000, 'at': 0.0}
_CFG_CACHE_TTL = 5.0


def _get_frontend_chunk_threshold_mb():
    """返回前端分片阈值（MB），默认 50，范围 5-2048。"""
    now = time.time()
    if now - _FE_CHUNK_THRESHOLD_CACHE['at'] < _CFG_CACHE_TTL:
        return _FE_CHUNK_THRESHOLD_CACHE['value']
    try:
        v = max(5, min(int(cfg_get('frontend_chunk_threshold_mb', 50)), 2048))
    except Exception:
        v = 50
    _FE_CHUNK_THRESHOLD_CACHE['value'] = v
    _FE_CHUNK_THRESHOLD_CACHE['at'] = now
    return v


def _get_frontend_chunk_size_mb():
    """返回前端单分片大小（MB），默认 5，范围 1-50。"""
    now = time.time()
    if now - _FE_CHUNK_SIZE_CACHE['at'] < _CFG_CACHE_TTL:
        return _FE_CHUNK_SIZE_CACHE['value']
    try:
        v = max(1, min(int(cfg_get('frontend_chunk_size_mb', 5)), 50))
    except Exception:
        v = 5
    _FE_CHUNK_SIZE_CACHE['value'] = v
    _FE_CHUNK_SIZE_CACHE['at'] = now
    return v


def _get_frontend_chunk_retries():
    """返回前端分片重试次数，默认 3，范围 0-10。"""
    now = time.time()
    if now - _FE_CHUNK_RETRIES_CACHE['at'] < _CFG_CACHE_TTL:
        return _FE_CHUNK_RETRIES_CACHE['value']
    try:
        v = max(0, min(int(cfg_get('frontend_chunk_retries', 3)), 10))
    except Exception:
        v = 3
    _FE_CHUNK_RETRIES_CACHE['value'] = v
    _FE_CHUNK_RETRIES_CACHE['at'] = now
    return v


def _get_frontend_chunk_retry_sleep_ms():
    """返回前端分片重试间隔（毫秒），默认 800，范围 100-10000。"""
    now = time.time()
    if now - _FE_CHUNK_RETRY_SLEEP_MS_CACHE['at'] < _CFG_CACHE_TTL:
        return _FE_CHUNK_RETRY_SLEEP_MS_CACHE['value']
    try:
        v = max(100, min(int(cfg_get('frontend_chunk_retry_sleep_ms', 800)), 10000))
    except Exception:
        v = 800
    _FE_CHUNK_RETRY_SLEEP_MS_CACHE['value'] = v
    _FE_CHUNK_RETRY_SLEEP_MS_CACHE['at'] = now
    return v


def _get_upload_xhr_timeout_ms():
    """返回上传页 XHR 总超时（毫秒），默认 600000（10 分钟），范围 30000-3600000。"""
    now = time.time()
    if now - _UPLOAD_XHR_TIMEOUT_MS_CACHE['at'] < _CFG_CACHE_TTL:
        return _UPLOAD_XHR_TIMEOUT_MS_CACHE['value']
    try:
        v = max(30000, min(int(cfg_get('upload_xhr_timeout_ms', 600000)), 3600000))
    except Exception:
        v = 600000
    _UPLOAD_XHR_TIMEOUT_MS_CACHE['value'] = v
    _UPLOAD_XHR_TIMEOUT_MS_CACHE['at'] = now
    return v


def _get_id_check_timeout():
    """【B 级新增】返回 ID 预检超时（毫秒），默认 20000，范围 5000-120000。"""
    now = time.time()
    if now - _ID_CHECK_TIMEOUT_CACHE['at'] < _CFG_CACHE_TTL:
        return _ID_CHECK_TIMEOUT_CACHE['value']
    try:
        v = max(5000, min(int(cfg_get('id_check_timeout', 20000)), 120000))
    except Exception:
        v = 20000
    _ID_CHECK_TIMEOUT_CACHE['value'] = v
    _ID_CHECK_TIMEOUT_CACHE['at'] = now
    return v


_PART_SUFFIX_RE = re.compile(r'^(.+)\.__ND_PART__(\d+)$')


def _cleanup_leftover_parts(dir_path):
    if not dir_path or not os.path.isdir(dir_path):
        return 0
    count = 0
    for root, dirs, files in os.walk(dir_path):
        for fname in files:
            if _PART_SUFFIX_RE.match(fname):
                try:
                    os.remove(os.path.join(root, fname))
                    count += 1
                except: pass
    return count


def _get_finalize_chunk_dir():
    is_finalize = request.form.get('is_chunk_finalize') == 'yes'
    if not is_finalize:
        return None
    batch_id = request.form.get('chunk_batch_id')
    batch_id = re.sub(r'[^a-zA-Z0-9_\-]', '', batch_id or '')
    if not batch_id:
        return None
    chunk_dir = os.path.join(core.TEMP_UPLOAD_DIR, batch_id)
    if not os.path.exists(chunk_dir):
        return None
    return chunk_dir


def _merge_and_cleanup_chunks(chunk_dir):
    if not chunk_dir or not os.path.isdir(chunk_dir):
        return 0
    _merge_part_files(chunk_dir)
    leftover = _cleanup_leftover_parts(chunk_dir)
    if leftover > 0:
        logging.warning(f"⚠️ 检测到 {leftover} 个分片合并失败，已清理")
    return leftover


def _parse_local_paths():
    raw_json = request.form.get('local_paths', '[]')
    paths = []
    try:
        parsed = json.loads(raw_json)
        if isinstance(parsed, list):
            paths = [str(p).strip() for p in parsed if p and str(p).strip()]
        elif parsed:
            logging.warning(f"[上传] local_paths 不是数组格式（{type(parsed).__name__}），已忽略")
    except Exception as e:
        if raw_json and raw_json.strip() and raw_json.strip() != '[]':
            logging.warning(f"[上传] local_paths JSON 解析失败，已忽略: {e}")
        paths = []

    if not paths:
        single = request.form.get('local_path', '').strip()
        if single:
            paths = [single]

    seen = set()
    deduped = []
    for p in paths:
        try:
            expanded = os.path.expanduser(p)
        except Exception:
            expanded = p
        if not expanded:
            continue
        try:
            key = os.path.abspath(expanded)
        except Exception:
            key = expanded
        if key not in seen:
            seen.add(key)
            deduped.append(expanded)
    return deduped


def _filter_nested_paths(valid_paths):
    if len(valid_paths) <= 1:
        return list(valid_paths)
    sorted_paths = sorted(valid_paths, key=lambda p: len(os.path.abspath(p)))
    kept = []
    kept_abs = []
    for p in sorted_paths:
        try:
            p_abs = os.path.abspath(p)
        except Exception:
            p_abs = p
        is_covered = False
        for k_abs in kept_abs:
            try:
                common = os.path.commonpath([p_abs, k_abs])
                if common == k_abs and p_abs != k_abs:
                    is_covered = True
                    break
            except ValueError:
                pass
        if not is_covered:
            kept.append(p)
            kept_abs.append(p_abs)
        else:
            logging.info(f"[上传] 跳过被包含路径: {p}")
    return kept


def _copy_local_paths_to_td(valid_paths, td, is_multi=None):
    if is_multi is None:
        is_multi = len(valid_paths) > 1
    try:
        for lp in valid_paths:
            if os.path.isfile(lp):
                fname = os.path.basename(lp)
                dst = os.path.join(td, fname)
                if os.path.exists(dst):
                    name, ext = os.path.splitext(fname)
                    dst = os.path.join(td, f"{name}_{uuid.uuid4().hex[:4]}{ext}")
                shutil.copy2(lp, dst)
            elif os.path.isdir(lp):
                if is_multi:
                    dirname = os.path.basename(lp.rstrip('/\\')) or f"folder_{uuid.uuid4().hex[:4]}"
                    dst_dir = os.path.join(td, dirname)
                    if os.path.exists(dst_dir):
                        dst_dir = os.path.join(td, f"{dirname}_{uuid.uuid4().hex[:4]}")
                    shutil.copytree(lp, dst_dir)
                else:
                    for item in os.listdir(lp):
                        src = os.path.join(lp, item)
                        dst = os.path.join(td, item)
                        if os.path.exists(dst):
                            if os.path.isdir(src):
                                dst = os.path.join(td, f"{item}_{uuid.uuid4().hex[:4]}")
                            else:
                                name, ext = os.path.splitext(item)
                                dst = os.path.join(td, f"{name}_{uuid.uuid4().hex[:4]}{ext}")
                        if os.path.isdir(src):
                            shutil.copytree(src, dst)
                        else:
                            shutil.copy2(src, dst)
        return True
    except Exception as e:
        logging.error(f"[上传] 复制本地路径失败: {e}")
        return False


def _safe_cleanup_td(td):
    if not td:
        return
    try:
        temp_root = os.path.abspath(core.TEMP_UPLOAD_DIR)
        td_abs = os.path.abspath(td)
        if not td_abs.startswith(temp_root + os.sep) or td_abs == temp_root:
            return
        if os.path.isfile(td_abs):
            try: os.remove(td_abs)
            except Exception: pass
        elif os.path.isdir(td_abs):
            try: shutil.rmtree(td_abs, ignore_errors=True)
            except Exception: pass
    except Exception:
        pass


def _start_worker_or_cleanup(tid, item, opts, nid, td, worker_fn):
    try:
        t = threading.Thread(target=worker_fn, args=(tid, item, None, opts, 1, 0), daemon=True)
        UPLOAD_TASKS[tid]['_thread_obj'] = t
        t.start()
        return True, ""
    except Exception as e:
        err_msg = str(e)
        logging.error(f"[上传] 启动线程失败 (task_id={tid}): {err_msg}")
        UPLOAD_TASKS.pop(tid, None)
        try:
            with DB_LOCK:
                conn = get_db()
                try:
                    conn.execute("UPDATE items SET status='error', error_msg=? WHERE id=?", (f"启动线程失败: {err_msg}", nid))
                    conn.execute("DELETE FROM upload_tasks WHERE task_id=?", (tid,))
                    conn.commit()
                finally:
                    conn.close()
        except Exception:
            pass
        _safe_cleanup_td(td)
        return False, err_msg


def _extract_zip_safe(zip_path, td, tag="[上传]"):
    extracted_file_count = 0
    try:
        with zipfile.ZipFile(zip_path, 'r') as z:
            total_size = 0
            for member in z.infolist():
                if '..' in member.filename or member.filename.startswith('/'):
                    continue
                total_size += member.file_size
                if total_size > 10 * 1024 * 1024 * 1024:
                    raise Exception("ZIP解压后总大小超过 10GB，已拦截。")
                z.extract(member, td)
                if not member.is_dir():
                    extracted_file_count += 1
        return True, extracted_file_count
    except Exception as e:
        logging.error(f"{tag} 解压失败: {zip_path} - {e}")
        return False, extracted_file_count


@views_bp.route('/upload', methods=['GET', 'POST'])
def upload():
    owner = session.get('owner')
    eid = request.args.get('edit_id')
    ei = None
    fid = request.args.get('folder_id', 0)
    if request.method == 'POST':
        fid = request.form.get('folder_id', fid)
    if eid:
        conn = get_db()
        ei = conn.execute("SELECT id, folder_id, identifier, title, creator, description, date, mediatype, collection, language, license, tags, file_path, file_name, custom_date FROM items WHERE id=? AND owner=?", (eid, owner)).fetchone()
        conn.close()
        if ei:
            fid = ei[1]
    if request.method == 'POST':
        ident = request.form.get('identifier', '').strip()
        title = request.form.get('title', '').strip()
        files = request.files.getlist('files')
        ceid = request.form.get('edit_id')
        is_skip_upload = request.form.get('skip_upload') == 'yes'
        is_unzip = request.form.get('unzip_zip') == 'yes'
        is_zip_upload = request.form.get('zip_upload') == 'yes'
        if is_unzip and is_zip_upload:
            is_unzip = False
        meta_form = _collect_meta_form(request.form)
        if not ident or not title:
            return jsonify({"status": "error", "msg": "ID和标题不能为空"})

        vf = [f for f in files if f and f.filename]
        local_paths = _parse_local_paths()
        is_finalize = request.form.get('is_chunk_finalize') == 'yes'

        if local_paths and vf:
            logging.warning(f"[上传] local_paths 和浏览器文件同时存在，将优先使用 local_paths（{len(local_paths)} 个路径），浏览器文件（{len(vf)} 个）被忽略")

        # 秒传
        if is_skip_upload and not ceid:
            # 【R427 修复 - 2026-11】秒传前检查同 identifier 是否正在上传
            #   防止 INSERT OR REPLACE 覆盖正在上传的 items 记录 → file_path 丢失
            #   1. 检查 UPLOAD_TASKS 活跃 upload 任务
            #   2. 检查 items 表里 status='uploading'
            #   详见文件头注释。
            _conflict_skip_msg = None
            for _tid, _t in list(UPLOAD_TASKS.items()):
                if _t.get('task_type') == 'upload' and _t.get('status') in ('waiting', 'uploading', 'paused'):
                    _td = _t.get('item_data')
                    if _td and len(_td) > 2 and _td[2] == ident:
                        _conflict_skip_msg = f"该 identifier「{ident}」已有上传任务进行中，不能秒传覆盖。请等待完成或先在队列中删除该任务，然后再试秒传"
                        break
            if _conflict_skip_msg is None:
                _row_chk = None
                try:
                    with DB_LOCK:
                        _conn_chk = get_db()
                        try:
                            _row_chk = _conn_chk.execute(
                                "SELECT status FROM items WHERE identifier=? AND owner=?",
                                (ident, owner)
                            ).fetchone()
                        finally:
                            _conn_chk.close()
                except Exception as _e:
                    logging.warning(f"[R427] 秒传前检查 items.status 失败（继续秒传）: {_e}")
                    _row_chk = None
                if _row_chk and _row_chk[0] == 'uploading':
                    _conflict_skip_msg = f"该 identifier「{ident}」在数据库中有正在上传的记录（上次上传未完成），不能秒传覆盖。请先去队列页重试或删除该任务，然后再试秒传"
            if _conflict_skip_msg:
                return jsonify({"status": "error", "msg": _conflict_skip_msg})

            with DB_LOCK:
                conn = get_db()
                try:
                    conn.execute('''INSERT OR REPLACE INTO items (owner, folder_id, identifier, title, creator, description, date, mediatype, collection, language, license, tags, status, item_url, custom_date) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'done', ?, ?)''', (owner, fid, ident, title, meta_form['creator'], meta_form['description'], meta_form['date'], meta_form['mediatype'], meta_form['collection'], meta_form['language'], meta_form['license'], meta_form['tags'], f"https://archive.org/details/{ident}", meta_form['custom_date']))
                    conn.commit()
                finally:
                    conn.close()
            return jsonify({"status": "success", "redirect": url_for('views.queue_view')})

        _finalize_chunk_dir = _get_finalize_chunk_dir()
        _leftover_count = 0
        if _finalize_chunk_dir:
            _leftover_count = _merge_and_cleanup_chunks(_finalize_chunk_dir)

        # P2-10 纯元数据分支
        if ceid and not vf and not local_paths and not is_finalize:
            old_status_check = ''
            try:
                with DB_LOCK:
                    conn_chk = get_db()
                    try:
                        old_row_chk = conn_chk.execute("SELECT status FROM items WHERE id=? AND owner=?", (ceid, owner)).fetchone()
                        old_status_check = old_row_chk[0] if old_row_chk else ''
                    finally:
                        conn_chk.close()
            except Exception:
                old_status_check = ''

            if old_status_check not in ('done', 'meta_pending'):
                status_text_map = {
                    'uploading': '上传中（上次上传意外中断）',
                    'pending': '排队中（尚未开始上传）',
                    'error': '上传失败（错误信息请去队列页查看）',
                }
                status_text = status_text_map.get(old_status_check, old_status_check or '未知')
                return jsonify({
                    "status": "error",
                    "msg": (
                        f"⚠️ 当前条目状态为「{status_text}」，说明上次的文件还没有上传完整。\n\n"
                        f"为防止把未完成的条目误标为「已完成」，本次只改元数据的操作已被阻止。\n\n"
                        f"请先通过以下方式之一恢复上传：\n"
                        f"1. 去「队列」页点击「🔄 重试」继续上次中断的上传\n"
                        f"2. 在本页面重新选择文件上传（会覆盖旧的未完成记录）\n\n"
                        f"文件上传完成后，才能单独修改标题/描述等元数据。"
                    )
                })

            with DB_LOCK:
                conn = get_db()
                conn.execute('''UPDATE items SET title=?, creator=?, description=?, date=?, mediatype=?, collection=?, language=?, license=?, tags=?, custom_date=?, status='meta_pending', error_msg=NULL WHERE id=? AND owner=?''', (title, meta_form['creator'], meta_form['description'], meta_form['date'], meta_form['mediatype'], meta_form['collection'], meta_form['language'], meta_form['license'], meta_form['tags'], meta_form['custom_date'], ceid, owner))
                conn.commit()
                conn.close()

            tid = str(uuid.uuid4())[:8]
            extra_data = {'db_id': int(ceid)}
            UPLOAD_TASKS[tid] = {
                "status": "waiting", "title": f"[更新元数据] {title}", "task_type": "push_meta",
                "created_at": time.time(), "extra_data": json.dumps(extra_data)
            }
            core.save_task_to_db(tid)
            t = threading.Thread(target=push_metadata_worker, args=(tid, int(ceid)), daemon=True)
            UPLOAD_TASKS[tid]['_thread_obj'] = t
            t.start()
            return jsonify({"status": "success", "redirect": url_for('views.queue_view')})

        # ========== 锁外：准备 td + fn ==========
        td, fn = None, None
        if local_paths:
            valid_paths = []
            invalid_paths = []
            for lp in local_paths:
                if not os.path.exists(lp):
                    logging.warning(f"[上传] 本地路径不存在，跳过: {lp}")
                    invalid_paths.append(lp)
                    continue
                valid_paths.append(lp)

            if not valid_paths:
                if invalid_paths:
                    shown = invalid_paths[:5]
                    extra = f"\n...（共 {len(invalid_paths)} 个无效路径）" if len(invalid_paths) > 5 else ""
                    msg = "❌ 所选本地路径都不存在，请检查路径或 Termux 权限：\n" + "\n".join(shown) + extra
                    return jsonify({"status": "error", "msg": msg})
                return jsonify({"status": "error", "msg": "❌ 没有有效的本地路径"})

            if invalid_paths:
                logging.warning(f"[上传] {len(invalid_paths)}/{len(local_paths)} 个路径不存在，已跳过")

            valid_paths = _filter_nested_paths(valid_paths)
            is_multi_final = len(valid_paths) > 1

            td = os.path.join(core.TEMP_UPLOAD_DIR, str(uuid.uuid4())[:8])
            os.makedirs(td, exist_ok=True)

            ok = _copy_local_paths_to_td(valid_paths, td, is_multi=is_multi_final)
            if not ok:
                _safe_cleanup_td(td)
                return jsonify({"status": "error", "msg": "❌ 本地复制失败，请检查权限或磁盘空间"})

            if is_multi_final:
                has_any_file = False
                for root, dirs, fs in os.walk(td):
                    if fs:
                        has_any_file = True
                        break
                if not has_any_file:
                    _safe_cleanup_td(td)
                    return jsonify({"status": "error", "msg": "❌ 所选路径不包含任何文件，请重新选择"})
                fn = f"[{len(valid_paths)} 个路径]"
            else:
                for root, dirs, fs in os.walk(td):
                    if fs:
                        fn = os.path.relpath(os.path.join(root, fs[0]), td).replace('\\', '/')
                        break
                if not fn:
                    _safe_cleanup_td(td)
                    return jsonify({"status": "error", "msg": "❌ 所选路径不包含任何文件（可能是空文件夹），请重新选择"})
        elif is_finalize:
            if not _finalize_chunk_dir:
                if _leftover_count > 0:
                    return jsonify({"status": "error", "msg": f"分片上传失败（{_leftover_count} 个分片合并失败），请重新上传"})
                return jsonify({"status": "error", "msg": "无效的批次号或暂存文件丢失"})
            td = _finalize_chunk_dir
            for root, dirs, fs in os.walk(td):
                if fs:
                    fn = os.path.relpath(os.path.join(root, fs[0]), td).replace('\\', '/')
                    break
            if not fn:
                if _leftover_count > 0:
                    return jsonify({"status": "error", "msg": f"分片上传失败（{_leftover_count} 个分片合并失败），请重新上传"})
                return jsonify({"status": "error", "msg": "暂存目录为空，请重新选择文件"})
        elif vf:
            td = os.path.join(core.TEMP_UPLOAD_DIR, str(uuid.uuid4())[:8])
            os.makedirs(td, exist_ok=True)
            fn = None
            for f in vf:
                orig_path = f.filename.replace('\\', '/')
                if 'temp_upload/' in orig_path:
                    orig_path = orig_path.split('temp_upload/', 1)[-1]
                    if '/' in orig_path:
                        orig_path = orig_path.split('/', 1)[-1]
                elif orig_path.startswith('/') or 'data/data' in orig_path:
                    orig_path = os.path.basename(orig_path)
                safe_path = orig_path.replace('../', '').lstrip('/')
                if not safe_path:
                    continue
                target_path = os.path.join(td, safe_path)
                os.makedirs(os.path.dirname(target_path) if os.path.dirname(target_path) else td, exist_ok=True)
                f.save(target_path)
                if not fn:
                    fn = safe_path
            if not fn:
                _safe_cleanup_td(td)
                td = None

        # ========== 锁外：zip/unzip ==========
        if td and fn:
            if is_zip_upload:
                zip_name = request.form.get('zip_custom_name', '').strip() or 'archive'
                zip_name = re.sub(r'[\\/:\*\?"<>\|\.\.]', '_', zip_name).strip('_') or 'archive'
                if not zip_name.endswith('.zip'):
                    zip_name += '.zip'
                zip_path = os.path.join(core.TEMP_UPLOAD_DIR, str(uuid.uuid4())[:8] + '_' + zip_name)
                try:
                    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_STORED) as z:
                        for root, dirs, fs in os.walk(td):
                            for file in fs:
                                z.write(os.path.join(root, file), os.path.relpath(os.path.join(root, file), td))
                    _safe_cleanup_td(td)
                    td, fn = zip_path, zip_name
                except Exception as e:
                    logging.error(f"[上传] ZIP 打包失败: {e}")
                    _safe_cleanup_td(td)
                    _safe_cleanup_td(zip_path)
                    return jsonify({"status": "error", "msg": f"❌ ZIP 打包失败: {e}"})
            if is_unzip:
                for root, dirs, fs in os.walk(td):
                    for file in fs:
                        if file.lower().endswith('.zip'):
                            zip_path = os.path.join(root, file)
                            unzip_ok, extracted_count = _extract_zip_safe(zip_path, td, tag="[上传]")
                            if unzip_ok and extracted_count > 0:
                                try:
                                    os.remove(zip_path)
                                except Exception as e:
                                    logging.warning(f"[上传] 删除 zip 失败（后续可能仍会尝试上传）: {zip_path} - {e}")
                            elif unzip_ok and extracted_count == 0:
                                logging.warning(f"[上传] {zip_path} 解压后无有效文件（可能全部被过滤或只含目录），保留原 zip 作为数据上传")
                            else:
                                logging.warning(f"[上传] {zip_path} 解压失败，将作为原始压缩包上传（用户数据不丢失）")
                fn = None
                for root, dirs, fs in os.walk(td):
                    if fs:
                        fn = os.path.relpath(os.path.join(root, fs[0]), td).replace('\\', '/')
                        break
                if not fn:
                    _safe_cleanup_td(td)
                    return jsonify({"status": "error", "msg": "❌ 解压后目录为空（所有文件被过滤或不存在），请重新选择"})

        # ========== 锁内：DB 事务（B18 加 try/except） ==========
        # 【R414 修复】新建分支 INSERT 前检查同 identifier 活跃 upload 任务
        item = None
        nid = None
        _conflict_msg = None
        if td and fn:
            try:
                with DB_LOCK:
                    conn = get_db()
                    try:
                        if ceid:
                            conn.execute('''UPDATE items SET identifier=?, title=?, creator=?, description=?, date=?, mediatype=?, collection=?, language=?, license=?, tags=?, file_path=?, file_name=?, status='uploading', error_msg=NULL, deleted_at=NULL, custom_date=? WHERE id=? AND owner=?''', (ident, title, meta_form['creator'], meta_form['description'], meta_form['date'], meta_form['mediatype'], meta_form['collection'], meta_form['language'], meta_form['license'], meta_form['tags'], td, fn, meta_form['custom_date'], ceid, owner))
                            nid = ceid
                        else:
                            # 【R414 修复】新建前检查同 identifier 活跃 upload 任务
                            for _tid, _t in list(UPLOAD_TASKS.items()):
                                if _t.get('task_type') == 'upload' and _t.get('status') in ('waiting', 'uploading', 'paused'):
                                    _td = _t.get('item_data')
                                    if _td and len(_td) > 2 and _td[2] == ident:
                                        _conflict_msg = f"该 identifier「{ident}」已有上传任务进行中，请等待完成或先在队列中删除该任务"
                                        break
                            if _conflict_msg is None:
                                cur = conn.execute('''INSERT INTO items (owner, folder_id, identifier, title, creator, description, date, mediatype, collection, language, license, tags, file_path, file_name, status, custom_date) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'uploading', ?)''', (owner, fid, ident, title, meta_form['creator'], meta_form['description'], meta_form['date'], meta_form['mediatype'], meta_form['collection'], meta_form['language'], meta_form['license'], meta_form['tags'], td, fn, meta_form['custom_date']))
                                nid = cur.lastrowid
                        if _conflict_msg is None:
                            conn.commit()
                            item = conn.execute("SELECT id, folder_id, identifier, title, creator, description, date, mediatype, collection, language, license, tags, file_path, file_name, custom_date FROM items WHERE id=?", (nid,)).fetchone()
                    finally:
                        conn.close()
            except Exception as e:
                logging.error(f"[上传] DB 写入失败: {e}")
                _safe_cleanup_td(td)
                return jsonify({"status": "error", "msg": f"❌ DB 写入失败: {e}"})

            if _conflict_msg:
                _safe_cleanup_td(td)
                return jsonify({"status": "error", "msg": _conflict_msg})

            if not item:
                _safe_cleanup_td(td)
                return jsonify({"status": "error", "msg": "条目不存在或无权访问"})

            opts = _build_upload_opts(request.form)
            tid = str(uuid.uuid4())[:8]
            UPLOAD_TASKS[tid] = {"status": "waiting", "title": title, "item_data": item, "task_type": "upload", "created_at": time.time()}
            core.save_task_to_db(tid)
            ok, err = _start_worker_or_cleanup(tid, item, opts, nid, td, upload_worker)
            if not ok:
                return jsonify({"status": "error", "msg": f"❌ 启动上传线程失败: {err}"})
            return jsonify({"status": "success", "redirect": url_for('views.queue_view')})

        if not ceid:
            return jsonify({"status": "error", "msg": "新建任务必须选择文件或填入本地路径"})

        with DB_LOCK:
            conn = get_db()
            try:
                old_row = conn.execute("SELECT status FROM items WHERE id=? AND owner=?", (ceid, owner)).fetchone()
                old_status = old_row[0] if old_row else ''
                if old_status not in ('done', 'meta_pending'):
                    status_text_map = {
                        'uploading': '上传中（上次上传意外中断）',
                        'pending': '排队中（尚未开始上传）',
                        'error': '上传失败（错误信息请去队列页查看）',
                    }
                    status_text = status_text_map.get(old_status, old_status or '未知')
                    return jsonify({
                        "status": "error",
                        "msg": (
                            f"⚠️ 当前条目状态为「{status_text}」，说明上次的文件还没有上传完整。\n\n"
                            f"请先通过以下方式之一恢复上传：\n"
                            f"1. 去「队列」页点击「🔄 重试」\n"
                            f"2. 在本页面重新选择文件上传\n\n"
                            f"文件上传完成后，才能单独修改元数据。"
                        )
                    })
                conn.execute('''UPDATE items SET title=?, creator=?, description=?, date=?, mediatype=?, collection=?, language=?, license=?, tags=?, custom_date=?, status='meta_pending' WHERE id=? AND owner=?''', (title, meta_form['creator'], meta_form['description'], meta_form['date'], meta_form['mediatype'], meta_form['collection'], meta_form['language'], meta_form['license'], meta_form['tags'], meta_form['custom_date'], ceid, owner))
                conn.commit()
            finally:
                conn.close()

        tid = str(uuid.uuid4())[:8]
        extra_data = {'db_id': int(ceid)}
        UPLOAD_TASKS[tid] = {"status": "waiting", "title": f"[更新元数据] {title}", "task_type": "push_meta", "created_at": time.time(), "extra_data": json.dumps(extra_data)}
        core.save_task_to_db(tid)
        t = threading.Thread(target=push_metadata_worker, args=(tid, int(ceid)), daemon=True)
        UPLOAD_TASKS[tid]['_thread_obj'] = t
        t.start()
        return jsonify({"status": "success", "redirect": url_for('views.queue_view')})

    # GET 渲染页面
    return render_template_string(
        UPLOAD_HTML, s=STYLE, n=get_navbar(ei != None, 0),
        ei=ei, f=fid, mo=MEDIA_OPTIONS, co=COLLECTION_OPTIONS,
        lang_options=LANG_OPTIONS, license_options=LICENSE_OPTIONS,
        upload_retries_max=cfg_get('upload_retries_max', 3),
        upload_sleep_min=cfg_get('upload_sleep_min', 10),
        id_check_debounce_ms=cfg_get('id_check_debounce_ms', 800),
        auto_check_id=cfg_get('auto_check_id', False),
        frontend_chunk_threshold_mb=_get_frontend_chunk_threshold_mb(),
        frontend_chunk_size_mb=_get_frontend_chunk_size_mb(),
        frontend_chunk_retries=_get_frontend_chunk_retries(),
        frontend_chunk_retry_sleep_ms=_get_frontend_chunk_retry_sleep_ms(),
        upload_xhr_timeout_ms=_get_upload_xhr_timeout_ms(),
        id_check_timeout=_get_id_check_timeout(),
    )


@views_bp.route('/append_files/<int:item_id>', methods=['GET', 'POST'])
def append_files(item_id):
    conn = get_db()
    owner = session.get('owner')
    item = conn.execute("SELECT id, identifier, title, mediatype FROM items WHERE id=? AND owner=?", (item_id, owner)).fetchone()
    if not item:
        conn.close()
        return "❌ 未找到或无权访问"

    if request.method == 'POST':
        # 【R428 修复 - 2026-11】检查同 identifier 活跃 append 任务
        _conflict_msg = None
        for _tid, _t in list(UPLOAD_TASKS.items()):
            if _t.get('task_type') == 'append' and _t.get('status') in ('waiting', 'uploading', 'paused'):
                _tid_extra = _t.get('extra_data')
                if _tid_extra:
                    try:
                        _extra = json.loads(_tid_extra)
                        if isinstance(_extra, dict) and _extra.get('identifier') == item[1]:
                            _conflict_msg = f"该 identifier「{item[1]}」已有追加任务进行中，请等待完成或先在队列中删除该任务"
                            break
                    except Exception:
                        pass
        if _conflict_msg:
            conn.close()
            return jsonify({"status": "error", "msg": _conflict_msg})

        files = [f for f in request.files.getlist('files') if f and f.filename]
        local_paths_json = request.form.get('local_paths', '[]')
        try:
            local_paths = json.loads(local_paths_json)
            if not isinstance(local_paths, list):
                local_paths = []
        except:
            local_paths = []

        is_finalize = request.form.get('is_chunk_finalize') == 'yes'
        is_unzip = request.form.get('unzip_zip') == 'yes'
        is_zip_upload = request.form.get('zip_upload') == 'yes'
        if is_unzip and is_zip_upload:
            is_unzip = False

        _finalize_chunk_dir = _get_finalize_chunk_dir()
        _leftover_count = 0
        if _finalize_chunk_dir:
            _leftover_count = _merge_and_cleanup_chunks(_finalize_chunk_dir)

        td = os.path.join(core.TEMP_UPLOAD_DIR, str(uuid.uuid4())[:8])
        os.makedirs(td, exist_ok=True)

        if local_paths:
            expanded_paths = []
            for lp in local_paths:
                try:
                    ep = os.path.expanduser(lp)
                except Exception:
                    ep = lp
                if ep:
                    expanded_paths.append(ep)
            expanded_paths = _filter_nested_paths(expanded_paths)
            is_multi = len(expanded_paths) > 1
            for lp in expanded_paths:
                if not os.path.exists(lp):
                    continue
                if os.path.isfile(lp):
                    fname = os.path.basename(lp)
                    dst = os.path.join(td, fname)
                    if os.path.exists(dst):
                        name, ext = os.path.splitext(fname)
                        dst = os.path.join(td, f"{name}_{uuid.uuid4().hex[:4]}{ext}")
                    shutil.copy2(lp, dst)
                elif os.path.isdir(lp):
                    if is_multi:
                        dirname = os.path.basename(lp.rstrip('/\\')) or f"folder_{uuid.uuid4().hex[:4]}"
                        dst_dir = os.path.join(td, dirname)
                        if os.path.exists(dst_dir):
                            dst_dir = os.path.join(td, f"{dirname}_{uuid.uuid4().hex[:4]}")
                        shutil.copytree(lp, dst_dir)
                    else:
                        for item in os.listdir(lp):
                            src = os.path.join(lp, item)
                            dst = os.path.join(td, item)
                            if os.path.exists(dst):
                                if os.path.isdir(src):
                                    dst = os.path.join(td, f"{item}_{uuid.uuid4().hex[:4]}")
                                else:
                                    name, ext = os.path.splitext(item)
                                    dst = os.path.join(td, f"{name}_{uuid.uuid4().hex[:4]}{ext}")
                            if os.path.isdir(src):
                                shutil.copytree(src, dst)
                            else:
                                shutil.copy2(src, dst)
        elif is_finalize:
            if not _finalize_chunk_dir:
                cleanup_temp_dir(td)
                conn.close()
                if _leftover_count > 0:
                    return jsonify({"status": "error", "msg": f"分片上传失败（{_leftover_count} 个分片合并失败），请重新上传"})
                return jsonify({"status": "error", "msg": "无效的批次号或暂存文件丢失"})
            for it in os.listdir(_finalize_chunk_dir):
                src = os.path.join(_finalize_chunk_dir, it)
                dst = os.path.join(td, it)
                if os.path.isdir(src):
                    shutil.copytree(src, dst)
                else:
                    shutil.copy2(src, dst)
            cleanup_temp_dir(_finalize_chunk_dir)
        elif files:
            for f in files:
                orig_path = f.filename.replace('\\', '/')
                if 'temp_upload/' in orig_path:
                    orig_path = orig_path.split('temp_upload/', 1)[-1]
                    if '/' in orig_path:
                        orig_path = orig_path.split('/', 1)[-1]
                elif orig_path.startswith('/') or 'data/data' in orig_path:
                    orig_path = os.path.basename(orig_path)
                safe_path = orig_path.replace('../', '').lstrip('/')
                if not safe_path:
                    continue
                target_path = os.path.join(td, safe_path)
                os.makedirs(os.path.dirname(target_path) if os.path.dirname(target_path) else td, exist_ok=True)
                f.save(target_path)

        if not os.listdir(td):
            cleanup_temp_dir(td)
            conn.close()
            if _leftover_count > 0:
                return jsonify({"status": "error", "msg": f"分片上传失败（{_leftover_count} 个分片合并失败），请重新上传"})
            return jsonify({"status": "error", "msg": "请选择文件或填入本地路径"})

        if is_zip_upload:
            zip_name = request.form.get('zip_custom_name', '').strip() or 'archive'
            zip_name = re.sub(r'[\\/:\*\?"<>\|\.\.]', '_', zip_name).strip('_') or 'archive'
            if not zip_name.endswith('.zip'):
                zip_name += '.zip'
            zip_path = os.path.join(core.TEMP_UPLOAD_DIR, str(uuid.uuid4())[:8] + '_' + zip_name)
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_STORED) as z:
                for root, dirs, fs in os.walk(td):
                    for file in fs:
                        z.write(os.path.join(root, file), os.path.relpath(os.path.join(root, file), td))
            cleanup_temp_dir(td)
            td = os.path.join(core.TEMP_UPLOAD_DIR, str(uuid.uuid4())[:8])
            os.makedirs(td, exist_ok=True)
            shutil.move(zip_path, os.path.join(td, zip_name))

        if is_unzip:
            for root, dirs, fs in os.walk(td):
                for file in fs:
                    if file.lower().endswith('.zip'):
                        zip_path = os.path.join(root, file)
                        unzip_ok, extracted_count = _extract_zip_safe(zip_path, td, tag="[追加]")
                        if unzip_ok and extracted_count > 0:
                            try:
                                os.remove(zip_path)
                            except Exception as e:
                                logging.warning(f"[追加] 删除 zip 失败: {zip_path} - {e}")
                        elif unzip_ok and extracted_count == 0:
                            logging.warning(f"[追加] {zip_path} 解压后无有效文件（可能全部被过滤或只含目录），保留原 zip 作为数据上传")
                        else:
                            logging.warning(f"[追加] {zip_path} 解压失败，将作为原始压缩包上传（用户数据不丢失）")

        file_infos = []
        for root, dirs, fs in os.walk(td):
            for file in fs:
                fp = os.path.join(root, file)
                rel = os.path.relpath(fp, td).replace('\\', '/')
                sz = os.path.getsize(fp)
                file_infos.append({'fp': fp, 'rel': rel, 'sz': sz})

        if not file_infos:
            cleanup_temp_dir(td)
            conn.close()
            if _leftover_count > 0:
                return jsonify({"status": "error", "msg": f"分片上传失败（{_leftover_count} 个分片合并失败），请重新上传"})
            return jsonify({"status": "error", "msg": "没有找到有效文件"})

        opts = _build_upload_opts(request.form)
        meta = {"title": item[2] or item[1], "mediatype": item[3] or "software"}
        tid = str(uuid.uuid4())[:8]

        extra_data = {
            'identifier': item[1],
            'file_infos': file_infos,
            'meta': meta,
            'db_id': item[0],
            'opts': opts
        }

        UPLOAD_TASKS[tid] = {
            "status": "waiting", "title": f"[批量追加] {item[2]}", "task_type": "append",
            "created_at": time.time(), "extra_data": json.dumps(extra_data)
        }
        core.save_task_to_db(tid)

        try:
            t = threading.Thread(target=append_files_worker, args=(tid, item[1], file_infos, meta, item[0], opts), daemon=True)
            UPLOAD_TASKS[tid]['_thread_obj'] = t
            t.start()
        except Exception as e:
            logging.error(f"[追加] 启动线程失败: {e}")
            UPLOAD_TASKS.pop(tid, None)
            try:
                with DB_LOCK:
                    conn2 = get_db()
                    try:
                        conn2.execute("DELETE FROM upload_tasks WHERE task_id=?", (tid,))
                        conn2.commit()
                    finally:
                        conn2.close()
            except Exception:
                pass
            cleanup_temp_dir(td)
            conn.close()
            return jsonify({"status": "error", "msg": f"❌ 启动追加线程失败: {e}"})

        conn.close()
        return redirect(url_for('views.queue_view'))

    conn.close()
    return render_template_string(
        APPEND_HTML, s=STYLE, n=get_navbar(False), item=item,
        frontend_chunk_threshold_mb=_get_frontend_chunk_threshold_mb(),
        frontend_chunk_size_mb=_get_frontend_chunk_size_mb(),
        frontend_chunk_retries=_get_frontend_chunk_retries(),
        frontend_chunk_retry_sleep_ms=_get_frontend_chunk_retry_sleep_ms(),
        upload_xhr_timeout_ms=_get_upload_xhr_timeout_ms(),
    )


@views_bp.route('/execute_all', methods=['POST'])
def execute_all():
    owner = session.get('owner')
    core.CONCURRENCY_LIMITER.set_max(get_max_concurrent())
    batch_limit = cfg_get('execute_batch_limit', 10)

    running_db_ids = set()
    for t in UPLOAD_TASKS.values():
        if t.get('status') in ('waiting', 'uploading', 'paused'):
            item_data = t.get('item_data')
            if item_data:
                running_db_ids.add(item_data[0])

    tasks_to_start = []

    with DB_LOCK:
        conn = get_db()
        items = conn.execute("SELECT id, folder_id, identifier, title, creator, description, date, mediatype, collection, language, license, tags, file_path, file_name, custom_date FROM items WHERE status IN ('pending', 'error') AND deleted_at IS NULL AND owner=?", (owner,)).fetchall()
        items_to_start = [item for item in items if item[0] not in running_db_ids]
        actual_start = min(len(items_to_start), batch_limit)

        started = 0
        for item in items_to_start:
            if started >= actual_start: break
            conn.execute("UPDATE items SET status='uploading' WHERE id=?", (item[0],))
            tasks_to_start.append(item)
            started += 1
        conn.commit()
        conn.close()

    started = 0
    for item in tasks_to_start:
        tid = str(uuid.uuid4())[:8]
        UPLOAD_TASKS[tid] = {
            "status": "waiting", "msg": "排队中...", "title": item[3],
            "item_data": item, "task_type": "upload", "percent": 0,
            "speed": "0.0", "current": "", "total": "", "eta": "",
            "raw_line": "", "batch_total": actual_start, "batch_done": started,
            "created_at": time.time()
        }
        core.save_task_to_db(tid)
        opts = {'generate_torrent': cfg_get('default_generate_torrent', False), 'access_level': 'public'}
        t = threading.Thread(target=upload_worker, args=(tid, item, None, opts, actual_start, started), daemon=True)
        UPLOAD_TASKS[tid]['_thread_obj'] = t
        t.start()
        started += 1

    return redirect(url_for('views.queue_view'))