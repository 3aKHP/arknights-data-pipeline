# Phase 0 Spike 报告：自建解包管线可行性验证

日期：2026-08-04 · 关联：3aKHP/prts-mcp#86

## 结论

**可行。** `arkprts` 一条命令即可从 HG CDN 产出当前客户端的完整 gamedata，
全部探针通过。最硬的两块（AB 解包/解密、FlatBuffers schema）由
`MooncellWiki/torappu` 生态（arkprts + OpenArknightsFBS + flatc）承担。

## 环境

- WSL2, Python 3.11.15 (uv venv), `arkprts[all]`, flatc 23.3.3（torappu 预编译二进制）
- 命令：`python -m arkprts.assets data --server cn`
- 产出：`data/cn/gamedata/{excel,levels,story,battle,...}`，约 426MB

## 探针结果（probe.py，全部 PASS）

| 检查项 | 结果 |
|---|---|
| 8 个必需 excel JSON（PRTS-MCP `GAMEDATA_FILES` 契约） | ✅ 全部存在 |
| 新干员 嘉辛塔/时隙/珊比 | ✅ 3/3，entry schema 与现 Release 一致 |
| 稀有度字段 | ✅ 仍为 `TIER_5`/`TIER_6` 字符串（应急包的数字稀有度问题来自当时的候选源，本路径无此问题） |
| `story_review_table[act53side]` | ✅ 19 条，story txt 全部可解析 |
| `stage_table` act53side 关卡 | ✅ 15 关（**应急包遗留的未证明项，现已证明**） |
| act53side 实际关卡文件 | ✅ 12 个 level JSON |
| `levels/enemydata/enemy_database.json` | ✅ |
| 非 UTF-8 伪 JSON | ✅ 0 个（本路径无此问题） |
| character_table 与现 Release 对拍 | ✅ 1323 条完全相同，key schema 一致 |

## 关键背景发现

- **Kengxxiao/ArknightsGameData 与 Kengxxiao/ArknightsStoryJson 均已 404（删库）**。
  原上游彻底消失，自建不再是"要不要"而是"必须"。
- 剧情 JSON 生态由 ASTR 继承：`050644zf/ASTR-Script` 的 `jsonconvert.py`
  产出格式与 PRTS-MCP 消费的 story JSON 完全一致（`storyList[{id,prop,attributes}]`
  + story_review 元数据）；`050644zf/ArknightsStoryJson` 数据仓库仍在更新（08-03）。

## 差距清单（Phase 1 设计输入）

1. **历史累积（最重要）**：arkprts 产出的是"当前客户端状态"——levels 仅覆盖
   41 个活动目录，现 Release 有 107 个（缺 970 个历史关卡文件，HG 会从
   hot_update_list 中移除旧活动内容）。工厂必须维护**累积数据树**：
   以 08-01 应急包（已在生产验证）为种子基线，每次运行合并新增/变更，
   从累积树打包发布，而非每次从零解包。excel 侧同理需监控字段级漂移。
2. **vc/vc_config.json**：现 Release 有、arkprts 未提取（客户端封禁名单），
   需要小补丁或后处理补上。
3. **下载鲁棒性**：arkprts 无重试、无完整性校验。spike 中两次瞬时故障
   （ContentLengthError / CBC padding error，均为截断下载），重跑即恢复。
   工厂需要重试包装 + 包级完整性校验，失败时 fail-closed 不发布。
4. **布局与打包**：`cn/` → `zh_CN/` 映射；三个 zip 的打包层（excel.zip /
   levels.zip / zh_CN.zip 内容划分与现 Release 对齐）。
5. **剧情转换链**：story txt → story JSON + 索引文件（storyinfo/chardict/
   wordcount/extrastory）由 ASTR-Script 承担；需 pin 版本并验证对
   新剧情格式的兼容性（`[uc]info` 目录用途待确认）。
6. **版本探测**：`hot_update_list.json`（含版本号与 AB 哈希）天然支持
   "无变化不发布"，避免空转产生噪音 Release。

## Phase 1 建议

1. 创建工厂仓库 `3aKHP/arknights-data-pipeline`（本 spike 工作目录为其前身）。
2. 流水线：`hot_update_list` 变更检测 → arkprts 解包（带重试/校验包装）
   → 归一化层（布局映射、vc_config 补丁、排除清单）→ 合并进累积数据树
   → ASTR-Script 剧情转换 → 校验门（schema/记录数回归/关键探针，复用 probe.py）
   → 打包三个 zip + manifest（源 commit、工具版本、包哈希、记录指标、排除项）
   → 发 `upstream-*` Release 到 `3aKHP/ArknightsGameData` / `3aKHP/ArknightsStoryJson`。
3. 种子数据：08-01 应急包作为累积树初始基线（需先从生产侧取回）。
4. 调度：先服务器 cron，稳定后再决定是否加 GHA 冗余。
