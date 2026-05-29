#!/usr/bin/env python3
"""
Hermes Token Dashboard — Real-time token consumption monitor.
Reads directly from Hermes state.db (SQLite).
"""
import json
import os
import sqlite3
import time
from datetime import datetime, timedelta
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

DB_PATH = os.path.expanduser("~/.hermes/state.db")
CACHE_TTL = 15
_cache = {}


def get_data(days=7):
    global _cache
    days = int(days)
    if days < 0:
        days = 0
    now_t = time.time()
    cache_key = days
    cached = _cache.get(cache_key)
    if cached and (now_t - cached["time"]) < CACHE_TTL:
        return cached["data"]

    # Return empty data if database doesn't exist
    if not os.path.exists(DB_PATH):
        return {
            "total_tokens": 0, "total_input": 0, "total_output": 0,
            "total_cache_read": 0, "total_sessions": 0, "total_messages": 0,
            "tool_calls": 0, "historical_tokens": 0, "days": [],
            "models": [], "hourly": [], "platforms": [], "providers": [],
            "model_provider": [], "range_label": "无数据", "range_start": "", "range_end": "",
            "updated_at": datetime.now().isoformat(), "error": f"数据库不存在: {DB_PATH}"
        }

    def provider_label(base_url):
        if not base_url:
            return "未知"
        base_url = base_url.rstrip("/")
        if "deepseek" in base_url:
            return "DeepSeek"
        if "openrouter" in base_url:
            return "OpenRouter"
        if "volces" in base_url:
            return "火山引擎"
        if "ollama" in base_url:
            return "Ollama"
        if "xiaomimimo" in base_url:
            return "xiaomimimo"
        if "192.168" in base_url:
            return "本地中转站"
        return base_url

    import re
    def model_label(model_str):
        model_str = model_str or "unknown"
        # Strip OpenRouter namespace prefix for cleaner display
        if "/" in model_str and not model_str.startswith("{"):
            model_str = model_str.split("/", 1)[-1]
        # Normalize: gpt → GPT
        model_str = re.sub(r'\bgpt\b', 'GPT', model_str, flags=re.IGNORECASE)
        # Friendly names
        _names = {"deepseek-v4-pro": "DeepSeek-v4-Pro"}
        if model_str in _names:
            return _names[model_str]
        try:
            model_obj = json.loads(model_str)
            return model_obj.get("default", model_obj.get("name", model_str[:30]))
        except Exception:
            return model_str[:30]


    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        # Check if sessions table exists
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='sessions'")
        if not cur.fetchone():
            conn.close()
            return {
                "total_tokens": 0, "total_input": 0, "total_output": 0,
                "total_cache_read": 0, "total_sessions": 0, "total_messages": 0,
                "tool_calls": 0, "historical_tokens": 0, "days": [],
                "models": [], "hourly": [], "platforms": [], "providers": [],
                "model_provider": [], "range_label": "无数据", "range_start": "", "range_end": "",
                "updated_at": datetime.now().isoformat(), "error": "sessions 表不存在"
            }
    except Exception as e:
        return {
            "total_tokens": 0, "total_input": 0, "total_output": 0,
            "total_cache_read": 0, "total_sessions": 0, "total_messages": 0,
            "tool_calls": 0, "historical_tokens": 0, "days": [],
            "models": [], "hourly": [], "platforms": [], "providers": [],
            "model_provider": [], "range_label": "错误", "range_start": "", "range_end": "",
            "updated_at": datetime.now().isoformat(), "error": str(e)
        }

    now = datetime.now()
    end = now.replace(hour=23, minute=59, second=59, microsecond=999999)
    all_time = (days == 0)
    session_day_expr = "date(s.started_at, 'unixepoch', 'localtime')"
    session_hour_expr = "CAST(strftime('%H', s.started_at, 'unixepoch', 'localtime') AS INTEGER)"

    if all_time:
        # Weekly aggregation — session tokens attach to their start day
        cur.execute(
            f"""
            SELECT {session_day_expr} as day,
                   SUM(s.input_tokens + s.output_tokens + COALESCE(s.cache_read_tokens, 0)) as tokens,
                   COUNT(*) as sessions
            FROM sessions s
            GROUP BY day ORDER BY day
        """
        )
        rows = cur.fetchall()
        cutoff = 0
        # Group into weeks (Mon-Sun)
        weekly = {}
        for row in rows:
            dt = datetime.strptime(row["day"], "%Y-%m-%d")
            ws = dt - timedelta(days=dt.weekday())
            wk = ws.strftime("%Y-%m-%d")
            if wk not in weekly:
                weekly[wk] = {"tokens": 0, "sessions": 0}
            weekly[wk]["tokens"] += row["tokens"] or 0
            weekly[wk]["sessions"] += row["sessions"] or 0

        days_list = []
        for wk in sorted(weekly):
            ws_date = datetime.strptime(wk, "%Y-%m-%d")
            we = ws_date + timedelta(days=6)
            we_str = we.strftime("%Y-%m-%d")
            # Skip future weeks that haven't started yet
            if ws_date > now:
                continue
            wkd = wk[5:]
            wed = we_str[5:]
            # Prepend year if cross-year boundary
            label = f"{wkd}~{wed}"
            if wk[:4] != we_str[:4]:
                label = f"{wk[:2]}{wkd}~{we_str[:2]}{wed}"
            days_list.append({
                "date": wk,
                "end_date": we_str,
                "tokens": weekly[wk]["tokens"],
                "sessions": weekly[wk]["sessions"],
                "label": label,
            })

        if days_list:
            start = datetime.strptime(days_list[0]["date"], "%Y-%m-%d")
        else:
            start = now - timedelta(days=6)
        range_label = "历史全部"
    else:
        start = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days - 1)
        cutoff = start.timestamp()

        # Daily tokens — session tokens attach to their start day
        cur.execute(
            f"""
            SELECT {session_day_expr} as day,
                   SUM(s.input_tokens + s.output_tokens + COALESCE(s.cache_read_tokens, 0)) as tokens,
                   COUNT(*) as sessions
            FROM sessions s
            WHERE s.started_at >= ?
            GROUP BY day ORDER BY day
        """,
            (cutoff,),
        )

        daily_data = {}
        for row in cur.fetchall():
            daily_data[row["day"]] = {
                "tokens": row["tokens"] or 0,
                "sessions": row["sessions"] or 0,
            }

        span = days
        days_list = []
        for i in range(span):
            d = (start + timedelta(days=i)).strftime("%Y-%m-%d")
            dd = daily_data.get(d, {"tokens": 0, "sessions": 0})
            days_list.append(
                {
                    "date": d,
                    "tokens": dd["tokens"],
                    "sessions": dd["sessions"],
                    "label": d[5:],
                }
            )
        range_label = f"最近 {days} 天"

    # Model breakdown (include cache_read)
    # Session-start boundary keeps models aligned with totals and charts
    cur.execute(
        f"""
        SELECT model, SUM(input_tokens + output_tokens + COALESCE(cache_read_tokens, 0)) as tokens, COUNT(*) as cnt
        FROM sessions s
        WHERE (s.started_at >= ? OR ? = 0) AND (s.input_tokens > 0 OR s.output_tokens > 0)
        GROUP BY s.model ORDER BY tokens DESC LIMIT 10
    """,
        (cutoff, cutoff),
    )

    models = []
    for row in cur.fetchall():
        name = model_label(row["model"])
        models.append({"name": name, "tokens": row["tokens"] or 0})

    # Hourly activity — count messages per hour (not sessions)
    msg_hour_expr = "CAST(strftime('%H', m.timestamp, 'unixepoch', 'localtime') AS INTEGER)"
    cur.execute(
        f"""
        SELECT {msg_hour_expr} as hour,
               COUNT(*) as cnt
        FROM messages m
        WHERE (m.timestamp >= ? OR ? = 0)
        GROUP BY hour ORDER BY hour
    """,
        (cutoff, cutoff),
    )

    hour_map = {row["hour"]: row["cnt"] or 0 for row in cur.fetchall()}
    hourly = [{"hour": f"{h:02d}:00", "count": hour_map.get(h, 0)} for h in range(24)]

    # Totals (include cache_read)
    # Session-start boundary ensures totals align with charts and breakdowns
    cur.execute(
        """
        SELECT SUM(s.input_tokens) as inp, SUM(s.output_tokens) as out,
               SUM(COALESCE(s.cache_read_tokens, 0)) as cache_read,
               COUNT(*) as cnt,
               SUM(s.message_count) as msgs,
               SUM(s.tool_call_count) as tools
        FROM sessions s
        WHERE (s.started_at >= ? OR ? = 0)
    """,
        (cutoff, cutoff),
    )
    totals = cur.fetchone()

    # Platform breakdown (include cache_read)
    # Session-start boundary keeps platform attribution aligned with totals
    cur.execute(
        f"""
        SELECT source, SUM(input_tokens + output_tokens + COALESCE(cache_read_tokens, 0)) as tokens, COUNT(*) as cnt
        FROM sessions s
        WHERE (s.started_at >= ? OR ? = 0) AND source IS NOT NULL
        GROUP BY source ORDER BY tokens DESC
    """,
        (cutoff, cutoff),
    )
    platforms = [{"name": r["source"] or "unknown", "tokens": r["tokens"] or 0} for r in cur.fetchall()]

    # API Provider breakdown (by normalized billing_base_url — shows actual API endpoint, not model name)
    # Session-start boundary ensures provider totals match overview totals
    cur.execute(
        f"""
        SELECT rtrim(billing_base_url, '/') as billing_base_url_norm,
               SUM(input_tokens + output_tokens + COALESCE(cache_read_tokens, 0)) as tokens,
               COUNT(*) as cnt
        FROM sessions s
        WHERE (s.started_at >= ? OR ? = 0) AND (s.input_tokens > 0 OR s.output_tokens > 0)
        GROUP BY rtrim(s.billing_base_url, '/') ORDER BY tokens DESC
    """,
        (cutoff, cutoff),
    )
    providers = [{"name": provider_label(r["billing_base_url_norm"]), "tokens": r["tokens"] or 0} for r in cur.fetchall()]

    # Model x Provider breakdown (joint view)
    # Session-start boundary keeps joint view aligned with totals and providers
    cur.execute(
        f"""
        SELECT model, rtrim(billing_base_url, '/') as billing_base_url_norm,
               SUM(input_tokens + output_tokens + COALESCE(cache_read_tokens, 0)) as tokens
        FROM sessions s
        WHERE (s.started_at >= ? OR ? = 0) AND (s.input_tokens > 0 OR s.output_tokens > 0)
        GROUP BY s.model, rtrim(s.billing_base_url, '/')
        ORDER BY tokens DESC
        LIMIT 14
    """,
        (cutoff, cutoff),
    )
    model_provider = [
        {
            "name": f"{model_label(r['model'])} @ {provider_label(r['billing_base_url_norm'])}",
            "tokens": r["tokens"] or 0,
        }
        for r in cur.fetchall()
    ]

    # Historical total tokens
    cur.execute(
        """
        SELECT SUM(input_tokens + output_tokens + COALESCE(cache_read_tokens, 0)) as tokens
        FROM sessions
    """
    )
    historical_tokens = cur.fetchone()["tokens"] or 0

    conn.close()

    data = {
        "total_tokens": (totals["inp"] or 0) + (totals["out"] or 0) + (totals["cache_read"] or 0),
        "total_input": totals["inp"] or 0,
        "total_output": totals["out"] or 0,
        "total_cache_read": totals["cache_read"] or 0,
        "total_sessions": totals["cnt"] or 0,
        "total_messages": totals["msgs"] or 0,
        "tool_calls": totals["tools"] or 0,
        "historical_tokens": historical_tokens,
        "days": days_list,
        "models": models,
        "hourly": hourly,
        "platforms": platforms,
        "providers": providers,
        "model_provider": model_provider,
        "range_label": range_label,
        "range_start": start.strftime("%Y-%m-%d"),
        "range_end": end.strftime("%Y-%m-%d"),
        "updated_at": datetime.now().isoformat(),
    }

    _cache[cache_key] = {"data": data, "time": time.time()}
    return data


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = urlparse(self.path).path
        params = parse_qs(urlparse(self.path).query)

        if path == "/api/data":
            days = int(params.get("days", [7])[0])
            data = get_data(days)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps(data, ensure_ascii=False).encode())

        elif path == "/" or path == "/index.html":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(HTML_PAGE.encode())

        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass


class ReuseThreadingHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True


HTML_PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Hermes Token Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
:root {
  --bg-0: #07111f;
  --bg-1: #0b1730;
  --bg-2: rgba(14, 26, 52, 0.72);
  --glass: rgba(255, 255, 255, 0.09);
  --glass-strong: rgba(255, 255, 255, 0.14);
  --glass-border: rgba(255, 255, 255, 0.18);
  --text: #eef4ff;
  --muted: #9eb0d1;
  --soft: #7183a6;
  --blue: #7cc8ff;
  --cyan: #72f2ff;
  --purple: #a58bff;
  --green: #7af7b5;
  --orange: #ffb36b;
  --red: #ff7f96;
  --shadow: 0 24px 80px rgba(3, 8, 20, 0.45);
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif;
  color: var(--text);
  min-height: 100vh;
  padding: 34px 22px 40px;
  background:
    radial-gradient(circle at top left, rgba(124, 200, 255, 0.18), transparent 28%),
    radial-gradient(circle at 85% 12%, rgba(165, 139, 255, 0.22), transparent 25%),
    radial-gradient(circle at 50% 100%, rgba(114, 242, 255, 0.16), transparent 35%),
    linear-gradient(180deg, #08101d 0%, #0b1630 52%, #091220 100%);
}
body::before,
body::after {
  content: '';
  position: fixed;
  inset: auto;
  width: 320px;
  height: 320px;
  border-radius: 50%;
  filter: blur(70px);
  z-index: 0;
  pointer-events: none;
}
body::before {
  top: -80px;
  right: -40px;
  background: rgba(124, 200, 255, 0.14);
}
body::after {
  bottom: -90px;
  left: -20px;
  background: rgba(165, 139, 255, 0.12);
}
.container {
  position: relative;
  z-index: 1;
  width: min(100%, 1680px);
  margin: 0 auto;
}
.topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
  margin-bottom: 16px;
  flex-wrap: wrap;
}
.topbar-left {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}
.topbar-title {
  font-size: clamp(18px, 2.2vw, 26px);
  font-weight: 700;
  letter-spacing: -0.04em;
}
.toolbar {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 10px;
  flex-wrap: wrap;
}
.segmented {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px;
  border-radius: 18px;
  border: 1px solid rgba(255,255,255,0.12);
  background: linear-gradient(180deg, rgba(255,255,255,0.11), rgba(255,255,255,0.05));
  box-shadow: var(--shadow);
  backdrop-filter: blur(20px) saturate(140%);
  -webkit-backdrop-filter: blur(20px) saturate(140%);
}
.seg-btn {
  border: 0;
  background: transparent;
  color: var(--muted);
  padding: 10px 14px;
  border-radius: 12px;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  transition: all .18s ease;
}
.seg-btn:hover {
  color: var(--text);
  background: rgba(255,255,255,0.06);
}
.seg-btn.active {
  color: #07111f;
  background: linear-gradient(135deg, #9fe7ff, #7af7b5);
  box-shadow: 0 10px 30px rgba(114,242,255,0.22);
}
.hidden-select { display: none; }
.range-box {
  display: inline-flex;
  align-items: center;
  gap: 10px;
  padding: 10px 14px;
  border-radius: 16px;
  border: 1px solid rgba(255,255,255,0.1);
  background: rgba(255,255,255,0.05);
}
.range-box .big {
  font-size: 14px;
  font-weight: 700;
  letter-spacing: -0.02em;
  margin: 0;
}
.range-box .small {
  color: var(--muted);
  font-size: 12px;
}
.stats-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
  gap: 14px;
  margin-bottom: 16px;
}
.stat-card, .chart-panel {
  position: relative;
  overflow: hidden;
  background: linear-gradient(180deg, rgba(255,255,255,0.11), rgba(255,255,255,0.05));
  border: 1px solid rgba(255,255,255,0.12);
  border-radius: 24px;
  box-shadow: var(--shadow);
  backdrop-filter: blur(20px) saturate(140%);
  -webkit-backdrop-filter: blur(20px) saturate(140%);
}
.stat-card::before, .chart-panel::before {
  content: '';
  position: absolute;
  inset: 0;
  background: linear-gradient(135deg, rgba(255,255,255,0.08), transparent 45%, rgba(114,242,255,0.05));
  pointer-events: none;
}
.stat-card {
  padding: 18px 18px 16px;
}
.stat-card .label {
  font-size: 12px;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--soft);
  margin-bottom: 10px;
}
.stat-card .value {
  font-size: 30px;
  line-height: 1;
  font-weight: 700;
  letter-spacing: -0.04em;
  margin-bottom: 8px;
}
.stat-card .sub {
  font-size: 13px;
  color: var(--muted);
}
.c-token { color: var(--blue); }
.c-session { color: var(--green); }
.c-input { color: var(--purple); }
.c-output { color: var(--orange); }
.c-cache { color: var(--cyan); }
.c-tool { color: var(--red); }
.c-msg { color: #b9ceff; }
.section-title {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 10px;
  margin-bottom: 14px;
}
.section-title h2 {
  font-size: 18px;
  letter-spacing: -0.03em;
}
.section-title span {
  font-size: 12px;
  color: var(--soft);
}
.charts {
  display: grid;
  grid-template-columns: minmax(0, 1.45fr) minmax(320px, 1fr);
  gap: 16px;
  margin-bottom: 16px;
}
.charts.single {
  grid-template-columns: 1fr;
}
.chart-panel {
  padding: 18px 18px 16px;
}
.chart-panel h3 {
  font-size: 14px;
  font-weight: 600;
  color: var(--text);
  margin-bottom: 6px;
  letter-spacing: -0.02em;
}
.chart-panel p.hint {
  font-size: 12px;
  color: var(--soft);
  margin-bottom: 14px;
}
.chart-panel canvas {
  width: 100%;
  min-height: 290px;
  max-height: 320px;
}
.meter-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.model-row {
  display: grid;
  grid-template-columns: 140px minmax(0, 1fr) 72px;
  gap: 10px;
  align-items: center;
}
.model-row .m-name {
  font-size: 13px;
  color: var(--muted);
  text-align: right;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.model-row .m-bar-wrap {
  position: relative;
  height: 12px;
  background: rgba(255,255,255,0.06);
  border: 1px solid rgba(255,255,255,0.08);
  border-radius: 999px;
  overflow: hidden;
}
.model-row .m-bar {
  height: 100%;
  border-radius: 999px;
  box-shadow: 0 0 18px rgba(255,255,255,0.16);
}
.model-row .m-count {
  font-size: 12px;
  color: var(--text);
  text-align: right;
}
.footer {
  margin-top: 12px;
  text-align: center;
  color: var(--soft);
  font-size: 12px;
  padding: 18px 0 8px;
}
.empty {
  text-align: center;
  color: var(--soft);
  padding: 46px 20px;
  font-size: 13px;
}
@media (max-width: 980px) {
  .charts { grid-template-columns: 1fr; }
  .topbar { align-items: flex-start; }
}
@media (max-width: 640px) {
  body { padding: 18px 12px 28px; }
  .stat-card, .chart-panel { border-radius: 20px; }
  .chart-panel, .stat-card { padding: 16px; }
  .model-row { grid-template-columns: 96px minmax(0, 1fr) 62px; }
  .model-row .m-name { font-size: 12px; }
  .stat-card .value { font-size: 24px; }
  .segmented { width: 100%; overflow-x: auto; }
  .toolbar { width: 100%; }
  .range-box { width: 100%; justify-content: space-between; }
}
</style>
</head>
<body>
<div class="container">
  <div class="topbar">
    <div class="topbar-left">
      <div class="topbar-title">Hermes Token Dashboard</div>
      <div class="range-box">
        <div class="big" id="rangeLabel">最近 7 天</div>
        <div class="small" id="rangeValue">—</div>
      </div>
    </div>
    <div class="toolbar">
      <div class="segmented" id="rangeControls">
        <button class="seg-btn" data-days="1" onclick="setDays(1)">1天</button>
        <button class="seg-btn" data-days="3" onclick="setDays(3)">3天</button>
        <button class="seg-btn active" data-days="7" onclick="setDays(7)">7天</button>
        <button class="seg-btn" data-days="14" onclick="setDays(14)">14天</button>
        <button class="seg-btn" data-days="30" onclick="setDays(30)">30天</button>
        <button class="seg-btn" data-days="0" onclick="setDays(0)">全部</button>
      </div>
      <select id="days" class="hidden-select" onchange="refresh()">
        <option value="1">最近 1 天</option>
        <option value="3">最近 3 天</option>
        <option value="7" selected>最近 7 天</option>
        <option value="14">最近 14 天</option>
        <option value="30">最近 30 天</option>
        <option value="0">历史全部</option>
      </select>
    </div>
  </div>

  <div class="stats-grid">
    <div class="stat-card">
      <div class="label">总 Token</div>
      <div class="value c-token" id="total">—</div>
      <div class="sub">历史累计 <span id="historical">—</span></div>
    </div>
    <div class="stat-card">
      <div class="label">输入 Token</div>
      <div class="value c-input" id="input">—</div>
      <div class="sub">Prompt 与上下文输入</div>
    </div>
    <div class="stat-card">
      <div class="label">输出 Token</div>
      <div class="value c-output" id="output">—</div>
      <div class="sub">模型生成结果</div>
    </div>
    <div class="stat-card">
      <div class="label">缓存命中</div>
      <div class="value c-cache" id="cacheRead">—</div>
      <div class="sub">计费口径已纳入</div>
    </div>
    <div class="stat-card">
      <div class="label">会话</div>
      <div class="value c-session" id="sessions">—</div>
      <div class="sub">时间范围内的 session</div>
    </div>
    <div class="stat-card">
      <div class="label">消息</div>
      <div class="value c-msg" id="messages">—</div>
      <div class="sub">消息总数</div>
    </div>
    <div class="stat-card">
      <div class="label">工具调用</div>
      <div class="value c-tool" id="tools">—</div>
      <div class="sub">执行动作次数</div>
    </div>
  </div>

  <div class="charts">
    <div class="chart-panel">
      <div class="section-title">
        <h2>API 端点分布</h2>
        <span>实际计费流量 · 含缓存命中</span>
      </div>
      <div id="providerBars" class="meter-list"></div>
    </div>
    <div class="chart-panel">
      <div class="section-title">
        <h2>模型 × 端点</h2>
        <span>联合统计，避免模型与渠道错位</span>
      </div>
      <div id="modelProviderBars" class="meter-list"></div>
    </div>
  </div>

  <div class="charts">
    <div class="chart-panel">
      <h3>📅 每日 Token 消耗</h3>
      <p class="hint">按会话起始日聚合，最后一列高亮当前统计窗口的最近日期。</p>
      <canvas id="dailyChart"></canvas>
    </div>
    <div class="chart-panel">
      <h3>🤖 模型用量</h3>
      <p class="hint">观察不同模型的消费结构与占比。</p>
      <div id="modelBars" class="meter-list"></div>
    </div>
  </div>

  <div class="charts">
    <div class="chart-panel">
      <h3>⏰ 时段活跃度</h3>
      <p class="hint">消息数分布，适合识别高峰时段。</p>
      <canvas id="hourlyChart"></canvas>
    </div>
    <div class="chart-panel">
      <h3>📱 平台分布</h3>
      <p class="hint">按来源平台聚合的 Token 消耗。</p>
      <div id="platformBars" class="meter-list"></div>
    </div>
  </div>

  <div class="footer" id="updated">加载中…</div>
</div>

<script>
const COLORS = ['#7cc8ff','#7af7b5','#a58bff','#ffb36b','#ff7f96','#72f2ff','#89a2ff','#ffd166','#b8f2e6','#cba6ff'];
let dailyChart, hourlyChart;

function fmt(n) {
  if (!n) return '0';
  if (n >= 1e12) return (n/1e12).toFixed(1) + '万亿';
  if (n >= 1e8) return (n/1e8).toFixed(1) + '亿';
  if (n >= 1e4) return (n/1e4).toFixed(1) + '万';
  return Number(n).toLocaleString('zh-CN');
}

function renderBars(targetId, items, colorOffset = 0) {
  const host = document.getElementById(targetId);
  if (!items || !items.length) {
    host.innerHTML = '<div class="empty">暂无数据</div>';
    return;
  }
  const maxVal = Math.max(...items.map(x => x.tokens), 1);
  host.innerHTML = items.map((item, i) => `
    <div class="model-row">
      <div class="m-name" title="${item.name}">${item.name}</div>
      <div class="m-bar-wrap">
        <div class="m-bar" style="width:${Math.max((item.tokens / maxVal * 100), 1).toFixed(1)}%; background:${COLORS[(i + colorOffset) % COLORS.length]}"></div>
      </div>
      <div class="m-count">${fmt(item.tokens)}</div>
    </div>
  `).join('');
}

function chartCommonGrid() {
  return 'rgba(255,255,255,0.08)';
}

function setDays(days) {
  const el = document.getElementById('days');
  el.value = String(days);
  document.querySelectorAll('.seg-btn').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.days === String(days));
  });
  refresh();
}

async function refresh() {
  const days = document.getElementById('days').value;
  try {
    const r = await fetch('/api/data?days=' + days);
    const d = await r.json();
    if (!d) return;

    document.getElementById('total').textContent = fmt(d.total_tokens);
    document.getElementById('input').textContent = fmt(d.total_input);
    document.getElementById('output').textContent = fmt(d.total_output);
    document.getElementById('cacheRead').textContent = fmt(d.total_cache_read);
    document.getElementById('sessions').textContent = (d.total_sessions || 0).toLocaleString('zh-CN');
    document.getElementById('messages').textContent = (d.total_messages || 0).toLocaleString('zh-CN');
    document.getElementById('tools').textContent = (d.tool_calls || 0).toLocaleString('zh-CN');
    document.getElementById('historical').textContent = fmt(d.historical_tokens);
    document.getElementById('rangeLabel').textContent = d.range_label || ('最近 ' + days + ' 天');
    document.getElementById('rangeValue').textContent = `${d.range_start} → ${d.range_end}`;
    document.getElementById('updated').textContent = '更新于 ' + new Date(d.updated_at).toLocaleString('zh-CN');

    renderBars('providerBars', d.providers || [], 4);
    renderBars('modelProviderBars', d.model_provider || [], 6);
    renderBars('modelBars', d.models || [], 0);
    renderBars('platformBars', d.platforms || [], 2);

    const labels = d.days.map(x => x.label);
    const values = d.days.map(x => x.tokens);
    if (!dailyChart) {
      dailyChart = new Chart(document.getElementById('dailyChart'), {
        type: 'bar',
        data: {
          labels,
          datasets: [{
            data: values,
            backgroundColor: values.map((v, i) => i === values.length - 1 ? 'rgba(124, 200, 255, 0.85)' : 'rgba(124, 200, 255, 0.32)'),
            borderColor: values.map((v, i) => i === values.length - 1 ? 'rgba(114, 242, 255, 0.95)' : 'rgba(124, 200, 255, 0.45)'),
            borderWidth: 1.2,
            borderRadius: 999,
            borderSkipped: false,
            maxBarThickness: 32,
            categoryPercentage: 0.72,
            barPercentage: 0.82,
          }]
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: { legend: { display: false } },
          scales: {
            x: { ticks: { color: '#9eb0d1', font: { size: 11 } }, grid: { display: false } },
            y: { ticks: { color: '#9eb0d1', callback: v => fmt(v), font: { size: 10 } }, grid: { color: chartCommonGrid() } }
          }
        }
      });
    } else {
      dailyChart.data.labels = labels;
      dailyChart.data.datasets[0].data = values;
      dailyChart.data.datasets[0].backgroundColor = values.map((v, i) => i === values.length - 1 ? 'rgba(124, 200, 255, 0.85)' : 'rgba(124, 200, 255, 0.32)');
      dailyChart.data.datasets[0].borderColor = values.map((v, i) => i === values.length - 1 ? 'rgba(114, 242, 255, 0.95)' : 'rgba(124, 200, 255, 0.45)');
      dailyChart.update();
    }

    const hours = d.hourly.map(h => h.hour);
    const hvals = d.hourly.map(h => h.count);
    if (!hourlyChart) {
      hourlyChart = new Chart(document.getElementById('hourlyChart'), {
        type: 'line',
        data: {
          labels: hours,
          datasets: [{
            data: hvals,
            borderColor: '#7af7b5',
            backgroundColor: 'rgba(122, 247, 181, 0.16)',
            fill: true,
            tension: 0.35,
            pointRadius: 2.5,
            pointHoverRadius: 5,
            pointBackgroundColor: '#bffff1'
          }]
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: { legend: { display: false } },
          scales: {
            x: { ticks: { color: '#9eb0d1', font: { size: 11 } }, grid: { display: false } },
            y: { ticks: { color: '#9eb0d1', font: { size: 10 } }, grid: { color: chartCommonGrid() } }
          }
        }
      });
    } else {
      hourlyChart.data.labels = hours;
      hourlyChart.data.datasets[0].data = hvals;
      hourlyChart.update();
    }
  } catch (e) {
    console.error(e);
  }
}

refresh();
setInterval(refresh, 3600000);
</script>
</body>
</html>"""


def main():
    port = int(os.environ.get("PORT", 6088))
    server = ReuseThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"🔮 Hermes Token Dashboard → http://0.0.0.0:{port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
        server.shutdown()


if __name__ == "__main__":
    main()
