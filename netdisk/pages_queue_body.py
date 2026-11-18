# pages_queue_body.py
# 队列页 body HTML（含导航、任务列表、速度图、重试队列、日志弹窗）
# 【🔴 G-78 修复 - 2026-11】顶部 3 个按钮（全部暂停/全部继续/清理已结束任务）
#                         的 onclick 加 this，让 pages_queue_js.py 里
#                         pauseAll(btn) / resumeAll(btn) / cleanTasks(btn) 的
#                         btn.disabled = true 生效，防快速连点并发请求。
# 【🔴 G-120 修复 - 2026-11】重试队列的上传卡片按 p[2]（status）区分显示：
#                          pending → "⏳ 排队中"（橙色）
#                          error   → "上传失败"（红色）
#                          修复前 pending 状态也显示"上传失败"，用户误以为出问题。

QUEUE_BODY = r"""</head><body>
{{ navbar|safe }}

<!-- 任务列表区域 -->
<div class="card">
    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:10px; flex-wrap:wrap; gap:10px;">
        <div style="display:flex; align-items:center; gap:10px;">
            <h2 style="margin:0; border:none;">📡 实时任务队列</h2>
            <span class="conc-badge" id="conc-badge">活跃: 0/{{ conc_max }}</span>
        </div>
        <div style="display:flex; gap:8px; flex-wrap:wrap;">
            <!-- 【G-78 修复】onclick 加 this，让 disabled 生效 -->
            <button class="btn btn-sm btn-purple" onclick="pauseAll(this)" style="margin:0;">⏸️ 全部暂停</button>
            <button class="btn btn-sm btn-green" onclick="resumeAll(this)" style="margin:0;">▶️ 全部继续</button>
            <button class="btn btn-sm btn-gray" onclick="cleanTasks(this)" style="margin:0;">🗑️ 清理已结束任务</button>
        </div>
    </div>
    <div id="task-list"><p>暂无进行中的任务...</p></div>
</div>

<!-- 顶部全局速度图 -->
<div class="card" id="global-speed-card" style="margin-bottom: 15px; padding: 10px; display: none;">
    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:5px;">
        <h3 style="margin:0; font-size:15px;">📈 全局实时网速</h3>
    </div>
    <div style="height: 100px; background:rgba(0,0,0,0.05); border-radius:6px; padding:5px;">
        <canvas id="speedChart" width="800" height="100"></canvas>
    </div>
</div>

<!-- 统一的重试队列（包含上传、下载、删除失败任务） -->
<div class="card" id="retryQueueCard">
    <h2 style="display:flex; justify-content:space-between; align-items:flex-end;">
        <span>⏳ 待处理重试队列</span>
        <span style="font-size:14px; font-weight:normal; color:var(--gray);">共 <b id="retryCount">{{ items|length }}</b> 个</span>
    </h2>
    <div style="font-size:13px; color:var(--gray); margin-bottom:15px;">这里汇总了所有上传、下载、删除、追加、重命名、拉取等失败的任务。你可以一键重新提交它们。</div>
    
    <div id="startQueueBtnWrapper" style="display: {% if items %}block{% else %}none{% endif %}; margin-bottom:20px;">
        <button type="button" class="btn btn-green" style="font-size:16px; padding:12px;" onclick="startBatchQueue(this)">🚀 开始处理所有重试任务</button>
    </div>
    
    <div id="retryItemsContainer">
    <!-- 1. 上传失败/排队中的任务 (由后端 Jinja 直接渲染) -->
    {% for p in items %}
    <div class="item-row retry-item-row" id="retry-item-{{ p[0] }}" data-type="upload" data-id="{{ p[0] }}">
        <div class="item-main">
            <strong>{{ p[1] }}</strong>
            <!-- 【G-120 修复】按 p[2]（status）区分显示 -->
            {% if p[2] == 'pending' %}
            <span class="status-badge" style="background:var(--orange)">⏳ 排队中</span>
            {% else %}
            <span class="status-badge" style="background:var(--danger)">上传失败</span>
            {% endif %}
            {% if p[3] %}<div style="color:var(--danger); font-size:13px; word-break:break-all; margin-top:6px;">{{ p[3] }}</div>{% endif %}
        </div>
        <div class="item-actions">
            <button class="btn btn-sm btn-green" onclick="retrySingle({{ p[0] }}, this)">🔄 单独重试</button>
            <a href="/upload?edit_id={{ p[0] }}" class="btn btn-sm btn-orange">重编</a>
            <a href="/trash_item/{{ p[0] }}" class="btn btn-sm btn-red">删除</a>
        </div>
    </div>
    {% endfor %}
    
    <!-- 2. 下载/删除/追加失败的任务 (由 JS 动态插入到这里) -->
    </div>
</div>

<!-- 完整日志大弹窗 -->
<div class="modal-overlay" id="logModal">
    <div class="modal-content" style="max-width: 900px; height: 85vh; display: flex; flex-direction: column; padding: 15px;">
        <div class="modal-title" style="margin-bottom: 10px;">
            <span id="logModalTitle">📄 完整日志</span>
            <span class="modal-close" onclick="closeLogModal()">&times;</span>
        </div>
        <div style="font-size: 12px; color: var(--gray); flex-shrink:0;">
            提示：如果你往上翻看历史，系统会自动暂停滚动；点击下方按钮可以恢复跟随最新进度。
        </div>
        <div class="modal-full-log" id="logModalContent"></div>
        <div class="modal-footer" style="display: flex; gap: 10px; padding-top: 15px; border-top: 1px solid var(--border); margin-top: 10px; flex-wrap:wrap; flex-shrink:0;">
            <button class="btn btn-sm btn-blue" onclick="copyModalLog()" style="margin:0;">📋 复制全部日志</button>
            <button class="btn btn-sm btn-orange" id="followBtn" onclick="followBottom()" style="margin:0; display:none;">📌 跟踪到底部</button>
            <button class="btn btn-sm btn-gray" onclick="clearModalLog()" style="margin:0;">🧹 清空日志</button>
            <button class="btn btn-sm btn-gray" onclick="closeLogModal()" style="margin:0;">关闭</button>
        </div>
    </div>
</div>
"""