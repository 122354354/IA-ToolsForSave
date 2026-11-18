# netdisk_routes_views_folder.py
# 文件夹增删改 / 条目移动 / 回收站操作 / 清空回收站

import os
from flask import request, redirect, url_for, session

from netdisk_blueprints import views_bp
from netdisk_core import DB_LOCK, get_db, cleanup_temp_dir

import netdisk_core as core


@views_bp.route('/edit_folder', methods=['POST'])
def edit_folder():
    folder_id = request.form.get('folder_id')
    new_name = request.form.get('new_name', '').strip()
    if folder_id and new_name:
        with DB_LOCK:
            conn = get_db()
            conn.execute("UPDATE folders SET name=? WHERE id=? AND owner=?", (new_name, folder_id, session.get('owner')))
            conn.commit()
            conn.close()
    return redirect(request.referrer or url_for('views.index'))


@views_bp.route('/add_folder', methods=['POST'])
def add_folder():
    owner = session.get('owner')
    parent_id = int(request.form.get('parent_id', 0))
    if request.form.get('folder_name'):
        with DB_LOCK:
            conn = get_db()
            if parent_id != 0:
                valid = conn.execute("SELECT id FROM folders WHERE id=? AND owner=?", (parent_id, owner)).fetchone()
                if not valid:
                    conn.close()
                    return redirect(request.referrer or url_for('views.index'))
            conn.execute("INSERT INTO folders (owner, name, parent_id) VALUES (?, ?, ?)", (owner, request.form.get('folder_name'), parent_id))
            conn.commit()
            conn.close()
    return redirect(request.referrer or url_for('views.index'))


@views_bp.route('/delete_folder', methods=['POST'])
def delete_folder():
    folder_id = request.form.get('folder_id')
    owner = session.get('owner')
    if folder_id:
        with DB_LOCK:
            conn = get_db()
            parent_row = conn.execute("SELECT parent_id FROM folders WHERE id=? AND owner=?", (folder_id, owner)).fetchone()
            if not parent_row:
                conn.close()
                return redirect(request.referrer or url_for('views.index'))
            parent_id = parent_row[0]
            conn.execute("UPDATE items SET folder_id=? WHERE folder_id=? AND owner=?", (parent_id, folder_id, owner))
            conn.execute("UPDATE folders SET parent_id=? WHERE parent_id=? AND owner=?", (parent_id, folder_id, owner))
            conn.execute("DELETE FROM folders WHERE id=? AND owner=?", (folder_id, owner))
            conn.commit()
            conn.close()
    return redirect(request.referrer or url_for('views.index'))


@views_bp.route('/move_item', methods=['POST'])
def move_item():
    target = request.form.get('target_folder_id')
    item_id = request.form.get('item_id')
    if target != "" and item_id:
        with DB_LOCK:
            conn = get_db()
            conn.execute("UPDATE items SET folder_id=? WHERE id=? AND owner=?", (target, item_id, session.get('owner')))
            conn.commit()
            conn.close()
    return redirect(request.referrer or url_for('views.index'))


@views_bp.route('/trash_item/<int:id>')
def trash_item(id):
    with DB_LOCK:
        conn = get_db()
        conn.execute("UPDATE items SET deleted_at=CURRENT_TIMESTAMP WHERE id=? AND owner=?", (id, session.get('owner')))
        conn.commit()
        conn.close()
    return redirect(request.referrer or url_for('views.index'))


@views_bp.route('/restore_item/<int:id>')
def restore_item(id):
    with DB_LOCK:
        conn = get_db()
        conn.execute("UPDATE items SET deleted_at=NULL WHERE id=? AND owner=?", (id, session.get('owner')))
        conn.commit()
        conn.close()
    return redirect(url_for('views.trash_view'))


@views_bp.route('/purge_item/<int:id>')
def purge_item(id):
    with DB_LOCK:
        conn = get_db()
        row = conn.execute("SELECT identifier, file_path FROM items WHERE id=? AND owner=?", (id, session.get('owner'))).fetchone()
        if row:
            identifier, file_path = row
            if file_path and os.path.abspath(file_path).startswith(os.path.abspath(core.TEMP_UPLOAD_DIR) + os.sep):
                cleanup_temp_dir(file_path)
            conn.execute("DELETE FROM file_cache WHERE identifier=?", (identifier,))
            thumb_path = os.path.join(core.THUMB_DIR, f"{identifier}.jpg")
            if os.path.exists(thumb_path):
                try: os.remove(thumb_path)
                except: pass
            conn.execute("DELETE FROM items WHERE id=? AND owner=?", (id, session.get('owner')))
        conn.commit()
        conn.close()
    return redirect(url_for('views.trash_view'))


@views_bp.route('/empty_trash', methods=['POST'])
def empty_trash():
    with DB_LOCK:
        conn = get_db()
        owner = session.get('owner')
        rows = conn.execute("SELECT identifier, file_path FROM items WHERE deleted_at IS NOT NULL AND owner=?", (owner,)).fetchall()
        for r in rows:
            identifier, file_path = r
            if file_path and os.path.abspath(file_path).startswith(os.path.abspath(core.TEMP_UPLOAD_DIR) + os.sep):
                cleanup_temp_dir(file_path)
            conn.execute("DELETE FROM file_cache WHERE identifier=?", (identifier,))
            thumb_path = os.path.join(core.THUMB_DIR, f"{identifier}.jpg")
            if os.path.exists(thumb_path):
                try: os.remove(thumb_path)
                except: pass
        conn.execute("DELETE FROM items WHERE deleted_at IS NOT NULL AND owner=?", (owner,))
        conn.commit()
        conn.close()
    return redirect(url_for('views.trash_view'))