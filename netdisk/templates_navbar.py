# templates_navbar.py
# 顶部导航栏 + 移动端更多菜单 + 代理弹窗 HTML/JS + 底部移动端导航
# get_navbar(show_back=False, parent_id=0, active_page="", is_login=False)


def get_navbar(show_back=False, parent_id=0, active_page="", is_login=False):
    if is_login:
        back_html = ""
    else:
        back_html = ""
        if show_back:
            if parent_id == 0: back_html = '<a href="/">⬅️ 返回</a>'
            else: back_html = f'<a href="/folder/{parent_id}">⬅️ 返回</a>'
    
    top_nav = r"""
    <div class="navbar">
        <div class="nav-links">
            <a href="/" class="mobile-keep">🏠 根目录</a>
            <a href="/discover" class="mobile-keep">🌍 发现</a>
            <a href="/search" class="mobile-keep">🔍 搜索</a>
            <a href="/history">📜 历史</a>
            <a href="/queue" class="mobile-keep">📝 队列</a>
            <a href="/trash">🗑️ 回收站</a>
            <a href="/settings" class="mobile-keep">⚙️ 设置</a>
            <button onclick="showProxyModal()" class="mobile-keep">🌐 代理</button>
            <button onclick="toggleTheme()">🌓 模式</button>
            <button class="mobile-more-btn" onclick="document.getElementById('mobileMoreMenu').style.display='flex'">⋯ 更多</button>
            {BACK_HTML}
        </div>
        <div><a href="/logout" class="danger">🚪 退出登录</a></div>
    </div>
    <div class="modal-overlay" id="mobileMoreMenu" onclick="if(event.target.id==='mobileMoreMenu')this.style.display='none'">
        <div class="modal-content" style="max-width:300px;">
            <div class="modal-title"><span>⋯ 更多功能</span><span class="modal-close" onclick="document.getElementById('mobileMoreMenu').style.display='none'">&times;</span></div>
            <a href="/discover" class="btn btn-gray" style="margin-bottom:10px;">🌍 发现资源</a>
            <a href="/history" class="btn btn-gray" style="margin-bottom:10px;">📜 历史上传</a>
            <a href="/trash" class="btn btn-gray" style="margin-bottom:10px;">🗑️ 回收站</a>
            <button onclick="toggleTheme(); document.getElementById('mobileMoreMenu').style.display='none'" class="btn btn-gray" style="margin-bottom:10px;">🌓 切换主题</button>
            <button onclick="shutdownApp()" class="btn btn-red">🛑 关闭网盘</button>
        </div>
    </div>
    <!-- 高级代理弹窗 -->
    <div class="modal-overlay" id="proxyModal" onclick="if(event.target.id==='proxyModal')this.style.display='none'">
        <div class="modal-content" style="max-width:600px;">
            <div class="modal-title"><span>🌐 高级代理管理</span><span class="modal-close" onclick="document.getElementById('proxyModal').style.display='none'">&times;</span></div>
            <div class="modal-body">
                
                <!-- 【新增】节点选择指南 -->
                <div style="background:#e8f5e9; border:1px solid var(--green); padding:12px; border-radius:8px; margin-bottom:15px; font-size:13px; color:#2e7d32;">
                    <b>💡 节点选择指南：</b><br>
                    1. 日常推荐使用 <b>W 协议</b>，底层自带 HTTP 代理，网盘上传下载极速且稳定。<br>
                    2. 如果 W 协议测速失败（报 502/超时），说明运营商封锁了 UDP 端口，此时请切换生成 <b>M 协议</b>（伪装性更强）。
                </div>

                <!-- 全局代理设置教程 -->
                <details style="margin-bottom:15px; border-color:var(--orange); background:rgba(255,152,0,0.05);">
                    <summary style="color:var(--orange);">📖 浏览器全局代理设置教程 (如何访问 IA 官网)</summary>
                    <div style="padding:10px; margin-top:10px; background:var(--card-bg); border-radius:6px; font-size:13px;">
                        <p style="margin-bottom:10px;">因为手机系统权限限制，Python 无法一键接管全局。但你可以配合浏览器自带的代理设置，秒开 IA 官网：</p>
                        <ol style="padding-left:20px; line-height:1.8;">
                            <li>在下方生成或启用一个节点。</li>
                            <li>在节点右侧点击「📋 复制」获取 <code>127.0.0.1:端口号</code>。</li>
                            <li>打开手机系统设置 -> Wi-Fi -> 修改当前连接的 Wi-Fi。</li>
                            <li>找到「代理」，改为「手动」。</li>
                            <li>主机名填 <code>127.0.0.1</code>，端口填刚刚复制的数字。</li>
                            <li>保存后，在浏览器直接输入 <code>archive.org</code>，即可秒开！</li>
                        </ol>
                        <span class="tip" style="margin-top:10px;">💡 提示：如果你用的是 Edge/Chrome 等浏览器，可以在设置里搜索“代理”直接配置。如果是 LxBox 等软件，直接把节点导入即可。</span>
                    </div>
                </details>

                <!-- 外置代理软件下载 -->
                <details style="margin-bottom:15px; border-color:var(--blue); background:rgba(33,150,243,0.05);">
                    <summary style="color:var(--blue);">📥 备用代理软件直接下载 (点击即可下载)</summary>
                    <div style="padding:10px; margin-top:10px; background:var(--card-bg); border-radius:6px; font-size:13px;">
                        <p style="margin-bottom:10px; font-size:12px; color:var(--gray);">如果内置节点因为运营商限制无法使用，或者你需要全局接管手机/电脑的所有网络（不仅限于浏览器），推荐使用以下成熟的第三方 WARP 客户端：</p>
                        
                        <div style="background:rgba(0,150,136,0.1); padding:10px; border-radius:6px; margin-bottom:10px; border-left:4px solid var(--primary);">
                            <span style="font-size:13px; font-weight:bold; color:var(--primary-dark);">📱 安卓手机端：LxBox (v2.25.17 arm64)</span>
                            <div style="font-size:12px; margin:5px 0; color:var(--gray);">稳定、一键生成云端 WARP 节点。</div>
                            <div style="display:flex; flex-wrap:wrap; gap:5px;">
                                <a href="https://gh-proxy.org/https://github.com/Leadaxe/LxBox/releases/download/v2.25.17/LxBox-v2.25.17-arm64-v8a.apk" target="_blank" class="btn btn-sm btn-green" style="flex:1;">📥 加速下载 (直链)</a>
                                <a href="https://github.com/Leadaxe/LxBox/releases/download/v2.25.17/LxBox-v2.25.17-arm64-v8a.apk" target="_blank" class="btn btn-sm btn-gray" style="flex:1;">🌐 官方直链</a>
                            </div>
                        </div>

                        <div style="background:rgba(33,150,243,0.1); padding:10px; border-radius:6px; border-left:4px solid var(--blue);">
                            <span style="font-size:13px; font-weight:bold; color:var(--blue);">💻 电脑端：Oblivion Desktop (v3.11.0 Win)</span>
                            <div style="font-size:12px; margin:5px 0; color:var(--gray);">功能与 LxBox 类似，带图形界面，一键连接。</div>
                            <div style="display:flex; flex-wrap:wrap; gap:5px;">
                                <a href="https://gh-proxy.org/https://github.com/bepass-org/oblivion-desktop/releases/download/v3.11.0/oblivion-desktop-win-x64.exe" target="_blank" class="btn btn-sm btn-green" style="flex:1;">📥 加速下载 (Win直链)</a>
                                <a href="https://github.com/bepass-org/oblivion-desktop/releases/download/v3.11.0/oblivion-desktop-win-x64.exe" target="_blank" class="btn btn-sm btn-gray" style="flex:1;">🌐 官方直链</a>
                            </div>
                        </div>
                    </div>
                </details>

                <div style="background:#e3f2fd; padding:15px; border-radius:8px; margin-bottom:15px; text-align:center;">
                    <h3 style="margin-top:0; border:none; color:var(--blue);">一键全自动 WARP</h3>
                    <p style="font-size:13px; color:var(--text); margin-bottom:10px;">系统会自动识别您当前是手机还是电脑，并下载对应的核心（Masque 协议 + 自动降级）。</p>
                    <button onclick="genDefaultWarp(this)" class="btn btn-green" style="padding:12px; border-radius:20px;">🚀 一键生成默认 WARP</button>
                </div>
                <details>
                    <summary style="color:var(--danger);">📦 核心下载失败？点击此处手动安装核心</summary>
                    <div style="margin-top:10px; padding:10px; background:rgba(0,0,0,0.05); border-radius:6px;">
                        <p style="font-size:12px; color:var(--gray); margin-bottom:10px;">如果自动下载失败，请对照下方你当前使用的设备，点击对应的加速下载链接。下载完成后在此处上传，后端会自动解压并赋予执行权限。</p>
                        
                        <!-- 手机端区块 -->
                        <div style="background:rgba(0,150,136,0.1); padding:10px; border-radius:6px; margin-bottom:10px; border-left:4px solid var(--primary);">
                            <span style="font-size:13px; font-weight:bold; color:var(--primary-dark);">📱 安卓手机 (Termux) 专用核心：</span>
                            <div style="font-size:12px; margin:5px 0; color:var(--gray);">请下载 arm64 版本 (绝大多数安卓手机适用)</div>
                            
                            <div style="display:flex; flex-wrap:wrap; gap:5px; margin-bottom:10px;">
                                <span style="font-size:12px; font-weight:bold; color:var(--blue); width:100%;">用于 W 协议 - sing-box:</span>
                                <a href="https://gh-proxy.org/https://github.com/SagerNet/sing-box/releases/download/v1.9.0/sing-box-1.9.0-linux-arm64.tar.gz" target="_blank" class="btn btn-sm btn-green" style="flex:1;">📥 加速下载 (arm64)</a>
                                <a href="https://github.com/SagerNet/sing-box/releases/download/v1.9.0/sing-box-1.9.0-linux-arm64.tar.gz" target="_blank" class="btn btn-sm btn-gray" style="flex:1;">🌐 官方原链 (arm64)</a>
                            </div>
                            <div style="display:flex; flex-wrap:wrap; gap:5px;">
                                <span style="font-size:12px; font-weight:bold; color:var(--primary); width:100%;">用于 M 协议 - usque:</span>
                                <a href="https://gh-proxy.org/https://github.com/Diniboy1123/usque/releases/download/v4.2.1/usque_4.2.1_linux_arm64.zip" target="_blank" class="btn btn-sm btn-green" style="flex:1;">📥 加速下载 (arm64)</a>
                                <a href="https://github.com/Diniboy1123/usque/releases/download/v4.2.1/usque_4.2.1_linux_arm64.zip" target="_blank" class="btn btn-sm btn-gray" style="flex:1;">🌐 官方原链 (arm64)</a>
                            </div>
                        </div>

                        <!-- 电脑端区块 -->
                        <div style="background:rgba(33,150,243,0.1); padding:10px; border-radius:6px; border-left:4px solid var(--blue);">
                            <span style="font-size:13px; font-weight:bold; color:var(--blue);">💻 电脑端 (Windows / Linux / macOS) 专用核心：</span>
                            <div style="font-size:12px; margin:5px 0; color:var(--gray);">Windows 选择 windows-amd64，Linux/Mac 选择 linux/darwin-amd64</div>
                            
                            <div style="display:flex; flex-wrap:wrap; gap:5px; margin-bottom:10px;">
                                <span style="font-size:12px; font-weight:bold; color:var(--blue); width:100%;">用于 W 协议 - sing-box:</span>
                                <a href="https://gh-proxy.org/https://github.com/SagerNet/sing-box/releases/download/v1.9.0/sing-box-1.9.0-windows-amd64.zip" target="_blank" class="btn btn-sm btn-green" style="flex:1;">📥 加速下载 (Win amd64)</a>
                                <a href="https://github.com/SagerNet/sing-box/releases/download/v1.9.0/sing-box-1.9.0-windows-amd64.zip" target="_blank" class="btn btn-sm btn-blue" style="flex:1;">🌐 官方原链 (Win)</a>
                            </div>
                            <div style="display:flex; flex-wrap:wrap; gap:5px;">
                                <span style="font-size:12px; font-weight:bold; color:var(--primary); width:100%;">用于 M 协议 - usque:</span>
                                <a href="https://gh-proxy.org/https://github.com/Diniboy1123/usque/releases/download/v4.2.1/usque_4.2.1_windows_amd64.zip" target="_blank" class="btn btn-sm btn-green" style="flex:1;">📥 加速下载 (Win amd64)</a>
                                <a href="https://github.com/Diniboy1123/usque/releases/download/v4.2.1/usque_4.2.1_windows_amd64.zip" target="_blank" class="btn btn-sm btn-blue" style="flex:1;">🌐 官方原链 (Win)</a>
                            </div>
                        </div>

                        <input type="file" id="coreFileInput" accept=".tar.gz,.tgz,.zip,application/octet-stream" style="margin-bottom:10px; margin-top:15px;">
                        <button onclick="uploadCore(this)" class="btn btn-orange" style="padding:10px; margin:0;">⬆️ 上传并安装核心</button>
                        <div id="coreUploadStatus" style="font-size:13px; text-align:center; margin-top:10px; font-weight:bold;"></div>
                    </div>
                </details>
                <details>
                    <summary>⚙️ 高级自定义生成</summary>
                    <div class="adv-container" style="background:var(--bg); border-radius:8px;">
                        <div style="display:flex; flex-direction:column; gap:8px; margin-bottom:15px; background:rgba(0,0,0,0.02); padding:10px; border-radius:6px;">
                            <label style="display:flex; align-items:center; gap:8px; font-size:14px; font-weight:bold; margin:0;"><input type="radio" name="proto" value="M" checked onchange="toggleSni()" style="width:18px;height:18px;margin:0;"> Masque (HTTP/3) [推荐]</label>
                            <label style="display:flex; align-items:center; gap:8px; font-size:14px; font-weight:bold; margin:0;"><input type="radio" name="proto" value="W" onchange="toggleSni()" style="width:18px;height:18px;margin:0;"> WireGuard (标准)</label>
                        </div>
                        <label>Transport (传输协议):</label>
                        <select id="transport-select">
                            <option value="auto">Auto (自动降级 h3 -> h2) [最稳]</option>
                            <option value="h3">HTTP/3 (QUIC) [最快，易被拦截]</option>
                            <option value="h2">HTTP/2 (TCP) [兼容性最好]</option>
                        </select>
                        <label>Endpoint IP (下拉选择):</label>
                        <select id="ip-select" onchange="toggleCustomInput('ip-select', 'ip-custom')">
                            <option value="162.159.193.10">162.159.193.10 (M协议默认)</option>
                            <option value="engage.cloudflareclient.com">engage.cloudflareclient.com (W协议默认)</option>
                            <option value="162.159.192.1">162.159.192.1</option>
                            <option value="188.114.96.1">188.114.96.1</option>
                            <option value="custom">✏️ 自定义输入...</option>
                        </select>
                        <input type="text" id="ip-custom" placeholder="输入自定义 IP" style="display:none;">
                        <label>端口:</label>
                        <select id="port-select" onchange="toggleCustomInput('port-select', 'port-custom')">
                            <option value="443">443 (HTTPS)</option>
                            <option value="2408">2408 (WireGuard)</option>
                            <option value="500">500 (IKEv2)</option>
                            <option value="1701">1701 (L2TP)</option>
                            <option value="4500">4500 (IPsec NAT-T)</option>
                            <option value="custom">✏️ 自定义输入...</option>
                        </select>
                        <input type="text" id="port-custom" placeholder="输入自定义端口" style="display:none;">
                        <div id="sni-section">
                            <label>SNI 伪装域名 (仅 Masque):</label>
                            <select id="sni-select" onchange="toggleCustomInput('sni-select', 'sni-custom')">
                                <option value="www.speedtest.net">www.speedtest.net (测速网 - 极稳)</option>
                                <option value="www.digitalocean.com">www.digitalocean.com (云服务商)</option>
                                <option value="www.github.com">www.github.com (开发者)</option>
                                <option value="www.icloud.com">www.icloud.com (苹果云)</option>
                                <option value="www.apple.com">www.apple.com (苹果)</option>
                                <option value="www.microsoft.com">www.microsoft.com (微软)</option>
                                <option value="www.bing.com">www.bing.com (必应)</option>
                                <option value="www.tesla.com">www.tesla.com (特斯拉)</option>
                                <option value="www.amazon.com">www.amazon.com (亚马逊)</option>
                                <option value="www.zoom.us">www.zoom.us (Zoom)</option>
                                <option value="www.spotify.com">www.spotify.com (Spotify)</option>
                                <option value="www.twitch.tv">www.twitch.tv (Twitch)</option>
                                <option value="www.wikipedia.org">www.wikipedia.org (维基)</option>
                                <option value="www.visa.com">www.visa.com (Visa)</option>
                                <option value="deepseek.com">deepseek.com (深度求索)</option>
                                <option value="www.baidu.com">www.baidu.com (百度)</option>
                                <option value="www.qq.com">www.qq.com (腾讯)</option>
                                <option value="www.taobao.com">www.taobao.com (淘宝)</option>
                                <option value="www.cloudflare.com">www.cloudflare.com (官方)</option>
                                <option value="custom">✏️ 自定义输入...</option>
                            </select>
                            <input type="text" id="sni-custom" placeholder="输入自定义 SNI 域名" style="display:none;">
                        </div>
                        <label>Keepalive (保活秒数):</label>
                        <input type="number" id="custom_keepalive" value="25" placeholder="默认 25">
                        <button onclick="genCustomWarp(this)" class="btn btn-blue" style="padding:10px; margin-top:10px;">生成自定义节点</button>
                    </div>
                </details>
                <h3 style="font-size:15px; margin-top:20px;">已保存节点</h3>
                <div id="proxyList"><p><span class="spinner"></span>加载中...</p></div>
                <fieldset style="margin-top:15px; padding:10px;">
                    <legend>手动添加代理链接</legend>
                    <input type="text" id="newProxyName" placeholder="名称（如：本地自建）" style="margin-bottom:5px;">
                    <input type="text" id="newProxyUrl" placeholder="地址（如：socks5://127.0.0.1:1080）" style="margin-bottom:5px;">
                    <button onclick="addProxy()" class="btn btn-orange" style="padding:8px;">保存链接</button>
                </fieldset>
            </div>
        </div>
    </div>
    <script>
        async function showProxyModal() { document.getElementById('proxyModal').style.display = 'flex'; await loadProxies(); }
        async function uploadCore(btn) {
            let input = document.getElementById('coreFileInput');
            if (!input.files.length) return alert('请选择核心文件');
            let fd = new FormData(); fd.append('file', input.files[0]);
            let old = btn.innerText; btn.innerText = '正在安装...'; btn.disabled = true;
            let status = document.getElementById('coreUploadStatus'); status.innerText = '';
            try {
                let res = await (await fetch('/api/warp/upload_core', {method:'POST', body: fd})).json();
                if(res.status === 'success') { status.innerText = '✅ ' + res.msg; status.style.color = 'var(--green)'; }
                else { status.innerText = '❌ ' + res.msg; status.style.color = 'var(--danger)'; }
            } catch(e) { status.innerText = '❌ 上传失败，请重试'; status.style.color = 'var(--danger)'; }
            btn.innerText = old; btn.disabled = false; input.value = '';
        }
        async function genDefaultWarp(btn) {
            let old = btn.innerText; btn.innerText = '正在注册并生成...'; btn.disabled = true;
            let res = await (await fetch('/api/warp/generate', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({default:true})})).json();
            btn.disabled = false; btn.innerText = old; alert(res.msg); await loadProxies();
        }
        async function genCustomWarp(btn) {
            let proto = document.querySelector('input[name="proto"]:checked').value;
            let transport = document.getElementById('transport-select').value;
            let ipSel = document.getElementById('ip-select').value; let ip = ipSel === 'custom' ? document.getElementById('ip-custom').value.trim() : ipSel;
            let portSel = document.getElementById('port-select').value; let port = portSel === 'custom' ? document.getElementById('port-custom').value.trim() : portSel;
            let sni = '';
            if(proto === 'M') { let sniSel = document.getElementById('sni-select').value; sni = sniSel === 'custom' ? document.getElementById('sni-custom').value.trim() : sniSel; }
            let keepalive = document.getElementById('custom_keepalive').value.trim() || '25';
            let old = btn.innerText; btn.innerText = '正在注册并生成...'; btn.disabled = true;
            let res = await (await fetch('/api/warp/generate', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({ default:false, protocol:proto, transport:transport, ip:ip, port:port, sni:sni, keepalive:keepalive })})).json();
            btn.disabled = false; btn.innerText = old; alert(res.msg); await loadProxies();
        }
        async function loadProxies() {
            let box = document.getElementById('proxyList'); box.innerHTML = '<p><span class="spinner"></span>加载中...</p>';
            let res = await (await fetch('/api/proxies')).json();
            if(!res.proxies || res.proxies.length === 0) { box.innerHTML = '<p style="color:var(--gray); font-size:13px;">暂无保存的代理</p>'; return; }
            let html = '';
            res.proxies.forEach((p, i) => {
                let isActive = res.active === p.url; let statusBadge = isActive ? '<span class="proxy-status status-active">当前启用</span>' : '';
                let port = p.url.split(':').pop();
                html += `<div class="proxy-item"><div class="proxy-info"><div style="font-weight:bold;">${escapeHtml(p.name)} ${statusBadge}</div><div class="proxy-url">${escapeHtml(p.url)}</div></div><div style="display:flex; gap:5px; flex-wrap:wrap;"><button class="btn btn-sm btn-gray" onclick="copyProxy('127.0.0.1:${port}')" title="复制给浏览器使用">📋 复制</button><button class="btn btn-sm btn-green" data-url="${escapeHtml(p.url)}" onclick="testProxy(this.dataset.url, this)">测速</button>${!isActive ? `<button class="btn btn-sm btn-blue" data-url="${escapeHtml(p.url)}" onclick="setActiveProxy(this.dataset.url)">启用</button>` : `<button class="btn btn-sm btn-orange" onclick="setActiveProxy('')">停用</button>`}<button class="btn btn-sm btn-red" onclick="delProxy(${i})">删除</button></div></div>`;
            });
            box.innerHTML = html;
        }
        function copyProxy(text) {
            if(navigator.clipboard) navigator.clipboard.writeText(text).then(()=>alert('✅ 已复制：' + text + '\\n请去浏览器代理设置中粘贴。')).catch(()=>fallbackCopy(text));
            else fallbackCopy(text);
        }
        function fallbackCopy(text) {
            var ta = document.createElement("textarea"); ta.value = text; ta.style.position = "fixed"; ta.style.opacity = "0";
            document.body.appendChild(ta); ta.select();
            try { document.execCommand('copy'); alert('✅ 已复制：' + text); } catch (err) { alert('❌ 复制失败'); }
            document.body.removeChild(ta);
        }
        async function testProxy(url, btn) {
            let old = btn.innerText; btn.innerText = '测试中...'; btn.disabled = true;
            let res = await (await fetch('/api/proxy/test?url=' + encodeURIComponent(url))).json();
            if(res.status === 'success') { btn.innerText = `✅ ${res.latency}ms`; btn.className = 'btn btn-sm btn-gray'; }
            else { btn.innerText = `❌ 失败`; btn.className = 'btn btn-sm btn-red'; alert('测速失败原因: ' + (res.msg || '网络不通或代理服务器拒绝连接')); }
            setTimeout(() => { btn.innerText = old; btn.disabled = false; }, 3000);
        }
        async function setActiveProxy(url) { await fetch('/api/proxy/active', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({url: url})}); await loadProxies(); }
        async function addProxy() {
            let name = document.getElementById('newProxyName').value.trim(); let url = document.getElementById('newProxyUrl').value.trim();
            if(!name || !url) return alert('请填写完整');
            await fetch('/api/proxy/add', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({name:name, url:url})});
            document.getElementById('newProxyName').value = ''; document.getElementById('newProxyUrl').value = ''; await loadProxies();
        }
        async function delProxy(idx) { if(!confirm('确定删除该代理？')) return; await fetch('/api/proxy/del', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({index: idx})}); await loadProxies(); }
    </script>
    """.replace("{BACK_HTML}", back_html)

    def act(name): return 'active' if active_page == name else ''
    bottom_nav = """
    <div class="bottom-nav">
        <a href="/" class="""" + act('index') + """"><span>🏠</span>主页</a>
        <a href="/discover" class="""" + act('discover') + """"><span>🌍</span>发现</a>
        <a href="/search" class="""" + act('search') + """"><span>🔍</span>搜索</a>
        <a href="/queue" class="""" + act('queue') + """"><span>📝</span>队列</a>
        <a href="/settings" class="""" + act('settings') + """"><span>⚙️</span>设置</a>
    </div>
    """
    
    if is_login:
        return top_nav.split('<div class="navbar">')[0] + '<div id="proxyModalPlaceholder">' + top_nav.split('<!-- 高级代理弹窗 -->')[1]
    return top_nav + bottom_nav