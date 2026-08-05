# Phase 3 Spike 报告：干员立绘/皮肤图片提取

日期：2026-08-05 · 关联：issue #1（服务 PRTS-MCP #85）

## 结论

**技术链路完全可行，零提取失败。** 9 个样本 AB 包（5 个 chararts + 3 个
skinpack + 2 个 hub）全部成功解包，91 个纹理 100% 解码。ASTC 和 ETC_RGB4
（含分离 alpha）两种格式均被 UnityPy 正确处理。

**全量覆盖假设成立**——454 名干员全部有对应的 `chararts` 包，无需累积。
图片产物线应作为独立管线运行，与现有 JSON 累积管线零耦合。

**分发必须走对象存储**——Sprite-only 估算 ~2.8 GB，多分辨率变体后 ~4 GB，
超过 GitHub Release 单 asset 2 GB 上限。GitHub Release 仍承载 JSON 数据
+ 图片索引；图片本身走 R2/S3，索引放 URL。

## 必答清单

### Q1：hot_update_list 艺术包命名分布

| 类别 | 命名模式 | 包数 | totalSize |
|---|---|---|---|
| `chararts/` | `chararts/char_<id>.ab` | 465 | 988 MB |
| `skinpack/` | `skinpack/char_<id>.ab` | 412 | 1,107 MB |
| `charpack/` | `charpack/char_<id>.ab` | 458 | **8.4 MB**（数据/atlas，非纹理） |
| `arts/charavatars/` | `avatar_hub.ab` | 1 | 0.05 MB |
| `arts/charportraits/` | `portraits_hub.ab` | 1 | 0.03 MB |
| `arts/dynchars/` | 动态/Live2D 角色 | 97 | — |

`charpack` 包仅 8.4 MB / 458 个包——这些是 MonoBehaviour/网格数据，不含
纹理，提取时**跳过**。

`avatar_hub.ab` / `portraits_hub.ab` 是 manifest 包（仅含 MonoBehaviour +
AssetBundle），不含实际纹理数据。头像和半身像纹理已在各自的 `chararts` 包
内（命名 `char_<id>` 无后缀，256–512 px）。

### Q2：全量覆盖假设——成立

> `character_table.json` 中 454 个干员 ID → **454/454 全部有 `chararts` 包**。

与 levels/story（HG 会从 hot_update_list 移除旧活动）根本不同，干员艺术包
长期保留。管线可以每次**全量重取、无需 baseline 合并**。

⚠️ 注意：hot_update_list 是权威来源，不能从 character_table 反推包名——
spike 中两个猜测的 ID（`char_1036_threese`、`char_1040_night2`）返回 404，
实际不存在。管线必须枚举 `hot_update_list.abInfos` 中的 `chararts/*` 和
`skinpack/*` 条目。

### Q3：UnityPy 提取链路——100% 成功

| 项 | 结果 |
|---|---|
| 测试包数 | 8（5 chararts + 3 skinpack，跨早期/中期/近期干员） |
| 提取纹理数 | 91 Texture2D + 20 Sprite = 111 |
| 解码失败 | **0** |
| ASTC 5x5 / 6x6 | ✅ 内建 alpha |
| ETC_RGB4 | ✅ 分离 alpha 被 UnityPy 自动合成 |

### Q4：每类别体积估算

基于样本的 PNG/AB 膨胀比（~3.5–4.6×）和 Sprite/Texture2D 比（~0.77×）：

| 类别 | AB totalSize | Sprite-only PNG 估算 | 含 atlas PNG 估算 |
|---|---|---|---|
| chararts (465 包) | 988 MB | **~1.2 GB** | ~3.5 GB |
| skinpack (412 包) | 1,107 MB | **~1.6 GB** | ~4.0 GB |
| 合计 | 2,095 MB | **~2.8 GB** | ~7.5 GB |

### Q5：多分辨率变体——AB 内无内建 1x/2x 变体

原始纹理为单一分辨率，无内建缩略图。尺寸分布（Sprite，即裁剪后实际立绘）：

| 统计 | 宽 (px) | 高 (px) | PNG 大小 |
|---|---|---|---|
| min | 636 | 1019 | 295 KB |
| P50 | ~1024 | ~1280 | ~600 KB |
| P95 | ~2048 | ~2048 | ~3500 KB |
| max | 2444 | 2444 | ~5700 KB |

主立绘（E2 精英化）通常 1300–2444 px；E0/E1 立绘通常 760–1400 px；
头像/建筑小人 256–512 px。

需要自行生成 `original` / `large` / `preview` 三档：
- `original`：Sprite 原始尺寸 PNG（直接提取产物）
- `large`：缩放到 max-1024px（适合 MCP 传输、客户端展示）
- `preview`：缩放到 max-256px（列表/预览）

三档总量估算：original ~2.8 GB + large ~0.5 GB + preview ~0.05 GB ≈ **~3.4 GB**。

## 纹理命名约定

从样本中归纳（需在索引生成步骤中程序化解析）：

```
chararts/char_<id>.ab 内的纹理：
  char_<id>            → 头像/半身像（256–512px，ETC_RGB4）
  char_<id>_1          → E0/E1 基础立绘
  char_<id>_1+         → E1 增强变体（部分干员）
  char_<id>_2          → E2 精英化立绘（最大）
  char_<id>_2b         → E2 建筑/小人 sprite
  build_char_<id>      → 基建小人（~300–440px）
  char_<id>[alpha]     → ETC 纹理的分离 alpha 通道（中间产物，不输出）

skinpack/char_<id>.ab 内的纹理：
  char_<id>_<skinId>   → 皮肤立绘（skinId 对应 skin_table.json）
  char_<id>_<skinId>b  → 皮肤建筑/小人 sprite
  build_char_<id>_<skinId> → 基建小人
```

skin_table.json 的 `charSkins` key 使用 `@` 分隔符（`char_002_amiya@epoque#4`），
AB 纹理名使用 `_`（`char_002_amiya_epoque#4`）。索引步骤需做 `@` → `_` 映射。

每名干员的皮肤数 = skinpack 包内纹理组数。skin_table.json 共 2,321 条皮肤。

## 对架构决策的输入

### 1. 管线形态：独立、全量、无累积

```
images-fetch    从 HG CDN 下载 chararts/* + skinpack/* AB 包（全量，不增量）
images-extract  UnityPy → Sprite PNG（跳过 Texture2D atlas 和 charpack）
images-variants 生成 original / large / preview 三档
images-index    从 skin_table.json + 提取产物生成索引 JSON
images-publish  PNG → 对象存储；索引 → GitHub Release
```

与现有 JSON 管线（`fetch`/`merge`/`validate`/`package`/`publish`）**零耦合**。
图片提取失败不阻塞 JSON 发布；JSON 校验失败不阻塞图片发布。

### 2. 分发：对象存储（R2/S3）

- chararts + skinpack Sprite-only ~2.8 GB；三档变体 ~3.4 GB
- 超过 GitHub Release 单 asset 2 GB 上限
- **GitHub Release 承载**：JSON 数据（现有四 asset）+ 图片索引 JSON（新 asset）
- **对象存储承载**：所有 PNG 文件，索引中放稳定 URL
- 索引用 `skin:<skinId>` 作为 artwork_id，记录每个变体的宽/高/MIME/大小/SHA-256/URL

### 3. 下载策略

- `check` 短路逻辑共用：versionId 未变 → 跳过图片管线
- versionId 变化 → 全量重取所有 chararts + skinpack 包（不依赖 diff）
- 877 个包 × ~12s 提取 ≈ 3 小时（可并发下载串行提取，或限并发提取）

### 4. 性能

单包提取耗时 5–20s（CPU 密集，主要是 ASTC 解码）。全量 877 包估算：
- 串行：~3 小时
- 并发下载（IO bound）+ 串行提取（CPU bound）：~2.5 小时
- 限并发提取（4 workers）：~50 分钟

GHA runner 可承受，但需要关注超时（默认 6h）。

## 未覆盖项（需手动验证）

以下项超出 akdp 侧 spike 范围，需 PRTS-MCP 侧或人工验证：

1. **PNG vs WebP 体积/质量对比**——技术简单（Pillow `save(format="WEBP")`），
   但透明通道的视觉质量需人工评审。建议在实现阶段加入变体生成步骤后顺带验证。
2. **不同分辨率的视觉差异**（512/1024/1536/2048）——需人工对比高细节立绘。
3. **PRTS-MCP Python/TS/RikkaHub 的实际传输上限**——只能在 #85 实现时实测。
4. **对象存储选型（R2 vs S3 vs 其他）的成本对比**——取决于流量预期。

## 样本产物

PNG 样本保存在 `spike/image-output/`（每包前 10 张），可供视觉检查。
完整提取数据在 `spike/image-output/spike-data.json` 和 `spike-data-2.json`。

## Phase 3 实现建议

1. 先实现 `images-fetch` + `images-extract`，产出 original PNG + 原始索引。
2. 验证索引覆盖率：skin_table.json 每个皮肤条目都有对应的提取产物。
3. 加入 `images-variants`（large/preview 缩放）。
4. 最后对接对象存储和 PRTS-MCP 契约。
5. 每步独立可运行、独立可校验，不触碰现有管线代码。
