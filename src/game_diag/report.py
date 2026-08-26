from __future__ import annotations

import html
import json
from datetime import UTC, datetime
from typing import Any


def _safe_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")


def _iso(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, tz=UTC).isoformat(timespec="milliseconds")


def _readable_local(ts_ms: int) -> str:
    local_time = datetime.fromtimestamp(ts_ms / 1000, tz=UTC).astimezone()
    offset = local_time.strftime("%z")
    readable_offset = f"UTC{offset[:3]}:{offset[3:]}" if offset else "本地时间"
    return f"{local_time:%Y年%m月%d日 %H:%M:%S}（{readable_offset}）"


def build_feedback_html(
    manifest: dict[str, Any],
    selection: dict[str, Any],
    summary: dict[str, Any],
    metrics: list[dict[str, Any]],
    events: list[dict[str, Any]],
    source_status: dict[str, Any],
    chart_available: bool,
) -> str:
    synthetic = bool(manifest.get("synthetic"))
    banner_html = (
        '<div class="banner">合成测试报告：性能与日志不是真实游戏采集数据。</div>'
        if synthetic
        else ""
    )
    process = manifest.get("process") or {}
    environment = manifest.get("environment") or {}
    sources = source_status.get("sources") or {}
    rows = []
    for name, value in sorted(sources.items()):
        status = html.escape(str(value.get("status", "unavailable")))
        reason = html.escape(str(value.get("reason") or "—"))
        coverage = value.get("coverage") or {}
        coverage_text = "—"
        if coverage.get("start_utc_ms") is not None:
            coverage_text = f"{_iso(coverage['start_utc_ms'])} ～ {_iso(coverage['end_utc_ms'])}"
        rows.append(
            f"<tr><td>{html.escape(name)}</td><td><span class='state {status}'>{status.upper()}</span></td>"
            f"<td>{html.escape(coverage_text)}</td><td>{reason}</td></tr>"
        )
    summary_cards = []
    preferred = (
        ("app_fps", "平均 Present FPS", "average"),
        ("frame_time_p95_ms", "应用帧时间 P95", "maximum"),
        ("process_cpu_pct", "CPU 峰值", "maximum"),
        ("process_memory_mb", "内存峰值", "maximum"),
        ("process_gpu_pct", "进程 GPU 峰值", "maximum"),
    )
    for metric, label, key in preferred:
        item = (summary.get("metrics") or {}).get(metric)
        if not item:
            continue
        value = item.get(key)
        if value is None:
            continue
        summary_cards.append(
            f"<article><small>{html.escape(label)}</small><strong>{value:.2f}</strong>"
            f"<span>{html.escape(str(item.get('unit') or ''))}</span></article>"
        )
    metrics_json = _safe_json(metrics)
    events_json = _safe_json(events)
    selection_json = _safe_json(selection)
    chart_script = '<script src="assets/chart.umd.min.js"></script>' if chart_available else ""
    background_seconds = int(selection.get("background_seconds_excluded") or 0)
    suspicious_gaps = int(selection.get("suspicious_frame_gap_count") or 0)
    if background_seconds:
        capture_notice = (
            f'<p class="capture-note good">所选时间段包含 {background_seconds} 秒游戏非前台数据；'
            "这些秒已从摘要和主曲线排除，原始 metrics.csv 仍完整保留。</p>"
        )
    elif not selection.get("foreground_state_available") and suspicious_gaps:
        capture_notice = (
            f'<p class="capture-note warn">该旧会话没有前台状态记录，并检测到 {suspicious_gaps} 个超过 1 秒的极端帧间隔；'
            "它们可能来自切后台或暂停，当前保留原值，但不应直接作为游戏内卡顿结论。</p>"
        )
    else:
        capture_notice = ""
    fps_notice = (
        '<p class="capture-note">“Present FPS”表示应用向显示系统提交帧的速率。'
        "关闭垂直同步或允许撕裂时，该值可以高于显示器刷新率，不应将其强制封顶。</p>"
    )
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>缺陷现场性能与日志证据</title>
<style>
:root{{--bg:#090909;--panel:#171512;--line:#40392f;--ink:#e7dfd2;--muted:#a9a197;--gold:#bca06c;--red:#b44b40;--green:#789574;--violet:#9b87b6}}*{{box-sizing:border-box}}body{{margin:0;background:linear-gradient(145deg,#090909,#12100e);color:var(--ink);font:14px/1.55 -apple-system,BlinkMacSystemFont,"Microsoft YaHei",sans-serif}}header,main{{width:min(1200px,calc(100% - 28px));margin:auto}}header{{padding:32px 0 20px}}h1{{margin:0;font-size:30px}}h2{{margin:0 0 14px;color:#d8cdbd}}p{{margin:6px 0;color:var(--muted)}}.banner{{margin-top:16px;padding:12px 14px;border-left:4px solid var(--violet);background:#211d18}}.tabs{{position:sticky;top:0;z-index:5;display:flex;gap:8px;padding:10px 0;background:#0d0c0b}}button{{border:1px solid #5a4d3b;background:#191613;color:#cfc5b6;padding:8px 14px;cursor:pointer}}button.active{{background:#4a3728;color:#fff}}section.view{{display:none}}section.view.active{{display:block}}.panel{{margin:14px 0;padding:20px;border:1px solid var(--line);background:rgba(23,21,18,.96)}}.grid{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}}.feedback-grid{{grid-template-columns:repeat(2,minmax(0,1fr))}}.card{{padding:15px;border:1px solid #39332b;background:#12110f}}.card small{{display:block;color:var(--gold)}}.card strong{{display:block;margin-top:7px;font-size:16px}}.summary{{display:flex;flex-wrap:wrap;gap:10px}}.summary article{{min-width:150px;padding:13px;border:1px solid #42392d;background:#11100e}}.summary small,.summary span{{display:block;color:var(--muted)}}.summary strong{{font-size:24px;color:#e0d5c5}}.chart{{height:280px;margin:12px 0 22px}}canvas{{max-height:280px}}table{{width:100%;border-collapse:collapse}}th,td{{padding:9px;border-bottom:1px solid #38332d;text-align:left;vertical-align:top}}th{{color:var(--gold)}}.state{{display:inline-block;padding:2px 7px;border:1px solid currentColor;font-size:11px}}.state.ok{{color:#84a77f}}.state.degraded{{color:#c3a15e}}.state.unavailable{{color:#c46a60}}code{{color:#d6b87c}}a{{color:#d6b87c}}.log-error{{color:#e88478}}.log-warning{{color:#d9b56d}}.empty{{padding:15px;border:1px dashed #594b3a;color:var(--muted)}}.capture-note{{padding:10px 12px;border-left:3px solid var(--gold);background:#211d18}}.capture-note.good{{border-color:var(--green)}}.capture-note.warn{{border-color:var(--red)}}details.evidence-note{{margin-top:14px;border-top:1px solid #38332d;padding-top:12px}}details.evidence-note summary{{color:var(--gold);cursor:pointer}}.file-notes{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px 18px;margin-top:12px}}.file-notes p{{margin:0}}.file-notes code{{display:block}}.log-help{{padding:10px 12px;border-left:3px solid var(--gold);background:#211d18}}.log-tools{{display:flex;flex-wrap:wrap;gap:7px;margin:12px 0}}.log-tools button{{padding:5px 10px}}.count{{white-space:nowrap;color:var(--muted)}}@media(max-width:760px){{.grid,.feedback-grid,.file-notes{{grid-template-columns:1fr}}.panel{{padding:14px}}}}
</style>{chart_script}</head><body>
<header><h1>缺陷现场性能与日志证据</h1><p>Submission ID：<code>{html.escape(selection['submission_id'])}</code></p>{banner_html}</header>
<main><div class="tabs"><button class="active" data-tab="qa">QA 完整性视图</button><button data-tab="dev">研发诊断视图</button></div>
<section class="view active" id="qa"><div class="panel"><h2>缺陷反馈</h2><div class="grid feedback-grid">
<article class="card"><small>问题描述</small><strong>场景切换后出现明显卡顿</strong></article>
<article class="card"><small>复现方式</small><strong>进入复杂场景后快速移动视角</strong></article>
<article class="card"><small>预期结果</small><strong>场景切换和视角移动过程保持流畅</strong></article>
<article class="card"><small>用户上报问题发生时间段</small><strong>开始：{_readable_local(selection['selected_start_utc_ms'])}<br>结束：{_readable_local(selection['selected_end_utc_ms'])}</strong></article>
</div></div>
<div class="panel"><h2>环境与覆盖</h2><div class="grid"><article class="card"><small>目标进程</small><strong>{html.escape(str(process.get('name') or '—'))}</strong><p>PID：{html.escape(str(process.get('pid') or '—'))}</p></article><article class="card"><small>会话</small><strong>{html.escape(str(manifest.get('session_id')))}</strong><p>{_iso(manifest['started_utc_ms'])}</p></article><article class="card"><small>主机</small><strong>{html.escape(str(environment.get('processor') or environment.get('platform') or '—'))}</strong><p>逻辑核心：{html.escape(str(environment.get('logical_cpus') or '—'))}</p></article></div></div>
<div class="panel"><h2>数据源完整性</h2><table><thead><tr><th>数据源</th><th>状态</th><th>覆盖</th><th>说明</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div>
</section>
<section class="view" id="dev"><div class="panel"><h2>原始证据</h2><p><a href="metrics.csv">metrics.csv</a> · <a href="events.jsonl">events.jsonl</a> · <a href="game-log.txt">game-log.txt</a> · <a href="source-status.json">source-status.json</a> · <a href="selection.json">selection.json</a></p><details class="evidence-note"><summary>这五个文件分别是什么？</summary><div class="file-notes"><p><code>metrics.csv</code>问题时间段及上下文内的性能采样，可供 Excel、Python 等工具分析。</p><p><code>events.jsonl</code>从游戏日志解析出的逐条结构化事件；页面合并显示不会改变此原始文件。</p><p><code>game-log.txt</code>证据范围内截取并脱敏后的可读游戏日志原文。</p><p><code>source-status.json</code>各采集来源的可用状态、覆盖时间、样本数及异常原因。</p><p><code>selection.json</code>用户上报时间段、实际导出范围、上下文和提交标识。</p></div></details></div>
<div class="panel"><h2>区间摘要</h2>{capture_notice}{fps_notice}<div class="summary">{''.join(summary_cards) or '<div class="empty">所选区间没有可汇总的性能指标。</div>'}</div></div>
<div class="panel"><h2>统一性能时间轴</h2><div id="chart-warning" class="empty" style="display:none">Chart.js 离线资源不可用；原始指标仍可从 metrics.csv 查看。</div><div class="chart"><canvas id="chart-fps"></canvas></div><div class="chart"><canvas id="chart-frame"></canvas></div><div class="chart"><canvas id="chart-load"></canvas></div><div class="chart"><canvas id="chart-memory"></canvas></div></div>
<div class="panel"><h2>日志事件</h2><p class="log-help">这里显示的是游戏原始日志标注的级别，不代表采集工具出错，也不等于已经确认是缺陷根因。相同事件已合并，逐条记录仍完整保留在 events.jsonl 中。</p><div class="log-tools"><button class="active" data-log-level="ALL">全部</button><button data-log-level="ERROR">ERROR</button><button data-log-level="WARNING">WARNING</button><button data-log-level="INFO">INFO</button></div><table><thead><tr><th>首次出现</th><th>最后出现</th><th>级别</th><th>模块</th><th>日志内容</th><th>次数</th></tr></thead><tbody id="logs"></tbody></table></div></section></main>
<script>
const METRICS={metrics_json};const EVENTS={events_json};const SELECTION={selection_json};
document.querySelectorAll('[data-tab]').forEach(b=>b.onclick=()=>{{document.querySelectorAll('[data-tab]').forEach(x=>x.classList.toggle('active',x===b));document.querySelectorAll('.view').forEach(x=>x.classList.toggle('active',x.id===b.dataset.tab));}});
function escapeHtml(v){{const d=document.createElement('div');d.textContent=v??'';return d.innerHTML;}}
function eventText(v){{return String(v??'').replace(/^\\[[^\\]]+\\]\\[[^\\]]+\\]\\s*/, '');}}
function localTime(v){{return new Date(v).toLocaleString('zh-CN',{{hour12:false}});}}
const eventGroups=new Map();EVENTS.forEach(e=>{{const content=eventText(e.message);const key=[e.level,e.category,content].join('\u001f');const old=eventGroups.get(key);if(old){{old.first=Math.min(old.first,e.ts_utc_ms);old.last=Math.max(old.last,e.ts_utc_ms);old.count+=1;}}else{{eventGroups.set(key,{{level:e.level,category:e.category,content,first:e.ts_utc_ms,last:e.ts_utc_ms,count:1}});}}}});const groupedEvents=[...eventGroups.values()].sort((a,b)=>a.first-b.first);const levelCounts=EVENTS.reduce((v,e)=>{{v[e.level]=(v[e.level]||0)+1;return v;}},{{}});document.querySelectorAll('[data-log-level]').forEach(b=>{{const level=b.dataset.logLevel;b.textContent=level==='ALL'?`全部（${{EVENTS.length}}）`:`${{level}}（${{levelCounts[level]||0}}）`;b.onclick=()=>{{document.querySelectorAll('[data-log-level]').forEach(x=>x.classList.toggle('active',x===b));renderLogs(level);}};}});
const logs=document.getElementById('logs');function renderLogs(level='ALL'){{const visible=groupedEvents.filter(e=>level==='ALL'||e.level===level);logs.innerHTML=visible.map(e=>{{const cls=e.level==='ERROR'?'log-error':e.level==='WARNING'?'log-warning':'';return `<tr><td>${{localTime(e.first)}}</td><td>${{localTime(e.last)}}</td><td class="${{cls}}">${{escapeHtml(e.level)}}</td><td>${{escapeHtml(e.category)}}</td><td>${{escapeHtml(e.content)}}</td><td class="count">${{e.count}} 次</td></tr>`;}}).join('')||'<tr><td colspan="6">当前筛选条件下没有日志事件。</td></tr>';}}renderLogs();
function points(name){{return METRICS.filter(x=>x.metric===name&&x.value!==null).map(x=>({{x:x.ts_utc_ms,y:x.value}}));}}
const band={{id:'selectionBand',beforeDatasetsDraw(chart){{const x=chart.scales.x;if(!x)return;const a=x.getPixelForValue(SELECTION.selected_start_utc_ms),b=x.getPixelForValue(SELECTION.selected_end_utc_ms);chart.ctx.save();chart.ctx.fillStyle='rgba(180,75,64,.16)';chart.ctx.fillRect(a,chart.chartArea.top,b-a,chart.chartArea.bottom-chart.chartArea.top);chart.ctx.restore();}}}};
function makeChart(id,datasets,title){{if(typeof Chart==='undefined'){{document.getElementById('chart-warning').style.display='block';return}}new Chart(document.getElementById(id),{{type:'line',data:{{datasets:datasets.map(d=>({{label:d[1],data:points(d[0]),borderColor:d[2],borderWidth:1.6,pointRadius:0,spanGaps:false}}))}},plugins:[band],options:{{responsive:true,maintainAspectRatio:false,animation:false,parsing:false,interaction:{{mode:'nearest',intersect:false}},plugins:{{title:{{display:true,text:title,color:'#d8cdbd'}},legend:{{labels:{{color:'#aaa'}}}}}},scales:{{x:{{type:'linear',ticks:{{color:'#999',callback:v=>new Date(v).toLocaleTimeString()}},grid:{{color:'#292622'}}}},y:{{ticks:{{color:'#999'}},grid:{{color:'#292622'}}}}}}}}}})}}
makeChart('chart-fps',[["app_fps","Present FPS","#7fa276"]],"Present FPS");
makeChart('chart-frame',[["frame_time_p95_ms","应用帧时间 P95 ms","#c45b4f"],["gpu_busy_p95_ms","GPU Busy P95 ms","#a58fce"]],"帧时间");
makeChart('chart-load',[["process_cpu_pct","进程 CPU %","#d2a85b"],["process_gpu_pct","进程 GPU %","#7a9fc6"]],"进程负载");
makeChart('chart-memory',[["process_memory_mb","进程内存 MiB","#b98a74"]],"内存");
</script></body></html>"""
