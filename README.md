# Arknights Data Pipeline

明日方舟 CN 服游戏数据的解包与打包流水线。本仓库**自身即分发点**——
产出的 `data-*` Release 直接被 [PRTS-MCP](https://github.com/3aKHP/prts-mcp) 消费
（见 issue [#86](https://github.com/3aKHP/prts-mcp/issues/86)）。

## 设计原则

上游原始数据仓库（Kengxxiao/*）已消失，本仓库自建数据契约：

1. **累积数据树**：客户端热更清单只反映"当前状态"，旧活动内容会被 HG 移除。
   每次发布 = 上一版 Release 基线 + 新解包结果的合并，历史内容只增不减。
2. **可验证**：每次发布附带 manifest（源版本、工具版本、包哈希、记录指标、
   排除项、合并统计）；校验门不过则不发布（fail-closed）。
3. **可回滚**：Release 序列即历史档案，回滚 = 将旧 Release 重新标记为 latest。
4. **统一分发**：一个 `data-<versionId>` Release 带四个 asset，替代旧的两个 fork
   仓库 + 两种 tag 前缀的历史模式。

## 流水线

```
check      变更检测：HG CDN versionId vs 工厂仓库最新 Release tag，未变化则短路
fetch      HG CDN → arkprts 解包（带重试/完整性校验）
normalize  cn/ → zh_CN/ 布局映射、排除清单
merge      基线 Release 树 ⊕ 新解包树 → 候选树（新文件覆盖，基线文件保留）
story      剧情 txt → JSON（vendor/ASTR-Script，含大小写错位修复与索引重建）
summarize  增量 LLM 双级别摘要（summaries.json + event_summaries.json）
validate   校验门：契约文件、探针、记录数回归、UTF-8、累积不变量、剧情转换完整性
package    三个 zip + manifest.json
publish    data-* 单 Release 四 asset，发到工厂仓库自身（gh CLI）
```

## 分发

每个游戏版本一个 Release（`data-<versionId>`），保持 draft 直到校验完成，带四个 asset：
- `zh_CN-excel.zip` — 数值表
- `zh_CN-levels.zip` — 关卡数据
- `zh_CN.zip` — excel + 剧情 JSON + ASTR 索引 + LLM 摘要
- `manifest.json` — 契约版本、源版本、校验指标和三个包的大小/SHA-256

Release notes 同步内嵌 manifest，方便人工审计。manifest 还记录流水线 commit、
ASTR/flatc 等转换器来源、归一化/排除政策以及每个包的大小和 SHA-256；消费端优先
校验 manifest asset，旧 Release 没有该 asset 时保留兼容读取。

### LLM 配置

`summarize` 步骤需要 OpenAI Chat Completions 兼容端点，通过环境变量或 `.env` 配置：

```
LLM_BASE_URL=https://api.deepseek.com/v1   # 或任意兼容端点
LLM_API_KEY=sk-...
LLM_MODEL=deepseek-v4-flash                # 或任意兼容模型
LLM_EXTRA_BODY={"thinking":{"type":"none"}} # 可选：供应商扩展参数（JSON 对象）
```

`LLM_EXTRA_BODY` 会原样合并进 Chat Completions 请求体。对推理型模型（如 deepseek-v4-flash）建议按上例关闭思考模式：思维链 token 计入 `max_tokens`，不关闭时可能以 `finish_reason=length` 且正文为空的形式耗尽预算（2026-09-03 事故空串摘要的根因）。

增量策略：候选树中的 `summaries.json` / `event_summaries.json` 由累积合并从基线
继承，`summarize` 只对缺失条目调 LLM。典型更新 ~15-20 章 + 1-2 活动 ≈ 20 次调用。

验收门禁：每个 LLM 响应必须通过共享验收（`summary_gate`：非空、长度下限、终止符
黑名单、格式污染、`finish_reason` 分类）才会写入；被拒响应在进程内有界重生成，
耗尽后经 `failed_details` 令流水线失败关闭。`validate` 阶段对全部库存条目做同样的
全量扫描（零 LLM 成本），任何缺失或失效条目都会阻断发布——因此本地未配置
`LLM_API_KEY` 时 `validate` 会因覆盖不全而失败，属预期行为。验收元数据随
`summaries.meta.json` / `event_summaries.meta.json` 发布；逐尝试调用账本写入
`work/llm-ledger.jsonl`（含完整供应商明细，仅经私有端点上传，配置
`LEDGER_ENDPOINT_URL` / `LEDGER_ENDPOINT_TOKEN` Secrets 后生效）。

## 自动化（GitHub Actions）

`.github/workflows/data-pipeline.yml` 每 2 小时运行一次：`check` 短路时 ~45 秒退出，
有新版本时自动跑完整链路并 `publish`。每次运行先执行 `tests/`，发布校验默认启用
`config/probes.json` 中的代表性干员和活动探针。需要以下 Repository Secrets：

- `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL`

也可通过 Actions 页面手动触发（workflow_dispatch）；勾选 `force` 可在 versionId
未变化时演练完整路径，不依赖等待下一次上游版本更新。

上游 HG CDN 是唯一新鲜度信号；第三方成品仓库只作为人工对照/应急调查，不会被
自动切换。最新已发布 Release 是累积基线和运行时回滚点，抓取、格式、探针或发布
校验任一步失败都会保留上一代数据。完整责任边界与人工切换规则见
[`SOURCE_STRATEGY.md`](SOURCE_STRATEGY.md)。

图片工作流额外从上一版 `images-*` Release 恢复私有的
`images-build-state.json`：它用 AB 包 hash 先筛出变化包，再以 Sprite SHA-256 决定
真正的发布 delta。该资产不属于 PRTS-MCP 的公开 `index.json` 契约；旧 Release 尚无
此资产、状态不兼容或版本不匹配时，工作流会安全回退到一次全量图片构建。

图片 baseline 的长期轮换计划见 [`docs/image-baseline-lifecycle-preliminary-memo.md`](docs/image-baseline-lifecycle-preliminary-memo.md)。该文档是初步评估备忘，仅供后续专项调研参考，不构成对节奏、schema、迁移方案、实现范围或交付时间的承诺与决策。实际方案将在 2026「感谢庆典」前瞻直播前冻结，届时以冻结文档为准。

## 本地使用

```bash
uv sync --all-extras
git submodule update --init   # ASTR-Script（story 步骤依赖）
# 全链路（fetch 需要网络和约 150MB 下载；versionId 未变时自动短路）
uv run akdp --workdir work run
# 或分步
uv run akdp --workdir work check
uv run akdp --workdir work fetch
uv run akdp --workdir work merge
uv run akdp --workdir work story
uv run akdp --workdir work summarize
uv run akdp --workdir work validate
uv run akdp --workdir work package
uv run akdp --workdir work publish          # dry-run（默认）
uv run akdp --workdir work publish --execute # 实际发布
```

workdir 结构：

```
work/
  baseline/     # 基线（从最新工厂 Release 解压；无 Release 时须人工预置）
  extract/      # arkprts 原始解包产物
  normalized/   # cn/ → zh_CN/ 布局映射后的中间产物
  candidate/    # 合并后的候选发布树
  dist/         # zip 产物 + manifest.json
```

## 致谢

- [thesadru/arkprts](https://github.com/thesadru/arkprts) — 下载/解包编排
- [MooncellWiki/torappu](https://github.com/MooncellWiki/torappu) — flatc 与解包技术
- [MooncellWiki/OpenArknightsFBS](https://github.com/MooncellWiki/OpenArknightsFBS) — FlatBuffers schema
- [050644zf/ASTR-Script](https://github.com/050644zf/ASTR-Script) — 剧情文本 → JSON 转换

游戏数据版权归属 上海鹰角网络科技有限公司，仅供学习交流使用。
