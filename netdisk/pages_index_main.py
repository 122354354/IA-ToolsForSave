# pages_index_main.py
# 首页（根目录 / 文件夹视图）HTML
# 【对话8】批量追加弹窗接入公共 LocalPicker 组件
# 【修复 E1-同源】LOCAL_PICKER_JS 用 Python 字符串拼接
# 【修复 F1-同源】onChange 更新 ba_info
# 【修复 F2-同源】同时选择时显示警告，提交前 confirm
# 【改进点 BB1】initBaPicker 加 window.LocalPicker 存在性检查
# 【改进点 BB2】INDEX_HTML 拼接时在 TOP / LOCAL_PICKER_JS / BOTTOM 之间加换行分隔
# 【修复 BB1-扩展】initBaPicker 去掉 alert（避免打断首页），只 console.error
# 【修复 BB3】4 个按钮 onclick 里 `if(...)stmt; else alert(...)` 是 JS 语法错误
#            （if(cond)stmt; 是完整 IfStatement，孤立的 else 会抛 SyntaxError）
#            改为三元表达式 `window.baPicker ? baPicker.xxx() : alert(...)`
#
# 【⏱️ 延迟与超时精细控制 - 2026-11 R147+R153 追加修复】
# 首页 file_cache 拉取（refreshPicker）也有 AbortController 15000ms 硬编码，
# 需要接 detail_fetch_timeout_ms。第 21 个文件 netdisk_routes_views_index.py
# 已传参（folder_view 的 render_template_string 追加 detail_fetch_timeout_ms）。
#
# 【🔴 BUG 修复 - 2026-11（第 3 次）】previewZip 的 AbortController 30000 被遗漏
#   修复：setTimeout(() => controller.abort(), 30000)
#        → setTimeout(() => controller.abort(), {{ detail_zip_timeout_ms|default(30000) }})
#
# 【🔴 R217 修复 - 2026-11】refreshPicker 的 {{ detail_fetch_timeout_ms }} 无 default
#   修复：加 |default(15000) 兜底
#
# 【🔴 R434 修复 - 2026-11（前端显示 skipped - 本轮）】
#   问题：doBatchAppendSubmit 只显示 "✅ 提交成功"，虽然后端 msg 里已包含
#        skipped 提示（R414 第 2 个文件 + R437），但用户容易忽略 msg 里的括号信息，
#        只看到"提交成功"就以为全部成功。
#   修复：doBatchAppendSubmit 的成功分支：
#     - res.skipped 非空 → 显示"✅ 已提交 N 个追加任务 + ⚠️ 已跳过 M 个条目..."
#     - res.skipped 为空 → 保持原 alert('✅ ' + res.msg)
#   为什么显示前 5 个：超过 5 个时列表太长（手机屏幕小），显示"等 N 个"更清晰。
#   为什么保留 res.msg：msg 里有"警告：N 个分片合并失败已清理"等补充信息，
#        在 skipped 为空时仍需要显示。
#   为什么不做 confirm：用户点确定后已经执行（是结果展示，不是询问）。
#
# 【📝 保留记录 - 2026-11，低优先，以后修】
#   R213：smartDownloadFrom / getMagnetFrom 的 fetch 无超时控制
#   R214：refreshPicker 的 clearTimeout 时序无害
#   R216：pages_index_main.py 与 pages_detail_item.py 都有 previewZip，独立实现
#   R453（本轮记录）：如果 res.count=0 且 res.skipped 非空（全部跳过），
#        alert 显示"✅ 已提交 0 个追加任务"略怪。但 R437 后端在 items_data
#        为空时返回 status='error'（走 else 分支），所以不会出现 count=0。
#        记录为"未来若后端改动需同步"。

from pages_local_picker import LOCAL_PICKER_JS

_INDEX_HTML_TOP = r"""
<!DOCTYPE html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">{{ style|safe }}
<style>
    .tree-folder { margin-left: 15px; border-left: 1px dashed var(--border); padding-left: 10px; margin-top: 5px; }
    .file-item-row { display: flex; flex-wrap: wrap; align-items: flex-start; padding: 12px; border-bottom: 1px solid var(--border); gap: 10px; background: var(--card-bg); transition: 0.2s; margin-bottom: 8px; border: 1px solid var(--border); border-radius: 6px; }
    .file-item-row:hover { background: rgba(0,0,0,0.02); }
    
    .tree-file-cb { width: 20px; height: 20px; margin: 2px 0 0 0; cursor: pointer; flex-shrink: 0; }
    .tree-folder-cb { position: static !important; width: 18px !important; height: 18px !important; margin: 0 8px 0 0 !important; cursor: pointer; flex-shrink: 0; }
    
    .file-info-col { flex: 1; min-width: 200px; word-break: break-all; }
    .file-actions-col { flex-basis: 100%; display: flex; gap: 8px; flex-wrap: wrap; padding-top: 8px; border-top: 1px dashed var(--border); }
    
    .stats-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(120px, 1fr)); gap: 15px; margin-bottom: 20px; }
    .stat-card { background: var(--bg); border-radius: 10px; padding: 15px; text-align: center; border: 1px solid var(--border); }
    .stat-value { font-size: 24px; font-weight: bold; color: var(--primary); margin-bottom: 5px; }
    .stat-label { font-size: 13px; color: var(--gray); }

    .preview-modal { display: none; position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.85); z-index: 10000; justify-content: center; align-items: center; flex-direction: column; }
    .preview-content { max-width: 90%; max-height: 80%; border-radius: 8px; box-shadow: 0 0 20px rgba(0,0,0,0.5); }
    
    .tree-row { display: flex; align-items: center; padding: 8px 0; gap: 8px; flex-wrap: wrap; }
    .tree-arrow { font-size: 12px; color: var(--primary); transition: 0.2s; display: inline-block; width: 14px; text-align: center; cursor: pointer; }
    .tree-children { display: none; margin-left: 15px; border-left: 1px dashed var(--border); padding-left: 10px; }
    
    .set-default-row { display: flex; align-items: center; justify-content: center; gap: 8px; padding: 8px; background: rgba(0,150,136,0.05); border-radius: 6px; margin: 10px 0; font-size: 13px; cursor: pointer; }
    .set-default-row input { width: 18px; height: 18px; margin: 0; }
</style>
</head><body>
{{ navbar|safe }}
<div class="card">
    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:15px; flex-wrap:wrap; gap:10px;">
        <div style="font-size:14px; color:var(--gray);">
            <a href="/" style="color:var(--primary); text-decoration:none;">🏠 根目录</a>
            {% for c in crumbs %} / <a href="/folder/{{ c[0] }}" style="color:var(--primary); text-decoration:none;">{{ c[1] }}</a>{% endfor %}
        </div>
        <button class="btn btn-sm btn-blue" onclick="showDashboard()" style="margin:0;">📊 账户空间看板</button>
    </div>

    <div style="margin-bottom: 20px;">
        <div class="folder-actions-bar">
            <div class="folder-actions-bar-left">{% if cf[0] != 0 %}<a href="/folder/{{ cf[2] }}" class="btn btn-sm btn-gray">⬅️ 返回</a>{% endif %}</div>
            <div class="folder-actions-bar-right">
                <a href="/search?folder_id={{ cf[0] }}&scope=folder" class="btn btn-sm btn-gray">🔍 搜本目录</a>
                <a href="/toggle_view" class="btn btn-sm btn-gray">{% if view_mode == 'card' %}📄 列表{% else %}🗂️ 卡片{% endif %}</a>
                <select onchange="location.href='?sort='+this.value" class="move-select"><option value="time_desc" {% if sort_by=='time_desc' %}selected{% endif %}>最新优先</option><option value="title_asc" {% if sort_by=='title_asc' %}selected{% endif %}>名称升序</option><option value="title_desc" {% if sort_by=='title_desc' %}selected{% endif %}>名称降序</option></select>
                {% if cf[0] == 0 %}<button class="btn btn-sm btn-purple" onclick="startPull(this)">☁️ 拉取</button>{% endif %}
            </div>
        </div>
        <h2 style="margin:0; border:none; font-size: 22px; color: var(--primary-dark); display:flex; align-items:center; gap:8px;"><span style="font-size:26px;">📁</span> <span style="word-break: break-all; line-height: 1.3;">{{ cf[1] }}</span></h2>
    </div>
    
    <form method="POST" action="/add_folder" style="display:flex; gap:10px; margin-bottom: 20px;"><input type="hidden" name="parent_id" value="{{ cf[0] }}"><input type="text" name="folder_name" placeholder="新建子文件夹..." required style="margin:0;"><button type="submit" style="width:80px; margin:0; flex-shrink:0;">新建</button></form>
    
    <div class="batch-action-bar" id="folderBatchBar" style="background:#e3f2fd; border-color:var(--blue);">
        <span>已选 <b id="folderSelCount">0</b> 个文件夹</span>
        <div style="display:flex; gap:10px; flex-wrap:wrap;">
            <select id="folderBatchMoveTarget" class="move-select" style="width:150px;"><option value="">移动到...</option><option value="0">🏠 根目录</option>{% for f in all_folders %}<option value="{{ f[0] }}">{{ f[1] }}</option>{% endfor %}</select>
            <button class="btn btn-sm btn-blue" onclick="doFolderBatchMove()" style="margin:0;">移动</button><button class="btn btn-sm btn-red" onclick="doFolderBatchDelete()" style="margin:0;">删除</button>
        </div>
    </div>
    
    <div class="folder-grid">
        {% for f in sf %}
        <div class="folder-card" draggable="true" ondragstart="dragStart(event, 'folder', {{ f[0] }})" ondragover="allowDrop(event)" ondrop="dropOnFolder(event, {{ f[0] }})" onclick="if(event.target.tagName!=='INPUT' && event.target.tagName!=='BUTTON' && event.target.tagName!=='A') location.href='/folder/{{ f[0] }}'">
            <input type="checkbox" class="folder-checkbox" value="{{ f[0] }}" onchange="updateFolderBatchBar()" onclick="event.stopPropagation()">
            <div class="folder-btn-group">
                <button class="folder-act-btn edit-folder-btn" data-fid="{{ f[0] }}" data-fname="{{ f[1] }}" onclick="event.stopPropagation(); editFolderFrom(this)" title="重命名">✏️</button>
                <button class="folder-act-btn move-folder-btn" onclick="event.stopPropagation(); showMoveFolderModal({{ f[0] }})" title="移动文件夹">📂</button>
                <button class="folder-act-btn del-folder-btn" data-fid="{{ f[0] }}" data-fname="{{ f[1] }}" onclick="event.stopPropagation(); deleteFolderFrom(this)" title="删除">&times;</button>
            </div>
            <a href="/folder/{{ f[0] }}" style="margin-top:15px;">📁 {{ f[1] }}</a>
            <div style="font-size:11px; color:var(--gray); margin-top:5px; font-weight:normal;">{{ fmt(folder_sizes[f[0]]) }}</div>
        </div>
        {% endfor %}
    </div>
</div>

<div class="card" ondragover="allowDrop(event)" ondrop="dropOnFolder(event, {{ cf[0] }})">
    <div class="folder-actions-bar"><h3 style="margin:0; border:none;">已归档文件 ({{ total_items }})</h3><a href="/upload?folder_id={{ cf[0] }}" class="btn btn-sm btn-blue" style="margin:0;">➕ 添加任务</a></div>
    <div class="batch-action-bar" id="batchBar">
        <span>已选择 <b id="selCount">0</b> 项</span>
        <div style="display:flex; gap:10px; flex-wrap:wrap;">
            <button class="btn btn-sm btn-purple" onclick="showBatchAppendModal()">批量追加文件/文件夹</button>
            <select id="batchMoveTarget" class="move-select" style="width:150px;"><option value="">移动到...</option><option value="0">🏠 根目录</option>{% for f in all_folders %}<option value="{{ f[0] }}">{{ f[1] }}</option>{% endfor %}</select>
            <button class="btn btn-sm btn-blue" onclick="doBatchMove()" style="margin:0;">移动</button><button class="btn btn-sm btn-red" onclick="doBatchTrash()" style="margin:0;">回收</button>
        </div>
    </div>
    {% for i in items %}
    <div class="item-row {% if view_mode == 'list' %}list-mode{% endif %}" id="item-row-{{ i[1] }}" draggable="true" ondragstart="dragStart(event, 'item', {{ i[0] }})">
        <input type="checkbox" class="batch-checkbox" value="{{ i[0] }}" onchange="updateBatchBar()">
        {% if view_mode == 'card' %}
        <div class="thumb-wrapper" style="background:#ddd; border-radius:8px; overflow:hidden; position:relative;">
            {% if has_thumb(i[1]) %}
            <img src="/thumb/{{ i[1] }}?t={{ i[0] }}" class="thumb-img" data-ident="{{ i[1] }}" data-title="{{ i[2] }}" onclick="showThumbModal(this.dataset.ident, this.dataset.title)" style="width:100%; height:100%; object-fit:cover; cursor:pointer;">
            {% else %}
            <div class="thumb-placeholder" data-ident="{{ i[1] }}" data-title="{{ i[2] }}" onclick="showThumbModal(this.dataset.ident, this.dataset.title)" style="display:flex; position:absolute; top:0; left:0; width:100%; height:100%; z-index:1; background:#e0e0e0; color:#888; align-items:center; justify-content:center; flex-direction:column; font-size:12px; border:1px dashed #ccc;"><span>🖼️</span>点我查看</div>
            {% endif %}
        </div>
        {% endif %}
        <div class="item-main">
            <div class="item-header">
                <div class="item-info"><strong>{{ i[2] }}</strong><span class="file-stats" id="file-count-{{ i[1] }}" style="display: {% if i[5] > 0 %}inline-block{% else %}none{% endif %};">📂 {{ i[5] }} 个文件</span><br><small>ID: {{ i[1] }}</small></div>
                <form method="POST" action="/move_item" style="margin:0; flex-shrink:0;"><input type="hidden" name="item_id" value="{{ i[0] }}"><select name="target_folder_id" onchange="this.form.submit()" class="move-select"><option value="">移动...</option><option value="0">🏠 根目录</option>{% for f in all_folders %}<option value="{{ f[0] }}">{{ f[1] }}</option>{% endfor %}</select></form>
            </div>
        </div>
        <div class="item-actions">
            <a href="https://archive.org/details/{{ i[1] }}" target="_blank" class="btn btn-sm btn-blue">🌐 官方页</a>
            <button class="btn btn-sm btn-blue" data-title="{{ i[2] }}" data-id="{{ i[1] }}" onclick="copyMarkdownFrom(this)" style="background:var(--blue);">🔗 MD</button>
            <button class="btn btn-sm btn-green" data-id="{{ i[1] }}" onclick="smartDownloadFrom(this)">📂 文件</button>
            <a href="/append_files/{{ i[0] }}" class="btn btn-sm btn-purple">➕ 追加</a>
            <button class="btn btn-sm btn-brown" data-filename="{{ i[1] }}_archive.torrent" data-url="https://archive.org/download/{{ i[1] }}/{{ i[1] }}_archive.torrent" onclick="triggerDownload(this.dataset.filename, this.dataset.url)">🌱 种子</button>
            <button class="btn btn-sm btn-brown" data-id="{{ i[1] }}" onclick="getMagnetFrom(this)" style="background:#5D4037;">🧲 磁力</button>
            <a href="/item_detail/{{ i[1] }}" class="btn btn-sm btn-gray">⚙️ 管理</a>
            <a href="/upload?edit_id={{ i[0] }}" class="btn btn-sm btn-gray">✏️ 编辑</a>
            <a href="/trash_item/{{ i[0] }}" class="btn btn-sm btn-red" onclick="return confirm('移入回收站？')">🗑️ 删除</a>
        </div>
    </div>
    {% endfor %}
    {% if total_items > 20 %}
    <div style="display:flex; justify-content:center; flex-wrap:wrap; gap:10px; margin-top:20px;">
        {% if page > 1 %}<a href="?page={{ page-1 }}&sort={{ sort_by }}" class="btn btn-sm btn-gray">上一页</a>{% endif %}
        <span style="padding:6px 12px; font-size:13px;">第 {{ page }} 页 / 共 {{ (total_items + 19) // 20 }} 页</span>
        {% if page * 20 < total_items %}<a href="?page={{ page+1 }}&sort={{ sort_by }}" class="btn btn-sm btn-gray">下一页</a>{% endif %}
    </div>
    {% endif %}
</div>

<!-- 账户数据看板弹窗 -->
<div class="modal-overlay" id="dashboardModal">
    <div class="modal-content" style="max-width:500px; text-align:center;">
        <div class="modal-title"><span>📊 账户空间看板</span><span class="modal-close" onclick="closeDashboard()">&times;</span></div>
        <div id="dashboardContent" style="padding: 20px 0;">
            <div class="spinner" style="width:40px; height:40px; border-width:4px; margin-bottom:10px;"></div>
            <p>正在向官方拉取数据并统计中...<br><span style="font-size:12px; color:var(--gray);">(如果条目过多可能需要几十秒)</span></p>
        </div>
    </div>
</div>

<!-- 移动文件夹弹窗 -->
<div class="modal-overlay" id="moveFolderModal"><div class="modal-content" style="max-width:400px;"><div class="modal-title"><span>📂 移动文件夹</span><span class="modal-close" onclick="document.getElementById('moveFolderModal').style.display='none'">&times;</span></div><div class="modal-body"><p style="font-size:14px; color:var(--gray); margin-bottom:10px;">请选择要移动到的目标文件夹：</p><select id="move_target_select" style="margin-bottom:15px;"><option value="0">🏠 根目录</option>{% for f in all_folders %}<option value="{{ f[0] }}">{{ f[1] }}</option>{% endfor %}</select><button type="button" class="btn btn-blue" onclick="confirmMoveFolder()">确定移动</button></div></div></div>

<!-- 文件列表弹窗 -->
<div class="modal-overlay" id="filePickerModal">
    <div class="modal-content" style="max-width:800px; height: 85vh; display:flex; flex-direction:column;">
        <div class="modal-title" style="flex-shrink:0;"><span>📂 文件列表</span><span class="modal-close" onclick="closePicker()">&times;</span></div>
        
        <div style="display:flex; justify-content:space-between; align-items:center; padding: 10px 0; border-bottom: 1px solid var(--border); flex-wrap:wrap; gap:10px;" id="pickerToolbarWrapper">
            <div style="display:flex; gap:8px; flex-wrap:wrap;" id="pickerToolbar">
                <button class="btn btn-sm btn-blue" id="view-toggle-btn" onclick="toggleFileView()">📁 切换树状视图</button>
                <button class="btn btn-sm btn-gray" onclick="selectAllFiles()">全选/取消</button>
                <button class="btn btn-sm btn-green" onclick="downloadSelectedFiles()">⬇️ 下载选中</button>
                <button class="btn btn-sm btn-red" onclick="deleteSelectedFiles()">🗑️ 删除选中</button>
            </div>
            <button class="btn btn-sm btn-orange" onclick="refreshPicker()" id="refreshBtn">🔄 重新提取</button>
        </div>

        <div class="modal-body" id="pickerBody" style="flex:1; overflow-y:auto; padding-top:10px;">
            <p><span class="spinner"></span>加载中...</p>
        </div>
    </div>
</div>

<!-- 磁力弹窗 -->
<div class="modal-overlay" id="magnetModal">
    <div class="modal-content" style="max-width:400px; text-align:center;">
        <div class="modal-title" style="justify-content:center;">🧲 磁力链接</div>
        <div class="modal-body">
            <p style="font-size:13px; color:var(--gray); margin-bottom:10px;">首次解析完成，请手动复制：</p>
            <textarea id="magnetText" readonly style="height:80px; font-size:12px; margin-bottom:15px; word-break:break-all; background:rgba(0,0,0,0.05);"></textarea>
            <button class="btn btn-blue" onclick="copyMagnetText()" style="margin-bottom:10px;">📋 复制链接</button>
            <button class="btn btn-gray" onclick="document.getElementById('magnetModal').style.display='none'">关闭</button>
        </div>
    </div>
</div>

<!-- 缩略图弹窗 -->
<div class="modal-overlay" id="thumbModal">
    <div class="modal-content" style="max-width:450px; padding:10px;">
        <div class="modal-title"><span id="thumbTitle">🖼️ 缩略图</span><span class="modal-close" onclick="closeThumbModal()">&times;</span></div>
        <div style="text-align:center; margin-bottom:15px;">
            <img id="thumbModalImg" src="" style="max-width:100%; max-height:50vh; border-radius:8px; object-fit:contain;">
        </div>
        <div style="display:flex; flex-wrap:wrap; gap:8px; justify-content:center;">
            <button class="btn btn-sm btn-blue" onclick="refreshThumb()" style="flex:1; min-width:45%; margin:0;">🔄 重新获取</button>
            <a id="thumbDownloadBtn" href="" download class="btn btn-sm btn-green" style="flex:1; min-width:45%; margin:0;">💾 保存图片</a>
            <button class="btn btn-sm btn-orange" onclick="document.getElementById('thumbFileInput').click()" style="flex:1; min-width:45%; margin:0;">🖼️ 自定义</button>
            <button class="btn btn-sm btn-gray" onclick="closeThumbModal()" style="flex:1; min-width:45%; margin:0;">❌ 关闭</button>
        </div>
        <input type="file" id="thumbFileInput" accept="image/*" style="display:none;" onchange="uploadCustomThumb()">
        <div id="thumbUploadStatus" style="text-align:center; font-size:12px; color:var(--primary); margin-top:10px;"></div>
    </div>
</div>

<!-- 单文件下载选择弹窗 -->
<div class="modal-overlay" id="downloadChoiceModal">
    <div class="modal-content" style="max-width:380px; text-align:center;">
        <div class="modal-title" style="justify-content:center;">⬇️ 选择下载方式</div>
        <div class="modal-body" style="margin-bottom:15px; font-size:14px; color:var(--gray);">
            请选择如何下载此文件：
        </div>
        <div style="display:flex; flex-direction:column; gap:10px;">
            <button class="btn btn-blue" onclick="confirmDownload('termux')" style="margin:0; padding:12px;">📱 Termux 后台下载 (推荐)</button>
            <button class="btn btn-green" onclick="confirmDownload('browser')" style="margin:0; padding:12px;">🌐 浏览器直接下载</button>
            <button class="btn btn-gray" onclick="document.getElementById('downloadChoiceModal').style.display='none'" style="margin:0; padding:12px;">❌ 取消</button>
        </div>
        <label class="set-default-row" onclick="event.stopPropagation();">
            <input type="checkbox" id="setAsDefaultSingle">
            <span>设为默认（以后单文件不再询问）</span>
        </label>
    </div>
</div>

<!-- 批量下载选择弹窗 -->
<div class="modal-overlay" id="batchDownloadChoiceModal">
    <div class="modal-content" style="max-width:420px; text-align:center;">
        <div class="modal-title" style="justify-content:center;">⬇️ 批量下载方式</div>
        <div class="modal-body" style="margin-bottom:15px; font-size:14px; color:var(--gray);" id="batchDownloadInfo">
            即将提交下载任务...
        </div>
        <div style="display:flex; flex-direction:column; gap:10px;">
            <button class="btn btn-blue" onclick="confirmBatchDownload('termux')" style="margin:0; padding:12px;">📱 全部走 Termux 后台队列</button>
            <button class="btn btn-green" onclick="confirmBatchDownload('browser')" style="margin:0; padding:12px;">🌐 全部走浏览器直接下载</button>
            <button class="btn btn-gray" onclick="document.getElementById('batchDownloadChoiceModal').style.display='none'" style="margin:0; padding:12px;">❌ 取消</button>
        </div>
        <label class="set-default-row" onclick="event.stopPropagation();">
            <input type="checkbox" id="setAsDefaultBatch">
            <span>设为默认（以后批量下载不再询问）</span>
        </label>
    </div>
</div>

<!-- 预检 Loading 弹窗 -->
<div class="modal-overlay" id="loadingModal" style="background: rgba(0,0,0,0.7);">
    <div class="modal-content" style="max-width:250px; text-align:center; padding:20px;">
        <div class="spinner" style="width:40px; height:40px; margin-bottom:15px; border-width:4px;"></div>
        <div style="font-size:15px; font-weight:bold; color:var(--primary-dark);" id="loadingText">正在向代理请求数据...</div>
    </div>
</div>

<!-- 在线预览弹窗 -->
<div class="preview-modal" id="previewModal" onclick="if(event.target.id==='previewModal')closePreview()">
    <div style="position:absolute; top:20px; right:20px; color:white; font-size:30px; cursor:pointer;" onclick="closePreview()">&times;</div>
    <div id="previewContainer" style="display:flex; justify-content:center; align-items:center; width:100%; height:100%;"></div>
</div>

<!-- ==================== 批量追加弹窗 ==================== -->
<div class="modal-overlay" id="batchAppendModal" style="background:rgba(0,0,0,0.8);">
    <div class="modal-content" style="max-width:600px; max-height:90vh; display:flex; flex-direction:column; padding:15px;">
        <div class="modal-title" style="margin-bottom:10px;"><span>➕ 批量追加文件</span><span class="modal-close" onclick="document.getElementById('batchAppendModal').style.display='none'">&times;</span></div>
        
        <div class="modal-body" style="flex:1; overflow-y:auto; padding-right:5px;">
            <div class="warning-box" style="margin-bottom:15px; padding:10px; font-size:13px;">💡 给选中的多个条目同时追加文件。支持同时选择多个文件夹和多个文件（可多次点击添加）。</div>
            
            <div class="file-input-box" style="position:relative; padding-bottom:10px;">
                <label style="font-weight:bold; font-size:15px; pointer-events:none;">1. 选择要追加的本地文件/文件夹</label>
                
                <div style="margin-top:15px; margin-bottom:15px; padding:15px; background:rgba(255,152,0,0.1); border:1px solid var(--orange); border-radius:8px;">
                    <div style="font-size:14px; color:var(--orange); font-weight:bold; margin-bottom:8px;">📁 选择手机内的数据（可多次选择追加）</div>
                    
                    <div id="ba_path_list" style="max-height:120px; overflow-y:auto; border:1px solid var(--border); background:#fff; border-radius:6px; padding:8px; margin-bottom:10px;">
                        <span style="color:#888; font-size:13px;">尚未选择任何文件或文件夹...</span>
                    </div>
                    
                    <div style="display:flex; gap:8px; align-items:center;">
                        <!-- 【修复 BB3】原 if(...)stmt; else alert(...) 是 JS 语法错误，改为三元表达式 -->
                        <button type="button" class="btn btn-orange" onclick="window.baPicker ? baPicker.open() : alert('本地选择组件加载失败，请刷新页面后重试')" style="width:auto; margin:0; padding:0 15px; height:42px; flex:1;">📂 点击浏览并添加</button>
                        <button type="button" class="btn btn-gray" onclick="window.baPicker ? baPicker.clearPaths() : alert('本地选择组件加载失败，请刷新页面后重试')" style="width:80px; margin:0; padding:0 10px; height:42px;">清空</button>
                    </div>
                </div>

                <details style="margin-top:10px; background:rgba(33,150,243,0.05); border:1px solid var(--blue); border-radius:8px; padding:10px;">
                    <summary style="cursor:pointer; color:var(--blue); font-size:13px; font-weight:bold;">🌐 或者通过浏览器选择 (支持多选 · 支持文件夹 · 支持分片)</summary>
                    <div style="padding:10px; margin-top:10px; background:var(--card-bg); border-radius:6px;">
                        <input type="file" id="ba_files" multiple style="display:block; margin:0; padding:10px; background:var(--bg); border:1px dashed var(--blue); border-radius:6px;">
                        <div style="display:flex; gap:8px; margin-top:8px;">
                            <!-- 【修复 BB3】三元表达式 -->
                            <button type="button" class="btn btn-sm btn-blue" onclick="window.baPicker ? baPicker.triggerDirPick() : alert('本地选择组件加载失败，请刷新页面后重试')" style="margin:0; flex:1; font-size:12px;">📁 从文件夹选择</button>
                            <button type="button" class="btn btn-sm btn-gray" onclick="window.baPicker ? baPicker.clearBrowserFiles() : alert('本地选择组件加载失败，请刷新页面后重试')" style="margin:0; flex:1; font-size:12px;">❌ 清空浏览器选择</button>
                        </div>
                        <span class="tip" style="margin-top:8px; font-size:11px; background:rgba(33,150,243,0.08); border-left-color:var(--blue);">💡 浏览器无法选择文件夹时可以用上面「本地路径」模式。总大小超50MB会自动切片。</span>
                    </div>
                </details>
                <div id="ba_info" style="font-size:13px; color:var(--primary); font-weight:bold; margin-top:10px;"></div>
            </div>
            
            <details style="margin-top:15px;" open>
                <summary>2. 高级设置</summary>
                <div class="adv-container">
                    <h3 style="font-size:14px; color:var(--primary); border:none; margin-top:0;">📦 ZIP 压缩包处理</h3>
                    <label class="adv-label"><input type="checkbox" id="ba_unzip_zip" onchange="if(this.checked) document.getElementById('ba_zip_upload').checked = false;"> <span>1. 自动解压 ZIP</span></label>
                    <label class="adv-label"><input type="checkbox" id="ba_zip_upload" onchange="if(this.checked) document.getElementById('ba_unzip_zip').checked = false;"> <span>2. 打包为 ZIP 再传</span></label>
                    <input type="text" id="ba_zip_custom_name" placeholder="自定义ZIP压缩包名称" style="width:180px; padding:6px; margin-left:10px;">
                    
                    <h3 style="font-size:14px; color:var(--primary); border:none; margin-top:20px;">⚙️ 大文件分块与断点续传</h3>
                    <label>文件分块阈值 (MB):</label>
                    <input type="number" id="ba_chunk_threshold" value="200" min="20" max="1024">
                    
                    <label>单块大小 (MB):</label>
                    <input type="number" id="ba_chunk_size" value="10" min="5" max="50">

                    <label class="adv-label" style="margin-top:15px; color:var(--danger);">
                        <input type="checkbox" id="ba_overwrite_same_file"> 
                        <span>3. 覆盖云端已有同名文件</span>
                    </label>

                    <h3 style="font-size:14px; color:var(--primary); border:none; margin-top:20px;">⚙️ 官方底层并发参数</h3>
                    <label>文件并发数 (1-16):</label>
                    <input type="number" id="ba_concurrency" value="8" min="1" max="16">
                    
                    <label class="adv-label" style="margin-top:15px;"><input type="checkbox" id="ba_generate_torrent" checked> <span>4. 生成衍生文件 / BT种子</span></label>
                    <label class="adv-label"><input type="checkbox" id="ba_no_derive"> <span>5. 跳过生成衍生文件</span></label>
                    <label class="adv-label"><input type="checkbox" id="ba_keep_old"> <span>6. 保留旧版本文件</span></label>
                    <label class="adv-label"><input type="checkbox" id="ba_no_directory"> <span>7. 忽略文件夹结构</span></label>
                    <label class="adv-label"><input type="checkbox" id="ba_no_index"> <span>8. 禁止搜索引擎索引</span></label>
                    <label class="adv-label"><input type="checkbox" id="ba_interactive_priority"> <span>9. 交互优先级</span></label>
                    <label class="adv-label"><input type="checkbox" id="ba_dark_mode"> <span>10. 官方页面暗黑模式</span></label>
                    <label class="adv-label"><input type="checkbox" id="ba_skip_meta"> <span>11. 跳过生成 meta.xml</span></label>
                    <label class="adv-label"><input type="checkbox" id="ba_checksum"> <span>12. 上传后计算校验和</span></label>

                    <label>13. 预估文件大小 (可选):</label>
                    <input type="text" id="ba_size_hint" placeholder="例如 500MB, 2GB">
                    
                    <label>14. 自动重试次数 (最高 5 次):</label>
                    <input type="number" id="ba_retries" min="0" max="5" placeholder="默认 5">
                </div>
            </details>
        </div>
        
        <div style="margin-top:15px; display:flex; gap:10px;">
            <button type="button" id="ba_submit_btn" class="btn btn-purple" onclick="doBatchAppendSubmit(this)" style="margin:0; flex:1; padding:12px;">🚀 加入追加队列</button>
            <button type="button" class="btn btn-gray" onclick="document.getElementById('batchAppendModal').style.display='none'" style="margin:0; flex:1; padding:12px;">❌ 取消</button>
        </div>
    </div>
</div>

"""

_INDEX_HTML_BOTTOM = r"""<script>
    const PROXY_DL = {{ 'true' if proxy_dl else 'false' }};
    const DL_DIR = "{{ dl_dir if dl_dir is defined else '/sdcard/Download' }}";

    let SINGLE_DOWNLOAD_DEFAULT = localStorage.getItem('single_download_default') || "{{ single_download_default }}";
    let BATCH_DOWNLOAD_DEFAULT = localStorage.getItem('batch_download_default') || "{{ batch_download_default }}";

    function getDLUrl(url) { return PROXY_DL ? '/api/download_proxy?url=' + encodeURIComponent(url) : url; }

    function intSafe(v) {
        let n = parseInt(v);
        return isNaN(n) ? 0 : n;
    }

    function buildEncDownloadUrl(identifier, filename) {
        let encIdent = encodeURIComponent(identifier);
        let encPath = String(filename || '').split('/').map(encodeURIComponent).join('/');
        return `https://archive.org/download/${encIdent}/${encPath}`;
    }

    async function showDashboard() {
        document.getElementById('dashboardModal').style.display = 'flex';
        let content = document.getElementById('dashboardContent');
        content.innerHTML = '<div class="spinner" style="width:40px; height:40px; border-width:4px; margin-bottom:10px;"></div><p>正在向官方拉取数据并统计中...<br><span style="font-size:12px; color:var(--gray);">(如果条目过多可能需要几十秒)</span></p>';
        
        try {
            let res = await (await fetch('/api/user_stats')).json();
            if (res.status === 'success') {
                let sizeStr = res.total_size > 1024*1024*1024 ? (res.total_size/1024/1024/1024).toFixed(2) + ' GB' : (res.total_size/1024/1024).toFixed(1) + ' MB';
                content.innerHTML = `
                    <div class="stats-grid">
                        <div class="stat-card">
                            <div class="stat-value">${res.total_items}</div>
                            <div class="stat-label">📁 条目总数</div>
                        </div>
                        <div class="stat-card">
                            <div class="stat-value">${res.total_files}</div>
                            <div class="stat-label">📄 文件总数</div>
                        </div>
                        <div class="stat-card" style="grid-column: 1 / -1;">
                            <div class="stat-value" style="color:var(--orange);">${sizeStr}</div>
                            <div class="stat-label">💾 总占用空间</div>
                        </div>
                    </div>
                    <p style="font-size:12px; color:var(--gray); margin-top:10px;">数据实时计算自 IA 官方服务器</p>
                `;
            } else {
                content.innerHTML = `<p style="color:red;">❌ 拉取失败: ${res.msg}</p>`;
            }
        } catch(e) {
            content.innerHTML = `<p style="color:red;">❌ 网络异常，无法获取数据</p>`;
        }
    }
    function closeDashboard() { document.getElementById('dashboardModal').style.display = 'none'; }

    let movingFolderId = null;
    function updateFolderBatchBar() { let checked = document.querySelectorAll('.folder-checkbox:checked'); let bar = document.getElementById('folderBatchBar'); if (checked.length > 0) { bar.style.display = 'flex'; document.getElementById('folderSelCount').innerText = checked.length; } else { bar.style.display = 'none'; } }
    async function doFolderBatchMove() { let target = document.getElementById('folderBatchMoveTarget').value; if (!target) return alert('请选择目标文件夹'); let ids = Array.from(document.querySelectorAll('.folder-checkbox:checked')).map(cb => cb.value); let res = await (await fetch('/api/batch_folder_action', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({action: 'move', ids: ids, target: target}) })).json(); if(res.status === 'success') location.reload(); else alert('失败: '+res.msg); }
    async function doFolderBatchDelete() { if (!confirm('确定要删除选中的文件夹吗？里面的条目会自动移回上一级。')) return; let ids = Array.from(document.querySelectorAll('.folder-checkbox:checked')).map(cb => cb.value); let res = await (await fetch('/api/batch_folder_action', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({action: 'delete', ids: ids}) })).json(); if(res.status === 'success') location.reload(); else alert('失败: '+res.msg); }
    function showMoveFolderModal(fid) { movingFolderId = fid; let sel = document.getElementById('move_target_select'); for(let i=0; i<sel.options.length; i++) sel.options[i].disabled = false; for(let i=0; i<sel.options.length; i++) if(String(sel.options[i].value) === String(fid)) sel.options[i].disabled = true; document.getElementById('moveFolderModal').style.display = 'flex'; }
    async function confirmMoveFolder() { let targetId = document.getElementById('move_target_select').value; if (String(targetId) === String(movingFolderId)) return alert('不能移动到自己内部'); let res = await (await fetch('/api/move_folder', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({folder_id: movingFolderId, target_id: parseInt(targetId)})})).json(); if (res.status === 'success') location.reload(); else alert(res.msg); }
    function editFolderFrom(btn) { let id = btn.dataset.fid, oldName = btn.dataset.fname; let newName = prompt("输入新的文件夹名称：", oldName); if (!newName || newName === oldName) return; let form = document.createElement('form'); form.method = 'POST'; form.action = '/edit_folder'; let i1 = document.createElement('input'); i1.type='hidden'; i1.name='folder_id'; i1.value=id; form.appendChild(i1); let i2 = document.createElement('input'); i2.type='hidden'; i2.name='new_name'; i2.value=newName; form.appendChild(i2); document.body.appendChild(form); form.submit(); }
    function deleteFolderFrom(btn) { let id = btn.dataset.fid, name = btn.dataset.fname; if(!confirm(`确定删除文件夹【${name}】吗？\n里面的条目会自动移回上一级。`)) return; let form = document.createElement('form'); form.method = 'POST'; form.action = '/delete_folder'; let input = document.createElement('input'); input.type = 'hidden'; input.name = 'folder_id'; input.value = id; form.appendChild(input); document.body.appendChild(form); form.submit(); }

    function updateBatchBar() { let checked = document.querySelectorAll('.batch-checkbox:checked'); let bar = document.getElementById('batchBar'); if (checked.length > 0) { bar.style.display = 'flex'; document.getElementById('selCount').innerText = checked.length; } else { bar.style.display = 'none'; } }
    async function doBatchMove() { let target = document.getElementById('batchMoveTarget').value; if (!target) return alert('请选择目标文件夹'); let ids = Array.from(document.querySelectorAll('.batch-checkbox:checked')).map(cb => cb.value); let res = await (await fetch('/api/batch_action', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({action: 'move', ids: ids, target: target}) })).json(); if(res.status === 'success') location.reload(); else alert('失败: '+res.msg); }
    async function doBatchTrash() { if (!confirm('确定将选中的文件移入回收站？')) return; let ids = Array.from(document.querySelectorAll('.batch-checkbox:checked')).map(cb => cb.value); let res = await (await fetch('/api/batch_action', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({action: 'trash', ids: ids}) })).json(); if(res.status === 'success') location.reload(); else alert('失败: '+res.msg); }
    
    async function getMagnetFrom(btn) { 
        let identifier = btn.dataset.id; 
        let oldText = btn.innerText; 
        btn.innerText = "解析中..."; 
        btn.disabled = true; 
        
        try { 
            let res = await (await fetch(`/api/magnet/${identifier}`)).json(); 
            if (res.magnet) { 
                if (res.cached) {
                    let ta = document.createElement("textarea"); 
                    ta.style.position = "fixed"; ta.style.opacity = "0"; 
                    document.body.appendChild(ta);
                    ta.value = res.magnet; 
                    ta.select(); 
                    ta.setSelectionRange(0, 99999); 
                    try { 
                        document.execCommand('copy'); 
                        alert('✅ 磁力链接已自动复制！'); 
                    } catch (err) { 
                        alert('❌ 自动复制被浏览器拦截，请手动复制：\n' + res.magnet); 
                    } 
                    document.body.removeChild(ta);
                } else {
                    document.getElementById('magnetText').value = res.magnet;
                    document.getElementById('magnetModal').style.display = 'flex';
                }
            } else {
                alert('❌ 获取磁力失败，请检查网络');
            }
        } catch (e) { alert('出错: ' + e); } 
        finally { btn.innerText = oldText; btn.disabled = false; } 
    }

    function copyMagnetText() {
        let text = document.getElementById('magnetText').value;
        let ta = document.createElement("textarea"); 
        ta.style.position = "fixed"; ta.style.opacity = "0"; 
        document.body.appendChild(ta);
        ta.value = text; 
        ta.select(); 
        ta.setSelectionRange(0, 99999); 
        try { 
            document.execCommand('copy'); 
            alert('✅ 磁力链接已复制！'); 
            document.getElementById('magnetModal').style.display='none';
        } catch (err) { 
            alert('❌ 复制失败，请手动长按文本框复制'); 
        } 
        document.body.removeChild(ta);
    }
    
    function copyMarkdownFrom(btn) { 
        let pureUrl = `https://archive.org/details/${btn.dataset.id}`;
        copyText(`> **${btn.dataset.title}**\n> [点击前往 Internet Archive 详情页](${pureUrl})`); 
    }
    
    let currentIdentifier = null;
    let currentFilesData = [];
    let isTreeView = false;
    
    let pickerToolbarDefaultHtml = document.getElementById('pickerToolbar').innerHTML;
    
    function restorePickerToolbar() {
        document.getElementById('pickerToolbar').innerHTML = pickerToolbarDefaultHtml;
        document.getElementById('refreshBtn').style.display = 'inline-block';
        isTreeView = false;
    }

    function toggleFileView() {
        isTreeView = !isTreeView;
        document.getElementById('view-toggle-btn').innerText = isTreeView ? '📄 切换平铺视图' : '📁 切换树状视图';
        renderFiles(currentFilesData);
    }

    async function smartDownloadFrom(btn) { 
        let identifier = btn.dataset.id; currentIdentifier = identifier; 
        btn.innerText = "⏳..."; btn.disabled = true; 
        
        restorePickerToolbar();
        
        document.getElementById('pickerBody').innerHTML = '<p><span class="spinner"></span>正在读取本地缓存...</p>'; 
        document.getElementById('filePickerModal').style.display = 'flex'; 
        try { 
            let c = await (await fetch(`/api/file_cache/${identifier}`)).json(); 
            if (c.cached && c.files && c.files.length > 0) { 
                currentFilesData = c.files;
                renderFiles(currentFilesData); 
            } else { 
                document.getElementById('pickerBody').innerHTML = `<p style="color:var(--orange); font-weight:bold; margin-bottom:10px;">本地暂无文件记录</p><p style="font-size:13px; color:var(--gray);">若要获取，请点击下方的「🔄 重新提取」按钮。此操作会发起网络请求。</p>`; 
            } 
        } catch (e) { 
            document.getElementById('pickerBody').innerHTML = '<p style="color:red;">❌ 读取本地缓存异常</p>'; 
        } finally { 
            btn.innerText = "📂 文件"; 
            btn.disabled = false; 
        } 
    }

    async function refreshPicker() { 
        if(!currentIdentifier) return; 
        document.getElementById('pickerBody').innerHTML = '<p><span class="spinner"></span>从官方拉取中...</p>'; 
        try {
            // 【R217 修复】{{ detail_fetch_timeout_ms }} → {{ detail_fetch_timeout_ms|default(15000) }}
            let controller = new AbortController(); let timeoutId = setTimeout(() => controller.abort(), {{ detail_fetch_timeout_ms|default(15000) }});
            let r = await (await fetch(`/api/force_refresh/${currentIdentifier}`, {signal: controller.signal})).json(); 
            clearTimeout(timeoutId);
            if (r.status === 'cooldown') { alert(r.msg); } 
            let c = await (await fetch(`/api/file_cache/${currentIdentifier}`)).json(); 
            if (c.cached && c.files && c.files.length > 0) { 
                currentFilesData = c.files;
                renderFiles(currentFilesData); 
                let cs = document.getElementById(`file-count-${currentIdentifier}`); 
                if(cs){cs.innerText=`📂 ${c.files.length} 个文件`; cs.style.display='inline-block';} 
            } else { document.getElementById('pickerBody').innerHTML = '<p style="color:red;">❌ 获取失败，请检查代理网络</p>'; }
        } catch(e) {
            if(e.name === 'AbortError') document.getElementById('pickerBody').innerHTML = '<p style="color:red;">❌ 请求超时，可能是代理断开</p>';
            else document.getElementById('pickerBody').innerHTML = '<p style="color:red;">❌ 网络异常</p>';
        }
    }

    function selectAllFiles() {
        let cbs = document.querySelectorAll('.tree-file-cb, .tree-folder-cb');
        let allChecked = Array.from(cbs).every(cb => cb.checked);
        cbs.forEach(cb => cb.checked = !allChecked);
    }

    async function deleteSelectedFiles() {
        let checked = Array.from(document.querySelectorAll('.tree-file-cb:checked')).map(cb => cb.value);
        if (checked.length === 0) return alert('请先勾选要删除的文件');
        if (!confirm(`确定要彻底删除这 ${checked.length} 个文件吗？\n（后台会进队列排队，支持暂停接替）`)) return;
        
        let btn = event.target;
        let old = btn.innerText;
        btn.innerText = '提交中...';
        btn.disabled = true;

        try {
            let res = await (await fetch('/api/delete_batch', {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    groups: [{ identifier: currentIdentifier, title: currentIdentifier, filenames: checked }]
                })
            })).json();
            if (res.status === 'success') {
                alert(`✅ 已提交 ${res.count} 个删除任务，请前往「队列」查看进度`);
                closePicker();
            } else {
                alert('失败: ' + res.msg);
            }
        } catch(e) { alert('网络异常'); }
        btn.innerText = old; btn.disabled = false;
    }

    function _getRelName(fullName, folderPath) {
        if (fullName === folderPath) return fullName.split('/').pop();
        return fullName.substring(folderPath.length + 1);
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
                    let real_u = f.custom_direct_url || buildEncDownloadUrl(currentIdentifier, f.filename);
                    folderFiles.push({ filename: _getRelName(f.filename, folderPath), url: real_u, size: intSafe(f.size) });
                }
            });
            if (folderFiles.length > 0) {
                items.push({ type: 'folder', name: folderPath.split('/').pop(), files: folderFiles });
                folder_count++;
            }
        });

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
                    let real_u = f.custom_direct_url || buildEncDownloadUrl(currentIdentifier, f.filename);
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
        document.getElementById('batchDownloadChoiceModal').style.display = 'none';
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
            alert(`✅ 已发送 ${totalFiles} 个文件到浏览器下载\n（浏览器下载不占用网盘队列，仅后端保留记录用于回溯）`);
            closePicker();
        } else {
            try {
                let res = await (await fetch('/api/download_batch', {
                    method: 'POST', headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({items: items, mode: 'termux'})
                })).json();
                if (res.status === 'success') {
                    alert(`✅ 已提交 ${res.count} 个下载任务，请前往「队列」查看进度`);
                    closePicker();
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

    function downloadFolderEvent(btn, event) {
        event.stopPropagation();
        let folderPath = btn.dataset.folderpath;
        let folderFiles = [];
        currentFilesData.forEach(f => {
            if (f.filename.startsWith(folderPath + '/') || f.filename === folderPath) {
                folderFiles.push({
                    filename: _getRelName(f.filename, folderPath),
                    url: f.custom_direct_url || buildEncDownloadUrl(currentIdentifier, f.filename),
                    size: intSafe(f.size)
                });
            }
        });
        downloadSingleFolder(folderPath.split('/').pop(), folderFiles);
    }

    async function downloadSingleFolder(folderName, folderFiles) {
        if (!confirm(`确定要下载文件夹【${folderName}】吗？\n包含 ${folderFiles.length} 个文件，将在后台自动建立目录结构。`)) return;
        let items = [{ type: 'folder', name: folderName, files: folderFiles }];
        if (BATCH_DOWNLOAD_DEFAULT === 'browser') {
            await doBatchDownload(items, 'browser', false);
        } else if (BATCH_DOWNLOAD_DEFAULT === 'termux') {
            await doBatchDownload(items, 'termux', false);
        } else if (BATCH_DOWNLOAD_DEFAULT === 'per_file') {
            for (let f of folderFiles) {
                let real_u = f.url || buildEncDownloadUrl(currentIdentifier, f.filename);
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

    let flatFilesList = [];
    function renderFileRow(f, isTree = false) {
        let idx = flatFilesList.length;
        flatFilesList.push(f);
        
        let real_u = f.custom_direct_url || buildEncDownloadUrl(currentIdentifier, f.filename);
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
            document.getElementById('pickerBody').innerHTML = '<p style="text-align:center; padding:20px;">没有文件</p>';
            return;
        }
        if (isTreeView) {
            let tree = buildTree(files);
            document.getElementById('pickerBody').innerHTML = `<div style="margin-top:10px;">${renderTree(tree)}</div>`;
        } else {
            let html = `<div style="margin-top:10px;">`;
            files.forEach(f => { html += renderFileRow(f, false); });
            html += `</div>`;
            document.getElementById('pickerBody').innerHTML = html;
        }
    }

    function previewByIdx(idx) { previewFile(flatFilesList[idx].filename); }
    function previewZipByIdx(idx) { previewZip(flatFilesList[idx].filename); }
    
    function triggerDownloadByIdx(idx) { 
        let f = flatFilesList[idx];
        let real_u = f.custom_direct_url || buildEncDownloadUrl(currentIdentifier, f.filename);
        let proxy_u = PROXY_DL ? '/api/download_proxy?url=' + encodeURIComponent(real_u) : real_u;
        triggerDownload(f.filename.split('/').pop(), real_u, proxy_u, intSafe(f.size)); 
    }
    
    function copyTextByIdx(idx) {
        let f = flatFilesList[idx];
        let real_u = f.custom_direct_url || buildEncDownloadUrl(currentIdentifier, f.filename);
        copyText(real_u);
    }

    async function renameByIdx(idx) { 
        let f = flatFilesList[idx];
        let oldName = f.filename; 
        let nn = prompt('新名称：', oldName); 
        if(!nn || nn === oldName) return; 
        let res = await (await fetch('/api/rename_file', {
            method:'POST', 
            headers:{'Content-Type':'application/json'}, 
            body:JSON.stringify({
                identifier: currentIdentifier, 
                rename_list: [{old_name: oldName, new_name: nn}]
            })
        })).json(); 
        if(res.status === 'success'){
            alert('✅ ' + (res.msg || '已提交重命名任务'));
            setTimeout(() => refreshPicker(), 3000);
        } else {
            alert(res.msg || '失败');
        }
    }
    async function deleteByIdx(idx) { 
        let f = flatFilesList[idx];
        let fn = f.filename; 
        if(!confirm(`确定要彻底删除文件【${fn}】吗？`)) return; 
        let res = await (await fetch('/api/delete_batch', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({groups: [{identifier: currentIdentifier, title: currentIdentifier, filenames: [fn]}]})})).json(); 
        if(res.status==='success'){ alert(`已提交 ${res.count} 个删除任务，请前往队列查看`); setTimeout(()=>refreshPicker(), 2000); } else alert(res.msg||'失败'); 
    }

    function previewFile(filename, ident = null) {
        let targetIdent = ident || currentIdentifier;
        let ext = filename.split('.').pop().toLowerCase();
        let encodedFn = filename.split('/').map(encodeURIComponent).join('/');
        let url = PROXY_DL ? `/api/preview?identifier=${encodeURIComponent(targetIdent)}&filename=${encodedFn}` : `https://archive.org/download/${encodeURIComponent(targetIdent)}/${encodedFn}`;
        let container = document.getElementById('previewContainer');
        
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
        document.getElementById('previewModal').style.display = 'flex';
    }

    function closePreview() {
        document.getElementById('previewModal').style.display = 'none';
        document.getElementById('previewContainer').innerHTML = '';
    }

    let zipFilesData = [];
    let isZipTreeView = false;
    let currentZipName = "";

    async function previewZip(filename) {
        if (!confirm(`即将向官方请求解析压缩包【${filename}】的内部结构。\n(此过程仅消耗极少流量，请稍候)`)) return;
        
        document.getElementById('loadingText').innerText = '正在向代理请求 ZIP 目录结构...';
        document.getElementById('loadingModal').style.display = 'flex';
        
        try {
            let controller = new AbortController(); let timeoutId = setTimeout(() => controller.abort(), {{ detail_zip_timeout_ms|default(30000) }});
            let res = await (await fetch('/api/zip_content', {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({identifier: currentIdentifier, filename: filename}),
                signal: controller.signal
            })).json();
            clearTimeout(timeoutId);
            
            document.getElementById('loadingModal').style.display = 'none';
            
            if (res.status === 'success' && res.files && res.files.length > 0) {
                zipFilesData = res.files.map(f => ({filename: f.filename, size: f.size}));
                currentZipName = filename;
                isZipTreeView = true; 
                
                document.getElementById('filePickerModal').style.display = 'flex';
                
                let toolbarHtml = `
                    <button class="btn btn-sm btn-blue" id="view-toggle-btn" onclick="toggleZipView()">📄 切换平铺视图</button>
                    <button class="btn btn-sm btn-gray" onclick="selectAllZipFiles()">全选/取消</button>
                    <button class="btn btn-sm btn-green" onclick="downloadSelectedZip()">⬇️ 下载选中</button>
                    <span style="font-size:14px; font-weight:bold; margin-left:10px; word-break:break-all;">📦 ${escapeHtml(filename)}</span>
                    <button class="btn btn-sm btn-gray" onclick="closeZipView()" style="margin-left:auto;">❌ 返回文件列表</button>
                `;
                document.getElementById('pickerToolbar').innerHTML = toolbarHtml;
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
        restorePickerToolbar();
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
                        url: buildEncDownloadUrl(currentIdentifier, fullPath),
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
                        url: buildEncDownloadUrl(currentIdentifier, fullPath),
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
        document.getElementById('pickerBody').innerHTML = html;
    }

    function previewByIdxZip(idx) { 
        let fname = zipFilesData[idx].filename;
        let fullPath = currentZipName + '/' + fname;
        previewFile(fullPath); 
    }
    
    function triggerZipDownloadByIdx(idx) {
        let fname = zipFilesData[idx].filename;
        let fullPath = currentZipName + '/' + fname;
        let real_u = buildEncDownloadUrl(currentIdentifier, fullPath);
        let proxy_u = PROXY_DL ? '/api/download_proxy?url=' + encodeURIComponent(real_u) : real_u;
        triggerDownload(fname.split('/').pop(), real_u, proxy_u, intSafe(zipFilesData[idx].size));
    }

    function downloadZipFolderEvent(btn, event) {
        event.stopPropagation();
        let folderPath = btn.dataset.folderpath;
        let folderFiles = [];
        zipFilesData.forEach(f => {
            if (f.filename.startsWith(folderPath + '/') || f.filename === folderPath) {
                let fullPath = currentZipName + '/' + f.filename;
                folderFiles.push({
                    filename: _getRelName(f.filename, folderPath),
                    url: buildEncDownloadUrl(currentIdentifier, fullPath),
                    size: intSafe(f.size)
                });
            }
        });
        let targetFolderName = currentZipName + "_" + folderPath.replace(/\//g, '_');
        downloadSingleFolder(targetFolderName, folderFiles);
    }

    function closePicker() { 
        document.getElementById('filePickerModal').style.display = 'none'; 
        restorePickerToolbar(); 
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
        document.getElementById('downloadChoiceModal').style.display = 'none';
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

    function copyText(text) { if (!text) return alert('没有内容'); if (navigator.clipboard) navigator.clipboard.writeText(text).then(() => alert('✅ 已复制')).catch(() => fallbackCopy(text)); else fallbackCopy(text); }
    function fallbackCopy(text) { var ta = document.createElement("textarea"); ta.value = text; ta.style.position = "fixed"; ta.style.opacity = "0"; document.body.appendChild(ta); ta.select(); try { document.execCommand('copy'); alert('✅ 已复制'); } catch (err) { alert('❌ 复制失败'); } document.body.removeChild(ta); }

    async function startPull(btn) { 
        let ot = btn.innerText; 
        btn.innerText = "提交中..."; 
        btn.disabled = true; 
        try { 
            let res = await (await fetch('/api/pull_from_ia', {method: 'POST'})).json(); 
            if(res.status === 'success') { 
                alert('✅ ' + (res.msg || '拉取任务已提交，请前往「队列」查看进度')); 
            } else {
                alert('❌ 拉取失败: ' + (res.msg || '未知错误')); 
            }
        } catch(e) { 
            alert('网络出错'); 
        } finally { 
            btn.innerText = ot; 
            btn.disabled = false; 
        } 
    }
    let draggedType = null, draggedId = null;
    function dragStart(e, type, id) { draggedType = type; draggedId = id; e.dataTransfer.effectAllowed = 'move'; }
    function allowDrop(e) { e.preventDefault(); e.dataTransfer.dropEffect = 'move'; }
    async function dropOnFolder(e, targetFolderId) { e.preventDefault(); if (draggedType === 'folder' && draggedId !== null) { if (draggedId == targetFolderId) return; let res = await (await fetch('/api/move_folder', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({folder_id: draggedId, target_id: targetFolderId})})).json(); if (res.status === 'success') location.reload(); else alert(res.msg); } else if (draggedType === 'item' && draggedId !== null) { let res = await (await fetch('/api/move_item_ajax', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({item_id: draggedId, target_id: targetFolderId})})).json(); if (res.status === 'success') location.reload(); else alert(res.msg); } }
    
    let currentThumbIdent = '';
    function showThumbModal(ident, title) { currentThumbIdent = ident; document.getElementById('thumbTitle').innerText = '🖼️ ' + title; document.getElementById('thumbModalImg').src = '/thumb/' + encodeURIComponent(ident) + '?t=' + new Date().getTime(); document.getElementById('thumbDownloadBtn').href = '/thumb/' + encodeURIComponent(ident) + '?download=1'; document.getElementById('thumbUploadStatus').innerText = ''; document.getElementById('thumbModal').style.display = 'flex'; }
    function closeThumbModal() { document.getElementById('thumbModal').style.display = 'none'; }
    async function refreshThumb() { let s = document.getElementById('thumbUploadStatus'); s.innerText = '正在重新获取...'; let res = await (await fetch('/api/thumb/refresh/' + encodeURIComponent(currentThumbIdent), {method:'POST'})).json(); if(res.status === 'success') { document.getElementById('thumbModalImg').src = '/thumb/' + encodeURIComponent(currentThumbIdent) + '?t=' + new Date().getTime(); s.innerText = '✅ 获取成功'; } else { s.innerText = '❌ 失败: ' + (res.msg || '未知错误'); } }
    async function uploadCustomThumb() { let fileInput = document.getElementById('thumbFileInput'); if(!fileInput.files.length) return; let syncIA = confirm('是否同步更新 IA 官方缩略图？\n\n确定 = 同步官方 (较慢)\n取消 = 仅修改本地'); let fd = new FormData(); fd.append('file', fileInput.files[0]); fd.append('sync_ia', syncIA ? 'yes' : 'no'); document.getElementById('thumbUploadStatus').innerText = '正在上传...'; let res = await (await fetch('/api/thumb/custom/' + encodeURIComponent(currentThumbIdent), {method:'POST', body: fd})).json(); if(res.status === 'success') { document.getElementById('thumbModalImg').src = '/thumb/' + encodeURIComponent(currentThumbIdent) + '?t=' + new Date().getTime(); document.getElementById('thumbUploadStatus').innerText = '✅ 自定义成功'; } else { document.getElementById('thumbUploadStatus').innerText = '❌ 失败: ' + res.msg; } fileInput.value = ''; }

    // ==================== 批量追加相关 JS（接入 LocalPicker）====================
    let baItemIds = [];
    let baSelectedPaths = [];
    let baPicker = null;

    function _refreshBaFileInfoDisplay() {
        const infoDiv = document.getElementById('ba_info');
        if (!infoDiv) return;
        const bfi = document.getElementById('ba_files');
        const browserCount = (bfi && bfi.files) ? bfi.files.length : 0;
        const localCount = baSelectedPaths.length;
        if (localCount > 0 && browserCount > 0) {
            infoDiv.innerHTML = `<span style="color:var(--danger);">⚠️ 已选 ${localCount} 个本地路径 + ${browserCount} 个浏览器文件，本地路径优先（浏览器文件将被忽略）</span>`;
        } else if (localCount > 0) {
            infoDiv.innerHTML = `已选本地路径: <span style="color:var(--blue);">${localCount} 个</span>`;
        } else if (browserCount > 0) {
            let total = 0;
            for (let i = 0; i < bfi.files.length; i++) total += bfi.files[i].size;
            const names = Array.from(bfi.files).slice(0, 3).map(f => f.name).join(', ');
            const more = browserCount > 3 ? ` 等 ${browserCount} 个文件` : '';
            infoDiv.innerHTML = `已选浏览器文件: <span style="color:var(--blue);">${escapeHtml(names)}${more}</span> (${(total/1024/1024).toFixed(2)} MB)`;
        } else {
            infoDiv.innerText = '';
        }
    }

    function initBaPicker() {
        if (!window.LocalPicker) {
            console.error('[initBaPicker] LocalPicker 组件未加载，可能 LOCAL_PICKER_JS 注入失败');
            return;
        }
        baPicker = window.LocalPicker.mount({
            instanceId: 'ba',
            listContainerId: 'ba_path_list',
            initialPaths: baSelectedPaths,
            onChange: (paths) => {
                baSelectedPaths = paths;
                _refreshBaFileInfoDisplay();
            },
            browserFilesInputId: 'ba_files',
            onBrowserFilesChange: (files) => {
                _refreshBaFileInfoDisplay();
            }
        });
        window.baPicker = baPicker;
    }

    document.addEventListener('DOMContentLoaded', () => {
        initBaPicker();
    });

    function showBatchAppendModal() {
        baItemIds = Array.from(document.querySelectorAll('.batch-checkbox:checked')).map(cb => cb.value);
        if (baItemIds.length === 0) return alert('请先勾选要追加的条目');
        
        if (baPicker) {
            baPicker.clearPaths();
            baPicker.clearBrowserFiles();
        }
        document.getElementById('batchAppendModal').style.display = 'flex';
    }

    async function doBatchAppendSubmit(btn) {
        if (baItemIds.length === 0) return alert('未选择条目，请刷新页面重试');
        
        let bfi = document.getElementById('ba_files');
        let files = bfi.files ? Array.from(bfi.files) : [];
        let paths = baPicker ? baPicker.getPaths() : baSelectedPaths;
        
        if (paths.length === 0 && files.length === 0) return alert('请选择本地文件/文件夹或通过浏览器选择文件');
        
        if (paths.length > 0 && files.length > 0) {
            if (!confirm('⚠️ 检测到同时选择了本地路径和浏览器文件。\n\n后端处理时本地路径优先，浏览器文件将被忽略。\n\n确定继续吗？')) return;
        }
        
        let oldTxt = btn.innerText;
        btn.innerText = '正在处理并提交...';
        btn.disabled = true;

        let fd = new FormData();
        baItemIds.forEach(id => fd.append('item_ids', id));
        fd.append('local_paths', JSON.stringify(paths));
        if (files.length > 0) {
            for(let f of files) fd.append('files', f);
        }
        
        fd.append('unzip_zip', document.getElementById('ba_unzip_zip').checked ? 'yes' : 'no');
        fd.append('zip_upload', document.getElementById('ba_zip_upload').checked ? 'yes' : 'no');
        fd.append('zip_custom_name', document.getElementById('ba_zip_custom_name').value.trim());
        fd.append('chunk_threshold', document.getElementById('ba_chunk_threshold').value);
        fd.append('chunk_size', document.getElementById('ba_chunk_size').value);
        fd.append('overwrite_same_file', document.getElementById('ba_overwrite_same_file').checked ? 'yes' : 'no');
        fd.append('concurrency', document.getElementById('ba_concurrency').value);
        fd.append('generate_torrent', document.getElementById('ba_generate_torrent').checked ? 'yes' : 'no');
        fd.append('no_derive', document.getElementById('ba_no_derive').checked ? 'yes' : 'no');
        fd.append('keep_old', document.getElementById('ba_keep_old').checked ? 'yes' : 'no');
        fd.append('no_directory', document.getElementById('ba_no_directory').checked ? 'yes' : 'no');
        fd.append('no_index', document.getElementById('ba_no_index').checked ? 'yes' : 'no');
        fd.append('interactive_priority', document.getElementById('ba_interactive_priority').checked ? 'yes' : 'no');
        fd.append('dark_mode', document.getElementById('ba_dark_mode').checked ? 'yes' : 'no');
        fd.append('skip_meta', document.getElementById('ba_skip_meta').checked ? 'yes' : 'no');
        fd.append('checksum', document.getElementById('ba_checksum').checked ? 'yes' : 'no');
        fd.append('size_hint', document.getElementById('ba_size_hint').value.trim());
        fd.append('retries', document.getElementById('ba_retries').value);
        
        try {
            let res = await (await fetch('/api/batch_append', { method: 'POST', body: fd })).json();
            if (res.status === 'success') {
                // 【R434 修复 - 2026-11】如果后端返回 skipped 列表非空，显示更清晰的提示
                // - res.skipped 非空 → "✅ 已提交 N 个追加任务 + ⚠️ 已跳过 M 个条目..."
                // - res.skipped 为空 → 保持原 alert('✅ ' + res.msg)（msg 里有"分片合并失败"等补充信息）
                let _skipped = (res.skipped && res.skipped.length) ? res.skipped : [];
                let _count = res.count || 0;
                if (_skipped.length > 0) {
                    let _shown = _skipped.slice(0, 5).join(', ');
                    let _more = _skipped.length > 5 ? ` 等 ${_skipped.length} 个` : '';
                    alert(`✅ 已提交 ${_count} 个追加任务\n\n⚠️ 已跳过 ${_skipped.length} 个条目（已有追加任务进行中）:\n${_shown}${_more}\n\n请等待完成或去队列页删除该任务后再试`);
                } else {
                    alert('✅ ' + res.msg);
                }
                document.getElementById('batchAppendModal').style.display = 'none';
                location.href = '/queue';
            } else {
                alert('❌ 提交失败: ' + res.msg);
            }
        } catch(e) {
            alert('❌ 网络异常或文件过大导致超时。');
        } finally {
            btn.innerText = oldTxt;
            btn.disabled = false;
        }
    }
</script>
</body></html>
"""

# 【改进点 BB2】拼接时在 TOP / LOCAL_PICKER_JS / BOTTOM 之间加换行分隔
INDEX_HTML = _INDEX_HTML_TOP + "\n" + LOCAL_PICKER_JS + "\n" + _INDEX_HTML_BOTTOM