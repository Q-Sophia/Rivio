# Rivio Demo

独立静态发布目录，默认打开首页即为 Demo。可将本目录作为独立 Vercel Project 的 Root Directory；目录内已包含 HTML、CSS、JavaScript 和脱敏 JSON，无需构建或后端服务。不包含 LiveDataProvider，`?demo=0` 也不会启用 Live。

入口：

- `/`：Demo 首页
- `/?view=report`：直接查看报告
- `/?view=replay`：开始回放

本地可用任意静态 HTTP 服务器提供本目录，例如在本目录运行 `python -m http.server 4174 --bind 127.0.0.1`。不要直接用 file:// 打开。

唯一数据源为 `task_user_5117fc817c3a`。仅加载同站静态 JSON；来源外链在用户主动点击时才访问原站。历史证据不足与异常合并对象保留。

本目录由仓库根目录的 `node scripts/sync-rivio-demo.mjs` 从现有前端及其脱敏快照同步。同步不读取 backend，不调用网络。前端更新后重新同步；需要更新历史导出时先单独运行原有导出脚本。部署时只使用本目录，不需要仓库根目录脚本，也不需要访问父目录。
