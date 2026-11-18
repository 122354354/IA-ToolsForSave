# netdisk_utils_ia.py
# IA 文件缓存/拉取、ZIP 结构解析、用户统计、缩略图上传、磁力计算
#
# 【🔴 R362 修复 - 2026-11（跨文件同源修复 - 与 R356 / R361 同类）】
#   问题：_fetch_and_cache_ia_files 里更新 items 时未检查 deleted_at IS NULL：
#     conn.execute("UPDATE items SET file_name=?, direct_url=? WHERE identifier=?", ...)
#   触发场景（0.01% 概率但用户可感知）：
#     1. 用户把条目移到回收站（/trash_item → deleted_at=CURRENT_TIMESTAMP）
#     2. 后台有 worker 正在跑（例如用户点"重新提取"、upload_worker 的
#        _fetch_and_cache_ia_files(force=True)、delete_files_worker 完成后
#        _fetch_and_cache_ia_files(force=True)、rename_files_worker 完成后
#        _fetch_and_cache_ia_files(force=True)）
#     3. _fetch_and_cache_ia_files 的 UPDATE items SET file_name=?, direct_url=?
#        WHERE identifier=? 不管 deleted_at
#     4. → 回收站条目的 file_name / direct_url 被覆盖成"最新最大文件"
#     5. 用户从回收站恢复后，看到"奇怪"的直链（自己没设过，被 refresh 覆盖）
#   修复：UPDATE items 加 `AND deleted_at IS NULL`。
#   为什么 file_cache 表的写入不加 deleted_at 过滤：
#     - file_cache 表是 per-identifier 缓存（没有 owner / deleted_at 概念）
#     - serve_thumb / api_file_cache / 下载代理都会读它
#     - 回收站恢复后需要它保持最新（否则恢复后看不到文件列表）
#     - 只修 items 表的 UPDATE，不改 file_cache 的写
#   为什么修复无回归：
#     - 归档区（deleted_at IS NULL）的条目：UPDATE items 正常执行 ✅
#     - 回收站（deleted_at NOT NULL）的条目：UPDATE items 影响 0 行，
#       file_cache 仍更新（恢复后能看到最新文件列表）✅
#
# 【📝 保留记录 - 2026-11，低优先，以后修】
#   R397（本轮记录）：_fetch_and_cache_ia_files 的 file_cache 表 DELETE + INSERT
#                    是 per-identifier 操作（无 owner / deleted_at 概念）。
#                    若多个用户共享同一个 identifier（IA 是公共库，理论上可能），
#                    file_cache 会被后者覆盖。但 file_cache 本质是"公开文件列表缓存"，
#                    覆盖后只是"用最新数据"，无副作用。记录。
#   R398（本轮记录）：_fetch_and_cache_ia_files 里 `old_custom` 从旧 file_cache 读
#                    custom_direct_url 保留。回收站条目的 custom_direct_url 会
#                    被保留并重新写回新 file_cache（合理，恢复后用户自定义直链还在）。
#   R399（本轮记录）：compute_magnet 的 UPDATE items SET magnet=? WHERE identifier=?
#                    未加 deleted_at IS NULL（与 R362 同类）。
#                    但 magnet 是"与文件内容无关的固定值"（种子解析结果），
#                    回收站恢复后仍有效，不影响用户体验。且磁力计算成本高
#                    （下载 .torrent + bencode 解析），回收站条目计算后缓存下来
#                    对恢复后的用户有益。记录，暂不修。
#                    若未来用户反馈"回收站条目被计算磁力占资源"，可加 deleted_at 过滤。
#
# 【📝 同源问题记录 - 其他文件，后续统一修】
#   - netdisk_utils_upload.py 的 _upload_large_file_s3 内部文件大小变化导致
#     上传不完整（R390）→ 待下一个文件修复

import os
import re
import time
import struct
import hashlib
import logging
import urllib.parse
import requests
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

import internetarchive as ia

from netdisk_state import (
    DB_LOCK, get_db, cfg_get,
)
from netdisk_utils_common import (
    _is_system_file,
    MAGNET_FAIL_CACHE, MAGNET_LOCK,
    _magnet_acquire, _magnet_release,
)
from netdisk_utils_net import (
    ia_get, get_ssl_verify, get_req_proxies, ensure_ia_config,
)


def _fetch_and_cache_ia_files(identifier, force=False):
    if not force:
        ttl = cfg_get('file_cache_ttl', -1)
        if ttl >= 0:
            conn = get_db()
            row = conn.execute("SELECT MAX(cached_at), COUNT(*) FROM file_cache WHERE identifier=?", (identifier,)).fetchone()
            conn.close()
            if row and row[1] > 0 and row[0] and ttl > 0:
                try:
                    last_ts = datetime.strptime(row[0], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).timestamp()
                    if time.time() - last_ts < ttl: return False
                except: pass
    resp = ia_get(f"https://archive.org/metadata/{identifier}", timeout=15)
    if not resp or resp.status_code != 200: return False
    try: data = resp.json()
    except: return False
    if not data or not data.get('files'): return False
    torrent_map = {f['name'][:-8]: f['name'] for f in data['files'] if f.get('name', '').lower().endswith('.torrent')}
    with DB_LOCK:
        conn = get_db()
        try:
            old_custom = {r[0]: r[1] for r in conn.execute("SELECT filename, custom_direct_url FROM file_cache WHERE identifier=?", (identifier,)).fetchall() if r[1]}
            conn.execute("DELETE FROM file_cache WHERE identifier=?", (identifier,))
            count = 0
            for f in data['files']:
                n = f.get('name', '')
                if _is_system_file(n): continue
                conn.execute("INSERT INTO file_cache (identifier, filename, size, md5, torrent_filename, custom_direct_url) VALUES (?, ?, ?, ?, ?, ?)",
                             (identifier, n, f.get('size', ''), f.get('md5', ''), torrent_map.get(n, ''), old_custom.get(n)))
                count += 1
            if count > 0:
                largest = conn.execute("SELECT filename FROM file_cache WHERE identifier=? ORDER BY CAST(COALESCE(NULLIF(size,''),'0') AS INTEGER) DESC LIMIT 1", (identifier,)).fetchone()
                if largest:
                    custom_url = old_custom.get(largest[0])
                    final_url = custom_url or f"https://archive.org/download/{identifier}/{largest[0]}"
                    # 【R362 修复 - 2026-11】加 `AND deleted_at IS NULL`，防覆盖回收站条目的
                    # file_name / direct_url（用户从回收站恢复后会看到"奇怪"的直链）。
                    # 为什么不修 file_cache 的写：
                    #   - file_cache 表是 per-identifier 缓存（没有 owner / deleted_at 概念）
                    #   - serve_thumb / api_file_cache / 下载代理都会读它
                    #   - 回收站恢复后需要它保持最新（否则恢复后看不到文件列表）
                    conn.execute(
                        "UPDATE items SET file_name=?, direct_url=? WHERE identifier=? AND deleted_at IS NULL",
                        (largest[0], final_url, identifier)
                    )
            conn.commit()
        except Exception as e:
            conn.rollback()
            logging.error(f"缓存文件列表失败: {e}")
            return False
        finally:
            conn.close()
    return True


def fetch_zip_structure(identifier, filename):
    safe_filename = urllib.parse.quote(filename)
    req_proxies = get_req_proxies()
    ssl_verify = get_ssl_verify()

    try:
        html_url = f"https://archive.org/download/{identifier}/{safe_filename}/"
        resp = requests.get(html_url, proxies=req_proxies, verify=ssl_verify, allow_redirects=True, timeout=20)
        if resp.status_code == 200 and '<html' in resp.text.lower():
            text = resp.text
            files_list = []
            seen = set()

            for m in re.finditer(r'<a\s+[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', text, re.IGNORECASE | re.DOTALL):
                href = m.group(1).strip()
                link_text = re.sub(r'<[^>]+>', '', m.group(2)).strip()

                if not link_text: continue

                if f"/download/{identifier}/" in href:
                    prefix = f"/download/{identifier}/"
                    idx = href.find(prefix)
                    if idx != -1:
                        path_part = href[idx + len(prefix):]
                        fname = link_text
                        if fname.startswith('//') or fname.startswith('http'):
                            fname = path_part
                            if fname.startswith(f"{filename}/"): fname = fname[len(filename)+1:]

                        fname = urllib.parse.unquote(fname)

                        if fname not in seen and not fname.startswith('//') and not fname.startswith('http'):
                            seen.add(fname)
                            files_list.append({"filename": fname, "size": 0, "is_dir": fname.endswith('/')})

            if files_list:
                logging.info(f"✅ 成功通过官方网页解析出 {len(files_list)} 个文件")
                return {"status": "success", "files": files_list}
    except Exception as e:
        logging.warning(f"官方 HTML 解析失败: {e}")

    if filename.lower().endswith('.zip'):
        url = f"https://archive.org/download/{identifier}/{safe_filename}"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Accept": "*/*",
            "Range": "bytes=-131072"
        }
        try:
            resp = requests.get(url, headers=headers, proxies=req_proxies, verify=ssl_verify, stream=True, timeout=30)
            if resp.status_code not in (200, 206):
                return {"status": "error", "msg": f"下载失败: HTTP {resp.status_code}"}

            data = b""
            if resp.status_code == 206:
                data = resp.content
            else:
                tail_data = b""
                for chunk in resp.iter_content(chunk_size=81920):
                    if chunk:
                        tail_data += chunk
                        if len(tail_data) > 262144:
                            tail_data = tail_data[-131072:]
                        if len(tail_data) == len(chunk) and len(chunk) > 100:
                            if b'<!DOCTYPE html' in chunk[:100].lower() or b'<html' in chunk[:100].lower():
                                return {"status": "error", "msg": "获取失败：代理节点返回了网页拦截，请更换节点"}
                data = tail_data

            if not data:
                return {"status": "error", "msg": "获取文件数据为空"}
            if b'<!DOCTYPE html' in data[:100].lower() or b'<html' in data[:100].lower():
                return {"status": "error", "msg": "下载失败：代理节点返回了网页拦截"}

            eocd_idx = data.rfind(b'PK\x05\x06')
            if eocd_idx == -1:
                return {"status": "error", "msg": "未找到ZIP中央目录 (代理返回异常或文件非ZIP)"}

            cd_size = struct.unpack('<I', data[eocd_idx+12:eocd_idx+16])[0]
            cd_offset = struct.unpack('<I', data[eocd_idx+16:eocd_idx+20])[0]

            if resp.status_code == 206:
                total_size = 0
                if 'Content-Range' in resp.headers:
                    total_size = int(resp.headers['Content-Range'].split('/')[-1])
                start_pos = max(0, total_size - len(data)) if total_size > 0 else 0
            else:
                total_size = len(data)
                start_pos = 0

            cd_data = b""
            if cd_offset >= start_pos and (cd_offset - start_pos + cd_size) <= len(data):
                cd_data = data[(cd_offset - start_pos) : (cd_offset - start_pos + cd_size)]
            else:
                if total_size == 0:
                    return {"status": "error", "msg": "代理不支持Range，且文件过大，无法获取ZIP目录"}

                headers['Range'] = f"bytes={cd_offset}-{cd_offset+cd_size-1}"
                resp_cd = requests.get(url, headers=headers, proxies=req_proxies, verify=ssl_verify, timeout=30)
                cd_data = resp_cd.content

            files_list = []
            idx = 0
            while idx < len(cd_data):
                if cd_data[idx:idx+4] != b'PK\x01\x02': break
                file_size = struct.unpack('<I', cd_data[idx+24:idx+28])[0]
                name_len = struct.unpack('<H', cd_data[idx+28:idx+30])[0]
                extra_len = struct.unpack('<H', cd_data[idx+30:idx+32])[0]
                comment_len = struct.unpack('<H', cd_data[idx+32:idx+34])[0]
                filename_bytes = cd_data[idx+46:idx+46+name_len]

                try:
                    filename_str = filename_bytes.decode('utf-8')
                except UnicodeDecodeError:
                    try:
                        filename_str = filename_bytes.decode('gbk')
                    except:
                        filename_str = filename_bytes.decode('utf-8', errors='replace')

                files_list.append({"filename": filename_str, "size": file_size, "is_dir": filename_str.endswith('/')})
                idx += 46 + name_len + extra_len + comment_len

            return {"status": "success", "files": files_list}
        except Exception as e:
            logging.error(f"解析ZIP失败: {e}")
            return {"status": "error", "msg": str(e)}

    return {"status": "error", "msg": "官方网页结构获取失败，且非 ZIP 格式无法本地解析，请下载后查看。"}


def fetch_user_stats(owner):
    try:
        url = f"https://archive.org/advancedsearch.php?q=uploader:{owner}&fl[]=identifier&rows=10000&output=json"
        resp = ia_get(url, timeout=20)
        if not resp or resp.status_code != 200: return {"status": "error", "msg": "拉取账户统计失败"}
        docs = resp.json().get('response', {}).get('docs', [])
        total_items = len(docs)
        total_size = 0
        total_files = 0
        with ThreadPoolExecutor(max_workers=5) as executor:
            def get_meta(ident):
                try:
                    r = ia_get(f"https://archive.org/metadata/{ident}", timeout=10)
                    if r and r.status_code == 200:
                        d = r.json()
                        files = d.get('files', [])
                        sz = sum(int(f.get('size', 0)) for f in files if f.get('size'))
                        return sz, len(files)
                except: pass
                return 0, 0
            futures = [executor.submit(get_meta, d['identifier']) for d in docs]
            for f in as_completed(futures):
                sz, count = f.result()
                total_size += sz
                total_files += count
        return {"status": "success", "total_items": total_items, "total_files": total_files, "total_size": total_size}
    except Exception as e:
        return {"status": "error", "msg": str(e)}


def upload_custom_thumb_py(identifier, thumb_path):
    if not ensure_ia_config(): return False, "配置文件生成失败"
    try:
        md = {"x-archive-ignore-preexisting-bucket": "1"}
        ia.upload(identifier, files={f"{identifier}.jpg": open(thumb_path, 'rb')}, metadata=md, verbose=False, verify=get_ssl_verify())
        return True, "同步官方成功"
    except Exception as e:
        return False, str(e)


def compute_magnet(identifier, owner=None):
    conn = get_db()
    if owner: row = conn.execute("SELECT magnet FROM items WHERE identifier=? AND owner=?", (identifier, owner)).fetchone()
    else: row = conn.execute("SELECT magnet FROM items WHERE identifier=?", (identifier,)).fetchone()
    conn.close()
    if row and row[0]: return row[0]
    now = time.time()
    with MAGNET_LOCK:
        t = MAGNET_FAIL_CACHE.get(identifier)
        if t and now - t < cfg_get('magnet_fail_ttl', 300): return ""
    _magnet_acquire()
    try:
        torrent_url = f"https://archive.org/download/{identifier}/{identifier}_archive.torrent"
        resp = ia_get(torrent_url, timeout=cfg_get('magnet_timeout', 30), stream=True)
        if not resp or resp.status_code != 200:
            with MAGNET_LOCK: MAGNET_FAIL_CACHE[identifier] = time.time()
            return ""
        data = bytearray()
        for chunk in resp.iter_content(8192):
            data.extend(chunk)
            if len(data) > 10 * 1024 * 1024: raise Exception("Torrent 种子文件异常过大，放弃解析")
        data = bytes(data)
        def decode_bencode(data):
            def _decode(data, idx):
                if data[idx:idx+1] == b'i':
                    end = data.index(b'e', idx); return int(data[idx+1:end]), end+1
                elif data[idx:idx+1] == b'l':
                    idx += 1; res = []
                    while data[idx:idx+1] != b'e':
                        val, idx = _decode(data, idx); res.append(val)
                    return res, idx+1
                elif data[idx:idx+1] == b'd':
                    idx += 1; res = {}
                    while data[idx:idx+1] != b'e':
                        k, idx = _decode(data, idx); v, idx = _decode(data, idx); res[k] = v
                    return res, idx+1
                elif data[idx:idx+1].isdigit():
                    colon = data.index(b':', idx); length = int(data[idx:colon])
                    return data[colon+1:colon+1+length], colon+1+length
                return None, idx
            return _decode(data, 0)[0]
        decoded = decode_bencode(data)
        info_dict = decoded.get(b'info', {})
        def encode_bencode(obj):
            if isinstance(obj, int): return b'i' + str(obj).encode() + b'e'
            elif isinstance(obj, bytes): return str(len(obj)).encode() + b':' + obj
            elif isinstance(obj, list): return b'l' + b''.join(encode_bencode(i) for i in obj) + b'e'
            elif isinstance(obj, dict):
                items = sorted(obj.items())
                return b'd' + b''.join(encode_bencode(k) + encode_bencode(v) for k, v in items) + b'e'
            return b''
        info_encoded = encode_bencode(info_dict)
        infohash = hashlib.sha1(info_encoded).hexdigest()
        magnet = f"magnet:?xt=urn:btih:{infohash}&dn={identifier}"
        with DB_LOCK:
            conn = get_db()
            if owner: conn.execute("UPDATE items SET magnet=? WHERE identifier=? AND owner=?", (magnet, identifier, owner))
            else: conn.execute("UPDATE items SET magnet=? WHERE identifier=?", (magnet, identifier))
            conn.commit(); conn.close()
        return magnet
    except Exception as e:
        logging.error(f"磁力计算失败: {e}")
        with MAGNET_LOCK: MAGNET_FAIL_CACHE[identifier] = time.time()
        return ""
    finally:
        _magnet_release()