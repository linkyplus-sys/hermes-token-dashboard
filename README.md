# Hermes Token Dashboard

实时查看 Hermes Agent 的 token 消耗统计。直接从 Hermes 的 `state.db`（SQLite）读取数据，展示交互式图表仪表盘（Chart.js）。

## 功能

- 📊 **每日 token 趋势** — 折线图追踪用量高峰
- 📈 **模型分布** — 水平条形图展示各模型 token 消耗
- ⏰ **时段活跃度** — 24 小时柱状图看活动高峰
- 🌐 **平台 / API 端点分布** — 按平台、来源、实际 API 端点分组
- 📋 **模型 × 端点联合视图** — 看出哪些模型走了哪个后端
- 🏷️ **实时统计卡片** — 总量、输入 / 输出 / 缓存读取、会话数、消息数、工具调用数
- 🔄 **1 天 / 3 天 / 7 天 / 30 天 / 全部** 历史范围切换（全部按 ISO 周聚合）
- 🪄 **Glassmorphism 深色 UI** — 紫色 / 青色渐变背景，毛玻璃卡片，响应式布局

## 快速使用

```bash
pip install https://github.com/linkyplus-sys/hermes-token-dashboard/archive/refs/heads/main.zip
```

> **💡 WebUI 用户注意：**
> Hermes WebUI 默认不会将 token 用量同步到 `state.db`，需要手动开启：
> 打开 WebUI → 设置 → 开启 **"Sync to Insights"**
> CLI / 飞书 / Cron 等来源的 token 默认就会记录，无需额外设置。

## 架构

- **Python 3** + `http.server.ThreadingHTTPServer` — 零外部依赖
- **SQLite** — 只读挂载 Hermes 的 `state.db`
- **Chart.js** — CDN 加载，前端动态渲染
- **15 秒缓存** — API 端点缓存，减少 DB 压力

## 部署

### Docker（推荐）

```bash
# ⚠️ 重要：把下面的路径改成你自己的 state.db 位置！
# 通常在 ~/.hermes/state.db
STATE_DB="$HOME/.hermes/state.db"

docker run -d \
  --name hermes-token-dash \
  --restart unless-stopped \
  -e TZ=Asia/Shanghai \
  -v "$STATE_DB":/root/.hermes/state.db:ro \
  -p 6088:6088 \
  python:3.11-slim \
  sh -c 'pip install https://github.com/linkyplus-sys/hermes-token-dashboard/archive/refs/heads/main.zip -q && python3 -c "from dashboard import main; main()"'
```

访问 `http://<你的IP>:6088`

⚠️ **必读提醒：**
1. **路径必须改成你自己的** — 把 `$STATE_DB` 改成你实际的 `state.db` 路径，通常是 `~/.hermes/state.db`
2. **时区必须设置** — `-e TZ=Asia/Shanghai` 是必须的，不然小时图会偏移 8 小时
3. **只读挂载** — `:ro` 确保 dashboard 不会修改你的数据库
4. **如果路径错误** — dashboard 会显示"数据库不存在"错误，不会崩溃

### 手动运行

```bash
python3 dashboard.py
```

## SQLite 表格说明

本项目读取 Hermes Agent 的 `state.db` 中的 `sessions` 表：

| 列 | 说明 |
|---|---|
| `started_at` | 会话开始时间戳（Unix） |
| `input_tokens` | 提示词 token（不含缓存） |
| `output_tokens` | 输出 token |
| `cache_read_tokens` | **缓存读取 token（也会计费！）** |
| `cache_write_tokens` | 缓存写入 token |
| `model` | 请求的模型名称 |
| `billing_base_url` | 实际 API 端点 |
| `source` | 平台（cli、feishu 等） |
| `message_count` | 消息数量 |
| `tool_call_count` | 工具调用次数 |

⚠️ `cache_read_tokens` 在某些 provider（如 DeepSeek）中可能占总账单 90%+。**所有 SQL 聚合 token 时都必须加上这个字段。**

## 贡献

欢迎 issue 和 PR。
