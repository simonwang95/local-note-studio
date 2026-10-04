# 0.1.29 内部包验收（2026-10-04）

结论：Apple Silicon 内部 app/DMG 的构建、包内容、隔离 Worker 端到端和原生桌面冒烟验收通过。验收范围包含 P0/P1 工作流回归及 P2 T-118 缓存管理与任务诊断。Developer ID 签名、公证、Intel 包、真实模型完整任务矩阵和独立干净 Mac 验收仍待完成。

## 产物

| 项目 | 结果 |
| --- | --- |
| 版本 | app、Tauri、Worker 均为 `0.1.29` |
| 架构 | `arm64` / 文件名 `aarch64` |
| DMG | `src-tauri/target/release/bundle/dmg/Local Note Studio_0.1.29_aarch64.dmg` |
| 大小 | 3,922,766 字节 |
| SHA-256 | `258c6840a3ca65a3ff5e45dfc286707b381c3439b329a3a721c1b12b55428c09` |
| 签名 | strict/deep ad-hoc 校验通过；未公证 |
| 安装入口 | APFS 只读挂载，`Applications -> /Applications` 有效 |
| 内容一致性 | 挂载 app 与构建 app 全部文件哈希一致；37 个 Worker 资源匹配源码；内嵌 Brotli JS/CSS 解压后匹配 `dist` |
| 本机文件排除 | 无 `env.local`、凭据文件、Cookie 文件或 Python 字节码 |

## 自动化与包内 Worker

`npm run release:check` 通过生产前端构建、两组前端回归、281 项 Python 测试、11 项 Rust 测试和发布配置检查；Shell 语法及 `git diff --check` 通过。新增 Rust 检查要求桌面隔离数据目录使用绝对路径。

[包内验收脚本](../scripts/verify_packaged_p2.py) 的源码 fixture 自检、原始 signed app 和只读挂载 DMG 三条路径均通过。脚本的 `--app` 模式需要当前构建生成的 Tauri 压缩资产、`dist` 与可用的 Python `brotli` 模块，用于验证实际嵌入资产，不能仅凭 JS 字符串扫描判断 Tauri 二进制内容；`--self-test` 不依赖这些构建资产。

每条 Worker 验收路径发起 18 次真实 stdin 请求，在独立临时状态/输出根目录使用虚构样本与包内缓存、诊断 API：

- 五类库存、来源/任务状态和失败恢复保护正确；审计事件单独统计并保留。
- 手动清理删除 2 项、2,029 字节；预览后新增队列引用及变更指纹的 2 项跳过。
- 外部进程持有全局锁时清理受阻，库存仍可读取。
- 自动清理默认关闭；启用 30 天策略后只删除 1 项旧可清理缓存，近期缓存原字节保留。
- 默认导出、原文选入、路径选入均符合选项，凭据与 Cookie 始终脱敏；已知 usage 正确，缺失指标保持未知。
- 源文件、正式笔记、图片、模型、有效恢复和诊断审计哨兵保留；整个 signed app 前后哈希不变，验收后再次通过签名检查。

复验示例（先构建当前版本；路径使用真实绝对路径）：

```bash
npm run release:check
npm run tauri:build -- --bundles app
npm run release:dmg
python3 scripts/verify_packaged_p2.py --self-test
python3 scripts/verify_packaged_p2.py --app "/absolute/path/Local Note Studio.app" --report /private/tmp/lns-p2-acceptance.json
```

对 DMG 的复验使用独占临时目录只读挂载，并在 `finally` 中卸载；本次映像已卸载。

## 原生桌面冒烟

从构建 app 复制测试副本，仅更改其 `CFBundleIdentifier` 并重新临时签名，隔离 WebView 配置。以 `LOCAL_NOTE_STUDIO_APP_DATA_DIR` 指定绝对临时目录启动，使用现有 Python，不安装运行时或下载模型。该副本的原生代码与资源来自同一构建；原始 sealed app 保持不变。

通过真实配置页和校验页操作，确认：

- 刷新空库存为 0 项；创建虚构样本后显示 4 项、2.3 KiB，其中 2 项恢复材料受保护、805 字节。
- 可清理缓存 1 项、750 字节；默认 30 天预览只列出该文件，未勾选原文证据不进入候选。
- 自动清理开关保持关闭，诊断缺失模型调用/用量显示“未知”，失败诊断和恢复定位按钮可见。
- 导出通过 WebView 实际下载 JSON；文件可解析且不含虚构原文、完整路径、测试密钥或 Cookie。下载样本已移回测试目录。
- 桌面冒烟仅做预览；实际删除、并发锁、队列新增引用和保留期限由上述包内 Worker 路径验收。5 个桌面样本文件的哈希未改变。测试进程已关闭。

操作边界：桌面工具最初自动启动了一个未继承隔离变量的额外测试副本，库存刷新读取过默认状态目录，并同步了测试窗口的引用快照；未执行清理、保留策略保存或原文导出。发现目录不符合预期后立即关闭该副本，保留带隔离变量的进程，确认空库存和返回路径后才进行样本验收。测试未替换或操作已安装应用的设置，也未删除真实缓存、正式笔记或源文件。后续验收应先确认进程路径、数据根目录和库存路径，避免工具自动启动副本丢失环境变量。

本次运行在开发机既有 Python 下，实际模型请求和运行时下载均为 0；它不代替无 Conda/Homebrew 的首次安装、升级、修复、卸载或独立用户完整工作流验收。公开发布继续按 [T-108/T-109](todo.md) 执行。
