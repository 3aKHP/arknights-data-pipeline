# Arknights Data Pipeline

明日方舟 CN 服游戏数据的解包与打包流水线 ——
[3aKHP/ArknightsGameData](https://github.com/3aKHP/ArknightsGameData) 与
[3aKHP/ArknightsStoryJson](https://github.com/3aKHP/ArknightsStoryJson)
Release 的数据工厂，服务于 [PRTS-MCP](https://github.com/3aKHP/prts-mcp)（见 issue #86）。

## 设计原则

上游原始数据仓库（Kengxxiao/*）已消失，本仓库自建数据契约：

1. **累积数据树**：客户端热更清单只反映"当前状态"，旧活动内容会被 HG 移除。
   每次发布 = 上一版 Release 基线 + 新解包结果的合并，历史内容只增不减。
2. **可验证**：每次发布附带 manifest（源版本、工具版本、包哈希、记录指标、
   排除项、合并统计）；校验门不过则不发布（fail-closed）。
3. **可回滚**：Release 序列即历史档案，回滚 = 将旧 Release 重新标记为 latest。
4. **分发契约不变**：产物为 `zh_CN-excel.zip` / `zh_CN-levels.zip` / `zh_CN.zip`，
   PRTS-MCP 客户端零改动。

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
publish    data-* 单 Release 三 asset，发到工厂仓库自身（gh CLI）
```

## 分发

工厂仓库（`3aKHP/arknights-data-pipeline`）自身就是分发点。每个游戏版本一个
Release（`data-<versionId>`），带三个 asset：
- `zh_CN-excel.zip` — 数值表
- `zh_CN-levels.zip` — 关卡数据
- `zh_CN.zip` — excel + 剧情 JSON + ASTR 索引 + LLM 摘要

Release notes 即 manifest.json（源版本、工具版本、合并统计、摘要统计、校验指标、包哈希）。

### LLM 配置

`summarize` 步骤需要 OpenAI Chat Completions 兼容端点，通过环境变量或 `.env` 配置：

```
LLM_BASE_URL=https://api.deepseek.com/v1   # 或任意兼容端点
LLM_API_KEY=sk-...
LLM_MODEL=deepseek-v4-flash                # 或任意兼容模型
```

增量策略：候选树中的 `summaries.json` / `event_summaries.json` 由累积合并从基线
继承，`summarize` 只对缺失条目调 LLM。典型更新 ~15-20 章 + 1-2 活动 ≈ 20 次调用。

## 使用

```bash
uv sync --all-extras
git submodule update --init   # ASTR-Script（story 步骤依赖）
# 全链路（fetch 需要网络和约 500MB 下载；versionId 未变时自动短路）
uv run akdp --workdir work run
# 或分步
uv run akdp --workdir work check
uv run akdp --workdir work fetch
uv run akdp --workdir work merge
uv run akdp --workdir work story
uv run akdp --workdir work validate
uv run akdp --workdir work package
```

workdir 结构：

```
work/
  baseline/     # 基线（从上一版 Release 解压）
  extract/      # 本次解包原始产物（arkprts 输出）
  candidate/    # 合并后的候选发布树
  dist/         # zip 产物 + manifest.json
```

## 致谢

- [thesadru/arkprts](https://github.com/thesadru/arkprts) — 下载/解包编排
- [MooncellWiki/torappu](https://github.com/MooncellWiki/torappu) — flatc 与解包技术
- [MooncellWiki/OpenArknightsFBS](https://github.com/MooncellWiki/OpenArknightsFBS) — FlatBuffers schema
- [050644zf/ASTR-Script](https://github.com/050644zf/ASTR-Script) — 剧情文本 → JSON 转换

游戏数据版权归属 上海鹰角网络科技有限公司，仅供学习交流使用。
