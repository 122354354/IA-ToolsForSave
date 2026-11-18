# netdisk_routes_api_upload.py
# 分片上传 / 批量追加 相关 API
# 【Bug 1 修复 - 第 4/5 步】api_batch_append 的 finalize 分支合并 .__ND_PART__ 分片
# 【Bug 24 修复】合并失败的分片残留被清理，防止被当作普通文件上传到 IA
# 【改进点 7 - 第 1/2 步】finalize 复制完成后清理 chunk_td（减少临时文件占用）
#
# 【🔴 R414 修复 - 2026-11（R414 第 2 个文件 - 中危）】
#   问题：api_batch_append（批量追加到多个条目）批量创建 tid + 启动
#        append_files_worker，未检查同 identifier 活跃 append 任务。
#   修复：收集活跃 append 任务 identifier 集合，过滤冲突 group（进 skipped）。
#
# 【🔴 R437 修复 - 2026-11（R414 第 2 个文件追加修复）】
#   问题：if not items_data: 未区分"全部冲突" vs "全部不存在/无权"。
#   修复：根据 skipped_identifiers 是否为空分支返回不同消息。
#
# 【🔴 R440 修复 - 2026-11（UX - 冲突时避免浪费用户上传时间 - 本轮）】
#   问题：R414/R437 修复后，冲突检查在"文件处理之后"（base_td 已创建、
#        local_paths 已复制、is_finalize 已合并分片）才执行：
#        - 用户上传 200MB 文件（浏览器 / 分片 / 本地路径）
#        - 全部冲突 → 清理 base_td → 返回 error
#        - 用户的 local_paths 复制时间浪费（200MB 约 1-2 秒）
#        - 用户的分片合并时间浪费（合并 N 个分片可能更久）
#   修复：把 items_data 查询 + 冲突检查从"文件处理之后"移到"参数解析之后、
#        文件处理之前"：
#        1. 参数解析后立即：opts = _build_upload_opts(form) + owner = session.get('owner')
#        2. 立即查询 items_data + 检查冲突
#        3. 全部冲突 → 提前返回 error（不创建 base_td、不复制 local_paths、不合并分片）
#        4. is_finalize 场景顺带清理 chunk_td（用户的分片已无用）
#        5. 部分冲突 → 过滤 items_data 后继续原逻辑
#   为什么仍无法避免"用户上传的 files（浏览器）"被占用：
#        - Flask 的 request.files 已经把文件内容读到内存或临时文件里（请求体）
#        - 这是 HTTP 协议层就发生的，无法在 Flask 层避免
#        - 但 Flask 的临时文件在请求结束后自动清理 → 不会累积
#   为什么仍然值得提前检查：
#        - local_paths 场景：避免复制 200MB 到 base_td（1-2 秒）
#        - is_finalize 场景：避免合并分片（N 个分片读取+写入，可能更久）
#        - 用户更早看到错误，不用等文件处理完
#   消息改进：
#        - 全部冲突 → "本次上传的文件已被丢弃（未占用磁盘）。请等待完成..."
#          （明确告知用户需要重传）
#        - 部分冲突 → 原消息（"N 个条目已有追加任务进行中，已跳过"）
#
# 【📝 保留记录 - 2026-11，低优先，以后修】
#   R434（已修）：前端 doBatchAppendSubmit 显示 skipped（R434）
#   R435：R414 检查有微秒级竞态窗口（同 R424/R430）
#   R436：R414 检查不覆盖"append + upload"跨类型同 identifier 并发（同 R431）
#   R438：skipped_identifiers 可能有重复（用户传重复 item_ids 时）→ 用 set 去重
#        可优化，但前端不会传重复 id，概率低
#   R439：R414 第 2 个文件的检查与 R428（netdisk_routes_views_upload.py 的
#        append_files）独立实现，逻辑一致，未来变更需同步
#   R458（本轮记录）：R440 提前检查后，部分冲突场景（用户传 5 个 item_ids，3 个冲突）
#        仍会创建 base_td（因为 2 个不冲突需要处理）。base_td 里包含用户本次上传
#        的全部文件（包括 3 个冲突条目的文件），这 3 个文件不复制到 item_td
#        （原行为不变），请求结束后 base_td 被 cleanup 清理。记录。
#   R459（本轮记录）：R440 提前检查后，全部冲突 + is_finalize 场景清理 chunk_td，
#        用户需重新上传所有分片。但至少避免了合并分片的时间浪费（R440 核心收益）。
#   R460（本轮记录）：R440 提前检查后，全部冲突 + local_paths 场景不创建 base_td，
#        用户的本地文件不受影响（重新提交时会重新复制到新 base_td）。正确。
#
# 【📝 同源问题记录 - 其他文件，后续统一修】
#   - R415（已修）：netdisk_routes_api_tasks.py 的 api_tasks_clean 未分批

import os
import re
import json
import uuid
import shutil
import zipfile
import threading
import time
import logging
from flask import request, jsonify, session

import netdisk_core as core
from netdisk_blueprints import api_bp
from netdisk_core import (
    UPLOAD_TASKS, DB_LOCK, get_db,
    cleanup_temp_dir, _build_upload_opts,
    append_files_worker,
)
from netdisk_utils import _merge_part_files


_PART_SUFFIX_RE = re.compile(r'^(.+)\.__ND_PART__(\d+)$')


def _cleanup_leftover_parts(dir_path):
    """【Bug 24 修复】清理 dir_path 里所有残留的分片文件（合并失败的）。
    返回清理数量。"""
    if not dir_path or not os.path.isdir(dir_path):
        return 0
    count = 0
    for root, dirs, files in os.walk(dir_path):
        for fname in files:
            if _PART_SUFFIX_RE.match(fname):
                try:
                    os.remove(os.path.join(root, fname))
                    count += 1
                except:
                    pass
    return count


@api_bp.route('/api/upload_chunk', methods=['POST'])
def api_upload_chunk():
    try:
        batch_id = request.form.get('chunk_batch_id')
        rel_path = request.form.get('chunk_rel_path', '')
        file = request.files.get('file_chunk')
        if not batch_id or not file:
            return jsonify({"status": "error", "msg": "参数缺失"})
        batch_id = re.sub(r'[^a-zA-Z0-9_\-]', '', batch_id)
        if not batch_id:
            return jsonify({"status": "error", "msg": "无效的批次号"})
        batch_dir = os.path.join(core.TEMP_UPLOAD_DIR, batch_id)
        os.makedirs(batch_dir, exist_ok=True)
        safe_path = rel_path.replace('\\', '/').replace('../', '').lstrip('/')
        if not safe_path:
            safe_path = file.filename or str(uuid.uuid4())
        target_path = os.path.join(batch_dir, safe_path)
        os.makedirs(os.path.dirname(target_path), exist_ok=True)
        file.save(target_path)
        return jsonify({"status": "success"})
    except Exception as e:
        return jsonify({"status": "error", "msg": str(e)})


@api_bp.route('/api/batch_append', methods=['POST'])
def api_batch_append():
    ids = request.form.getlist('item_ids')
    files = [f for f in request.files.getlist('files') if f and f.filename]
    local_paths_json = request.form.get('local_paths', '[]')
    try:
        local_paths = json.loads(local_paths_json)
        if not isinstance(local_paths, list):
            local_paths = []
    except:
        local_paths = []

    is_finalize = request.form.get('is_chunk_finalize') == 'yes'

    if not ids or (not files and not local_paths and not is_finalize):
        return jsonify({'status': 'error', 'msg': '参数缺失，请选择文件和条目'})

    is_unzip = request.form.get('unzip_zip') == 'yes'
    is_zip_upload = request.form.get('zip_upload') == 'yes'
    if is_unzip and is_zip_upload:
        is_unzip = False

    # 【R440 修复 - 2026-11】提前解析 opts + owner（不依赖 base_td）
    opts = _build_upload_opts(request.form)
    owner = session.get('owner')

    # 【R440 修复 - 2026-11】提前查询 items_data + 检查冲突（在文件处理之前）
    #   全部冲突 → 提前返回 error（不创建 base_td、不复制 local_paths、不合并分片）
    #   is_finalize 场景顺带清理 chunk_td（用户的分片已无用）
    items_data = []
    with DB_LOCK:
        conn = get_db()
        try:
            for item_id in ids:
                row = conn.execute("SELECT identifier, title, mediatype FROM items WHERE id=? AND owner=?", (item_id, owner)).fetchone()
                if row:
                    items_data.append((item_id, row[0], row[1], row[2]))
        finally:
            conn.close()

    # 【R414 修复】检查同 identifier 活跃 append 任务
    _active_append_identifiers = set()
    for _tid, _t in list(UPLOAD_TASKS.items()):
        if _t.get('task_type') == 'append' and _t.get('status') in ('waiting', 'uploading', 'paused'):
            _tid_extra = _t.get('extra_data')
            if _tid_extra:
                try:
                    _extra = json.loads(_tid_extra)
                    if isinstance(_extra, dict):
                        _ident = _extra.get('identifier')
                        if _ident:
                            _active_append_identifiers.add(_ident)
                except Exception:
                    pass

    skipped_identifiers = []
    if _active_append_identifiers:
        filtered_items_data = []
        for item_id, identifier, title, mediatype in items_data:
            if identifier in _active_append_identifiers:
                skipped_identifiers.append(identifier)
                continue
            filtered_items_data.append((item_id, identifier, title, mediatype))
        items_data = filtered_items_data

    # 【R440 修复】全部冲突 → 提前返回，不创建 base_td / 不复制文件 / 不合并分片
    if not items_data:
        # is_finalize 场景：清理用户已上传的分片（已无用，避免占用磁盘）
        if is_finalize:
            _batch_id = request.form.get('chunk_batch_id')
            _batch_id = re.sub(r'[^a-zA-Z0-9_\-]', '', _batch_id or '')
            if _batch_id:
                _chunk_td = os.path.join(core.TEMP_UPLOAD_DIR, _batch_id)
                if os.path.exists(_chunk_td):
                    try:
                        cleanup_temp_dir(_chunk_td)
                        logging.info(f"[R440] 全部冲突，已清理用户已上传的分片: {_chunk_td}")
                    except Exception as _e:
                        logging.warning(f"[R440] 清理 chunk_td 失败: {_e}")

        # 【R437 修复】区分"全部冲突" vs "全部不存在/无权"
        if skipped_identifiers:
            _msg = '所选条目均已有追加任务进行中，本次上传的文件已被丢弃（未占用磁盘）。请等待完成或先在队列中删除该任务，然后再试'
            _shown = skipped_identifiers[:5]
            _extra_str = f'（共 {len(skipped_identifiers)} 个：{", ".join(_shown)}{"..." if len(skipped_identifiers) > 5 else ""}）'
            _msg += _extra_str
        else:
            _msg = '所选条目不存在或无权访问（可能已被其他页面删除），请刷新页面后重试'
        return jsonify({
            'status': 'error',
            'msg': _msg,
            'skipped': skipped_identifiers
        })

    # ========== 以下为原文件处理逻辑（部分冲突场景继续执行） ==========
    base_td = os.path.join(core.TEMP_UPLOAD_DIR, str(uuid.uuid4())[:8])
    os.makedirs(base_td, exist_ok=True)

    leftover_count = 0  # 【Bug 24 修复】分片合并失败的残留数量

    if local_paths:
        for lp in local_paths:
            if not os.path.exists(lp): continue
            if os.path.isfile(lp):
                fname = os.path.basename(lp)
                dst = os.path.join(base_td, fname)
                if os.path.exists(dst):
                    name, ext = os.path.splitext(fname)
                    dst = os.path.join(base_td, f"{name}_{uuid.uuid4().hex[:4]}{ext}")
                shutil.copy2(lp, dst)
            elif os.path.isdir(lp):
                dirname = os.path.basename(lp.rstrip('/\\'))
                dst_dir = os.path.join(base_td, dirname)
                if os.path.exists(dst_dir):
                    dst_dir = os.path.join(base_td, f"{dirname}_{uuid.uuid4().hex[:4]}")
                shutil.copytree(lp, dst_dir)
    elif is_finalize:
        batch_id = request.form.get('chunk_batch_id')
        batch_id = re.sub(r'[^a-zA-Z0-9_\-]', '', batch_id or '')
        if not batch_id:
            cleanup_temp_dir(base_td)
            return jsonify({'status': 'error', 'msg': '无效的批次号'})
        chunk_td = os.path.join(core.TEMP_UPLOAD_DIR, batch_id)
        if not os.path.exists(chunk_td):
            cleanup_temp_dir(base_td)
            return jsonify({'status': 'error', 'msg': '暂存文件丢失，请重新上传'})

        # 【Bug 1 修复 - 第 4/5 步】finalize 前先合并分片
        _merge_part_files(chunk_td)

        # 【Bug 24 修复】清理合并失败的分片残留
        leftover_count = _cleanup_leftover_parts(chunk_td)
        if leftover_count > 0:
            logging.warning(f"⚠️ [批量追加] 检测到 {leftover_count} 个分片合并失败，已清理")

        for it in os.listdir(chunk_td):
            src = os.path.join(chunk_td, it)
            dst = os.path.join(base_td, it)
            if os.path.isdir(src):
                shutil.copytree(src, dst)
            else:
                shutil.copy2(src, dst)

        # 【改进点 7 - 第 1/2 步】复制完成后清理 chunk_td，减少临时文件占用
        cleanup_temp_dir(chunk_td)
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
            if not safe_path: continue
            target_path = os.path.join(base_td, safe_path)
            os.makedirs(os.path.dirname(target_path) if os.path.dirname(target_path) else base_td, exist_ok=True)
            f.save(target_path)

    if not os.listdir(base_td):
        cleanup_temp_dir(base_td)
        if leftover_count > 0:
            return jsonify({'status': 'error', 'msg': f'分片上传失败（{leftover_count} 个分片合并失败），请重新上传'})
        return jsonify({'status': 'error', 'msg': '没有找到有效文件'})

    if is_zip_upload:
        zip_name = request.form.get('zip_custom_name', '').strip() or 'archive'
        zip_name = re.sub(r'[\\/:\*\?"<>\|\.\.]', '_', zip_name).strip('_') or 'archive'
        if not zip_name.endswith('.zip'):
            zip_name += '.zip'
        zip_path = os.path.join(core.TEMP_UPLOAD_DIR, str(uuid.uuid4())[:8] + '_' + zip_name)
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_STORED) as z:
            for root, dirs, fs in os.walk(base_td):
                for file in fs:
                    z.write(os.path.join(root, file), os.path.relpath(os.path.join(root, file), base_td))
        cleanup_temp_dir(base_td)
        base_td = os.path.join(core.TEMP_UPLOAD_DIR, str(uuid.uuid4())[:8])
        os.makedirs(base_td, exist_ok=True)
        shutil.move(zip_path, os.path.join(base_td, zip_name))

    if is_unzip:
        for root, dirs, fs in os.walk(base_td):
            for file in fs:
                if file.lower().endswith('.zip'):
                    z_path = os.path.join(root, file)
                    try:
                        with zipfile.ZipFile(z_path, 'r') as z:
                            total_size = 0
                            for member in z.infolist():
                                if '..' in member.filename or member.filename.startswith('/'):
                                    continue
                                total_size += member.file_size
                                if total_size > 10 * 1024 * 1024 * 1024:
                                    raise Exception("ZIP解压后总大小超过 10GB，已拦截。")
                                z.extract(member, base_td)
                        os.remove(z_path)
                    except Exception as e:
                        logging.error(f"解压失败: {e}")

    base_file_infos = []
    for root, dirs, fs in os.walk(base_td):
        for file in fs:
            fp = os.path.join(root, file)
            rel = os.path.relpath(fp, base_td).replace('\\', '/')
            sz = os.path.getsize(fp)
            base_file_infos.append({'fp': fp, 'rel': rel, 'sz': sz})

    if not base_file_infos:
        cleanup_temp_dir(base_td)
        if leftover_count > 0:
            return jsonify({'status': 'error', 'msg': f'分片上传失败（{leftover_count} 个分片合并失败），请重新上传'})
        return jsonify({'status': 'error', 'msg': '没有有效文件'})

    # 锁外做文件复制 + 创建任务 + 启动线程
    task_ids = []
    for item_id, identifier, title, mediatype in items_data:
        meta = {"title": title or identifier, "mediatype": mediatype or "software"}
        item_td = os.path.join(core.TEMP_UPLOAD_DIR, str(uuid.uuid4())[:8])
        os.makedirs(item_td, exist_ok=True)
        item_file_infos = []
        for f_info in base_file_infos:
            dst = os.path.join(item_td, f_info['rel'])
            os.makedirs(os.path.dirname(dst) if os.path.dirname(dst) else item_td, exist_ok=True)
            shutil.copy2(f_info['fp'], dst)
            item_file_infos.append({'fp': dst, 'rel': f_info['rel'], 'sz': f_info['sz']})

        extra_data = {
            'identifier': identifier,
            'file_infos': item_file_infos,
            'meta': meta,
            'db_id': item_id,
            'opts': opts,
        }
        tid = str(uuid.uuid4())[:8]
        UPLOAD_TASKS[tid] = {
            "status": "waiting", "title": f"[批量追加] {meta.get('title', identifier)}",
            "task_type": "append",
            "created_at": time.time(), "extra_data": json.dumps(extra_data)
        }
        core.save_task_to_db(tid)
        t = threading.Thread(target=append_files_worker, args=(tid, identifier, item_file_infos, meta, item_id, opts), daemon=True)
        UPLOAD_TASKS[tid]['_thread_obj'] = t
        t.start()
        task_ids.append(tid)

    cleanup_temp_dir(base_td)
    msg = '批量追加任务已提交'
    if skipped_identifiers:
        _shown = skipped_identifiers[:3]
        _extra_str = f'（{len(skipped_identifiers)} 个条目已有追加任务进行中，已跳过：{", ".join(_shown)}{"..." if len(skipped_identifiers) > 3 else ""}）'
        msg += _extra_str
    if leftover_count > 0:
        msg += f'（警告：{leftover_count} 个分片合并失败已清理，请重新上传失败的文件）'
    return jsonify({
        'status': 'success',
        'msg': msg,
        'task_ids': task_ids,
        'count': len(task_ids),
        'skipped': skipped_identifiers
    })