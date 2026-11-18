# pages_settings_body.py
# 设置页主体 HTML（含权限管理、自动化开关、缓存策略、下载设置、底层性能、本地密码、临时清理、备份、系统日志区）
# 【改进点 76】新增 raw_line_max_lines 设置项（默认 500，范围 10-5000）
#
# 【⏱️ 延迟与超时精细控制 - 2026-11】
# 「⏱️ 延迟与超时精细控制」大 details 含 7 大板块，共 56 项（49 INT + 7 FLOAT）。
#
# 【🔴 R273 修复 - 2026-11（本轮）】
#   warp_reg_request_timeout tip 更新：
#     原：「默认 10。... W 协议原硬编码为 15 秒，现统一为 10 秒（如需原 15 秒可调大）。」
#     新：「默认 15。... W 协议原 15 秒，临时节点原 10 秒，统一 15 秒（临时节点多等 5 秒但成功概率更高）。」
#   原因：默认值已从 10 改为 15（第 1/8 次生成），tip 需同步更新。
#
# 【🔴 R279 新增 - 2026-11（本轮）】
#   板块3 新增 id_check_ip_window（默认 60，10-3600 秒），放在 id_check_timeout 后：
#   → 替换 api_check_identifier 的 IP_ID_CHECK 60 秒滑动窗口硬编码（第 3/8 次生成接入）
#
# 【📝 保留记录 - 2026-11，低优先，以后修】
#   R22：7 大板块 details 默认折叠，移动端用户可能找不到。

SETTINGS_BODY = r"""<!DOCTYPE html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">{{ style|safe }}</head><body>
{{ navbar|safe }}
{% if request.args.get('saved') %}<div class="card" style="max-width:900px; background:#e8f5e9; color:#2e7d32; font-weight:bold; text-align:center;">✅ 设置已保存成功</div>{% endif %}
<div class="card">
    <h2>⚙️ 系统设置</h2>
    
    <!-- 【新增】权限管理 -->
    <fieldset style="border: 2px solid var(--danger); background: rgba(244, 67, 54, 0.05); margin-bottom: 20px;">
        <legend style="color: var(--danger); font-size: 16px;">🛡️ 系统权限管理 (存储与防休眠)</legend>
        <p style="font-size:13px; color:var(--text); margin-bottom:15px; line-height:1.6;">
            在手机上使用本地文件夹、导入备份时，<b>必须要有存储权限</b>；<br>
            长时间锁屏传文件时，<b>必须要有防休眠锁</b>，否则会被系统杀掉后台。<br>
            如果点击按钮没有弹出系统窗口，请前往手机设置手动授权：
            <br><code style="background:rgba(0,0,0,0.1); padding:4px 8px; display:inline-block; margin-top:5px;">设置 -> 应用 -> Termux -> 权限 -> 文件和媒体 -> 允许</code>
        </p>
        <div style="display:flex; gap:10px; flex-wrap:wrap;">
            <button type="button" onclick="requestPerm('storage', this)" class="btn btn-danger" style="flex:1; padding:12px;">📂 申请存储权限</button>
            <button type="button" onclick="requestPerm('wake_lock', this)" class="btn btn-purple" style="flex:1; padding:12px;">🔒 获取防休眠锁</button>
        </div>
        <div style="display:flex; gap:10px; flex-wrap:wrap; margin-top:10px;">
            <button type="button" onclick="installDeps(this)" class="btn btn-green" style="width:100%; padding:12px;">🔧 一键安装依赖 + 自动下载代理核心</button>
        </div>
        <div id="statusBox" style="font-size:13px; text-align:center; margin-top:10px; font-weight:bold;"></div>
    </fieldset>

    <form method="POST" novalidate>
        <input type="hidden" name="action" value="save">
        
        <!-- ==================== 自动化与安全 ==================== -->
        <fieldset style="border: 2px solid var(--green); background: rgba(76, 175, 80, 0.05);">
            <legend style="color: var(--green);">🛡️ 核心通信与底层自保 (推荐保持开启)</legend>
            <span class="tip" style="border-left-color: var(--green); background: rgba(76, 175, 80, 0.1);">这部分决定了网盘的基础稳定性和安全性，建议不要关闭。</span>
            
            <div class="auto-switch-row">
                <label class="auto-label">🌐 全局网络代理 (所有请求走代理)</label>
                <span style="font-size:12px; color:var(--gray);">开启后，上传、下载、解析、预览等所有请求都会走你设置的代理。如果你手机开了 LxBox 全局，请关闭此项以免双重代理卡死。</span>
                <div class="switch-container"><label class="switch"><input type="checkbox" name="download_via_proxy" value="yes" {% if cfg.download_via_proxy %}checked{% endif %}><span class="slider"></span></label></div>
            </div>

            <div class="auto-switch-row">
                <label class="auto-label">🔒 强制验证 SSL 证书 (防中间人劫持)</label>
                <span style="font-size:12px; color:var(--gray);">强烈建议开启，防止代理节点窃取密码或文件。如遇极差节点无法使用，可临时关闭。</span>
                <div class="switch-container"><label class="switch"><input type="checkbox" name="verify_ssl" value="yes" {% if cfg.verify_ssl %}checked{% endif %}><span class="slider"></span></label></div>
            </div>
            
            <div style="margin-top:15px; padding-top:15px; border-top:1px dashed var(--border);">
                <label class="auto-label" style="color:var(--primary);">⚙️ IA 风控保护 (限流与重试)</label>
                <div style="display:flex; flex-wrap:wrap; gap:10px; margin-top:8px;">
                    <div style="flex:1; min-width:120px;"><label style="font-size:12px;">窗口内最多请求</label><input type="number" name="ia_rate_max" value="{{ cfg.ia_rate_max }}" min="1" max="100"></div>
                    <div style="flex:1; min-width:120px;"><label style="font-size:12px;">窗口秒数</label><input type="number" name="ia_rate_window" value="{{ cfg.ia_rate_window }}" min="5" max="600"></div>
                    <div style="flex:1; min-width:120px;"><label style="font-size:12px;">429冷却(秒)</label><input type="number" name="ia_429_backoff" value="{{ cfg.ia_429_backoff }}" min="10" max="600"></div>
                </div>
            </div>
        </fieldset>

        <fieldset style="border: 2px solid var(--orange); background: rgba(255, 152, 0, 0.05);">
            <legend style="color: var(--orange);">🤖 自动化执行开关 (默认全关)</legend>
            <span class="tip" style="border-left-color: var(--orange); background: rgba(255, 152, 0, 0.1);">这里所有开关出厂默认都是<b>关闭</b>的。为了防止“背着你”消耗流量或修改数据，只有你主动开启的选项才会生效。</span>
            
            <div class="auto-switch-row"><label class="auto-label">上传完成后同步官方衍生文件</label><span style="font-size:12px; color:var(--gray);">（IA后台生成的种子、转码等，非你传的原文件）</span><div class="switch-container"><select name="auto_fetch_after_upload" style="width:auto; margin:0;"><option value="lazy" {% if cfg.auto_fetch_after_upload=='lazy' %}selected{% endif %}>延迟（默认，等你点文件才拉）</option><option value="immediate" {% if cfg.auto_fetch_after_upload=='immediate' %}selected{% endif %}>立即（传完自动拉）</option><option value="never" {% if cfg.auto_fetch_after_upload=='never' %}selected{% endif %}>永不（纯手动）</option></select></div></div>
            <div class="auto-switch-row"><label class="auto-label">打开详情页自动拉取文件列表</label><div class="switch-container"><label class="switch"><input type="checkbox" name="auto_fetch_on_detail" value="yes" {% if cfg.auto_fetch_on_detail %}checked{% endif %}><span class="slider"></span></label></div></div>
            <div class="auto-switch-row"><label class="auto-label">本地无图时，自动下载缩略图</label><span style="font-size:12px; color:var(--gray);">（只要本地有图，永远优先用本地，不耗流量）</span><div class="switch-container"><label class="switch"><input type="checkbox" name="auto_load_thumb" value="yes" {% if cfg.auto_load_thumb %}checked{% endif %}><span class="slider"></span></label></div></div>
            <div class="auto-switch-row"><label class="auto-label">输入 ID 时自动检查冲突</label><div class="switch-container"><label class="switch"><input type="checkbox" name="auto_check_id" value="yes" {% if cfg.auto_check_id %}checked{% endif %}><span class="slider"></span></label></div></div>
            <div class="auto-switch-row"><label class="auto-label">每次启动时自动清空发现页搜索历史</label><div class="switch-container"><label class="switch"><input type="checkbox" name="auto_clear_history" value="yes" {% if cfg.auto_clear_history %}checked{% endif %}><span class="slider"></span></label></div></div>
            <div class="auto-switch-row"><label class="auto-label">每周自动备份数据库</label><div class="switch-container"><label class="switch"><input type="checkbox" name="auto_backup" value="yes" {% if cfg.auto_backup %}checked{% endif %}><span class="slider"></span></label></div></div>
            <div class="auto-switch-row"><label class="auto-label">每天自动清理回收站</label><div class="switch-container"><label class="switch"><input type="checkbox" name="auto_clean_trash" value="yes" {% if cfg.auto_clean_trash %}checked{% endif %}><span class="slider"></span></label></div></div>
            <div class="auto-switch-row"><label class="auto-label">自动清理内存中已结束的任务</label><span style="font-size:12px; color:var(--gray);">（上传完成或报错 1 小时后自动从队列移除，不影响历史记录）</span><div class="switch-container"><label class="switch"><input type="checkbox" name="auto_clean_tasks" value="yes" {% if cfg.auto_clean_tasks %}checked{% endif %}><span class="slider"></span></label></div></div>
            <div class="auto-switch-row"><label class="auto-label">允许自动重命名文件（内部接口）</label><div class="switch-container"><label class="switch"><input type="checkbox" name="rename_allow_tasks_php" value="yes" {% if cfg.rename_allow_tasks_php %}checked{% endif %}><span class="slider"></span></label></div></div>
            <div class="auto-switch-row"><label class="auto-label">上传时默认勾选“生成种子”</label><div class="switch-container"><label class="switch"><input type="checkbox" name="default_generate_torrent" value="yes" {% if cfg.default_generate_torrent %}checked{% endif %}><span class="slider"></span></label></div></div>
        </fieldset>

        <!-- ==================== 缓存策略 ==================== -->
        <fieldset style="border: 2px solid var(--primary); background: rgba(0, 150, 136, 0.05);">
            <legend style="color: var(--primary);">💾 长期数据缓存策略 (默认永久保留)</legend>
            <span class="tip" style="border-left-color: var(--primary); background: rgba(0, 150, 136, 0.1);">这里的配置决定了网盘数据能保留多久。为了让你回头找东西时依然秒开，默认 <b>-1（永久）</b>，不会自动删除。</span>
            
            <div style="display:grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap:15px;">
                <div>
                    <label style="font-size:13px; font-weight:bold;">文件列表缓存 (秒)</label>
                    <input type="number" name="file_cache_ttl" value="{{ cfg.file_cache_ttl }}" min="-1">
                    <span class="tip" style="font-size:11px; padding:4px;">默认 -1 永久，不自动删。</span>
                </div>
                <div>
                    <label style="font-size:13px; font-weight:bold;">缩略图缓存 (秒)</label>
                    <input type="number" name="thumb_cache_ttl" value="{{ cfg.thumb_cache_ttl }}" min="-1">
                    <span class="tip" style="font-size:11px; padding:4px;">默认 -1 永久，不自动删。</span>
                </div>
                <div>
                    <label style="font-size:13px; font-weight:bold;">从 IA 拉取结果缓存 (秒)</label>
                    <input type="number" name="pull_cache_ttl" value="{{ cfg.pull_cache_ttl }}" min="-1">
                    <span class="tip" style="font-size:11px; padding:4px;">默认 -1 永久，防止频繁请求。</span>
                </div>
                <div>
                    <label style="font-size:13px; font-weight:bold;">ID 预检缓存 (秒)</label>
                    <input type="number" name="id_check_ttl" value="{{ cfg.id_check_ttl }}" min="0">
                    <span class="tip" style="font-size:11px; padding:4px;">默认 300，短效防重复。</span>
                </div>
                <div>
                    <label style="font-size:13px; font-weight:bold;">磁力解析失败缓存 (秒)</label>
                    <input type="number" name="magnet_fail_ttl" value="{{ cfg.magnet_fail_ttl }}" min="0">
                    <span class="tip" style="font-size:11px; padding:4px;">默认 300，仅缓存失败结果。</span>
                </div>
            </div>
            <div style="margin-top:15px;">
                <label style="font-size:13px; font-weight:bold;">回收站过期清理 (天数，填 0 不清理):</label>
                <input type="number" name="trash_retention" value="{{ trash_ret }}" min="0">
                <span class="tip" style="font-size:11px; padding:4px;">只有打开了上方“每天自动清理回收站”开关时，这个天数才会生效。</span>
            </div>
        </fieldset>

        <!-- ==================== 下载设置 ==================== -->
        <fieldset>
            <legend>📥 Termux 下载设置</legend>
            <label>下载保存目录 (默认 /sdcard/Download):</label>
            <input type="text" name="download_dir" value="{{ dl_dir }}" placeholder="/sdcard/Download">
            
            <label style="margin-top:15px;">🔹 单文件下载默认方式:</label>
            <select name="single_download_default">
                <option value="ask" {% if single_download_default == 'ask' %}selected{% endif %}>每次询问我（默认）</option>
                <option value="termux" {% if single_download_default == 'termux' %}selected{% endif %}>默认 Termux 后台下载</option>
                <option value="browser" {% if single_download_default == 'browser' %}selected{% endif %}>默认浏览器直接下载</option>
            </select>

            <label style="margin-top:15px;">🔹 批量下载默认方式:</label>
            <select name="batch_download_default">
                <option value="ask" {% if batch_download_default == 'ask' %}selected{% endif %}>每次询问我（默认）</option>
                <option value="termux" {% if batch_download_default == 'termux' %}selected{% endif %}>默认全部走 Termux 后台下载</option>
                <option value="browser" {% if batch_download_default == 'browser' %}selected{% endif %}>默认全部走浏览器直接下载</option>
                <option value="per_file" {% if batch_download_default == 'per_file' %}selected{% endif %}>逐文件单独询问</option>
            </select>
        </fieldset>

        <!-- ==================== 底层性能参数 ==================== -->
        <details>
            <summary>🚀 底层性能与高级限流参数 (高级玩家可调)</summary>
            <div style="margin-top:15px; display:grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap:15px;">
                <div><label>最大并发任务数 (1-10):</label><input type="number" name="max_concurrent" min="1" max="10" value="{{ max_c }}"><span class="tip" style="font-size:11px; padding:4px;">大卡片的并发上限。</span></div>
                <div><label>删除任务内部并发 (1-10):</label><input type="number" name="delete_concurrency" min="1" max="10" value="{{ delete_conc }}"><span class="tip" style="font-size:11px; padding:4px;">批量删除时同时删几个文件。</span></div>
                <div><label>ID 输入框防抖 (毫秒):</label><input type="number" name="id_check_debounce_ms" value="{{ cfg.id_check_debounce_ms }}" min="0" max="5000"></div>
                <div><label>每 IP 每分钟 ID 预检上限:</label><input type="number" name="id_check_ip_limit" value="{{ cfg.id_check_ip_limit }}" min="1" max="100"></div>
                <div><label>每 IP 每分钟缩略图上限:</label><input type="number" name="thumb_ip_limit" value="{{ cfg.thumb_ip_limit }}" min="1" max="500"></div>
                <div><label>强制刷新缓存冷却 (秒):</label><input type="number" name="refresh_cooldown" value="{{ cfg.refresh_cooldown }}" min="0" max="3600"></div>
                <div><label>逐文件删除间隔 (秒):</label><input type="number" step="0.1" name="delete_file_sleep" value="{{ cfg.delete_file_sleep }}" min="0"><span class="tip" style="font-size:11px; padding:4px;">填太小容易触发风控。</span></div>
                <div><label>上传重试次数上限 (0-5):</label><input type="number" name="upload_retries_max" value="{{ cfg.upload_retries_max }}" min="0" max="5"><span class="tip" style="font-size:11px; padding:4px;">控制「新建上传任务」表单里「自动重试次数」输入框的最大值上限。区别于板块 1 的「单文件重试次数」。</span></div>
                <div><label>一键重试启动上限:</label><input type="number" name="execute_batch_limit" value="{{ cfg.execute_batch_limit }}" min="1" max="100"></div>
                <!-- 【改进点 76】新增 raw_line_max_lines 设置项 -->
                <div><label>单任务日志最大行数 (10-5000):</label><input type="number" name="raw_line_max_lines" value="{{ cfg.raw_line_max_lines }}" min="10" max="5000"><span class="tip" style="font-size:11px; padding:4px;">每个任务在队列页最多保留多少行日志（默认 500）。</span></div>
            </div>
        </details>

        <!-- ==================== 【⏱️ 延迟与超时精细控制】7 大板块 ==================== -->
        <details>
            <summary>⏱️ 延迟与超时精细控制 (7 大板块 · 高级玩家可调)</summary>
            <span class="tip" style="border-left-color: var(--purple); background: rgba(156,39,176,0.08); margin-top:10px;">
                💡 这部分提供了所有底层延迟、超时、重试次数的自由调整。每项都有合法范围，超出范围会自动取边界值。<br>
                ⚠️ <b>以下参数默认已优化，不懂的保持默认即可。</b>所有字段都是"自由输入"，不需要选下拉。<br>
                ⏳ <b>部署进度提示：</b>部分参数将在对应模块接入后才完全生效；当前保存后，可能会看到部分参数暂未立即生效（后续版本会逐一接入），届时会自动生效，无需再次修改。
            </span>

            <!-- ============ 板块1：⬆️ 上传与 S3 分片延迟 ============ -->
            <div style="border:2px solid var(--purple); border-radius:8px; padding:12px; margin-bottom:15px; background:rgba(156,39,176,0.03);">
                <h3 style="font-size:14px; color:var(--purple); border:none; margin-top:0;">⬆️ 板块 1：上传与 S3 分片延迟</h3>
                <span class="tip" style="font-size:11px; background:rgba(156,39,176,0.08); border-left-color:var(--purple);">影响大文件分片上传速度与稳定性。默认值已经过优化。</span>
                <div style="display:grid; grid-template-columns:repeat(auto-fit, minmax(240px, 1fr)); gap:12px; margin-top:10px;">
                    <div>
                        <label>S3 分片间等待 (秒)</label>
                        <input type="number" name="s3_part_sleep" value="{{ cfg.s3_part_sleep }}" min="0" max="10" step="any" inputmode="decimal">
                        <span class="tip" style="font-size:11px; padding:4px;">默认 0.1。每个分片传完后等待，越大越慢但越稳。</span>
                    </div>
                    <div>
                        <label>S3 重试退避基数</label>
                        <input type="number" name="s3_retry_backoff_base" value="{{ cfg.s3_retry_backoff_base }}" min="1" max="10" step="1" inputmode="numeric">
                        <span class="tip" style="font-size:11px; padding:4px; background:rgba(244,67,54,0.08); border-left-color:var(--danger);">⚠️ 指数增长：填 2 时重试间隔为 1+2+4=7 秒；填 10 时为 1+10+100=111 秒。默认 2。</span>
                    </div>
                    <div>
                        <label>S3 连接超时 (秒)</label>
                        <input type="number" name="s3_timeout_connect" value="{{ cfg.s3_timeout_connect }}" min="5" max="120" step="1" inputmode="numeric">
                        <span class="tip" style="font-size:11px; padding:4px;">默认 15。建立连接的最长等待。</span>
                    </div>
                    <div>
                        <label>S3 读取超时 (秒)</label>
                        <input type="number" name="s3_timeout_read" value="{{ cfg.s3_timeout_read }}" min="10" max="600" step="1" inputmode="numeric">
                        <span class="tip" style="font-size:11px; padding:4px;">默认 60。等待响应数据的最长等待。</span>
                    </div>
                    <div>
                        <label>上传主循环刷新 (秒)</label>
                        <input type="number" name="upload_poll_interval" value="{{ cfg.upload_poll_interval }}" min="0.1" max="10" step="any" inputmode="decimal">
                        <span class="tip" style="font-size:11px; padding:4px;">默认 0.5。上传进度/状态的刷新轮询间隔。</span>
                    </div>
                    <div>
                        <label>单文件重试次数 (0-20)</label>
                        <input type="number" name="upload_file_retry" value="{{ cfg.upload_file_retry }}" min="0" max="20" step="1" inputmode="numeric">
                        <span class="tip" style="font-size:11px; padding:4px; background:rgba(33,150,243,0.08); border-left-color:var(--blue);">只作用于<b>上传 worker 的单文件失败重试</b>。区别于板块 5 的「Worker 风控重试」和「底层性能」里的「上传重试次数上限」。</span>
                    </div>
                    <div>
                        <label>TCP 保活空闲 (秒)</label>
                        <input type="number" name="tcp_keepidle" value="{{ cfg.tcp_keepidle }}" min="5" max="300" step="1" inputmode="numeric">
                        <span class="tip" style="font-size:11px; padding:4px;">默认 30。pycurl 连接空闲多久开始保活探测。</span>
                    </div>
                    <div>
                        <label>TCP 保活间隔 (秒)</label>
                        <input type="number" name="tcp_keepintvl" value="{{ cfg.tcp_keepintvl }}" min="1" max="60" step="1" inputmode="numeric">
                        <span class="tip" style="font-size:11px; padding:4px;">默认 10。两次保活探测的间隔。</span>
                    </div>
                </div>
            </div>

            <!-- ============ 板块2：⬇️ 下载与代理延迟 ============ -->
            <div style="border:2px solid var(--blue); border-radius:8px; padding:12px; margin-bottom:15px; background:rgba(33,150,243,0.03);">
                <h3 style="font-size:14px; color:var(--blue); border:none; margin-top:0;">⬇️ 板块 2：下载与代理延迟</h3>
                <span class="tip" style="font-size:11px; background:rgba(33,150,243,0.08); border-left-color:var(--blue);">影响单文件下载、下载代理、在线预览的超时和重试。默认值已经过优化。</span>
                <div style="display:grid; grid-template-columns:repeat(auto-fit, minmax(240px, 1fr)); gap:12px; margin-top:10px;">
                    <div>
                        <label>下载连接超时 (秒)</label>
                        <input type="number" name="download_timeout_connect" value="{{ cfg.download_timeout_connect }}" min="5" max="120" step="1" inputmode="numeric">
                        <span class="tip" style="font-size:11px; padding:4px;">默认 15。单文件下载建立连接的最长等待。</span>
                    </div>
                    <div>
                        <label>下载读取超时 (秒)</label>
                        <input type="number" name="download_timeout_read" value="{{ cfg.download_timeout_read }}" min="5" max="300" step="1" inputmode="numeric">
                        <span class="tip" style="font-size:11px; padding:4px;">默认 15。单文件下载等待数据的最长等待。</span>
                    </div>
                    <div>
                        <label>下载重试间隔 (秒)</label>
                        <input type="number" name="download_retry_sleep" value="{{ cfg.download_retry_sleep }}" min="0" max="60" step="any" inputmode="decimal">
                        <!-- 【R61 修复】文案改为「总等待时长」+「默认 5」 -->
                        <span class="tip" style="font-size:11px; padding:4px;">默认 5。下载失败后的<b>总等待时长</b>，期间每 0.5 秒检查一次暂停。支持小数（如 0.5 秒）。</span>
                    </div>
                    <div>
                        <label>下载进度刷新 (秒)</label>
                        <input type="number" name="download_progress_interval" value="{{ cfg.download_progress_interval }}" min="0.1" max="10" step="any" inputmode="decimal">
                        <span class="tip" style="font-size:11px; padding:4px;">默认 1。下载进度写库频率。</span>
                    </div>
                    <div>
                        <label>流式块大小 (字节)</label>
                        <input type="number" name="stream_chunk_size" value="{{ cfg.stream_chunk_size }}" min="4096" max="1048576" step="1" inputmode="numeric">
                        <span class="tip" style="font-size:11px; padding:4px;">默认 65536（≈ 64 KB）。下载/预览每次读取字节数。4096 ≈ 4 KB，1048576 ≈ 1 MB。</span>
                    </div>
                    <div>
                        <label>代理下载 IP 限流 (次/分)</label>
                        <input type="number" name="proxy_dl_ip_limit" value="{{ cfg.proxy_dl_ip_limit }}" min="1" max="10000" step="1" inputmode="numeric">
                        <span class="tip" style="font-size:11px; padding:4px;">默认 200。每 IP 每分钟最多下载代理请求。</span>
                    </div>
                    <!-- 【B 级新增】proxy_dl_ip_window -->
                    <div>
                        <label>代理下载 IP 限流窗口 (秒)</label>
                        <input type="number" name="proxy_dl_ip_window" value="{{ cfg.proxy_dl_ip_window }}" min="10" max="3600" step="1" inputmode="numeric">
                        <span class="tip" style="font-size:11px; padding:4px;">默认 60。每 IP 限流滑动窗口时长，与「代理下载 IP 限流」配合使用。</span>
                    </div>
                    <div>
                        <label>代理下载连接超时 (秒)</label>
                        <input type="number" name="proxy_dl_timeout_connect" value="{{ cfg.proxy_dl_timeout_connect }}" min="1" max="60" step="1" inputmode="numeric">
                        <span class="tip" style="font-size:11px; padding:4px;">默认 5。</span>
                    </div>
                    <div>
                        <label>代理下载读取超时 (秒)</label>
                        <input type="number" name="proxy_dl_timeout_read" value="{{ cfg.proxy_dl_timeout_read }}" min="5" max="300" step="1" inputmode="numeric">
                        <span class="tip" style="font-size:11px; padding:4px;">默认 15。</span>
                    </div>
                    <div>
                        <label>代理下载重试次数</label>
                        <input type="number" name="proxy_dl_retry" value="{{ cfg.proxy_dl_retry }}" min="0" max="10" step="1" inputmode="numeric">
                        <span class="tip" style="font-size:11px; padding:4px;">默认 3。填 0 = 不重试但至少尝试 1 次。</span>
                    </div>
                    <div>
                        <label>预览连接超时 (秒)</label>
                        <input type="number" name="preview_timeout_connect" value="{{ cfg.preview_timeout_connect }}" min="1" max="60" step="1" inputmode="numeric">
                        <span class="tip" style="font-size:11px; padding:4px;">默认 10。</span>
                    </div>
                    <div>
                        <label>预览读取超时 (秒)</label>
                        <input type="number" name="preview_timeout_read" value="{{ cfg.preview_timeout_read }}" min="5" max="600" step="1" inputmode="numeric">
                        <span class="tip" style="font-size:11px; padding:4px;">默认 60。</span>
                    </div>
                </div>
            </div>

            <!-- ============ 板块3：🌐 前端分片与轮询 ============ -->
            <div style="border:2px solid var(--green); border-radius:8px; padding:12px; margin-bottom:15px; background:rgba(76,175,80,0.03);">
                <h3 style="font-size:14px; color:var(--green); border:none; margin-top:0;">🌐 板块 3：前端分片与轮询</h3>
                <span class="tip" style="font-size:11px; background:rgba(76,175,80,0.08); border-left-color:var(--green);">影响浏览器端上传分片、队列刷新、各页面请求超时。修改后请刷新页面生效。</span>
                <div style="display:grid; grid-template-columns:repeat(auto-fit, minmax(240px, 1fr)); gap:12px; margin-top:10px;">
                    <div>
                        <label>前端分片阈值 (MB)</label>
                        <input type="number" name="frontend_chunk_threshold_mb" value="{{ cfg.frontend_chunk_threshold_mb }}" min="5" max="2048" step="1" inputmode="numeric">
                        <span class="tip" style="font-size:11px; padding:4px;">默认 50。浏览器上传超过此大小自动分片。</span>
                    </div>
                    <div>
                        <label>前端分片大小 (MB)</label>
                        <input type="number" name="frontend_chunk_size_mb" value="{{ cfg.frontend_chunk_size_mb }}" min="1" max="50" step="1" inputmode="numeric">
                        <span class="tip" style="font-size:11px; padding:4px;">默认 5。每个分片的大小。</span>
                    </div>
                    <div>
                        <label>前端分片重试次数</label>
                        <input type="number" name="frontend_chunk_retries" value="{{ cfg.frontend_chunk_retries }}" min="0" max="10" step="1" inputmode="numeric">
                        <span class="tip" style="font-size:11px; padding:4px;">默认 3。单个分片失败后重试次数。填 0 = 不重试但至少尝试 1 次。</span>
                    </div>
                    <div>
                        <label>前端分片重试间隔 (毫秒)</label>
                        <input type="number" name="frontend_chunk_retry_sleep_ms" value="{{ cfg.frontend_chunk_retry_sleep_ms }}" min="100" max="10000" step="1" inputmode="numeric">
                        <span class="tip" style="font-size:11px; padding:4px;">默认 800。分片失败后等待多久重试。</span>
                    </div>
                    <div>
                        <label>队列页刷新间隔 (毫秒)</label>
                        <input type="number" name="queue_poll_interval_ms" value="{{ cfg.queue_poll_interval_ms }}" min="200" max="10000" step="1" inputmode="numeric">
                        <span class="tip" style="font-size:11px; padding:4px;">默认 500。队列页轮询 /api/tasks 间隔。</span>
                    </div>
                    <div>
                        <label>队列刷新超时 (毫秒)</label>
                        <input type="number" name="queue_refresh_timeout_ms" value="{{ cfg.queue_refresh_timeout_ms }}" min="2000" max="60000" step="1" inputmode="numeric">
                        <span class="tip" style="font-size:11px; padding:4px;">默认 10000 (10 秒)。单次刷新 AbortController 超时。</span>
                    </div>
                    <div>
                        <label>队列卡住解锁 (毫秒)</label>
                        <input type="number" name="queue_stuck_timeout_ms" value="{{ cfg.queue_stuck_timeout_ms }}" min="3000" max="120000" step="1" inputmode="numeric">
                        <!-- 【R159 修复】加「建议 ≥ 队列刷新超时 + 5 秒」提示 -->
                        <span class="tip" style="font-size:11px; padding:4px; background:rgba(33,150,243,0.08); border-left-color:var(--blue);">默认 15000 (15 秒)。_refreshInFlight 卡住兜底解锁。<b>建议 ≥ 队列刷新超时 + 5 秒</b>（前端会 Math.max(stuck, refresh + 5000) 兜底，填太小会自动放大）。</span>
                    </div>
                    <div>
                        <label>上传 XHR 超时 (毫秒)</label>
                        <input type="number" name="upload_xhr_timeout_ms" value="{{ cfg.upload_xhr_timeout_ms }}" min="30000" max="3600000" step="1" inputmode="numeric">
                        <span class="tip" style="font-size:11px; padding:4px;">默认 600000 (10 分钟)。上传页/追加页 XHR 总超时。</span>
                    </div>
                    <div>
                        <label>详情页拉取超时 (毫秒)</label>
                        <input type="number" name="detail_fetch_timeout_ms" value="{{ cfg.detail_fetch_timeout_ms }}" min="1000" max="120000" step="1" inputmode="numeric">
                        <span class="tip" style="font-size:11px; padding:4px;">默认 15000 (15 秒)。详情页/首页 file_cache 拉取超时。</span>
                    </div>
                    <div>
                        <label>详情页 ZIP 超时 (毫秒)</label>
                        <input type="number" name="detail_zip_timeout_ms" value="{{ cfg.detail_zip_timeout_ms }}" min="3000" max="300000" step="1" inputmode="numeric">
                        <span class="tip" style="font-size:11px; padding:4px;">默认 30000 (30 秒)。ZIP 结构解析超时。</span>
                    </div>
                    <!-- 【B 级新增】id_check_timeout -->
                    <div>
                        <label>ID 预检超时 (毫秒)</label>
                        <input type="number" name="id_check_timeout" value="{{ cfg.id_check_timeout }}" min="5000" max="120000" step="1" inputmode="numeric">
                        <span class="tip" style="font-size:11px; padding:4px; background:rgba(33,150,243,0.08); border-left-color:var(--blue);">默认 20000 (20 秒)。上传页 ID 预检请求的超时。区别于上方「ID 预检缓存」和「ID 输入框防抖」。</span>
                    </div>
                    <!-- 【R279 新增】id_check_ip_window -->
                    <div>
                        <label>ID 预检 IP 限流窗口 (秒)</label>
                        <input type="number" name="id_check_ip_window" value="{{ cfg.id_check_ip_window }}" min="10" max="3600" step="1" inputmode="numeric">
                        <span class="tip" style="font-size:11px; padding:4px; background:rgba(33,150,243,0.08); border-left-color:var(--blue);">默认 60。每 IP ID 预检限流滑动窗口，与「底层性能」里的「每 IP 每分钟 ID 预检上限」配合使用。</span>
                    </div>
                </div>
            </div>

            <!-- ============ 板块4：🖼️ 缩略图与虚拟任务 ============ -->
            <div style="border:2px solid var(--orange); border-radius:8px; padding:12px; margin-bottom:15px; background:rgba(255,152,0,0.03);">
                <h3 style="font-size:14px; color:var(--orange); border:none; margin-top:0;">🖼️ 板块 4：缩略图与虚拟任务</h3>
                <span class="tip" style="font-size:11px; background:rgba(255,152,0,0.08); border-left-color:var(--orange);">缩略图超时只在 auto_load_thumb 开关开启或用户主动刷新时用到；虚拟任务默认永不自动清理。</span>
                <div style="display:grid; grid-template-columns:repeat(auto-fit, minmax(240px, 1fr)); gap:12px; margin-top:10px;">
                    <div>
                        <label>缩略图下载超时 (秒)</label>
                        <input type="number" name="thumb_timeout" value="{{ cfg.thumb_timeout }}" min="1" max="60" step="1" inputmode="numeric">
                        <span class="tip" style="font-size:11px; padding:4px;">默认 4。自动拉缩略图超时（只在 auto_load_thumb 开启时触发）。</span>
                    </div>
                    <div>
                        <label>缩略图手动刷新超时 (秒)</label>
                        <input type="number" name="thumb_manual_timeout" value="{{ cfg.thumb_manual_timeout }}" min="3" max="120" step="1" inputmode="numeric">
                        <span class="tip" style="font-size:11px; padding:4px;">默认 15。用户点击「重新获取」时的超时。</span>
                    </div>
                    <div>
                        <label>缩略图失败缓存 (秒)</label>
                        <input type="number" name="thumb_fail_cache_ttl" value="{{ cfg.thumb_fail_cache_ttl }}" min="0" max="3600" step="1" inputmode="numeric">
                        <span class="tip" style="font-size:11px; padding:4px;">默认 300。失败后多久内不再重试拉取（不是自动删除已下载的图）。</span>
                    </div>
                    <!-- 【B 级新增】thumb_ip_window -->
                    <div>
                        <label>缩略图 IP 限流窗口 (秒)</label>
                        <input type="number" name="thumb_ip_window" value="{{ cfg.thumb_ip_window }}" min="10" max="3600" step="1" inputmode="numeric">
                        <span class="tip" style="font-size:11px; padding:4px;">默认 60。每 IP 缩略图限流滑动窗口，与「每 IP 每分钟缩略图上限」配合使用。</span>
                    </div>
                    <div>
                        <label>虚拟任务过期 (秒，0=永不)</label>
                        <input type="number" name="virtual_task_ttl" value="{{ cfg.virtual_task_ttl }}" min="0" max="86400" step="1" inputmode="numeric">
                        <span class="tip" style="font-size:11px; padding:4px; background:rgba(76,175,80,0.08); border-left-color:var(--green);">默认 <b>0 = 永不自动清理</b>（推荐）。浏览器下载产生的虚拟任务记录默认保留，用户可去队列页手动清理。填 &gt;0 才启用自动清理。</span>
                    </div>
                </div>
            </div>

            <!-- ============ 板块5：📋 任务队列与数据库 ============ -->
            <div style="border:2px solid var(--gray); border-radius:8px; padding:12px; margin-bottom:15px; background:rgba(96,125,139,0.03);">
                <h3 style="font-size:14px; color:var(--gray); border:none; margin-top:0;">📋 板块 5：任务队列与数据库</h3>
                <span class="tip" style="font-size:11px; background:rgba(96,125,139,0.08); border-left-color:var(--gray);">影响队列恢复、SQLite 连接、Worker 重试次数。默认值已经过优化。</span>
                <div style="display:grid; grid-template-columns:repeat(auto-fit, minmax(240px, 1fr)); gap:12px; margin-top:10px;">
                    <div>
                        <label>单次 resume 重启上限 (个)</label>
                        <input type="number" name="max_auto_restart_per_resume" value="{{ cfg.max_auto_restart_per_resume }}" min="1" max="100" step="1" inputmode="numeric">
                        <span class="tip" style="font-size:11px; padding:4px;">默认 20。每次点击「继续」最多自动重启几个文件。</span>
                    </div>
                    <div>
                        <label>SQLite 连接超时 (秒)</label>
                        <input type="number" name="db_timeout" value="{{ cfg.db_timeout }}" min="5" max="300" step="1" inputmode="numeric">
                        <span class="tip" style="font-size:11px; padding:4px;">默认 30。sqlite3.connect timeout。</span>
                    </div>
                    <div>
                        <label>Worker 风控重试次数</label>
                        <input type="number" name="worker_max_retries" value="{{ cfg.worker_max_retries }}" min="0" max="20" step="1" inputmode="numeric">
                        <!-- 【R107 修复】明确「填 0 = 不重试但至少尝试 1 次」 -->
                        <span class="tip" style="font-size:11px; padding:4px; background:rgba(33,150,243,0.08); border-left-color:var(--blue);">默认 5。<b>填 0 = 不重试但至少尝试 1 次</b>。作用于<b>追加 / 元数据 / 重命名</b> worker 的重试。区别于板块 1 的「单文件重试」和「底层性能」里的「上传重试次数上限」。</span>
                    </div>
                </div>
            </div>

            <!-- ============ 板块6：🤖 后台守护 ============ -->
            <div style="border:2px solid var(--danger); border-radius:8px; padding:12px; margin-bottom:15px; background:rgba(244,67,54,0.03);">
                <h3 style="font-size:14px; color:var(--danger); border:none; margin-top:0;">🤖 板块 6：后台守护</h3>
                <span class="tip" style="font-size:11px; background:rgba(244,67,54,0.08); border-left-color:var(--danger);">
                    ⚠️ <b>以下参数需先在「🤖 自动化执行开关」中开启对应开关才生效</b>（开关默认关闭）：<br>
                    · <code>auto_backup</code> 开关 → 控制「自动备份间隔」「自动备份保留数」<br>
                    · <code>auto_clean_tasks</code> 开关 → 控制「内存任务清理间隔」「内存任务过期阈值」<br>
                    不开启开关时，这些参数完全不生效，不会自动执行任何动作。
                </span>
                <div style="display:grid; grid-template-columns:repeat(auto-fit, minmax(240px, 1fr)); gap:12px; margin-top:10px;">
                    <div>
                        <label>自动备份间隔 (天)</label>
                        <input type="number" name="auto_backup_days" value="{{ cfg.auto_backup_days }}" min="1" max="365" step="1" inputmode="numeric">
                        <span class="tip" style="font-size:11px; padding:4px;">默认 7。仅在 auto_backup 开关开启时生效。</span>
                    </div>
                    <div>
                        <label>自动备份保留数 (个)</label>
                        <input type="number" name="auto_backup_keep" value="{{ cfg.auto_backup_keep }}" min="1" max="20" step="1" inputmode="numeric">
                        <span class="tip" style="font-size:11px; padding:4px;">默认 4。仅在 auto_backup 开关开启时生效。每个备份 = config.json + db。</span>
                    </div>
                    <div>
                        <label>内存任务清理间隔 (秒)</label>
                        <input type="number" name="clean_memory_interval" value="{{ cfg.clean_memory_interval }}" min="60" max="86400" step="1" inputmode="numeric">
                        <span class="tip" style="font-size:11px; padding:4px;">默认 3600。仅在 auto_clean_tasks 开关开启时生效。</span>
                    </div>
                    <div>
                        <label>内存任务过期阈值 (秒)</label>
                        <input type="number" name="clean_memory_age" value="{{ cfg.clean_memory_age }}" min="60" max="86400" step="1" inputmode="numeric">
                        <span class="tip" style="font-size:11px; padding:4px;">默认 3600。任务存在多久后才算过期（仅在 auto_clean_tasks 开启时生效）。</span>
                    </div>
                </div>
            </div>

            <!-- ============ 板块7：🌐 WARP 代理 ============ -->
            <div style="border:2px solid var(--primary); border-radius:8px; padding:12px; margin-bottom:15px; background:rgba(0,150,136,0.03);">
                <h3 style="font-size:14px; color:var(--primary); border:none; margin-top:0;">🌐 板块 7：WARP 代理</h3>
                <span class="tip" style="font-size:11px; background:rgba(0,150,136,0.08); border-left-color:var(--primary);">⚠️ 需要代理功能可用时才会用到这些参数。不影响主流程。</span>
                <div style="display:grid; grid-template-columns:repeat(auto-fit, minmax(240px, 1fr)); gap:12px; margin-top:10px;">
                    <div>
                        <label>IP 优选并发 (个)</label>
                        <input type="number" name="warp_ip_concurrency" value="{{ cfg.warp_ip_concurrency }}" min="1" max="100" step="1" inputmode="numeric">
                        <span class="tip" style="font-size:11px; padding:4px;">默认 20。并测 Cloudflare IP 的线程数。</span>
                    </div>
                    <div>
                        <label>IP 单次超时 (秒)</label>
                        <input type="number" name="warp_ip_timeout" value="{{ cfg.warp_ip_timeout }}" min="0.5" max="10" step="any" inputmode="decimal">
                        <span class="tip" style="font-size:11px; padding:4px;">默认 1.5。单个 IP TCP 连接超时。</span>
                    </div>
                    <div>
                        <label>IP 优选阈值 (秒)</label>
                        <input type="number" name="warp_ip_threshold" value="{{ cfg.warp_ip_threshold }}" min="0.5" max="10" step="any" inputmode="decimal">
                        <!-- 【R164 修复】加「建议 ≤ IP 单次超时」提示 -->
                        <span class="tip" style="font-size:11px; padding:4px; background:rgba(33,150,243,0.08); border-left-color:var(--blue);">默认 1.5。超过此延迟的 IP 被丢弃。<b>建议 ≤ IP 单次超时</b>（超过单次超时的 IP 会返回 999.0 哨兵值被丢弃，阈值宽松无效）。</span>
                    </div>
                    <div>
                        <label>最优 IP 保留数 (个)</label>
                        <input type="number" name="warp_ip_keep" value="{{ cfg.warp_ip_keep }}" min="1" max="20" step="1" inputmode="numeric">
                        <span class="tip" style="font-size:11px; padding:4px;">默认 5。保留前几个最优 IP。</span>
                    </div>
                    <div>
                        <label>注册超时 (秒)</label>
                        <input type="number" name="warp_register_timeout" value="{{ cfg.warp_register_timeout }}" min="30" max="600" step="1" inputmode="numeric">
                        <span class="tip" style="font-size:11px; padding:4px;">默认 120。usque 走代理注册超时。</span>
                    </div>
                    <div>
                        <label>直连注册超时 (秒)</label>
                        <input type="number" name="warp_direct_timeout" value="{{ cfg.warp_direct_timeout }}" min="10" max="300" step="1" inputmode="numeric">
                        <span class="tip" style="font-size:11px; padding:4px;">默认 30。usque 直连注册超时。</span>
                    </div>
                    <div>
                        <label>API 探测超时 (秒)</label>
                        <input type="number" name="warp_probe_timeout" value="{{ cfg.warp_probe_timeout }}" min="3" max="60" step="1" inputmode="numeric">
                        <span class="tip" style="font-size:11px; padding:4px;">默认 10。代理探测 cloudflare API 超时。</span>
                    </div>
                    <div>
                        <label>核心启动等待 (秒)</label>
                        <input type="number" name="warp_startup_wait" value="{{ cfg.warp_startup_wait }}" min="0.3" max="10" step="any" inputmode="decimal">
                        <span class="tip" style="font-size:11px; padding:4px;">默认 1.0。sing-box 启动后等待监听。</span>
                    </div>
                    <div>
                        <label>代理测速超时 (秒)</label>
                        <input type="number" name="proxy_test_timeout" value="{{ cfg.proxy_test_timeout }}" min="1" max="60" step="1" inputmode="numeric">
                        <span class="tip" style="font-size:11px; padding:4px;">默认 8。/api/proxy/test 超时。</span>
                    </div>
                    <!-- 【B 级新增】warp_usque_startup_timeout -->
                    <div>
                        <label>usque 启动检测超时 (秒)</label>
                        <input type="number" name="warp_usque_startup_timeout" value="{{ cfg.warp_usque_startup_timeout }}" min="5" max="120" step="1" inputmode="numeric">
                        <span class="tip" style="font-size:11px; padding:4px;">默认 15。M 协议 usque 启动后等待端口就绪的总超时（原为 30 次 × 0.5 秒循环 = 15 秒）。</span>
                    </div>
                    <!-- 【B 级新增】dns_check_timeout -->
                    <div>
                        <label>DNS 预检超时 (秒)</label>
                        <input type="number" name="dns_check_timeout" value="{{ cfg.dns_check_timeout }}" min="1" max="60" step="1" inputmode="numeric">
                        <span class="tip" style="font-size:11px; padding:4px;">默认 5。生成 M 协议节点前检查 api.cloudflareclient.com DNS 解析的超时。</span>
                    </div>
                    <!-- 【B 级新增 + R273 修复】warp_reg_request_timeout -->
                    <div>
                        <label>WARP 注册请求超时 (秒)</label>
                        <input type="number" name="warp_reg_request_timeout" value="{{ cfg.warp_reg_request_timeout }}" min="5" max="60" step="1" inputmode="numeric">
                        <!-- 【R273 修复】默认值 10 → 15，tip 更新 -->
                        <span class="tip" style="font-size:11px; padding:4px; background:rgba(33,150,243,0.08); border-left-color:var(--blue);">默认 15。生成 WARP 节点时设备注册请求的超时。W 协议原 15 秒、临时节点原 10 秒，现已统一为 15 秒（临时节点会多等 5 秒，但成功概率更高，更稳定）。</span>
                    </div>
                </div>
            </div>

        </details>

        <details>
            <summary>🔒 本地访问密码 / CDN / 代理地址</summary>
            <div style="margin-top:15px;">
                <label>本地访问密码 (留空则不启用):</label><input type="text" name="local_pwd" value="{{ local_pwd }}">
                <span class="tip">若忘记密码，可在 Termux 修改 config.json。</span>
                <label>自定义 CDN 前缀:</label><input type="text" name="cdn_prefix" value="{{ cdn_prefix }}" placeholder="例如: https://my-proxy.com/">
                <label>CLI 代理地址 (节点):</label><input type="text" name="proxy" value="{{ proxy }}" placeholder="例如: socks5://127.0.0.1:1080">
            </div>
        </details>

        <button type="submit" class="btn btn-blue" style="padding:15px; margin-top:15px;">💾 保存所有设置</button>
    </form>

    <fieldset style="margin-top:20px;"><legend>🧹 临时文件管理</legend><p style="font-size:14px;">临时目录: <code>{{ temp_dir }}</code><br>占用: <b>{{ temp_size }}</b>（{{ temp_count }} 个）</p><form method="POST" action="/settings/clean_temp" onsubmit="return confirm('⚠️ 警告：清理临时文件会导致正在等待上传、追加或失败中断的任务无法断点续传！确定要强制清空吗？');"><button type="submit" class="btn btn-red">🧹 强制清理所有临时文件</button></form></fieldset>
    <fieldset style="margin-top:20px;"><legend>💾 一键备份与恢复</legend><a href="/backup/download" class="btn btn-green" style="margin-bottom:10px;">⬇️ 导出备份 (.zip)</a><form method="POST" action="/backup/upload" enctype="multipart/form-data" onsubmit="return confirm('恢复将覆盖当前数据库，确定继续？');"><input type="file" name="file" accept=".zip" required style="margin-bottom:10px;"><button type="submit" class="btn btn-orange">⬆️ 导入备份 (.zip)</button></form></fieldset>
    
    <fieldset style="margin-top:20px;">
        <legend>📝 实时系统日志</legend>
        <div style="display:flex; justify-content:flex-start; gap:10px; margin-bottom:10px;">
            <button type="button" class="btn btn-sm btn-gray" onclick="fetchLogs()" style="margin:0;">🔄 刷新日志</button>
            <button type="button" class="btn btn-sm btn-blue" onclick="copyLogs()" style="margin:0;">📋 复制日志</button>
        </div>
        <div style="background:rgba(0,0,0,0.1); border-radius:8px; padding:10px; height:200px; overflow-y:auto; font-family:monospace; font-size:12px;" id="log-box">加载中...</div>
    </fieldset>
</div>
"""