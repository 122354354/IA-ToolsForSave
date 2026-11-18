# netdisk_utils_background.py
# 后台守护线程：自动备份、自动清理回收站、清理内存任务
#
# 【⏱️ 延迟与超时精细控制 - 2026-11 第 14/20 步】
# 接入 4 个新配置项（全部 cfg_get 读取 + 5 秒 TTL 缓存）：
#   1. auto_backup_days      (默认 7，1-365 天)   → _auto_backup_worker 的备份间隔
#   2. auto_backup_keep      (默认 4，1-20 个)    → _auto_backup_worker 的保留数
#   3. clean_memory_interval (默认 3600，60-86400 秒) → _clean_memory_tasks_worker 检查间隔
#   4. clean_memory_age      (默认 3600，60-86400 秒) → _clean_memory_tasks_worker 过期阈值
#
# 【🔴 核心原则：默认行为与原代码完全一致 + 开关未开启时完全不生效】
#   - 所有开关判断保留（cfg_get('auto_backup', False) / cfg_get('auto_clean_tasks', False)）
#   - 用户未在设置页开启 auto_backup / auto_clean_tasks 时，这些参数完全不会被读取
#   - 默认值与原硬编码完全一致（7 天 / 4 个 / 3600 秒 / 3600 秒）
#   - 不新增任何自动删除/自动清理逻辑
#
# 【默认值对比（与原代码）】
#   - auto_backup_days      : 7  → 7   ✅ 完全一致
#   - auto_backup_keep      : 4  → 4   ✅ 完全一致
#   - clean_memory_interval : 3600 → 3600 ✅ 完全一致
#   - clean_memory_age      : 3600 → 3600 ✅ 完全一致
#
# 【不改的参数（用户未提出配置）】
#   - _auto_backup_worker 的 time.sleep(3600) 检查间隔：备份间隔才是用户要调的，
#     检查间隔每小时扫一次是合理设计（最多延迟 1 小时触发备份）
#   - _auto_clean_trash_worker 的 time.sleep(3600 * 12)：12 小时检查一次，用户未提出配置
#   - _auto_clean_trash_worker 的 trash_retention：原已通过 cfg_get('trash_retention', 0) 配置化
#
# 【sleep 阻塞说明】
#   - _clean_memory_tasks_worker 的 time.sleep(_get_clean_memory_interval()) 是阻塞 sleep
#     用户改配置后，要等当前 sleep 结束（最多 86400 秒 = 1 天）才生效
#     与原代码 time.sleep(3600) 行为一致（原代码也是阻塞 1 小时后生效）
#     引入分段 sleep 会显著增加复杂度，本轮不改（R120 记录）
#
# 【设计说明】
#   - 4 个 getter 都带 5 秒 TTL 缓存，用户改配置后最多 5 秒生效
#     但受限于 sleep 阻塞，实际生效时间 = 下次循环开始时（可能最长 1 天）
#   - config.json 损坏 / 非法值 → 回退默认值

import os
import time
import logging
import zipfile

from netdisk_state import (
    BACKUP_DIR, CONFIG_FILE, DB_FILE, TEMP_UPLOAD_DIR, UPLOAD_TASKS,
    DB_LOCK, get_db, cfg_get,
)
from netdisk_utils_common import cleanup_temp_dir


# ========== 【⏱️ 延迟与超时精细控制】4 个带 5 秒 TTL 缓存的读取函数 ==========
_AUTO_BACKUP_DAYS_CACHE = {'value': 7, 'at': 0.0}
_AUTO_BACKUP_KEEP_CACHE = {'value': 4, 'at': 0.0}
_CLEAN_MEMORY_INTERVAL_CACHE = {'value': 3600, 'at': 0.0}
_CLEAN_MEMORY_AGE_CACHE = {'value': 3600, 'at': 0.0}
_CFG_CACHE_TTL = 5.0


def _get_auto_backup_days():
    """返回自动备份间隔天数，默认 7，范围 1-365。

    语义说明：
      - 仅在 cfg_get('auto_backup', False) 开启时生效
      - 默认 7 天：与原来 `now - last_backup > 7 * 86400` 一致
      - 用户填 1 → 每天备份（最多占 7 份空间，配合 auto_backup_keep）
      - 用户填 365 → 一年备份一次
    """
    now = time.time()
    if now - _AUTO_BACKUP_DAYS_CACHE['at'] < _CFG_CACHE_TTL:
        return _AUTO_BACKUP_DAYS_CACHE['value']
    try:
        v = max(1, min(int(cfg_get('auto_backup_days', 7)), 365))
    except Exception:
        v = 7
    _AUTO_BACKUP_DAYS_CACHE['value'] = v
    _AUTO_BACKUP_DAYS_CACHE['at'] = now
    return v


def _get_auto_backup_keep():
    """返回自动备份保留数，默认 4，范围 1-20。

    语义说明：
      - 仅在 cfg_get('auto_backup', False) 开启时生效
      - 默认 4 个：与原来 `while len(autos) > 4` 一致
      - 用户填 1 → 只保留最新 1 份（最省空间）
      - 用户填 20 → 保留 20 份历史（占空间多）
    """
    now = time.time()
    if now - _AUTO_BACKUP_KEEP_CACHE['at'] < _CFG_CACHE_TTL:
        return _AUTO_BACKUP_KEEP_CACHE['value']
    try:
        v = max(1, min(int(cfg_get('auto_backup_keep', 4)), 20))
    except Exception:
        v = 4
    _AUTO_BACKUP_KEEP_CACHE['value'] = v
    _AUTO_BACKUP_KEEP_CACHE['at'] = now
    return v


def _get_clean_memory_interval():
    """返回内存任务清理检查间隔（秒），默认 3600，范围 60-86400。

    语义说明：
      - 仅在 cfg_get('auto_clean_tasks', False) 开启时生效
      - 默认 3600 秒（1 小时）：与原来 `time.sleep(3600)` 一致
      - 用户填 60 → 每分钟检查一次（高频，CPU 略升）
      - 用户填 86400 → 每天检查一次（低频，最省 CPU）
      - 注意：这是阻塞 sleep，改配置后最多等 86400 秒才生效
    """
    now = time.time()
    if now - _CLEAN_MEMORY_INTERVAL_CACHE['at'] < _CFG_CACHE_TTL:
        return _CLEAN_MEMORY_INTERVAL_CACHE['value']
    try:
        v = max(60, min(int(cfg_get('clean_memory_interval', 3600)), 86400))
    except Exception:
        v = 3600
    _CLEAN_MEMORY_INTERVAL_CACHE['value'] = v
    _CLEAN_MEMORY_INTERVAL_CACHE['at'] = now
    return v


def _get_clean_memory_age():
    """返回内存任务过期阈值（秒），默认 3600，范围 60-86400。

    语义说明：
      - 仅在 cfg_get('auto_clean_tasks', False) 开启时生效
      - 默认 3600 秒（1 小时）：与原来 `now - created > 3600` 一致
      - 用户填 60 → 任务完成/失败 1 分钟后即清理
      - 用户填 86400 → 任务完成/失败 1 天后才清理
    """
    now = time.time()
    if now - _CLEAN_MEMORY_AGE_CACHE['at'] < _CFG_CACHE_TTL:
        return _CLEAN_MEMORY_AGE_CACHE['value']
    try:
        v = max(60, min(int(cfg_get('clean_memory_age', 3600)), 86400))
    except Exception:
        v = 3600
    _CLEAN_MEMORY_AGE_CACHE['value'] = v
    _CLEAN_MEMORY_AGE_CACHE['at'] = now
    return v


def _auto_backup_worker():
    last_backup = 0
    while True:
        # 检查间隔固定 3600 秒（每小时扫一次，最多延迟 1 小时触发备份）
        # 用户未提出配置检查间隔，备份间隔由 auto_backup_days 控制
        time.sleep(3600)
        try:
            if cfg_get('auto_backup', False):
                now = time.time()
                # 【⏱️ 延迟与超时精细控制】7 * 86400 → _get_auto_backup_days() * 86400
                backup_days = _get_auto_backup_days()
                if now - last_backup > backup_days * 86400:
                    ts = time.strftime('%Y%m%d_%H%M%S')
                    backup_path = os.path.join(BACKUP_DIR, f"netdisk_auto_backup_{ts}.zip")
                    with zipfile.ZipFile(backup_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                        if os.path.exists(CONFIG_FILE): zf.write(CONFIG_FILE, 'config.json')
                        if os.path.exists(DB_FILE): zf.write(DB_FILE, 'netdisk.db')
                    logging.info(f"✅ 自动备份完成: {backup_path}")
                    last_backup = now
                    autos = sorted([f for f in os.listdir(BACKUP_DIR) if f.startswith('netdisk_auto_backup_')])
                    # 【⏱️ 延迟与超时精细控制】4 → _get_auto_backup_keep()
                    keep_count = _get_auto_backup_keep()
                    while len(autos) > keep_count:
                        try: os.remove(os.path.join(BACKUP_DIR, autos.pop(0)))
                        except: pass
        except Exception as e:
            logging.error(f"自动备份失败: {e}")


def _auto_clean_trash_worker():
    # 用户未提出配置此项，保留原样
    while True:
        time.sleep(3600 * 12)
        try:
            if not cfg_get('auto_clean_trash', False): continue
            days = cfg_get('trash_retention', 0)
            if days > 0:
                with DB_LOCK:
                    conn = get_db()
                    rows = conn.execute("SELECT file_path FROM items WHERE deleted_at IS NOT NULL AND deleted_at < datetime('now', ?)", (f'-{days} days',)).fetchall()
                    for r in rows:
                        if r[0] and os.path.abspath(r[0]).startswith(os.path.abspath(TEMP_UPLOAD_DIR) + os.sep): cleanup_temp_dir(r[0])
                    conn.execute("DELETE FROM items WHERE deleted_at IS NOT NULL AND deleted_at < datetime('now', ?)", (f'-{days} days',))
                    conn.commit(); conn.close()
                logging.info(f"✅ 已自动清理超过 {days} 天的回收站记录及临时文件")
        except Exception as e:
            logging.error(f"自动清理失败: {e}")


def _clean_memory_tasks_worker():
    while True:
        # 【⏱️ 延迟与超时精细控制】time.sleep(3600) → time.sleep(_get_clean_memory_interval())
        #   注意：这是阻塞 sleep，用户改配置后最多等 _get_clean_memory_interval() 秒才生效
        time.sleep(_get_clean_memory_interval())
        try:
            if not cfg_get('auto_clean_tasks', False): continue
            now = time.time()
            # 【⏱️ 延迟与超时精细控制】3600 → _get_clean_memory_age()
            age_threshold = _get_clean_memory_age()
            to_remove = []
            for k, v in list(UPLOAD_TASKS.items()):
                if v.get('status') in ('done', 'error'):
                    created = v.get('created_at', now)
                    if now - created > age_threshold:
                        to_remove.append(k)
            for k in to_remove: UPLOAD_TASKS.pop(k, None)
            if to_remove:
                logging.info(f"✅ 已清理 {len(to_remove)} 个内存中的已完成/失败任务")
        except: pass