# netdisk_app_discover.py
# 发现页缓存、代理下载 IP 日志、搜索历史、虚拟任务清理线程
#
# 【R320 + R322 修复 - 2026-11（同源类问题统一修复第 3/5 个文件）】
# 问题：_clean_virtual_tasks_worker 有两个真实问题：
#
#   1. R320（数据一致性）：先 `UPLOAD_TASKS.pop(k)` 再尝试 DB 删除。
#      若 DB 删除失败（锁冲突/磁盘满/进程被 kill），内存已 pop 但 DB 残留：
#      - upload_tasks 表持续膨胀（虽然虚拟任务 status='done' 不会在
#        _restore_interrupted_tasks 里被"复活"，但记录累积占用 DB 空间）
#      - sub_tasks 表若有对应记录也残留（虚拟任务实际无 sub_tasks，但保险起见
#        仍要一起删）
#      修复：改为"先 DB 删除成功 → 再 pop 内存"，DB 失败时不 pop，下次循环再试。
#
#   2. R322（性能，修正上一轮描述）：每个任务单独 `get_db()` + `conn.close()`。
#      1000 个任务 = 1000 次连接。每次 get_db 的 `PRAGMA journal_mode=WAL`
#      在 DB 繁忙时可能阻塞至 db_timeout（默认 30 秒），1000 个任务 = 30000 秒
#      = 8 小时（"清理"变"卡死"）。
#      注意：原代码是单条 DELETE（`WHERE task_id=?`），不存在 SQLite 999 变量上限问题。
#      修复：共享 1 个连接 + 分批 IN 批量 DELETE（每批 500 个，引入 IN 后才需
#      考虑 SQLite 999 变量上限，所以分批）
#      → 1000 个任务 = 1 次 get_db + 2 批 DELETE（总耗时 < 1 秒）
#
# 【事务语义】所有批次共用一个事务：全部 commit 或全部 rollback
#   → 内存 pop 前确保 DB 全删成功 → 内存和 DB 一致
#   → 若中途失败 rollback，DB 全部保留，不 pop 内存，下次循环再试
#
# 【同源问题记录 - netdisk_app.py】
# netdisk_app.py 里也有一份同样的 _clean_virtual_tasks_worker（拆分遗留的重复代码），
# 本次修复只改 netdisk_app_discover.py；netdisk_app.py 的那份待第 4/5 个文件修复。
#
# 【其他同源问题记录 - 待后续修复】
# - netdisk_workers_upload.py 的 retry_single_file_worker（R321）→ 第 5 个文件
# - netdisk_utils_ia.py 的 _fetch_and_cache_ia_files 未检查 deleted_at（R362）
#   → 跨文件同源，待后续统一修时一并处理

import time
import logging
import threading

from netdisk_core import (
    get_config, save_config, get_db, UPLOAD_TASKS, DB_LOCK,
)

DISCOVER_CACHE = {}
PROXY_DL_IP_LOG = {}


# 【P2-12 修复】语义改为"进程启动时清空一次"
# 原实现是"首次调用 get_search_history 时清空一次"，会导致：
#   1. 用户已经在发现页看到旧历史，刷新页面才消失
#   2. 语义模糊（设置文案是"每次启动清空"，但实际是"首次访问发现页清空"）
# 新实现在模块加载时（即进程启动时）执行一次，语义清晰
def _maybe_clear_history_on_startup():
    try:
        cfg = get_config()
        if cfg.get('auto_clear_history', False):
            hist = cfg.get('discover_history', [])
            if hist:  # 只有非空才写，避免无意义的磁盘写
                cfg['discover_history'] = []
                save_config(cfg)
                logging.info("🧹 已按设置清空发现页搜索历史（进程启动时清空一次）")
    except Exception as e:
        logging.warning(f"启动清空搜索历史失败: {e}")


# 模块加载时执行一次（进程启动时）
_maybe_clear_history_on_startup()


def get_search_history():
    # 【P2-12 修复】启动时已经清空过一次（若配置开启），这里直接返回即可
    return get_config().get('discover_history', [])


def add_search_history(q):
    if not q: return
    cfg = get_config()
    hist = cfg.get('discover_history', [])
    if q in hist: hist.remove(q)
    hist.insert(0, q)
    cfg['discover_history'] = hist[:20]
    save_config(cfg)


def _clean_virtual_tasks_worker():
    """虚拟任务清理守护线程。

    【R320 + R322 修复 - 2026-11（详见文件头注释）】
    每 60 秒扫一次 UPLOAD_TASKS，找出 is_virtual 且 expires_at < now 的过期任务：
      1. 先 DB 删除成功（事务，全部 commit 或全部 rollback）
      2. DB 成功后 pop 内存（避免内存/DB 不一致）
      3. DB 失败不 pop（下次循环再试）
      4. 分批 DELETE（每批 500 个，避免 SQLite 999 变量上限）
      5. 共享 1 个 DB 连接（避免 N 个任务 N 次 get_db 的 WAL PRAGMA 阻塞）
    """
    # 【R322 修复】批量 DELETE 的分批大小，避免 SQLite 999 变量上限
    BATCH_SIZE = 500

    while True:
        time.sleep(60)
        try:
            now = time.time()
            to_remove = []
            for k, v in list(UPLOAD_TASKS.items()):
                if v.get('is_virtual') and v.get('expires_at', 0) < now:
                    to_remove.append(k)

            if not to_remove:
                continue

            # 【R320 + R322 修复】先 DB 删除（共享 1 个连接 + 分批 IN），成功后再 pop 内存
            try:
                with DB_LOCK:
                    conn = get_db()
                    try:
                        for i in range(0, len(to_remove), BATCH_SIZE):
                            batch = to_remove[i:i + BATCH_SIZE]
                            placeholders = ','.join(['?'] * len(batch))
                            conn.execute(f"DELETE FROM sub_tasks WHERE task_id IN ({placeholders})", batch)
                            conn.execute(f"DELETE FROM upload_tasks WHERE task_id IN ({placeholders})", batch)
                        conn.commit()
                    finally:
                        conn.close()
            except Exception as e:
                # DB 删除失败：不 pop 内存（下次再试），避免内存/DB 不一致
                logging.warning(
                    f"[虚拟任务清理] DB 删除失败，{len(to_remove)} 个任务保留待下次清理: {e}"
                )
                continue

            # 【R320 修复】DB 删除成功后才 pop 内存
            for k in to_remove:
                UPLOAD_TASKS.pop(k, None)
            logging.info(f"🧹 [虚拟任务清理] 已清理 {len(to_remove)} 个过期虚拟任务")
        except Exception as e:
            logging.warning(f"[虚拟任务清理] 循环异常: {e}")
