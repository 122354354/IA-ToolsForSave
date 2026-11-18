# pages_queue_style.py
# 队列页专用 <style> 片段

QUEUE_STYLE = r"""<style>
    .raw-line { 
        font-family: monospace; font-size: 12px; color: var(--gray); 
        background: rgba(0,0,0,0.05); padding: 8px; border-radius: 4px; 
        margin-top: 8px; white-space: pre-wrap; word-break: break-all; 
        height: 160px; overflow-y: auto; border-left: 3px solid var(--gray); 
        width: 100%; box-sizing: border-box; line-height: 1.4;
    }
    .modal-full-log {
        flex: 1; width: 100%; height: 100%; overflow-y: auto; margin-top: 10px;
        font-family: monospace; font-size: 13px; background: var(--bg); color: var(--text);
        padding: 10px; border-radius: 8px; border: 1px solid var(--border); white-space: pre-wrap; word-break: break-all;
    }
    #speedChart { width: 100% !important; height: 100px !important; display: block; }
    
    .sub-task-list { margin-top: 10px; background: rgba(0,0,0,0.02); border-radius: 8px; padding: 8px; max-height: 300px; overflow-y: auto; border: 1px solid var(--border); -webkit-overflow-scrolling: touch; }
    .sub-task-item { display: flex; flex-direction: column; padding: 10px 8px; border-bottom: 1px solid var(--border); gap: 5px; }
    .sub-task-item:last-child { border-bottom: none; }
    
    .sub-task-info { display: flex; flex-direction: column; width: 100%; gap: 6px; }
    .sub-task-filename { word-break: break-all; word-wrap: break-word; font-size: 13px; width: 100%; text-align: left; }
    .sub-task-status-row { display: flex; justify-content: space-between; align-items: center; width: 100%; flex-wrap: wrap; gap: 5px; }
    
    .sub-task-size { color: var(--gray); font-size: 12px; }
    .sub-progress-bar { width: 100%; height: 6px; background: rgba(0,0,0,0.1); border-radius: 3px; overflow: hidden; margin-top: 5px; position: relative; }
    .sub-progress-fill { height: 100%; background: linear-gradient(90deg, #009688 0%, #4CAF50 100%); width: 0%; transition: width 0.3s; }
    .sub-speed-badge { font-size: 11px; color: var(--primary-dark); background: rgba(0,150,136,0.1); padding: 1px 6px; border-radius: 4px; font-weight: bold; margin-left: 5px; }
    
    .virtual-badge { display: inline-block; padding: 2px 8px; border-radius: 12px; font-size: 12px; font-weight: bold; color: white; background: linear-gradient(90deg, #2196F3 0%, #03A9F4 100%); margin-left: 10px; }
</style>
"""