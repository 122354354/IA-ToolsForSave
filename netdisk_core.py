# netdisk_core.py
# 此文件作为统一入口，汇总拆分出去的各个核心模块，保持与 netdisk.py 的完全兼容

import os
import sys
import json

# 1. 导入所有状态、路径、数据库操作
from netdisk_state import (
    HOME_DIR, DB_FILE, CONFIG_FILE, TEMP_UPLOAD_DIR, THUMB_DIR, WARP_DIR, BACKUP_DIR,
    UPLOAD_TASKS, DB_LOCK, CONFIG_LOCK, APPEND_CWD_LOCK, UPLOAD_COOLDOWN_LOCK,
    LANG_OPTIONS, LICENSE_OPTIONS, MEDIA_OPTIONS, COLLECTION_OPTIONS,
    get_config, save_config, cfg_get, get_db, init_db, get_active_account, get_max_concurrent, get_folder_tree_options
)

# 2. 导入所有的工具函数、API请求、解密、S3分块等
from netdisk_utils import (
    get_ssl_verify, format_bytes, get_req_proxies, IA_LIMITER, ia_get, CONCURRENCY_LIMITER,
    ID_CHECK_CACHE, ID_CHECK_LOCK, IP_ID_CHECK, PULL_CACHE, PULL_LOCK, REFRESH_COOLDOWN_MAP, REFRESH_LOCK,
    THUMB_FAIL_CACHE, THUMB_IP_LOG, IP_REQUEST_LOG, MEM_LOG, ensure_ia_config, get_cli_env, _get_s3_credentials,
    _is_system_file, cleanup_temp_dir, _fetch_and_cache_ia_files, fetch_zip_structure, fetch_user_stats,
    upload_custom_thumb_py, compute_magnet, save_task_to_db, _delete_ia_file, _rename_ia_file,
    _push_tags_update, _collect_meta_form, _build_upload_opts, ProgressFileWrapper, sign_s3_v4, _upload_large_file_s3,
    _auto_backup_worker, _auto_clean_trash_worker, _clean_memory_tasks_worker
)

# 3. 导入所有的后台工作线程（上传/下载/删除/追加/推元数据/重命名/拉取）
from netdisk_workers import (
    upload_worker, retry_single_file_worker, download_file_worker, download_folder_worker,
    push_metadata_worker, delete_files_worker, append_files_worker, rename_files_worker, pull_from_ia_worker
)