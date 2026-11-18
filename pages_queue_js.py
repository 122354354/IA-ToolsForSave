# pages_queue_js.py
# 队列页全部 JS 逻辑
# 【AD】主循环 upload task 按 item_db_id 去重（活跃优先 + first-seen）
# 【AE】refresh 加 _refreshInFlight 并发保护
# 【AF】subIdCache 按 taskId + filename 分配唯一自增 ID
# 【AG】deleteDownloadTask / deleteTaskCard 加 try/catch
# 【AH】卡片状态变化用 beforebegin + remove 保持位置
# 【AI】?. 改为 !el || 短路
# 【AK】startBatchQueue 改用逐个 retry_single(item_db_id)
# 【AL】task-retry-row 改为按 task_id 增量更新
# 【AM】startBatchQueue 只清空成功提交的项
# 【AN】data-filename-enc + decodeURIComponent 传 filename
# 【AP2】retrySingleFile 成功时 finally 不恢复 btn
# 【修复 AJ】refresh 尾部 .finally() 改为 .then(() => { _refreshInFlight = false; })
# 【修复 AJ2 + AK】script 开头加 _allSettled 兼容函数（Polyfill ES2015）
# 【🔴 P0-29 修复 - 2026-11】B1 双卡片 + G-33 + G-34 + PA + QA2 + QA4 + 前端 500ms 刷新
# 【G-73 记录】G-38 断点续传指示需后端配合
# 【🔴 G-81 修复 - 2026-11】getResumeHint 加 ID 过短判断
# 【🔴 Bug E 修复 - 2026-11】getResumeHint 与后端 _is_valid_identifier 完全对齐
#
# 【🔴 历史修复 - 2026-11】
#   Bug C：getSubId 原型链问题 → Object.create(null) + hasOwnProperty.call
#   Bug D：subTaskIsScrolling 持续滚动不刷新 → +2 秒兜底
#   Bug F：retrySingleFile 成功时 btn 永久 disabled → finally 不判断 success
#   Bug G：filename.length 对 undefined/null 抛 TypeError → String(filename||'')
#   Bug T：retrySingleFile/retryAllSubFiles 调 loadSubTasks 无 force → force=true
#   Bug H：4 个全局对象用 {} → Object.create(null)（防 __proto__ 污染）
#   Bug I：refresh 里 retryContainer/container/speedCard/modalContent 为 null → 加 null 检查
#   Bug J：retryAllSubFiles 检查后端返回的 status
#   Bug K：_refreshInFlight 永久 true → AbortController + 15 秒兜底解锁
#   Bug K2：旧 setTimeout 误伤新请求的 AbortController → 闭包捕获 myCtrl
#   Bug K3：兜底解锁后旧请求 finally 误设 _refreshInFlight → _refreshToken 校验
#   G-133~150：deleteTaskCard 清理 + _cleanupSubIdCache hasOwnProperty + 局部 map Object.create(null)
#              + toggleExpand btn null 检查 + 4 处 alert msg 兜底 + cleanTasks 显示 msg
#              + 4 处 btn null 检查 + res.files 兜底
#   G-186：getResumeHint 纯数字判断已确认在 Bug E 时删除（与后端 _is_valid_identifier 一致）
#
# 【🔴 G-211 + G-212 修复 - 2026-11（本轮）】
#   G-211：sizeInfo 显示层的 t.uploaded_bytes 未 min。
#     触发：netdisk_workers_upload.py 的 G-95 修复后 sz 用实时 getsize 值，
#          文件变大时 uploaded_bytes > total_size_bytes
#          → 前端显示"总计已传 20MB / 10MB"（字节数矛盾）。
#          后端 G-165 曾尝试 min，但与主循环写入交互导致跳动，已回滚。
#          正确方案：后端 uploaded_bytes 保留实际值，前端显示层 Math.min。
#     修复：sizeInfo 里 t.uploaded_bytes 加 Math.min(t.uploaded_bytes, t.total_size_bytes)。
#   G-212：主卡片进度条 width + pct 文本未 min（创建 + 更新共 4 处）。
#     触发：同上（虽然后端 pct 已 min（G-162/G-164），但为防御性 + 旧数据兼容，
#          前端再加一层 Math.min 保护）。
#     修复：4 处加 Math.min(100, t.percent || 0)。
#
# 【⏱️ 延迟与超时精细控制 - 2026-11 第 19/20 步】
# 3 处前端硬编码 → Jinja 变量（由第 16 个文件 netdisk_routes_views_manage.py 传入）：
#   1. setInterval(refresh, 500) → setInterval(refresh, QUEUE_POLL_INTERVAL)
#      其中 QUEUE_POLL_INTERVAL = {{ queue_poll_interval_ms }}（默认 500）
#   2. setTimeout(() => myCtrl.abort(), 10000) → setTimeout(..., QUEUE_REFRESH_TIMEOUT)
#      其中 QUEUE_REFRESH_TIMEOUT = {{ queue_refresh_timeout_ms }}（默认 10000）
#   3. _refreshStartTime > 15000 → > QUEUE_STUCK_TIMEOUT
#      其中 QUEUE_STUCK_TIMEOUT = Math.max({{ queue_stuck_timeout_ms }}, QUEUE_REFRESH_TIMEOUT + 5000)
#
# 【🔴 R137 兜底（本轮新增）】
#   queue_stuck_timeout_ms 应 >= queue_refresh_timeout_ms，否则"卡住兜底解锁"先于
#   "单次刷新超时"触发，导致正常刷新被误中断。
#   修复：QUEUE_STUCK_TIMEOUT = Math.max(queue_stuck_timeout_ms, QUEUE_REFRESH_TIMEOUT + 5000)
#        即使用户填 queue_stuck_timeout_ms=3000 + queue_refresh_timeout_ms=60000，
#        前端也会用 Math.max(3000, 65000) = 65000 → 兜底解锁在单次刷新超时后 5 秒才触发。
#
# 【🔴 默认值与原来完全一致】
#   - QUEUE_POLL_INTERVAL = 500 → 原 setInterval(refresh, 500) ✅
#   - QUEUE_REFRESH_TIMEOUT = 10000 → 原 setTimeout(..., 10000) ✅
#   - QUEUE_STUCK_TIMEOUT = Math.max(15000, 10000+5000) = 15000 → 原 _refreshStartTime > 15000 ✅
#
# 【中间状态兼容】
#   - 第 16 个文件已完成传参 → 本文件引用参数 → 不会 UndefinedError ✅
#   - 顺序正确（第 16 → 第 19）
#
# 【📝 保留记录 - 2026-11，低优先，以后修】
#   G-116：deleteTaskCard / deleteDownloadTask 不检查 /api/tasks/del 返回值
#   G-121：Jinja 静态渲染的 retry-item 不随 items 表变化消失
#   G-122：startBatchQueue 文案"失败任务"但含 pending
#   G-125/126/128/142/143/144/151/152/153/154/155/156/157/158/159/179：见历史
#   G-210：netdisk_workers_download.py 的 _update_parent_download_progress pct 极端场景
#   G-216：netdisk_workers_upload.py 主循环 tail 的 real_uploaded_bytes 显示可能超
#          （raw_line 纯文本，用户感知弱）
#   R156：用户填 queue_poll_interval_ms=200 时每秒 5 次 /api/tasks 请求 → 服务器压力略增
#   R157：用户填 queue_poll_interval_ms=10000 时队列刷新 10 秒一次 → UI 明显卡顿
#   R158：页面已打开后改配置需刷新页面才生效（所有前端配置的正常行为，R148 已记录）

QUEUE_JS = r"""<script>
    // 【⏱️ 延迟与超时精细控制】顶部 3 个常量（Jinja 渲染一次，后续所有引用用常量）
    const QUEUE_POLL_INTERVAL = {{ queue_poll_interval_ms }};
    const QUEUE_REFRESH_TIMEOUT = {{ queue_refresh_timeout_ms }};
    // 【R137 兜底】stuck 应 >= refresh + 5000，防用户配错导致误中断
    const QUEUE_STUCK_TIMEOUT = Math.max({{ queue_stuck_timeout_ms }}, QUEUE_REFRESH_TIMEOUT + 5000);

    // 【修复 AJ2 + AK】Promise.allSettled 兼容 Polyfill（ES2015）
    function _allSettled(promises) {
        return Promise.all(promises.map(function(p) {
            return Promise.resolve(p).then(
                function(value) { return {status: 'fulfilled', value: value}; },
                function(reason) { return {status: 'rejected', reason: reason}; }
            );
        }));
    }

    let lastTaskData = {};
    let viewingTaskId = null;
    let followMode = true;
    let isLogCleared = false;
    let expandedTasks = new Set();
    // 【Bug H 修复】4 个按 taskId 索引的全局对象用 Object.create(null) 防原型链污染
    let subTaskIsScrolling = Object.create(null);
    let subTaskRequestSeq = Object.create(null);
    const speeds = [];
    let _refreshInFlight = false;
    // 【Bug K 修复】追踪 refresh 开始时间 + AbortController
    let _refreshStartTime = 0;
    let _refreshAbortCtrl = null;
    // 【Bug K3 修复】请求 token，请求完成时验证 token 才解锁
    let _refreshToken = 0;
    let subIdCache = Object.create(null);
    let subIdCounter = 0;
    // 【Bug D 修复】记录每个 task 上次拉取子任务时间，用于 isScrolling 超时兜底
    let _subTaskLastLoadTime = Object.create(null);

    function escapeHtml(s){ if (!s) return ''; return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }

    // 【Bug C + H 修复】subIdCache 用 Object.create(null) 避免原型链污染
    // + subIdCache[taskId] 也用 Object.create(null) + hasOwnProperty.call 双保险
    function getSubId(taskId, filename) {
        if (!subIdCache[taskId]) subIdCache[taskId] = Object.create(null);
        if (!Object.prototype.hasOwnProperty.call(subIdCache[taskId], filename)) {
            subIdCache[taskId][filename] = ++subIdCounter;
        }
        return 'sub_' + taskId + '_' + subIdCache[taskId][filename];
    }
    
    function formatSize(bytes) {
        if (!bytes || bytes === 0 || bytes < 0) return '0 B';
        if (bytes < 1024) return bytes + ' B';
        if (bytes < 1048576) return (bytes/1024).toFixed(1) + ' KB';
        if (bytes < 1073741824) return (bytes/1048576).toFixed(1) + ' MB';
        return (bytes/1073741824).toFixed(2) + ' GB';
    }

    function formatSpeed(bytesPerSec) {
        if (!bytesPerSec || bytesPerSec < 0) return '';
        if (bytesPerSec < 1024) return bytesPerSec.toFixed(0) + ' B/s';
        if (bytesPerSec < 1048576) return (bytesPerSec/1024).toFixed(1) + ' KB/s';
        if (bytesPerSec < 1073741824) return (bytesPerSec/1048576).toFixed(2) + ' MB/s';
        return (bytesPerSec/1073741824).toFixed(2) + ' GB/s';
    }

    function taskActionWord(taskType) {
        switch (taskType) {
            case 'upload': return '上传';
            case 'download': return '下载';
            case 'download_folder': return '下载';
            case 'download_browser': return '浏览器下载';
            case 'delete': return '删除';
            case 'append': return '追加';
            case 'rename': return '重命名';
            case 'pull': return '拉取';
            case 'push_meta': return '更新元数据';
            default: return '处理';
        }
    }

    function taskIcon(taskType) {
        switch (taskType) {
            case 'upload': return '⬆️';
            case 'download': return '⬇️';
            case 'download_folder': return '📁';
            case 'download_browser': return '🌐';
            case 'delete': return '🗑️';
            case 'append': return '➕';
            case 'rename': return '✏️';
            case 'pull': return '☁️';
            case 'push_meta': return '📝';
            default: return '📦';
        }
    }

    function getStatusText(taskType, status) {
        if (status === 'done') return '已完成';
        if (status === 'error') return '失败';
        if (status === 'paused') return '已暂停';
        if (status === 'waiting') return '排队中';
        if (status === 'cancelled') return '已取消';
        
        if (status === 'uploading' || status === 'downloading' || status === 'deleting') {
            switch(taskType) {
                case 'upload': return '上传中';
                case 'append': return '追加中';
                case 'push_meta': return '更新中';
                case 'download':
                case 'download_folder': return '下载中';
                case 'delete': return '删除中';
                case 'rename': return '重命名中';
                case 'pull': return '拉取中';
                default: return '处理中';
            }
        }
        return status;
    }

    // 【G-73 记录】断点续传指示（需后端 api_tasks 加 identifier 字段才能完整实现）
    // 【Bug E 修复】与后端 _is_valid_identifier 完全对齐（^[a-zA-Z0-9][a-zA-Z0-9_.-]{4,100}$）
    function getResumeHint(taskType, identifier) {
        if (taskType !== 'upload' && taskType !== 'append') return null;
        if (!identifier || typeof identifier !== 'string') {
            return { level: 'unknown', text: '断点续传: 取决于 ID 合规性' };
        }
        if (identifier.length < 5) {
            return { level: 'no', text: '断点续传: 不支持（ID 过短，需≥5字符）' };
        }
        if (identifier.length > 101) {
            return { level: 'no', text: '断点续传: 不支持（ID 过长，需≤101字符）' };
        }
        if (!/^[a-zA-Z0-9]/.test(identifier)) {
            return { level: 'no', text: '断点续传: 不支持（ID 首字符必须为字母/数字）' };
        }
        if (!/^[a-zA-Z0-9_.-]+$/.test(identifier)) {
            return { level: 'no', text: '断点续传: 不支持（ID 含非法字符，仅允许字母/数字/_/./-）' };
        }
        return { level: 'yes', text: '断点续传: 支持' };
    }

    function drawSpeedChart(newSpeed) { 
        if(speeds.length > 50) speeds.shift(); 
        speeds.push(newSpeed); 
        const canvas = document.getElementById('speedChart'); 
        if(!canvas) return; 
        const ctx = canvas.getContext('2d'); 
        ctx.clearRect(0, 0, canvas.width, canvas.height); 
        ctx.beginPath(); 
        ctx.strokeStyle = '#009688'; 
        ctx.lineWidth = 2; 
        let max = Math.max(...speeds, 1); 
        for(let i=0; i<speeds.length; i++) { 
            let x = (i / 50) * canvas.width; 
            let y = canvas.height - (speeds[i] / max) * canvas.height; 
            if(i===0) ctx.moveTo(x, y); 
            else ctx.lineTo(x, y); 
        } 
        ctx.stroke(); 
    }
    
    // 【QA4】cleanTasks 成功后清空前端缓存
    async function cleanTasks(btn) {
        if(!confirm('确定清理队列中已完成和已失败的任务显示吗？')) return;
        let old = btn ? btn.innerText : '';
        if (btn) { btn.innerText = '清理中...'; btn.disabled = true; }
        try {
            let res = await (await fetch('/api/tasks/clean', {method:'POST'})).json();
            if(res.status === 'success') {
                lastTaskData = {};
                // 【Bug H 修复】同步改 Object.create(null)
                subIdCache = Object.create(null);
                subIdCounter = 0;
                expandedTasks.clear();
                _subTaskLastLoadTime = Object.create(null);
                subTaskIsScrolling = Object.create(null);
                subTaskRequestSeq = Object.create(null);
                speeds.length = 0;
                location.reload();
            } else {
                // 【G-141 修复】显示具体错误原因
                alert(res.msg || '清理失败');
            }
        } catch(e) {
            alert('网络错误：' + e.message);
        } finally {
            if (btn) { btn.innerText = old; btn.disabled = false; }
        }
    }

    async function pauseAll(btn) {
        let tasks = Object.keys(lastTaskData).filter(k => ['uploading', 'downloading', 'deleting', 'waiting'].includes(lastTaskData[k].status) && !lastTaskData[k].is_virtual);
        if (tasks.length === 0) return alert('当前没有可以暂停的任务');
        if (!confirm(`确定要暂停当前 ${tasks.length} 个进行中/等待中的任务吗？`)) return;
        
        let old = btn ? btn.innerText : '';
        if (btn) { btn.innerText = '暂停中...'; btn.disabled = true; }
        try {
            let promises = tasks.map(k => fetch('/api/tasks/toggle_pause', {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({task_id: k, action: 'pause'})
            }));
            await _allSettled(promises);
            refresh();
        } finally {
            if (btn) { btn.innerText = old; btn.disabled = false; }
        }
    }

    async function resumeAll(btn) {
        let tasks = Object.keys(lastTaskData).filter(k => lastTaskData[k].status === 'paused' && !lastTaskData[k].is_virtual);
        if (tasks.length === 0) return alert('当前没有被暂停的任务');
        if (!confirm(`确定要恢复当前 ${tasks.length} 个暂停中的任务吗？`)) return;
        
        let old = btn ? btn.innerText : '';
        if (btn) { btn.innerText = '恢复中...'; btn.disabled = true; }
        try {
            let promises = tasks.map(k => fetch('/api/tasks/toggle_pause', {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({task_id: k, action: 'resume'})
            }));
            await _allSettled(promises);
            refresh();
        } finally {
            if (btn) { btn.innerText = old; btn.disabled = false; }
        }
    }

    function updateRetryQueueUI() {
        let count = document.querySelectorAll('.retry-item-row').length;
        let countSpan = document.getElementById('retryCount');
        if (countSpan) countSpan.innerText = count;
        
        let btnWrapper = document.getElementById('startQueueBtnWrapper');
        if (btnWrapper) {
            if (count === 0) { btnWrapper.style.display = 'none'; } else { btnWrapper.style.display = 'block'; }
        }
    }

    async function startBatchQueue(btn) {
        // 【G-148 修复】btn null 检查
        if (!btn) return;
        let count = document.querySelectorAll('.retry-item-row').length;
        if (count === 0) return alert('没有待重试的任务');
        if(!confirm(`警告：即将重新处理 ${count} 个失败任务。\n请确保当前网络稳定。`)) return;
        
        let oldText = btn.innerText;
        btn.innerText = '正在提交...';
        btn.disabled = true;
        
        try {
            let successCount = 0, failCount = 0;
            
            let taskRows = document.querySelectorAll('.task-retry-row');
            let taskPromises = [];
            taskRows.forEach(el => {
                let taskId = el.dataset.taskId;
                taskPromises.push(
                    fetch('/api/tasks/retry_task', {
                        method: 'POST', headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({task_id: taskId})
                    }).then(r => r.json()).then(res => ({taskId, ok: res.status === 'success'}))
                    .catch(() => ({taskId, ok: false}))
                );
            });
            let taskResults = await _allSettled(taskPromises);
            taskResults.forEach(r => {
                if (r.status === 'fulfilled' && r.value && r.value.ok) {
                    let el = document.getElementById('task-retry-' + r.value.taskId);
                    if (el) el.remove();
                    successCount++;
                } else {
                    failCount++;
                }
            });
            
            let uploadItems = document.querySelectorAll('.retry-item-row[data-type="upload"]');
            for (let el of uploadItems) {
                let itemId = el.dataset.id;
                if (!itemId) continue;
                try {
                    let r = await (await fetch('/api/retry_single', {
                        method: 'POST', headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({id: parseInt(itemId)})
                    })).json();
                    if (r.status === 'success') {
                        el.remove();
                        successCount++;
                    } else {
                        failCount++;
                    }
                } catch(e) {
                    failCount++;
                }
            }
            
            updateRetryQueueUI();
            if (failCount > 0) {
                alert(`⚠️ ${successCount} 个已提交，${failCount} 个提交失败，请稍后重试`);
            }
            setTimeout(refresh, 1000);
        } catch (e) {
            alert('部分重试提交失败：' + e.message);
        } finally {
            btn.innerText = '🚀 开始处理所有重试任务';
            btn.disabled = false;
        }
    }

    async function retrySingle(itemId, btn) {
        // 【G-146 修复】btn null 检查
        if (!btn) return;
        if(!confirm('确定单独重试该上传任务？')) return;
        let old = btn.innerText; btn.innerText = '提交中...'; btn.disabled = true;
        try {
            let res = await (await fetch('/api/retry_single', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({id: itemId}) })).json();
            if(res.status === 'success') {
                let el = document.getElementById('retry-item-' + itemId);
                if (el) el.remove();
                updateRetryQueueUI();
                setTimeout(refresh, 500);
            } else { 
                // 【G-140a 修复】res.msg 为 undefined 时显示 "操作失败"
                alert(res.msg || '操作失败'); 
                btn.innerText = old; btn.disabled = false; 
            }
        } catch(e) { alert('网络错误'); btn.innerText = old; btn.disabled = false; }
    }

    async function retryTaskFromQueue(taskId, btn) {
        // 【G-147 修复】btn null 检查
        if (!btn) return;
        if(!confirm('确定要重试该任务吗？\n（如果是下载任务，将会自动从上次中断的地方继续）')) return;
        let old = btn.innerText;
        btn.innerText = '提交中...';
        btn.disabled = true;
        try {
            let res = await (await fetch('/api/tasks/retry_task', {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({task_id: taskId})
            })).json();
            if (res.status === 'success') {
                let el = document.getElementById('task-retry-' + taskId);
                if (el) el.remove();
                updateRetryQueueUI();
                setTimeout(refresh, 800);
            } else {
                // 【G-140b 修复】res.msg 为 undefined 时显示 "操作失败"
                alert(res.msg || '操作失败');
                btn.innerText = old;
                btn.disabled = false;
            }
        } catch(e) {
            alert('网络错误');
            btn.innerText = old;
            btn.disabled = false;
        }
    }

    async function deleteDownloadTask(taskId) {
        if(!confirm('确定要忽略并删除这个失败的任务吗？')) return;
        try {
            await fetch('/api/tasks/del', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({task_id: taskId}) });
        } catch(e) { console.warn('[deleteDownloadTask] 网络异常', e); }
        let el = document.getElementById('task-retry-' + taskId);
        if (el) el.remove();
        updateRetryQueueUI();
        setTimeout(refresh, 500);
    }

    async function deleteTaskCard(taskId) {
        let task = lastTaskData[taskId];
        if (!task) return;
        
        let isActive = ['uploading', 'downloading', 'deleting', 'waiting'].includes(task.status);
        let warnMsg = isActive ? '⚠️ 警告：该任务正在进行中！\n\n直接删除会强制中止后台传输。\n确定要强制中止并删除该任务吗？' : '确定要删除该任务卡片吗？';
        
        if (!confirm(warnMsg)) return;
        
        try {
            await fetch('/api/tasks/del', {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({task_id: taskId})
            });
        } catch(e) { console.warn('[deleteTaskCard] 网络异常', e); }
        
        let card = document.getElementById('task-card-' + taskId);
        if (card) card.remove();
        delete lastTaskData[taskId];
        expandedTasks.delete(taskId);
        if (subIdCache[taskId]) delete subIdCache[taskId];
        if (_subTaskLastLoadTime[taskId]) delete _subTaskLastLoadTime[taskId];
        // 【G-133 修复】立即清理，不依赖 _cleanupSubIdCache 兜底
        delete subTaskIsScrolling[taskId];
        delete subTaskRequestSeq[taskId];
        
        setTimeout(refresh, 300);
    }

    async function togglePause(taskId, isPaused, btn) {
        // 【G-145 修复】btn null 检查
        if (!btn) return;
        let old = btn.innerText; btn.innerText = '提交中...'; btn.disabled = true;
        try {
            let action = isPaused ? 'resume' : 'pause';
            let res = await (await fetch('/api/tasks/toggle_pause', {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({task_id: taskId, action: action})
            })).json();
            if(res.status === 'success') { refresh(); } else { 
                // 【G-140c 修复】res.msg 为 undefined 时显示 "操作失败"
                alert(res.msg || '操作失败'); 
            }
        } catch(e) { alert('网络错误'); }
        btn.innerText = old; btn.disabled = false;
    }

    function openLogModal(taskId, title) {
        viewingTaskId = taskId;
        followMode = true;
        isLogCleared = false;
        document.getElementById('logModalTitle').innerText = '📄 完整日志: ' + title;
        document.getElementById('logModal').style.display = 'flex';
        document.getElementById('followBtn').style.display = 'none';
        
        let task = lastTaskData ? lastTaskData[taskId] : null;
        document.getElementById('logModalContent').innerText = (task && task.raw_line) ? task.raw_line : '暂无日志';
    }

    function closeLogModal() { document.getElementById('logModal').style.display = 'none'; viewingTaskId = null; }

    async function clearModalLog() {
        if (!viewingTaskId) return;
        document.getElementById('logModalContent').innerText = '';
        isLogCleared = true; 
        await fetch('/api/tasks/clear_log', {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({task_id: viewingTaskId})
        });
        if (lastTaskData[viewingTaskId]) lastTaskData[viewingTaskId].raw_line = '';
    }

    function copyModalLog() {
        let text = document.getElementById('logModalContent').innerText;
        if(!text) return alert('暂无日志');
        if(navigator.clipboard) navigator.clipboard.writeText(text).then(()=>alert('✅ 日志已复制')).catch(()=>fallbackCopy(text));
        else fallbackCopy(text);
    }

    function fallbackCopy(text) {
        var ta = document.createElement("textarea");
        ta.value = text; ta.style.position = "fixed"; ta.style.opacity = "0";
        document.body.appendChild(ta); ta.select();
        try { document.execCommand('copy'); alert('✅ 日志已复制'); } catch (err) { alert('❌ 复制失败'); }
        document.body.removeChild(ta);
    }
    
    function followBottom() {
        followMode = true;
        document.getElementById('followBtn').style.display = 'none';
        let content = document.getElementById('logModalContent');
        content.scrollTop = content.scrollHeight;
    }

    document.addEventListener('DOMContentLoaded', () => {
        let content = document.getElementById('logModalContent');
        content.addEventListener('scroll', () => {
            let isAtBottom = content.scrollHeight - content.scrollTop - content.clientHeight < 50;
            if (isAtBottom) {
                followMode = true; document.getElementById('followBtn').style.display = 'none';
            } else {
                followMode = false; document.getElementById('followBtn').style.display = 'inline-block';
            }
        });
    });

    function toggleExpand(taskId) {
        let listDiv = document.getElementById('sub-list-' + taskId);
        let btn = document.getElementById('expand-btn-' + taskId);
        if (!listDiv) return;
        
        if (expandedTasks.has(taskId)) {
            expandedTasks.delete(taskId); 
            listDiv.style.display = 'none'; 
            // 【G-136 修复】btn 可能为 null
            if (btn) btn.innerText = '🔽 展开详细文件列表';
        } else {
            expandedTasks.add(taskId); 
            listDiv.style.display = 'block'; 
            if (btn) btn.innerText = '🔼 收起文件列表';
            loadSubTasks(taskId, listDiv, false);
        }
    }

    function toggleRetryExpand(item_db_id, task_id, btn) {
        let container = document.getElementById('retry-sub-list-' + item_db_id);
        if (!container) return;
        if (container.style.display === 'block') {
            container.style.display = 'none';
            if (btn) btn.innerText = '🔽 展开详细文件列表';
        } else {
            container.style.display = 'block';
            if (btn) btn.innerText = '🔼 收起文件列表';
            loadSubTasks(task_id, container, true);
        }
    }

    function getSubTaskStatusInfo(f, actionWord) {
        let icon = '⏳'; let color = 'var(--blue)'; let text = '';
        let size = parseInt(f.size) || 0;
        let done = parseInt(f.uploaded_bytes) || 0;
        
        if (f.status === 'skipped') { 
            icon = '☁️'; color = 'var(--gray)'; text = '云端已存在，跳过'; 
        } else if (f.status === 'done') { 
            icon = '✅'; color = 'var(--green)'; text = '已完成'; 
        } else if (f.status === 'error') { 
            icon = '❌'; color = 'var(--danger)'; text = (f.error_msg || '失败'); 
        } else if (f.status === 'uploading') { 
            text = `${actionWord}中`;
        } else if (f.status === 'waiting' && done > 0) { 
            icon = '⏳'; text = `准备中`;
        } else if (f.status === 'waiting') {
            text = '排队中...';
        }
        return { icon, color, text };
    }

    function buildSubTaskHtml(taskId, f, actionWord, readonly) {
        let sInfo = getSubTaskStatusInfo(f, actionWord);
        let size = parseInt(f.size) || 0;
        let done = parseInt(f.uploaded_bytes) || 0;
        
        let pct = 0;
        if (size > 0 && done > 0) pct = Math.min(100, (done / size) * 100);
        else if (f.status === 'done' || f.status === 'skipped') pct = 100;

        let progBarHtml = '';
        if (size > 0) {
            progBarHtml = `<div class="sub-progress-bar"><div class="sub-progress-fill" style="width: ${pct}%;"></div></div>`;
        }

        let encodedName = encodeURIComponent(f.filename);
        let retryBtn = '';
        if (f.status === 'error' && !readonly) {
            retryBtn = `<button class="btn btn-sm btn-orange sub-task-retry-btn" style="padding:2px 8px; font-size:11px; margin:0;" data-task-id="${escapeHtml(taskId)}" data-filename-enc="${escapeHtml(encodedName)}" onclick="retrySingleFile(this.dataset.taskId, decodeURIComponent(this.dataset.filenameEnc), this)">🔄 重试</button>`;
        }

        let sizeText = `${formatSize(done)} / ${formatSize(size)}`;
        let safeId = getSubId(taskId, f.filename);

        return `<div class="sub-task-item" id="${safeId}">
            <div class="sub-task-info">
                <div class="sub-task-filename" style="color:${sInfo.color};">
                    <span class="sub-task-icon">${escapeHtml(sInfo.icon)}</span> ${escapeHtml(f.filename)}
                </div>
                <div class="sub-task-status-row">
                    <span class="sub-task-status-text" style="color:${sInfo.color}; font-weight:bold; font-size:12px;">${escapeHtml(sInfo.text)}</span>
                    <span class="sub-task-right" style="display:flex; gap:8px; align-items:center; margin-left:auto;">
                        <span class="sub-task-size-text" style="color:var(--gray); font-size:11px; font-family:monospace; white-space:nowrap;">${sizeText}</span>
                        ${retryBtn}
                    </span>
                </div>
            </div>
            ${progBarHtml}
        </div>`;
    }

    // 【Bug D + T 修复】loadSubTasks 加 force 参数 + isScrolling 2 秒兜底
    async function loadSubTasks(taskId, targetContainer, readonly, force) {
        let listDiv = targetContainer || document.getElementById('sub-list-' + taskId);
        if (!listDiv) return;
        
        if (subTaskIsScrolling[taskId] && !force) {
            let lastLoad = _subTaskLastLoadTime[taskId] || 0;
            if (Date.now() - lastLoad < 2000) return;
            subTaskIsScrolling[taskId] = false;
        }

        if (!subTaskRequestSeq[taskId]) subTaskRequestSeq[taskId] = 0;
        let currentSeq = ++subTaskRequestSeq[taskId];

        try {
            let res = await (await fetch('/api/tasks/files/' + taskId)).json();
            if (currentSeq !== subTaskRequestSeq[taskId]) return;

            _subTaskLastLoadTime[taskId] = Date.now();

            // 【G-150 修复】res 为 null 时 res.files 抛 TypeError
            let files = (res && res.files) || [];
            
            if (files.length === 0) { 
                if (!listDiv.querySelector('.sub-task-list')) {
                    listDiv.innerHTML = '<div style="padding:10px; text-align:center; color:var(--gray);">暂无子文件记录</div>';
                }
                return; 
            }
            
            let parentTask = lastTaskData[taskId] || {};
            let taskType = parentTask.task_type || 'upload';
            let actionWord = taskActionWord(taskType);

            files.sort((a, b) => {
                const rank = { 'uploading': 1, 'waiting': 2, 'error': 3, 'done': 4, 'skipped': 5 };
                return (rank[a.status] || 99) - (rank[b.status] || 99);
            });

            let hasErr = files.some(f => f.status === 'error');

            let oldScrollTop = 0;
            let oldList = listDiv.querySelector('.sub-task-list');
            if (oldList) oldScrollTop = oldList.scrollTop;

            let html = '';
            if (hasErr && !readonly) {
                html += `<div class="sub-task-error-bar" style="display:flex; justify-content:space-between; align-items:center; padding: 8px 12px; background: rgba(244,67,54,0.1); border: 1px solid var(--danger); border-radius: 6px; margin-bottom: 10px; flex-wrap: wrap; gap: 5px;">
                    <span style="font-size:13px; color:var(--danger); font-weight:bold;">⚠️ 检测到有文件失败，可尝试一键重试</span>
                    <button class="btn btn-sm btn-orange" style="margin:0; padding: 4px 10px; font-size:12px;" onclick="retryAllSubFiles('${taskId}', this)">🔄 一键重试失败文件</button>
                </div>`;
            }
            html += '<div class="sub-task-list">';
            files.forEach(f => { html += buildSubTaskHtml(taskId, f, actionWord, readonly); });
            html += '</div>';
            listDiv.innerHTML = html;

            let newList = listDiv.querySelector('.sub-task-list');
            if (newList && oldScrollTop > 0) {
                newList.scrollTop = oldScrollTop;
            }

            if (newList && !newList._scrollBound) {
                newList._scrollBound = true;
                newList.addEventListener('scroll', () => {
                    subTaskIsScrolling[taskId] = true;
                    clearTimeout(newList._scrollTimer);
                    newList._scrollTimer = setTimeout(() => { subTaskIsScrolling[taskId] = false; }, 300);
                }, { passive: true });
            }

        } catch(e) { 
            if (listDiv.innerHTML.includes('spinner')) listDiv.innerHTML = '<div style="color:red; padding:10px;">读取子任务出错</div>'; 
        }
    }

    // 【Bug F + G + T 修复】
    async function retrySingleFile(taskId, filename, btn) {
        if (btn && btn.disabled) return;
        let displayName = String(filename || '');
        if (displayName.length > 100) displayName = displayName.slice(0, 100) + '...';
        if(!confirm('确定要单独重试该文件吗？\n' + displayName)) return;
        let oldText = btn ? btn.innerText : '';
        if (btn) { btn.innerText = '提交中...'; btn.disabled = true; }
        try {
            let res = await (await fetch('/api/retry_file', {
                method: 'POST', headers: {'Content-Type': 'application/json'}, 
                body: JSON.stringify({task_id: taskId, filename: filename})
            })).json();
            if(res.status === 'success') { 
                alert('✅ 已提交单文件重试，进度即将恢复...'); 
                loadSubTasks(taskId, null, false, true);
            } else { 
                // 【G-140d 修复】res.msg 为 undefined 时显示 "未知错误"
                alert('失败: ' + (res.msg || '未知错误')); 
            }
        } catch(e) { 
            alert('网络错误'); 
        } finally {
            if (btn) { btn.innerText = oldText; btn.disabled = false; }
        }
    }

    // 【Bug J 修复】retryAllSubFiles 检查后端返回的 status
    async function retryAllSubFiles(taskId, btn) {
        if (btn && btn.disabled) return;
        let old = btn ? btn.innerText : '';
        if (btn) { btn.innerText = '查询中...'; btn.disabled = true; }
        
        let res;
        try {
            res = await (await fetch('/api/tasks/files/' + taskId)).json();
        } catch(e) {
            alert('网络错误，请稍后重试');
            if (btn) { btn.innerText = old; btn.disabled = false; }
            return;
        }
        // 【G-149 修复】res 为 null 时 res.files 抛 TypeError
        let errFiles = ((res && res.files) || []).filter(f => f.status === 'error');
        if (errFiles.length === 0) {
            alert('没有失败的文件');
            if (btn) { btn.innerText = old; btn.disabled = false; }
            return;
        }
        if (!confirm(`确定要重试这 ${errFiles.length} 个失败的文件吗？`)) {
            if (btn) { btn.innerText = old; btn.disabled = false; }
            return;
        }
        
        if (btn) btn.innerText = '提交中...';

        try {
            let succeeded = 0, failed = 0;
            let firstErrMsg = '';
            for (let f of errFiles) {
                try {
                    // 【Bug J 修复】解析后端返回，只有 status === 'success' 才计成功
                    let r = await (await fetch('/api/retry_file', {
                        method: 'POST', headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({task_id: taskId, filename: f.filename})
                    })).json();
                    if (r && r.status === 'success') {
                        succeeded++;
                    } else {
                        failed++;
                        if (!firstErrMsg && r && r.msg) firstErrMsg = r.msg;
                    }
                } catch(inner_e) {
                    failed++;
                    if (!firstErrMsg && inner_e && inner_e.message) firstErrMsg = inner_e.message;
                }
                await new Promise(r => setTimeout(r, 200)); 
            }
            if (failed > 0) {
                let extraMsg = firstErrMsg ? `\n首个错误: ${firstErrMsg}` : '';
                alert(`⚠️ 已提交 ${succeeded} 个，${failed} 个提交失败，请稍后重试${extraMsg}`);
            } else {
                alert('✅ 已全部提交重试，进度即将恢复...');
            }
            loadSubTasks(taskId, null, false, true);
        } catch(e) {
            alert('提交异常：' + (e && e.message ? e.message : '网络异常'));
        } finally {
            if (btn) { btn.innerText = old; btn.disabled = false; }
        }
    }

    // 【QA2】清理 subIdCache 中不在 lastTaskData 的 key
    // 【G-134 修复】改用 Object.prototype.hasOwnProperty.call
    function _cleanupSubIdCache() {
        for (let tid of Object.keys(subIdCache)) {
            if (!Object.prototype.hasOwnProperty.call(lastTaskData, tid)) {
                delete subIdCache[tid];
            }
        }
        for (let tid of Array.from(expandedTasks)) {
            if (!Object.prototype.hasOwnProperty.call(lastTaskData, tid)) {
                expandedTasks.delete(tid);
            }
        }
        for (let tid of Object.keys(subTaskRequestSeq)) {
            if (!Object.prototype.hasOwnProperty.call(lastTaskData, tid)) {
                delete subTaskRequestSeq[tid];
            }
        }
        for (let tid of Object.keys(subTaskIsScrolling)) {
            if (!Object.prototype.hasOwnProperty.call(lastTaskData, tid)) {
                delete subTaskIsScrolling[tid];
            }
        }
        for (let tid of Object.keys(_subTaskLastLoadTime)) {
            if (!Object.prototype.hasOwnProperty.call(lastTaskData, tid)) {
                delete _subTaskLastLoadTime[tid];
            }
        }
    }

    function refresh(){
        // 【Bug K 修复】_refreshInFlight 永久 true 兜底解锁（QUEUE_STUCK_TIMEOUT）
        if (_refreshInFlight) {
            if (_refreshStartTime > 0 && Date.now() - _refreshStartTime > QUEUE_STUCK_TIMEOUT) {
                _refreshInFlight = false;
                if (_refreshAbortCtrl) {
                    try { _refreshAbortCtrl.abort(); } catch(e) {}
                    _refreshAbortCtrl = null;
                }
            } else {
                return;
            }
        }
        _refreshInFlight = true;
        _refreshStartTime = Date.now();
        // 【Bug K3 修复】生成新 token
        const myToken = ++_refreshToken;
        
        // 【Bug K 修复】AbortController（超时 QUEUE_REFRESH_TIMEOUT，旧浏览器不支持时安全降级）
        let fetchOpts = {};
        try {
            if (typeof AbortController !== 'undefined') {
                // 【Bug K2 修复】用闭包捕获本次请求的 myCtrl
                const myCtrl = new AbortController();
                _refreshAbortCtrl = myCtrl;
                fetchOpts.signal = myCtrl.signal;
                setTimeout(() => {
                    try { myCtrl.abort(); } catch(e) {}
                }, QUEUE_REFRESH_TIMEOUT);
            }
        } catch(e) {
            _refreshAbortCtrl = null;
            fetchOpts = {};
        }
        
        fetch('/api/tasks', fetchOpts).then(r=>r.json()).then(d=>{
            lastTaskData = d;
            let failHtml = ''; 
            let activeTasks = [];
            let addedUploadItemIds = new Set();
            let newTaskRetryIds = new Set();

            let uploadItemActiveMap = Object.create(null);
            for (let k in d) {
                let t = d[k];
                if (t.is_virtual) continue;
                if (t.task_type === 'upload' && t.item_db_id && ['done', 'waiting', 'uploading', 'paused'].includes(t.status)) {
                    uploadItemActiveMap[t.item_db_id] = true;
                }
            }

            let uploadLatestMap = Object.create(null);
            for (let k in d) {
                let t = d[k];
                if (t.is_virtual) continue;
                if (t.task_type === 'upload' && t.item_db_id) {
                    let isActive = ['waiting', 'uploading', 'paused', 'done'].includes(t.status);
                    let prev = uploadLatestMap[t.item_db_id];
                    if (!prev) {
                        uploadLatestMap[t.item_db_id] = {k, isActive};
                    } else {
                        if (isActive && !prev.isActive) {
                            uploadLatestMap[t.item_db_id] = {k, isActive};
                        }
                    }
                }
            }

            for(let k in d){
                let t=d[k]; 
                
                if (t.is_virtual) continue;
                
                if (t.task_type === 'upload' && t.item_db_id) {
                    if (uploadItemActiveMap[t.item_db_id]) {
                        let el = document.getElementById('retry-item-' + t.item_db_id);
                        if (el) { el.remove(); }
                    } else if (t.status === 'error' && !addedUploadItemIds.has(t.item_db_id)) {
                        addedUploadItemIds.add(t.item_db_id);
                        let existEl = document.getElementById('retry-item-' + t.item_db_id);
                        if (!existEl) {
                            let resumeHint = getResumeHint(t.task_type, t.identifier);
                            let resumeBadgeHtml = '';
                            if (resumeHint) {
                                let badgeColor = resumeHint.level === 'yes' ? 'var(--green)' : (resumeHint.level === 'no' ? 'var(--danger)' : 'var(--gray)');
                                resumeBadgeHtml = `<span class="status-badge" style="background:${badgeColor}; font-size:11px;">${escapeHtml(resumeHint.text)}</span>`;
                            }
                            failHtml += `<div class="item-row retry-item-row" id="retry-item-${t.item_db_id}" data-type="upload" data-id="${t.item_db_id}" style="flex-direction:column; align-items:stretch;">
                                <div class="item-main" style="width:100%;">
                                    <strong>${escapeHtml(t.title || '')}</strong><span class="status-badge" style="background:var(--danger)">上传失败</span>${resumeBadgeHtml}
                                    ${t.msg ? `<div style="color:var(--danger); font-size:13px; word-break:break-all; margin-top:6px;">${escapeHtml(t.msg)}</div>` : ''}
                                </div>
                                <div class="item-actions" style="width:100%;">
                                    <button class="btn btn-sm btn-gray" onclick="toggleRetryExpand(${t.item_db_id}, '${k}', this)">🔽 展开详细文件列表</button>
                                    <button class="btn btn-sm btn-green" onclick="retrySingle(${t.item_db_id}, this)">🔄 单独重试</button>
                                    <a href="/upload?edit_id=${t.item_db_id}" class="btn btn-sm btn-orange">重编</a>
                                    <a href="/trash_item/${t.item_db_id}" class="btn btn-sm btn-red">删除</a>
                                </div>
                                <div id="retry-sub-list-${t.item_db_id}" style="display:none; width:100%; margin-top:8px;"></div>
                            </div>`;
                        }
                    }
                    
                    let latestInfo = uploadLatestMap[t.item_db_id];
                    if (latestInfo && latestInfo.k !== k) {
                        let oldCard = document.getElementById('task-card-' + k);
                        if (oldCard) oldCard.remove();
                        continue;
                    }
                    
                    if (t.status === 'error') {
                        let oldCard = document.getElementById('task-card-' + k);
                        if (oldCard) oldCard.remove();
                        continue;
                    }
                }
                
                if (t.status === 'error' && ['download', 'download_folder', 'delete', 'append', 'rename', 'pull', 'push_meta'].includes(t.task_type)) {
                    let icon = taskIcon(t.task_type);
                    let taskRetryId = 'task-retry-' + k;
                    let existRetryEl = document.getElementById(taskRetryId);
                    newTaskRetryIds.add(k);
                    if (!existRetryEl) {
                        failHtml += `<div class="item-row retry-item-row task-retry-row" id="${taskRetryId}" data-type="task" data-task-id="${k}">
                            <div class="item-main">
                                <strong>${icon} ${escapeHtml(t.title)}</strong><span class="status-badge" style="background:var(--danger)">失败</span>
                                <div style="color:var(--danger); font-size:13px; word-break:break-all; margin-top:6px;">${escapeHtml(t.msg)}</div>
                            </div>
                            <div class="item-actions">
                                <button class="btn btn-sm btn-green" onclick="retryTaskFromQueue('${k}', this)">🔄 重试</button>
                                <button class="btn btn-sm btn-gray" onclick="deleteDownloadTask('${k}')">🗑️ 忽略</button>
                            </div>
                        </div>`;
                    }
                    continue;
                }
                
                activeTasks.push({k, t});
            }

            let retryContainer = document.getElementById('retryItemsContainer');
            if (retryContainer) {
                retryContainer.querySelectorAll('.task-retry-row').forEach(el => {
                    let taskId = el.dataset.taskId;
                    if (taskId && !newTaskRetryIds.has(taskId)) el.remove();
                });
                if (failHtml) retryContainer.insertAdjacentHTML('beforeend', failHtml);
            }
            updateRetryQueueUI();

            let container = document.getElementById('task-list');
            if (container) {
                if (activeTasks.length === 0) {
                    if (!container.innerHTML.includes('暂无进行中的任务')) { container.innerHTML = '<p>暂无进行中的任务...</p>'; }
                } else {
                    if (container.innerHTML.includes('暂无进行中的任务')) container.innerHTML = '';
                    
                    activeTasks.forEach(({k, t}) => {
                        let card = document.getElementById('task-card-' + k);
                        let statusText = getStatusText(t.task_type, t.status);
                        let statusEl = document.getElementById('status-' + k);
                        let isStatusChanged = !statusEl || statusEl.innerText !== statusText;

                        if (!card || isStatusChanged) {
                            let color = t.status === 'error' ? 'var(--danger)'
                                      : t.status === 'done' ? 'var(--green)'
                                      : t.status === 'waiting' ? 'var(--gray)'
                                      : t.status === 'paused' ? 'var(--orange)'
                                      : t.status === 'deleting' ? '#c62828'
                                      : t.status === 'downloading' ? 'var(--blue)'
                                      : 'var(--blue)';
                            let titleIcon = taskIcon(t.task_type);
                            let isPaused = t.status === 'paused';
                            
                            let canPause = ['upload','download','download_folder','delete','append','rename','pull','push_meta'].includes(t.task_type) && (t.status === 'uploading' || t.status === 'waiting' || t.status === 'paused' || t.status === 'downloading' || t.status === 'deleting');
                            
                            let expandBtnHtml = `<button class="btn btn-sm btn-blue" id="expand-btn-${k}" onclick="toggleExpand('${k}')">🔽 展开详细文件列表</button>`;

                            let canRetry = t.status === 'error' && ['download', 'download_folder', 'delete', 'append', 'rename', 'pull', 'push_meta'].includes(t.task_type);
                            let retryBtnHtml = '';
                            if (canRetry) {
                                retryBtnHtml = `<button class="btn btn-sm btn-green" onclick="retryTaskFromQueue('${k}', this)">🔄 重试</button>`;
                            } else if (t.task_type === 'upload' && t.status === 'error' && t.item_db_id && !uploadItemActiveMap[t.item_db_id]) {
                                retryBtnHtml = `<button class="btn btn-sm btn-green" onclick="retrySingle(${t.item_db_id}, this)">🔄 重试</button>`;
                            }

                            let resumeHint = getResumeHint(t.task_type, t.identifier);
                            let resumeBadgeHtml = '';
                            if (resumeHint) {
                                let badgeColor = resumeHint.level === 'yes' ? 'var(--green)' : (resumeHint.level === 'no' ? 'var(--danger)' : 'var(--gray)');
                                resumeBadgeHtml = `<span class="status-badge" style="background:${badgeColor}; font-size:11px; margin-left:5px;">${escapeHtml(resumeHint.text)}</span>`;
                            }

                            // 【G-212 修复 - 2026-11】主卡片创建时进度条 width + pct 文本加 Math.min(100, ...) 兜底
                            let cardHtml = `<div class="item-row" id="task-card-${k}" style="border-left:5px solid ${color}; flex-direction:column; align-items:stretch;">
                                <div>
                                    <strong>${titleIcon} ${escapeHtml(t.title)}</strong>
                                    <span class="status-badge" id="status-${k}" style="background:${color}">${statusText}</span>${resumeBadgeHtml}
                                    <p id="msg-${k}" style="color:${color}; font-size:14px; word-break:break-all; margin-top:5px;">${escapeHtml(t.msg || '')}</p>
                                    ${t.batch_total > 1 ? `<div style="font-size:12px; color:var(--blue); margin-top:5px;">本次进度: ${t.batch_done} / ${t.batch_total}</div>` : ''}
                                </div>
                                <div class="progress-container"><div class="progress-bar-fill" id="bar-${k}" style="width: ${Math.min(100, t.percent || 0)}%; background: ${isPaused ? 'orange' : (t.status === 'deleting' ? '#c62828' : 'linear-gradient(90deg, #009688 0%, #4CAF50 100%)')};"></div><div class="progress-text" id="pct-${k}">${Math.min(100, t.percent || 0)}%</div></div>
                                <div class="speed-text" style="line-height:1.8; margin-top:5px; text-align:left; display:flex; flex-wrap:wrap; gap:15px;">
                                    <span id="size-info-${k}" style="color:var(--primary-dark); font-size:13px; font-weight:bold;"></span>
                                    <span id="speed-${k}" style="color:var(--gray); font-size:13px;"></span>
                                </div>
                                <div class="raw-line" id="mini-log-${k}">正在获取系统日志...</div>
                                <div style="margin-top:10px; display:flex; gap:10px; flex-wrap:wrap;">
                                    ${expandBtnHtml}
                                    <button class="btn btn-sm btn-orange" data-task-id="${k}" data-title="${escapeHtml(t.title)}" onclick="openLogModal(this.dataset.taskId, this.dataset.title)">📄 查看完整日志</button>
                                    ${retryBtnHtml}
                                    ${canPause ? `<button class="btn btn-sm ${isPaused ? 'btn-green' : 'btn-purple'}" onclick="togglePause('${k}', ${isPaused}, this)">${isPaused ? '▶️ 继续' : '⏸️ 暂停'}</button>` : ''}
                                    <button class="btn btn-sm btn-red" onclick="deleteTaskCard('${k}')">🗑️ 删除</button>
                                </div>
                                <div id="sub-list-${k}" style="display:none; margin-top:5px;"></div>
                            </div>`;
                            
                            if (card) {
                                let wasExpanded = expandedTasks.has(k);
                                card.insertAdjacentHTML('beforebegin', cardHtml);
                                card.remove();
                                if (wasExpanded) {
                                    let sl = document.getElementById('sub-list-' + k);
                                    if (sl) sl.style.display = 'block';
                                    let eb = document.getElementById('expand-btn-' + k);
                                    if (eb) eb.innerText = '🔼 收起文件列表';
                                    loadSubTasks(k, sl, false);
                                }
                            } else {
                                container.insertAdjacentHTML('beforeend', cardHtml);
                                if (expandedTasks.has(k)) {
                                    let sl = document.getElementById('sub-list-' + k);
                                    if (sl) sl.style.display = 'block';
                                    let eb = document.getElementById('expand-btn-' + k);
                                    if (eb) eb.innerText = '🔼 收起文件列表';
                                    loadSubTasks(k, sl, false);
                                }
                            }
                        } else {
                            // 【G-212 修复 - 2026-11】更新时进度条 width + pct 文本加 Math.min(100, ...) 兜底
                            let bar = document.getElementById('bar-' + k);
                            if (bar) bar.style.width = Math.min(100, t.percent || 0) + '%';
                            
                            let pctText = document.getElementById('pct-' + k);
                            if (pctText) pctText.innerText = `${Math.min(100, t.percent || 0)}%` + ((t.current && t.total) ? ` (${t.current}/${t.total})` : '');
                            
                            let msgEl = document.getElementById('msg-' + k);
                            if (msgEl) msgEl.innerText = t.msg || '';

                            let sizeInfo = document.getElementById('size-info-' + k);
                            if (sizeInfo) {
                                let parts = [];
                                let word = '已处理';
                                if (t.task_type === 'upload' || t.task_type === 'append') word = '已传';
                                else if (t.task_type === 'delete') word = '已删';
                                else if (t.task_type === 'download' || t.task_type === 'download_folder') word = '已下';
                                else if (t.task_type === 'rename') word = '已改';
                                
                                // 【G-211 修复 - 2026-11】sizeInfo 显示层 uploaded_bytes 加 Math.min
                                //   防 G-95 修复后文件变大时显示"总计已传 20MB / 10MB"（字节数矛盾）。
                                if (t.total_size_bytes > 0) {
                                    let display_uploaded = Math.min(t.uploaded_bytes || 0, t.total_size_bytes);
                                    parts.push(`总计${word}: ${formatSize(display_uploaded)} / ${formatSize(t.total_size_bytes)}`);
                                }
                                if (t.total_files_count > 0) parts.push(`${word}: ${t.files_uploaded || 0} 个 / ${t.total_files_count} 个`);
                                sizeInfo.innerHTML = parts.join(' | ');
                            }
                            
                            let speedEl = document.getElementById('speed-' + k);
                            if (speedEl) {
                                speedEl.innerText = (t.speed !== '0.0' && t.speed !== '0') ? `🚀 ${t.speed}` : '';
                            }
                            
                            let miniLogEl = document.getElementById('mini-log-' + k);
                            if (miniLogEl && t.raw_line) {
                                let lines = t.raw_line.split('\n').filter(x=>x.trim()!=='');
                                let smallLines = lines.slice(-8).join('\n');
                                if (miniLogEl.textContent !== smallLines) {
                                    miniLogEl.textContent = smallLines;
                                    miniLogEl.scrollTop = miniLogEl.scrollHeight;
                                }
                            }
                            
                            if (expandedTasks.has(k)) { 
                                let sl = document.getElementById('sub-list-' + k);
                                loadSubTasks(k, sl, false); 
                            }
                        }
                    });
                }
            }

            let hasActiveTransfer = activeTasks.some(t => t.t.status === 'uploading' || t.t.status === 'downloading');
            let totalSpeed = activeTasks
                .filter(t => t.t.status === 'uploading' || t.t.status === 'downloading')
                .reduce((sum, t) => sum + (parseFloat(t.t.speed) || 0), 0);
            let speedCard = document.getElementById('global-speed-card');
            if (speedCard) {
                if(hasActiveTransfer) { speedCard.style.display = 'block'; drawSpeedChart(totalSpeed); } 
                else { speedCard.style.display = 'none'; drawSpeedChart(0); }
            }

            if (viewingTaskId && d[viewingTaskId] && !isLogCleared) {
                let fullLog = d[viewingTaskId].raw_line || '暂无日志';
                let modalContent = document.getElementById('logModalContent');
                if (modalContent && modalContent.innerText !== fullLog) {
                    modalContent.innerText = fullLog;
                    if (followMode) modalContent.scrollTop = modalContent.scrollHeight;
                }
            }

            _cleanupSubIdCache();

            fetch('/api/conc_status').then(r=>r.json()).then(s=>{ 
                let b = document.getElementById('conc-badge'); 
                if(b) b.innerText = `活跃: ${s.current}/${s.max}`; 
            }).catch(() => {
                let b = document.getElementById('conc-badge'); 
                if(b) b.innerText = `活跃: ?/?`;
            });
        }).catch(() => {}).then(() => {
            // 【Bug K3 修复】只有当前请求的 token 才解锁
            if (_refreshToken === myToken) {
                _refreshInFlight = false;
                _refreshAbortCtrl = null;
            }
        });
    }
    
    // 【⏱️ 延迟与超时精细控制】setInterval(refresh, 500) → setInterval(refresh, QUEUE_POLL_INTERVAL)
    setInterval(refresh, QUEUE_POLL_INTERVAL); 
    refresh();
</script>
</body></html>
"""