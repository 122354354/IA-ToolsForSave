# netdisk_routes_views_auth.py
# 本地密码鉴权 / 账号登录 / 登出

from flask import request, render_template_string, redirect, url_for, session

from netdisk_blueprints import views_bp
from netdisk_core import get_config, save_config
from templates_module import STYLE, get_navbar
from pages_module import LOGIN_HTML


@views_bp.route('/local_auth', methods=['GET', 'POST'])
def local_auth():
    cfg = get_config()
    if not cfg.get('local_pwd'):
        session['local_authed'] = True
        return redirect(url_for('views.login'))
    if request.method == 'POST':
        if request.form.get('password') == cfg.get('local_pwd'):
            session['local_authed'] = True
            return redirect(url_for('views.login'))
        else:
            return render_template_string("<body style='background:#eef2f5; color:#333; text-align:center; padding:50px;'><h2>❌ 密码错误</h2><a href='/local_auth'>返回重试</a></body>")
    html = """<!DOCTYPE html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">{{ style|safe }}</head><body style="display:flex; justify-content:center; align-items:center; min-height:100vh; margin:0;"><div class="card" style="max-width:350px; padding:20px; text-align:center;"><h2>🔒 本地面板已锁定</h2><p style="font-size:13px; color:var(--gray); margin-bottom:15px;">请输入访问密码</p><form method="POST"><input type="password" name="password" placeholder="输入 PIN 码或密码" required autofocus><button type="submit" style="margin-top:10px;">解锁</button></form></div></body></html>"""
    return render_template_string(html, style=STYLE)


@views_bp.route('/login', methods=['GET', 'POST'])
def login():
    cfg = get_config()
    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'select':
            selected_email = request.form.get('saved_email')
            if selected_email in cfg.get("accounts", {}):
                cfg["current"] = selected_email
                save_config(cfg)
                session['logged_in'] = True
                session['owner'] = selected_email
                return redirect(url_for('views.index'))
            return "❌ 账号不存在"
        email, password = request.form.get('email', '').strip(), request.form.get('password', '').strip()
        s3_access, s3_secret = request.form.get('s3_access', '').strip(), request.form.get('s3_secret', '').strip()
        method = request.form.get('method', 's3')
        if not email and (not s3_access or not s3_secret):
            return "❌ 必须提供凭证"
        if email and not password and not (s3_access and s3_secret):
            return "❌ 邮箱登录需要提供密码（或改用 S3 密钥）"
        owner_email, ia_name = email, email.split('@')[0] if email else 'account'
        if s3_access and s3_secret:
            try:
                import internetarchive as ia
                info = ia.get_user_info(s3_access, s3_secret)
                owner_email = info.get('email') or info.get('username') or ('s3_' + s3_access[:8])
                ia_name = info.get('screenname') or info.get('username') or owner_email
            except Exception as e:
                return f"❌ S3 验证失败: {e}"
        elif email and password:
            try:
                import internetarchive as ia
                ia.configure(email, password)
                s3_cfg = ia.get_s3_keys()
                info = ia.get_user_info(s3_cfg[0], s3_cfg[1])
                owner_email = info.get('email') or email
                ia_name = info.get('screenname') or info.get('username') or email
            except Exception as e:
                return f"❌ 邮箱/密码验证失败: {e}"
        account_data = {"method": method, "ia_name": ia_name}
        if email: account_data["email"] = email
        if password: account_data["password"] = password
        if s3_access: account_data["s3_access"] = s3_access
        if s3_secret: account_data["s3_secret"] = s3_secret
        cfg["accounts"][owner_email] = account_data
        cfg["current"] = owner_email
        save_config(cfg)
        session['logged_in'] = True
        session['owner'] = owner_email
        return redirect(url_for('views.index'))
    return render_template_string(LOGIN_HTML, style=STYLE, saved_accounts=cfg.get("accounts", {}), n=get_navbar(is_login=True))


@views_bp.route('/logout')
def logout():
    session.clear()
    cfg = get_config()
    cfg["current"] = ""
    save_config(cfg)
    return redirect(url_for('views.login'))