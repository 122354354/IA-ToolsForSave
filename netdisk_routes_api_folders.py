# netdisk_routes_api_folders.py
# 文件夹/条目批量移动、回收等 API

from flask import request, jsonify, session

from netdisk_blueprints import api_bp
from netdisk_core import DB_LOCK, get_db


@api_bp.route('/api/batch_action', methods=['POST'])
def api_batch_action():
    data = request.get_json()
    action, ids = data.get('action'), data.get('ids', [])
    if not ids:
        return jsonify({'status': 'error', 'msg': '未选择任何条目'})
    with DB_LOCK:
        conn = get_db()
        try:
            if action == 'move':
                target = data.get('target')
                for i in ids:
                    conn.execute("UPDATE items SET folder_id=? WHERE id=? AND owner=?", (target, i, session.get('owner')))
            elif action == 'trash':
                for i in ids:
                    conn.execute("UPDATE items SET deleted_at=CURRENT_TIMESTAMP WHERE id=? AND owner=?", (i, session.get('owner')))
            conn.commit()
            return jsonify({'status': 'success'})
        except Exception as e:
            return jsonify({'status': 'error', 'msg': str(e)})
        finally:
            conn.close()


@api_bp.route('/api/batch_folder_action', methods=['POST'])
def api_batch_folder_action():
    data = request.get_json()
    action, ids = data.get('action'), data.get('ids', [])
    if not ids:
        return jsonify({'status': 'error', 'msg': '未选择任何文件夹'})
    owner = session.get('owner')
    with DB_LOCK:
        conn = get_db()
        try:
            for fid in ids:
                valid = conn.execute("SELECT id FROM folders WHERE id=? AND owner=?", (fid, owner)).fetchone()
                if not valid: continue
                if action == 'move':
                    target = data.get('target')
                    if str(target) == str(fid): continue
                    is_cycle = False
                    c = target
                    visited = set()
                    while c != 0:
                        if str(c) == str(fid):
                            is_cycle = True
                            break
                        if c in visited: break
                        visited.add(c)
                        row = conn.execute("SELECT parent_id FROM folders WHERE id=?", (c,)).fetchone()
                        if not row: break
                        c = row[0]
                    if is_cycle:
                        continue
                    conn.execute("UPDATE folders SET parent_id=? WHERE id=? AND owner=?", (target, fid, owner))
                elif action == 'delete':
                    parent_row = conn.execute("SELECT parent_id FROM folders WHERE id=? AND owner=?", (fid, owner)).fetchone()
                    parent_id = parent_row[0] if parent_row else 0
                    conn.execute("UPDATE items SET folder_id=? WHERE folder_id=? AND owner=?", (parent_id, fid, owner))
                    conn.execute("UPDATE folders SET parent_id=? WHERE parent_id=? AND owner=?", (parent_id, fid, owner))
                    conn.execute("DELETE FROM folders WHERE id=? AND owner=?", (fid, owner))
            conn.commit()
            return jsonify({'status': 'success'})
        except Exception as e:
            return jsonify({'status': 'error', 'msg': str(e)})
        finally:
            conn.close()


@api_bp.route('/api/move_item_ajax', methods=['POST'])
def api_move_item_ajax():
    data = request.get_json()
    with DB_LOCK:
        conn = get_db()
        conn.execute("UPDATE items SET folder_id=? WHERE id=? AND owner=?", (data.get('target_id'), data.get('item_id'), session.get('owner')))
        conn.commit()
        conn.close()
    return jsonify({'status': 'success'})


@api_bp.route('/api/move_folder', methods=['POST'])
def api_move_folder():
    data = request.get_json()
    fid, tid = data.get('folder_id'), data.get('target_id')
    if fid == tid:
        return jsonify({'status': 'error', 'msg': '不能移动到自己内部'})
    with DB_LOCK:
        conn = get_db()
        valid = conn.execute("SELECT id FROM folders WHERE id=? AND owner=?", (fid, session.get('owner'))).fetchone()
        if not valid:
            conn.close()
            return jsonify({'status': 'error', 'msg': '文件夹不存在'})
        c = tid
        while c != 0:
            row = conn.execute("SELECT parent_id FROM folders WHERE id=?", (c,)).fetchone()
            if not row: break
            if row[0] == fid:
                conn.close()
                return jsonify({'status': 'error', 'msg': '禁止死循环'})
            c = row[0]
        conn.execute("UPDATE folders SET parent_id=? WHERE id=? AND owner=?", (tid, fid, session.get('owner')))
        conn.commit()
        conn.close()
    return jsonify({'status': 'success'})