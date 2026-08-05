# Image Index Schema & State Lifecycle (Frozen)

日期：2026-08-06 · 关联：issue #1 Phase 3C/D

本文档冻结 `index.json` 的最终 schema 和管线各阶段的状态生命周期。
PRTS-MCP 消费端以本文档为准，不以 A/B 阶段的中间产物为准。

## 1. index.json 最终 schema

```json
{
  "schemaVersion": "akdp-images/v1",
  "baselineVersion": "26-06-01-12-00-00_abc123",
  "currentVersion": "26-08-03-23-34-20_a745fc",
  "shards": {
    "chararts-original": "images-baseline-chararts-original-26-06-01.zip",
    "chararts-large": "images-baseline-chararts-large-26-06-01.zip",
    "chararts-preview": "images-baseline-chararts-preview-26-06-01.zip",
    "skinpack-original": "images-baseline-skinpack-original-26-06-01.zip",
    "skinpack-large": "images-baseline-skinpack-large-26-06-01.zip",
    "skinpack-preview": "images-baseline-skinpack-preview-26-06-01.zip"
  },
  "artworks": {
    "char_002_amiya#2": {
      "kind": "base",
      "sinceVersion": "26-06-01-12-00-00_abc123",
      "shard": "chararts",
      "original": {
        "file": "char_002_amiya#2.original.png",
        "w": 2072, "h": 2232,
        "bytes": 3529229,
        "sha256": "95252140..."
      },
      "large": {
        "file": "char_002_amiya#2.large.png",
        "w": 951, "h": 1024,
        "bytes": 1096810,
        "sha256": "0e8bd3ca..."
      },
      "preview": {
        "file": "char_002_amiya#2.preview.png",
        "w": 238, "h": 256,
        "bytes": 108746,
        "sha256": "0012711c..."
      }
    }
  }
}
```

### 字段定义

| 字段 | 级别 | 说明 |
|---|---|---|
| `schemaVersion` | root | `"akdp-images/v1"`；PRTS-MCP 校验此值，不匹配则拒绝 |
| `baselineVersion` | root | 当前 baseline 对应的 versionId；客户端据此判断是否需要下载新 baseline |
| `currentVersion` | root | 本索引对应的 versionId（= delta Release tag 后缀） |
| `shards` | root | baseline 分片文件名；key = `"<shard>-<variant>"`（6 个分片：chararts-original / chararts-large / chararts-preview / skinpack-original / skinpack-large / skinpack-preview） |
| `artworks` | root | skinId → 艺术品条目的映射 |
| `kind` | artwork | `"base"`（无 `@`）或 `"skin"`（有 `@`） |
| `sinceVersion` | artwork | 该艺术品首次出现的 versionId；客户端据此判断需要哪些 delta |
| `shard` | artwork | `"chararts"` 或 `"skinpack"`；baseline 分片归属 |
| `original`/`large`/`preview` | artwork | 三档变体；每档都包含 `file`/`w`/`h`/`bytes`/`sha256` |
| `file` | variant | 相对于 zip 根的文件名；客户端用此名在本地目录查找 |

### 不在本 schema 中的字段

- `skinName` / `skinGroupName` / `description` / `obtainApproach` — 语义字段，PRTS-MCP 从 gamedata excel 表 join
- `charId` — 可从 skinId 推导（`#`/`@` 前的 charId prefix），或从 skin_table 查
- `drawerList`（画师）— 同上

## 2. 状态生命周期

管线有四个数据位置，各有不同的生命周期：

```
images-cache/          persistent canonical store (跨运行保留)
  *.ab                 AB 包原始字节；hash diff 的基础
  hashes.json          上次成功下载的 bundle hash map

images-out/            per-run staging (每次运行重建)
  *.original.png       Phase A 提取产物
  *.large.png          Phase C 变体产物
  *.preview.png        Phase C 变体产物
  index.json           Phase B+C 产物（全量快照）

images-prev/           previous index (跨运行保留)
  index.json           上一版发布的索引；diff 的基础

dist/                  per-run packaging output
  images-delta-*.zip   delta 包
  index.json           嵌入 delta 的全量索引副本
```

### 2.1 per-run staging (`images-out/`)

每次 `images-extract` 运行时**清空并重建**（已有实现：`for f in out_dir.glob("*.png"): f.unlink()`）。
`images-index` 和 `images-variants` 在此目录上操作，产出 `index.json`。
运行结束后，`images-out/index.json` 是**当前版本的完整快照**。

### 2.2 persistent canonical store (`images-cache/`)

`images-cache/hashes.json` 跨运行保留，是增量 fetch 的基础。
AB 包文件也在缓存中保留，避免重复下载。

### 2.3 previous index (`images-prev/`)

`images-prev/index.json` 是**上一次发布的索引**——即上一次成功运行后从
`images-out/index.json` 拷贝而来。它提供 `compute_delta()` 的 previous 参数。

- 首次运行：`images-prev/` 不存在 → `compute_delta(current, None)` → 全部 added
- 后续运行：`compute_delta(current, prev)` → 只有新增/变化的 skinId 进 delta

在 Phase D 发布成功后，`images-out/index.json` 拷贝到 `images-prev/index.json`。

### 2.4 sinceVersion 语义

`sinceVersion` 记录一个艺术品**首次出现在哪个版本**：

- 首次运行（无 prev）：所有条目 `sinceVersion = currentVersion`
- 后续运行：
  - **added** 集合的条目：`sinceVersion = currentVersion`
  - **unchanged** 的条目：`sinceVersion` 从 prev index 继承
  - **changed** 的条目（sha256 变了）：`sinceVersion` 保持原值（首次出现的版本不变，但文件内容更新了）

客户端据此判断：对于本地 baseline 之后、currentVersion 之前的版本，需要下载哪些 delta。

### 2.5 删除语义

当皮肤从 `skin_table.json` 中移除时：
- 该 skinId 不在当前 `valid_skin_ids` 中
- `images-extract` 不会为其生成 PNG（已被过滤）
- `images-index` 不会为其创建条目
- `compute_delta()` 将其放入 `removed` 集合
- Phase D 的 delta 包**不包含**该文件，但 index 中也**不再有条目**
- 客户端侧：索引中不存在的条目 = 该艺术品不再可用；客户端可以选择保留或删除本地文件

**注意**：干员立绘几乎不会被删除（HG 不会移除已有干员），所以 `removed` 集合
在实践中极少非空。但管线必须正确处理这种情况。

### 2.6 原子写入

- `index.json`：先写临时文件 `index.json.tmp`，然后 `os.replace()` 原子替换
- delta zip：同样先写临时文件再 rename
- `images-prev/index.json`：发布成功后才拷贝，确保失败的运行不会污染 diff 基础

## 3. 变体规范

| 变体 | 最长边上限 | 缩放算法 | 放大策略 |
|---|---|---|---|
| `original` | 无（原图） | 不缩放 | — |
| `large` | 1024 px | Lanczos | 不放大；原图更小时直接复制 |
| `preview` | 256 px | Lanczos | 不放大；原图更小时直接复制 |

三档均为 PNG 格式。WebP 等其他格式留作未来优化（见 issue #4）。

每个变体的元数据必须包含全部五个字段：`file`、`w`、`h`、`bytes`、`sha256`。

## 4. Phase D 对 index 的补充

Phase B/C 产出的 index 不包含 `baselineVersion`、`shards`、`sinceVersion` 和
`schemaVersion`。这些在 Phase D（package/publish）阶段补充：

- `schemaVersion`：固定常量，在打包时注入
- `baselineVersion`：从 `images-prev/` 或 baseline Release tag 读取
- `shards`：baseline 分片文件名，在打包时注入
- `sinceVersion`：从 prev index 继承 + 新增条目设为 currentVersion

Phase D 的 `images-package` 步骤读取 Phase C 的 index，补充这些字段，
注入 `schemaVersion`，然后写入 delta zip。

## 5. baseline 分片实测（2026-08-06, 1362 项）

PNG → ZIP 压缩比 ≈ 0.996（PNG 已内建 DEFLATE，zip 二次压缩收益 < 0.5%）。

| 分片 | 原始大小 | zip 后 | GitHub 2 GB |
|---|---|---|---|
| chararts-original | 1.81 GB | ~1.80 GB | ✅ |
| chararts-large | 0.75 GB | ~0.75 GB | ✅ |
| chararts-preview | 0.07 GB | ~0.07 GB | ✅ |
| skinpack-original | 1.84 GB | ~1.83 GB | ✅ |
| skinpack-large | 0.63 GB | ~0.63 GB | ✅ |
| skinpack-preview | 0.06 GB | ~0.06 GB | ✅ |
| chararts 三档合计 | 2.63 GB | — | ❌ 超 2 GB |
| skinpack 三档合计 | 2.53 GB | — | ❌ 超 2 GB |

结论：必须按 **shard × variant = 6 片**分片，不能按 shard 合并三档。

客户端可以按需下载子集（例如只下 `*-large` + `*-preview`，跳过 `*-original`
以节省 ~3.6 GB 磁盘）。索引的 `shards` 字段列出全部 6 个分片，客户端自行决定
下载哪些。
