# pages_upload.py
# 上传页 HTML
# 【Bug 1 修复 - 第 5/5 步】前端分片命名改为 {relPath}.__ND_PART__{N}
#                        配合后端 _merge_part_files 正确按序拼接
# 【对话6】接入公共 LocalPicker 组件
# 【修复 E1】LOCAL_PICKER_JS 用 Python 字符串拼接嵌入
# 【修复 F1】onChange 更新 infoDiv
# 【修复 F2】同时选择时显示警告，提交前 confirm
# 【改进点 AX】handleAsyncUpload 三重 confirm 合并为 1 个
# 【改进点 AW】initUpPicker 末尾主动调一次 _refreshFileInfoDisplay
# 【改进点 AU】upSelectedPaths 与 picker 内部 paths 加注释说明同步关系
# 【改进点 AV】render_template_string 调用处注释说明 Jinja 特性
# 【改进点 B15】up_path_list 上方加 up_path_count_display 显示"共 N 项"
# 【修复 SC4-同源】_refreshFileInfoDisplay 里去掉 currentFileSize > 0 内层条件
# 【修复 BB1-同源】initUpPicker 加 window.LocalPicker 存在性检查，
#                  防极端场景 TypeError（LOCAL_PICKER_JS 注入失败）
# 【修复 BB1-扩展-同源】4 个按钮从 `if(window.upPicker)xxx()` 改为三元表达式
#
# 【🔴 G-37 第 1 轮修复 - 2026-11】
#   在 ID 输入区下方增加断点续传支持的静态提示
# 【🔴 G-181 修复 - 2026-11】
#   文案改为"建议使用全小写（大写 ID 会自动回退到兼容模式，仍支持分片断点续传，
#         但首次尝试可能多一次失败重试）"
# 【🔴 G-182 修复 - 2026-11】
#   补充"点（.）"（后端 _IDENTIFIER_RE 实际允许点）
#
# 【⏱️ 延迟与超时精细控制 - 2026-11 第 17/20 步】
# 前端 5 处硬编码 → Jinja 变量（由第 15 个文件 netdisk_routes_views_upload.py 传入）：
#   1. doChunkedUpload 里 `const CHUNK_SIZE = 5 * 1024 * 1024` → `{{ frontend_chunk_size_mb }} * 1024 * 1024`
#   2. doChunkedUpload 里 `for (let tryN = 0; tryN < 3; tryN++)` → `Math.max(1, {{ frontend_chunk_retries }})`（R129 兜底）
#   3. doChunkedUpload 里 `setTimeout(rs, 800)` → `setTimeout(rs, {{ frontend_chunk_retry_sleep_ms }})`
#   4. handleAsyncUpload 里 `const FRONTEND_CHUNK_THRESHOLD = 50 * 1024 * 1024` → `{{ frontend_chunk_threshold_mb }} * 1024 * 1024`
#   5. handleAsyncUpload 里 `xhr.timeout = 600000` → `xhr.timeout = {{ upload_xhr_timeout_ms }}`
#
# 【🔴 R145 + R259 修复 - 2026-11（B 级第 6 次生成）】
#   _doPrecheck 里 ID 预检超时 20000 硬编码 → {{ id_check_timeout|default(20000) }}
#   alert 文案里的"20秒"动态化 → 用 JS 计算 _id_check_timeout_sec
#   使用 |default(20000) 兜底：第 5 次已传参，且防止未来第 5 个文件误删传参时 UndefinedError
#
# 【🔴 默认值与原来完全一致】
#   - frontend_chunk_size_mb = 5 → `5 * 1024 * 1024` = 原 `5 * 1024 * 1024` ✅
#   - frontend_chunk_retries = 3 → `Math.max(1, 3)` = 3 → 原 `3` ✅
#   - frontend_chunk_retry_sleep_ms = 800 → `800` ✅
#   - frontend_chunk_threshold_mb = 50 → `50 * 1024 * 1024` = 原 `50 * 1024 * 1024` ✅
#   - upload_xhr_timeout_ms = 600000 → `600000` ✅
#   - id_check_timeout = 20000 → `20000` = 原 `20000` ✅
#
# 【中间状态兼容】
#   - 第 5 个文件已传 id_check_timeout → 本文件引用 → 不会 UndefinedError ✅
#   - 顺序正确（第 5 → 第 6）✅
#
# 【📝 保留记录 - 2026-11，低优先，以后修】
#   R145：_doPrecheck 的 20000ms 超时硬编码 → 本轮 R145 修复 ✅
#   R259：alert 文案里的"20秒"动态化 → 本轮修复 ✅

from pages_local_picker import LOCAL_PICKER_JS

_UPLOAD_HTML_TOP = r"""
<!DOCTYPE html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">{{ s|safe }}
</head><body>
{{ n|safe }}
<div class="card">
    <div style="margin-bottom:15px; display:flex; justify-content:space-between; align-items:center;">
        <button onclick="history.back()" class="btn btn-sm btn-gray" style="margin:0;">⬅️ 返回上一级</button>
    </div>
    <h2>{{ '✏️ 编辑任务' if ei else '新建归档任务' }}</h2>
    
    {% if not ei %}
    <div class="card" style="background:rgba(0,150,136,0.05); border:1px solid var(--primary); padding:10px; margin-bottom:15px;">
        <div style="font-size:13px; font-weight:bold; color:var(--primary); margin-bottom:10px;">📂 本地草稿箱 (支持保存不同游戏的配置)</div>
        <div style="display:flex; gap:8px; flex-wrap:wrap;">
            <select id="draft_select" class="move-select" style="flex:1; max-width:200px;"><option value="">-- 选择草稿 --</option></select>
            <button type="button" class="btn btn-sm btn-blue" onclick="loadSelectedDraft()" style="margin:0;">载入</button>
            <button type="button" class="btn btn-sm btn-red" onclick="deleteSelectedDraft()" style="margin:0;">删除</button>
            <button type="button" class="btn btn-sm btn-green" onclick="saveDraft()" style="margin:0;">💾 保存当前为新草稿</button>
        </div>
    </div>
    {% endif %}

    <div class="warning-box">💡 <b>本地直传模式：</b>直接从 Termux 读取手机文件，极速且完美保留多层级文件夹结构！</div>
    
    <div id="upload-progress-container" style="display:none; margin-bottom:20px; padding:15px; background:rgba(0,150,136,0.1); border-radius:8px;">
        <div style="font-weight:bold; margin-bottom:10px; color:var(--primary);">⏳ 正在交由后端处理，请勿刷新页面...</div>
        <div class="progress-container"><div class="progress-bar-fill" id="upload-bar" style="width:0%;"></div><div class="progress-text" id="upload-pct">0%</div></div>
    </div>
    
    <form id="upload-form" method="POST" onsubmit="return handleAsyncUpload(event)">
        <input type="hidden" name="folder_id" value="{{ f }}"><input type="hidden" name="skip_upload" id="skip_upload" value="no">
        <input type="hidden" name="local_paths" id="local_paths_hidden" value="[]">
        {% if ei %}<input type="hidden" name="edit_id" value="{{ ei[0] }}">{% endif %}
        
        <div class="file-input-box" id="drop_zone" style="position:relative; padding-bottom:20px;">
            <label style="font-weight:bold; font-size:16px; pointer-events:none;">1. 选择文件或文件夹 (纯本地读取)</label>
            
            <div style="margin-top:15px; margin-bottom:20px; padding:15px; background:rgba(255,152,0,0.1); border:1px solid var(--orange); border-radius:8px;">
                <div style="font-size:14px; color:var(--orange); font-weight:bold; margin-bottom:8px;">📁 选择手机内的数据（可多次浏览追加）</div>
                <!-- 【改进点 B15】显示当前已选路径数 -->
                <div id="up_path_count_display" style="font-size:11px; color:var(--gray); margin-bottom:5px;"></div>
                <div id="up_path_list" style="max-height:150px; overflow-y:auto; border:1px solid var(--border); background:#fff; border-radius:6px; padding:10px; margin-bottom:10px;">
                    <span style="color:#888; font-size:13px;">尚未选择任何文件或文件夹...</span>
                </div>
                <div style="display:flex; gap:8px; align-items:center;">
                    <!-- 【修复 BB1-扩展-同源】三元表达式，防 picker 未加载时按钮无反馈 -->
                    <button type="button" class="btn btn-orange" onclick="window.upPicker ? upPicker.open() : alert('本地选择组件加载失败，请刷新页面后重试')" style="width:auto; margin:0; padding:0 15px; height:42px; flex:1;">📂 点击浏览并添加</button>
                    <button type="button" class="btn btn-gray" onclick="window.upPicker ? upPicker.clearPaths() : alert('本地选择组件加载失败，请刷新页面后重试')" style="width:80px; margin:0; padding:0 10px; height:42px;">清空</button>
                </div>
                <span class="tip" style="margin-top:8px; font-size:11px;">支持选择整个文件夹（保留目录结构）或单独选择文件，可多次浏览追加。</span>
            </div>

            <details style="margin-top:15px; background:rgba(33,150,243,0.05); border:1px solid var(--blue); border-radius:8px; padding:10px;">
                <summary style="cursor:pointer; color:var(--blue); font-size:13px; font-weight:bold;">🌐 或者通过浏览器选择文件上传 (远程/跨设备 · 支持分片 · 支持文件夹)</summary>
                <div style="padding:10px; margin-top:10px; background:var(--card-bg); border-radius:6px;">
                    <input type="file" name="files" id="browser_files_input" multiple style="display:block; margin:0; padding:10px; background:var(--bg); border:1px dashed var(--blue); border-radius:6px;">
                    <div style="display:flex; gap:8px; margin-top:8px;">
                        <!-- 【修复 BB1-扩展-同源】三元表达式 -->
                        <button type="button" class="btn btn-sm btn-blue" onclick="window.upPicker ? upPicker.triggerDirPick() : alert('本地选择组件加载失败，请刷新页面后重试')" style="margin:0; flex:1; font-size:12px;">📁 从文件夹选择</button>
                        <button type="button" class="btn btn-sm btn-gray" onclick="window.upPicker ? upPicker.clearBrowserFiles() : alert('本地选择组件加载失败，请刷新页面后重试')" style="margin:0; flex:1; font-size:12px;">❌ 清空浏览器选择</button>
                    </div>
                    <!-- 【遗漏 2 修复】"50MB" → "{{ frontend_chunk_threshold_mb }}MB"，"5MB" → "{{ frontend_chunk_size_mb }}MB" -->
                    <span class="tip" style="margin-top:8px; font-size:11px; background:rgba(33,150,243,0.08); border-left-color:var(--blue);">💡 如果手机本地文件用上面的「本地路径」模式更快。浏览器上传用于从其他设备上传，总大小超过 {{ frontend_chunk_threshold_mb }}MB 会自动切成 {{ frontend_chunk_size_mb }}MB 分片，支持断点重试。</span>
                </div>
            </details>

            <div id="file_info_display" style="font-size:13px; color:var(--primary); font-weight:bold; margin-top:10px;">
                {% if ei and ei[13] %}
                <div style="background:#e8f5e9; border:1px solid var(--green); padding:10px; border-radius:6px; margin-bottom:10px; color:var(--green);">
                    ✅ 后台已缓存文件，无需重新选择。直接修改下方元数据点击保存即可。
                </div>
                <span style="color:var(--gray);">当前绑定: </span><span style="background:var(--primary); color:white; padding:2px 6px; border-radius:4px;">{{ ei[13] }}</span>
                <span style="color:var(--gray);">(未重新选择则沿用原文件)</span>
                {% endif %}
            </div>
        </div>
        
        <fieldset>
            <legend>2. 核心标识 (必填)</legend>
            <label>Identifier (唯一ID):</label>
            <div style="display:flex; gap:8px; align-items:center;">
                <input name="identifier" id="id_input" value="{{ ei[2] if ei else '' }}" required {% if ei %}readonly style="background:rgba(0,0,0,0.1);flex:1;"{% else %}style="flex:1;"{% endif %} onblur="precheckID()">
                {% if not ei %}<button type="button" id="check_id_btn" onclick="manualCheckID()" class="btn btn-sm btn-gray" style="width:auto; margin:0; flex-shrink:0;">🔍 检查</button>{% endif %}
            </div>
            <!-- 【G-182 修复】补充"点（.）"（后端 _IDENTIFIER_RE 实际允许点） -->
            <span class="tip">这是 IA 上该条目的唯一链接后缀。只能用英文字母、数字、下划线、连字符或点（.）。</span>
            <!-- 【G-37 第 1 轮 + G-181 修复】断点续传支持提示（与后端 _IDENTIFIER_RE 对齐） -->
            <span class="tip" style="background:rgba(0,150,136,0.1); border-left-color:var(--primary);">
                💡 断点续传支持：ID 需 5-101 字符，首字符必须为字母/数字，仅允许字母、数字、_、.、-。建议使用全小写（大写 ID 会自动回退到兼容模式，仍支持分片断点续传，但首次尝试可能多一次失败重试）。
            </span>
            <label>Title (显示标题):</label>
            <input name="title" value="{{ ei[3] if ei else '' }}" required>
            <span class="tip">展现给所有人的名字，支持中文。</span>
            
            {% if not ei %}
            <label class="adv-label" style="margin-top:15px; color:var(--danger); background:rgba(244,67,54,0.1); padding:10px; border-radius:6px; border:1px solid var(--danger);">
                <input type="checkbox" id="skip_check" value="yes"> 
                <span>强制跳过 ID 预检 (⚠️ 危险：仅在网络极度超时，且你确信该 ID 绝无重复时勾选)</span>
            </label>
            {% endif %}
        </fieldset>
        
        {% if ei %}<fieldset><legend>🗂️ 当前文件管理</legend><div id="edit-file-list"><p>可通过上方重新选文件覆盖旧文件，或直接编辑元数据。</p></div></fieldset>{% endif %}
        
        <details>
            <summary>3. 高级上传设置 (包含 ZIP 处理、生成种子、分块等)</summary>
            <div class="adv-container">
                <h3 style="font-size:14px; color:var(--primary); border:none; margin-top:0;">📦 ZIP 压缩包处理</h3>
                <label class="adv-label"><input type="checkbox" name="unzip_zip" value="yes" onchange="if(this.checked) document.getElementById('zip_upload').checked = false;"> <span>1. 自动解压 ZIP (由网盘后台在本地解开再传)</span></label>
                <span class="tip">如果你上传了一个 ZIP 包，勾选此项后，后台会先解压，再把里面的文件传上去。</span>
                <label class="adv-label"><input type="checkbox" name="zip_upload" value="yes" id="zip_upload" onchange="if(this.checked) document.querySelector('input[name=unzip_zip]').checked = false;"> <span>2. 将选中的散装文件自动打包为 ZIP 再传</span></label>
                <span class="tip">如果你选了零散文件，勾选此项会把它们先打成 ZIP，然后再上传。</span>
                <input type="text" id="zip_custom_name" name="zip_custom_name" placeholder="自定义ZIP压缩包名称" style="width:180px; padding:6px; margin-left:10px;">
                
                <h3 style="font-size:14px; color:var(--primary); border:none; margin-top:20px;">⚙️ 大文件分块与断点续传</h3>
                <label>文件分块阈值 (MB):</label>
                <input type="number" name="chunk_threshold" value="200" min="20" max="1024">
                <!-- 【遗漏 3 修复】"50MB" → "{{ frontend_chunk_threshold_mb }}MB"，"5MB" → "{{ frontend_chunk_size_mb }}MB" -->
                <span class="tip">超过此大小的文件，将自动开启 S3 分块断点续传功能（后端）。默认 200MB。注意：浏览器上传模式下，前端也会在总大小 >{{ frontend_chunk_threshold_mb }}MB 时切成 {{ frontend_chunk_size_mb }}MB 分片。</span>
                
                <label>单块大小 (MB):</label>
                <input type="number" name="chunk_size" value="10" min="5" max="50">
                <span class="tip">S3 分块上传时每块的大小。默认 10MB。如果网络极差，可以调小到 5MB。</span>

                <label class="adv-label" style="margin-top:15px; color:var(--danger);">
                    <input type="checkbox" name="overwrite_same_file" value="yes"> 
                    <span>3. 覆盖云端已有同名文件 (默认不勾选则智能跳过)</span>
                </label>
                <span class="tip">如果不勾选，后台会检查云端已有的文件。如果大小和MD5一致，将直接秒传跳过，节省流量。勾选后则无视云端文件强制重传。</span>

                <h3 style="font-size:14px; color:var(--primary); border:none; margin-top:20px;">⚙️ 官方底层并发参数</h3>
                
                <label>文件并发上传数 (1-16，越大越快):</label>
                <input type="number" name="concurrency" value="8" min="1" max="16">
                <span class="tip">现在底层使用的是官方 Python 库的真多线程。8 是官方推荐值；如果你在传包含大量小文件的文件夹，调大能有效防止代理断连且速度极快。极限为 16，不要填太大，容易触发代理风控。</span>

                <label class="adv-label" style="margin-top:15px;"><input type="checkbox" name="generate_torrent" value="yes" {% if not ei %}checked{% endif %}> <span>4. 生成衍生文件 / BT种子 (推荐)</span></label>
                <span class="tip">官方服务器会自动生成缩略图、BT种子及文本提取，让其他人下载更快。不影响你的上传速度。</span>
                <label class="adv-label"><input type="checkbox" name="no_derive" value="yes"> <span>5. 跳过生成衍生文件 (极速上传)</span></label>
                <span class="tip">如果你想尽快传完关电脑/关手机，勾选此项。官方不会去解析你的文件。与上一项互斥。</span>
                <label class="adv-label"><input type="checkbox" name="keep_old" value="yes"> <span>6. 保留旧版本文件 (防覆盖)</span></label>
                <span class="tip">如果发生同名文件冲突，保留旧文件。</span>
                <label class="adv-label"><input type="checkbox" name="no_directory" value="yes"> <span>7. 忽略文件夹结构 (平铺模式)</span></label>
                <span class="tip">如果上传一个包含多层目录的文件夹，所有文件会被「拍平」放在同一个目录，不管层级。</span>
                <label class="adv-label"><input type="checkbox" name="force_overwrite" id="force_overwrite" value="yes"> <span>8. 强制覆盖已有条目 (Ignore Bucket)</span></label>
                <span class="tip">如果提示 ID 冲突，勾选此项后会将新文件覆盖到原有条目中，不会报错。</span>
                <label class="adv-label"><input type="checkbox" name="no_index" value="yes"> <span>9. 禁止搜索引擎索引 (隐私模式)</span></label>
                <span class="tip">你的文件不会出现在 IA 的官方搜索列表中，只有知道链接的人才能访问。</span>
                <label class="adv-label"><input type="checkbox" name="interactive_priority" value="yes"> <span>10. 交互优先级 (插队处理)</span></label>
                <span class="tip">让服务器分配更多资源，优先插队处理你的上传。</span>
                <label class="adv-label"><input type="checkbox" name="dark_mode" value="yes"> <span>11. 官方页面暗黑模式</span></label>
                <span class="tip">在 IA 官方详情页查看该条目时，默认使用暗色主题。</span>
                <label class="adv-label"><input type="checkbox" name="skip_meta" value="yes"> <span>12. 跳过生成 meta.xml</span></label>
                <span class="tip">不上传额外的 _meta.xml 文件。通常不建议勾选。</span>
                <label class="adv-label"><input type="checkbox" name="checksum" value="yes"> <span>13. 上传后计算校验和</span></label>
                <span class="tip">在上传后对文件进行 MD5 校验，确保完整性。</span>
                <label>14. 访问权限级别:</label>
                <select name="access_level"><option value="public">公开 (Public) - 任何人可下载</option><option value="restricted">受限 (Restricted) - 需要特殊权限</option></select>
                <span class="tip">公开即可满足绝大多数分享需求，请勿选受限。</span>
                <label>15. 预估文件大小 (可选):</label>
                <input type="text" name="size_hint" placeholder="例如 500MB, 2GB">
                <span class="tip">帮助 IA 服务器提前为你预留存储和带宽资源。</span>
                <label>16. 自动重试次数 (最高 {{ upload_retries_max }} 次):</label>
                <input type="number" name="retries" min="0" max="{{ upload_retries_max }}" placeholder="默认 5">
                <span class="tip">遇到网络波动时自动重试上传。填太大会被官方风控封号。</span>
                <label>17. 自动重试休眠秒数 (最低 {{ upload_sleep_min }} 秒):</label>
                <input type="number" name="sleep" min="{{ upload_sleep_min }}" placeholder="默认 1">
                <span class="tip">重试前的等待时间，填太小会触发 CC 攻击拦截。</span>
                <label>18. 自定义 Header 头 (高级玩家用):</label>
                <textarea name="custom_headers" rows="2" placeholder="x-archive-meta-custom:hello"></textarea>
            </div>
        </details>
        
        <fieldset>
            <legend>4. 扩展元数据 (选填)</legend>
            <label>Creator (作者/制作组):</label><input name="creator" id="meta_creator" value="{{ ei[4] if ei else '' }}"><span class="tip">比如填"某某汉化组"、"某某开发"。多个作者可用逗号隔开。</span>
            <label>Description (详细描述):</label><textarea name="description" rows="4">{{ ei[5] if ei else '' }}</textarea><span class="tip">这里可以写游戏的玩法、背景、安装说明，支持 Markdown 格式排版。</span>
            <label>Tags (关键词标签):</label><input name="tags" value="{{ ei[11] if ei else '' }}" placeholder="用逗号分隔"><span class="tip">标签越多，别人在 IA 搜到的概率越大。例如：游戏, RPG, 汉化版, 东方Project。</span>
            <label>Date (发布日期):</label><input name="date" value="{{ ei[6] if ei else '' }}" placeholder="格式: 2026-10-28">
            <label>Custom Date (其他日期):</label><input name="custom_date" value="{{ ei[14] if ei and ei|length > 14 else '' }}" placeholder="格式: 2026-10-28"><span class="tip">用于特殊的日期，官方详情页会单独列出。</span>
            <label>Mediatype (媒体类型):</label><select name="mediatype">{% for v,n in mo %}<option value="{{v}}" {% if ei and ei[7]==v %}selected{% endif %}>{{n}}</option>{% endfor %}</select>
            <label>Collection (归档合集):</label><select name="collection">{% for v,n in co %}<option value="{{v}}" {% if ei and ei[8]==v %}selected{% endif %}>{{n}}</option>{% endfor %}</select>
            <label>Language (语言):</label><select name="language" id="meta_lang">{% for v,n in lang_options %}<option value="{{v}}" {% if ei and ei[9]==v %}selected{% endif %}>{{n}}</option>{% endfor %}</select><span class="tip">选择正确的语言，有助于对应语种的用户搜索。</span>
            <label>License (版权协议):</label><select name="license" id="meta_lic">{% for v,n in license_options %}<option value="{{v}}" {% if ei and ei[10]==v %}selected{% endif %}>{{n}}</option>{% endfor %}</select><span class="tip">如果你只是搬运/分享，建议留空；如果你有版权，可以选 CC 协议。</span>
        </fieldset>
        <button type="submit" id="submit_btn" class="btn btn-blue" style="padding:15px;">{{ '💾 保存修改' if ei else '加入上传队列' }}</button>
    </form>
</div>

"""

_UPLOAD_HTML_BOTTOM = r"""<script>
    const EDIT_ID = "{{ ei[2] if ei else '' }}"; const isEdit = {{ 'true' if ei else 'false' }}; const AUTO_CHECK = {{ 'true' if auto_check_id else 'false' }}; const DEBOUNCE_MS = {{ id_check_debounce_ms }};
    let currentFileSize = 0; let precheckTimer = null;
    
    let idVerified = false;
    let verifiedIdValue = '';
    
    // 【R145/R259 修复 - 2026-11】ID 预检超时（毫秒）和秒数（用于文案）
    // |default(20000) 兜底：第 5 个文件已传参，防止未来误删传参时 UndefinedError
    const _ID_CHECK_TIMEOUT_MS = {{ id_check_timeout|default(20000) }};
    const _ID_CHECK_TIMEOUT_SEC = Math.round(_ID_CHECK_TIMEOUT_MS / 1000);
    
    // 【改进点 AU】upSelectedPaths 是页面侧追踪数组
    //   - picker 内部维护自己的 paths
    //   - 两者通过 onChange（picker → 页面）/ setPaths（页面 → picker）/ getPaths（页面读取）同步
    let upSelectedPaths = [];
    let upPicker = null;
    let editBindingHtml = '';
    
    function getDrafts() { let str = localStorage.getItem('ia_upload_drafts_v2'); return str ? JSON.parse(str) : {}; }
    function renderDrafts() {
        let sel = document.getElementById('draft_select'); if(!sel) return;
        let drafts = getDrafts(); sel.innerHTML = '<option value="">-- 选择草稿 --</option>';
        for(let key in drafts) { let opt = document.createElement('option'); opt.value = key; opt.innerText = key; sel.appendChild(opt); }
    }
    function saveDraft() {
        let id = document.getElementById('id_input').value.trim();
        let title = document.querySelector('input[name="title"]').value.trim();
        let defaultName = id || title || ('草稿_' + new Date().toLocaleString().replace(/:/g, '-'));
        let name = prompt("请输入草稿名称 (用于区分不同游戏):", defaultName);
        if(!name) return;
        let draft = {};
        document.querySelectorAll('#upload-form input, #upload-form textarea, #upload-form select').forEach(el => {
            if (!el.name || el.type === 'file' || el.name === 'skip_upload' || el.name === 'edit_id' || el.name === 'local_paths' || el.name === 'local_path') return;
            if (el.type === 'checkbox') draft[el.name] = el.checked;
            else if (el.type === 'radio') { if (el.checked) draft[el.name] = el.value; }
            else draft[el.name] = el.value;
        });
        draft['__local_paths__'] = upSelectedPaths.slice();
        let drafts = getDrafts(); drafts[name] = draft; localStorage.setItem('ia_upload_drafts_v2', JSON.stringify(drafts));
        alert("✅ 草稿 [" + name + "] 已保存"); renderDrafts();
    }
    function loadSelectedDraft() {
        let name = document.getElementById('draft_select').value; if(!name) return alert('请选择草稿');
        let drafts = getDrafts(); let draft = drafts[name]; if(!draft) return;
        for (let key in draft) {
            if (key === '__local_paths__') {
                upSelectedPaths = Array.isArray(draft[key]) ? draft[key].slice() : [];
                if (upPicker) upPicker.setPaths(upSelectedPaths);
                const h = document.getElementById('local_paths_hidden');
                if (h) h.value = JSON.stringify(upSelectedPaths);
                continue;
            }
            let el = document.querySelector(`#upload-form [name="${key}"]`); if (!el) continue;
            if (el.type === 'checkbox') el.checked = draft[key];
            else if (el.type === 'radio') { let radios = document.querySelectorAll(`#upload-form [name="${key}"]`); radios.forEach(r => { if (r.value === draft[key]) r.checked = true; }); }
            else { el.value = draft[key]; }
        }
        idVerified = false; verifiedIdValue = '';
        alert('✅ 草稿加载成功，请重新检查 ID 是否可用。');
    }
    function deleteSelectedDraft() {
        let name = document.getElementById('draft_select').value; if(!name) return alert('请选择草稿');
        if(!confirm(`确定要删除草稿 [${name}] 吗？`)) return;
        let drafts = getDrafts(); delete drafts[name]; localStorage.setItem('ia_upload_drafts_v2', JSON.stringify(drafts)); renderDrafts();
    }
    
    document.getElementById('id_input').addEventListener('input', function() {
        if (this.value.trim() !== verifiedIdValue) idVerified = false;
    });
    
    // 【修复 F1/F2 + 改进点 B15】统一刷新 infoDiv + up_path_count_display
    function _refreshFileInfoDisplay() {
        const infoDiv = document.getElementById('file_info_display');
        // 【改进点 B15】显示已选路径数
        const countDisplay = document.getElementById('up_path_count_display');
        if (countDisplay) {
            const n = upSelectedPaths.length;
            countDisplay.innerText = n > 0 ? `已选 ${n} 个本地路径` : '';
        }
        if (!infoDiv) return;
        const bfi = document.getElementById('browser_files_input');
        const browserCount = (bfi && bfi.files) ? bfi.files.length : 0;
        const localCount = upSelectedPaths.length;
        if (localCount > 0 && browserCount > 0) {
            infoDiv.innerHTML = `<span style="color:var(--danger);">⚠️ 已选 ${localCount} 个本地路径 + ${browserCount} 个浏览器文件，本地路径优先（浏览器文件将被忽略）</span>`;
        } else if (localCount > 0) {
            infoDiv.innerHTML = `已选本地路径: <span style="color:var(--blue);">${localCount} 个</span>` + (isEdit ? ' <span style="color:var(--orange);">(提交后将覆盖原绑定)</span>' : '');
        } else if (browserCount > 0) {
            // 【修复 SC4-同源】去掉 currentFileSize > 0 内层条件，防 0 字节文件（用户选空文件时看不到已选提示）
            const names = Array.from(bfi.files).slice(0, 3).map(f => f.name).join(', ');
            const more = browserCount > 3 ? ` 等 ${browserCount} 个文件` : '';
            // 【遗漏 1 修复】50*1024*1024 → {{ frontend_chunk_threshold_mb }} * 1024 * 1024
            infoDiv.innerHTML = `已选浏览器文件: <span style="color:var(--blue);">${escapeHtml(names)}${more}</span> (${(currentFileSize/1024/1024).toFixed(2)} MB)` + (currentFileSize > {{ frontend_chunk_threshold_mb }} * 1024 * 1024 ? ' <span style="color:var(--orange);">[将自动分片上传]</span>' : '');
        } else {
            if (isEdit && editBindingHtml) {
                infoDiv.innerHTML = editBindingHtml;
            } else {
                infoDiv.innerText = '';
            }
        }
    }
    
    function initUpPicker() {
        const infoDiv = document.getElementById('file_info_display');
        editBindingHtml = infoDiv ? infoDiv.innerHTML : '';
        
        // 【修复 BB1-同源】LocalPicker 存在性检查，防极端场景 TypeError
        // （LOCAL_PICKER_JS 注入失败时，如 CSP 拦截 inline script / 浏览器扩展干扰 / 字符串拼接出错）
        if (!window.LocalPicker) {
            console.error('[initUpPicker] LocalPicker 组件未加载，可能 LOCAL_PICKER_JS 注入失败');
            return;
        }
        
        upPicker = window.LocalPicker.mount({
            instanceId: 'up',
            listContainerId: 'up_path_list',
            initialPaths: upSelectedPaths,
            onChange: (paths) => {
                upSelectedPaths = paths;
                const h = document.getElementById('local_paths_hidden');
                if (h) h.value = JSON.stringify(paths);
                _refreshFileInfoDisplay();
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
                _refreshFileInfoDisplay();
            }
        });
        window.upPicker = upPicker;
        // 【改进点 AW】主动刷新一次（防御性），让 infoDiv 和 up_path_count_display 与初态一致
        _refreshFileInfoDisplay();
    }
    
    document.addEventListener('DOMContentLoaded', () => { 
        initUpPicker();
        if(!isEdit) renderDrafts(); 
    });
    
    async function _doPrecheck() { 
        let id = document.getElementById('id_input').value.trim(); if (!id) return; 
        let btn = document.getElementById('check_id_btn');
        let oldTxt = '🔍 检查';
        if (btn) { btn.innerText = '检查中...'; btn.disabled = true; }
        
        try {
            // 【R145 修复】20000 → {{ id_check_timeout|default(20000) }}
            let controller = new AbortController(); 
            let timeoutId = setTimeout(() => controller.abort(), _ID_CHECK_TIMEOUT_MS);
            let res = await (await fetch(`/api/check_identifier?identifier=${encodeURIComponent(id)}&size=${currentFileSize}`, {signal: controller.signal})).json(); 
            clearTimeout(timeoutId);
            
            if (res.source !== 'unknown') { idVerified = true; verifiedIdValue = id; }
            
            if (res.exists) { 
                if (res.source === 'local_done') { 
                    if (confirm(`✨ ${res.msg}【${id}】！\n是否直接秒传（跳过文件上传）？`)) { document.getElementById('skip_upload').value = 'yes'; return; } 
                } else if (res.source === 'unknown') { 
                    alert(`⚠️ 无法连接 IA 核实 ID。请勾选下方的「强制跳过 ID 预检」后重试。`); 
                } else {
                    let fo = document.getElementById('force_overwrite'); 
                    if (fo && fo.checked) confirm(`⚠️ 警告：ID【${id}】已存在，勾选了强制覆盖，确认继续？`); 
                    else alert(`⚠️ 此 ID【${id}】已被占用，请更换。如需覆盖请勾选"强制覆盖"。`); 
                }
            } else {
                alert(`✅ 恭喜，ID【${id}】目前可用！`);
            }
        } catch(e) {
            // 【R259 修复】"20秒" → 动态 _ID_CHECK_TIMEOUT_SEC
            if (e.name === 'AbortError') alert(`❌ 检查超时 (${_ID_CHECK_TIMEOUT_SEC}秒无响应)，请检查代理网络。`);
            else alert("❌ 检查失败，网络异常");
        } finally {
            if (btn) { btn.innerText = oldTxt; btn.disabled = false; }
        }
    }
    
    function precheckID() { if (isEdit) return; if (!AUTO_CHECK) return; let id = document.getElementById('id_input').value.trim(); if (!id) return; clearTimeout(precheckTimer); precheckTimer = setTimeout(_doPrecheck, DEBOUNCE_MS); }
    async function manualCheckID() { if (isEdit) return; await _doPrecheck(); }

    async function doChunkedUpload(form, allFiles, submitBtn, progContainer, progBar, progPct) {
        // 【⏱️ 延迟与超时精细控制】CHUNK_SIZE = 5 * 1024 * 1024 → {{ frontend_chunk_size_mb }} * 1024 * 1024
        const CHUNK_SIZE = {{ frontend_chunk_size_mb }} * 1024 * 1024;
        let batchId = 'c' + Date.now() + '_' + Math.random().toString(36).slice(2,8);
        let totalBytes = 0;
        for (let f of allFiles) totalBytes += f.size;
        if (totalBytes <= 0) { alert('选中的文件为空'); submitBtn.disabled = false; submitBtn.innerText = '加入上传队列'; progContainer.style.display = 'none'; return false; }
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
                    submitBtn.disabled = false; submitBtn.innerText = '加入上传队列';
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
            let r = await fetch('/upload', { method: 'POST', body: fd });
            let res = await r.json();
            if (res.status === 'success') { location.href = res.redirect; return false; }
            else {
                alert(res.msg || '提交失败');
                submitBtn.disabled = false; submitBtn.innerText = '加入上传队列';
                progContainer.style.display = 'none';
                return false;
            }
        } catch(e) {
            alert('提交失败：' + e.message);
            submitBtn.disabled = false; submitBtn.innerText = '加入上传队列';
            progContainer.style.display = 'none';
            return false;
        }
    }

    async function handleAsyncUpload(e) {
        e.preventDefault();
        
        let submitBtn = document.getElementById('submit_btn');
        if (submitBtn.disabled) return false;
        
        let form = document.getElementById('upload-form');
        let skipUpload = document.getElementById('skip_upload').value === 'yes';
        let hasLocalPaths = upSelectedPaths.length > 0;
        let bfi = document.getElementById('browser_files_input');
        let browserFiles = (bfi && bfi.files) ? Array.from(bfi.files) : [];

        if (!skipUpload && !hasLocalPaths && !isEdit && browserFiles.length === 0) {
            alert('新建任务必须选择本地文件/文件夹，或者通过浏览器选择文件！');
            return false;
        }
        
        let id = document.getElementById('id_input').value.trim(); if (!id) return false;
        
        let skipCheck = document.getElementById('skip_check');
        let forceOverwrite = document.getElementById('force_overwrite');
        
        if(!isEdit) {
            if (!idVerified && !(skipCheck && skipCheck.checked) && !(forceOverwrite && forceOverwrite.checked)) {
                alert(`🚫 安全拦截：\n为了确保不会覆盖云端已有的同 ID 文件，请先点击上方的「🔍 检查」按钮确认可用。\n\n(如果你已经知晓风险，请勾选下方的「强制跳过 ID 预检」)`);
                return false;
            }
            if (idVerified && verifiedIdValue !== id && !(skipCheck && skipCheck.checked) && !(forceOverwrite && forceOverwrite.checked)) {
                alert(`🚫 安全拦截：\n你修改了 ID，但尚未进行验证，请先点击「🔍 检查」！`);
                return false;
            }
        }
        
        if (skipUpload) { 
            let fd = new FormData(form); 
            fd.set('local_paths', JSON.stringify(upSelectedPaths));
            submitBtn.disabled = true; submitBtn.innerText = '🚀 正在提交，请稍候...';
            try {
                let res = await (await fetch('/upload', { method: 'POST', body: fd })).json(); 
                if(res.status === 'success') { location.href = res.redirect; } 
                else { alert(res.msg); submitBtn.disabled = false; submitBtn.innerText = '加入上传队列'; }
            } catch(e) {
                alert('网络异常');
                submitBtn.disabled = false; submitBtn.innerText = '加入上传队列';
            }
            return false; 
        }
        
        // 【改进点 AX】三重 confirm 合并为 1 个
        let hasNewSelection = hasLocalPaths || browserFiles.length > 0;
        if (isEdit && hasNewSelection) {
            let parts = [];
            if (hasLocalPaths) parts.push(`${upSelectedPaths.length} 个本地路径`);
            if (browserFiles.length > 0) parts.push(`${browserFiles.length} 个浏览器文件`);
            let msg = `⚠️ 你选择了新的${parts.join(' + ')}，将覆盖原有文件记录，确定继续吗？`;
            if (hasLocalPaths && browserFiles.length > 0) {
                msg += '\n\n注意：后端处理时本地路径优先，浏览器文件将被忽略。';
            }
            if (!confirm(msg)) return false;
        } else if (!isEdit && hasLocalPaths && browserFiles.length > 0) {
            if (!confirm('⚠️ 检测到同时选择了本地路径和浏览器文件。\n\n后端处理时本地路径优先，浏览器文件将被忽略。\n\n确定继续吗？')) return false;
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
        const lpHidden = document.getElementById('local_paths_hidden');
        if (lpHidden) lpHidden.value = JSON.stringify(upSelectedPaths);
        fd.set('local_paths', JSON.stringify(upSelectedPaths));
        if (fd.has('local_path')) fd.delete('local_path');
        progContainer.style.display = 'block';
        submitBtn.disabled = true; 
        submitBtn.innerText = '⏳ 正在交由后端本地复制...';
        
        let xhr = new XMLHttpRequest(); xhr.open('POST', '/upload', true);
        // 【⏱️ 延迟与超时精细控制】xhr.timeout = 600000 → xhr.timeout = {{ upload_xhr_timeout_ms }}
        xhr.timeout = {{ upload_xhr_timeout_ms }};
        xhr.upload.onprogress = function(e) { if (e.lengthComputable) { let pct = Math.round((e.loaded / e.total) * 100); progBar.style.width = pct + '%'; progPct.innerText = pct + '%'; } };
        
        xhr.onload = function() { 
            if (xhr.status === 200) { 
                try { let res = JSON.parse(xhr.responseText); 
                    if(res.status === 'success') { location.href = res.redirect; } 
                    else { alert(res.msg); submitBtn.disabled = false; submitBtn.innerText = '加入上传队列'; } 
                } catch(e) { 
                    location.href = '/queue'; 
                } 
            } else { 
                alert('上传失败，请重试。'); submitBtn.disabled = false; submitBtn.innerText = '加入上传队列'; 
            } 
            progContainer.style.display = 'none'; 
        };
        xhr.onerror = function() {
            alert('提示：网络连接断开或本地路径读取失败。');
            submitBtn.disabled = false; submitBtn.innerText = '加入上传队列';
            progContainer.style.display = 'none';
        };
        xhr.ontimeout = function() {
            alert('超时，可能是文件过大正在本地复制中，请稍后前往队列查看。');
            submitBtn.disabled = false; submitBtn.innerText = '加入上传队列';
            progContainer.style.display = 'none';
        };
        xhr.send(fd); 
        return false;
    }
</script>
</body></html>
"""

# 【改进点 AV】render_template_string 调用处（在 netdisk_routes_views_upload.py）的参数
#   s / n / ei / f / mo / co / lang_options / license_options
#   / upload_retries_max / upload_sleep_min / id_check_debounce_ms / auto_check_id
#   / frontend_chunk_threshold_mb / frontend_chunk_size_mb / frontend_chunk_retries
#   / frontend_chunk_retry_sleep_ms / upload_xhr_timeout_ms / id_check_timeout
# 若某个参数漏传，Jinja 会抛 UndefinedError（"xxx is undefined"）
# 这是 Jinja 特性，无编译期检查。新增模板变量时需同步在 render_template_string 参数里加。
UPLOAD_HTML = _UPLOAD_HTML_TOP + LOCAL_PICKER_JS + _UPLOAD_HTML_BOTTOM