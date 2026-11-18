# pages_auth.py
# 登录页 HTML

LOGIN_HTML = r"""
<!DOCTYPE html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">{{ style|safe }}
<style>
    body { display:flex; flex-direction:column; align-items:center; justify-content:center; min-height:100vh; padding:10px; margin:0; } 
    .login-container { width:100%; max-width:380px; } 
    .tab-container { display:flex; margin-bottom:10px; border-bottom:1px solid var(--border); } 
    .tab-btn { flex:1; padding:8px; text-align:center; cursor:pointer; font-weight:bold; font-size:14px; color:var(--gray); border-bottom:2px solid transparent; transition:0.2s; } 
    .tab-btn.active { color:var(--primary); border-bottom-color:var(--primary); } 
    .tab-content { display:none; } 
    .tab-content.active { display:block; }
    .radio-group { display: flex; align-items: center; justify-content: center; gap: 20px; padding: 5px; margin-top: 5px; }
    .radio-group label { display: flex; align-items: center; gap: 6px; white-space: nowrap; margin: 0; padding: 0; font-weight: bold; font-size: 13px; }
    .radio-group input[type="radio"] { width: 16px; height: 16px; margin: 0; }
    .card { padding: 15px; }
    .loading-overlay { display: none; position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.7); z-index: 9999; justify-content: center; align-items: center; flex-direction: column; color: white; }
    .spinner { width: 40px; height: 40px; border: 4px solid rgba(255,255,255,0.3); border-top-color: #fff; border-radius: 50%; animation: spin 1s linear infinite; margin-bottom: 15px; }
    @keyframes spin { 100% { transform: rotate(360deg); } }
</style></head><body>

<!-- 安装库 Loading 弹窗 -->
<div class="loading-overlay" id="installLoading">
    <div class="spinner"></div>
    <div style="font-size:16px; font-weight:bold; margin-bottom:5px;">正在后台安装依赖库...</div>
    <div style="font-size:12px; color:#ccc;" id="installStatus">请稍候，这可能需要 10~30 秒</div>
</div>

<div class="login-container"><div class="card">
    <!-- 修改了顶部链接，改为触发弹窗说明 -->
    <div class="top-links">
        <a href="javascript:void(0)" onclick="showHelpModal('signup')">🔗 官方注册</a>
        <a href="javascript:void(0)" onclick="showHelpModal('s3')">🔑 获取 S3</a>
    </div>
    <h2 style="text-align:center; border:none; font-size:20px; margin-bottom: 2px;">IA 网盘 (V135)</h2><p style="text-align:center; font-size:12px; color:var(--gray); margin-bottom:10px;">全功能安全旗舰版</p>
    
    <div style="background:rgba(244,67,54,0.1); border:1px solid var(--danger); border-radius:6px; padding:8px; margin-bottom:15px; font-size:12px; color:var(--danger); text-align:center;">
        💡 强烈建议使用 <b>S3 密钥</b> 登录，邮箱密码极易被官方风控拦截，且无法使用断点续传。
    </div>

    <div class="tab-container"><div class="tab-btn active" onclick="switchTab('s3')">🔑 S3 密钥</div><div class="tab-btn" onclick="switchTab('email')">📧 邮箱密码</div></div>
    <form method="POST"><input type="hidden" name="action" value="new">
        <div id="tab-s3" class="tab-content active">
            <label>Access Key:</label><div class="pwd-wrapper"><input type="password" name="s3_access" id="s3_access"><span class="pwd-toggle" onclick="togglePwd('s3_access')">👁️</span></div>
            <label>Secret Key:</label><div class="pwd-wrapper"><input type="password" name="s3_secret" id="s3_secret"><span class="pwd-toggle" onclick="togglePwd('s3_secret')">👁️</span></div>
            
            <!-- 懒人一键提取 S3 -->
            <div style="background:rgba(0,150,136,0.08); border:1px dashed var(--primary); border-radius:8px; padding:10px; margin-top:15px;">
                <div style="font-size:12px; font-weight:bold; color:var(--primary); margin-bottom:8px; display:flex; align-items:center; gap:5px;">
                    ✨ 懒人模式：直接提取 (无需去官网)
                </div>
                <div style="display:flex; flex-direction:column; gap:5px;">
                    <input type="email" id="fetch_s3_email" placeholder="输入 IA 注册邮箱" style="margin:0; padding:8px; font-size:13px;">
                    <input type="password" id="fetch_s3_pwd" placeholder="输入 IA 登录密码" style="margin:0; padding:8px; font-size:13px;">
                    <button type="button" onclick="fetchS3(this)" class="btn btn-sm btn-blue" style="margin:0; padding:8px;">🚀 一键提取并自动填入</button>
                </div>
            </div>
        </div>
        
        <div id="tab-email" class="tab-content"><label>邮箱:</label><input type="email" name="email"><label>密码:</label><div class="pwd-wrapper"><input type="password" name="password" id="email_pwd"><span class="pwd-toggle" onclick="togglePwd('email_pwd')">👁️</span></div></div>
        
        <fieldset style="padding:5px; margin-top:15px; margin-bottom:10px;"><legend style="font-size:13px;">首选通道</legend><div class="radio-group"><label><input type="radio" name="method" value="s3" checked> S3 密钥</label><label><input type="radio" name="method" value="email"> 邮箱密码</label></div></fieldset>
        <button type="submit" style="border-radius:20px; padding:10px;">保存并进入系统</button>
    </form>
    
    <div style="display:flex; gap:10px; margin-top:10px;">
        <button onclick="showProxyModal()" class="btn btn-purple" style="margin:0; border-radius:20px; padding:10px; flex:1;">🌐 代理配置</button>
        <button onclick="installDeps(this)" class="btn btn-gray" style="margin:0; border-radius:20px; padding:10px; flex:1; background:#607D8B;">🔧 安装依赖</button>
    </div>

    {% if saved_accounts %}<div style="text-align:center; margin-top:15px; font-size:12px; color:var(--gray); cursor:pointer;" onclick="document.getElementById('accountModal').style.display='flex'">👥 切换已保存账号</div>{% endif %}
</div></div>

{% if saved_accounts %}
<div class="modal-overlay" id="accountModal"><div class="modal-content"><div class="modal-title"><span>👥 已保存账号</span><span class="modal-close" onclick="document.getElementById('accountModal').style.display='none'">&times;</span></div><div class="account-list">
    {% for email, acc in saved_accounts.items() %}
    <div class="account-item"><div><div class="account-name">{{ acc.custom_name or acc.ia_name or email.split('@')[0] }}</div><div class="email-container"><span class="email-text" id="email_{{ loop.index }}">{{ email }}</span><span class="email-mask" id="mask_{{ loop.index }}">************</span><button class="eye-btn" onclick="toggleEmail({{ loop.index }})">👁️ 显示</button></div></div>
        <div style="display:flex; align-items:center;"><button class="btn-edit-name" data-email="{{ email }}" data-name="{{ acc.custom_name or acc.ia_name or email.split('@')[0] }}" onclick="editNameFrom(this)">✏️</button><form method="POST" style="margin:0;"><input type="hidden" name="action" value="select"><input type="hidden" name="saved_email" value="{{ email }}"><button type="submit" class="btn-enter">进入</button></form></div>
    </div>{% endfor %}
</div></div></div>{% endif %}

<!-- 新手引导弹窗 -->
<div class="modal-overlay" id="helpModal">
    <div class="modal-content" style="max-width:420px; text-align:center; padding:20px; max-height: 85vh; overflow-y: auto;">
        <div class="modal-title" style="justify-content:center; border-bottom:none;">
            <span id="helpTitle">🔗 前往官网</span>
        </div>
        <div id="helpContentSignup" style="display:none; font-size:14px; color:var(--text); text-align:left; line-height:1.6; margin-bottom:20px;">
            <p style="margin-bottom:10px; color:var(--danger); font-weight:bold;">⚠️ 官方注册页有极强的人机验证，网盘后端无法代劳。</p>
            <p style="margin-bottom:15px;">由于域名被运营商拦截，你需要先让设备全局科学上网，才能在浏览器里打开注册页面。</p>
            
            <p style="margin-bottom:10px; font-weight:bold; color:var(--primary);">🛠️ 解决步骤：</p>
            <ol style="padding-left:20px; margin-bottom:15px;">
                <li>点击下方下载 <b>LxBox</b> 或 <b>Oblivion</b>。</li>
                <li>在软件里点一键连接，开启全局代理。</li>
                <li>再点下方「前往官网注册」即可。</li>
            </ol>
            
            <div style="background:rgba(33,150,243,0.1); border:1px solid var(--blue); padding:10px; border-radius:6px; font-size:13px; margin-bottom:10px;">
                <div style="display:flex; flex-direction:column; gap:8px;">
                    <div style="text-align:left; line-height:1.8;">
                        <span style="font-size:12px; font-weight:bold; color:var(--primary);">📱 安卓手机 (LxBox):</span><br>
                        <a href="https://gh-proxy.org/https://github.com/Leadaxe/LxBox/releases/download/v2.25.17/LxBox-v2.25.17-arm64-v8a.apk" target="_blank" class="btn btn-sm btn-green" style="padding:4px 10px; font-size:12px; margin:2px 0;">📥 下载 LxBox</a>
                    </div>
                    <div style="text-align:left; line-height:1.8; border-top:1px dashed var(--border); padding-top:8px;">
                        <span style="font-size:12px; font-weight:bold; color:var(--blue);">💻 电脑端 (Oblivion Win):</span><br>
                        <a href="https://gh-proxy.org/https://github.com/bepass-org/oblivion-desktop/releases/download/v3.11.0/oblivion-desktop-win-x64.exe" target="_blank" class="btn btn-sm btn-green" style="padding:4px 10px; font-size:12px; margin:2px 0;">📥 下载 PC 版</a>
                    </div>
                </div>
            </div>
        </div>

        <div id="helpContentS3" style="display:none; font-size:14px; color:var(--text); text-align:left; line-height:1.6; margin-bottom:20px;">
            <p style="margin-bottom:10px; color:var(--primary); font-weight:bold;">💡 获取 S3 密钥，不需要去官网！</p>
            <p style="margin-bottom:15px;">如果你已经注册了 IA 账号，只需关闭此弹窗，在登录页的 <b>“S3 密钥”</b> 标签下方找到 <b>“✨ 懒人模式”</b>，输入你的邮箱和密码，网盘会自动帮你提取并填入。</p>
            <p style="margin-bottom:15px; color:var(--danger);">注意：使用此功能前，请确保你已经在「🌐 代理配置」里生成了一个可用的节点，否则无法连接 IA 服务器。</p>
        </div>
        
        <div style="display:flex; flex-direction:column; gap:10px;">
            <button onclick="closeHelpAndGo()" class="btn btn-blue" style="margin:0; padding:12px; border-radius:8px;" id="helpGoBtn">🚀 前往官网</button>
            <button onclick="document.getElementById('helpModal').style.display='none'" class="btn btn-gray" style="margin:0; padding:12px; border-radius:8px;">❌ 关闭</button>
        </div>
    </div>
</div>

{{ n|safe }}
<script>
    let currentHelpTarget = '';
    
    function showHelpModal(target) {
        currentHelpTarget = target;
        document.getElementById('helpContentSignup').style.display = target === 'signup' ? 'block' : 'none';
        document.getElementById('helpContentS3').style.display = target === 's3' ? 'block' : 'none';
        document.getElementById('helpTitle').innerText = target === 'signup' ? '🔗 前往官网注册' : '🔑 关于 S3 密钥';
        
        let goBtn = document.getElementById('helpGoBtn');
        if (target === 'signup') {
            goBtn.style.display = 'block';
            goBtn.innerText = '🚀 我已配好代理，前往注册页面';
        } else {
            goBtn.style.display = 'none'; // S3 引导不需要去官网
        }
        document.getElementById('helpModal').style.display = 'flex';
    }
    
    function closeHelpAndGo() {
        document.getElementById('helpModal').style.display = 'none';
        if (currentHelpTarget === 'signup') {
            window.open('https://archive.org/account/signup', '_blank');
        }
    }

    async function fetchS3(btn) {
        let email = document.getElementById('fetch_s3_email').value.trim();
        let pwd = document.getElementById('fetch_s3_pwd').value.trim();
        if (!email || !pwd) return alert('请先输入邮箱和密码');
        
        let oldText = btn.innerText;
        btn.innerText = '⏳ 正在连接官方提取...';
        btn.disabled = true;
        
        try {
            let res = await (await fetch('/api/fetch_s3', {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({email: email, password: pwd})
            })).json();
            
            if (res.status === 'success') {
                document.getElementById('s3_access').value = res.access_key;
                document.getElementById('s3_secret').value = res.secret_key;
                alert('✅ S3 密钥提取成功！已自动填入，请点击「保存并进入系统」。');
                document.getElementById('fetch_s3_pwd').value = ''; // 清空密码
            } else {
                alert('❌ 提取失败：\n' + res.msg);
            }
        } catch(e) {
            alert('❌ 网络异常或代理未开启，请检查代理节点是否正常工作。');
        }
        
        btn.innerText = oldText;
        btn.disabled = false;
    }

    function switchTab(tabName) { document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active')); document.querySelectorAll('.tab-btn').forEach(el => el.classList.remove('active')); document.getElementById('tab-' + tabName).classList.add('active'); event.target.classList.add('active'); } 
    function togglePwd(id) { var input = document.getElementById(id); if (input.type === "password") { input.type = "text"; event.target.innerText = "🙈"; } else { input.type = "password"; event.target.innerText = "👁️"; } } 
    function toggleEmail(index) { var e = document.getElementById('email_' + index), m = document.getElementById('mask_' + index), b = event.target; if (e.style.display === 'none' || e.style.display === '') { e.style.display = 'inline'; m.style.display = 'none'; b.innerText = '🙈 隐藏'; } else { e.style.display = 'none'; m.style.display = 'inline'; b.innerText = '👁️ 显示'; } } 
    async function editNameFrom(btn) { let email = btn.dataset.email, oldName = btn.dataset.name; let newName = prompt("自定义账号名：", oldName); if (newName === null || newName.trim() === "" || newName === oldName) return; let res = await (await fetch('/api/update_name', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({email: email, name: newName.trim()}) })).json(); if (res.status === 'success') location.reload(); else alert(res.msg || '失败'); }
    
    async function installDeps(btn) {
        if (!confirm('确定要自动安装/修复系统所需的 Python 依赖库吗？\n(需要网络连接，可能需要几十秒)')) return;
        let overlay = document.getElementById('installLoading');
        let status = document.getElementById('installStatus');
        overlay.style.display = 'flex';
        status.innerText = '正在执行 pip install... 请稍候';
        
        try {
            let res = await (await fetch('/api/install_deps', { method: 'POST' })).json();
            overlay.style.display = 'none';
            alert(res.msg);
        } catch(e) {
            overlay.style.display = 'none';
            alert('❌ 安装请求超时或网络异常，请检查终端是否还在运行。');
        }
    }
</script>
</body></html>
"""