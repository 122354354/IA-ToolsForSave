# pages_discover.py
# 发现页（IA 全网搜索）HTML

DISCOVER_HTML = r"""
<!DOCTYPE html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">{{ s|safe }}
<style>
    .history-tag { background: rgba(0,150,136,0.1); color: var(--primary-dark); border-radius: 15px; padding: 6px 14px; font-size: 13px; cursor: pointer; display: inline-flex; align-items: center; border: 1px solid var(--primary); transition: 0.2s; user-select: none; }
    .history-tag:hover { background: rgba(0,150,136,0.2); }
    .history-del-btn { color: #f44336; margin-left: 8px; font-weight: bold; font-size: 16px; line-height: 1; }
    .history-del-btn:hover { color: #d32f2f; }
</style>
</head><body>
{{ n|safe }}
<div class="card">
    <div style="margin-bottom:15px;"><button onclick="history.back()" class="btn btn-sm btn-gray">⬅️ 返回上一级</button></div>
    <h2>🌍 探索 IA 资源</h2>
    <p style="font-size:13px; color:var(--gray); margin-bottom:10px;">这是通过官方 API 实时搜索全网资源。为保护您的代理 IP 不被封禁，每次仅拉取 20 条结果。</p>
    <form method="GET" action="/discover" onsubmit="document.getElementById('searchBtn').innerText='搜索中...'; document.getElementById('searchBtn').disabled=true;" style="display:flex; gap:10px; margin-bottom:15px;">
        <input type="text" name="q" value="{{ raw_q }}" placeholder="搜索资源（如：东方Project, rom, 中文）..." style="margin:0; flex:1; min-width:0;" required>
        <button type="submit" id="searchBtn" class="btn btn-blue" style="width:80px; margin:0; flex-shrink:0;">搜索</button>
    </form>
    
    <!-- 历史搜索记录 -->
    {% if history %}
    <div style="margin-bottom:20px; border-top:1px dashed var(--border); padding-top:15px;">
        <span style="font-size:12px; color:var(--gray); font-weight:bold; margin-right:10px;">搜索历史:</span>
        <div style="display:flex; flex-wrap:wrap; gap:8px; margin-top:8px;">
            {% for h in history %}
            <div class="history-tag" onclick="location.href='/discover?q={{ h | urlencode }}'">
                {{ h }}
                <!-- 【核心修复 Bug 11】用 data-q 代替内联字符串拼接 -->
                <span class="history-del-btn" data-q="{{ h }}" onclick="event.stopPropagation(); delHistory(this.dataset.q)">×</span>
            </div>
            {% endfor %}
        </div>
    </div>
    {% endif %}
</div>

{% if raw_q %}
<div class="card">
    <h3 style="margin-top:0;">搜索结果 (共约 {{ total }} 项)</h3>
    {% for item in items %}
    <div class="item-row" style="flex-direction:row; flex-wrap:wrap; align-items:flex-start; gap:10px;">
        <div class="thumb-wrapper" style="width:80px; height:80px; flex-shrink:0; background:#e0e0e0; border-radius:8px;">
            <!-- 【核心修复 Bug 11】用 data-ident 代替内联字符串拼接 -->
            <div style="width:100%; height:100%; display:flex; align-items:center; justify-content:center; cursor:pointer; font-size:12px; color:#888;" data-ident="{{ item.identifier }}" onclick="loadDiscoverThumb(this, this.dataset.ident)">点我加载</div>
        </div>
        <div style="flex:1; min-width:200px; word-break:break-all;">
            <strong style="font-size:15px; color:var(--text);">{{ item.title }}</strong><br>
            <small style="color:var(--gray);">ID: {{ item.identifier }}</small><br>
            <small style="color:var(--gray);">👤 {{ item.creator or '未知' }}</small><br>
            <small style="color:var(--gray);">👁️ {{ item.downloads or 0 }} 次 | ⭐ {{ item.stars or 0 }}</small>
            <p style="font-size:12px; color:var(--gray); margin-top:6px; display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden;">{{ item.description or '暂无描述' }}</p>
            <div style="display:flex; gap:6px; flex-wrap:wrap; margin-top:8px;">
                <!-- 【核心修复 Bug 11】存入网盘按钮改为全部 data-* -->
                <button class="btn btn-sm btn-green" 
                    data-ident="{{ item.identifier }}"
                    data-title="{{ item.title or '' }}"
                    data-creator="{{ item.creator or '' }}"
                    data-date="{{ item.date or '' }}"
                    data-mediatype="{{ item.mediatype or 'software' }}"
                    onclick="saveToNetdisk(this)">📥 存入网盘</button>
                <a href="https://archive.org/details/{{ item.identifier }}" target="_blank" class="btn btn-sm btn-blue">🌐 官网</a>
                <!-- 【核心修复 Bug 11】种子按钮改为 data-* -->
                <button class="btn btn-sm btn-brown" 
                    data-filename="{{ item.identifier }}_archive.torrent"
                    data-url="https://archive.org/download/{{ item.identifier }}/{{ item.identifier }}_archive.torrent"
                    onclick="triggerDownload(this.dataset.filename, this.dataset.url)">🌱 种子</button>
            </div>
        </div>
    </div>
    {% endfor %}
    {% if not items %}
    <p style="color:#999; text-align:center; padding:40px 0;">未找到相关资源，换个词试试。</p>
    {% endif %}
    
    {% if total > 20 %}
    <div style="display:flex; justify-content:center; flex-wrap:wrap; gap:10px; margin-top:20px; align-items:center;">
        {% if page > 1 %}<button onclick="goPage({{ page-1 }})" class="btn btn-sm btn-gray page-btn">上一页</button>{% endif %}
        <span style="padding:6px; font-size:13px;">第 {{ page }} / {{ (total + 19) // 20 }} 页</span>
        <input type="number" id="pageJump" min="1" max="{{ (total + 19) // 20 }}" style="width:60px; display:inline-block; margin:0; padding:4px; text-align:center;" placeholder="页码">
        <button onclick="let p=document.getElementById('pageJump').value; if(p) goPage(p);" class="btn btn-sm btn-blue page-btn" style="margin:0;">跳转</button>
        {% if page * 20 < total %}<button onclick="goPage({{ page+1 }})" class="btn btn-sm btn-gray page-btn">下一页</button>{% endif %}
    </div>
    {% endif %}
</div>

<div class="modal-overlay" id="bigImgModal" onclick="this.style.display='none'">
    <div class="modal-content" style="background:transparent; box-shadow:none; border:none; justify-content:center; align-items:center;">
        <img id="bigImgSrc" src="" style="max-width:100%; max-height:90vh; border-radius:8px;">
    </div>
</div>
<div class="modal-overlay" id="downloadChoiceModal">
    <div class="modal-content" style="max-width:350px; text-align:center;">
        <div class="modal-title" style="justify-content:center;">⬇️ 选择下载方式</div>
        <div class="modal-body" style="margin-bottom:15px; font-size:14px; color:var(--gray);">
            请选择如何下载此文件：
        </div>
        <div style="display:flex; flex-direction:column; gap:10px;">
            <button class="btn btn-blue" onclick="confirmDownload('termux')" style="margin:0; padding:12px;">📱 Termux 后台下载 (推荐)</button>
            <button class="btn btn-green" onclick="confirmDownload('browser')" style="margin:0; padding:12px;">🌐 浏览器直接下载</button>
            <button class="btn btn-gray" onclick="document.getElementById('downloadChoiceModal').style.display='none'" style="margin:0; padding:12px;">❌ 取消</button>
        </div>
    </div>
</div>
<!-- 预检 Loading 弹窗 -->
<div class="modal-overlay" id="loadingModal" style="background: rgba(0,0,0,0.7);">
    <div class="modal-content" style="max-width:250px; text-align:center; padding:20px;">
        <div class="spinner" style="width:40px; height:40px; margin-bottom:15px; border-width:4px;"></div>
        <div style="font-size:15px; font-weight:bold; color:var(--primary-dark);" id="loadingText">正在向代理请求数据...</div>
        <div style="font-size:12px; color:var(--gray); margin-top:10px;">如果当前节点被拦截，可能需要稍等几秒</div>
    </div>
</div>

<script>
    const PROXY_DL = {{ 'true' if proxy_dl else 'false' }};

    async function delHistory(q) {
        if(!confirm('确定删除这个搜索记录吗？')) return;
        let res = await (await fetch('/api/search_history/del', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({q: q})})).json();
        if(res.status === 'success') location.reload();
    }

    function goPage(page) {
        let btns = document.querySelectorAll('.page-btn');
        btns.forEach(b => { b.disabled = true; b.innerText = '加载中...'; });
        location.href = '/discover?q={{ q | urlencode }}&page=' + page;
    }

    function loadDiscoverThumb(el, identifier) {
        let container = el.parentNode;
        container.innerHTML = '<span class="spinner"></span>';
        let img = new Image();
        img.onload = () => { container.innerHTML = ''; img.style.width='80px'; img.style.height='80px'; img.style.objectFit='cover'; img.style.borderRadius='8px'; img.onclick = () => showBigImg(img.src); container.appendChild(img); };
        img.onerror = () => { container.innerHTML = '❌无图'; };
        img.src = '/api/download_proxy?url=' + encodeURIComponent('https://archive.org/services/img/' + identifier);
    }

    let currentDLFile = { filename: '', realUrl: '', finalUrl: '' };
    function triggerDownload(filename, realUrl, finalUrl) {
        let pUrl = finalUrl;
        if (!pUrl) {
            pUrl = PROXY_DL ? '/api/download_proxy?url=' + encodeURIComponent(realUrl) : realUrl;
        }
        currentDLFile = { filename: filename, realUrl: realUrl, finalUrl: pUrl };
        document.getElementById('downloadChoiceModal').style.display = 'flex';
    }
    
    async function confirmDownload(mode) {
        document.getElementById('downloadChoiceModal').style.display = 'none';
        if(mode === 'browser') {
            document.getElementById('loadingModal').style.display = 'flex';
            document.getElementById('loadingText').innerText = '正在向代理请求数据...';
            
            let controller = new AbortController();
            let timeoutId = setTimeout(() => controller.abort(), 12000);
            
            try {
                let testResp = await fetch(currentDLFile.finalUrl, { 
                    headers: {'Range': 'bytes=0-1024'}, 
                    signal: controller.signal 
                });
                clearTimeout(timeoutId);
                
                if (!testResp.ok && testResp.status !== 206) {
                    throw new Error(`HTTP ${testResp.status}`);
                }
                
                document.getElementById('loadingModal').style.display = 'none';
                let a = document.createElement('a');
                a.href = currentDLFile.finalUrl;
                a.download = currentDLFile.filename;
                a.target = '_blank';
                document.body.appendChild(a);
                a.click();
                document.body.removeChild(a);
                
            } catch(e) {
                clearTimeout(timeoutId);
                document.getElementById('loadingModal').style.display = 'none';
                if (e.name === 'AbortError') alert('下载超时：代理节点在 12 秒内无响应，建议更换节点。');
                else alert('下载失败：' + e.message + '\n可能是代理节点被拦截，请尝试更换节点。');
            }
        } else if (mode === 'termux') {
            // 【P1-4 修复】统一走 /api/download_batch，与首页/详情页一致
            // 好处：走大卡片下载队列（支持暂停/断点续传/全局速度图/失败重试），并能正确计算进度百分比
            try {
                let res = await (await fetch('/api/download_batch', {
                    method: 'POST', headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({
                        items: [{
                            type: 'file',
                            name: currentDLFile.filename,
                            files: [{ filename: currentDLFile.filename, url: currentDLFile.realUrl, size: 0 }]
                        }],
                        mode: 'termux'
                    })
                })).json();
                if (res.status === 'success') {
                    alert('✅ 已加入下载队列，请前往「队列」页面查看进度');
                } else {
                    alert('失败: ' + res.msg);
                }
            } catch(e) { alert('网络异常: ' + e.message); }
        }
    }

    function showBigImg(src) { document.getElementById('bigImgSrc').src = src; document.getElementById('bigImgModal').style.display = 'flex'; }
    
    // 【核心修复 Bug 11】改为从 btn.dataset 读取全部字段，不再依赖 SSR 字符串拼接
    async function saveToNetdisk(btn) {
        let identifier = btn.dataset.ident;
        let title = btn.dataset.title;
        let creator = btn.dataset.creator;
        let date = btn.dataset.date;
        let mediatype = btn.dataset.mediatype;
        if(!confirm(`确定将【${title}】存入网盘目录吗？\n（存入后可点「📂 文件」直接下载）`)) return;
        let old = btn.innerText; btn.innerText = '保存中...'; btn.disabled = true;
        let fd = new FormData();
        fd.append('identifier', identifier); fd.append('title', title); fd.append('creator', creator);
        fd.append('date', date); fd.append('mediatype', mediatype); fd.append('skip_upload', 'yes');
        try {
            let res = await (await fetch('/upload', { method: 'POST', body: fd })).json();
            if(res.status === 'success') { alert('✅ 保存成功！请前往主页查看。'); location.href = res.redirect; }
            else { alert('失败: ' + res.msg); btn.innerText = old; btn.disabled = false; }
        } catch(e) {
            alert('网络异常: ' + e.message);
            btn.innerText = old; btn.disabled = false;
        }
    }
</script>
{% endif %}
</body></html>
"""