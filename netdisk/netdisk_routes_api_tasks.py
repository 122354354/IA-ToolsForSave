# netdisk_routes_api_tasks.py
# 任务队列相关 API
# 【A1b】api_retry_file 进入 worker 前先查 sub_tasks.status
# 【🔴 G-73】api_tasks 新增返回 identifier 字段
# 【🔴 P0-30】遍历 UPLOAD_TASKS.items() 无 list() 保护
# 【🔴 P0-31】4 处 KeyError 窗口 + 4 处 request.get_json() None + 3 处 conn.close() 泄漏
# 【🔴 P0-32】item[14] IndexError + extra 非 dict TypeError
# 【🔴 P0-33】item[3]~item[12] 无长度保护 + worker 启动异常恢复 status + filename 非字符串
# 【🔴 P0-34】api_retry_file 路径穿越漏洞
# 【🔴 G-89】api_retry_file 的 threading.Thread.start() 加 try/except
# 【🔴 G-90】api_retry_file 的 filename 含 \x00 → ValueError
# 【🔴 P0-35】符号链接攻击绕过 P0-34（os.path.realpath 替代 abspath）
# 【🔴 P0-37】api_retry_file 用 os.path.exists 没排除目录（os.path.isfile 替代）
# 【🔴 P0-38】G-100 item_data 提取两次 + G-101 identifier 为空前置 + G-108 opts=None
# 【🔴 P0-39】G-110 item_data 一次提取 + G-109 opts 显式 isinstance
# 【🔴 P0-40 修复 - 2026-11】api_retry_file 的 f_path 不是 str 时 TypeError → 500
# 【🔴 G-91 记录】api_retry_task 的 t['status']='waiting' 到 nt.start() 窗口内 task 被删
# 【🔴 G-98 记录】api_retry_task 的 except 分支 core.save_task_to_db 无 try/except（内部有）
# 【🔴 G-102 记录】api_retry_task 的 except KeyError 后 status 变 error
#
# 【🔴 第 1 轮修复 - 2026-11（Bug 1/2/3/4）】见历史
# 【🔴 第 2 轮修复 - 2026-11（G-170 + G-177 + G-87）】见历史
# 【🔴 G-194 + G-195 修复 - 2026-11】见 _restart_upload_retries
# 【⏱️ 延迟与超时精细控制 - 2026-11 第 11/20 步】见 _get_max_auto_restart_per_resume
# 【🔴 R103 + R313 + R317 修复 - 2026-11】见 api_toggle_pause 注释
#
# 【🔴 R334 修复 - 2026-11（第 1 轮）】
#   api_retry_file 的 row is None 时明确拦截，防止 worker 真的上传文件但
#   sub_tasks 里无记录导致数据不一致。
#   api_retry_task 对 upload 类型明确拒绝，引导用户走 /api/retry_single。
#
# 【🔴 R335 修复 - 2026-11（第 2 轮）】
#   api_retry_task 加 _RETRY_TASK_LOCK 原子抢占，防止并发请求同时启动多个 worker。
#
# 【🔴 R340 修复 - 2026-11（第 3 轮）】
#   api_retry_file 改为原子抢占模式（DB 层 UPDATE ... WHERE status='error'），
#   防止并发调用启动两个 retry_single_file_worker 上传同一文件。
#
# 【🔴 R415 修复 - 2026-11（第 4 轮 - 本轮 - 低危）】
#   问题：api_tasks_clean 用单条 `DELETE FROM ... WHERE task_id IN (?, ?, ...)`
#        一次删除所有 to_remove。SQLite 默认 SQLITE_MAX_VARIABLE_NUMBER = 999
#        （SQLite ≥ 3.32 默认 32766，但旧版 SQLite / 某些编译选项仍为 999）。
#        to_remove > 999 时报错 "too many SQL variables"。
#   触发场景（真实可能，用户可感知）：
#     - 用户关闭 auto_clean_tasks（自动化开关默认全关）
#     - 累积 1000+ 已结束任务（done / error）
#     - 点「清理已结束任务」→ 报错 → 任务累积越来越多 → 永久卡住
#     - 前端显示 "清理失败: too many SQL variables"（晦涩技术错误）
#   修复：分批 DELETE（每批 500 个），保持事务语义（全部成功 commit，任一失败 rollback）：
#        1. with DB_LOCK 内循环分批（500 一批）
#        2. 每批：DELETE sub_tasks + DELETE upload_tasks
#        3. 全部批次执行完后 conn.commit()
#        4. 任一批抛异常 → 跳过 commit → finally conn.close() 自动 rollback
#        → DB 全部保留，不 pop 内存，下次点清理重试
#   与 _clean_virtual_tasks_worker 的 R322 修复逻辑一致（同样分批 500）。
#   为什么分批大小是 500：
#     - 每批 2 条 SQL × 500 个 ? = 1000 个变量，安全在 999 上限内
#     - 太小（如 100）→ 批次多，DB_LOCK 持锁时间长
#     - 太大（如 900）→ 接近上限，可能触发边界
#   为什么仍保留原事务语义（全 commit 或全 rollback）：
#     - 与 _clean_virtual_tasks_worker 一致
#     - 若部分批次成功部分失败，pop 内存后 DB 残留会导致下次清理时记录消失但
#       DB 里还有 → 重启后 _restore_interrupted_tasks 可能"复活"
#     - 全部回滚最安全（内存/DB 一致）
#
# 【📝 保留记录 - 2026-11，低优先，以后修】
#   G-53 / G-91 / G-98 / G-102 / G-105 / G-106 / G-111 / G-112 / G-113 / G-114
#   / G-192 / G-193 / G-196 / G-197：见历史
#   R314：前端 UI 需配合（主卡片 error + upload 状态显示「继续重启剩余」按钮）
#   R315：api_retry_single 检测已有 sub_tasks 时改走 R103 追加重启
#   R316：retry_single_file_worker 等待 slot 时响应 paused
#   R318：孤儿 'uploading' 无法通过「继续」自动恢复（用户点「重试」）
#   R320：api_tasks_del DB 删除 3 次失败时内存已 pop 但 DB 有残留
#   R321：_restart_upload_retries 的 sz 在 worker 真正上传前可能过期
#   R322：api_tasks_clean 的 to_remove > 999 时 SQLite 变量上限报错（R415 修复）
#   R323：_update_parent_task_progress 的 elif current_status == 'waiting'
#   R324：_restart_upload_retries 的 item_data 长度校验失败无日志
#   R325：api_tasks 的 raw_line 受 raw_line_max_lines 上限控制
#   R326：_worker_alive 的 th.is_alive() 极少数 Python 版本偶发 RuntimeError
#   R336：_RETRY_TASK_LOCK 只锁 api_retry_task
#   R337：api_retry_task 的 start_err is None 兜底判定
#   R338：并发 retry_task + api_tasks_del 时可能启动孤儿 worker
#   R363：api_retry_file 的 _rollback_claim 用 WHERE status='uploading' 保护
#   R364：api_retry_file 抢占成功后、worker 启动成功前的微秒窗口内，
#         用户点 api_tasks_clean 清理了该 task（status='uploading' 不在白名单）→ 安全
#   R365：api_retry_file 抢占成功后、worker 真正执行 UPDATE 前的窗口内
#         sub_tasks.status='uploading'，前端 api_tasks/files 显示"上传中"
#   R366：api_retry_file 的 _rollback_claim 回滚失败（DB 崩溃）状态可能卡 uploading
#         （init_db 重启时 uploading → error 恢复）
#
#   R443（本轮记录）：R415 修复后 to_remove > 999 时锁内循环 N/500 批
#                    （10000 个任务 20 批，锁持有时间约 20 × 2ms = 40ms）。
#                    对用户几乎无感，记录。
#   R444（本轮记录）：R415 修复后 to_remove 极大（10 万+）时锁持有时间约 400ms。
#                    极端场景（用户从未清理 + 连续失败 10 万次任务），可接受。
#   R445（本轮记录）：R415 修复后单次清理耗时略增（分批循环），但成功的概率从 0%
#                    （to_remove > 999 时）变为 100% —— 显著改善。
#
# 【📝 同源问题记录 - 其他文件，后续统一修】
#   - netdisk_utils_ia.py 的 _fetch_and_cache_ia_files 未检查 deleted_at（R362）已修
#   - 全部已知同源问题已修复完毕

import os
import json
import threading
import time
import logging
from flask import request, jsonify

import netdisk_core as core
from netdisk_blueprints import api_bp
from netdisk_core import (
    UPLOAD_TASKS, DB_LOCK, MEM_LOG, CONCURRENCY_LIMITER, get_db,
    download_folder_worker, delete_files_worker, append_files_worker,
    rename_files_worker, pull_from_ia_worker, push_metadata_worker,
    retry_single_file_worker,
)


# ========== 【⏱️ 延迟与超时精细控制】max_auto_restart_per_resume 5 秒 TTL 缓存 ==========
_MAX_AUTO_RESTART_PER_RESUME_CACHE = {'value': 20, 'at': 0.0}
_CFG_CACHE_TTL = 5.0

# 【保留】原硬编码常量作为兜底（外部若直接 from netdisk_routes_api_tasks import
#         _MAX_AUTO_RESTART_PER_RESUME 仍可用，不报错；但内部逻辑改走 getter）
_MAX_AUTO_RESTART_PER_RESUME = 20


# ========== 【R335 修复 - 2026-11】api_retry_task 并发原子抢占锁 ==========
# 用途：防止两个并发 api_retry_task 请求同时看到 status='error'，
#       都改 status='waiting' 并都启动 worker → 同一 task 跑两个 worker。
#
# 为什么不复用其他锁：
#   - DB_LOCK：锁内会启动 worker（worker 内部调 DB_LOCK）→ 死锁风险
#   - CONFIG_LOCK：只保护 config.json 写入，与任务状态无关
#   - APPEND_CWD_LOCK：append 专用的 cwd 切换锁，与任务状态无关
#   - UPLOAD_COOLDOWN_LOCK：只保护 GLOBAL_UPLOAD_COOLDOWN，与任务状态无关
_RETRY_TASK_LOCK = threading.Lock()

# 【R335 修复 - 2026-11】api_retry_task 支持的任务类型白名单。
# 用于在改 status 之前前置拒绝 unknown 类型（避免 waiting→error 微秒级闪动）。
# 不含 'upload'：upload 走 items 表 + /api/retry_single（已单独前置拒绝）。
_SUPPORTED_TASK_TYPES = (
    'download', 'download_folder', 'delete', 'append', 'rename', 'pull', 'push_meta'
)


def _get_max_auto_restart_per_resume():
    """返回单次 resume 最多自动重启的文件数，默认 20，范围 1-100。"""
    now = time.time()
    if now - _MAX_AUTO_RESTART_PER_RESUME_CACHE['at'] < _CFG_CACHE_TTL:
        return _MAX_AUTO_RESTART_PER_RESUME_CACHE['value']
    try:
        v = max(1, min(int(core.cfg_get('max_auto_restart_per_resume', 20)), 100))
    except Exception:
        v = 20
    _MAX_AUTO_RESTART_PER_RESUME_CACHE['value'] = v
    _MAX_AUTO_RESTART_PER_RESUME_CACHE['at'] = now
    return v


def _is_path_inside(child_abs, parent_abs):
    """【P0-34】检查 child_abs 是否在 parent_abs 内（防路径穿越）。"""
    if child_abs == parent_abs:
        return True
    return child_abs.startswith(parent_abs + os.sep)


def _worker_alive(t):
    """【G-170 + G-177 修复】检查任务的主 worker 线程是否还活着。"""
    if t is None:
        return False
    th = t.get('_thread_obj')
    if th is None:
        return False
    try:
        return th.is_alive()
    except Exception:
        return False


def _sync_items_done(task_id, item_data):
    """【G-170 修复】主 worker 已退出且 sub_tasks 全部完成 → 标记 items done。"""
    if not item_data or len(item_data) <= 2:
        return
    try:
        db_id = item_data[0]
        identifier = item_data[2]
    except (IndexError, TypeError):
        return
    if not db_id or not identifier:
        return
    try:
        with DB_LOCK:
            conn = get_db()
            try:
                conn.execute(
                    "UPDATE items SET status='done', item_url=?, error_msg=NULL WHERE id=?",
                    (f"https://archive.org/details/{identifier}", db_id)
                )
                conn.commit()
            finally:
                conn.close()
    except Exception as e:
        logging.warning(f"[api_toggle_pause] 同步 items done 失败 (db_id={db_id}): {e}")


def _restart_upload_retries(task_id, item_data, sub_rows):
    """【G-170 + G-177 修复】主 worker 已死 → 对 error 的 sub_tasks 自动重启
    retry_single_file_worker（带路径穿越防护 + realpath/isfile/getsize 检查）。

    【G-194 修复】每个文件先做原子 UPDATE 抢占（防并发 resume 重复启动）。
    【⏱️ 延迟与超时精细控制】单次上限从硬编码 20 改为 _get_max_auto_restart_per_resume()
    返回 (started, skipped_by_limit, skipped_by_race)。
    """
    if not item_data or len(item_data) < 13:
        return 0, 0, 0
    identifier = item_data[2]
    f_path = item_data[12]
    if not identifier or not isinstance(f_path, str) or not f_path:
        return 0, 0, 0

    # 构造 meta_dict（与 api_retry_file 一致）
    meta_dict = {"title": item_data[3], "mediatype": item_data[6] or "software", "collection": item_data[8] or "opensource"}
    if item_data[4]: meta_dict["creator"] = item_data[4]
    if item_data[5]: meta_dict["description"] = item_data[5]
    if item_data[7]: meta_dict["date"] = item_data[7]
    if len(item_data) > 14 and item_data[14]: meta_dict["x-archive-meta-date"] = item_data[14]
    if item_data[9]: meta_dict["language"] = item_data[9]
    if item_data[10]: meta_dict["licenseurl"] = item_data[10]
    if item_data[11]: meta_dict["subject"] = item_data[11]
    opts = {}

    to_restart = []
    to_mark_error = []
    for r in sub_rows:
        st = r[0]
        fn = r[1]
        if st != 'error':
            continue
        if not isinstance(fn, str) or not fn:
            continue
        if '\x00' in fn:
            to_mark_error.append(fn)
            continue
        real_fp = None
        if os.path.isdir(f_path):
            real_fp = os.path.join(f_path, fn)
        elif os.path.isfile(f_path) and os.path.basename(f_path) == fn:
            real_fp = f_path
        if not real_fp:
            to_mark_error.append(fn)
            continue
        try:
            real_fp_real = os.path.realpath(real_fp)
            f_path_real = os.path.realpath(f_path)
        except (OSError, ValueError):
            to_mark_error.append(fn)
            continue
        if not _is_path_inside(real_fp_real, f_path_real):
            to_mark_error.append(fn)
            continue
        if not os.path.isfile(real_fp_real):
            to_mark_error.append(fn)
            continue
        try:
            sz = os.path.getsize(real_fp_real)
        except (OSError, ValueError):
            to_mark_error.append(fn)
            continue
        to_restart.append((real_fp_real, fn, sz))

    # 把路径非法/文件已清理的标 error（更新错误信息）
    if to_mark_error:
        try:
            with DB_LOCK:
                conn = get_db()
                try:
                    for fn in to_mark_error:
                        conn.execute(
                            "UPDATE sub_tasks SET status='error', error_msg=? WHERE task_id=? AND file_path=?",
                            ("本地文件已被清理或路径非法", task_id, fn)
                        )
                    conn.commit()
                finally:
                    conn.close()
        except Exception as e:
            logging.warning(f"[api_toggle_pause] 标路径非法为 error 失败: {e}")

    # 【⏱️ 延迟与超时精细控制】限制单次重启数量（默认 20，用户可配 1-100）
    max_restart = _get_max_auto_restart_per_resume()
    limited = to_restart[:max_restart]
    skipped_by_limit = len(to_restart) - len(limited)

    started = 0
    skipped_by_race = 0
    for fp, fn, sz in limited:
        # 【G-194 修复】原子 UPDATE 抢占（防并发 resume 重复启动）
        try:
            with DB_LOCK:
                conn = get_db()
                try:
                    cur = conn.execute(
                        "UPDATE sub_tasks SET status='uploading', error_msg=NULL "
                        "WHERE task_id=? AND file_path=? AND status='error'",
                        (task_id, fn)
                    )
                    conn.commit()
                    if cur.rowcount == 0:
                        skipped_by_race += 1
                        continue
                finally:
                    conn.close()
        except Exception as e:
            logging.warning(f"[api_toggle_pause] 抢占 sub_task 失败 ({fn}): {e}")
            continue

        try:
            th = threading.Thread(
                target=retry_single_file_worker,
                args=(task_id, identifier, fp, fn, sz, meta_dict, opts),
                daemon=True
            )
            th.start()
            started += 1
        except Exception as e:
            logging.warning(f"[api_toggle_pause] 启动 retry worker 失败 ({fn}): {e}")
            try:
                with DB_LOCK:
                    conn = get_db()
                    try:
                        conn.execute(
                            "UPDATE sub_tasks SET status='error', error_msg=? WHERE task_id=? AND file_path=?",
                            (f"启动失败: {str(e)[:200]}", task_id, fn)
                        )
                        conn.commit()
                    finally:
                        conn.close()
            except Exception:
                pass
    return started, skipped_by_limit, skipped_by_race


@api_bp.route('/api/tasks')
def api_tasks():
    safe = {}
    for k, v in list(UPLOAD_TASKS.items()):
        item_data = v.get('item_data')
        db_id = item_data[0] if item_data else None

        safe[k] = {
            'title': v.get('title', ''),
            'status': v.get('status', 'unknown'),
            'msg': v.get('msg', ''),
            'percent': v.get('percent', 0),
            'speed': v.get('speed', '0.0'),
            'current': v.get('current', ''),
            'total': v.get('total', ''),
            'eta': v.get('eta', ''),
            'raw_line': v.get('raw_line', ''),
            'batch_total': v.get('batch_total', 1),
            'batch_done': v.get('batch_done', 0),
            'task_type': v.get('task_type', 'upload'),
            'download_url': v.get('download_url', ''),
            'download_filename': v.get('download_filename', ''),
            'total_files_count': v.get('total_files_count', 0),
            'total_size_bytes': v.get('total_size_bytes', 0),
            'files_uploaded': v.get('files_uploaded', 0),
            'uploaded_bytes': v.get('uploaded_bytes', 0),
            'is_virtual': v.get('is_virtual', False),
        }

        identifier = None
        if item_data and len(item_data) > 2:
            identifier = item_data[2]
        if not identifier:
            extra_str = v.get('extra_data')
            if extra_str:
                try:
                    extra = json.loads(extra_str)
                    if isinstance(extra, dict):
                        identifier = extra.get('identifier')
                except Exception:
                    pass
        if identifier:
            safe[k]['identifier'] = identifier

        if db_id:
            safe[k]['item_db_id'] = db_id
    return jsonify(safe)


@api_bp.route('/api/tasks/files/<task_id>')
def api_task_files(task_id):
    with DB_LOCK:
        conn = get_db()
        try:
            rows = conn.execute("SELECT file_path, file_size, status, uploaded_bytes, error_msg FROM sub_tasks WHERE task_id=?", (task_id,)).fetchall()
        finally:
            conn.close()

    files = []
    for r in rows:
        files.append({
            "filename": r[0],
            "size": r[1],
            "status": r[2],
            "uploaded_bytes": r[3],
            "error_msg": r[4]
        })
    return jsonify({"files": files})


@api_bp.route('/api/retry_file', methods=['POST'])
def api_retry_file():
    """【R334 + R340 修复 - 2026-11】

    【R334】row is None 时明确拦截，防止 worker 真的上传文件但 sub_tasks 无记录。

    【R340 修复 - 2026-11】原子抢占模式：
      1. 原子 UPDATE 抢占（`WHERE status='error'`），rowcount>0 才算成功
      2. 抢占失败 → 查一次状态 → 返回明确错误（已被抢占/状态不对）
      3. 抢占成功后所有失败分支用 _rollback_claim 回滚 status='error'
         （带 `WHERE status='uploading'` 保护，不误伤已变 done/error）
      4. worker 启动成功 → 正常返回
    防止两次并发调用启动两个 retry_single_file_worker 上传同一文件。
    """
    data = request.get_json() or {}
    task_id = data.get('task_id')
    filename = data.get('filename')
    if not task_id or not filename:
        return jsonify({'status': 'error', 'msg': '参数缺失'})

    if not isinstance(filename, str):
        return jsonify({'status': 'error', 'msg': '文件名无效'})

    if '\x00' in filename:
        return jsonify({'status': 'error', 'msg': '文件名含非法字符'})

    task = UPLOAD_TASKS.get(task_id)
    if not task:
        return jsonify({'status': 'error', 'msg': '父任务不存在'})

    item = task.get('item_data')
    if not item:
        return jsonify({'status': 'error', 'msg': '父任务缺少 item_data'})

    # ========== 【R340 修复】原子抢占：UPDATE ... WHERE status='error' ==========
    # 两个并发请求只有一个 rowcount>0（SQLite 的 UPDATE 原子性保证）
    try:
        with DB_LOCK:
            conn = get_db()
            try:
                cur = conn.execute(
                    "UPDATE sub_tasks SET status='uploading', error_msg=NULL "
                    "WHERE task_id=? AND file_path=? AND status='error'",
                    (task_id, filename)
                )
                conn.commit()
                claimed = (cur.rowcount > 0)
            finally:
                conn.close()
    except Exception as e:
        logging.warning(f"[api_retry_file] 抢占 sub_task 失败: {e}")
        return jsonify({'status': 'error', 'msg': f'抢占失败（DB 异常）: {e}'})

    if not claimed:
        # 抢占失败：查一次状态给用户明确原因
        # 【R334 修复】row is None 时明确告知（用户旧页面/并发清理）
        try:
            with DB_LOCK:
                conn = get_db()
                try:
                    row = conn.execute(
                        "SELECT status FROM sub_tasks WHERE task_id=? AND file_path=?",
                        (task_id, filename)
                    ).fetchone()
                finally:
                    conn.close()
        except Exception:
            row = None

        if row is None:
            return jsonify({
                'status': 'error',
                'msg': '该文件在子任务记录中不存在（可能已被其他 worker 完成或清理），请刷新队列后重试'
            })

        cur_status = row[0] or ''
        if cur_status != 'error':
            status_text = {
                'uploading': '上传中或已被其他请求抢占',
                'waiting': '排队中',
                'done': '已完成',
                'skipped': '云端已存在，已跳过',
            }.get(cur_status, cur_status or '未知')
            return jsonify({
                'status': 'error',
                'msg': f'该文件当前状态为「{status_text}」，不是失败状态，无需重试'
            })
        # 极罕见：cur_status 是 error 但 UPDATE 失败（可能被另一线程并发改回 error）
        return jsonify({
            'status': 'error',
            'msg': '抢占失败（可能被其他请求并发处理），请刷新队列后重试'
        })

    # ========== 抢占成功：后续所有失败分支必须回滚 status='error' ==========
    def _rollback_claim(reason_short):
        """抢占成功后失败时回滚 sub_tasks.status='error'，避免卡在 uploading。

        带 `WHERE status='uploading'` 保护：
          - 若 worker 已启动并把 status 改成 done/error（极端情况），不误伤
          - 若 api_tasks_clean 已删除该记录，不会误伤
        """
        try:
            with DB_LOCK:
                conn = get_db()
                try:
                    conn.execute(
                        "UPDATE sub_tasks SET status='error', error_msg=? "
                        "WHERE task_id=? AND file_path=? AND status='uploading'",
                        (reason_short, task_id, filename)
                    )
                    conn.commit()
                finally:
                    conn.close()
        except Exception as e:
            logging.warning(f"[api_retry_file] 回滚 claim 失败 (task={task_id}, file={filename}): {e}")

    if len(item) < 13:
        _rollback_claim('item_data 结构异常，无法重试')
        return jsonify({'status': 'error', 'msg': 'item_data 结构异常，无法重试'})

    identifier = item[2]
    if not identifier:
        _rollback_claim('identifier 为空，请点「✏️ 编辑」补全')
        return jsonify({'status': 'error', 'msg': '该条目 identifier 为空，无法重试。请点「✏️ 编辑」补全 identifier'})

    f_path = item[12]
    if not isinstance(f_path, str) or not f_path:
        _rollback_claim('item_data 中的 file_path 无效')
        return jsonify({'status': 'error', 'msg': 'item_data 中的 file_path 无效'})

    real_fp = None
    if os.path.isdir(f_path):
        real_fp = os.path.join(f_path, filename)
    elif os.path.isfile(f_path) and os.path.basename(f_path) == filename:
        real_fp = f_path

    if not real_fp:
        _rollback_claim('本地文件已被清理')
        return jsonify({'status': 'error', 'msg': '本地文件已被清理，无法重试'})

    try:
        real_fp_realpath = os.path.realpath(real_fp)
        f_path_realpath = os.path.realpath(f_path)
    except (OSError, ValueError) as e:
        _rollback_claim(f'路径解析失败: {e}')
        return jsonify({'status': 'error', 'msg': f'路径解析失败: {e}'})

    if not _is_path_inside(real_fp_realpath, f_path_realpath):
        logging.warning(
            f"[api_retry_file] 路径穿越/符号链接拦截: task_id={task_id}, "
            f"filename={filename!r}, real_fp_realpath={real_fp_realpath!r}, "
            f"f_path_realpath={f_path_realpath!r}"
        )
        _rollback_claim('文件路径非法')
        return jsonify({'status': 'error', 'msg': '文件路径非法'})

    if not os.path.isfile(real_fp_realpath):
        _rollback_claim('本地文件已被清理或不是普通文件')
        return jsonify({'status': 'error', 'msg': '本地文件已被清理或不是普通文件，无法重试'})

    try:
        sz = os.path.getsize(real_fp_realpath)
    except (OSError, ValueError) as e:
        _rollback_claim(f'读取文件大小失败: {e}')
        return jsonify({'status': 'error', 'msg': f'读取文件大小失败: {e}'})

    meta_dict = {"title": item[3], "mediatype": item[6] or "software", "collection": item[8] or "opensource"}
    if item[4]: meta_dict["creator"] = item[4]
    if item[5]: meta_dict["description"] = item[5]
    if item[7]: meta_dict["date"] = item[7]
    if len(item) > 14 and item[14]: meta_dict["x-archive-meta-date"] = item[14]
    if item[9]: meta_dict["language"] = item[9]
    if item[10]: meta_dict["licenseurl"] = item[10]
    if item[11]: meta_dict["subject"] = item[11]

    opts = {}
    try:
        threading.Thread(
            target=retry_single_file_worker,
            args=(task_id, identifier, real_fp_realpath, filename, sz, meta_dict, opts),
            daemon=True
        ).start()
    except Exception as e:
        logging.error(f"[api_retry_file] 启动 worker 失败: {e}")
        _rollback_claim(f'启动 worker 失败: {str(e)[:200]}')
        return jsonify({'status': 'error', 'msg': f'启动 worker 失败: {e}'})

    return jsonify({'status': 'success', 'msg': '单文件重试已提交'})


@api_bp.route('/api/tasks/retry_task', methods=['POST'])
def api_retry_task():
    """【R335 修复 - 2026-11 重构】任务级重试。

    结构（快路径锁外 + 慢路径锁内）：
      1. 快路径（锁外只读，不写状态）：参数校验 / t 存在 / status=='error'
         / extra 合法 / t_type 前置白名单（upload 明确拒绝，unknown 明确拒绝）
      2. 慢路径（_RETRY_TASK_LOCK 内）：双重检查 status=='error' → 改 'waiting'
         → 启动 worker（防并发请求同时启动多个 worker）
      3. 锁外 save_task_to_db（内部有 DB_LOCK，避免锁内耗时）
    """
    data = request.get_json() or {}
    task_id = data.get('task_id')
    if not task_id:
        return jsonify({"status": "error", "msg": "参数缺失"})

    t = UPLOAD_TASKS.get(task_id)
    if t is None:
        return jsonify({"status": "error", "msg": "任务不存在"})

    # ========== 快路径（锁外只读）：所有只读校验前置，避免无谓加锁 ==========
    if t.get('status') != 'error':
        return jsonify({"status": "error", "msg": "只能重试失败的任务"})

    extra_str = t.get('extra_data')
    if not extra_str:
        return jsonify({"status": "error", "msg": "缺少恢复参数"})

    try:
        extra = json.loads(extra_str)
    except Exception:
        return jsonify({"status": "error", "msg": "参数解析失败"})

    if not isinstance(extra, dict):
        return jsonify({"status": "error", "msg": "参数格式错误（非字典）"})

    t_type = t.get('task_type')

    # 【R334 修复】upload 类型明确拒绝（走 /api/retry_single）
    if t_type == 'upload':
        return jsonify({
            "status": "error",
            "msg": "上传任务请使用队列卡片上的「🔄 重试」按钮，或到「待处理重试队列」中操作（不要用任务级重试）"
        })

    # 【R335 改进】未知任务类型前置拒绝（避免 error→waiting→error 的微秒级闪动）
    if t_type not in _SUPPORTED_TASK_TYPES:
        return jsonify({"status": "error", "msg": "未知任务类型"})

    # ========== 慢路径（锁内）：双重检查 + 原子抢占 + 启动 worker ==========
    old_status = 'error'
    old_msg = t.get('msg', '')
    start_err = None

    with _RETRY_TASK_LOCK:
        # 双重检查：从快路径到这里之间，可能已被其他并发请求抢走（改了 status 或启动 worker）
        if t.get('status') != 'error':
            return jsonify({
                "status": "error",
                "msg": "任务状态已变化（可能已被其他请求处理），请刷新后重试"
            })

        t['status'] = 'waiting'
        t['msg'] = '重新排队...'

        try:
            if t_type in ('download', 'download_folder'):
                nt = threading.Thread(target=download_folder_worker, args=(task_id, extra['folder_name'], extra['files_list'], extra['dl_dir']), daemon=True)
                t['_thread_obj'] = nt
                nt.start()
            elif t_type == 'delete':
                nt = threading.Thread(target=delete_files_worker, args=(task_id, extra['identifier'], extra['filenames'], extra['db_id']), daemon=True)
                t['_thread_obj'] = nt
                nt.start()
            elif t_type == 'append':
                opts_raw = extra.get('opts')
                if not isinstance(opts_raw, dict):
                    opts_raw = {}
                meta_raw = extra.get('meta')
                if not isinstance(meta_raw, dict):
                    meta_raw = {}
                nt = threading.Thread(target=append_files_worker, args=(task_id, extra['identifier'], extra['file_infos'], meta_raw, extra['db_id'], opts_raw), daemon=True)
                t['_thread_obj'] = nt
                nt.start()
            elif t_type == 'rename':
                nt = threading.Thread(target=rename_files_worker, args=(task_id, extra['identifier'], extra['rename_list']), daemon=True)
                t['_thread_obj'] = nt
                nt.start()
            elif t_type == 'pull':
                nt = threading.Thread(target=pull_from_ia_worker, args=(task_id, extra['owner'], extra.get('current_page', 1)), daemon=True)
                t['_thread_obj'] = nt
                nt.start()
            elif t_type == 'push_meta':
                nt = threading.Thread(target=push_metadata_worker, args=(task_id, extra['db_id']), daemon=True)
                t['_thread_obj'] = nt
                nt.start()
            # 注：走到这里 t_type 已在白名单内（快路径校验），不会再落入 else。
        except KeyError as ke:
            t['status'] = old_status
            t['msg'] = f'恢复参数缺失: {ke}'
            start_err = f"恢复参数缺失: {ke}"
        except Exception as e:
            t['status'] = 'error'
            t['msg'] = f'启动 worker 失败: {e}'
            start_err = f"启动 worker 失败: {e}"

    # ========== 锁外持久化（内部有 DB_LOCK，避免锁内耗时） ==========
    core.save_task_to_db(task_id)

    if start_err:
        return jsonify({"status": "error", "msg": start_err})

    return jsonify({"status": "success", "msg": "已重新提交至队列"})


@api_bp.route('/api/tasks/toggle_pause', methods=['POST'])
def api_toggle_pause():
    """【G-170 + G-177 + G-87 + G-194 + G-195 + R103 + R313 + R317 修复 - 2026-11】

    - pause：直接改状态为 paused（worker 会在检查点响应）
    - resume：
        0. 状态检查（R103 + R313 修复后）：
           - paused → 标准恢复流程
           - waiting + upload + 父 worker 活着 → 幂等返回 success（防重复点击）
           - waiting + upload + 父 worker 已死 → R103 追加重启
           - error + upload → R313 追加重启
           - 其他（done / error 非 upload / waiting 非 upload）→ 拒绝
        1. worker alive → 只改 status='waiting'（worker 自己恢复）
        2. worker 已死 + sub_tasks 全部 done/skipped → 直接标 done + 同步 items
        3. worker 已死 + 有 error/waiting 的 sub_tasks →
           把 waiting 标 error（孤儿），自动重启 retry_single_file_worker
           （【R317 修复】只标 'waiting'，不动 'uploading'，避免误伤正在跑的 worker）
           （【G-194】原子 UPDATE 抢占，防并发重复启动）
        4. 非 upload 类型且 worker 已死 → 标 error + msg 提示手动「重试」
    - action 白名单：只接受 pause / resume（G-87）
    """
    data = request.get_json() or {}
    task_id = data.get('task_id')
    action = data.get('action')

    if not task_id:
        return jsonify({"status": "error", "msg": "参数缺失"})

    t = UPLOAD_TASKS.get(task_id)
    if t is None:
        return jsonify({"status": "error", "msg": "任务不存在"})

    if t.get('is_virtual'):
        return jsonify({"status": "error", "msg": "虚拟任务（浏览器下载）不支持暂停"})

    # 【G-87 修复】action 白名单
    if action not in ('pause', 'resume'):
        return jsonify({"status": "error", "msg": "无效操作"})

    if action == 'pause':
        t['status'] = 'paused'
        t['msg'] = '⏸️ 任务已暂停（槽位由后台释放）'
        core.save_task_to_db(task_id)
        return jsonify({"status": "success"})

    # ===== resume 分支 =====
    _current_status = t.get('status')
    task_type = t.get('task_type')
    _was_alive = _worker_alive(t)

    if _current_status == 'paused':
        pass  # 标准恢复流程
    elif _current_status == 'waiting' and task_type == 'upload':
        if _was_alive:
            return jsonify({"status": "success", "msg": "任务正在恢复中，无需重复操作"})
        # worker 已死 → 走 R103 追加重启逻辑
    elif _current_status == 'error' and task_type == 'upload':
        # 【R313 修复】error 状态下允许 R103 追加重启
        pass
    else:
        return jsonify({"status": "error", "msg": "任务不是暂停/失败状态，无需恢复"})

    if _was_alive:
        t['status'] = 'waiting'
        t['msg'] = '▶️ 恢复中，等待并发槽位...'
        core.save_task_to_db(task_id)
        return jsonify({"status": "success", "msg": "已提交恢复"})

    if task_type != 'upload':
        t['status'] = 'error'
        t['msg'] = '❌ 主线程已退出，请点「🔄 重试」重新排队'
        core.save_task_to_db(task_id)
        return jsonify({"status": "error", "msg": "主线程已退出，请点「🔄 重试」重新排队"})

    try:
        with DB_LOCK:
            conn = get_db()
            try:
                sub_rows = conn.execute(
                    "SELECT status, file_path FROM sub_tasks WHERE task_id=?",
                    (task_id,)
                ).fetchall()
            finally:
                conn.close()
    except Exception as e:
        logging.warning(f"[api_toggle_pause] 查 sub_tasks 失败 (task={task_id}): {e}")
        t['status'] = 'error'
        t['msg'] = f'❌ 查子任务失败: {e}'
        core.save_task_to_db(task_id)
        return jsonify({"status": "error", "msg": f"查子任务失败: {e}"})

    if not sub_rows:
        t['status'] = 'error'
        t['msg'] = '❌ 主线程已退出且无子任务记录，请点「🔄 重试」重新排队'
        core.save_task_to_db(task_id)
        return jsonify({"status": "error", "msg": "主线程已退出，请点「🔄 重试」"})

    total_cnt = len(sub_rows)
    done_cnt = sum(1 for r in sub_rows if r[0] in ('done', 'skipped'))

    if done_cnt == total_cnt:
        t['status'] = 'done'
        t['msg'] = '✅ 全部完成（恢复时检测到）'
        t['percent'] = 100
        t['files_uploaded'] = done_cnt
        t['uploaded_bytes'] = t.get('total_size_bytes', 0)
        t['current'] = str(done_cnt)
        t['speed'] = '0.0'
        core.save_task_to_db(task_id)
        _sync_items_done(task_id, t.get('item_data'))
        return jsonify({"status": "success", "msg": "全部已完成，已标记为完成"})

    # 【R317 修复 - 2026-11】只标 'waiting' 为 'error'，不动 'uploading'
    try:
        with DB_LOCK:
            conn = get_db()
            try:
                conn.execute(
                    "UPDATE sub_tasks SET status='error', error_msg='孤儿状态（主线程已退出），恢复时标记为 error 以便重试' "
                    "WHERE task_id=? AND status='waiting'",
                    (task_id,)
                )
                conn.commit()
            finally:
                conn.close()
    except Exception as e:
        logging.warning(f"[api_toggle_pause] 标孤儿为 error 失败 (task={task_id}): {e}")

    # 重新查 sub_tasks（拿到完整信息）
    try:
        with DB_LOCK:
            conn = get_db()
            try:
                sub_rows = conn.execute(
                    "SELECT status, file_path FROM sub_tasks WHERE task_id=?",
                    (task_id,)
                ).fetchall()
            finally:
                conn.close()
    except Exception as e:
        logging.warning(f"[api_toggle_pause] 重查 sub_tasks 失败: {e}")
        t['status'] = 'error'
        t['msg'] = f'❌ 重查子任务失败: {e}'
        core.save_task_to_db(task_id)
        return jsonify({"status": "error", "msg": f"重查子任务失败: {e}"})

    started, skipped_by_limit, skipped_by_race = _restart_upload_retries(task_id, t.get('item_data'), sub_rows)

    if started > 0:
        t['status'] = 'waiting'
        t['msg'] = f'▶️ 已恢复，自动重启 {started} 个文件的重试'
        if skipped_by_limit > 0:
            t['msg'] += f'（剩余 {skipped_by_limit} 个可再次点击「继续」追加重启）'
        if skipped_by_race > 0:
            t['msg'] += f'（{skipped_by_race} 个文件被其他请求抢占，已跳过）'
        core.save_task_to_db(task_id)
        return jsonify({"status": "success", "msg": t['msg']})
    else:
        t['status'] = 'error'
        t['msg'] = '❌ 恢复失败：无法重启任何文件（本地文件可能已清理）'
        core.save_task_to_db(task_id)
        return jsonify({"status": "error", "msg": t['msg']})


@api_bp.route('/api/tasks/clear_log', methods=['POST'])
def api_tasks_clear_log():
    data = request.get_json() or {}
    task_id = data.get('task_id')
    if task_id:
        t = UPLOAD_TASKS.get(task_id)
        if t is not None:
            t['raw_line'] = ''
            core.save_task_to_db(task_id)
    return jsonify({"status": "success"})


@api_bp.route('/api/tasks/clean', methods=['POST'])
def api_tasks_clean():
    """【Bug 1+2 + 改进 修复 - 2026-11】清理已结束任务。

    【R415 修复 - 2026-11】分批 DELETE（每批 500），避免 to_remove > 999 时
    SQLite "too many SQL variables"。保持事务语义：全部成功 commit，任一失败 rollback。
    """
    to_remove = []
    for k, v in list(UPLOAD_TASKS.items()):
        if v.get('status') not in ('done', 'error'):
            continue
        thread_obj = v.get('_thread_obj')
        if thread_obj is not None and thread_obj.is_alive():
            continue
        to_remove.append(k)

    if not to_remove:
        return jsonify({"status": "success", "count": 0})

    # 【R415 修复】分批 DELETE（每批 500，避免 SQLite 999 变量上限）
    _BATCH_SIZE = 500
    try:
        with DB_LOCK:
            conn = get_db()
            try:
                for _i in range(0, len(to_remove), _BATCH_SIZE):
                    _batch = to_remove[_i:_i + _BATCH_SIZE]
                    _ph = ','.join(['?'] * len(_batch))
                    conn.execute(f"DELETE FROM sub_tasks WHERE task_id IN ({_ph})", _batch)
                    conn.execute(f"DELETE FROM upload_tasks WHERE task_id IN ({_ph})", _batch)
                conn.commit()
            finally:
                conn.close()
    except Exception as e:
        logging.warning(f"[api_tasks_clean] DB 删除失败: {e}")
        return jsonify({"status": "error", "msg": f"清理失败: {e}", "count": 0})

    for k in to_remove:
        UPLOAD_TASKS.pop(k, None)

    return jsonify({"status": "success", "count": len(to_remove)})


@api_bp.route('/api/tasks/del', methods=['POST'])
def api_tasks_del():
    """【Bug 4 修复 - 2026-11】删除单个任务。"""
    data = request.get_json() or {}
    tid = data.get('task_id')
    if not tid:
        return jsonify({"status": "success"})

    UPLOAD_TASKS.pop(tid, None)

    last_err = None
    for attempt in range(3):
        try:
            with DB_LOCK:
                conn = get_db()
                try:
                    conn.execute("DELETE FROM upload_tasks WHERE task_id=?", (tid,))
                    conn.execute("DELETE FROM sub_tasks WHERE task_id=?", (tid,))
                    conn.commit()
                finally:
                    conn.close()
            return jsonify({"status": "success"})
        except Exception as e:
            last_err = e
            if attempt < 2:
                time.sleep(0.3)

    logging.warning(f"[api_tasks_del] DB 删除失败 3 次 (task_id={tid}): {last_err}")
    return jsonify({"status": "error", "msg": f"删除失败: {last_err}"})


@api_bp.route('/api/conc_status')
def api_conc_status():
    return jsonify(CONCURRENCY_LIMITER.status())


@api_bp.route('/api/logs')
def api_logs():
    return jsonify({'logs': list(MEM_LOG.logs)})