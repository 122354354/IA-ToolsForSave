# netdisk_state.py
# 汇总文件：统一导出所有状态、路径、锁、选项、数据库函数
# 外部代码继续 from netdisk_state import xxx 即可，无需改动
#
# 【重要】GLOBAL_UPLOAD_COOLDOWN 不在此汇总导出！
# 原因：它是可变全局，所有读写必须走 netdisk_state_locks.GLOBAL_UPLOAD_COOLDOWN
# 如果从本文件 import GLOBAL_UPLOAD_COOLDOWN，会拿到快照（0.0），后续赋值不会同步
# 涉及的文件：netdisk_utils_upload.py、netdisk_workers_upload.py、
#            netdisk_workers_append.py、netdisk_workers_download.py
# 全部使用 import netdisk_state_locks + netdisk_state_locks.GLOBAL_UPLOAD_COOLDOWN

from netdisk_state_paths import (
    HOME_DIR, DB_FILE, CONFIG_FILE, TEMP_UPLOAD_DIR, THUMB_DIR, WARP_DIR, BACKUP_DIR,
)
from netdisk_state_locks import (
    UPLOAD_TASKS, DB_LOCK, CONFIG_LOCK, APPEND_CWD_LOCK, UPLOAD_COOLDOWN_LOCK,
)
from netdisk_state_options import (
    LANG_OPTIONS, LICENSE_OPTIONS, MEDIA_OPTIONS, COLLECTION_OPTIONS,
)
from netdisk_state_db import (
    get_config, save_config, cfg_get, get_db, init_db,
    get_active_account, get_max_concurrent, get_folder_tree_options,
)

__all__ = [
    # 路径
    'HOME_DIR', 'DB_FILE', 'CONFIG_FILE', 'TEMP_UPLOAD_DIR', 'THUMB_DIR', 'WARP_DIR', 'BACKUP_DIR',
    # 锁与任务容器
    'UPLOAD_TASKS', 'DB_LOCK', 'CONFIG_LOCK', 'APPEND_CWD_LOCK', 'UPLOAD_COOLDOWN_LOCK',
    # 选项
    'LANG_OPTIONS', 'LICENSE_OPTIONS', 'MEDIA_OPTIONS', 'COLLECTION_OPTIONS',
    # 数据库函数
    'get_config', 'save_config', 'cfg_get', 'get_db', 'init_db',
    'get_active_account', 'get_max_concurrent', 'get_folder_tree_options',
]