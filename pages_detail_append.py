# pages_detail_append.py
# 批量/单条目追加文件页 HTML
# 【Bug 1 修复 - 第 6/5 步】前端分片命名改为 {relPath}.__ND_PART__{N}
# 【对话7】接入公共 LocalPicker 组件
# 【修复 E1-同源】LOCAL_PICKER_JS 用 Python 字符串拼接
# 【修复 F1-同源】onChange 更新 infoDiv
# 【修复 F2-同源】同时选择时显示警告，提交前 confirm
# 【改进点 AW】initApPicker 末尾主动调一次 _refreshApFileInfoDisplay（防御性）
# 【改进点 AU】apSelectedPaths 定义处加注释说明与 picker 内部 paths 的同步关系
# 【改进点 AV】APPEND_HTML 底部注释说明 render_template_string 参数与模板变量对应关系
# 【改进点 AA1 修正】doChunkedUpload 里 location.href='/queue' 是必须的（fetch 跟随 302 拿到 HTML 但浏览器不跳转）
# 【改进点 AA2】删除 _APPEND_HTML_TOP 末尾多余的 \n\n
# 【修复 SA1-死代码】AA3 的 if (browserFiles.length === 0 && !hasLocalPaths) 是死代码
# 【改进点 AA3-修正】confirm 后重新取一次 browserFiles（防极端场景异步修改）
# 【修复 SC4】_refreshApFileInfoDisplay 里去掉 currentFileSize > 0 条件
# 【修复 BB1-同源】initApPicker 加 window.LocalPicker 存在性检查，
#                  防极端场景 TypeError（LOCAL_PICKER_JS 注入失败）
# 【修复 BB1-扩展-同源】4 个按钮从 `if(window.apPicker)xxx()` 改为三元表达式
#                      `window.apPicker ? apPicker.xxx() : alert('...')`，
#                      用户点按钮时如果 picker 未加载会弹出明确提示（与 pages_index_main.py 的 BB3 对齐）
#
# 【🔴 G-37 第 2 轮修复 - 2026-11】
#   目标：追加页在目标条目 ID 显示行下方增加断点续传支持提示。
#   实现：纯 Jinja 条件判断（不改后端路由，不需要新增 render_template_string 参数）：
#     - 提取 item[1]（目标条目 identifier）
#     - 用 Jinja namespace + 循环逐字符检查字符集
#     - 判断：长度 5-101 + 首字符字母/数字 + 全字符集 [a-zA-Z0-9_.-]
#     - 合规：✅ 支持分片断点续传 + 建议全小写（大写会自动回退兼容模式）
#     - 不合规：⚠️ 不支持（原因），大文件会回退单次上传
#   与 pages_upload.py 的 G-37 第 1 轮、pages_queue_js.py 的 getResumeHint、
#   netdisk_workers_upload.py 的 _IDENTIFIER_RE 完全对齐。
#   注意：用 `item[1] or ''` 防 None（DB TEXT NOT NULL 理论上不会是 None，
#        但历史数据可能为空字符串）。Jinja `and` 短路保证 `_ap_id[0]` 只在长度≥5 时访问。
#   兼容性：Jinja namespace（{% set ns = namespace(...) %}）需要 Jinja 2.10+（2017），
#          Flask 2.x/3.x 默认带 Jinja 3.x，Termux pip 安装最新版，肯定支持。
#
# 【⏱️ 延迟与超时精细控制 - 2026-11 第 18/20 步】
# 前端 5 处硬编码 → Jinja 变量（由第 15 个文件 netdisk_routes_views_upload.py 传入）：
#   1. doChunkedUpload 里 `const CHUNK_SIZE = 5 * 1024 * 1024` → `{{ frontend_chunk_size_mb }} * 1024 * 1024`
#   2. doChunkedUpload 里 `for (let tryN = 0; tryN < 3; tryN++)` → `Math.max(1, {{ frontend_chunk_retries }})`（R129 兜底）
#   3. doChunkedUpload 里 `setTimeout(rs, 800)` → `setTimeout(rs, {{ frontend_chunk_retry_sleep_ms }})`
#   4. handleAppendUpload 里 `const FRONTEND_CHUNK_THRESHOLD = 50 * 1024 * 1024` → `{{ frontend_chunk_threshold_mb }} * 1024 * 1024`
#   5. handleAppendUpload 里 `xhr.timeout = 600000` → `xhr.timeout = {{ upload_xhr_timeout_ms }}`
#
# 【🔴 额外修复（本轮检查发现的 2 处遗漏，与第 17 个文件同源）】
#   6. _refreshApFileInfoDisplay 里 `currentFileSize > 50*1024*1024` → `currentFileSize > {{ frontend_chunk_threshold_mb }} * 1024 * 1024`
#   7. HTML 文案 "总大小超50MB会自动切片" → "总大小超{{ frontend_chunk_threshold_mb }}MB会自动切片"
#
# 【🔴 默认值与原来完全一致】
#   - frontend_chunk_size_mb = 5 → `5 * 1024 * 1024` = 原 `5 * 1024 * 1024` ✅
#   - frontend_chunk_retries = 3 → `Math.max(1, 3)` = 3 → 原 `3` ✅
#   - frontend_chunk_retry_sleep_ms = 800 → `800` ✅
#   - frontend_chunk_threshold_mb = 50 → `50 * 1024 * 1024` = 原 `50 * 1024 * 1024` ✅
#   - upload_xhr_timeout_ms = 600000 → `600000` ✅
#
# 【🔴 R129 兜底说明】
#   - 用户填 `frontend_chunk_retries = 0` → `Math.max(1, 0)` = 1 → 前端分片至少尝试 1 次
#   - 与后端 worker 的 `max(1, ...)` 风格一致
#
# 【中间状态兼容】
#   - 第 15 个文件已完成传参 → 本文件引用参数 → **不会 UndefinedError** ✅
#   - 顺序正确（第 15 → 第 17 → 第 18）
#
# 【📝 保留记录 - 2026-11，低优先，以后修】
#   G-185：P1-8 既有：{{ ei[2] if ei else '' }} 若 ei 存在但 ei[2] 为 None
#          （DB 是 TEXT NOT NULL，理论上不会），Jinja 会输出 "None" 字符串。
#   G-184：pages_upload.py 新 tip 文案"首次尝试可能多一次失败重试"语义略模糊
#          （可改"内部重试"），本轮追加页提示保持同样风格。
#   R146（本轮已修）：pages_detail_append.py 与 pages_upload.py 同步修复 2 处遗漏
#   R149：本文件只有 1 处 "总大小超50MB" 文案（比 pages_upload.py 少 1 处），
#        因为追加页的 HTML 结构里没有"高级设置"里的重复文案。

from pages_local_picker import LOCAL_PICKER_JS

_APPEND_HTML_TOP = r"""
<!DOCTYPE html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">{{ s|safe }}
</head><body>
{{ n|safe }}
<div class="card">
    <div style="margin-bottom:15px; display:flex; justify-content:space-between; align-items:center;">
        <button onclick="history.back()" class="btn btn-sm btn-gray" style="margin:0;">⬅️ 返回上一级</button>
    </div>
    
    <div style="background:linear-gradient(135deg, #FF9800, #F44336); color:white; padding:15px; border-radius:10px; margin-bottom:20px; box-shadow:0 4px 10px rgba(244,67,54,0.3); text-align:center;">
        <h2 style="margin:0; color:white; border:none; font-size:24px;">➕ 全新追加页面</h2>
        <p style="margin-top:5px; font-size:14px; opacity:0.9;">支持添加多个文件夹/文件，拥有与上传一致的队列体验！</p>
    </div>

    {% set _ap_id = item[1] or '' %}
    {% set _ap_allowed = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-' %}
    {% set _ap_ns = namespace(bad=0) %}
    {% for _ap_ch in _ap_id %}{% if _ap_ch not in _ap_allowed %}{% set _ap_ns.bad = _ap_ns.bad + 1 %}{% endif %}{% endfor %}
    {% set _ap_id_ok = (_ap_id|length >= 5) and (_ap_id|length <= 101) and (_ap_id[0] in _ap_allowed) and (_ap_id[0] not in '_.-') and (_ap_ns.bad == 0) %}
    <p>目标条目 ID: <b style="color:var(--primary); font-size:16px;">{{ item[1] }}</b> ({{ item[2] }})</p>
    <!-- 【G-37 第 2 轮】断点续传支持提示（与 pages_upload.py / getResumeHint / _IDENTIFIER_RE 一致） -->
    <span class="tip" style="background:rgba(0,150,136,0.1); border-left-color:var(--primary);">
        {% if _ap_id_ok %}
        ✅ 目标 ID 合规，支持分片断点续传（大文件可秒传/断点续传）。建议全小写（大写 ID 会自动回退到兼容模式，仍支持分片，但首次尝试可能多一次失败重试）。
        {% else %}
        ⚠️ 目标 ID 不合规（需 5-101 字符，首字符字母/数字，仅允许字母、数字、_、.、-），大文件可能无法使用分片断点续传（会回退单次上传）。
        {% endif %}
    </span>
    
    <div class="warning-box">💡 如果选择了多个相同文件，系统会自动重命名（如 file(1).txt）以防止云端覆盖冲突。</div>
    
    <div id="upload-progress-container" style="display:none; margin-bottom:20px; padding:15px; background:rgba(0,150,136,0.1); border-radius:8px;">
        <div style="font-weight:bold; margin-bottom:10px; color:var(--primary);">⏳ 正在处理文件，请勿刷新页面...</div>
        <div class="progress-container"><div class="progress-bar-fill" id="upload-bar" style="width:0%;"></div><div class="progress-text" id="upload-pct">0%</div></div>
    </div>
    
    <form id="append-form" method="POST" onsubmit="return handleAppendUpload(event)">
        <input type="hidden" name="item_id" value="{{ item[0] }}">
        <input type="hidden" name="local_paths" id="ap_local_paths_hidden" value="[]">
        
        <div class="file-input-box" id="drop_zone" style="position:relative; padding-bottom:20px;">
            <label style="font-weight:bold; font-size:16px; pointer-events:none;">1. 选择要追加的文件或文件夹</label>
            
            <div style="margin-top:15px; margin-bottom:20px; padding:15px; background:rgba(255,152,0,0.1); border:1px solid var(--orange); border-radius:8px;">
                <div style="font-size:14px; color:var(--orange); font-weight:bold; margin-bottom:8px;">📁 选择手机内的数据（可多次选择追加）</div>
                
                <div id="ap_path_list" style="max-height:150px; overflow-y:auto; border:1px solid var(--border); background:#fff; border-radius:6px; padding:10px; margin-bottom:10px;">
                    <span style="color:#888; font-size:13px;">尚未选择任何文件或文件夹...</span>
                </div>
                
                <div style="display:flex; gap:8px; align-items:center;">
                    <!-- 【修复 BB1-扩展-同源】三元表达式，防 picker 未加载时按钮无反馈 -->
                    <button type="button" class="btn btn-orange" onclick="window.apPicker ? apPicker.open() : alert('本地选择组件加载失败，请刷新页面后重试')" style="width:auto; margin:0; padding:0 15px; height:42px; flex:1;">📂 点击浏览并添加</button>
                    <button type="button" class="btn btn-gray" onclick="window.apPicker ? apPicker.clearPaths() : alert('本地选择组件加载失败，请刷新页面后重试')" style="width:80px; margin:0; padding:0 10px; height:42px;">清空</button>
                </div>
            </div>

            <details style="margin-top:15px; background:rgba(33,150,243,0.05); border:1px solid var(--blue); border-radius:8px; padding:10px;">
                <summary style="cursor:pointer; color:var(--blue); font-size:13px; font-weight:bold;">🌐 或者通过浏览器选择文件上传 (支持多选 · 支持文件夹 · 支持分片)</summary>
                <div style="padding:10px; margin-top:10px; background:var(--card-bg); border-radius:6px;">
                    <input type="file" name="files" id="browser_files_input" multiple style="display:block; margin:0; padding:10px; background:var(--bg); border:1px dashed var(--blue); border-radius:6px;">
                    <div style="display:flex; gap:8px; margin-top:8px;">
                        <!-- 【修复 BB1-扩展-同源】三元表达式 -->
                        <button type="button" class="btn btn-sm btn-blue" onclick="window.apPicker ? apPicker.triggerDirPick() : alert('本地选择组件加载失败，请刷新页面后重试')" style="margin:0; flex:1; font-size:12px;">📁 从文件夹选择</button>
                        <button type="button" class="btn btn-sm btn-gray" onclick="window.apPicker ? apPicker.clearBrowserFiles() : alert('本地选择组件加载失败，请刷新页面后重试')" style="margin:0; flex:1; font-size:12px;">❌ 清空浏览器选择</button>
                    </div>
                    <!-- 【遗漏修复】"50MB" → "{{ frontend_chunk_threshold_mb }}MB" -->
                    <span class="tip" style="margin-top:8px; font-size:11px; background:rgba(33,150,243,0.08); border-left-color:var(--blue);">💡 浏览器无法选择文件夹时可以用上面「本地路径」模式。总大小超{{ frontend_chunk_threshold_mb }}MB会自动切片，支持断点重试。</span>
                </div>
            </details>

            <div id="file_info_display" style="font-size:13px; color:var(--primary); font-weight:bold; margin-top:10px;"></div>
        </div>
        
        <details>
            <summary>2. 高级追加设置</summary>
            <div class="adv-container">
                <h3 style="font-size:14px; color:var(--primary); border:none; margin-top:0;">📦 ZIP 压缩包处理</h3>
                <label class="adv-label"><input type="checkbox" name="unzip_zip" value="yes" onchange="if(this.checked) document.getElementById('zip_upload').checked = false;"> <span>1. 自动解压 ZIP</span></label>
                <label class="adv-label"><input type="checkbox" name="zip_upload" value="yes" id="zip_upload" onchange="if(this.checked) document.querySelector('input[name=unzip_zip]').checked = false;"> <span>2. 打包为 ZIP 再传</span></label>
                <input type="text" id="zip_custom_name" name="zip_custom_name" placeholder="自定义ZIP压缩包名称" style="width:180px; padding:6px; margin-left:10px;">
                
                <h3 style="font-size:14px; color:var(--primary); border:none; margin-top:20px;">⚙️ 大文件分块与断点续传</h3>
                <label>文件分块阈值 (MB):</label>
                <input type="number" name="chunk_threshold" value="200" min="20" max="1024">
                
                <label>单块大小 (MB):</label>
                <input type="number" name="chunk_size" value="10" min="5" max="50">

                <label class="adv-label" style="margin-top:15px; color:var(--danger);">
                    <input type="checkbox" name="overwrite_same_file" value="yes"> 
                    <span>3. 覆盖云端已有同名文件</span>
                </label>

                <h3 style="font-size:14px; color:var(--primary); border:none; margin-top:20px;">⚙️ 官方底层并发参数</h3>
                <label>文件并发数 (1-16):</label>
                <input type="number" name="concurrency" value="8" min="1" max="16">
                
                <label class="adv-label" style="margin-top:15px;"><input type="checkbox" name="generate_torrent" value="yes" checked> <span>4. 生成衍生文件 / BT种子</span></label>
                <label class="adv-label"><input type="checkbox" name="no_derive" value="yes"> <span>5. 跳过生成衍生文件</span></label>
                <label class="adv-label"><input type="checkbox" name="keep_old" value="yes"> <span>6. 保留旧版本文件</span></label>
                <label class="adv-label"><input type="checkbox" name="no_directory" value="yes"> <span>7. 忽略文件夹结构</span></label>
                <label class="adv-label"><input type="checkbox" name="no_index" value="yes"> <span>8. 禁止搜索引擎索引</span></label>
                <label class="adv-label"><input type="checkbox" name="interactive_priority" value="yes"> <span>9. 交互优先级</span></label>
                <label class="adv-label"><input type="checkbox" name="dark_mode" value="yes"> <span>10. 官方页面暗黑模式</span></label>
                <label class="adv-label"><input type="checkbox" name="skip_meta" value="yes"> <span>11. 跳过生成 meta.xml</span></label>
                <label class="adv-label"><input type="checkbox" name="checksum" value="yes"> <span>12. 上传后计算校验和</span></label>

                <label>13. 预估文件大小 (可选):</label>
                <input type="text" name="size_hint" placeholder="例如 500MB, 2GB">
                
                <label>14. 自动重试次数 (最高 5 次):</label>
                <input type="number" name="retries" min="0" max="5" placeholder="默认 5">
            </div>
        </details>
        
        <button type="submit" id="submit_btn" class="btn btn-purple" style="padding:15px;">➕ 加入追加队列</button>
    </form>
</div>
"""

_APPEND_HTML_BOTTOM = r"""<script>
    let currentFileSize = 0;

    // 【改进点 AU】apSelectedPaths 是页面侧追踪数组
    //   - picker 内部维护自己的 paths
    //   - 两者通过 onChange（picker → 页面）/ setPaths（页面 → picker）/ getPaths（页面读取）同步
    let apSelectedPaths = [];
    let apPicker = null;

    // 【修复 F1-同源/F2-同源】统一刷新 infoDiv
    function _refreshApFileInfoDisplay() {
        const infoDiv = document.getElementById('file_info_display');
        if (!infoDiv) return;
        const bfi = document.getElementById('browser_files_input');
        const browserCount = (bfi && bfi.files) ? bfi.files.length : 0;
        const localCount = apSelectedPaths.length;
        if (localCount > 0 && browserCount > 0) {
            infoDiv.innerHTML = `<span style="color:var(--danger);">⚠️ 已选 ${localCount} 个本地路径 + ${browserCount} 个浏览器文件，本地路径优先（浏览器文件将被忽略）</span>`;
        } else if (localCount > 0) {
            infoDiv.innerHTML = `已选本地路径: <span style="color:var(--blue);">${localCount} 个</span>`;
        } else if (browserCount > 0) {
            // 【修复 SC4】去掉 currentFileSize > 0 条件，防 0 字节文件（用户选空文件时看不到已选提示）
            const names = Array.from(bfi.files).slice(0, 3).map(f => f.name).join(', ');
            const more = browserCount > 3 ? ` 等 ${browserCount} 个文件` : '';
            // 【遗漏 1 修复】50*1024*1024 → {{ frontend_chunk_threshold_mb }} * 1024 * 1024
            infoDiv.innerHTML = `已选浏览器文件: <span style="color:var(--blue);">${escapeHtml(names)}${more}</span> (${(currentFileSize/1024/1024).toFixed(2)} MB)` + (currentFileSize > {{ frontend_chunk_threshold_mb }} * 1024 * 1024 ? ' <span style="color:var(--orange);">[将自动分片上传]</span>' : '');
        } else {
            infoDiv.innerText = '';
        }
    }

    function initApPicker() {
        // 【修复 BB1-同源】LocalPicker 存在性检查，防极端场景 TypeError
        // （LOCAL_PICKER_JS 注入失败时，如 CSP 拦截 inline script / 浏览器扩展干扰 / 字符串拼接出错）
        if (!window.LocalPicker) {
            console.error('[initApPicker] LocalPicker 组件未加载，可能 LOCAL_PICKER_JS 注入失败');
            return;
        }
        apPicker = window.LocalPicker.mount({
            instanceId: 'ap',
            listContainerId: 'ap_path_list',
            initialPaths: apSelectedPaths,
            onChange: (paths) => {
                apSelectedPaths = paths;
                const h = document.getElementById('ap_local_paths_hidden');
                if (h) h.value = JSON.stringify(paths);
                _refreshApFileInfoDisplay();
            },
            browserFilesInputId: 'browser_files_input',
            onBrowserFilesChange: (files) => {
                if (!files || files.length === 0) {
                    currentFileSize = 0;
                } else {
                    let total = 0;
                    for (let i = 0; i < files.length; i++) total += files[i].size;
                    currentFileSize = total;
                }
                _refreshApFileInfoDisplay();
            }
        });
        window.apPicker = apPicker;
        // 【改进点 AW】主动刷新一次（防御性），让 infoDiv 与初态一致
        _refreshApFileInfoDisplay();
    }

    document.addEventListener('DOMContentLoaded', () => {
        initApPicker();
    });

    async function doChunkedUpload(form, allFiles, submitBtn, progContainer, progBar, progPct) {
        // 【⏱️ 延迟与超时精细控制】CHUNK_SIZE = 5 * 1024 * 1024 → {{ frontend_chunk_size_mb }} * 1024 * 1024
        const CHUNK_SIZE = {{ frontend_chunk_size_mb }} * 1024 * 1024;
        let batchId = 'c' + Date.now() + '_' + Math.random().toString(36).slice(2,8);
        let totalBytes = 0;
        for (let f of allFiles) totalBytes += f.size;
        if (totalBytes <= 0) { alert('选中的文件为空'); submitBtn.disabled = false; submitBtn.innerText = '加入追加队列'; progContainer.style.display = 'none'; return false; }
        let uploadedBytes = 0;
        progContainer.style.display = 'block';
        submitBtn.disabled = true;
        submitBtn.innerText = '⏳ 分片上传中...';

        for (let i = 0; i < allFiles.length; i++) {
            let file = allFiles[i];
            let relPath = (file.webkitRelativePath && file.webkitRelativePath.length > 0) ? file.webkitRelativePath : file.name;
            relPath = relPath.replace(/\\/g, '/').replace(/\.\.\//g, '').replace(/^\/+/, '');
            let offset = 0;
            while (offset < file.size) {
                let end = Math.min(offset + CHUNK_SIZE, file.size);
                let partIndex = Math.floor(offset / CHUNK_SIZE);
                let chunk = file.slice(offset, end);
                let fd = new FormData();
                fd.append('chunk_batch_id', batchId);
                fd.append('chunk_rel_path', relPath + '.__ND_PART__' + partIndex);
                fd.append('file_chunk', chunk, relPath.split('/').pop() || 'chunk');
                let ok = false, lastErr = '';
                // 【⏱️ 延迟与超时精细控制】tryN < 3 → tryN < Math.max(1, {{ frontend_chunk_retries }})（R129 兜底）
                for (let tryN = 0; tryN < Math.max(1, {{ frontend_chunk_retries }}); tryN++) {
                    try {
                        let r = await fetch('/api/upload_chunk', { method: 'POST', body: fd });
                        let j = await r.json();
                        if (j.status === 'success') { ok = true; break; }
                        lastErr = j.msg || '未知错误';
                    } catch(e) { lastErr = e.message || '网络异常'; }
                    // 【⏱️ 延迟与超时精细控制】setTimeout(rs, 800) → setTimeout(rs, {{ frontend_chunk_retry_sleep_ms }})
                    await new Promise(rs => setTimeout(rs, {{ frontend_chunk_retry_sleep_ms }}));
                }
                if (!ok) {
                    alert(`分片上传失败：${relPath} @ 分片${partIndex}\n原因：${lastErr}\n\n请检查网络后重试（重新提交会从新 batch 开始，旧分片留在临时目录，可在设置里清理）。`);
                    submitBtn.disabled = false; submitBtn.innerText = '加入追加队列';
                    progContainer.style.display = 'none';
                    return false;
                }
                uploadedBytes += (end - offset);
                let pct = Math.round((uploadedBytes / totalBytes) * 100);
                progBar.style.width = pct + '%';
                progPct.innerText = `分片上传中 ${pct}% (${i+1}/${allFiles.length} 个文件)`;
                offset = end;
            }
        }

        let fd = new FormData(form);
        try { fd.delete('files'); } catch(e) {}
        fd.set('is_chunk_finalize', 'yes');
        fd.set('chunk_batch_id', batchId);
        fd.set('local_paths', '[]');
        submitBtn.innerText = '⏳ 正在提交任务...';
        progPct.innerText = '正在提交...';
        try {
            let r = await fetch(location.href, { method: 'POST', body: fd });
            let ct = r.headers.get('content-type') || '';
            if (ct.includes('application/json')) {
                let res = await r.json();
                alert(res.msg || '提交失败');
                submitBtn.disabled = false; submitBtn.innerText = '加入追加队列';
                progContainer.style.display = 'none';
                return false;
            } else {
                // 【改进点 AA1 修正】此处 location.href='/queue' 是必须的：
                //   后端 finalize 成功后 redirect('/queue')（302），fetch 默认跟随 302 拿到 HTML，
                //   但浏览器 URL 仍在当前页（/append_files/N），必须手动跳转
                location.href = '/queue';
                return false;
            }
        } catch(e) {
            alert('提交失败：' + e.message);
            submitBtn.disabled = false; submitBtn.innerText = '加入追加队列';
            progContainer.style.display = 'none';
            return false;
        }
    }

    async function handleAppendUpload(e) {
        e.preventDefault();
        
        let submitBtn = document.getElementById('submit_btn');
        if (submitBtn.disabled) return false;
        
        let form = document.getElementById('append-form');
        let bfi = document.getElementById('browser_files_input');
        let browserFiles = (bfi && bfi.files) ? Array.from(bfi.files) : [];
        let hasLocalPaths = apSelectedPaths.length > 0;

        if (!hasLocalPaths && browserFiles.length === 0) {
            alert('请选择本地文件/文件夹，或者通过浏览器选择文件！');
            return false;
        }

        // 【修复 F2-同源】同时选择本地路径 + 浏览器文件时，提交前明确提示"本地优先"
        if (hasLocalPaths && browserFiles.length > 0) {
            if (!confirm('⚠️ 检测到同时选择了本地路径和浏览器文件。\n\n后端处理时本地路径优先，浏览器文件将被忽略。\n\n确定继续吗？')) return false;
            // 【改进点 AA3-修正】confirm 阻塞后重新取一次 browserFiles（防极端场景异步修改）
            // 【修复 SA1-死代码】原 if (browserFiles.length === 0 && !hasLocalPaths) 是死代码，
            //   改为只日志提示（本地优先，浏览器文件被清空）
            let prevBrowserCount = browserFiles.length;
            browserFiles = (bfi && bfi.files) ? Array.from(bfi.files) : [];
            if (browserFiles.length === 0 && prevBrowserCount > 0) {
                console.warn('[append] confirm 后浏览器文件被清空，仅使用本地路径（本地优先）');
            }
        }

        let progContainer = document.getElementById('upload-progress-container');
        let progBar = document.getElementById('upload-bar');
        let progPct = document.getElementById('upload-pct');
        
        // 【⏱️ 延迟与超时精细控制】FRONTEND_CHUNK_THRESHOLD = 50 * 1024 * 1024 → {{ frontend_chunk_threshold_mb }} * 1024 * 1024
        const FRONTEND_CHUNK_THRESHOLD = {{ frontend_chunk_threshold_mb }} * 1024 * 1024;
        if (!hasLocalPaths && browserFiles.length > 0) {
            let total = 0;
            for (let f of browserFiles) total += f.size;
            if (total > FRONTEND_CHUNK_THRESHOLD) {
                return await doChunkedUpload(form, browserFiles, submitBtn, progContainer, progBar, progPct);
            }
        }
        
        let fd = new FormData(form);
        const lpHidden = document.getElementById('ap_local_paths_hidden');
        if (lpHidden) lpHidden.value = JSON.stringify(apSelectedPaths);
        if (hasLocalPaths) {
            fd.set('local_paths', JSON.stringify(apSelectedPaths));
        } else {
            fd.set('local_paths', '[]');
        }
        if (fd.has('local_path')) fd.delete('local_path');
        
        progContainer.style.display = 'block';
        submitBtn.disabled = true; 
        submitBtn.innerText = '⏳ 正在交由后端本地复制...';
        
        let xhr = new XMLHttpRequest(); xhr.open('POST', location.href, true);
        // 【⏱️ 延迟与超时精细控制】xhr.timeout = 600000 → xhr.timeout = {{ upload_xhr_timeout_ms }}
        xhr.timeout = {{ upload_xhr_timeout_ms }};
        xhr.upload.onprogress = function(e) { if (e.lengthComputable) { let pct = Math.round((e.loaded / e.total) * 100); progBar.style.width = pct + '%'; progPct.innerText = pct + '%'; } };
        
        xhr.onload = function() { 
            if (xhr.status === 200) { 
                let ct = xhr.getResponseHeader('content-type') || '';
                if (ct.includes('application/json')) {
                    try { 
                        let res = JSON.parse(xhr.responseText); 
                        alert(res.msg || '提交失败'); 
                        submitBtn.disabled = false; submitBtn.innerText = '加入追加队列'; 
                    } catch(e) { 
                        location.href = '/queue'; 
                    } 
                } else {
                    location.href = '/queue'; 
                }
            } else { 
                alert('上传失败，请重试。'); submitBtn.disabled = false; submitBtn.innerText = '加入追加队列'; 
            } 
            progContainer.style.display = 'none'; 
        };
        xhr.onerror = function() {
            alert('提示：网络连接断开或本地路径读取失败。');
            submitBtn.disabled = false; submitBtn.innerText = '加入追加队列';
            progContainer.style.display = 'none';
        };
        xhr.ontimeout = function() {
            alert('超时，可能是文件过大正在本地复制中，请稍后前往队列查看。');
            submitBtn.disabled = false; submitBtn.innerText = '加入追加队列';
            progContainer.style.display = 'none';
        };
        xhr.send(fd); 
        return false;
    }
</script>
</body></html>
"""

# 【改进点 AV】render_template_string 调用处（在 netdisk_routes_views_upload.py 的 append_files）
#   参数：s / n / item
#   新增参数（第 15 个文件第 18/20 步）：
#     / frontend_chunk_threshold_mb / frontend_chunk_size_mb / frontend_chunk_retries
#     / frontend_chunk_retry_sleep_ms / upload_xhr_timeout_ms
#   若某个参数漏传，Jinja 会抛 UndefinedError（"xxx is undefined"）
#   这是 Jinja 特性，无编译期检查。新增模板变量时需同步在 render_template_string 参数里加。
APPEND_HTML = _APPEND_HTML_TOP + LOCAL_PICKER_JS + _APPEND_HTML_BOTTOM