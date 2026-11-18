# pages_settings_js.py
# 设置页 JS（日志拉取/复制、权限申请、一键安装依赖）

SETTINGS_JS = r"""<script>
    let currentLogs = [];
    async function fetchLogs() { let res = await (await fetch('/api/logs')).json(); currentLogs = res.logs; let box = document.getElementById('log-box'); box.innerHTML = currentLogs.map(l => `<div>${escapeHtml(l)}</div>`).join(''); box.scrollTop = box.scrollHeight; }
    function copyLogs() {
        if(!currentLogs || currentLogs.length === 0) return alert('暂无日志');
        let text = currentLogs.join('\n');
        if(navigator.clipboard) navigator.clipboard.writeText(text).then(()=>alert('✅ 日志已复制到剪贴板')).catch(()=>fallbackCopy(text));
        else fallbackCopy(text);
    }
    function fallbackCopy(text) { var ta = document.createElement("textarea"); ta.value = text; ta.style.position = "fixed"; ta.style.opacity = "0"; document.body.appendChild(ta); ta.select(); try { document.execCommand('copy'); alert('✅ 日志已复制'); } catch (err) { alert('❌ 复制失败'); } document.body.removeChild(ta); }

    // 【新增】申请权限
    async function requestPerm(type, btn) {
        let old = btn.innerText;
        btn.innerText = '正在唤起系统...';
        btn.disabled = true;
        let statusBox = document.getElementById('statusBox');
        statusBox.innerText = '请留意手机屏幕上方是否弹出了提示...';
        statusBox.style.color = 'var(--primary)';
        
        try {
            let res = await (await fetch('/api/request_permission', {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({type: type})
            })).json();
            statusBox.innerText = res.msg;
            statusBox.style.color = res.status === 'success' ? 'var(--green)' : 'var(--danger)';
        } catch(e) {
            statusBox.innerText = '❌ 请求超时，请检查终端是否在运行。';
            statusBox.style.color = 'var(--danger)';
        }
        btn.innerText = old;
        btn.disabled = false;
    }

    // 【新增】一键安装依赖 (同时自动触发权限与下载核心)
    async function installDeps(btn) {
        if (!confirm('确定要自动安装 Python 依赖库、系统组件并下载代理核心吗？\n(需要网络连接，可能需要几十秒)')) return;
        let old = btn.innerText;
        btn.innerText = '⏳ 正在安装，请勿刷新页面...';
        btn.disabled = true;
        let statusBox = document.getElementById('statusBox');
        statusBox.innerText = '正在执行 pip install 及核心下载，请耐心等待...';
        statusBox.style.color = 'var(--primary)';
        
        try {
            let res = await (await fetch('/api/install_deps', { method: 'POST' })).json();
            statusBox.innerText = res.msg;
            statusBox.style.color = res.status === 'success' ? 'var(--green)' : 'var(--danger)';
        } catch(e) {
            statusBox.innerText = '❌ 安装请求超时或网络异常。';
            statusBox.style.color = 'var(--danger)';
        }
        btn.innerText = old;
        btn.disabled = false;
    }

    fetchLogs();
</script>
</body></html>
"""