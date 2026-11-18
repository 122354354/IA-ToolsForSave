# templates_style.py
# 全局样式 + 通用 JS（主题切换、escapeHtml、代理模式切换、自定义输入显隐、关闭网盘）

STYLE = r"""
<style>
    :root { --primary: #009688; --primary-dark: #00796b; --bg: #eef2f5; --card-bg: #ffffff; --text: #333333; --border: #dddddd; --danger: #f44336; --blue: #2196F3; --gray: #607D8B; --brown: #795548; --purple: #9C27B0; --orange: #FF9800; --green: #4CAF50; }
    [data-theme="dark"] { --bg: #121212; --card-bg: #1e1e1e; --text: #e0e0e0; --border: #333333; --primary-dark: #80cbc4; --gray: #90a4ae; }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: -apple-system, sans-serif; background: var(--bg); padding: 15px; color: var(--text); line-height: 1.6; overflow-x: hidden; transition: background 0.3s, color 0.3s; word-break: break-word; }
    
    .navbar { background: var(--primary); padding: 12px; border-radius: 10px; color: white; display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 15px; justify-content: space-between; align-items: center; box-shadow: 0 4px 12px rgba(0,150,136,0.2);}
    .nav-links { display: flex; gap: 6px; flex-wrap: wrap; align-items: center;}
    .navbar a, .navbar button { color: white; text-decoration: none; font-weight: bold; font-size: 13px; padding: 6px 10px; border-radius: 6px; background: rgba(255,255,255,0.15); transition: all 0.2s; border: none; cursor: pointer; display: inline-flex; justify-content: center; align-items: center; height: 34px; width: auto; margin: 0; }
    .navbar a:hover, .navbar button:hover { background: rgba(255,255,255,0.3); transform: translateY(-1px); }
    .navbar a.danger { background: var(--danger); }
    .navbar .mobile-more-btn { display: none; }
    @media (max-width: 768px) {
        .navbar .nav-links > a:not(.mobile-keep):not(.danger), .navbar .nav-links > button:not(.mobile-keep):not(.mobile-more-btn) { display: none; }
        .navbar .mobile-more-btn { display: inline-flex; }
    }
    
    .card { background: var(--card-bg); padding: 15px; border-radius: 12px; box-shadow: 0 4px 15px rgba(0,0,0,0.06); max-width: 900px; margin: 0 auto 20px auto; width: 100%; overflow: hidden;}
    h2, h3 { margin-bottom: 15px; color: var(--primary-dark); border-bottom: 1px solid var(--border); padding-bottom: 10px; }
    
    input, textarea, select { width: 100%; padding: 10px; margin: 5px 0; border: 1px solid var(--border); border-radius: 8px; font-size: 14px; outline: none; transition: 0.3s; background: var(--bg); color: var(--text); max-width: 100%; }
    input:focus, textarea:focus, select:focus { border-color: var(--primary); box-shadow: 0 0 0 3px rgba(0,150,136,0.15); }
    button, .btn { padding: 10px 15px; background: var(--primary); color: white; border: none; border-radius: 8px; cursor: pointer; font-size: 14px; font-weight: bold; width: 100%; margin-top: 10px; text-align: center; text-decoration: none; display: inline-block; transition: 0.2s; }
    button:hover, .btn:hover { filter: brightness(0.9); transform: translateY(-1px); }
    .btn-sm { padding: 6px 12px; font-size: 13px; width: auto; margin: 0; border-radius: 6px; white-space: nowrap; }
    .btn-blue { background: var(--blue); } .btn-green { background: var(--green); } .btn-red { background: var(--danger); }
    .btn-gray { background: var(--gray); } .btn-brown { background: var(--brown); } .btn-purple { background: var(--purple); } .btn-orange { background: var(--orange); }
    
    .folder-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(130px, 1fr)); gap: 25px 15px; margin-top: 20px; padding: 0 5px; }
    .folder-card { background: #e0f2f1; padding: 15px 10px; border-radius: 10px; text-align: center; color: var(--primary-dark); font-weight: bold; display: flex; flex-direction: column; justify-content: center; align-items: center; position: relative; border: 1px solid #b2dfdb; transition: transform 0.2s; min-height: 80px; cursor: pointer; }
    [data-theme="dark"] .folder-card { background: #263238; border-color: #37474f; color: #80cbc4; }
    .folder-card a { text-decoration: none; color: var(--primary-dark); word-break: break-all; margin-top: 5px; display: block; width: 100%; height: 100%; font-size: 14px;}
    .folder-card:hover { transform: translateY(-2px); box-shadow: 0 4px 10px rgba(0,150,136,0.15); }
    
    .folder-checkbox { position: absolute; top: 10px; left: 10px; width: 22px; height: 22px; cursor: pointer; z-index: 10; margin: 0; }
    .folder-btn-group { position: absolute; top: -10px; right: -10px; display: flex; gap: 4px; z-index: 10; }
    .folder-act-btn { background: #fff; border: 2px solid #ddd; border-radius: 50%; width: 28px; height: 28px; display: flex; justify-content: center; align-items: center; font-size: 14px; font-weight: bold; cursor: pointer; box-shadow: 0 2px 5px rgba(0,0,0,0.15); transition: 0.2s; line-height: 1; padding: 0; margin: 0;}
    .del-folder-btn { color: #d32f2f; } .del-folder-btn:hover { background: var(--danger); color: white; transform: scale(1.1); }
    .edit-folder-btn { color: #f57f17; } .edit-folder-btn:hover { background: var(--orange); color: white; transform: scale(1.1); }
    .move-folder-btn { color: var(--blue); } .move-folder-btn:hover { background: var(--blue); color: white; transform: scale(1.1); }
    
    .item-row { display: flex; flex-wrap: wrap; padding: 15px; border: 1px solid var(--border); background: var(--bg); margin-bottom: 12px; border-radius: 10px; gap: 12px; align-items: flex-start; transition: 0.2s; }
    .item-row:hover { background: var(--card-bg); box-shadow: 0 2px 8px rgba(0,0,0,0.05); }
    .item-row.list-mode { flex-wrap: wrap; align-items: center; padding: 10px; gap: 8px; }
    .item-row.list-mode .item-main { min-width: 150px; flex: 1; }
    .item-row .batch-checkbox { flex-shrink: 0; margin-top: 20px; width: 20px; height: 20px; cursor: pointer; }
    .item-row.list-mode .batch-checkbox { margin-top: 0; }
    .item-main { flex: 1; min-width: 200px; display: flex; flex-direction: column; gap: 6px; }
    .item-header { display: flex; justify-content: space-between; align-items: flex-start; flex-wrap: wrap; gap: 10px; }
    .item-info strong { font-size: 16px; color: var(--text); word-break: break-all; display: block;}
    .item-info small { color: var(--gray); font-size: 12px; word-break: break-all; display: block;}
    
    .thumb-wrapper { width: 60px; height: 60px; flex-shrink: 0; position: relative; }
    .thumb-img { width: 100%; height: 100%; object-fit: cover; border-radius: 8px; border: 1px solid var(--border); background: rgba(0,0,0,0.05); }
    .thumb-placeholder { width: 100%; height: 100%; border-radius: 8px; border: 1px dashed var(--border); background: rgba(0,0,0,0.05); display: none; flex-direction: column; align-items: center; justify-content: center; color: var(--gray); font-size: 10px; cursor: pointer; line-height: 1.2; text-align: center; }
    .thumb-placeholder span { font-size: 16px; margin-bottom: 2px; }

    .item-actions { flex-basis: 100%; display: flex; gap: 6px; flex-wrap: wrap; padding-top: 10px; border-top: 1px dashed var(--border); }
    .item-row.list-mode .item-actions { flex-basis: 100%; padding-top: 8px; border-top: 1px dashed var(--border); justify-content: flex-start; }
    .item-actions .btn { margin: 0; }
    
    .folder-actions-bar { display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 10px; margin-bottom: 15px; }
    .folder-actions-bar-left, .folder-actions-bar-right { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
    
    .move-select { padding: 6px; border-radius: 6px; font-size: 13px; border: 1px solid var(--border); background: var(--bg); color: var(--text); width: auto; max-width: 120px; margin: 0; cursor: pointer; }
    .batch-action-bar { display: none; background: #fff3e0; border: 1px solid var(--orange); padding: 10px; border-radius: 8px; margin-bottom: 15px; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 10px;}
    [data-theme="dark"] .batch-action-bar { background: #3e2723; border-color: #ffb74d; }
    
    fieldset { border: 1px solid var(--border); border-radius: 8px; padding: 15px; margin-bottom: 20px; background: var(--card-bg); width: 100%; overflow: hidden; min-width: 0; }
    legend { font-weight: bold; color: var(--primary); font-size: 15px; padding: 0 10px; }
    .tip { font-size: 12px; color: var(--gray); margin-bottom: 12px; display: block; background: rgba(0,150,136,0.1); padding: 8px; border-radius: 6px; border-left: 4px solid var(--primary); word-break: break-word; overflow-wrap: anywhere;}
    code { word-break: break-all; white-space: pre-wrap; background: rgba(0,0,0,0.1); padding: 2px 6px; border-radius: 4px; display: inline-block; max-width: 100%; }
    .warning-box { background: #ffebee; border: 2px solid var(--danger); color: #b71c1c; padding: 15px; border-radius: 8px; margin-bottom: 20px; font-weight: bold; text-align: center; }
    
    .progress-container { width: 100%; background: rgba(0,0,0,0.1); border-radius: 6px; overflow: hidden; height: 24px; margin-top: 10px; position: relative; box-shadow: inset 0 1px 3px rgba(0,0,0,0.2); }
    .progress-bar-fill { height: 100%; background: linear-gradient(90deg, #009688 0%, #4CAF50 100%); width: 0%; transition: width 0.3s ease; }
    .progress-text { position: absolute; width: 100%; text-align: center; font-size: 13px; font-weight: bold; color: var(--text); top: 50%; transform: translateY(-50%); text-shadow: 0 0 3px var(--card-bg); }
    .speed-text { font-size: 12px; color: var(--primary-dark); font-weight: bold; text-align: right; margin-top: 4px; word-break: break-all; white-space: normal; line-height: 1.4; }
    .raw-line { font-family: Consolas, monospace; font-size: 11px; color: var(--gray); background: rgba(0,0,0,0.05); padding: 6px 8px; border-radius: 4px; margin-top: 6px; white-space: pre-wrap; max-height: 70px; overflow: auto; border-left: 3px solid var(--gray); }
    
    .status-badge { display: inline-block; padding: 2px 8px; border-radius: 12px; font-size: 12px; font-weight: bold; color: white; background: var(--blue); margin-left: 10px; }
    .file-stats { font-size: 12px; color: var(--primary-dark); background: rgba(0,150,136,0.1); padding: 4px 10px; border-radius: 12px; display: inline-block; font-weight: bold; margin-left: 8px; }
    .spinner { display: inline-block; width: 16px; height: 16px; border: 2px solid rgba(0,0,0,0.1); border-left-color: var(--blue); border-radius: 50%; animation: spin 1s linear infinite; vertical-align: middle; margin-right: 5px; }
    @keyframes spin { 100% { transform: rotate(360deg); } }
    
    .modal-overlay { display: none; position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.6); z-index: 9999; justify-content: center; align-items: center; backdrop-filter: blur(3px); overflow-y: auto; padding: 15px; }
    .modal-content { background: var(--card-bg); color: var(--text); width: 100%; max-width: 600px; border-radius: 12px; padding: 15px; box-shadow: 0 10px 30px rgba(0,0,0,0.2); max-height: 90vh; display: flex; flex-direction: column; }
    .modal-title { font-size: 18px; font-weight: bold; color: var(--primary-dark); margin-bottom: 10px; display: flex; justify-content: space-between; align-items: center; flex-shrink: 0;}
    .modal-body { overflow-y: auto; flex: 1; padding-right: 5px; }
    .modal-close { cursor: pointer; font-size: 24px; color: var(--gray); line-height: 1; }
    
    details { border: 1px solid var(--border); border-radius: 8px; padding: 10px; margin-bottom: 15px; background: var(--card-bg); }
    summary { font-weight: bold; color: var(--primary); font-size: 14px; cursor: pointer; outline: none; }
    .adv-container { margin-top: 10px; padding-top: 10px; border-top: 1px dashed var(--border); }
    .adv-label { display: flex; align-items: flex-start; gap: 10px; margin-bottom: 10px; cursor: pointer; font-weight: bold; }
    .adv-label input[type="checkbox"] { width: 20px; height: 20px; flex-shrink: 0; margin-top: 3px;}
    
    .pwd-wrapper { position: relative; }
    .pwd-toggle { position: absolute; right: 10px; top: 50%; transform: translateY(-50%); cursor: pointer; user-select: none; font-size: 18px; color:var(--gray); }
    
    .account-item { display: flex; justify-content: space-between; align-items: center; padding: 10px; border: 1px solid var(--border); border-radius: 8px; margin-bottom: 10px; background: var(--bg); transition: 0.2s; flex-wrap: wrap; gap:10px;}
    .account-item:hover { border-color: var(--primary); background: var(--card-bg); }
    .account-name { font-weight: bold; color: var(--text); font-size: 14px; }
    .email-container { display: flex; align-items: center; gap: 5px; font-size: 12px; color: var(--gray); }
    .email-text { display: none; } .email-mask { display: inline; } .eye-btn { background: none; border: none; cursor: pointer; font-size: 12px; color: var(--blue); padding: 0; width: auto; margin: 0; }
    .btn-enter { background: var(--green); color: white; padding: 6px 12px; border-radius: 6px; text-decoration: none; font-size: 13px; font-weight: bold; cursor: pointer; border: none; }
    .btn-edit-name { background: none; border: none; color: var(--orange); font-size: 16px; cursor: pointer; margin-right: 8px; padding: 0 5px; }
    
    .conc-badge { display: inline-block; background: rgba(0,150,136,0.1); color: var(--primary-dark); font-size: 12px; font-weight: bold; padding: 4px 10px; border-radius: 12px; margin-left: 10px; }
    .bottom-nav { display: none; }
    
    .auto-switch-row { display: flex; flex-direction: column; padding: 10px 0; border-bottom: 1px dashed var(--border); gap: 8px; }
    .auto-switch-row:last-child { border-bottom: none; }
    .auto-switch-row label.auto-label { font-weight: bold; margin: 0; padding: 0; font-size: 14px; color: var(--text); }
    .auto-switch-row .switch-container { display: flex; justify-content: flex-end; }
    .switch { position: relative; display: inline-block; width: 48px; height: 26px; flex-shrink: 0; margin: 0; }
    .switch input { opacity: 0; width: 0; height: 0; }
    .slider { position: absolute; cursor: pointer; top: 0; left: 0; right: 0; bottom: 0; background-color: #ccc; transition: .3s; border-radius: 26px; }
    .slider:before { position: absolute; content: ""; height: 20px; width: 20px; left: 3px; bottom: 3px; background-color: white; transition: .3s; border-radius: 50%; }
    input:checked + .slider { background-color: var(--primary); }
    input:checked + .slider:before { transform: translateX(22px); }
    
    .proxy-item { display: flex; justify-content: space-between; align-items: center; padding: 10px; border: 1px solid var(--border); border-radius: 8px; margin-bottom: 8px; background: var(--bg); flex-wrap: wrap; gap: 10px; }
    .proxy-info { flex: 1; min-width: 150px; }
    .proxy-url { font-size: 12px; color: var(--gray); word-break: break-all; }
    .proxy-status { font-size: 12px; font-weight: bold; padding: 2px 6px; border-radius: 4px; margin-left: 8px; }
    .status-good { background: #e8f5e9; color: #2e7d32; }
    .status-bad { background: #ffebee; color: #c62828; }
    .status-active { background: var(--primary); color: white; }
    
    .top-links { display: flex; justify-content: center; gap: 15px; margin-bottom: 15px; }
    .top-links a { color: var(--primary); text-decoration: none; font-size: 13px; font-weight: bold; background: rgba(0,150,136,0.1); padding: 5px 12px; border-radius: 15px; }
    
    @media (max-width: 768px) {
        body { padding-bottom: 70px; padding-left: 10px; padding-right: 10px; }
        .bottom-nav { display: flex; position: fixed; bottom: 0; left: 0; right: 0; height: 60px; background: var(--card-bg); border-top: 1px solid var(--border); box-shadow: 0 -2px 10px rgba(0,0,0,0.05); z-index: 9998; justify-content: space-around; align-items: center; }
        .bottom-nav a { display: flex; flex-direction: column; align-items: center; justify-content: center; text-decoration: none; color: var(--gray); font-size: 11px; width: 20%; height: 100%; transition: 0.2s; }
        .bottom-nav a.active { color: var(--primary); }
        .bottom-nav a span { font-size: 20px; margin-bottom: 2px; }
    }
    mark { background-color: #ffeb3b; color: #000; padding: 0 2px; border-radius: 2px; }
</style>
<script>
    function toggleTheme() { let isDark = document.documentElement.getAttribute('data-theme') === 'dark'; if (isDark) { document.documentElement.removeAttribute('data-theme'); localStorage.setItem('theme', 'light'); } else { document.documentElement.setAttribute('data-theme', 'dark'); localStorage.setItem('theme', 'dark'); } }
    if (localStorage.getItem('theme') === 'dark') { document.documentElement.setAttribute('data-theme', 'dark'); }
    function escapeHtml(str) {
        if(!str) return '';
        return String(str).replace(/[&<>"']/g, function(m){ return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]; });
    }
    function toggleSni() {
        let proto = document.querySelector('input[name="proto"]:checked').value;
        document.getElementById('sni-section').style.display = proto === 'M' ? 'block' : 'none';
        document.getElementById('port-select').value = proto === 'M' ? '443' : '2408';
        document.getElementById('ip-select').value = proto === 'M' ? '162.159.193.10' : 'engage.cloudflareclient.com';
    }
    function toggleCustomInput(selectId, inputId) {
        let sel = document.getElementById(selectId);
        let inp = document.getElementById(inputId);
        if (sel.value === 'custom') { inp.style.display = 'block'; }
        else { inp.style.display = 'none'; inp.value = ''; }
    }

    // 【新增】关闭网盘功能
    function shutdownApp() {
        if (!confirm('⚠️ 确定要关闭网盘吗？\n\n网盘的后台进程将被终止，手机防休眠锁将被释放。\n关闭后你需要重新在终端运行 python netdisk.py 才能再次打开。')) return;
        
        let loading = document.createElement('div');
        loading.style.cssText = 'position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.8);z-index:99999;display:flex;justify-content:center;align-items:center;color:white;font-size:16px;font-weight:bold;flex-direction:column;';
        loading.innerHTML = '<div class="spinner" style="width:40px;height:40px;border-width:4px;margin-bottom:15px;"></div><div>正在安全关闭网盘...</div>';
        document.body.appendChild(loading);
        
        fetch('/api/shutdown', { method: 'POST' }).then(() => {
            setTimeout(() => {
                document.body.innerHTML = '<div style="display:flex;justify-content:center;align-items:center;height:100vh;background:#121212;color:#e0e0e0;font-family:sans-serif;text-align:center;padding:20px;flex-direction:column;"><div style="font-size:30px;margin-bottom:15px;">🛑</div><h2 style="color:#f44336;border:none;">网盘已关闭</h2><p style="font-size:14px;color:#90a4ae;margin-top:10px;">后台进程已被彻底终止，防休眠锁已释放。<br><br>你可以直接关闭此网页。<br>如需再次使用，请在 Termux 重新运行 <code>python netdisk.py</code>。</p></div>';
            }, 1500);
        }).catch(() => {
            alert('关闭请求失败，请直接去 Termux 终端按 Ctrl+C 终止。');
            document.body.removeChild(loading);
        });
    }
</script>
"""