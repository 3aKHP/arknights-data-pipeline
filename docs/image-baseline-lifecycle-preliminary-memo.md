# 图片基线生命周期初步评估备忘

日期：2026-09-04

状态：**初步评估，仅供后续调研参考，未冻结**

> 本文不构成对基线频率、活动识别规则、索引 schema、分片方式、迁移方案、实现范围或交付时间的承诺与决策。相关事项将在更大范围、更深层次的专项评估后，于 **2026「感谢庆典」前瞻直播前**形成冻结文档。届时如本文、历史 Issue 评论、当前实现或其他材料与冻结文档冲突，以冻结文档为准。

## 1. 背景

图片产物线最初采用 baseline + delta 分发。历史设计讨论先提出约 6 个月重切一次 baseline，随后在 [Issue #1 的最终设计存档][issue-1-final]中改为“大版本约 3 个月”重切，并要求同时发布 0-diff sentinel delta。这个最终节奏没有进入版本化公开文档，导致当前冻结的 [`image-index-schema.md`](image-index-schema.md) 只描述 baseline 的结构，没有描述其轮换生命周期。

原始意图是将候选重切窗口与国服四类大型限定活动对齐：

1. 农历新年活动；
2. 周年庆典；
3. 夏季活动；
4. 半周年庆，即感谢庆典。

这些节点通常伴随官方前瞻直播、限定寻访和较大的客户端内容更新，适合作为人工可预期的运维窗口。本文重新检查这一经验规则是否仍适合当前实现，并记录冻结前必须解决的问题。

## 2. 当前证据

### 2.1 四个限定窗口具有稳定的季度节奏

当前 [`data-26-09-03-04-06-00_ed95a2` Release][evidence-data-release] 中的 `gacha_table.json` 保存了历史 `gachaRuleType=LIMITED` 寻访。2023 年至 2026 年夏季共 15 个连续限定池：

| 年份 | 农历新年 | 周年庆典 | 夏季  | 感谢庆典 |
| ---- | -------- | -------- | ----- | -------- |
| 2023 | 01-17    | 05-01    | 08-01 | 11-01    |
| 2024 | 02-01    | 05-01    | 08-01 | 11-01    |
| 2025 | 01-22    | 05-01    | 08-02 | 11-01    |
| 2026 | 02-10    | 05-01    | 08-01 | 尚未发生 |

14 个相邻间隔平均 92.27 天，范围为 80 至 104 天。由此可见，四类限定活动比固定的“每 90 天”更贴合真实发布节奏；其中农历新年日期自然在 1 月至 2 月间漂移，不能用固定公历日期替代。

活动名称、SideStory 名称和签到开始时间可能变化，也可能与真正的限定池或客户端更新错开。因此，若后续保留活动锚点，机器可判定的身份应基于目标限定寻访及其对应的正式客户端 `versionId`，活动名称只应作为人类可读的运维日历。

### 2.2 当前 baseline 已接近单资产限制，但可通过分片治理

首个图片 baseline `images-baseline-26-08-03-23-34-20_a745fc` 已按 `shard × variant` 拆成 6 个 ZIP。其中两个 original 分片为：

| 分片                |        字节数 | 占 GitHub 2 GiB 单文件限制 |
| ------------------- | ------------: | -------------------------: |
| `chararts-original` | 1,801,151,651 |                     83.87% |
| `skinpack-original` | 1,831,760,104 |                     85.30% |

[GitHub Releases 当前规则][github-release-limits]允许一个 Release 包含最多 1000 个资产，单个文件必须小于 2 GiB，Release 总大小和带宽没有上限。因此，长期容量风险可以在未来 baseline 中通过多个独立 ZIP 分片解决，不要求依赖 PNG `optimize=True` 压缩来规避单文件上限。

### 2.3 当前实现尚不能安全执行周期性 re-baseline

本仓库目前只有“没有 previous index 时生成 baseline”的隐式分支，没有显式且可审计的 re-baseline 命令、候选构建模式或激活流程。当前 schema 还固定为 6 个 `"<shard>-<variant>" -> filename` 映射。

PRTS-MCP 的 Python 与 TypeScript 消费端同样固定请求这 6 个逻辑 shard。当前 `baselineVersion` 一旦变化，消费者会离开增量复用路径并重新下载所选 baseline：

- 默认 large + preview 集合约 1.50 GB；
- 启用 original 后约 5.14 GB。

这可以保证正确性，但季度轮换会令所有既有部署周期性全量重建。是否允许既有 generation 在逐文件哈希验证后迁移到新 baseline，尚未设计和验证。

当前发布器还允许对已存在的 Release 资产使用 `--clobber`。若目标活动版本已经作为普通 delta 发布，事后以相同 `versionId` 补切 baseline 可能改写既有索引或资产，不能作为安全的默认流程。

## 3. 初步建议（未冻结）

### 3.1 保留四个活动窗口，但定义为候选窗口

初步建议保留原始节奏：每年以国服农历新年、周年、夏季、感谢庆典四类限定寻访所对应的首个正式客户端 `versionId` 作为候选 baseline 窗口，而不是使用固定 90 天计时器。

“候选窗口”不等于必须发布。任何构建、完整性、兼容性或真实消费验收失败时，应继续沿用上一 baseline 并正常发布 delta；不得为了日历对齐覆盖已发布资产或激活不完整 baseline。

还应保留事件外的容量与修复兜底，例如分片接近上限、delta 链异常、协议迁移或上游结构发生不兼容变化时，允许经过独立审批提前重切。

### 3.2 将构建、发布和激活拆成三个阶段

初步倾向将生命周期拆分为：

1. **Build**：从目标 `versionId` 全量重建候选 baseline，生成确定性分片、完整 index、build state 和可审计统计；
2. **Publish**：先以不可被消费者发现的候选状态上传全部资产并独立校验 digest、ZIP、文件集合和索引；
3. **Activate**：仅在 AKDP 与 PRTS-MCP 验收通过后，发布权威 index，使新 `baselineVersion` 对消费者可见。

活动更新的普通 delta 不应被 baseline 构建阻塞。究竟由活动版本自身激活 baseline，还是由紧随其后的版本引用已验证 baseline，需要专项评估；本文不选择其中任何方案。

### 3.3 进一步分片应保持独立 ZIP

未来 original 资产宜按确定性规则拆成多个可独立下载、独立校验、独立解压的普通 ZIP，并留出明显增长余量；不建议使用依赖多卷拼接的 `.z01`/`.zip` 形式。

以下 schema 方向均尚未决定：

- 保持 `akdp-images/v1`，增加更多稳定 shard key；
- 将一个逻辑 shard 映射为文件列表；
- 引入 `akdp-images/v2` 和显式 shard descriptor。

任何选择都必须先验证 Python/TypeScript 双实现、旧客户端失败方式和新旧索引共存策略。

### 3.4 优先避免存量消费者的不必要全量下载

需要研究能否让已经持有完整、较新 generation 的消费者，通过权威 index 的逐文件 SHA-256 验证后更新 baseline 元数据，而不是重新下载相同 PNG。该优化不得削弱以下 fail-closed 边界：缺片、缺 delta、版本倒退、索引不匹配或文件哈希不符时，不得激活候选 generation。

## 4. 冻结前待回答的问题

至少需要完成以下评估，才适合形成最终冻结文档：

1. 如何从 `gacha_table.json` 稳定识别四类限定系列，而不是误把联动限定或其他特殊池作为 re-baseline 触发器？
2. 候选 baseline 在活动更新前、同版本更新中还是更新后构建？失败时如何保持普通 delta 发布不受影响？
3. 新 baseline 如何原子地变为可发现状态，且绝不 clobber 已发布的权威 index？
4. multipart shard 使用 v1 扩展还是 v2 schema？旧消费者应继续使用旧 baseline、明确拒绝，还是通过兼容字段迁移？
5. 存量消费者能否复用已有 generation？fresh install、升级、回滚和离线恢复分别采用什么路径？
6. shard 的确定性划分、目标大小、硬上限和增长余量是多少？如何避免单个超大 artwork 破坏均衡？
7. 新 baseline 激活后，旧 baseline 和旧 delta 链保留多久？如何证明仍可恢复旧版本？
8. 哪些 Actions、Release、双实现测试、生产自然同步和真实 MCP 调用证据构成激活门槛？
9. 如果 2026 感谢庆典窗口前仍未满足门槛，是延期、跳过本轮还是继续使用旧协议？

## 5. 当前临时边界

在冻结文档产生前：

- 当前 `akdp-images/v1`、首个 baseline 和既有 delta 链继续作为生产权威；
- 自动工作流只发布普通 delta，不自动发起 re-baseline；
- 不以“四个活动窗口”作为现有代码已经承诺或已经实现的行为；
- 不因本文修改 #4 的 PNG 编码策略，也不把格式迁移并入 baseline 生命周期；
- 不删除、覆盖或重写现有 baseline、delta、index 或 build-state Release 资产。

最终生命周期、迁移方案、验收矩阵和首次执行窗口，将在 2026「感谢庆典」前瞻直播前冻结并写入独立的权威文档。

[issue-1-final]: https://github.com/3aKHP/arknights-data-pipeline/issues/1#issuecomment-5196827004
[evidence-data-release]: https://github.com/3aKHP/arknights-data-pipeline/releases/tag/data-26-09-03-04-06-00_ed95a2
[github-release-limits]: https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases#storage-and-bandwidth-quotas
