# pages_local_picker.py
# 公共本地选择组件（目录浏览弹窗 + 收藏夹 + 多选列表管理 + 浏览器文件/文件夹选择）
# 【改进点 K】renderList 改为事件委托（容器上绑一次 click，AbortController 管理），destroy 时自动解绑
# 【改进点 L】filesInput / dirInput 已被其它 picker 绑定时不覆盖，console.warn 警告
# 【改进点 U】新增 _normalizePath()，addPath/setPaths/去重比较都用归一化路径
# 【改进点 O】addPath 相同路径不重复追加（配合 U 归一化）
# 【改进点 W1】mergeFilesWithDedup 注释说明"追加"意图
# 【修复 PA】destroy 里只在 _localPickerBound === iid 时才清空，防误清其它 picker 的绑定标志
# 【修复 PD2】addPath 里 _normalizePath 后为空（纯空白）时提示"路径无效"，防 push 空字符串
# 【记录 PE1】listContainerId 容器不存在时移除按钮失效（3 个页面正常）
# 【记录 PE2】Windows 路径归一化不一致（Termux 是 Android/Linux，不改）
# 【记录 PE3】loadDir 行元素 onclick 可优化为事件委托
# 【记录 PE4】先 mount B 后 destroy A 时 B 是"半死"状态（多页面不共存）
# 【记录 PE5】多页面同时用同一 browserFilesInputId 时只有一个生效（多页面不共存）

LOCAL_PICKER_JS = r"""<script>
(function() {
    'use strict';

    // ========== CSS 幂等注入 ==========
    if (!document.getElementById('localPickerStyle')) {
        const style = document.createElement('style');
        style.id = 'localPickerStyle';
        style.textContent = `
            .local-picker-path-item { display:flex; justify-content:space-between; align-items:flex-start; padding:6px 8px; border-bottom:1px dashed var(--border,#ddd); gap:8px; font-size:13px; }
            .local-picker-path-item:last-child { border-bottom:none; }
            .local-picker-path-item span { flex:1; word-break:break-all; overflow-wrap:anywhere; }
            .local-picker-path-item button { width:24px; height:24px; padding:0; margin:0; background:transparent; border:none; color:var(--danger,#f44336); font-size:16px; cursor:pointer; flex-shrink:0; border-radius:4px; line-height:1; }
            .local-picker-path-item button:hover { background:rgba(244,67,54,0.1); }
            .local-picker-row { display:flex; align-items:center; gap:8px; padding:8px 10px; cursor:pointer; border-radius:4px; margin-bottom:2px; background:#fff; border:1px solid var(--border,#ddd); transition:0.15s; }
            .local-picker-row:hover { background:rgba(0,150,136,0.1); border-color:var(--primary,#009688); }
            .local-picker-row > span:first-child { font-size:18px; flex-shrink:0; }
            .local-picker-row > span:last-child { word-break:break-all; overflow-wrap:anywhere; font-size:13px; }
            .fav-tag { background:#e3f2fd; color:var(--blue,#2196F3); padding:3px 8px; border-radius:12px; font-size:12px; cursor:pointer; display:inline-flex; align-items:center; gap:4px; border:1px solid var(--blue,#2196F3); max-width:100%; transition:0.15s; }
            .fav-tag:hover { background:#bbdefb; }
            .fav-tag span { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; max-width:120px; }
            .fav-tag button { background:transparent; border:none; color:var(--danger,#f44336); font-weight:bold; cursor:pointer; font-size:14px; padding:0 2px; margin:0; width:auto; line-height:1; }
            .fav-tag button:hover { color:#b71c1c; }
            [data-theme="dark"] .local-picker-row { background:#2d2d2d; }
            [data-theme="dark"] .fav-tag { background:#1a3a5c; }
        `;
        document.head.appendChild(style);
    }

    // ========== 工具函数 ==========
    function escapeHtml(s) {
        if (!s) return '';
        return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    }

    // 【改进点 U】路径归一化：去掉末尾 '/'，保留根 '/'
    function _normalizePath(p) {
        if (!p) return '';
        p = String(p).trim();
        if (!p) return '';
        if (p === '/') return '/';
        p = p.replace(/\/+$/, '');
        return p || '/';
    }

    // 【改进点 W1】合并两个 File 列表，按 webkitRelativePath||name + size + lastModified 去重
    // 【W1 说明】"追加"意图下，后放的覆盖前面的（用户最后选的优先）
    function mergeFilesWithDedup(filesA, filesB) {
        const map = new Map();
        for (let i = 0; i < filesA.length; i++) {
            const f = filesA[i];
            if (!f) continue;
            const relPath = (f.webkitRelativePath && f.webkitRelativePath.length > 0) ? f.webkitRelativePath : f.name;
            const key = relPath + '|' + (f.size || 0) + '|' + (f.lastModified || 0);
            map.set(key, f);
        }
        for (let i = 0; i < filesB.length; i++) {
            const f = filesB[i];
            if (!f) continue;
            const relPath = (f.webkitRelativePath && f.webkitRelativePath.length > 0) ? f.webkitRelativePath : f.name;
            const key = relPath + '|' + (f.size || 0) + '|' + (f.lastModified || 0);
            map.set(key, f);
        }
        return Array.from(map.values());
    }

    // ========== 全局键 ==========
    const FAV_KEY = 'netdisk_local_picker_favs';
    const LAST_DIR_KEY = 'netdisk_local_picker_last_dir';

    function getFavs() {
        try {
            const s = localStorage.getItem(FAV_KEY);
            const arr = s ? JSON.parse(s) : [];
            return Array.isArray(arr) ? arr.filter(x => typeof x === 'string') : [];
        } catch(e) { return []; }
    }
    function saveFavs(favs) {
        try { localStorage.setItem(FAV_KEY, JSON.stringify(favs)); } catch(e) {}
    }
    function getLastDir() {
        try { return localStorage.getItem(LAST_DIR_KEY) || ''; } catch(e) { return ''; }
    }
    function setLastDir(d) {
        try { localStorage.setItem(LAST_DIR_KEY, d || ''); } catch(e) {}
    }

    // ========== 实例存储 ==========
    const pickers = {};

    // ========== 渲染 modal HTML ==========
    function renderModal(iid) {
        return '<div class="modal-overlay" id="localPickerModal_' + iid + '" style="background:rgba(0,0,0,0.8); z-index:10000;">'
            + '<div class="modal-content" style="max-width:500px; height:80vh; display:flex; flex-direction:column; padding:15px;">'
            + '<div class="modal-title" style="margin-bottom:10px;"><span>📂 选择手机本地内容</span><span class="modal-close" data-close>&times;</span></div>'
            + '<div style="margin-bottom:10px; display:flex; flex-wrap:wrap; align-items:center; gap:5px; border-bottom:1px dashed var(--border,#ddd); padding-bottom:10px;">'
            + '<span style="font-size:12px; font-weight:bold; color:var(--orange,#FF9800); flex-shrink:0;">⭐ 收藏夹:</span>'
            + '<div id="localPickerFavs_' + iid + '" style="display:flex; flex-wrap:wrap; flex:1; gap:5px;"></div>'
            + '<button type="button" class="btn btn-sm btn-gray" data-add-fav style="margin:0; padding:2px 8px; font-size:11px; flex-shrink:0;">+ 收藏当前</button>'
            + '</div>'
            + '<div style="background:var(--bg,#eef2f5); padding:10px; border-radius:6px; font-size:13px; word-break:break-all; border:1px solid var(--border,#ddd);">'
            + '<b>当前路径:</b> <span id="localPickerPath_' + iid + '">/sdcard</span>'
            + '</div>'
            + '<div style="display:flex; gap:5px; margin:10px 0;">'
            + '<button type="button" class="btn btn-sm btn-gray" data-up style="margin:0; flex:1;">⬆️ 上一级</button>'
            + '<button type="button" class="btn btn-sm btn-blue" data-home style="margin:0; flex:1;">🏠 存储根目录</button>'
            + '</div>'
            + '<div id="localPickerList_' + iid + '" style="flex:1; overflow-y:auto; border:1px solid var(--border,#ddd); border-radius:6px; background:var(--bg,#eef2f5); padding:5px; -webkit-overflow-scrolling:touch;">'
            + '<p style="text-align:center;padding:20px;">读取中...</p>'
            + '</div>'
            + '<div style="margin-top:15px; display:flex; gap:10px;">'
            + '<button type="button" class="btn btn-green" data-add-current style="margin:0; flex:1; padding:12px; font-size:15px;">✅ 添加当前文件夹</button>'
            + '<button type="button" class="btn btn-gray" data-close style="margin:0; flex:1; padding:12px;">❌ 取消</button>'
            + '</div>'
            + '</div></div>';
    }

    // ========== 核心：mount ==========
    function mount(opts) {
        if (!opts || !opts.instanceId) {
            console.error('[LocalPicker] 缺少 instanceId');
            return null;
        }
        const iid = opts.instanceId;
        if (pickers[iid]) return pickers[iid];

        const modalId = 'localPickerModal_' + iid;
        if (!document.getElementById(modalId)) {
            document.body.insertAdjacentHTML('beforeend', renderModal(iid));
        }

        let paths = Array.isArray(opts.initialPaths) ? opts.initialPaths.slice() : [];
        let currentPath = '';
        let parentPath = '';
        let currentAbort = null;
        let loadSeq = 0;
        let listAbort = null;

        function notifyChange() {
            if (typeof opts.onChange === 'function') {
                try { opts.onChange(paths.slice()); } catch(e) { console.error(e); }
            }
        }

        function renderList() {
            const container = opts.listContainerId ? document.getElementById(opts.listContainerId) : null;
            if (!container) return;
            if (paths.length === 0) {
                container.innerHTML = '<span style="color:#888; font-size:13px;">尚未选择任何文件或文件夹...</span>';
                return;
            }
            let html = '';
            paths.forEach((p, i) => {
                html += '<div class="local-picker-path-item"><span>' + escapeHtml(p) + '</span><button type="button" data-idx="' + i + '" title="移除">×</button></div>';
            });
            container.innerHTML = html;
        }

        // 【改进点 K】容器事件委托
        const listContainer = opts.listContainerId ? document.getElementById(opts.listContainerId) : null;
        if (listContainer) {
            listAbort = new AbortController();
            listContainer.addEventListener('click', (e) => {
                const btn = e.target.closest('button[data-idx]');
                if (!btn) return;
                e.stopPropagation();
                const idx = parseInt(btn.dataset.idx);
                if (idx >= 0 && idx < paths.length) {
                    paths.splice(idx, 1);
                    renderList();
                    notifyChange();
                }
            }, { signal: listAbort.signal });
        }

        function renderFavs() {
            const container = document.getElementById('localPickerFavs_' + iid);
            if (!container) return;
            const favs = getFavs();
            if (favs.length === 0) {
                container.innerHTML = '<span style="font-size:12px; color:var(--gray,#607D8B);">暂无</span>';
                return;
            }
            let html = '';
            favs.forEach((p, i) => {
                const name = p.split('/').pop() || '/';
                html += '<div class="fav-tag" data-path="' + escapeHtml(p) + '" data-idx="' + i + '"><span>' + escapeHtml(name) + '</span><button type="button" data-del="' + i + '" title="删除收藏">×</button></div>';
            });
            container.innerHTML = html;
            container.querySelectorAll('.fav-tag').forEach(el => {
                el.onclick = (e) => {
                    if (e.target.tagName === 'BUTTON') return;
                    loadDir(el.dataset.path);
                };
            });
            container.querySelectorAll('button[data-del]').forEach(btn => {
                btn.onclick = (e) => {
                    e.stopPropagation();
                    const idx = parseInt(btn.dataset.del);
                    const curFavs = getFavs();
                    if (idx >= 0 && idx < curFavs.length) {
                        curFavs.splice(idx, 1);
                        saveFavs(curFavs);
                        renderFavs();
                    }
                };
            });
        }

        async function loadDir(path) {
            if (currentAbort) {
                try { currentAbort.abort(); } catch(e) {}
            }
            const seq = ++loadSeq;
            const controller = new AbortController();
            currentAbort = controller;
            const timer = setTimeout(() => controller.abort(), 20000);

            const listBox = document.getElementById('localPickerList_' + iid);
            const pathDisplay = document.getElementById('localPickerPath_' + iid);
            if (listBox) listBox.innerHTML = '<p style="text-align:center;padding:20px;"><span class="spinner"></span>读取中...</p>';

            try {
                const res = await (await fetch('/api/browse_dir?path=' + encodeURIComponent(path || ''), { signal: controller.signal })).json();
                clearTimeout(timer);
                if (seq !== loadSeq) return;
                if (res.status === 'error') {
                    currentPath = '';
                    parentPath = '';
                    if (pathDisplay) pathDisplay.innerText = '';
                    if (listBox) listBox.innerHTML = '<p style="color:var(--danger,#f44336);text-align:center;padding:20px;">❌ ' + escapeHtml(res.msg) + '</p>';
                    return;
                }
                currentPath = res.current_path || '';
                parentPath = res.parent_path || '';
                if (pathDisplay) pathDisplay.innerText = currentPath;
                setLastDir(currentPath);

                let html = '';
                if (res.dirs && res.dirs.length > 0) {
                    res.dirs.forEach(d => {
                        html += '<div class="local-picker-row" data-type="dir" data-path="' + escapeHtml(d.path) + '"><span>📁</span><span>' + escapeHtml(d.name) + '</span></div>';
                    });
                }
                if (res.files && res.files.length > 0) {
                    res.files.forEach(f => {
                        html += '<div class="local-picker-row" data-type="file" data-path="' + escapeHtml(f.path) + '" data-size="' + (f.size || 0) + '"><span>📄</span><span style="flex:1">' + escapeHtml(f.name) + '</span><span style="color:var(--gray,#607D8B);font-size:11px;flex-shrink:0;">' + ((f.size || 0)/1024/1024).toFixed(2) + ' MB</span></div>';
                    });
                }
                if (!html) html = '<p style="color:var(--gray,#607D8B);text-align:center;padding:20px;font-size:13px;">空目录</p>';
                if (listBox) listBox.innerHTML = html;

                if (listBox) {
                    listBox.querySelectorAll('.local-picker-row').forEach(el => {
                        if (el.dataset.type === 'dir') {
                            el.onclick = () => loadDir(el.dataset.path);
                        } else {
                            el.onclick = () => addPath(el.dataset.path);
                        }
                    });
                }
            } catch(e) {
                clearTimeout(timer);
                if (seq !== loadSeq) return;
                currentPath = '';
                parentPath = '';
                if (pathDisplay) pathDisplay.innerText = '';
                if (listBox) {
                    const msg = e.name === 'AbortError' ? '读取超时，可能是 Termux 权限不足或网络中断' : '读取失败';
                    listBox.innerHTML = '<p style="color:var(--danger,#f44336);text-align:center;padding:20px;">❌ ' + msg + '</p>';
                }
            }
        }

        function addPath(p) {
            if (!p) {
                alert('当前路径为空，请先选择一个目录');
                return;
            }
            const normalized = _normalizePath(p);
            // 【修复 PD2】归一化后为空（纯空白）时提示，防 push 空字符串
            if (!normalized) {
                alert('路径无效（纯空白），请重新选择');
                return;
            }
            if (paths.some(x => _normalizePath(x) === normalized)) {
                closeModal();
                return;
            }
            paths.push(normalized);
            renderList();
            closeModal();
            notifyChange();
        }

        function openModal() {
            const m = document.getElementById(modalId);
            if (m) m.style.display = 'flex';
            renderFavs();
            loadDir(getLastDir());
        }
        function closeModal() {
            const m = document.getElementById(modalId);
            if (m) m.style.display = 'none';
        }

        const modal = document.getElementById(modalId);
        if (modal) {
            modal.querySelectorAll('[data-close]').forEach(el => { el.onclick = closeModal; });
            const homeBtn = modal.querySelector('[data-home]');
            if (homeBtn) homeBtn.onclick = () => loadDir('');
            const upBtn = modal.querySelector('[data-up]');
            if (upBtn) upBtn.onclick = () => { if (parentPath) loadDir(parentPath); else alert('已经是根目录了'); };
            const addCurrentBtn = modal.querySelector('[data-add-current]');
            if (addCurrentBtn) addCurrentBtn.onclick = () => {
                if (!currentPath) {
                    alert('当前路径尚未加载成功，请先选择一个目录');
                    return;
                }
                addPath(currentPath);
            };
            const addFavBtn = modal.querySelector('[data-add-fav]');
            if (addFavBtn) addFavBtn.onclick = () => {
                if (!currentPath) {
                    alert('当前路径尚未加载成功，请先选择一个目录');
                    return;
                }
                const favs = getFavs();
                if (!favs.includes(currentPath)) {
                    favs.push(currentPath);
                    saveFavs(favs);
                    renderFavs();
                } else {
                    alert('该路径已在收藏夹中');
                }
            };
        }

        let filesInputHandler = null;
        const filesInput = opts.browserFilesInputId ? document.getElementById(opts.browserFilesInputId) : null;
        if (filesInput) {
            if (filesInput._localPickerBound) {
                if (filesInput._localPickerBound !== iid) {
                    console.warn(`[LocalPicker] filesInput(${opts.browserFilesInputId}) 已被 ${filesInput._localPickerBound} 绑定，跳过 ${iid}`);
                }
            } else {
                filesInput._localPickerBound = iid;
                filesInputHandler = () => {
                    if (typeof opts.onBrowserFilesChange === 'function') {
                        try { opts.onBrowserFilesChange(filesInput.files); } catch(e) { console.error(e); }
                    }
                };
                filesInput.addEventListener('change', filesInputHandler);
            }
        }

        const dirInputId = opts.browserDirInputId || ('localPickerDir_' + iid);
        let dirInput = document.getElementById(dirInputId);
        let dirInputInternal = false;
        if (!dirInput) {
            dirInputInternal = true;
            dirInput = document.createElement('input');
            dirInput.type = 'file';
            dirInput.id = dirInputId;
            dirInput.multiple = true;
            dirInput.style.display = 'none';
            if ('webkitdirectory' in dirInput) dirInput.webkitdirectory = true;
            document.body.appendChild(dirInput);
        }
        let dirInputHandler = null;
        if (dirInput) {
            if (dirInput._localPickerBound) {
                if (dirInput._localPickerBound !== iid) {
                    console.warn(`[LocalPicker] dirInput(${dirInputId}) 已被 ${dirInput._localPickerBound} 绑定，跳过 ${iid}`);
                }
            } else {
                dirInput._localPickerBound = iid;
                dirInputHandler = () => {
                    if (!dirInput.files || dirInput.files.length === 0) {
                        dirInput.value = '';
                        return;
                    }
                    const pickedFiles = Array.from(dirInput.files);
                    dirInput.value = '';
                    if (!filesInput) {
                        alert('页面未配置浏览器文件 input，无法合并文件夹选择');
                        return;
                    }
                    try {
                        const dt = new DataTransfer();
                        const merged = mergeFilesWithDedup(
                            filesInput.files ? Array.from(filesInput.files) : [],
                            pickedFiles
                        );
                        for (const f of merged) dt.items.add(f);
                        filesInput.files = dt.files;
                    } catch(e) {
                        alert('当前浏览器不支持合并选择。请刷新页面后一次性选择全部文件，或改用本地管理器。');
                    }
                    if (typeof opts.onBrowserFilesChange === 'function') {
                        try { opts.onBrowserFilesChange(filesInput.files); } catch(e) { console.error(e); }
                    }
                };
                dirInput.addEventListener('change', dirInputHandler);
            }
        }

        const picker = {
            open: openModal,
            close: closeModal,
            getPaths: () => paths.slice(),
            setPaths: (arr) => {
                if (!Array.isArray(arr)) { paths = []; renderList(); notifyChange(); return; }
                const seen = new Set();
                const newPaths = [];
                for (const p of arr) {
                    const n = _normalizePath(p);
                    if (n && !seen.has(n)) { seen.add(n); newPaths.push(n); }
                }
                paths = newPaths;
                renderList();
                notifyChange();
            },
            clearPaths: () => { paths = []; renderList(); notifyChange(); },
            addPath: addPath,
            removePath: (idx) => {
                if (idx >= 0 && idx < paths.length) {
                    paths.splice(idx, 1);
                    renderList();
                    notifyChange();
                }
            },
            triggerDirPick: () => {
                if (dirInput.webkitdirectory === undefined) {
                    if (!confirm('当前浏览器不支持文件夹选择，是否改为选择多个文件？')) return;
                }
                dirInput.click();
            },
            triggerFilePick: () => {
                if (filesInput) filesInput.click();
                else alert('页面未配置浏览器文件 input');
            },
            clearBrowserFiles: () => {
                if (filesInput) {
                    try { filesInput.value = ''; } catch(e) {}
                    if (typeof opts.onBrowserFilesChange === 'function') {
                        try { opts.onBrowserFilesChange(filesInput.files); } catch(e) {}
                    }
                }
            },
            destroy: () => {
                if (currentAbort) {
                    try { currentAbort.abort(); } catch(e) {}
                    currentAbort = null;
                }
                if (listAbort) {
                    try { listAbort.abort(); } catch(e) {}
                    listAbort = null;
                }
                const m = document.getElementById(modalId);
                if (m && m.parentNode) m.parentNode.removeChild(m);
                if (dirInputInternal && dirInput && dirInput.parentNode) {
                    dirInput.parentNode.removeChild(dirInput);
                }
                if (filesInput && filesInputHandler) {
                    try { filesInput.removeEventListener('change', filesInputHandler); } catch(e) {}
                    filesInputHandler = null;
                }
                if (dirInput && dirInputHandler) {
                    try { dirInput.removeEventListener('change', dirInputHandler); } catch(e) {}
                    dirInputHandler = null;
                }
                // 【修复 PA】只在 _localPickerBound === iid 时才清空，防误清其它 picker 的绑定标志
                if (filesInput) {
                    try {
                        if (filesInput._localPickerBound === iid) filesInput._localPickerBound = null;
                    } catch(e) {}
                }
                if (dirInput) {
                    try {
                        if (dirInput._localPickerBound === iid) dirInput._localPickerBound = null;
                    } catch(e) {}
                }
                delete pickers[iid];
            }
        };

        pickers[iid] = picker;
        picker.setPaths(Array.isArray(opts.initialPaths) ? opts.initialPaths : []);
        return picker;
    }

    window.LocalPicker = { mount: mount };
})();
</script>"""