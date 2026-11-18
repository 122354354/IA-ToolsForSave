# pages_detail_item.py
# 条目详情页 HTML（含文件列表、树状视图、ZIP 解压浏览、缩略图管理、预览、下载弹窗）
#
# 【⏱️ 延迟与超时精细控制 - 2026-11 R152 修复】
# 全文扫描确认共 3 处 AbortController 超时：
#   1. loadFiles(force) 里 setTimeout(..., 15000) → {{ detail_fetch_timeout_ms|default(15000) }}
#   2. refreshFiles() 里 setTimeout(..., 15000) → {{ detail_fetch_timeout_ms|default(15000) }}
#   3. previewZip(filename) 里 setTimeout(..., 30000) → {{ detail_zip_timeout_ms|default(30000) }}
#
# 【参数说明】
#   detail_fetch_timeout_ms (默认 15000，1000-120000ms)
#   → 详情页 file_cache 拉取 / force_refresh 请求的 AbortController 超时
#   detail_zip_timeout_ms   (默认 30000，3000-300000ms)
#   → 详情页 ZIP 结构解析请求的 AbortController 超时
#
# 【默认值对比（与原硬编码）】
#   - detail_fetch_timeout_ms: 15000 → 15000 ✅ 完全一致
#   - detail_zip_timeout_ms: 30000 → 30000 ✅ 完全一致
#
# 【为什么用 |default(...) 兜底】
#   第 16 个文件 netdisk_routes_views_manage.py 的 item_detail() 已传这两个参数。
#   但用 |default(...) 是防御性写法：
#     - 传参正常时用配置值（不影响功能）✅
#     - 未来若第 16 个文件重构误删传参 → 不会 Jinja UndefinedError → 页面仍可用 ✅
#     - 与 pages_index_main.py 的 previewZip 写法一致（R218 记录）✅
#
# 【未修改的位置（有意保留）】
#   - deleteSelectedFiles / renameByIdx / deleteByIdx 里 fetch 无 AbortController（原代码行为）
#   - triggerDownloadByIdx / previewByIdx 的 fetch 无 AbortController
#   - setTimeout(rs, 200) / setTimeout(rs, 150) 是浏览器下载间隔，非超时，保留
#   - setTimeout(() => loadFiles(true), 3000) / setTimeout(()=>loadFiles(true), 2000) 是延迟刷新，非超时，保留
#
# 【中间状态兼容】
#   - 第 16 个文件已传 detail_fetch_timeout_ms / detail_zip_timeout_ms ✅
#   - 顺序正确（第 16 → 第 4 次）✅
#
# 【📝 保留记录 - 2026-11，低优先，以后修】
#   R217：pages_index_main.py 的 refreshPicker 用 {{ detail_fetch_timeout_ms }} 无 default
#         （本文件的 loadFiles / refreshFiles 已加 default 兜底，更鲁棒）
#   R218：pages_index_main.py 的 previewZip 用 {{ detail_zip_timeout_ms|default(30000) }}
#   R219：Jinja default(30000) 只在 undefined 时生效（传 0/None 不会用 default），
#         配置范围不含这些值 → 安全
#   R220：详情页 3 处 AbortController 全部替换，无遗漏 ✅

ITEM_DETAIL_HTML = r"""
<!DOCTYPE html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">{{ s|safe }}
<style>
    .tree-row { display: flex; align-items: center; padding: 8px 0; gap: 8px; flex-wrap: wrap; }
    .tree-arrow { font-size: 12px; color: var(--primary); transition: 0.2s; display: inline-block; width: 14px; text-align: center; cursor: pointer; }
    .tree-children { display: none; margin-left: 15px; border-left: 1px dashed var(--border); padding-left: 10px; }
    .file-item-row { display: flex; flex-wrap: wrap; align-items: flex-start; padding: 12px; border-bottom: 1px solid var(--border); gap: 10px; background: var(--card-bg); transition: 0.2s; margin-bottom: 8px; border: 1px solid var(--border); border-radius: 6px;}
    .file-item-row:hover { background: rgba(0,0,0,0.02); }
    
    .tree-file-cb { width: 20px; height: 20px; margin: 2px 0 0 0; cursor: pointer; flex-shrink: 0; }
    .tree-folder-cb { position: static !important; width: 18px !important; height: 18px !important; margin: 0 8px 0 0 !important; cursor: pointer; flex-shrink: 0; }
    
    .file-info-col { flex: 1; min-width: 200px; word-break: break-all; }
    .file-actions-col { flex-basis: 100%; display: flex; gap: 8px; flex-wrap: wrap; padding-top: 8px; border-top: 1px dashed var(--border); }
    .preview-modal { display: none; position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.85); z-index: 10000; justify-content: center; align-items: center; flex-direction: column; }
    .preview-content { max-width: 90%; max-height: 80%; border-radius: 8px; box-shadow: 0 0 20px rgba(0,0,0,0.5); }
    .set-default-row { display: flex; align-items: center; justify-content: center; gap: 8px; padding: 8px; background: rgba(0,150,136,0.05); border-radius: 6px; margin: 10px 0; font-size: 13px; cursor: pointer; }
    .set-default-row input { width: 18px; height: 18px; margin: 0; }
</style>
</head><body>
{{ n|safe }}
<div class="card">
    <div style="margin-bottom:15px;"><button onclick="history.back()" class="btn btn-sm btn-gray">⬅️ 返回上一级</button></div>
    <div style="margin-bottom:20px; display:flex; gap:15px; flex-wrap:wrap; align-items:flex-start;">
        <div style="width:120px; text-align:center;">
            <div id="detailThumbBox" data-ident="{{ i }}" data-title="{{ t }}" style="width:100%; aspect-ratio: 1; background:#e0e0e0; border-radius:8px; border:1px solid var(--border); cursor:pointer; display:flex; align-items:center; justify-content:center; color:#888; font-size:14px; flex-direction:column; overflow:hidden;">
                {% if has_thumb %}
                <img src="/thumb/{{ i }}?t={{ range(1, 99999) | random }}" style="width:100%; height:100%; object-fit:cover;" onclick="showThumbModal(this.closest('#detailThumbBox').dataset.ident, this.closest('#detailThumbBox').dataset.title)">
                {% else %}
                <div onclick="loadDetailThumb(this.closest('#detailThumbBox').dataset.ident)" style="display:flex; flex-direction:column; align-items:center; justify-content:center; width:100%; height:100%;"><span>🖼️</span>点我加载</div>
                {% endif %}
            </div>
            <div style="font-size:12px; color:var(--blue); margin-top:5px; cursor:pointer;" data-ident="{{ i }}" data-title="{{ t }}" onclick="showThumbModal(this.dataset.ident, this.dataset.title)">点击管理缩略图</div>
        </div>
        <div style="flex:1; min-width:200px;">
            <h2 style="margin-top:0;">{{ t }}</h2>
            <p style="word-break: break-all; color:var(--gray); font-size:13px;">ID: {{ i }}</p>
            <div style="display:flex; gap:10px; flex-wrap:wrap;">
                <a href="https://archive.org/details/{{ i }}" target="_blank" class="btn btn-sm btn-blue">🌐 官方页</a>
                <button class="btn btn-sm btn-purple" onclick="showCombinedPreview()">👁️ 本地综合预览</button>
                <button class="btn btn-sm btn-gray" onclick="forceRefresh()">🔄 强制重拉直链</button>
            </div>
        </div>
    </div>
    <fieldset style="margin-bottom:15px;"><legend>🏷️ 快捷标签编辑 (Tag)</legend><div style="display:flex; gap:10px;"><input type="text" id="tags_input" value="{{ tags }}" style="margin:0;"><button class="btn btn-sm btn-blue" onclick="updateTags()" style="margin:0; width:80px;">保存</button></div><span class="tip" style="margin-top:10px;">修改后直接推送到 IA 官方，无需排队。多个标签用逗号隔开。</span></fieldset>
    
    <fieldset>
        <legend>📂 全部文件列表</legend>
        
        <div style="display:flex; justify-content:space-between; align-items:center; padding: 10px 0; border-bottom: 1px solid var(--border); flex-wrap:wrap; gap:10px;" id="detailToolbarWrapper">
            <div style="display:flex; gap:8px; flex-wrap:wrap;" id="detailToolbar">
                <button class="btn btn-sm btn-blue" id="view-toggle-btn" onclick="toggleFileView()">📁 切换树状视图</button>
                <button class="btn btn-sm btn-gray" onclick="selectAllFiles()">全选/取消</button>
                <button class="btn btn-sm btn-green" onclick="downloadSelectedFiles()">⬇️ 下载选中</button>
                <button class="btn btn-sm btn-red" onclick="deleteSelectedFiles()">🗑️ 删除选中</button>
            </div>
            <button class="btn btn-sm btn-orange" onclick="refreshFiles()" id="refreshBtn">🔄 重新提取</button>
        </div>
        
        <div id="files" style="margin-top:10px; max-height: 60vh; overflow-y:auto;">
            <p style="text-align:center; padding:20px;"><span class="spinner"></span>读取中...</p>
        </div>
    </fieldset>
</div>

<div class="modal-overlay" id="combinedPreviewModal">
    <div class="modal-content" style="max-width:800px; height:85vh; display:flex; flex-direction:column;">
        <div class="modal-title"><span>👁️ 本地综合预览</span><span class="modal-close" onclick="closeModal('combinedPreviewModal')">&times;</span></div>
        
        <div style="display:flex; justify-content:space-between; align-items:center; padding: 10px 0; border-bottom: 1px solid var(--border); flex-wrap:wrap; gap:10px; flex-shrink:0;">
            <div style="display:flex; gap:8px; flex-wrap:wrap;">
                <button class="btn btn-sm btn-blue" id="combined-view-toggle" onclick="toggleCombinedView()">📁 切换树状视图</button>
            </div>
            <span style="font-size:12px; color:var(--gray);">这里只展示可以预览的文件，点击文件名即可预览。</span>
        </div>

        <div id="combinedPreviewList" style="flex:1; overflow-y:auto; background:var(--bg); border-radius:8px; border:1px solid var(--border); padding:10px; margin-top:10px;">
            <p><span class="spinner"></span>读取中...</p>
        </div>
    </div>
</div>

<div class="modal-overlay" id="thumbModal">
    <div class="modal-content" style="max-width:450px; padding:10px;">
        <div class="modal-title"><span id="thumbTitle">🖼️ 缩略图</span><span class="modal-close" onclick="closeModal('thumbModal')">&times;</span></div>
        <div style="text-align:center; margin-bottom:15px;">
            <img id="thumbModalImg" src="" style="max-width:100%; max-height:50vh; border-radius:8px; object-fit:contain;">
        </div>
        <div style="display:flex; flex-wrap:wrap; gap:8px; justify-content:center;">
            <button class="btn btn-sm btn-blue" onclick="refreshThumb()" style="flex:1; min-width:45%; margin:0;">🔄 重新获取</button>
            <a id="thumbDownloadBtn" href="" download class="btn btn-sm btn-green" style="flex:1; min-width:45%; margin:0;">💾 保存图片</a>
            <button class="btn btn-sm btn-orange" onclick="document.getElementById('thumbFileInput').click()" style="flex:1; min-width:45%; margin:0;">🖼️ 自定义</button>
            <button class="btn btn-sm btn-gray" onclick="closeModal('thumbModal')" style="flex:1; min-width:45%; margin:0;">❌ 关闭</button>
        </div>
        <input type="file" id="thumbFileInput" accept="image/*" style="display:none;" onchange="uploadCustomThumb()">
        <div id="thumbUploadStatus" style="text-align:center; font-size:12px; color:var(--primary); margin-top:10px;"></div>
    </div>
</div>

<div class="modal-overlay" id="downloadChoiceModal">
    <div class="modal-content" style="max-width:380px; text-align:center;">
        <div class="modal-title" style="justify-content:center;">⬇️ 选择下载方式</div>
        <div class="modal-body" style="margin-bottom:15px; font-size:14px; color:var(--gray);">
            请选择如何下载此文件：
        </div>
        <div style="display:flex; flex-direction:column; gap:10px;">
            <button class="btn btn-blue" onclick="confirmDownload('termux')" style="margin:0; padding:12px;">📱 Termux 后台下载 (推荐)</button>
            <button class="btn btn-green" onclick="confirmDownload('browser')" style="margin:0; padding:12px;">🌐 浏览器直接下载</button>
            <button class="btn btn-gray" onclick="closeModal('downloadChoiceModal')" style="margin:0; padding:12px;">❌ 取消</button>
        </div>
        <label class="set-default-row" onclick="event.stopPropagation();">
            <input type="checkbox" id="setAsDefaultSingle">
            <span>设为默认（以后单文件不再询问）</span>
        </label>
    </div>
</div>

<div class="modal-overlay" id="batchDownloadChoiceModal">
    <div class="modal-content" style="max-width:420px; text-align:center;">
        <div class="modal-title" style="justify-content:center;">⬇️ 批量下载方式</div>
        <div class="modal-body" style="margin-bottom:15px; font-size:14px; color:var(--gray);" id="batchDownloadInfo">
            即将提交下载任务...
        </div>
        <div style="display:flex; flex-direction:column; gap:10px;">
            <button class="btn btn-blue" onclick="confirmBatchDownload('termux')" style="margin:0; padding:12px;">📱 全部走 Termux 后台队列</button>
            <button class="btn btn-green" onclick="confirmBatchDownload('browser')" style="margin:0; padding:12px;">🌐 全部走浏览器直接下载</button>
            <button class="btn btn-gray" onclick="closeModal('batchDownloadChoiceModal')" style="margin:0; padding:12px;">❌ 取消</button>
        </div>
        <label class="set-default-row" onclick="event.stopPropagation();">
            <input type="checkbox" id="setAsDefaultBatch">
            <span>设为默认（以后批量下载不再询问）</span>
        </label>
    </div>
</div>

<div class="modal-overlay" id="loadingModal" style="background: rgba(0,0,0,0.7);">
    <div class="modal-content" style="max-width:250px; text-align:center; padding:20px;">
        <div class="spinner" style="width:40px; height:40px; margin-bottom:15px; border-width:4px;"></div>
        <div style="font-size:15px; font-weight:bold; color:var(--primary-dark);" id="loadingText">正在向代理请求数据...</div>
    </div>
</div>

<div class="preview-modal" id="onlinePreviewModal" onclick="if(event.target.id==='onlinePreviewModal')closeOnlinePreview()">
    <div style="position:absolute; top:20px; right:20px; color:white; font-size:30px; cursor:pointer;" onclick="closeOnlinePreview()">&times;</div>
    <div id="onlinePreviewContainer" style="display:flex; justify-content:center; align-items:center; width:100%; height:100%;"></div>
</div>

<script>
    const IDENTIFIER = "{{ i }}"; 
    const AUTO_FETCH_DETAIL = {{ 'true' if auto_fetch_detail else 'false' }}; 
    const PROXY_DL = {{ 'true' if proxy_dl else 'false' }}; 
    const DL_DIR = "{{ dl_dir }}";

    let SINGLE_DOWNLOAD_DEFAULT = localStorage.getItem('single_download_default') || "{{ single_download_default }}";
    let BATCH_DOWNLOAD_DEFAULT = localStorage.getItem('batch_download_default') || "{{ batch_download_default }}";

    function closeModal(id) { document.getElementById(id).style.display = 'none'; }

    function buildEncDownloadUrl(identifier, filename) {
        let encIdent = encodeURIComponent(identifier);
        let encPath = String(filename || '').split('/').map(encodeURIComponent).join('/');
        return `https://archive.org/download/${encIdent}/${encPath}`;
    }

    // 【P1-4】取相对 folderPath 的路径，避免后端拼出 "Download/文件夹名/完整/路径" 重复
    function _getRelName(fullName, folderPath) {
        if (fullName === folderPath) return fullName.split('/').pop();
        return fullName.substring(folderPath.length + 1);
    }

    function intSafe(v) {
        let n = parseInt(v);
        return isNaN(n) ? 0 : n;
    }

    function loadDetailThumb(identifier) {
        let box = document.getElementById('detailThumbBox');
        if (!box) return;
        let title = box.dataset.title || '';
        box.innerHTML = '<span class="spinner"></span>';
        let img = new Image();
        img.onload = () => { 
            box.innerHTML = ''; 
            img.style.width='100%'; 
            img.style.height='100%'; 
            img.style.objectFit='cover'; 
            img.style.cursor='pointer'; 
            img.onclick = () => showThumbModal(identifier, title); 
            box.appendChild(img); 
        };
        img.onerror = () => { box.innerHTML = '❌无图'; };
        img.src = '/thumb/' + encodeURIComponent(identifier) + '?t=' + new Date().getTime();
    }

    let currentFilesData = [];
    let flatFilesList = []; 
    let isTreeView = false;
    
    let defaultDetailToolbarHtml = document.getElementById('detailToolbar').innerHTML;
    function restoreDetailToolbar() {
        document.getElementById('detailToolbar').innerHTML = defaultDetailToolbarHtml;
        document.getElementById('refreshBtn').style.display = 'inline-block';
        isTreeView = false;
    }

    async function updateTags() { 
        let tags = document.getElementById('tags_input').value.trim(); 
        let res = await (await fetch('/api/update_tags', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({identifier: IDENTIFIER, tags: tags})})).json(); 
        if(res.status === 'success') alert('✅ 标签更新成功'); 
        else alert('❌ 失败: ' + res.msg); 
    }
    
    async function loadFiles(force) {
        const c = document.getElementById('files'); 
        c.innerHTML = '<p style="text-align:center; padding:20px;"><span class="spinner"></span>读取中...</p>';
        try {
            // 【R152 修复】AbortController 15000 → {{ detail_fetch_timeout_ms|default(15000) }}
            let controller = new AbortController(); let timeoutId = setTimeout(() => controller.abort(), {{ detail_fetch_timeout_ms|default(15000) }});
            let d = await (await fetch(`/api/file_cache/${IDENTIFIER}`, {signal: controller.signal})).json(); clearTimeout(timeoutId);
            
            if (d.cached && d.files && d.files.length > 0) { 
                currentFilesData = d.files;
                renderFiles(currentFilesData); 
                return; 
            }
            if (!force && !AUTO_FETCH_DETAIL) { 
                c.innerHTML = '<p style="text-align:center; padding:20px;">本地暂无记录，请点击右上角「🔄 重新提取」。</p>'; 
                return; 
            }
            if (!force) { 
                await refreshFiles();
                return;
            }
            c.innerHTML = '<p style="text-align:center; padding:20px;">本地暂无记录，请点击右上角「🔄 重新提取」。</p>';
        } catch(e) {
            if(e.name === 'AbortError') c.innerHTML = '<p style="color:red; text-align:center; padding:20px;">❌ 请求超时，可能是代理断开，请检查网络</p>';
            else c.innerHTML = '<p style="color:red; text-align:center; padding:20px;">❌ 网络异常或代理断开，请检查网络后重试</p>';
        }
    }

    async function refreshFiles() {
        document.getElementById('files').innerHTML = '<p style="text-align:center; padding:20px;"><span class="spinner"></span>获取中...</p>'; 
        try {
            // 【R152 修复】AbortController 15000 → {{ detail_fetch_timeout_ms|default(15000) }}
            let controller = new AbortController(); let timeoutId = setTimeout(() => controller.abort(), {{ detail_fetch_timeout_ms|default(15000) }});
            let r = await (await fetch(`/api/force_refresh/${IDENTIFIER}`, {signal: controller.signal})).json(); 
            clearTimeout(timeoutId);
            if (r.status === 'cooldown') alert(r.msg); 
            loadFiles(true);
        } catch(e) {
            if(e.name === 'AbortError') alert('获取超时');
            loadFiles(true);
        }
    }

    function toggleFileView() {
        isTreeView = !isTreeView;
        document.getElementById('view-toggle-btn').innerText = isTreeView ? '📄 切换平铺视图' : '📁 切换树状视图';
        renderFiles(currentFilesData);
    }

    function selectAllFiles() {
        let cbs = document.querySelectorAll('.tree-file-cb, .tree-folder-cb');
        let allChecked = Array.from(cbs).every(cb => cb.checked);
        cbs.forEach(cb => cb.checked = !allChecked);
    }

    async function deleteSelectedFiles() {
        let checked = Array.from(document.querySelectorAll('.tree-file-cb:checked')).map(cb => cb.value);
        if (checked.length === 0) return alert('请先勾选要删除的文件');
        if (!confirm(`确定要彻底删除这 ${checked.length} 个文件吗？\n（后台会进队列排队，并发槽满时自动等待，支持暂停接替）`)) return;
        
        let btn = event.target;
        let old = btn.innerText;
        btn.innerText = '提交中...';
        btn.disabled = true;

        try {
            let res = await (await fetch('/api/delete_batch', {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    groups: [{ identifier: IDENTIFIER, title: IDENTIFIER, filenames: checked }]
                })
            })).json();
            if (res.status === 'success') {
                alert(`✅ 已提交 ${res.count} 个删除任务，请前往「队列」页面查看进度`);
                setTimeout(() => loadFiles(true), 2000);
            } else {
                alert('失败: ' + res.msg);
            }
        } catch(e) { alert('网络异常'); }
        btn.innerText = old; btn.disabled = false;
    }

    function downloadSelectedFiles() {
        if (currentZipName) { downloadSelectedZip(); return; }
        
        let selectedFiles = Array.from(document.querySelectorAll('.tree-file-cb:checked')).map(cb => cb.value);
        let selectedFolders = Array.from(document.querySelectorAll('.tree-folder-cb:checked')).map(cb => cb.dataset.path);
        
        if (selectedFiles.length === 0 && selectedFolders.length === 0) return alert('请先勾选要下载的文件或文件夹');

        let items = [];
        let folder_count = 0, file_count = 0;
        selectedFolders.forEach(folderPath => {
            let folderFiles = [];
            currentFilesData.forEach(f => {
                if (f.filename.startsWith(folderPath + '/') || f.filename === folderPath) {
                    let real_u = f.custom_direct_url || buildEncDownloadUrl(IDENTIFIER, f.filename);
                    // 【P1-4 修复】filename 用相对 folderPath 的路径
                    folderFiles.push({ filename: _getRelName(f.filename, folderPath), url: real_u, size: intSafe(f.size) });
                }
            });
            if (folderFiles.length > 0) {
                items.push({ type: 'folder', name: folderPath.split('/').pop(), files: folderFiles });
                folder_count++;
            }
        });

        // 【待修-1 修复】收集所有被文件夹覆盖的文件路径，避免文件夹内文件被重复下载
        let filesCoveredByFolders = new Set();
        selectedFolders.forEach(folderPath => {
            currentFilesData.forEach(f => {
                if (f.filename.startsWith(folderPath + '/') || f.filename === folderPath) {
                    filesCoveredByFolders.add(f.filename);
                }
            });
        });

        if (selectedFiles.length > 0) {
            let fileObjs = [];
            selectedFiles.forEach(fname => {
                if (filesCoveredByFolders.has(fname)) return;
                let f = currentFilesData.find(item => item.filename === fname);
                if (f) {
                    let real_u = f.custom_direct_url || buildEncDownloadUrl(IDENTIFIER, f.filename);
                    fileObjs.push({ filename: f.filename, url: real_u, size: intSafe(f.size) });
                }
            });
            if (fileObjs.length > 0) {
                let itemName = fileObjs.length === 1 ? fileObjs[0].filename.split('/').pop() : `选中的 ${fileObjs.length} 个文件`;
                items.push({ type: 'file', name: itemName, files: fileObjs });
                file_count = fileObjs.length;
            }
        }

        if (items.length === 0) return alert('没有可下载的内容');

        if (BATCH_DOWNLOAD_DEFAULT === 'termux') {
            doBatchDownload(items, 'termux', false);
        } else if (BATCH_DOWNLOAD_DEFAULT === 'browser') {
            doBatchDownload(items, 'browser', false);
        } else if (BATCH_DOWNLOAD_DEFAULT === 'per_file') {
            items.forEach(it => {
                it.files.forEach(f => {
                    let proxy_u = PROXY_DL ? '/api/download_proxy?url=' + encodeURIComponent(f.url) : f.url;
                    triggerDownload(f.filename.split('/').pop(), f.url, proxy_u, f.size);
                });
            });
        } else {
            document.getElementById('batchDownloadInfo').innerHTML = 
                `即将提交下载任务：<br><b style="color:var(--primary);">${folder_count} 个文件夹</b> + <b style="color:var(--primary);">${file_count} 个文件</b>`;
            window._pendingBatchItems = items;
            document.getElementById('setAsDefaultBatch').checked = false;
            document.getElementById('batchDownloadChoiceModal').style.display = 'flex';
        }
    }

    async function confirmBatchDownload(mode) {
        let setAsDefault = document.getElementById('setAsDefaultBatch').checked;
        let items = window._pendingBatchItems || [];
        closeModal('batchDownloadChoiceModal');
        window._pendingBatchItems = null;
        if (items.length === 0) return;
        await doBatchDownload(items, mode, setAsDefault);
    }

    async function doBatchDownload(items, mode, setAsDefault) {
        if (setAsDefault) {
            localStorage.setItem('batch_download_default', mode);
            BATCH_DOWNLOAD_DEFAULT = mode;
        }

        if (mode === 'browser') {
            let totalFiles = 0;
            for (let it of items) {
                for (let f of it.files) {
                    let proxy_u = PROXY_DL ? '/api/download_proxy?url=' + encodeURIComponent(f.url) : f.url;
                    let a = document.createElement('a');
                    a.href = proxy_u;
                    a.download = f.filename.split('/').pop();
                    a.target = '_blank';
                    document.body.appendChild(a);
                    a.click();
                    document.body.removeChild(a);
                    totalFiles++;
                    await new Promise(rs => setTimeout(rs, 200));
                }
            }
            try {
                await fetch('/api/download_batch', {
                    method: 'POST', headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({items: items, mode: 'browser'})
                });
            } catch(e) {}
            // 【P2-8 修复】原文案会误导用户去队列找虚拟任务，实际 pages_queue.py 会过滤 is_virtual
            alert(`✅ 已发送 ${totalFiles} 个文件到浏览器下载\n（浏览器下载不占用网盘队列，仅后端保留记录用于回溯）`);
            if (currentZipName) closeZipView();
        } else {
            try {
                let res = await (await fetch('/api/download_batch', {
                    method: 'POST', headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({items: items, mode: 'termux'})
                })).json();
                if (res.status === 'success') {
                    alert(`✅ 已提交 ${res.count} 个下载任务，请前往「队列」查看进度\n（大卡片会排队，支持暂停接替）`);
                    if (currentZipName) closeZipView();
                } else {
                    alert('失败: ' + res.msg);
                }
            } catch(e) {
                alert('网络异常: ' + e.message);
            }
        }
    }

    function formatSize(bytes) { if(!bytes || isNaN(bytes)) return '未知'; bytes = parseInt(bytes); if(bytes < 1024) return bytes + ' B'; if(bytes < 1048576) return (bytes/1024).toFixed(1) + ' KB'; if(bytes < 1073741824) return (bytes/1048576).toFixed(1) + ' MB'; return (bytes/1073741824).toFixed(2) + ' GB'; }

    function buildTree(files) {
        let root = {};
        files.forEach(f => {
            let parts = f.filename.split('/');
            let current = root;
            for (let i = 0; i < parts.length; i++) {
                let part = parts[i];
                if (i === parts.length - 1) {
                    current[part] = { _isFile: true, data: f };
                } else {
                    if (!current[part]) current[part] = { _isFolder: true, children: {} };
                    current = current[part].children;
                }
            }
        });
        return root;
    }

    function toggleTreeRow(id, arrowEl) {
        let box = document.getElementById('tree_' + id);
        let arrow = document.getElementById('arrow_' + id);
        if(!box) return;
        if (box.style.display === 'block') {
            box.style.display = 'none';
            if(arrow) arrow.style.transform = 'rotate(0deg)';
        } else {
            box.style.display = 'block';
            if(arrow) arrow.style.transform = 'rotate(90deg)';
        }
    }

    function toggleFolderCheckbox(cb, id) {
        let box = document.getElementById('tree_' + id);
        if(!box) return;
        let subCbs = box.querySelectorAll('input.tree-file-cb, input.tree-folder-cb');
        subCbs.forEach(sub => { sub.checked = cb.checked; });
    }

    // 【P1-4 修复】文件夹内文件名传相对路径
    function downloadFolderEvent(btn, event) {
        event.stopPropagation();
        let folderPath = btn.dataset.folderpath;
        let folderFiles = [];
        currentFilesData.forEach(f => {
            if (f.filename.startsWith(folderPath + '/') || f.filename === folderPath) {
                folderFiles.push({
                    filename: _getRelName(f.filename, folderPath),
                    url: f.custom_direct_url || buildEncDownloadUrl(IDENTIFIER, f.filename),
                    size: intSafe(f.size)
                });
            }
        });
        downloadSingleFolder(folderPath.split('/').pop(), folderFiles);
    }

    // 【一致性修复】补全 per_file / ask 分支，与 pages_index.py 行为一致
    async function downloadSingleFolder(folderName, folderFiles) {
        if (!confirm(`确定要下载文件夹【${folderName}】吗？\n包含 ${folderFiles.length} 个文件，将在后台自动建立目录结构。`)) return;
        let items = [{ type: 'folder', name: folderName, files: folderFiles }];
        
        if (BATCH_DOWNLOAD_DEFAULT === 'browser') {
            await doBatchDownload(items, 'browser', false);
        } else if (BATCH_DOWNLOAD_DEFAULT === 'termux') {
            await doBatchDownload(items, 'termux', false);
        } else if (BATCH_DOWNLOAD_DEFAULT === 'per_file') {
            for (let f of folderFiles) {
                let real_u = f.url || buildEncDownloadUrl(IDENTIFIER, f.filename);
                let proxy_u = PROXY_DL ? '/api/download_proxy?url=' + encodeURIComponent(real_u) : real_u;
                triggerDownload(f.filename.split('/').pop(), real_u, proxy_u, intSafe(f.size));
                await new Promise(rs => setTimeout(rs, 150));
            }
        } else {
            document.getElementById('batchDownloadInfo').innerHTML = 
                `即将提交下载任务：<br><b style="color:var(--primary);">1 个文件夹</b>（共 <b style="color:var(--primary);">${folderFiles.length} 个文件</b>）`;
            window._pendingBatchItems = items;
            document.getElementById('setAsDefaultBatch').checked = false;
            document.getElementById('batchDownloadChoiceModal').style.display = 'flex';
        }
    }

    function renderTree(node, pathStr = '') {
        let html = '';
        let keys = Object.keys(node).sort((a,b) => {
            let aF = node[a]._isFolder, bF = node[b]._isFolder;
            if (aF && !bF) return -1; if (!aF && bF) return 1; return a.localeCompare(b);
        });

        keys.forEach(k => {
            if (node[k]._isFolder) {
                let folderPath = pathStr ? pathStr + '/' + k : k;
                let folderId = 'folder_' + Math.random().toString(36).substr(2, 9);
                html += `<div style="margin-bottom:5px;">
                    <div class="tree-row">
                        <span class="tree-arrow" id="arrow_${folderId}" onclick="toggleTreeRow('${folderId}', this)">▶</span>
                        <input type="checkbox" class="tree-folder-cb" data-path="${escapeHtml(folderPath)}" onclick="event.stopPropagation(); toggleFolderCheckbox(this, '${folderId}')">
                        <span style="font-weight:bold; word-break:break-all; flex:1; cursor:pointer;" onclick="toggleTreeRow('${folderId}', document.getElementById('arrow_${folderId}'))">📁 ${escapeHtml(k)}</span>
                        <button class="btn btn-sm btn-green" style="padding:2px 8px; font-size:11px;" data-folderpath="${escapeHtml(folderPath)}" onclick="downloadFolderEvent(this, event)">⬇️ 下载</button>
                    </div>
                    <div class="tree-children" id="tree_${folderId}">
                        ${renderTree(node[k].children, folderPath)}
                    </div>
                </div>`;
            } else {
                html += renderFileRow(node[k].data, true);
            }
        });
        return html;
    }

    function renderFileRow(f, isTree = false) {
        let idx = flatFilesList.length;
        flatFilesList.push(f);
        
        let real_u = f.custom_direct_url || buildEncDownloadUrl(IDENTIFIER, f.filename);
        let szStr = formatSize(f.size); 
        let displayName = isTree ? f.filename.split('/').pop() : f.filename;
        
        let ext = f.filename.split('.').pop().toLowerCase();
        let imgExts = ['jpg', 'jpeg', 'png', 'gif', 'webp', 'bmp', 'svg'];
        let vidExts = ['mp4', 'webm', 'mkv', 'mov', 'avi', 'm3u8'];
        let audExts = ['mp3', 'wav', 'ogg', 'm4a', 'flac', 'aac'];
        let zipExts = ['zip', 'rar', '7z', 'tar', 'gz'];
        
        let previewBtn = '';
        if (imgExts.includes(ext) || vidExts.includes(ext) || audExts.includes(ext)) {
            previewBtn = `<button class="btn btn-sm btn-blue" onclick="previewByIdx(${idx})">👁️ 预览</button>`;
        } else if (zipExts.includes(ext)) {
            previewBtn = `<button class="btn btn-sm btn-blue" onclick="previewZipByIdx(${idx})">📦 解压浏览</button>`;
        } else {
            previewBtn = `<button class="btn btn-sm btn-gray" onclick="alert('该格式暂不支持预览')">❌ 无法预览</button>`;
        }

        return `<div class="file-item-row">
            <input type="checkbox" class="tree-file-cb" value="${escapeHtml(f.filename)}">
            <div class="file-info-col">
                <span style="font-weight:bold; font-size:14px; display:block; margin-bottom:2px;">📄 ${escapeHtml(displayName)}</span>
                <span style="color:var(--gray); font-size:12px;">📦 ${szStr}</span>
            </div>
            <div class="file-actions-col">
                ${previewBtn}
                <button class="btn btn-sm btn-green" onclick="triggerDownloadByIdx(${idx})">⬇️ 下载</button>
                <button class="btn btn-sm btn-gray" onclick="copyTextByIdx(${idx})">📋 复制直链</button>
                <button class="btn btn-sm btn-orange" onclick="renameByIdx(${idx})">✏️ 重命名</button>
                <button class="btn btn-sm btn-red" onclick="deleteByIdx(${idx})">🗑️ 删除</button>
            </div>
        </div>`;
    }

    function renderFiles(files) { 
        flatFilesList = [];
        if (!files || files.length === 0) {
            document.getElementById('files').innerHTML = '<p style="text-align:center; padding:20px;">没有文件记录</p>';
            return;
        }
        if (isTreeView) {
            let tree = buildTree(files);
            document.getElementById('files').innerHTML = `<div style="margin-top:10px;">${renderTree(tree)}</div>`;
        } else {
            let html = `<div style="margin-top:10px;">`;
            files.forEach(f => { html += renderFileRow(f, false); });
            html += `</div>`;
            document.getElementById('files').innerHTML = html;
        }
    }

    function previewByIdx(idx) { previewFile(flatFilesList[idx].filename); }
    function previewZipByIdx(idx) { previewZip(flatFilesList[idx].filename); }
    
    function triggerDownloadByIdx(idx) { 
        let f = flatFilesList[idx];
        let real_u = f.custom_direct_url || buildEncDownloadUrl(IDENTIFIER, f.filename);
        let proxy_u = PROXY_DL ? '/api/download_proxy?url=' + encodeURIComponent(real_u) : real_u;
        triggerDownload(f.filename.split('/').pop(), real_u, proxy_u, intSafe(f.size)); 
    }
    
    function copyTextByIdx(idx) {
        let f = flatFilesList[idx];
        let real_u = f.custom_direct_url || buildEncDownloadUrl(IDENTIFIER, f.filename);
        copyText(real_u);
    }

    // 【P1-3 修复】后端 api_rename_file 读的是 rename_list: [{old_name, new_name}]
    async function renameByIdx(idx) { 
        let f = flatFilesList[idx];
        let oldName = f.filename; 
        let nn = prompt('新名称：', oldName); 
        if(!nn || nn === oldName) return; 
        let res = await (await fetch('/api/rename_file', {
            method:'POST', 
            headers:{'Content-Type':'application/json'}, 
            body:JSON.stringify({
                identifier: IDENTIFIER, 
                rename_list: [{old_name: oldName, new_name: nn}]
            })
        })).json(); 
        if(res.status === 'success'){
            alert('✅ ' + (res.msg || '已提交重命名任务'));
            setTimeout(() => loadFiles(true), 3000);
        } else {
            alert(res.msg || '失败');
        }
    }
    async function deleteByIdx(idx) { 
        let f = flatFilesList[idx];
        let fn = f.filename; 
        if(!confirm(`确定要彻底删除文件【${fn}】吗？`)) return; 
        let res = await (await fetch('/api/delete_batch', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({groups: [{identifier: IDENTIFIER, title: IDENTIFIER, filenames: [fn]}]})})).json(); 
        if(res.status==='success'){ alert(`已提交 ${res.count} 个删除任务，请前往队列查看`); setTimeout(()=>loadFiles(true), 2000); } else alert(res.msg||'失败'); 
    }

    function previewFile(filename, ident = null) {
        let targetIdent = ident || IDENTIFIER;
        let ext = filename.split('.').pop().toLowerCase();
        let encodedFn = filename.split('/').map(encodeURIComponent).join('/');
        let url = PROXY_DL ? `/api/preview?identifier=${encodeURIComponent(targetIdent)}&filename=${encodedFn}` : `https://archive.org/download/${encodeURIComponent(targetIdent)}/${encodedFn}`;
        let container = document.getElementById('onlinePreviewContainer');
        
        let imgExts = ['jpg', 'jpeg', 'png', 'gif', 'webp', 'bmp', 'svg'];
        let vidExts = ['mp4', 'webm', 'mkv', 'mov', 'avi', 'm3u8'];
        let audExts = ['mp3', 'wav', 'ogg', 'm4a', 'flac', 'aac'];
        
        if (imgExts.includes(ext)) {
            container.innerHTML = `<img src="${url}" class="preview-content">`;
        } else if (vidExts.includes(ext)) {
            container.innerHTML = `<video src="${url}" controls autoplay class="preview-content"></video>`;
        } else if (audExts.includes(ext)) {
            container.innerHTML = `<audio src="${url}" controls autoplay style="width:80%;"></audio>`;
        } else {
            alert('该文件格式暂不支持网页内直接预览，请下载后查看。');
            return;
        }
        document.getElementById('onlinePreviewModal').style.display = 'flex';
    }

    function closeOnlinePreview() {
        document.getElementById('onlinePreviewModal').style.display = 'none';
        document.getElementById('onlinePreviewContainer').innerHTML = '';
    }

    let combinedFilesData = [];
    let isCombinedTreeView = false;

    function showCombinedPreview() {
        if (!currentFilesData || currentFilesData.length === 0) {
            alert('暂无文件，请点击「🔄 重新提取」。');
            return;
        }
        
        let imgExts = ['jpg', 'jpeg', 'png', 'gif', 'webp', 'bmp', 'svg'];
        let vidExts = ['mp4', 'webm', 'mkv', 'mov', 'avi', 'm3u8'];
        let audExts = ['mp3', 'wav', 'ogg', 'm4a', 'flac', 'aac'];
        let zipExts = ['zip', 'rar', '7z', 'tar', 'gz'];
        
        combinedFilesData = currentFilesData.filter(f => {
            let ext = f.filename.split('.').pop().toLowerCase();
            return imgExts.includes(ext) || vidExts.includes(ext) || audExts.includes(ext) || zipExts.includes(ext);
        });
        
        if (combinedFilesData.length === 0) {
            alert('该条目下没有支持预览的文件。');
            return;
        }
        
        isCombinedTreeView = false;
        document.getElementById('combined-view-toggle').innerText = '📁 切换树状视图';
        renderCombinedFiles();
        document.getElementById('combinedPreviewModal').style.display = 'flex';
    }

    function toggleCombinedView() {
        isCombinedTreeView = !isCombinedTreeView;
        document.getElementById('combined-view-toggle').innerText = isCombinedTreeView ? '📄 切换平铺视图' : '📁 切换树状视图';
        renderCombinedFiles();
    }

    function renderCombinedFiles() {
        let container = document.getElementById('combinedPreviewList');
        if (!combinedFilesData.length) { container.innerHTML = '<p>无</p>'; return; }

        let imgExts = ['jpg', 'jpeg', 'png', 'gif', 'webp', 'bmp', 'svg'];
        let vidExts = ['mp4', 'webm', 'mkv', 'mov', 'avi', 'm3u8'];
        let audExts = ['mp3', 'wav', 'ogg', 'm4a', 'flac', 'aac'];
        let zipExts = ['zip', 'rar', '7z', 'tar', 'gz'];

        if (isCombinedTreeView) {
            let tree = buildTree(combinedFilesData);
            container.innerHTML = `<div style="margin-top:10px;">${renderCombinedTree(tree)}</div>`;
        } else {
            let html = '<div style="display:flex; flex-direction:column; gap:10px;">';
            combinedFilesData.forEach((f, idx) => {
                let ext = f.filename.split('.').pop().toLowerCase();
                let icon = imgExts.includes(ext) ? '🖼️' : (vidExts.includes(ext) ? '🎬' : (audExts.includes(ext) ? '🎵' : '📦'));
                let szStr = formatSize(f.size);
                
                html += `<div class="file-item-row" style="cursor:pointer; border:1px solid var(--border); border-radius:8px; padding:10px;" onclick="combinedFileClickByIdx(${idx})">
                    <div style="flex:1; word-break:break-all;">
                        <span style="font-size:20px; margin-right:10px;">${icon}</span>
                        <span style="font-weight:bold; font-size:14px;">${escapeHtml(f.filename)}</span>
                    </div>
                    <span style="color:var(--gray); font-size:12px;">${szStr}</span>
                </div>`;
            });
            html += '</div>';
            container.innerHTML = html;
        }
    }

    function renderCombinedTree(node, pathStr = '') {
        let html = '';
        let keys = Object.keys(node).sort((a,b) => {
            let aF = node[a]._isFolder, bF = node[b]._isFolder;
            if (aF && !bF) return -1; if (!aF && bF) return 1; return a.localeCompare(b);
        });

        let imgExts = ['jpg', 'jpeg', 'png', 'gif', 'webp', 'bmp', 'svg'];
        let vidExts = ['mp4', 'webm', 'mkv', 'mov', 'avi', 'm3u8'];
        let audExts = ['mp3', 'wav', 'ogg', 'm4a', 'flac', 'aac'];
        let zipExts = ['zip', 'rar', '7z', 'tar', 'gz'];

        keys.forEach(k => {
            if (node[k]._isFolder) {
                let folderId = 'comb_' + Math.random().toString(36).substr(2, 9);
                html += `<div style="margin-bottom:5px;">
                    <div class="tree-row">
                        <span class="tree-arrow" id="arrow_${folderId}" onclick="toggleTreeRow('${folderId}', this)">▶</span>
                        <span style="font-weight:bold; word-break:break-all; flex:1; cursor:pointer;" onclick="toggleTreeRow('${folderId}', document.getElementById('arrow_${folderId}'))">📁 ${escapeHtml(k)}</span>
                    </div>
                    <div class="tree-children" id="tree_${folderId}">
                        ${renderCombinedTree(node[k].children, pathStr ? pathStr + '/' + k : k)}
                    </div>
                </div>`;
            } else {
                let f = node[k].data;
                let ext = f.filename.split('.').pop().toLowerCase();
                let icon = imgExts.includes(ext) ? '🖼️' : (vidExts.includes(ext) ? '🎬' : (audExts.includes(ext) ? '🎵' : '📦'));
                let szStr = formatSize(f.size);
                
                let idx = combinedFilesData.findIndex(item => item.filename === f.filename);
                if (idx < 0) idx = 0;

                html += `<div class="file-item-row" style="border:none; padding:8px; cursor:pointer;" onclick="combinedFileClickByIdx(${idx})">
                    <div style="flex:1; word-break:break-all;">
                        <span style="font-size:16px; margin-right:8px;">${icon}</span>
                        <span style="font-weight:bold; font-size:13px;">${escapeHtml(f.filename.split('/').pop())}</span>
                    </div>
                    <span style="color:var(--gray); font-size:11px;">${szStr}</span>
                </div>`;
            }
        });
        return html;
    }

    function combinedFileClickByIdx(idx) {
        let f = combinedFilesData[idx];
        if(!f) return;
        let ext = f.filename.split('.').pop().toLowerCase();
        let isZip = ['zip', 'rar', '7z', 'tar', 'gz'].includes(ext);
        if (isZip) {
            document.getElementById('combinedPreviewModal').style.display = 'none';
            previewZip(f.filename);
        } else {
            previewFile(f.filename);
        }
    }

    let zipFilesData = [];
    let isZipTreeView = false;
    let currentZipName = "";

    async function previewZip(filename) {
        if (!confirm(`即将向官方请求解析压缩包【${filename}】的内部结构。\n(此过程仅消耗极少流量，请稍候)`)) return;
        
        document.getElementById('loadingText').innerText = '正在向代理请求 ZIP 目录结构...';
        document.getElementById('loadingModal').style.display = 'flex';
        
        try {
            // 【R152 修复】AbortController 30000 → {{ detail_zip_timeout_ms|default(30000) }}
            let controller = new AbortController(); let timeoutId = setTimeout(() => controller.abort(), {{ detail_zip_timeout_ms|default(30000) }});
            let res = await (await fetch('/api/zip_content', {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({identifier: IDENTIFIER, filename: filename}),
                signal: controller.signal
            })).json();
            clearTimeout(timeoutId);
            
            document.getElementById('loadingModal').style.display = 'none';
            
            if (res.status === 'success' && res.files && res.files.length > 0) {
                zipFilesData = res.files.map(f => ({filename: f.filename, size: f.size}));
                currentZipName = filename; 
                isZipTreeView = true; 
                
                let toolbarHtml = `
                    <button class="btn btn-sm btn-blue" id="view-toggle-btn" onclick="toggleZipView()">📄 切换平铺视图</button>
                    <button class="btn btn-sm btn-gray" onclick="selectAllZipFiles()">全选/取消</button>
                    <button class="btn btn-sm btn-green" onclick="downloadSelectedZip()">⬇️ 下载选中</button>
                    <span style="font-size:14px; font-weight:bold; margin-left:10px; word-break:break-all;">📦 ${escapeHtml(filename)}</span>
                    <button class="btn btn-sm btn-gray" onclick="closeZipView()" style="margin-left:auto;">❌ 返回文件列表</button>
                `;
                let toolbarContainer = document.getElementById('detailToolbar');
                if (toolbarContainer) toolbarContainer.innerHTML = toolbarHtml;
                document.getElementById('refreshBtn').style.display = 'none';

                renderZipFiles();
            } else {
                alert('解析失败: ' + (res.msg || 'ZIP 结构为空或解析错误'));
            }
        } catch(e) {
            document.getElementById('loadingModal').style.display = 'none';
            if(e.name === 'AbortError') alert('解析超时，可能是代理节点无法获取文件尾部');
            else alert('网络异常，无法获取 ZIP 结构');
        }
    }

    function closeZipView() {
        restoreDetailToolbar();
        renderFiles(currentFilesData);
        currentZipName = "";
    }

    function toggleZipView() {
        isZipTreeView = !isZipTreeView;
        document.getElementById('view-toggle-btn').innerText = isZipTreeView ? '📄 切换平铺视图' : '📁 切换树状视图';
        renderZipFiles();
    }

    function selectAllZipFiles() {
        let cbs = document.querySelectorAll('.tree-file-cb, .tree-folder-cb');
        let allChecked = Array.from(cbs).every(cb => cb.checked);
        cbs.forEach(cb => cb.checked = !allChecked);
    }

    // 【P1-4 修复】ZIP 内文件夹下载用相对路径
    // 【待修-1 修复】跳过已被文件夹覆盖的文件，避免重复下载
    function downloadSelectedZip() {
        let selectedFiles = Array.from(document.querySelectorAll('.tree-file-cb:checked')).map(cb => cb.value);
        let selectedFolders = Array.from(document.querySelectorAll('.tree-folder-cb:checked')).map(cb => cb.dataset.path);
        
        if (selectedFiles.length === 0 && selectedFolders.length === 0) return alert('请先勾选要下载的文件或文件夹');

        let items = [];
        let folder_count = 0, file_count = 0;
        selectedFolders.forEach(folderPath => {
            let folderFiles = [];
            zipFilesData.forEach(f => {
                if (f.filename.startsWith(folderPath + '/') || f.filename === folderPath) {
                    let fullPath = currentZipName + '/' + f.filename;
                    folderFiles.push({
                        filename: _getRelName(f.filename, folderPath),
                        url: buildEncDownloadUrl(IDENTIFIER, fullPath),
                        size: intSafe(f.size)
                    });
                }
            });
            if (folderFiles.length > 0) {
                let targetName = currentZipName + "_" + folderPath.replace(/\//g, '_');
                items.push({ type: 'folder', name: targetName, files: folderFiles });
                folder_count++;
            }
        });

        let filesCoveredByFolders = new Set();
        selectedFolders.forEach(folderPath => {
            zipFilesData.forEach(f => {
                if (f.filename.startsWith(folderPath + '/') || f.filename === folderPath) {
                    filesCoveredByFolders.add(f.filename);
                }
            });
        });

        if (selectedFiles.length > 0) {
            let fileObjs = [];
            selectedFiles.forEach(fname => {
                if (filesCoveredByFolders.has(fname)) return;
                let f = zipFilesData.find(item => item.filename === fname);
                if (f) {
                    let fullPath = currentZipName + '/' + f.filename;
                    fileObjs.push({
                        filename: f.filename,
                        url: buildEncDownloadUrl(IDENTIFIER, fullPath),
                        size: intSafe(f.size)
                    });
                }
            });
            if (fileObjs.length > 0) {
                let itemName = currentZipName + "_" + (fileObjs.length === 1 ? fileObjs[0].filename.split('/').pop() : `${fileObjs.length}个文件`);
                items.push({ type: 'file', name: itemName, files: fileObjs });
                file_count = fileObjs.length;
            }
        }

        if (items.length === 0) return alert('没有可下载的内容');

        if (BATCH_DOWNLOAD_DEFAULT === 'termux') {
            doBatchDownload(items, 'termux', false);
        } else if (BATCH_DOWNLOAD_DEFAULT === 'browser') {
            doBatchDownload(items, 'browser', false);
        } else if (BATCH_DOWNLOAD_DEFAULT === 'per_file') {
            items.forEach(it => {
                it.files.forEach(f => {
                    let proxy_u = PROXY_DL ? '/api/download_proxy?url=' + encodeURIComponent(f.url) : f.url;
                    triggerDownload(f.filename.split('/').pop(), f.url, proxy_u, f.size);
                });
            });
        } else {
            document.getElementById('batchDownloadInfo').innerHTML = 
                `即将提交压缩包内下载任务：<br><b style="color:var(--primary);">${folder_count} 个文件夹</b> + <b style="color:var(--primary);">${file_count} 个文件</b>`;
            window._pendingBatchItems = items;
            document.getElementById('setAsDefaultBatch').checked = false;
            document.getElementById('batchDownloadChoiceModal').style.display = 'flex';
        }
    }

    function renderZipFiles() {
        let html = '';
        if (isZipTreeView) {
            let tree = buildTree(zipFilesData);
            function renderZipTree(node, pathStr = '') {
                let h = '';
                let keys = Object.keys(node).sort((a,b) => {
                    let aF = node[a]._isFolder, bF = node[b]._isFolder;
                    if (aF && !bF) return -1; if (!aF && bF) return 1; return a.localeCompare(b);
                });
                keys.forEach(k => {
                    if (node[k]._isFolder) {
                        let folderId = 'zip_' + Math.random().toString(36).substr(2, 9);
                        let folderPath = pathStr ? pathStr + '/' + k : k;
                        
                        h += `<div style="margin-bottom:5px;">
                            <div class="tree-row">
                                <span class="tree-arrow" id="arrow_${folderId}" onclick="toggleTreeRow('${folderId}', this)">▶</span>
                                <input type="checkbox" class="tree-folder-cb" data-path="${escapeHtml(folderPath)}" onclick="event.stopPropagation(); toggleFolderCheckbox(this, '${folderId}')">
                                <span style="font-weight:bold; word-break:break-all; flex:1; cursor:pointer;" onclick="toggleTreeRow('${folderId}', document.getElementById('arrow_${folderId}'))">📁 ${escapeHtml(k)}</span>
                                <button class="btn btn-sm btn-green" style="padding:2px 8px; font-size:11px;" data-folderpath="${escapeHtml(folderPath)}" onclick="downloadZipFolderEvent(this, event)">⬇️ 下载</button>
                            </div>
                            <div class="tree-children" id="tree_${folderId}">
                                ${renderZipTree(node[k].children, folderPath)}
                            </div>
                        </div>`;
                    } else {
                        let fname = node[k].data.filename;
                        let szStr = formatSize(node[k].data.size);
                        let ext = fname.split('.').pop().toLowerCase();
                        let canPreview = ['jpg', 'jpeg', 'png', 'gif', 'webp', 'bmp', 'svg', 'mp4', 'webm', 'mp3', 'wav', 'ogg', 'm4a'].includes(ext);
                        
                        let idx = zipFilesData.findIndex(item => item.filename === fname);
                        let btn = canPreview ? `<button class="btn btn-sm btn-blue" style="padding:2px 8px; font-size:11px; margin-left:10px;" onclick="event.stopPropagation(); previewByIdxZip(${idx})">👁️ 预览</button>` : '';
                        
                        h += `<div class="file-item-row" style="border:none; padding:8px;">
                            <input type="checkbox" class="tree-file-cb" value="${escapeHtml(fname)}">
                            <div class="file-info-col"><span style="font-weight:bold;">📄 ${escapeHtml(fname.split('/').pop())}</span></div>
                            <span style="color:var(--gray); font-size:12px; flex-shrink:0;">📦 ${szStr}</span>
                            ${btn}
                            <button class="btn btn-sm btn-green" style="padding:2px 8px; font-size:11px; margin-left:5px;" onclick="triggerZipDownloadByIdx(${idx})">⬇️ 下载</button>
                        </div>`;
                    }
                });
                return h;
            }
            html = `<div style="margin-top:10px;">${renderZipTree(tree, '')}</div>`;
        } else {
            html = `<div style="margin-top:10px;">`;
            zipFilesData.forEach((f, idx) => {
                let szStr = formatSize(f.size);
                let ext = f.filename.split('.').pop().toLowerCase();
                let canPreview = ['jpg', 'jpeg', 'png', 'gif', 'webp', 'bmp', 'svg', 'mp4', 'webm', 'mp3', 'wav', 'ogg', 'm4a'].includes(ext);
                let btn = canPreview ? `<button class="btn btn-sm btn-blue" style="padding:2px 8px; font-size:11px; margin:0 5px;" onclick="previewByIdxZip(${idx})">👁️ 预览</button>` : '';
                
                html += `<div class="file-item-row" style="border:none; padding:8px; display:flex; align-items:center; justify-content:space-between;">
                    <input type="checkbox" class="tree-file-cb" value="${escapeHtml(f.filename)}">
                    <span style="font-weight:bold; word-break:break-all; flex:1; margin-left:10px;">📄 ${escapeHtml(f.filename)}</span>
                    <span style="color:var(--gray); font-size:12px; flex-shrink:0;">📦 ${szStr}</span>
                    ${btn}
                    <button class="btn btn-sm btn-green" style="padding:2px 8px; font-size:11px; margin-left:5px;" onclick="triggerZipDownloadByIdx(${idx})">⬇️ 下载</button>
                </div>`;
            });
            html += `</div>`;
        }
        document.getElementById('files').innerHTML = html;
    }

    function previewByIdxZip(idx) { 
        let fname = zipFilesData[idx].filename;
        let fullPath = currentZipName + '/' + fname;
        previewFile(fullPath); 
    }
    
    function triggerZipDownloadByIdx(idx) {
        let fname = zipFilesData[idx].filename;
        let fullPath = currentZipName + '/' + fname;
        let real_u = buildEncDownloadUrl(IDENTIFIER, fullPath);
        let proxy_u = PROXY_DL ? '/api/download_proxy?url=' + encodeURIComponent(real_u) : real_u;
        triggerDownload(fname.split('/').pop(), real_u, proxy_u, intSafe(zipFilesData[idx].size));
    }

    // 【P1-4 修复】ZIP 内文件夹下载用相对路径
    function downloadZipFolderEvent(btn, event) {
        event.stopPropagation();
        let folderPath = btn.dataset.folderpath;
        let folderFiles = [];
        zipFilesData.forEach(f => {
            if (f.filename.startsWith(folderPath + '/') || f.filename === folderPath) {
                let fullPath = currentZipName + '/' + f.filename;
                folderFiles.push({
                    filename: _getRelName(f.filename, folderPath),
                    url: buildEncDownloadUrl(IDENTIFIER, fullPath),
                    size: intSafe(f.size)
                });
            }
        });
        let targetFolderName = currentZipName + "_" + folderPath.replace(/\//g, '_');
        downloadSingleFolder(targetFolderName, folderFiles);
    }

    let currentDLFile = { filename: '', realUrl: '', finalUrl: '', size: 0 };

    function triggerDownload(filename, realUrl, finalUrl, size = 0) {
        if (SINGLE_DOWNLOAD_DEFAULT === 'termux') {
            doSingleDownload(filename, realUrl, size, 'termux', false);
            return;
        }
        if (SINGLE_DOWNLOAD_DEFAULT === 'browser') {
            doSingleDownload(filename, realUrl, size, 'browser', false);
            return;
        }
        let pUrl = finalUrl;
        if (!pUrl) pUrl = PROXY_DL ? '/api/download_proxy?url=' + encodeURIComponent(realUrl) : realUrl;
        currentDLFile = { filename: filename, realUrl: realUrl, finalUrl: pUrl, size: size };
        document.getElementById('setAsDefaultSingle').checked = false;
        document.getElementById('downloadChoiceModal').style.display = 'flex';
    }

    async function confirmDownload(mode) {
        let setAsDefault = document.getElementById('setAsDefaultSingle').checked;
        closeModal('downloadChoiceModal');
        await doSingleDownload(currentDLFile.filename, currentDLFile.realUrl, currentDLFile.size, mode, setAsDefault);
    }

    async function doSingleDownload(filename, realUrl, size, mode, setAsDefault) {
        if (setAsDefault) {
            localStorage.setItem('single_download_default', mode);
            SINGLE_DOWNLOAD_DEFAULT = mode;
        }

        if (mode === 'browser') {
            let proxy_u = PROXY_DL ? '/api/download_proxy?url=' + encodeURIComponent(realUrl) : realUrl;
            let a = document.createElement('a');
            a.href = proxy_u;
            a.download = filename.split('/').pop();
            a.target = '_blank';
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            try {
                await fetch('/api/download_batch', {
                    method: 'POST', headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({items: [{type: 'file', name: filename, files: [{filename, url: realUrl, size: intSafe(size)}]}], mode: 'browser'})
                });
            } catch(e) {}
        } else {
            try {
                let res = await (await fetch('/api/download_batch', {
                    method: 'POST', headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({items: [{type: 'file', name: filename, files: [{filename, url: realUrl, size: intSafe(size)}]}], mode: 'termux'})
                })).json();
                if (res.status === 'success') {
                    alert('✅ 已加入下载队列，请前往「队列」查看进度');
                    if (currentZipName) closeZipView();
                } else {
                    alert('失败: ' + res.msg);
                }
            } catch(e) {
                alert('网络异常: ' + e.message);
            }
        }
    }

    function copyText(text) { if(!text) return; if(navigator.clipboard) navigator.clipboard.writeText(text).then(()=>alert('✅ 已复制')).catch(()=>fallbackCopy(text)); else fallbackCopy(text); }
    function fallbackCopy(text) { var ta = document.createElement("textarea"); ta.value = text; ta.style.position = "fixed"; ta.style.opacity = "0"; document.body.appendChild(ta); ta.select(); try { document.execCommand('copy'); alert('✅ 已复制'); } catch (err) { alert('❌ 复制失败'); } document.body.removeChild(ta); }
    
    function forceRefresh() { refreshFiles(); }
    
    let currentThumbIdent = '';
    function showThumbModal(ident, title) { currentThumbIdent = ident; document.getElementById('thumbTitle').innerText = '🖼️ ' + title; document.getElementById('thumbModalImg').src = '/thumb/' + encodeURIComponent(ident) + '?t=' + new Date().getTime(); document.getElementById('thumbDownloadBtn').href = '/thumb/' + encodeURIComponent(ident) + '?download=1'; document.getElementById('thumbUploadStatus').innerText = ''; document.getElementById('thumbModal').style.display = 'flex'; }
    async function refreshThumb() { let s = document.getElementById('thumbUploadStatus'); s.innerText = '正在重新获取...'; let res = await (await fetch('/api/thumb/refresh/' + encodeURIComponent(currentThumbIdent), {method:'POST'})).json(); if(res.status === 'success') { document.getElementById('thumbModalImg').src = '/thumb/' + encodeURIComponent(currentThumbIdent) + '?t=' + new Date().getTime(); s.innerText = '✅ 获取成功'; } else { s.innerText = '❌ 失败: ' + (res.msg || '未知错误'); } }
    async function uploadCustomThumb() { let fileInput = document.getElementById('thumbFileInput'); if(!fileInput.files.length) return; let syncIA = confirm('是否同步更新 IA 官方缩略图？\n\n确定 = 同步官方 (较慢)\n取消 = 仅修改本地'); let fd = new FormData(); fd.append('file', fileInput.files[0]); fd.append('sync_ia', syncIA ? 'yes' : 'no'); document.getElementById('thumbUploadStatus').innerText = '正在上传...'; let res = await (await fetch('/api/thumb/custom/' + encodeURIComponent(currentThumbIdent), {method:'POST', body: fd})).json(); if(res.status === 'success') { document.getElementById('thumbModalImg').src = '/thumb/' + encodeURIComponent(currentThumbIdent) + '?t=' + new Date().getTime(); document.getElementById('thumbUploadStatus').innerText = '✅ 自定义成功'; } else { document.getElementById('thumbUploadStatus').innerText = '❌ 失败: ' + res.msg; } fileInput.value = ''; }
    
    loadFiles(false);
</script>
</body></html>
"""