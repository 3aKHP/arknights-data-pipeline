# 数据来源、回退与切换策略

本策略把“上游是否刚好发布新版本”和“我们的系统是否可靠”分开处理。前者不受本仓库
控制，只作为持续运维观测；开发验收使用可重复的强制全链路、无变化短路和故障路径测试。

## 责任边界

| 数据域 | 新鲜数据主源 | 受控构建/分发 | 可用性回退 |
|--------|--------------|---------------|------------|
| GameData（excel/levels） | 鹰角 HG CDN 的客户端热更清单与资源 | 本仓库解包、归一化、累积合并并发布 `zh_CN-excel.zip` / `zh_CN-levels.zip` | 上一版工厂 Release；PRTS-MCP 上一代已激活数据与 bundled 数据 |
| StoryJson | 同一 HG CDN 中的剧情文本与元数据 | 本仓库固定版本的 ASTR-Script 转换并发布 `zh_CN.zip` | 上一版工厂 Release；PRTS-MCP 上一代已激活数据与 bundled 数据 |

HG CDN 决定“有没有新数据”；本仓库负责下载重试、schema/布局归一化、累积历史、探针、
可复现打包和发布完整性。第三方成品数据仓库可以用于人工对拍和事故调查，但不会自动成为
可信输入；自动切换到未经同一契约校验的成品源会把停更风险替换成 schema 污染风险。

解包/转换工具是可固定、可 fork 的构建依赖，不是成品数据依赖。manifest 记录流水线
commit、arkprts/UnityPy 版本、ASTR-Script commit、torappu flatc 来源 commit 与二进制
SHA-256，以及版本化归一化/排除政策。

## 切换和恢复

正常路径只接受 HG CDN 的 `versionId` 作为新鲜度信号。网络失败、格式变化、记录数回退、
探针失败、坏包或远端资产校验失败时，流程 fail-closed，不发布，也不改变消费者的上一代
有效数据。

需要使用应急快照或替代提取工具时，切换必须人工发起：固定输入快照和工具版本，经过同一
normalize/merge/validate/package 流程，用 `--force` 演练，保持 Release 为 draft，核对
manifest 和三个资产，完成 Python、TypeScript 与真实 MCP `tools/call` 验收后才允许发布。
回滚通过恢复旧 Release 为 latest 或保持消费者上一代激活目录完成，不依赖服务重启。

## 可控验收与运维观察

开发/关闭门槛包括：

- `versionId` 相同的确定性短路测试；
- `workflow_dispatch force` 驱动的完整路径；
- 下载失败、404 旧 manifest 兼容、manifest/schema/哈希不匹配和坏包测试；
- draft-first 发布、断点恢复、同名资产覆盖及发布前远端 digest 复核；
- Python/TypeScript 测试与 MCP initialize、tools/list、tools/call 边界验收。

定时 Action 是否在真实上游更新窗口自动命中新版本，只是上线后的运维观察项，不阻断开发
合并或 Issue 关闭。出现新版本后仍应检查探针、manifest、Release 资产和消费者激活状态。
