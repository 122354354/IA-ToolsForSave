# netdisk_utils_upload.py
# 上传参数收集/构建、元数据转换、进度包装类、S3 V4 签名、S3 分块上传
# 【Bug 18 修复 - 第 1 步】_upload_large_file_s3 新增 expected_worker_token 参数
# 【改进点 17 - 1/3】_upload_large_file_s3 新增 progress_callback 参数
# 【修复 A1c】Initiate 请求补 x-archive-auto-make-bucket: 1 头
# 【修复 A1e-修正】Initiate 失败时的错误提示
# 【修复 A1d-回滚】query 保持 "uploads="（带等号）
# 【修复 A1g】path 和 uploadId URL 编码
# 【修复 A1j】_upload_large_file_s3 的 Initiate 段 conn 加 try/finally
# 【P0 修复 - 2026-11】Initiate 失败自动 fallback ia.upload()
# 【P1 修复 - 2026-11】fallback 进度条：HTTPConnection.send hook 精确跟踪（不限速）
# 【🔴 P0-6】分片 PUT 和 Complete 也改用 LOW 认证
# 【🔴 P0-7】所有 requests 加 timeout
# 【🔴 P0-8】multipart 上传后元数据不保存
# 【🔴 P0-9】分片记录写不进去（INSERT OR REPLACE + 内部重试）
# 【🔴 P0-10】分片遇 IA 风控没触发全局冷却
# 【🔴 P0-11】uploadId 失效时死循环
# 【🔴 P0-12】暂停功能在分片上传时失效
# 【🔴 P0-13】403 被无条件视为风控
# 【🔴 P0-14】断电丢失本地记录导致重传分片（ListParts 补全）
# 【🔴 P0-15】ListParts 后没清理"本地有但 IA 没有"的分片 → InvalidPart 死循环
# 【🔴 P0-18】_push_metadata_after_complete 报 verbose 参数错误（三级回退）
# 【🔴 P1-3】分片进度 10MB 一跳（pycurl 重写 _put_part_low_auth）
# 【🔴 P0-21】virtual 尝试改用 _is_virtual_safe_identifier（允许大写/下划线）
# 【🔴 P1-4】pycurl 的 READFUNCTION 一次性返回全部 10MB（READFUNCTION 改 16KB 分块）
# 【🔴 P1-5】_make_part_progress_cb 传 overall 导致主循环重复计算
# 【🔴 P1-6】进度条回退 + 子卡片初始值错误
# 【🔴 P1-7】pycurl 复用 WARP 已死的 TCP 连接导致卡 120 秒
# 【🔴 P1-8 修复 - 2026-11】分片间 time.sleep(1) → time.sleep(0.1)
#
# 【🔴 G-59 第 1 轮修复 - 2026-11】见历史
# 【🔴 G-165 修复 - 2026-11】见历史
# 【🔴 G-171 修复 - 2026-11】见历史
# 【🔴 G-172 + G-173 修复 - 2026-11】见历史
# 【🔴 G-175 修复 - 2026-11】见历史
#
# 【⏱️ 延迟与超时精细控制 - 2026-11 第 4/20 步】
# 接入 6 个新配置项（全部 cfg_get 读取 + 5 秒 TTL 缓存）：
#   1. s3_timeout_connect (默认 15，5-120 秒) → _get_s3_timeout()[0]
#   2. s3_timeout_read    (默认 60，10-600 秒) → _get_s3_timeout()[1]
#   3. s3_retry_backoff_base (默认 2，1-10) → _get_s3_retry_backoff_base()
#   4. s3_part_sleep      (默认 0.1，0-10 秒) → _get_s3_part_sleep()
#   5. tcp_keepidle       (默认 30，5-300 秒) → _get_tcp_keepalive()[0]
#   6. tcp_keepintvl      (默认 10，1-60 秒)  → _get_tcp_keepalive()[1]
#
# 【🔴 R390 修复 - 2026-11（跨文件同源修复 - 第 2/2 个文件 - 本轮）】
#   问题：_upload_large_file_s3(identifier, rel_path, fp, file_size, opts, task_id, ...)
#        在分片循环中按调用方传入的 file_size 算 total_parts。
#        如果文件在"调用方取 file_size"到"分片循环完成"之间被修改：
#     - 文件变小：f.read(chunk_size) 返回不足 chunk_size 的字节
#       → 上传不完整分片 → IA 报错或文件损坏
#     - 文件变大：total_parts 按旧 file_size 算 → 只上传前 N 片
#       → 末尾追加内容被丢弃 → 文件不完整
#   触发场景（0.01%，但数据完整性风险）：
#     - 用户在 upload_worker / retry_single_file_worker 上传过程中用 termux 编辑文件
#     - 文件是日志/大文本，用户在上传过程中追加数据
#     - 断电前文件被部分写入，重启后 worker 继续按旧 size 上传
#   与 R321 / R389（retry_single_file_worker 内部的 sz TOCTOU）同源，但层级不同：
#     - R321 / R389 在"worker 启动前"检查（取实时 size）
#     - R390 在"分片循环内"检查（防 worker 真正上传过程中再次变化）
#
#   修复（3 处）：
#     1. 分片计算前（每个 _reexamine_attempt 迭代内）重新 os.path.getsize(fp)，
#        与传入 file_size 不等时抛 Exception → 上层捕获标 error
#     2. 分片循环里 data = f.read(chunk_size) 之后检查 len(data)
#        是否等于 min(chunk_size, file_size - offset)，不等抛 Exception
#        覆盖"上传过程中文件变小"
#     3. Complete 成功之后重新 os.path.getsize(fp)，若变化只 warning
#        （无法回滚已完成上传，但让用户知道末尾追加内容可能未上传）
#
#   为什么修复无正常路径副作用：
#     - 文件大小未变化时，3 处检查行为完全一致（无额外 IO 开销，1 次 getsize 极快）
#     - 文件变化时抛 Exception → 上层 upload_one_file / retry_single_file_worker
#       的 except Exception 捕获 → _safe_worker_mark_error 标 error
#       → 用户看到"文件大小在上传前已变更"错误 → 可重新提交上传任务
#     - 修复防御了"文件在 worker 真正上传过程中被外部程序修改"的极端场景
#
#   为什么把 total_parts 计算移进 for 循环：
#     原代码 total_parts 在 for 循环外计算。若文件在第一次 _UploadExpiredException
#     重试（第二次 for 迭代）前发生变化，旧 total_parts 会过期。移进循环内
#     + 每次迭代都检查文件大小，保证每次重试都用最新的 total_parts。
#
#   为什么检查 2 检测不到"文件变大"：
#     - 文件变大时，前 N 片（按旧 file_size 算）内容未变，f.read(chunk_size) 返回
#       完整 chunk_size 字节 → len(data) == chunk_size 通过检查
#     - 最后一片（旧 file_size - offset）也正常读取（文件变大后数据足够）
#     - 所以"变大"场景只能靠检查 3 在完成后 warning
#     - 检查 3 概率极低（用户主动追加 + 上传完成后的时间窗口），可接受
#
# 【📝 保留记录 - 2026-11，低优先，以后修】
#   R404（本轮记录）：检查 3 在 Complete 成功后 getsize，若文件变大只 warning。
#                    用户可能不知道末尾追加内容未上传。改进方向：上传完成后
#                    把实际上传的字节数（total_parts * chunk_size 或 last_part_size）
#                    写入 items.file_size，让用户对比。
#   R405（本轮记录）：检查 1 在 _reexamine_attempt 迭代内。若文件在第一次 for 循环
#                    中途变化 → 分片循环里的检查 2 检测到 → 抛 Exception → 不重试
#                    （普通 Exception 不触发 _UploadExpiredException 的 retry 路径）
#                    → 上层标记 error → 用户可重新提交。OK。
#   R406（本轮记录）：检查 2 抛 Exception 不区分"文件变化"和"磁盘读取错误"。
#                    极端场景（磁盘故障 f.read 返回短数据）也标 error，用户看到
#                    "文件可能在上传过程中被修改"，可能误导。但磁盘故障用户
#                    无论如何都需要重新上传，错误消息不影响用户决策。
#
# 【📝 同源问题记录 - 其他文件，后续统一修】
#   - 全部已知同源问题已修复完毕（R340/R345/R351/R356/R361/R362/R321/R389/R320/R322/R334/R335/R376/R379/R390）
#   - netdisk_utils_ia.py 的 _fetch_and_cache_ia_files 已加 deleted_at IS NULL（R362，上一轮）

import os
import re
import sys
import time
import uuid
import base64
import random
import hashlib
import hmac
import logging
import threading
import http.client
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime
from email.utils import formatdate

import requests

try:
    import pycurl
    _PYCURL_AVAILABLE = True
except ImportError:
    pycurl = None
    _PYCURL_AVAILABLE = False


import netdisk_state_locks
from netdisk_state import (
    UPLOAD_COOLDOWN_LOCK, UPLOAD_TASKS, DB_LOCK, get_db, cfg_get,
)
from netdisk_utils_net import (
    get_ssl_verify, get_req_proxies, _get_s3_credentials, ensure_ia_config,
)


# ========== 【⏱️ 延迟与超时精细控制】4 个带 5 秒 TTL 缓存的读取函数 ==========
# 背景：S3 分片上传时每个分片会调用多次 timeout / sleep 读取，
#      1000 个分片就是几千次读 config.json，必须缓存。
# 缓存键独立，互不影响。
_S3_TIMEOUT_CACHE = {'value': (15, 60), 'at': 0.0}
_S3_PART_SLEEP_CACHE = {'value': 0.1, 'at': 0.0}
_S3_BACKOFF_BASE_CACHE = {'value': 2, 'at': 0.0}
_TCP_KEEPALIVE_CACHE = {'value': (30, 10), 'at': 0.0}
_CFG_CACHE_TTL = 5.0


def _get_s3_timeout():
    """返回 (connect_timeout, read_timeout)，默认 (15, 60)，范围 connect 5-120 / read 10-600。"""
    now = time.time()
    if now - _S3_TIMEOUT_CACHE['at'] < _CFG_CACHE_TTL:
        return _S3_TIMEOUT_CACHE['value']
    try:
        c = max(5, min(int(cfg_get('s3_timeout_connect', 15)), 120))
        r = max(10, min(int(cfg_get('s3_timeout_read', 60)), 600))
    except Exception:
        c, r = 15, 60
    _S3_TIMEOUT_CACHE['value'] = (c, r)
    _S3_TIMEOUT_CACHE['at'] = now
    return (c, r)


def _get_s3_part_sleep():
    """返回 S3 分片间等待秒数，默认 0.1，范围 0-10。"""
    now = time.time()
    if now - _S3_PART_SLEEP_CACHE['at'] < _CFG_CACHE_TTL:
        return _S3_PART_SLEEP_CACHE['value']
    try:
        v = max(0.0, min(float(cfg_get('s3_part_sleep', 0.1)), 10.0))
    except Exception:
        v = 0.1
    _S3_PART_SLEEP_CACHE['value'] = v
    _S3_PART_SLEEP_CACHE['at'] = now
    return v


def _get_s3_retry_backoff_base():
    """返回 S3 重试退避基数，默认 2，范围 1-10。

    实际重试间隔 = base ^ attempt（attempt 从 0 开始）。
    - base=1：固定 1 秒（不指数增长）
    - base=2：1, 2, 4 秒（原行为）
    - base=10：1, 10, 100 秒（指数爆炸，UI 有警告）
    """
    now = time.time()
    if now - _S3_BACKOFF_BASE_CACHE['at'] < _CFG_CACHE_TTL:
        return _S3_BACKOFF_BASE_CACHE['value']
    try:
        v = max(1, min(int(cfg_get('s3_retry_backoff_base', 2)), 10))
    except Exception:
        v = 2
    _S3_BACKOFF_BASE_CACHE['value'] = v
    _S3_BACKOFF_BASE_CACHE['at'] = now
    return v


def _get_tcp_keepalive():
    """返回 (keepidle, keepintvl) 秒数，默认 (30, 10)，范围 keepidle 5-300 / keepintvl 1-60。"""
    now = time.time()
    if now - _TCP_KEEPALIVE_CACHE['at'] < _CFG_CACHE_TTL:
        return _TCP_KEEPALIVE_CACHE['value']
    try:
        idle = max(5, min(int(cfg_get('tcp_keepidle', 30)), 300))
        intvl = max(1, min(int(cfg_get('tcp_keepintvl', 10)), 60))
    except Exception:
        idle, intvl = 30, 10
    _TCP_KEEPALIVE_CACHE['value'] = (idle, intvl)
    _TCP_KEEPALIVE_CACHE['at'] = now
    return (idle, intvl)


# ========== 常量 ==========
# 【保留】_DEFAULT_TIMEOUT 作为「永不读配置时的兜底」，现已被 _get_s3_timeout() 取代。
# 保留是为了：若未来某处忘改，还能用这个常量兜底。
_DEFAULT_TIMEOUT = (15, 60)
_RATE_LIMIT_KEYWORDS = [
    'reduce your request rate', 'too many requests', 'rate limit',
    'slow down', 'temporarily unavailable', 'bucket_tasks_queued',
]
# 【G-175 修复】取消类异常关键字（用于 except Exception 里判定）
_CANCEL_KEYWORDS = ("任务已被删除", "任务已被替换")


class _UploadExpiredException(Exception):
    """uploadId 失效（IA 7 天过期或用户重建 bucket）时抛出。"""
    pass


class _UploadPausedException(Exception):
    """用户暂停任务时抛出。"""
    pass


def _check_pause_or_token(task_id, expected_worker_token, pause_task_id,
                          canceled_msg, replaced_msg, paused_msg, check_paused=True):
    """【G-59 第 1 轮 - 2026-11】统一检查：token 模式 或 pause 模式。

    行为（按优先级）：
    1. expected_worker_token is not None → token 模式（upload_worker 场景）
    2. expected_worker_token is None 且 pause_task_id is not None → pause 模式
       （retry_single_file_worker 场景）
    3. 两者都 None → 不检查（向后兼容）
    """
    if expected_worker_token is not None:
        _t = UPLOAD_TASKS.get(task_id)
        if _t is None:
            raise Exception(canceled_msg)
        if _t.get('_worker_token') != expected_worker_token:
            raise Exception(replaced_msg)
        if check_paused and _t.get('status') == 'paused':
            raise _UploadPausedException(paused_msg)
    elif pause_task_id is not None:
        _t = UPLOAD_TASKS.get(pause_task_id)
        if _t is None:
            raise Exception(canceled_msg)
        if check_paused and _t.get('status') == 'paused':
            raise _UploadPausedException(paused_msg)


class _CIHeaders:
    """大小写不敏感的 header 容器（模拟 requests.CaseInsensitiveDict）。"""
    def __init__(self):
        self._data = {}

    def set(self, k, v):
        if not k:
            return
        self._data[k.lower()] = v

    def get(self, k, default=None):
        if not k:
            return default
        return self._data.get(k.lower(), default)

    def __contains__(self, k):
        if not k:
            return False
        return k.lower() in self._data

    def items(self):
        return self._data.items()


class _PycurlResponse:
    """模拟 requests.Response 的接口。"""
    def __init__(self, status_code, body, headers_ci):
        self.status_code = status_code
        self.content = body
        self.headers = headers_ci
        try:
            self.text = body.decode('utf-8', errors='replace') if body else ''
        except Exception:
            self.text = ''


_send_local = threading.local()
_send_patch_lock = threading.Lock()
_send_patched = [False]


def _ensure_http_send_patched():
    """幂等 patch http.client.HTTPConnection.send（requests 回退路径用）。"""
    if _send_patched[0]:
        return
    with _send_patch_lock:
        if _send_patched[0]:
            return
        _orig_send = http.client.HTTPConnection.send

        def _new_send(self, data):
            result = _orig_send(self, data)
            tracker = getattr(_send_local, 'tracker', None)
            if tracker is not None:
                try:
                    tracker(len(data))
                except Exception:
                    pass
            return result

        http.client.HTTPConnection.send = _new_send
        _send_patched[0] = True


# ========== identifier 校验 ==========
_DNS_IDENTIFIER_RE = re.compile(r'^[a-z0-9][a-z0-9\-\.]{1,61}[a-z0-9]$')
_IP_ADDR_RE = re.compile(r'^\d+\.\d+\.\d+\.\d+$')


def _is_dns_legal_identifier(identifier):
    """严格 DNS 合法检查（AWS 官方规则）。"""
    if not identifier:
        return False
    if len(identifier) < 3 or len(identifier) > 63:
        return False
    if not _DNS_IDENTIFIER_RE.match(identifier):
        return False
    if '..' in identifier:
        return False
    if _IP_ADDR_RE.match(identifier):
        return False
    return True


def _is_virtual_safe_identifier(identifier):
    """检查 identifier 是否适合 virtual-hosted-style。"""
    if not identifier:
        return False
    if len(identifier) > 63:
        return False
    for ch in identifier:
        if ch in '/?#% \t\n\r@:\\':
            return False
    if identifier.startswith('.') or identifier.startswith('-') or identifier.startswith('_'):
        return False
    if identifier.endswith('.'):
        return False
    if '..' in identifier:
        return False
    if all(c.isdigit() or c == '.' for c in identifier):
        return False
    return True


def _build_s3_request(identifier, rel_path, style, query=''):
    """根据 style 构建 S3 请求的 (endpoint, path_encoded, full_url)。"""
    if style == 'virtual':
        endpoint = f"https://{identifier}.s3.us.archive.org"
        path_encoded = _url_encode_path(f"/{rel_path.lstrip('/')}")
    else:
        endpoint = "https://s3.us.archive.org"
        path_encoded = _url_encode_path(f"/{identifier}/{rel_path}")

    if query:
        full_url = f"{endpoint}{path_encoded}?{query}"
    else:
        full_url = f"{endpoint}{path_encoded}"
    return endpoint, path_encoded, full_url


def _push_metadata_after_complete(identifier, metadata, ak, sk):
    """Complete 成功后推送元数据到 IA（三级回退）。"""
    if not metadata:
        return
    try:
        import internetarchive as ia
        ensure_ia_config()
    except Exception as e:
        logging.warning(f"[S3 Metadata] import internetarchive 失败: {e}")
        return

    errors = []

    try:
        ia.modify_metadata(identifier, metadata=metadata, verbose=False)
        logging.info(f"[S3 Metadata] 元数据推送成功（方式 1）: {identifier}")
        return
    except TypeError as e:
        errors.append(f"方式 1(TypeError): {e}")
    except Exception as e:
        errors.append(f"方式 1: {e}")

    try:
        ia.modify_metadata(identifier, metadata=metadata)
        logging.info(f"[S3 Metadata] 元数据推送成功（方式 2）: {identifier}")
        return
    except TypeError as e:
        errors.append(f"方式 2(TypeError): {e}")
    except Exception as e:
        errors.append(f"方式 2: {e}")

    try:
        item = ia.get_item(identifier)
        item.modify_metadata(metadata)
        logging.info(f"[S3 Metadata] 元数据推送成功（方式 3）: {identifier}")
        return
    except Exception as e:
        errors.append(f"方式 3: {e}")

    logging.warning(
        f"[S3 Metadata] 元数据推送失败（所有方式），用户可手动在 IA 网站编辑: "
        f"{identifier} | 错误详情: {'; '.join(errors[:3])}"
    )


def _ensure_bucket_exists(identifier, ak, sk):
    """通过 PUT 一个 1 字节探针文件，确保 bucket 存在。

    【⏱️ 延迟与超时精细控制】PUT probe 用 s3_timeout_connect / s3_timeout_read；
    DELETE probe 是短操作，clamp 到 min(10, s3_timeout_connect) / min(30, s3_timeout_read)
    """
    probe_rel_path = f"_nd_probe_{uuid.uuid4().hex[:8]}"
    endpoint = "https://s3.us.archive.org"
    path_encoded = _url_encode_path(f"/{identifier}/{probe_rel_path}")
    url = f"{endpoint}{path_encoded}"
    headers = {
        "Authorization": f"LOW {ak}:{sk}",
        "x-archive-auto-make-bucket": "1",
        "x-archive-meta-mediatype": "data",
        "x-archive-meta-title": identifier,
        "x-archive-meta-collection": "opensource",
        "Content-Type": "application/octet-stream",
        "User-Agent": "Mozilla/5.0",
    }
    req_proxies = get_req_proxies()

    s3_conn, s3_read = _get_s3_timeout()

    try:
        r = requests.put(url, headers=headers, data=b"\x00",
                        verify=get_ssl_verify(), proxies=req_proxies,
                        timeout=(s3_conn, s3_read))
        if r.status_code not in (200, 201):
            return False, f"probe PUT HTTP {r.status_code}: {(r.text or '')[:300]}"

        # DELETE 探针：短操作，clamp 到 max 10/30 秒
        del_conn = min(s3_conn, 10)
        del_read = min(s3_read, 30)
        try:
            requests.delete(url, headers={"Authorization": f"LOW {ak}:{sk}"},
                            verify=get_ssl_verify(), proxies=req_proxies,
                            timeout=(del_conn, del_read))
        except Exception as e:
            logging.warning(f"[S3 Bucket] 探针删除失败（不影响主流程）: {e}")

        return True, ""
    except Exception as e:
        return False, f"probe PUT 异常: {e}"


def _try_initiate_low_auth(identifier, rel_path, ak, sk, style):
    """LOW 认证 multipart Initiate。"""
    endpoint, path_encoded, url = _build_s3_request(identifier, rel_path, style, 'uploads')
    headers = {
        'Authorization': f'LOW {ak}:{sk}',
        'User-Agent': 'Mozilla/5.0',
    }
    req_proxies = get_req_proxies()
    r = requests.post(url, headers=headers, verify=get_ssl_verify(), proxies=req_proxies,
                      timeout=_get_s3_timeout())

    if r.status_code != 200:
        raise Exception(f"HTTP {r.status_code}: {(r.text or '')[:300]}")

    try:
        root = ET.fromstring(r.text)
    except Exception as e:
        raise Exception(f"XML 解析失败: {e}, 响应: {(r.text or '')[:200]}")

    ns = {'s3': 'http://s3.amazonaws.com/doc/2006-03-01/'}
    elem = root.find('s3:UploadId', ns)
    if elem is None or not elem.text:
        raise Exception(f"响应无 UploadId: {(r.text or '')[:300]}")
    return elem.text


def _list_uploaded_parts(identifier, rel_path, upload_id, ak, sk, style):
    """查询 IA 上该 uploadId 已上传的分片。"""
    query = f"uploadId={_url_encode_query_value(upload_id)}"
    endpoint, path_encoded, url = _build_s3_request(identifier, rel_path, style, query)
    headers = {
        'Authorization': f'LOW {ak}:{sk}',
        'User-Agent': 'Mozilla/5.0',
    }
    req_proxies = get_req_proxies()

    try:
        r = requests.get(url, headers=headers, verify=get_ssl_verify(), proxies=req_proxies,
                         timeout=_get_s3_timeout())

        if r.status_code in (404, 410):
            err_text = r.text or ""
            if 'NoSuchUpload' in err_text or 'NoSuchKey' in err_text:
                logging.warning(
                    f"[S3 ListParts] uploadId 已失效（HTTP {r.status_code}），"
                    f"需清理重传: {err_text[:200]}"
                )
                return None
            logging.warning(
                f"[S3 ListParts] HTTP {r.status_code}，降级到本地记录: {err_text[:200]}"
            )
            return False, {}

        if r.status_code != 200:
            logging.warning(
                f"[S3 ListParts] HTTP {r.status_code}，降级到本地记录: {(r.text or '')[:200]}"
            )
            return False, {}

        try:
            root = ET.fromstring(r.text)
        except Exception as e:
            logging.warning(f"[S3 ListParts] XML 解析失败，降级到本地记录: {e}")
            return False, {}

        ns = {'s3': 'http://s3.amazonaws.com/doc/2006-03-01/'}
        result = {}
        for part in root.findall('s3:Part', ns):
            try:
                pn_elem = part.find('s3:PartNumber', ns)
                etag_elem = part.find('s3:ETag', ns)
                if pn_elem is None or etag_elem is None:
                    continue
                pn = int(pn_elem.text)
                etag = (etag_elem.text or '').strip('"')
                if pn > 0:
                    result[pn] = etag
            except Exception:
                continue
        return True, result
    except Exception as e:
        logging.warning(f"[S3 ListParts] 异常，降级到本地记录: {e}")
        return False, {}


_PYCURL_USED_LOGGED = [False]


def _put_part_low_auth_pycurl(identifier, rel_path, upload_id, part_number, data, ak, sk, style,
                              part_progress_cb=None):
    """用 pycurl 上传单个分片（READFUNCTION 16KB 分块 + 强制新连接 + keepalive）。"""
    import io

    query = f"partNumber={part_number}&uploadId={_url_encode_query_value(upload_id)}"
    endpoint, path_encoded, url = _build_s3_request(identifier, rel_path, style, query)

    data_stream = io.BytesIO(data)
    total_bytes = len(data)
    read_count = [0]
    last_report = [0.0]

    def _readfunction(size):
        """pycurl 调用的 READFUNCTION，每次返回 ≤ 16KB。"""
        actual_size = min(size, 16384) if size > 0 else 16384
        chunk = data_stream.read(actual_size)
        if chunk and part_progress_cb is not None:
            read_count[0] += len(chunk)
            now = time.time()
            if now - last_report[0] >= 0.1:
                last_report[0] = now
                try:
                    part_progress_cb(read_count[0])
                except Exception:
                    pass
        return chunk

    resp_body = io.BytesIO()
    resp_headers = _CIHeaders()

    def _header_cb(line):
        try:
            line_str = line.decode('latin-1', errors='replace').strip()
            if ':' in line_str:
                k, v = line_str.split(':', 1)
                resp_headers.set(k.strip(), v.strip())
        except Exception:
            pass
        return len(line)

    c = pycurl.Curl()
    try:
        c.setopt(pycurl.URL, url)
        c.setopt(pycurl.UPLOAD, 1)
        c.setopt(pycurl.READFUNCTION, _readfunction)
        c.setopt(pycurl.INFILESIZE, total_bytes)
        c.setopt(pycurl.HTTPHEADER, [
            f'Authorization: LOW {ak}:{sk}',
            'Content-Type: application/octet-stream',
            'User-Agent: Mozilla/5.0',
        ])
        c.setopt(pycurl.WRITEFUNCTION, resp_body.write)
        c.setopt(pycurl.HEADERFUNCTION, _header_cb)
        c.setopt(pycurl.NOPROGRESS, 1)

        # 【P1-7】强制新连接，不复用 WARP 已死的 TCP 连接
        c.setopt(pycurl.FORBID_REUSE, 1)
        c.setopt(pycurl.FRESH_CONNECT, 1)

        # 【P1-7】TCP keepalive —— 【⏱️ 延迟与超时精细控制】读配置
        keepidle, keepintvl = _get_tcp_keepalive()
        try:
            c.setopt(pycurl.TCP_KEEPALIVE, 1)
            c.setopt(pycurl.TCP_KEEPIDLE, keepidle)
            c.setopt(pycurl.TCP_KEEPINTVL, keepintvl)
        except (AttributeError, Exception) as ke:
            logging.warning(f"[pycurl] TCP keepalive 配置失败（忽略）: {ke}")

        verify = get_ssl_verify()
        if verify:
            c.setopt(pycurl.SSL_VERIFYPEER, 1)
            c.setopt(pycurl.SSL_VERIFYHOST, 2)
        else:
            c.setopt(pycurl.SSL_VERIFYPEER, 0)
            c.setopt(pycurl.SSL_VERIFYHOST, 0)

        # 【⏱️ 延迟与超时精细控制】CONNECTTIMEOUT / TIMEOUT 读配置
        s3_conn, s3_read = _get_s3_timeout()
        c.setopt(pycurl.CONNECTTIMEOUT, s3_conn)
        c.setopt(pycurl.TIMEOUT, s3_read)

        req_proxies = get_req_proxies()
        if req_proxies:
            proxy_url = req_proxies.get('http') or req_proxies.get('https')
            if proxy_url:
                try:
                    if proxy_url.startswith('socks5h://'):
                        c.setopt(pycurl.PROXY, proxy_url[len('socks5h://'):])
                        try:
                            c.setopt(pycurl.PROXYTYPE, pycurl.PROXYTYPE_SOCKS5_HOSTNAME)
                        except AttributeError:
                            c.setopt(pycurl.PROXYTYPE, pycurl.PROXYTYPE_SOCKS5)
                    elif proxy_url.startswith('socks5://'):
                        c.setopt(pycurl.PROXY, proxy_url[len('socks5://'):])
                        c.setopt(pycurl.PROXYTYPE, pycurl.PROXYTYPE_SOCKS5)
                    elif proxy_url.startswith('http://'):
                        c.setopt(pycurl.PROXY, proxy_url[len('http://'):])
                        c.setopt(pycurl.PROXYTYPE, pycurl.PROXYTYPE_HTTP)
                    elif proxy_url.startswith('https://'):
                        c.setopt(pycurl.PROXY, proxy_url[len('https://'):])
                        c.setopt(pycurl.PROXYTYPE, pycurl.PROXYTYPE_HTTPS)
                    else:
                        c.setopt(pycurl.PROXY, proxy_url)
                except Exception as pe:
                    logging.warning(f"[pycurl] 设置代理失败，忽略: {pe}")

        if not _PYCURL_USED_LOGGED[0]:
            logging.info(f"[pycurl] 使用 pycurl 上传（进度平滑 + 强制新连接 + keepalive）")
            _PYCURL_USED_LOGGED[0] = True

        c.perform()

        if part_progress_cb is not None and read_count[0] < total_bytes:
            try:
                part_progress_cb(total_bytes)
            except Exception:
                pass

        try:
            status = c.getinfo(pycurl.RESPONSE_CODE)
        except Exception:
            status = 0
        body_bytes = resp_body.getvalue()
        return _PycurlResponse(status, body_bytes, resp_headers)
    finally:
        try:
            c.close()
        except Exception:
            pass


def _put_part_low_auth_requests(identifier, rel_path, upload_id, part_number, data, ak, sk, style,
                                part_progress_cb=None):
    """requests 版本（pycurl 不可用时回退，进度 10MB 一跳）。"""
    query = f"partNumber={part_number}&uploadId={_url_encode_query_value(upload_id)}"
    endpoint, path_encoded, url = _build_s3_request(identifier, rel_path, style, query)
    headers = {
        'Authorization': f'LOW {ak}:{sk}',
        'Content-Type': 'application/octet-stream',
        'User-Agent': 'Mozilla/5.0',
    }
    req_proxies = get_req_proxies()

    _ensure_http_send_patched()

    if part_progress_cb is not None:
        part_sent = [0]

        def _tracker(n):
            part_sent[0] += n
            try:
                part_progress_cb(part_sent[0])
            except Exception:
                pass

        _send_local.tracker = _tracker

    try:
        return requests.put(url, headers=headers, data=data,
                            verify=get_ssl_verify(), proxies=req_proxies,
                            timeout=_get_s3_timeout())
    finally:
        if part_progress_cb is not None:
            _send_local.tracker = None


def _put_part_low_auth(identifier, rel_path, upload_id, part_number, data, ak, sk, style,
                       part_progress_cb=None):
    """LOW 认证上传一个分片。pycurl 优先，requests 回退。"""
    if _PYCURL_AVAILABLE:
        try:
            return _put_part_low_auth_pycurl(identifier, rel_path, upload_id, part_number,
                                              data, ak, sk, style,
                                              part_progress_cb=part_progress_cb)
        except Exception as e:
            logging.warning(f"[pycurl] 上传失败，回退 requests: {e}")
    return _put_part_low_auth_requests(identifier, rel_path, upload_id, part_number,
                                        data, ak, sk, style,
                                        part_progress_cb=part_progress_cb)


def _put_part_with_retry(identifier, rel_path, upload_id, part_number, data, ak, sk, style,
                         max_retries=3, progress_cb_for_part=None,
                         task_id=None, expected_worker_token=None,
                         pause_task_id=None):
    """带重试的分片 PUT。"""
    last_err = None
    backoff_base = _get_s3_retry_backoff_base()
    for attempt in range(max_retries):
        if expected_worker_token is not None or pause_task_id is not None:
            _check_pause_or_token(
                task_id, expected_worker_token, pause_task_id,
                "任务已被删除，中止",
                "任务已被替换，中止",
                "用户暂停分片上传",
            )

        try:
            resp = _put_part_low_auth(identifier, rel_path, upload_id, part_number,
                                       data, ak, sk, style,
                                       part_progress_cb=progress_cb_for_part)

            if resp.status_code == 200:
                return resp

            err_text = resp.text or ""
            last_err = f"HTTP {resp.status_code} {err_text[:300]}"

            if (resp.status_code in (404, 410)
                    or 'NoSuchUpload' in err_text
                    or 'InvalidRequest' in err_text):
                raise _UploadExpiredException(f"uploadId 失效: {last_err}")

            is_rate_limit = (
                resp.status_code in (429, 503)
                or any(kw in err_text.lower() for kw in _RATE_LIMIT_KEYWORDS)
            )
            if is_rate_limit:
                cooldown_secs = int(cfg_get('ia_429_backoff', 60))
                with UPLOAD_COOLDOWN_LOCK:
                    netdisk_state_locks.GLOBAL_UPLOAD_COOLDOWN = time.time() + cooldown_secs
                logging.warning(
                    f"[S3 风控] 分片 {part_number} 触发 IA 风控，全局冷却 {cooldown_secs}s "
                    f"({attempt+1}/{max_retries}): {last_err}"
                )
                for _ in range(cooldown_secs):
                    if expected_worker_token is not None or pause_task_id is not None:
                        _check_pause_or_token(
                            task_id, expected_worker_token, pause_task_id,
                            "任务已被删除，中止",
                            "任务已被替换，中止",
                            "用户暂停（风控冷却中）",
                        )
                    time.sleep(1)
                continue

            logging.warning(
                f"[S3 PUT] 分片 {part_number} 第 {attempt+1}/{max_retries} 次失败: {last_err}"
            )
            if attempt < max_retries - 1:
                time.sleep(backoff_base ** attempt)
        except _UploadExpiredException:
            raise
        except _UploadPausedException:
            raise
        except Exception as e:
            last_err = str(e)
            if any(kw in last_err for kw in _CANCEL_KEYWORDS):
                raise
            logging.warning(
                f"[S3 PUT] 分片 {part_number} 第 {attempt+1}/{max_retries} 次异常: {last_err}"
            )
            if attempt < max_retries - 1:
                time.sleep(backoff_base ** attempt)
    raise Exception(f"分片 {part_number} 重试 {max_retries} 次仍失败: {last_err}")


def _complete_multipart_low_auth(identifier, rel_path, upload_id, xml_data, ak, sk, style):
    """LOW 认证 Complete multipart 上传（单次）。"""
    query = f"uploadId={_url_encode_query_value(upload_id)}"
    endpoint, path_encoded, url = _build_s3_request(identifier, rel_path, style, query)
    headers = {
        'Authorization': f'LOW {ak}:{sk}',
        'Content-Type': 'application/xml',
        'User-Agent': 'Mozilla/5.0',
    }
    req_proxies = get_req_proxies()
    return requests.post(url, headers=headers, data=xml_data,
                         verify=get_ssl_verify(), proxies=req_proxies,
                         timeout=_get_s3_timeout())


def _complete_with_retry(identifier, rel_path, upload_id, xml_data, ak, sk, style,
                          max_retries=3, task_id=None, expected_worker_token=None,
                          pause_task_id=None):
    """带重试的 Complete。"""
    last_err = None
    backoff_base = _get_s3_retry_backoff_base()
    for attempt in range(max_retries):
        if expected_worker_token is not None or pause_task_id is not None:
            _check_pause_or_token(
                task_id, expected_worker_token, pause_task_id,
                "任务已被删除，中止",
                "任务已被替换，中止",
                "用户暂停（Complete 阶段）",
            )

        try:
            resp = _complete_multipart_low_auth(identifier, rel_path, upload_id,
                                                  xml_data, ak, sk, style)

            if resp.status_code == 200:
                return resp

            err_text = resp.text or ""
            last_err = f"HTTP {resp.status_code} {err_text[:400]}"

            if (resp.status_code in (404, 410)
                    or 'NoSuchUpload' in err_text):
                raise _UploadExpiredException(f"uploadId 失效: {last_err}")

            is_rate_limit = (
                resp.status_code in (429, 503)
                or any(kw in err_text.lower() for kw in _RATE_LIMIT_KEYWORDS)
            )
            if is_rate_limit:
                cooldown_secs = int(cfg_get('ia_429_backoff', 60))
                with UPLOAD_COOLDOWN_LOCK:
                    netdisk_state_locks.GLOBAL_UPLOAD_COOLDOWN = time.time() + cooldown_secs
                logging.warning(
                    f"[S3 Complete 风控] 触发 IA 风控，全局冷却 {cooldown_secs}s "
                    f"({attempt+1}/{max_retries}): {last_err}"
                )
                for _ in range(cooldown_secs):
                    if expected_worker_token is not None or pause_task_id is not None:
                        _check_pause_or_token(
                            task_id, expected_worker_token, pause_task_id,
                            "任务已被删除，中止",
                            "任务已被替换，中止",
                            "用户暂停（Complete 风控冷却中）",
                        )
                    time.sleep(1)
                continue

            logging.warning(f"[S3 Complete] 第 {attempt+1}/{max_retries} 次失败: {last_err}")
            if attempt < max_retries - 1:
                time.sleep(backoff_base ** attempt)
        except _UploadExpiredException:
            raise
        except _UploadPausedException:
            raise
        except Exception as e:
            last_err = str(e)
            if any(kw in last_err for kw in _CANCEL_KEYWORDS):
                raise
            logging.warning(f"[S3 Complete] 第 {attempt+1}/{max_retries} 次异常: {last_err}")
            if attempt < max_retries - 1:
                time.sleep(backoff_base ** attempt)
    raise Exception(f"Complete 重试 {max_retries} 次仍失败: {last_err}")


def _sign_s3_v2_query(access_key, secret_key, method, bucket, key_raw, expires_in=3600):
    expires = int(time.time()) + expires_in
    string_to_sign = f"{method}\n\n\n{expires}\n/{bucket}/{key_raw}?uploads"
    h = hmac.new(secret_key.encode('utf-8'), string_to_sign.encode('utf-8'), hashlib.sha1)
    signature = base64.b64encode(h.digest()).decode('utf-8')
    return {
        'AWSAccessKeyId': urllib.parse.quote(access_key, safe=''),
        'Expires': str(expires),
        'Signature': urllib.parse.quote(signature, safe=''),
    }


def _try_initiate_v2_query(identifier, rel_path, ak, sk, style):
    params = _sign_s3_v2_query(ak, sk, 'POST', identifier, rel_path)
    query = f"AWSAccessKeyId={params['AWSAccessKeyId']}&Expires={params['Expires']}&Signature={params['Signature']}&uploads"
    endpoint, path_encoded, url = _build_s3_request(identifier, rel_path, style, query)
    headers = {"User-Agent": "Mozilla/5.0"}
    req_proxies = get_req_proxies()
    r = requests.post(url, headers=headers, verify=get_ssl_verify(), proxies=req_proxies,
                      timeout=_get_s3_timeout())
    if r.status_code != 200:
        raise Exception(f"HTTP {r.status_code}: {(r.text or '')[:300]}")
    try:
        root = ET.fromstring(r.text)
    except Exception as e:
        raise Exception(f"XML 解析失败: {e}, 响应: {(r.text or '')[:200]}")
    ns = {'s3': 'http://s3.amazonaws.com/doc/2006-03-01/'}
    elem = root.find('s3:UploadId', ns)
    if elem is None or not elem.text:
        raise Exception(f"响应无 UploadId: {(r.text or '')[:300]}")
    return elem.text


def _try_make_ia_file(rel_path, pf, sz, md5_hex, sha1_hex):
    if not (md5_hex and sha1_hex):
        return None
    try:
        from internetarchive import File as _IAFile
    except ImportError:
        return None
    try:
        return _IAFile(rel_path, fileobj=pf, size=sz, md5=md5_hex, sha1=sha1_hex)
    except (TypeError, AttributeError, ValueError):
        pass
    except Exception:
        pass
    try:
        return _IAFile(rel_path, file=fp, size=sz, md5=md5_hex, sha1=sha1_hex)
    except (TypeError, AttributeError, ValueError):
        pass
    except Exception:
        pass
    try:
        return _IAFile(name=rel_path, fileobj=pf, size=sz, md5=md5_hex, sha1=sha1_hex)
    except (TypeError, AttributeError, ValueError):
        pass
    except Exception:
        pass
    return None


def _collect_meta_form(form):
    return {
        'creator':     form.get('creator', '').strip(),
        'description': form.get('description', '').strip(),
        'date':        form.get('date', '').strip(),
        'custom_date': form.get('custom_date', '').strip(),
        'mediatype':   (form.get('mediatype') or 'software').strip(),
        'collection':  (form.get('collection') or 'opensource').strip(),
        'language':    form.get('language', '').strip(),
        'license':     form.get('license', '').strip(),
        'tags':        form.get('tags', '').strip(),
    }


def _build_upload_opts(form):
    opts = {
        'generate_torrent':     form.get('generate_torrent') == 'yes',
        'no_derive':            form.get('no_derive') == 'yes',
        'keep_old':             form.get('keep_old') == 'yes',
        'no_directory':         form.get('no_directory') == 'yes',
        'force_overwrite':      form.get('force_overwrite') == 'yes',
        'overwrite_same_file':  form.get('overwrite_same_file') == 'yes',
        'chunk_threshold':      max(20, int(form.get('chunk_threshold', 200))),
        'chunk_size':           max(5, min(int(form.get('chunk_size', 10)), 50)),
        'no_index':             form.get('no_index') == 'yes',
        'interactive_priority': form.get('interactive_priority') == 'yes',
        'dark_mode':            form.get('dark_mode') == 'yes',
        'skip_meta':            form.get('skip_meta') == 'yes',
        'checksum':             form.get('checksum') == 'yes',
        'access_level':         form.get('access_level', 'public'),
        'size_hint':            form.get('size_hint', '').strip(),
        'custom_headers':       form.get('custom_headers', '').strip(),
        'concurrency':          form.get('concurrency', '8'),
    }
    try:
        r = int(form.get('retries') or 0)
        opts['retries'] = max(0, min(r, cfg_get('upload_retries_max', 3)))
    except:
        opts['retries'] = 0
    try:
        s = int(form.get('sleep') or 0)
        opts['sleep'] = max(cfg_get('upload_sleep_min', 10), s)
    except:
        opts['sleep'] = cfg_get('upload_sleep_min', 10)
    if opts['generate_torrent'] and opts['no_derive']:
        dp = cfg_get('derive_priority', 'no_derive')
        if dp == 'no_derive':
            opts['generate_torrent'] = False
        elif dp == 'generate_torrent':
            opts['no_derive'] = False
    return opts


def _opts_to_metadata(opts):
    md = {}
    if opts.get('generate_torrent'):
        md['x-archive-queue-derive'] = '1'
    if opts.get('no_derive'):
        md['x-archive-no-derive'] = '1'
    if opts.get('force_overwrite'):
        md['x-archive-ignore-preexisting-bucket'] = '1'
    if opts.get('access_level') == 'restricted':
        md['access'] = 'restricted'
    if opts.get('keep_old'):
        md['x-archive-keep-old-version'] = '1'
    if opts.get('no_directory'):
        md['x-archive-no-directory'] = '1'
    if opts.get('no_index'):
        md['x-archive-no-index'] = '1'
    if opts.get('interactive_priority'):
        md['x-archive-interactive-priority'] = '1'
    if opts.get('dark_mode'):
        md['x-archive-dark-theme'] = '1'
    if opts.get('skip_meta'):
        md['x-archive-skip-meta'] = '1'
    if opts.get('size_hint'):
        md['x-archive-size-hint'] = opts['size_hint']
    return md


class ProgressFileWrapper:
    def __init__(self, fp, rel_path, total_bytes, progress_map, lock):
        self.file = open(fp, 'rb')
        self.rel_path = rel_path
        self.total_bytes = total_bytes
        self.progress_map = progress_map
        self.lock = lock
        with self.lock:
            self.progress_map[self.rel_path] = (0, self.total_bytes)

    def read(self, size=-1):
        data = self.file.read(size)
        if data:
            with self.lock:
                self.progress_map[self.rel_path] = (self.file.tell(), self.total_bytes)
        return data

    def readinto(self, b):
        data = self.file.readinto(b)
        if data:
            with self.lock:
                self.progress_map[self.rel_path] = (self.file.tell(), self.total_bytes)
        return data

    def seek(self, offset, whence=0):
        res = self.file.seek(offset, whence)
        with self.lock:
            self.progress_map[self.rel_path] = (self.file.tell(), self.total_bytes)
        return res

    def tell(self):
        return self.file.tell()

    def close(self):
        self.file.close()
        with self.lock:
            self.progress_map.pop(self.rel_path, None)

    def __len__(self):
        return self.total_bytes

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def sign_s3_v4(access_key, secret_key, method, endpoint_url, path, query_string, headers,
               payload_hash, region='us-east-1', service='s3'):
    t = datetime.utcnow()
    amz_date = t.strftime('%Y%m%dT%H%M%SZ')
    date_stamp = t.strftime('%Y%m%d')
    headers['x-amz-date'] = amz_date
    headers['host'] = urllib.parse.urlparse(endpoint_url).netloc
    if payload_hash:
        headers['x-amz-content-sha256'] = payload_hash
    canonical_uri = path
    canonical_querystring = query_string
    sorted_headers = sorted([(k.lower(), str(v).strip()) for k, v in headers.items()])
    canonical_headers = ''.join([f"{k}:{v}\n" for k, v in sorted_headers])
    signed_headers = ';'.join([k for k, v in sorted_headers])
    canonical_request = f"{method}\n{canonical_uri}\n{canonical_querystring}\n{canonical_headers}\n{signed_headers}\n{payload_hash}"
    algorithm = 'AWS4-HMAC-SHA256'
    credential_scope = f"{date_stamp}/{region}/{service}/aws4_request"
    string_to_sign = f"{algorithm}\n{amz_date}\n{credential_scope}\n{hashlib.sha256(canonical_request.encode('utf-8')).hexdigest()}"

    def sign(key, msg):
        return hmac.new(key, msg.encode('utf-8'), hashlib.sha256).digest()

    kDate = sign(('AWS4' + secret_key).encode('utf-8'), date_stamp)
    kRegion = sign(kDate, region)
    kService = sign(kRegion, service)
    kSigning = sign(kService, 'aws4_request')
    signature = hmac.new(kSigning, string_to_sign.encode('utf-8'), hashlib.sha256).hexdigest()
    authorization_header = f"{algorithm} Credential={access_key}/{credential_scope}, SignedHeaders={signed_headers}, Signature={signature}"
    headers['Authorization'] = authorization_header
    return headers


def _url_encode_path(path):
    return urllib.parse.quote(path, safe='/')


def _url_encode_query_value(v):
    return urllib.parse.quote(str(v), safe='')


def _compute_md5_sha1_with_progress(fp, file_size, rel_path, progress_callback,
                                     task_id=None, expected_worker_token=None,
                                     pause_task_id=None):
    """计算 md5/sha1（带进度回调 + token/pause 检查）。"""
    md5 = hashlib.md5()
    sha1 = hashlib.sha1()
    read_bytes = 0
    last_report = 0.0
    last_token_check = 0
    report_interval = 0.5
    token_check_interval = 1024 * 1024

    with open(fp, 'rb') as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            md5.update(chunk)
            sha1.update(chunk)
            read_bytes += len(chunk)

            if (task_id is not None
                    and (expected_worker_token is not None or pause_task_id is not None)
                    and read_bytes - last_token_check >= token_check_interval):
                last_token_check = read_bytes
                _check_pause_or_token(
                    task_id, expected_worker_token, pause_task_id,
                    "任务已被删除，中止 md5 计算",
                    "任务已被替换，中止 md5 计算",
                    "用户暂停（md5 计算中）",
                )

            now = time.time()
            if now - last_report > report_interval:
                last_report = now
                if progress_callback is not None and file_size > 0:
                    try:
                        fake_progress = int(file_size * 0.05 * (read_bytes / file_size))
                        progress_callback(rel_path, fake_progress, file_size)
                    except Exception:
                        pass

    return md5.hexdigest(), sha1.hexdigest()


def _fallback_to_single_upload(identifier, rel_path, fp, file_size, opts,
                                task_id, expected_worker_token, progress_callback,
                                pause_task_id=None):
    """Initiate 失败时 fallback 到 ia.upload() 单次上传。"""
    import internetarchive as ia

    if expected_worker_token is not None or pause_task_id is not None:
        _check_pause_or_token(
            task_id, expected_worker_token, pause_task_id,
            "任务已被删除，中止 fallback 上传",
            "任务已被替换，中止 fallback 上传",
            "用户暂停（fallback 上传前）",
        )

    with DB_LOCK:
        conn = get_db()
        try:
            row = conn.execute(
                "SELECT title, creator, description, date, mediatype, collection, "
                "language, license, tags, custom_date FROM items WHERE identifier=?",
                (identifier,)
            ).fetchone()
        finally:
            conn.close()

    if not row:
        raise Exception(f"Fallback 上传失败：items 表中找不到 identifier={identifier}")

    title, creator, desc, date, media, coll, lang, lic, tags, custom_date = row

    meta_dict = {
        "title": title or identifier,
        "mediatype": media or "software",
        "collection": coll or "opensource",
    }
    if creator:
        meta_dict["creator"] = creator
    if desc:
        meta_dict["description"] = desc
    if date:
        meta_dict["date"] = date
    if custom_date:
        meta_dict["x-archive-meta-date"] = custom_date
    if lang:
        meta_dict["language"] = lang
    if lic:
        meta_dict["licenseurl"] = lic
    if tags:
        meta_dict["subject"] = tags
    meta_dict.update(_opts_to_metadata(opts))

    logging.info(f"[S3 fallback] 正在计算 md5/sha1: {rel_path} ({file_size/1024/1024:.1f}MB)")
    md5_hex, sha1_hex = _compute_md5_sha1_with_progress(
        fp, file_size, rel_path, progress_callback,
        task_id=task_id, expected_worker_token=expected_worker_token,
        pause_task_id=pause_task_id,
    )

    if expected_worker_token is not None or pause_task_id is not None:
        _check_pause_or_token(
            task_id, expected_worker_token, pause_task_id,
            "任务已被删除，中止 fallback 上传",
            "任务已被替换，中止 fallback 上传",
            "用户暂停（md5 计算后）",
        )

    _ensure_http_send_patched()

    total_sent = [0]
    base_progress = int(file_size * 0.05)
    last_log_time = [0.0]

    def _progress_tracker(n):
        total_sent[0] += n
        now = time.time()
        if now - last_log_time[0] >= 0.5:
            last_log_time[0] = now
            pct = min(100.0, total_sent[0] * 100 / file_size) if file_size > 0 else 0.0
            try:
                print(
                    f"[S3 fallback] 上传中: {rel_path} {pct:.2f}% "
                    f"({total_sent[0]/1024/1024:.2f}MB/{file_size/1024/1024:.2f}MB)",
                    file=sys.stderr, flush=True
                )
            except Exception:
                pass
        if progress_callback is not None:
            try:
                sent = min(total_sent[0], file_size)
                mapped = base_progress + int(sent * 0.95)
                if mapped > file_size:
                    mapped = file_size
                progress_callback(rel_path, mapped, file_size)
            except Exception:
                pass

    fallback_progress_map = {}
    fallback_lock = threading.Lock()

    _send_local.tracker = _progress_tracker
    try:
        with ProgressFileWrapper(fp, rel_path, file_size, fallback_progress_map, fallback_lock) as pf:
            ia_file = _try_make_ia_file(rel_path, pf, file_size, md5_hex, sha1_hex)
            if ia_file is not None:
                files_param = {rel_path: ia_file}
            else:
                try:
                    pf.seek(0)
                except Exception:
                    pass
                files_param = {rel_path: pf}

            logging.info(f"[S3 fallback] 开始单次上传: {rel_path} ({file_size/1024/1024:.1f}MB)")
            try:
                ia.upload(identifier, files=files_param, metadata=meta_dict,
                          retries=0, verbose=False, verify=get_ssl_verify())
            except Exception as e:
                raise Exception(f"Fallback 单次上传失败: {e}")
            logging.info(f"[S3 fallback] 单次上传完成: {rel_path}")
    finally:
        _send_local.tracker = None

    if expected_worker_token is not None or pause_task_id is not None:
        _check_pause_or_token(
            task_id, expected_worker_token, pause_task_id,
            "上传完成但任务已被删除",
            "上传完成但任务已被替换",
            "上传完成但用户已暂停",
            check_paused=False,
        )

    if progress_callback is not None:
        try:
            progress_callback(rel_path, file_size, file_size)
        except Exception:
            pass


def _upload_large_file_s3(identifier, rel_path, fp, file_size, opts, task_id,
                          expected_worker_token=None, progress_callback=None,
                          metadata=None, check_paused_task_id=None):
    """大文件 S3 分块上传。

    【G-59 第 1 轮】新增 check_paused_task_id
    【⏱️ 延迟与超时精细控制】分片间 sleep 读 s3_part_sleep 配置
    【R390 修复 - 2026-11】3 处文件大小检查（详见文件头注释）
    """
    ak, sk = _get_s3_credentials()
    if not ak:
        raise Exception("S3 密钥获取失败，无法进行断点续传")
    chunk_size_mb = opts.get('chunk_size', 10)
    chunk_size = chunk_size_mb * 1024 * 1024

    for _reexamine_attempt in range(2):
        try:
            # 【R390 修复 - 2026-11】分片计算前检查文件大小
            #   场景：调用方（upload_one_file / retry_single_file_worker）拿到 file_size 后到
            #        调用本函数之间（含 _reexamine_attempt 第二次重试）文件被修改。
            #   后果：total_parts 按旧 file_size 算 → 上传不完整或读空分片。
            #   为什么放在 for 循环内：每次 _UploadExpiredException 重试都重新检查，
            #        避免第二次重试时 total_parts 用第一次的旧值。
            try:
                _actual_sz = os.path.getsize(fp)
            except (OSError, ValueError) as e:
                raise Exception(f"读取文件大小失败: {e}")
            if _actual_sz != file_size:
                raise Exception(
                    f"文件大小在上传前已变更（{file_size}→{_actual_sz} 字节），"
                    f"请重新提交上传任务（避免上传不完整的文件）"
                )
            total_parts = (file_size + chunk_size - 1) // chunk_size

            upload_id = None
            init_style = 'path'
            conn = get_db()
            try:
                row = conn.execute(
                    "SELECT upload_id, etag FROM file_chunks WHERE identifier=? AND file_path=? AND part_number=0 LIMIT 1",
                    (identifier, rel_path)
                ).fetchone()
                if row:
                    upload_id = row[0]
                    init_style = row[1] if row[1] in ('virtual', 'path') else 'path'
            finally:
                conn.close()

            if not upload_id:
                if expected_worker_token is not None or check_paused_task_id is not None:
                    _check_pause_or_token(
                        task_id, expected_worker_token, check_paused_task_id,
                        "任务已被删除，中止 Initiate",
                        "任务已被替换，中止 Initiate",
                        "用户暂停（Initiate 前）",
                    )

                errors = []
                virtual_safe = _is_virtual_safe_identifier(identifier)

                probe_ok, probe_err = _ensure_bucket_exists(identifier, ak, sk)
                if probe_ok:
                    logging.info(f"[S3 Bucket] 探针成功，bucket '{identifier}' 已就绪")
                else:
                    errors.append(f"probe: {probe_err}")
                    logging.warning(f"[S3 Bucket] 探针失败（继续尝试 Initiate）: {probe_err}")

                attempts = []
                if virtual_safe:
                    logging.info(f"[S3 Initiate] 尝试 virtual-hosted-style: https://{identifier}.s3.us.archive.org/")
                    attempts.append(('virtual', 'LOW', lambda: _try_initiate_low_auth(identifier, rel_path, ak, sk, 'virtual')))
                attempts.append(('path', 'LOW', lambda: _try_initiate_low_auth(identifier, rel_path, ak, sk, 'path')))
                if virtual_safe:
                    attempts.append(('virtual', 'v2_query', lambda: _try_initiate_v2_query(identifier, rel_path, ak, sk, 'virtual')))
                attempts.append(('path', 'v2_query', lambda: _try_initiate_v2_query(identifier, rel_path, ak, sk, 'path')))

                for style, sign_type, fn in attempts:
                    try:
                        upload_id = fn()
                        init_style = style
                        logging.info(f"[S3 Initiate] {style}+{sign_type} 成功: {identifier}")
                        break
                    except Exception as e:
                        errors.append(f"{style}+{sign_type}: {e}")
                        logging.warning(f"[S3 Initiate] {style}+{sign_type} 失败: {e}")

                if upload_id is None:
                    logging.warning(
                        f"[S3 Initiate] 所有方式均失败，fallback 到 ia.upload() "
                        f"（文件: {rel_path}, 大小: {file_size/1024/1024:.1f}MB）"
                        f"\n  错误详情: {'; '.join(errors)}"
                    )
                    _fallback_to_single_upload(
                        identifier, rel_path, fp, file_size, opts,
                        task_id, expected_worker_token, progress_callback,
                        pause_task_id=check_paused_task_id,
                    )
                    return

                conn = get_db()
                try:
                    conn.execute(
                        "INSERT INTO file_chunks (identifier, file_path, upload_id, part_number, etag) VALUES (?, ?, ?, 0, ?)",
                        (identifier, rel_path, upload_id, init_style)
                    )
                    conn.commit()
                finally:
                    conn.close()
            else:
                if expected_worker_token is not None or check_paused_task_id is not None:
                    _check_pause_or_token(
                        task_id, expected_worker_token, check_paused_task_id,
                        "任务已被删除，中止 ListParts",
                        "任务已被替换，中止 ListParts",
                        "用户暂停（ListParts 前）",
                    )

                logging.info(f"[S3 Resume] 检测到断点 uploadId，调 ListParts 补全本地记录: {rel_path}")
                list_result = _list_uploaded_parts(identifier, rel_path, upload_id, ak, sk, init_style)

                if list_result is None:
                    raise _UploadExpiredException("ListParts 显示 uploadId 已失效")

                list_ok, ia_parts = list_result

                if list_ok:
                    logging.info(
                        f"[S3 ListParts] IA 上有 {len(ia_parts)} 个分片已上传: {sorted(ia_parts.keys())}"
                    )
                    c = get_db()
                    try:
                        local_parts = {r[0] for r in c.execute(
                            "SELECT part_number FROM file_chunks WHERE identifier=? AND file_path=? AND part_number > 0",
                            (identifier, rel_path)
                        ).fetchall()}
                        stale = local_parts - set(ia_parts.keys())
                        if stale:
                            logging.warning(
                                f"[S3 ListParts] 发现 {len(stale)} 个本地记录但 IA 已删除的分片: "
                                f"{sorted(stale)}，清理以避免 Complete 死循环"
                            )
                            for pn in stale:
                                c.execute(
                                    "DELETE FROM file_chunks WHERE identifier=? AND file_path=? AND part_number=?",
                                    (identifier, rel_path, pn)
                                )

                        missing = set(ia_parts.keys()) - local_parts
                        if missing:
                            logging.info(
                                f"[S3 ListParts] 补全 {len(missing)} 个 IA 有但本地缺的分片: "
                                f"{sorted(missing)}"
                            )
                        for pn, etag in ia_parts.items():
                            c.execute(
                                "INSERT OR REPLACE INTO file_chunks "
                                "(identifier, file_path, upload_id, part_number, etag, status) "
                                "VALUES (?, ?, ?, ?, ?, 'uploaded')",
                                (identifier, rel_path, upload_id, pn, etag)
                            )
                        c.commit()
                    finally:
                        c.close()
                else:
                    logging.info(f"[S3 ListParts] 查询失败，用本地记录")

            uploaded_parts = set()
            conn = get_db()
            try:
                uploaded_parts = {
                    row[0] for row in conn.execute(
                        "SELECT part_number FROM file_chunks WHERE identifier=? AND file_path=? AND status='uploaded'",
                        (identifier, rel_path)
                    ).fetchall()
                }
            finally:
                conn.close()

            if uploaded_parts:
                logging.info(f"[S3 Resume] 检测到 {len(uploaded_parts)} 片已上传，跳过: {sorted(uploaded_parts)}")

            completed_bytes_in_this_file = 0
            for pn in uploaded_parts:
                if pn < total_parts:
                    completed_bytes_in_this_file += chunk_size
                else:
                    last_part_size = file_size - (total_parts - 1) * chunk_size
                    completed_bytes_in_this_file += last_part_size

            if progress_callback is not None:
                try:
                    progress_callback(rel_path, completed_bytes_in_this_file, file_size)
                except Exception:
                    pass

            with open(fp, 'rb') as f:
                for part_number in range(1, total_parts + 1):
                    if part_number in uploaded_parts:
                        logging.info(f"[S3 Upload] 分片 {part_number}/{total_parts} 已上传，跳过")
                        continue

                    if expected_worker_token is not None or check_paused_task_id is not None:
                        _check_pause_or_token(
                            task_id, expected_worker_token, check_paused_task_id,
                            "任务已被删除，中止分片上传",
                            "任务已被替换，中止分片上传",
                            "用户暂停（分片循环中）",
                        )

                    with UPLOAD_COOLDOWN_LOCK:
                        cooldown = netdisk_state_locks.GLOBAL_UPLOAD_COOLDOWN
                    now = time.time()
                    if now < cooldown:
                        time.sleep(cooldown - now + random.uniform(0.1, 1.0))

                    offset = (part_number - 1) * chunk_size
                    f.seek(offset)
                    data = f.read(chunk_size)

                    # 【R390 修复 - 2026-11】检查分片读取字节数，防文件在上传过程中被修改（变小）
                    #   场景：分片循环期间用户用 termux 编辑/truncate 了文件
                    #   后果：f.read 返回不足 chunk_size → 上传不完整分片 → IA 报错或损坏
                    #   预期长度：
                    #     - 非最后一片：chunk_size
                    #     - 最后一片：file_size - (total_parts - 1) * chunk_size
                    _expected_len = min(chunk_size, file_size - offset)
                    if len(data) != _expected_len:
                        raise Exception(
                            f"文件分片 {part_number}/{total_parts} 读取异常"
                            f"（预期 {_expected_len} 字节，实际 {len(data)} 字节），"
                            f"文件可能在上传过程中被修改，请重新提交上传任务"
                        )

                    logging.info(
                        f"[S3 Upload] 分片 {part_number}/{total_parts} 开始上传 "
                        f"({len(data)/1024/1024:.1f}MB): {rel_path}"
                    )

                    def _make_part_progress_cb(completed_before_this_part):
                        def _cb(part_sent):
                            if progress_callback is None:
                                return
                            file_uploaded = completed_before_this_part + part_sent
                            if file_uploaded > file_size:
                                file_uploaded = file_size
                            try:
                                progress_callback(rel_path, file_uploaded, file_size)
                            except Exception:
                                pass
                        return _cb

                    resp = _put_part_with_retry(
                        identifier, rel_path, upload_id, part_number,
                        data, ak, sk, init_style, max_retries=3,
                        progress_cb_for_part=_make_part_progress_cb(completed_bytes_in_this_file),
                        task_id=task_id,
                        expected_worker_token=expected_worker_token,
                        pause_task_id=check_paused_task_id,
                    )

                    etag = resp.headers.get('ETag', '').strip('"')

                    c = get_db()
                    try:
                        c.execute(
                            "INSERT OR REPLACE INTO file_chunks "
                            "(identifier, file_path, upload_id, part_number, etag, status) "
                            "VALUES (?, ?, ?, ?, ?, 'uploaded')",
                            (identifier, rel_path, upload_id, part_number, etag)
                        )
                        c.commit()
                    finally:
                        c.close()

                    completed_bytes_in_this_file += len(data)

                    logging.info(
                        f"[S3 Upload] 分片 {part_number}/{total_parts} 完成 "
                        f"(ETag: {etag[:20]}...): {rel_path}"
                    )

                    time.sleep(_get_s3_part_sleep())

            c = get_db()
            try:
                rows = c.execute(
                    "SELECT part_number, etag FROM file_chunks "
                    "WHERE identifier=? AND file_path=? AND status='uploaded' ORDER BY part_number",
                    (identifier, rel_path)
                ).fetchall()
            finally:
                c.close()

            xml_parts = "".join(
                [f"<Part><PartNumber>{r[0]}</PartNumber><ETag>\"{r[1]}\"</ETag></Part>" for r in rows]
            )
            xml_data = f"<CompleteMultipartUpload>{xml_parts}</CompleteMultipartUpload>".encode('utf-8')

            logging.info(f"[S3 Complete] 发送 Complete 请求: {rel_path}（{len(rows)} 片）")

            resp = _complete_with_retry(
                identifier, rel_path, upload_id, xml_data, ak, sk, init_style,
                max_retries=3,
                task_id=task_id,
                expected_worker_token=expected_worker_token,
                pause_task_id=check_paused_task_id,
            )

            logging.info(f"[S3 Complete] 成功: {rel_path}")

            # 【R390 修复 - 2026-11】上传完成后检查文件大小（仅 warning，不阻塞）
            #   场景：文件在分片循环期间变大（追加内容）
            #   后果：total_parts 按旧 file_size 算 → 末尾追加内容未上传
            #   为什么只 warning 不抛异常：Complete 已成功，回滚需 delete + 重新上传整个文件，
            #   代价大；且用户主动追加场景概率极低。warning 让用户知道可手动检查 IA 端文件。
            try:
                _post_sz = os.path.getsize(fp)
            except (OSError, ValueError):
                _post_sz = file_size
            if _post_sz != file_size:
                logging.warning(
                    f"[S3 上传完成后] 文件大小变更: {rel_path} {file_size}→{_post_sz} 字节"
                    f"（如为变大，末尾追加内容可能未上传；如为变小，上传内容可能不完整。请检查 IA 端文件）"
                )

            if metadata:
                _push_metadata_after_complete(identifier, metadata, ak, sk)

            c = get_db()
            try:
                c.execute("DELETE FROM file_chunks WHERE identifier=? AND file_path=?", (identifier, rel_path))
                c.commit()
            finally:
                c.close()

            return

        except _UploadPausedException:
            logging.info(f"[S3 Paused] 用户暂停分片上传，保留断点: {rel_path}")
            raise
        except _UploadExpiredException as e:
            if _reexamine_attempt == 0:
                logging.warning(
                    f"[S3 Retry] uploadId 失效，清理 file_chunks 后重新 Initiate 一次: {e}"
                )
                try:
                    c = get_db()
                    try:
                        c.execute(
                            "DELETE FROM file_chunks WHERE identifier=? AND file_path=?",
                            (identifier, rel_path)
                        )
                        c.commit()
                    finally:
                        c.close()
                except Exception as ce:
                    logging.error(f"[S3 Retry] 清理 file_chunks 失败: {ce}")
                continue
            else:
                logging.error(f"[S3 Retry] 第二次 uploadId 失效，放弃: {e}")
                raise