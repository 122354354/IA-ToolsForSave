# netdisk_utils.py
# 汇总文件：统一导出所有工具函数、缓存、限流器、Worker 辅助函数
# 外部代码继续 from netdisk_utils import xxx 即可，无需改动
# 【Bug 1 修复 - 第 2/5 步】导出 _merge_part_files
# 【改进点 80 - 2/6】导出 _append_log（统一日志追加工具，供 4 个 worker 复用）

from netdisk_utils_common import (
    format_bytes, cleanup_temp_dir, _is_system_file,
    _merge_part_files,
    _append_log,  # 【改进点 80 - 2/6】
    ID_CHECK_CACHE, ID_CHECK_LOCK, IP_ID_CHECK,
    MAGNET_FAIL_CACHE, MAGNET_LOCK,
    MAGNET_COUNT, MAGNET_COUNT_LOCK, MAGNET_COUNT_CV,
    PULL_CACHE, PULL_LOCK,
    REFRESH_COOLDOWN_MAP, REFRESH_LOCK,
    RENAME_COOLDOWN_MAP, RENAME_ACCOUNT_HISTORY, RENAME_LOCK,
    THUMB_FAIL_CACHE, THUMB_IP_LOG, IP_REQUEST_LOG,
    _magnet_acquire, _magnet_release,
    MemoryLogHandler, MEM_LOG,
    save_task_to_db,
)

from netdisk_utils_net import (
    get_ssl_verify, get_req_proxies,
    IARateLimiter, IA_LIMITER, ia_get,
    ConcurrencyLimiter, CONCURRENCY_LIMITER,
    ensure_ia_config, get_cli_env, _get_s3_credentials,
)

from netdisk_utils_ia import (
    _fetch_and_cache_ia_files,
    fetch_zip_structure, fetch_user_stats,
    upload_custom_thumb_py, compute_magnet,
)

from netdisk_utils_upload import (
    _collect_meta_form, _build_upload_opts, _opts_to_metadata,
    ProgressFileWrapper, sign_s3_v4, _upload_large_file_s3,
)

from netdisk_utils_fileops import (
    _delete_ia_file,
    _rename_cooldown_ok, _rename_cooldown_set,
    _rename_account_quota_ok, _rename_account_record,
    _rename_ia_file, _push_tags_update,
)

from netdisk_utils_background import (
    _auto_backup_worker, _auto_clean_trash_worker, _clean_memory_tasks_worker,
)