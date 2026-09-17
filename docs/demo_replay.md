# Rivio 静态 Demo

唯一来源：`task_user_5117fc817c3a` / `pipeline_fa1316738666`。不拼接其他任务。

## 本地查看

在项目根目录启动纯静态服务器（不启动 FastAPI）：

```powershell
python -m http.server 4173 --bind 127.0.0.1 --directory frontend
```

- 首页：`http://127.0.0.1:4173/?demo=1`
- 最终报告：`http://127.0.0.1:4173/?demo=1&view=report`
- 自动开始回放：`http://127.0.0.1:4173/?demo=1&view=replay`

需通过 HTTP 查看，不能直接双击 HTML；JSON 是同站静态文件请求。来源原文链接只在用户点击后打开外站，页面不会自动抓取或预加载外部资料。本地来源详情和证据摘录无需访问原站。

不带 `demo=1` 仍走原 Live 初始化与 API/SSE。Demo 不恢复或覆盖 Live localStorage，不注册 Live 命令按钮。Demo 的遗留 API 调用在 Provider 内拒绝，JSON 加载失败也不会回退。

## 数据导出

```powershell
node backend/export_demo_run.mjs
```

脚本只依赖 Node 标准库，从固定 Run 目录读取白名单字段，验证引用闭合、Pipeline 身份和事件证据集合后写入 `frontend/demo/demo_run.json`。输出保存 Artifact SHA256；不会复制原始目录、Prompt、推理内容、LLM 日志、工具/Observation payload、上下文或网页全文。可疑凭证、路径和 URL 参数导致导出失败，而不是继续发布。不要把整个项目根目录作为静态发布目录。

报告正文、32 条 Statement、8 条 Claim/Citation、13 条 Evidence 和 17 个 Source 保留原 ID。Coverage 15 项和任务 32 项保留异常合并对象“小红书、抖音”。首页和 Brief 展示真实需求中的两个对象；Workspace 内部历史产物原样保留。

## 回放语义

默认 33 秒。`atMs` 是展示时间，`created_at` 是原始时间；按 Pipeline sequence 依次应用所有事件，屏幕可批量刷新。现有 WorkspaceProjection 筛选公开活动。数据层只按成功验证的证据 ID 去重累加；`evidence_added` 不视为新增证据。

前 2 秒在现有输入框逐字展示已保存的真实需求，第 2–3 秒展示已确认 Brief，随后进入 Workspace。输入动画是演示效果，不声称复原历史键盘操作，也不发送请求；重播会从空输入框重新开始。

Coverage 两个真实检查点为 11/13 条证据、16/17 个来源；第一轮只有汇总，最终矩阵到第二轮检查点才显示，不反推第一轮格子。阶段成果按真实交接出现。未回放任务使用独立展示状态，最终任务保留 14 个 waiting_for_collector、7 个 evidence_exhausted 和 11 个 evidence_extracted。

跳过与直接报告使用同一最终快照，重新播放清空状态与时钟。历史 Reviewer 是规则审查，保留审查问题；完成流程不表示 Coverage 100%。

## 验证

```powershell
# 已安装 Playwright 的环境；可通过 NODE_PATH 指向其 node_modules。
$env:DEMO_BROWSER_CHANNEL = 'msedge'
node frontend/check-demo.mjs
```

测试仅启动临时纯静态服务器，所有 API 路径均返回 503；记录页面请求和 EventSource 构造，验证两个入口、32 条段落交互、回放/跳过/重播、历史不足、移动端宽度、深链接刷新、localStorage 隔离及 JSON 缺失不回退。截图写入系统临时目录。

架构归属：离线导出属于 Infrastructure/Context 的公开投影；回放是前端展示逻辑，不执行 Tool 或 Orchestration。原 evidence-first 主链与 Live 执行流程不变。
