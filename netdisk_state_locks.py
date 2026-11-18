# netdisk_state_locks.py
# 全局可变状态与锁
# 【重要】所有对 GLOBAL_UPLOAD_COOLDOWN 的读写都必须通过
#         import netdisk_state_locks + netdisk_state_locks.GLOBAL_UPLOAD_COOLDOWN
#         不能 from netdisk_state_locks import GLOBAL_UPLOAD_COOLDOWN（会拿到快照）

import threading

UPLOAD_TASKS = {}
DB_LOCK = threading.Lock()
CONFIG_LOCK = threading.Lock()
APPEND_CWD_LOCK = threading.Lock()

GLOBAL_UPLOAD_COOLDOWN = 0.0
UPLOAD_COOLDOWN_LOCK = threading.Lock()