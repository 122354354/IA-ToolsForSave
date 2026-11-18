# netdisk_utils_common.py
# 通用工具、全局缓存字典、内存日志、任务入库、磁力并发锁
# 【Bug 1 修复 - 第 1/5 步】新增 _merge_part_files(dir_path) 分片合并工具函数
# 【Bug 23 修复】分片命名改用独特的 .__ND_PART__{N} 后缀
# 【改进点 80 - 1/6】新增 _append_log(history_lines, line, max_chars=200) 统一单行截断日志
# 【改进点 1】_merge_part_files 改为原子替换：先拼到 .merging 临时文件，
#             成功后 os.replace 原子替换，失败保留分片并删除临时文件
#             （防止拼接中途进程被 kill 时留下不完整的 target 文件）

import os
import re
import shutil
import logging
import collections
import threading
import time
import uuid

import netdisk_state
from netdisk_state import (
    UPLOAD_TASKS, DB_LOCK,
    get_db, cfg_get,
)


# ========== 通用工具函数 ==========

def format_bytes(b):
    if not b: return "0 B"
    b = float(b)
    if b < 1024: return f"{b:.1f} B"
    if b < 1048576: return f"{b/1024:.1f} KB"
    if b < 1073741824: return f"{b/1048576:.1f} MB"
    return f"{b/1073741824:.2f} GB"


def cleanup_temp_dir(path):
    if not path or not os.path.exists(path): return
    try:
        if os.path.isdir(path): shutil.rmtree(path)
        else: os.remove(path)
    except: pass


def _is_system_file(name):
    n = (name or '').lower()
    if not n: return False
    if n.endswith('_meta.xml') or n.endswith('_files.xml') or n.endswith('_meta.sqlite') or n.endswith('_reviews.xml'): return True
    if n.endswith('.torrent'): return True
    if n in ('files.xml', 'meta.xml'): return True
    return False


# ========== 【改进点 80】统一日志追加工具 ==========

def _append_log(history_lines, line, max_chars=200):
    """【Bug 10 / 改进点 80】单行截断 + append 到 deque，防单行过长导致 raw_line 爆炸。

    history_lines: collections.deque（由调用方传入，调用方负责并发锁）
    line:          任意可转 str 的对象；None / 空字符串 / 假值 → 跳过
    max_chars:     单行最大长度，超过截断并加 "..."

    行为：
      - 非字符串自动 str() 转换
      - 超过 max_chars 截断 + "..."
      - 直接 append 到 history_lines（deque 自带 maxlen 保护）
    """
    if not line:
        return
    line = str(line)
    if len(line) > max_chars:
        line = line[:max_chars] + "..."
    history_lines.append(line)


# ========== 【Bug 1 / Bug 23 / 改进点 1 修复】分片合并工具 ==========

# 前端分片命名格式：{原文件名}.__ND_PART__{N}
# 例如：video.mp4.__ND_PART__0, video.mp4.__ND_PART__1, ...
# 使用 __ND_PART__ 这个特殊标记（双下划线 + 全大写），
# 几乎不可能与用户真实文件名冲突，彻底避免 Bug 23 误伤。
_PART_SUFFIX_RE = re.compile(r'^(.+)\.__ND_PART__(\d+)$')


def _merge_part_files(dir_path):
    """【Bug 1 / Bug 23 修复】合并 {name}.__ND_PART__{N} 系列分片成 {name}，
    删除分片临时文件。

    【改进点 1】原子替换策略：
      1. 先拼到 {target}.merging.{uuid} 临时文件
      2. 拼接成功后 os.replace 原子替换到 {target}
      3. 拼接失败或进程中断 → target 保持原状（不完整），分片完整保留
      4. 无论成功失败，清理临时文件

    适用范围：
      前端分片上传（>50MB 文件切成多个 5MB 分片）的 finalize 阶段。
      前端把每个分片的 chunk_rel_path 设为 "{原文件名}.__ND_PART__{N}"，
      后端 api_upload_chunk 各自独立存成文件，不覆盖。
      本函数在 finalize 时把同一 {原文件名} 的所有分片按 N 升序拼接成完整文件。

    兼容性：
      如果目录中没有 .__ND_PART__ 系列文件（旧前端/未分片上传/普通文件），
      本函数不做任何操作。

    安全性：
      - 只处理文件名符合 <任意>.__ND_PART__<纯数字> 的文件
      - 分片编号必须从 0 开始连续（0,1,2,...），否则跳过该组（防止数据损坏）
      - 拼接失败时不会删除任何分片文件
      - 拼接成功后删除分片临时文件
      - 使用独特后缀，不会误伤用户目录中的 <任意名字>.partN 普通文件
      - 【改进点 1】原子替换：不会留下不完整的 target 文件
    """
    if not dir_path or not os.path.isdir(dir_path):
        return

    for root, dirs, files in os.walk(dir_path):
        # 按 (所在目录, 原始文件名) 分组收集 .__ND_PART__ 文件
        groups = {}  # key = (dir, base_name) -> list of (part_num, full_path)
        for fname in files:
            m = _PART_SUFFIX_RE.match(fname)
            if not m:
                continue
            base_name = m.group(1)
            try:
                part_num = int(m.group(2))
            except ValueError:
                continue
            key = (root, base_name)
            groups.setdefault(key, []).append((part_num, os.path.join(root, fname)))

        if not groups:
            continue

        for (gdir, base_name), parts in groups.items():
            parts.sort(key=lambda x: x[0])
            nums = [p[0] for p in parts]

            # 分片编号必须从 0 开始连续：0,1,2,...,len-1
            if nums != list(range(len(nums))):
                logging.warning(
                    f"⚠️ [分片合并] 跳过 {base_name}：分片编号不连续 {nums}，"
                    f"可能是上次分片上传中断。请重新上传该文件。"
                )
                continue

            target_path = os.path.join(gdir, base_name)
            # 【改进点 1】使用独特的临时文件名（含 uuid，避免多进程冲突）
            tmp_path = target_path + f".merging.{uuid.uuid4().hex[:8]}"
            try:
                with open(tmp_path, 'wb') as out:
                    for _, part_path in parts:
                        with open(part_path, 'rb') as p:
                            while True:
                                chunk = p.read(1024 * 1024)  # 1MB
                                if not chunk:
                                    break
                                out.write(chunk)
                # 【改进点 1】原子替换到 target（POSIX 保证要么成功要么 target 不变）
                os.replace(tmp_path, target_path)
                # 拼接成功后再删除分片
                for _, part_path in parts:
                    try: os.remove(part_path)
                    except: pass
                logging.info(
                    f"✅ [分片合并] {base_name}：{len(parts)} 个分片已原子拼接为完整文件"
                )
            except Exception as e:
                # 拼接失败：保留所有分片，删除临时文件
                try: os.remove(tmp_path)
                except: pass
                logging.error(f"❌ [分片合并] {base_name} 拼接失败: {e}")


# ========== 全局缓存字典 ==========

ID_CHECK_CACHE = {}
ID_CHECK_LOCK = threading.Lock()
IP_ID_CHECK = collections.defaultdict(list)
MAGNET_FAIL_CACHE = {}
MAGNET_LOCK = threading.Lock()
MAGNET_COUNT = {'current': 0}
MAGNET_COUNT_LOCK = threading.Lock()
MAGNET_COUNT_CV = threading.Condition(MAGNET_COUNT_LOCK)

PULL_CACHE = {}
PULL_LOCK = threading.Lock()
REFRESH_COOLDOWN_MAP = {}
REFRESH_LOCK = threading.Lock()
RENAME_COOLDOWN_MAP = {}
RENAME_ACCOUNT_HISTORY = collections.defaultdict(list)
RENAME_LOCK = threading.Lock()
THUMB_FAIL_CACHE = {}
THUMB_IP_LOG = collections.defaultdict(list)
IP_REQUEST_LOG = collections.defaultdict(list)


# ========== 磁力并发闸门 ==========

def _magnet_acquire():
    with MAGNET_COUNT_CV:
        limit = cfg_get('magnet_concurrent_limit', 2)
        while MAGNET_COUNT['current'] >= limit: MAGNET_COUNT_CV.wait()
        MAGNET_COUNT['current'] += 1


def _magnet_release():
    with MAGNET_COUNT_CV:
        if MAGNET_COUNT['current'] > 0: MAGNET_COUNT['current'] -= 1
        MAGNET_COUNT_CV.notify_all()


# ========== 内存日志 ==========

class MemoryLogHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.logs = collections.deque(maxlen=200)
    def emit(self, record):
        self.logs.append(self.format(record))

MEM_LOG = MemoryLogHandler()
MEM_LOG.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(message)s', datefmt='%H:%M:%S'))
logging.getLogger().addHandler(MEM_LOG)
logging.getLogger().setLevel(logging.INFO)


# ========== 任务入库 ==========

def save_task_to_db(task_id):
    t = UPLOAD_TASKS.get(task_id)
    if not t: return
    try:
        with DB_LOCK:
            conn = get_db()
            conn.execute('''INSERT OR REPLACE INTO upload_tasks (task_id, title, status, msg, percent, speed, current, total, eta, raw_line, batch_total, batch_done, task_type, extra_data) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                         (task_id, t.get('title',''), t.get('status',''), t.get('msg',''), t.get('percent',0), t.get('speed','0.0'), t.get('current',''), t.get('total',''), t.get('eta',''), t.get('raw_line',''), t.get('batch_total',1), t.get('batch_done',0), t.get('task_type','upload'), t.get('extra_data','')))
            conn.commit(); conn.close()
    except: pass