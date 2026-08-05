#!/usr/bin/env python3
"""Phase 3 spike: download art AB bundles and extract Texture2D/Sprite → PNG.

Answers the remaining spike questions from issue #1:
  - Texture format distribution and ASTC/ETC2 decode success rate
  - Sprite vs Texture2D (atlas packing?)
  - PNG output sizes vs AB input sizes
  - Original dimensions → multi-resolution variant strategy
  - Per-bundle extraction timing

Usage: probe-images.py <output_dir>
"""

from __future__ import annotations

import asyncio
import io
import json
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import UnityPy
from UnityPy.helpers import CompressionHelper
from UnityPy.enums.BundleFile import CompressionFlags

# arkprts patches LZHAM decompression; replicate so standalone UnityPy works.
from arkprts.assets.bundle import decompress_lz4ak

CompressionHelper.DECOMPRESSION_MAP[CompressionFlags.LZHAM] = decompress_lz4ak

from arkprts import network as netn
from arkprts.assets.bundle import asset_path_to_server_filename, unzip_only_file

# Representative sample: mix of rarity/era, plus hub bundles.
SAMPLE_BUNDLES = [
    "chararts/char_002_amiya.ab",        # Amiya — flagship, early
    "chararts/char_010_chen.ab",          # Ch'en — early 6★
    "chararts/char_451_robin.ab",         # Robin — mid-era
    "chararts/char_1036_threese.ab",      # Three-Sensitive — recent
    "chararts/char_1040_night2.ab",       # recent 6★
    "skinpack/char_002_amiya.ab",         # Amiya skins
    "skinpack/char_010_chen.ab",          # Ch'en skins
    "arts/charavatars/avatar_hub.ab",     # all avatars (atlas hub)
    "arts/charportraits/portraits_hub.ab",  # all portraits (atlas hub)
]


@dataclass
class TextureInfo:
    name: str
    type: str          # Texture2D / Sprite
    width: int
    height: int
    texture_format: str
    has_alpha: bool
    png_bytes: int
    source: str        # which bundle


@dataclass
class BundleResult:
    bundle_name: str
    ab_bytes: int
    elapsed_s: float
    textures: list[TextureInfo] = field(default_factory=list)
    failed: list[dict] = field(default_factory=list)
    other_objects: Counter = field(default_factory=Counter)


async def download_bundle(path: str, server: str = "cn") -> bytes:
    """Download and unzip a single AB bundle from HG CDN."""
    session = netn.NetworkSession(default_server=server)
    try:
        platform = session.default_platform or "Android"
        if not session.versions[(server, platform)]:
            await session.load_version_config(server, platform)
        url = (
            session.domains[server]["hu"]
            + f"/{platform}/assets/{session.versions[(server, platform)]['resVersion']}/"
            + asset_path_to_server_filename(path)
        )
        async with session.session.get(url) as response:
            response.raise_for_status()
            zipped = await response.read()
        return unzip_only_file(zipped)
    finally:
        await session.session.close()


def extract_textures(ab_data: bytes, bundle_name: str) -> BundleResult:
    """Load AB with UnityPy, extract every Texture2D/Sprite as PNG."""
    result = BundleResult(
        bundle_name=bundle_name,
        ab_bytes=len(ab_data),
        elapsed_s=0.0,
    )
    t0 = time.monotonic()

    env = UnityPy.load(io.BytesIO(ab_data))

    for obj in env.objects:
        result.other_objects[obj.type.name] += 1

        if obj.type.name in ("Texture2D", "Sprite"):
            try:
                data = obj.read()
                # Sprite wraps a Texture2D; get the underlying texture
                if obj.type.name == "Sprite":
                    tex = data.m_RD.texture if hasattr(data, "m_RD") else data.read_typetree()
                    if hasattr(tex, "read"):
                        tex = tex.read()
                    img = data.image
                    name = data.m_Name or f"sprite_{obj.path_id}"
                else:
                    img = data.image
                    name = data.m_Name or f"tex_{obj.path_id}"

                fmt_name = getattr(data, "m_TextureFormat", None)
                fmt_str = str(fmt_name).split(".")[-1] if fmt_name else "unknown"

                # Get original dimensions
                w = getattr(data, "m_Width", img.width)
                h = getattr(data, "m_Height", img.height)

                has_alpha = "A" in img.getbands()

                buf = io.BytesIO()
                img.save(buf, format="PNG", optimize=True)
                png_size = buf.tell()

                result.textures.append(TextureInfo(
                    name=name,
                    type=obj.type.name,
                    width=w,
                    height=h,
                    texture_format=fmt_str,
                    has_alpha=has_alpha,
                    png_bytes=png_size,
                    source=bundle_name,
                ))
            except Exception as e:
                result.failed.append({
                    "type": obj.type.name,
                    "path_id": str(obj.path_id),
                    "error": f"{type(e).__name__}: {e}",
                })

    result.elapsed_s = time.monotonic() - t0
    return result


def main() -> int:
    out_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "spike/image-output")
    out_dir.mkdir(parents=True, exist_ok=True)

    all_results: list[BundleResult] = []

    for bundle_path in SAMPLE_BUNDLES:
        print(f"\n{'='*60}")
        print(f"Downloading {bundle_path}...")
        try:
            ab_data = asyncio.run(download_bundle(bundle_path))
        except Exception as e:
            print(f"  DOWNLOAD FAILED: {type(e).__name__}: {e}")
            all_results.append(BundleResult(
                bundle_name=bundle_path, ab_bytes=0, elapsed_s=0,
                failed=[{"error": f"download: {e}"}],
            ))
            continue
        print(f"  AB size: {len(ab_data)/1e6:.2f} MB")

        print(f"  Extracting textures...")
        result = extract_textures(ab_data, bundle_path)
        print(f"  {len(result.textures)} textures, {len(result.failed)} failures, {result.elapsed_s:.1f}s")
        print(f"  Object types: {dict(result.other_objects.most_common())}")

        # Save PNGs for manual inspection (first 5 per bundle)
        bundle_dir = out_dir / bundle_path.replace("/", "_").replace(".ab", "")
        bundle_dir.mkdir(parents=True, exist_ok=True)
        saved = 0
        env = UnityPy.load(io.BytesIO(ab_data))
        for obj in env.objects:
            if obj.type.name in ("Texture2D", "Sprite") and saved < 10:
                try:
                    data = obj.read()
                    img = data.image if obj.type.name == "Texture2D" else data.image
                    name = (data.m_Name or f"obj_{obj.path_id}").replace("/", "_")
                    img.save(bundle_dir / f"{name}.png")
                    saved += 1
                except Exception:
                    pass

        all_results.append(result)

    # ---- Summary report ----
    print(f"\n{'='*60}")
    print("SPIKE SUMMARY")
    print(f"{'='*60}\n")

    all_tex = [t for r in all_results for t in r.textures]
    all_failed = [f for r in all_results for f in r.failed]
    total_ab = sum(r.ab_bytes for r in all_results)
    total_png = sum(t.png_bytes for t in all_tex)

    print(f"Bundles tested:       {len(all_results)}")
    print(f"AB bytes downloaded:  {total_ab/1e6:.1f} MB")
    print(f"Textures extracted:   {len(all_tex)}")
    print(f"PNG total size:       {total_png/1e6:.1f} MB")
    print(f"Failures:             {len(all_failed)}")

    if all_tex:
        widths = sorted(t.width for t in all_tex)
        heights = sorted(t.height for t in all_tex)
        sizes = sorted(t.png_bytes for t in all_tex)
        print(f"\nDimensions (px):")
        print(f"  width  range {widths[0]}-{widths[-1]}, median {widths[len(widths)//2]}")
        print(f"  height range {heights[0]}-{heights[-1]}, median {heights[len(heights)//2]}")
        print(f"\nPNG size per texture:")
        print(f"  min {sizes[0]/1e3:.0f}KB  P50 {sizes[len(sizes)//2]/1e3:.0f}KB  P95 {sizes[int(len(sizes)*0.95)]/1e3:.0f}KB  max {sizes[-1]/1e3:.0f}KB")

        print(f"\nTexture format distribution:")
        fmt_counts = Counter(t.texture_format for t in all_tex)
        for fmt, cnt in fmt_counts.most_common():
            print(f"  {fmt:30s} {cnt}")

        print(f"\nType distribution:")
        type_counts = Counter(t.type for t in all_tex)
        for typ, cnt in type_counts.most_common():
            print(f"  {typ:15s} {cnt}")

        alpha_count = sum(1 for t in all_tex if t.has_alpha)
        print(f"\nHas alpha channel: {alpha_count}/{len(all_tex)}")

    if all_failed:
        print(f"\nFailures breakdown:")
        err_counts = Counter(
            f["type"] if "type" in f else "download"
            for f in all_failed
        )
        for typ, cnt in err_counts.most_common():
            print(f"  {typ}: {cnt}")
        sample_errs = all_failed[:5]
        for e in sample_errs:
            print(f"    {e}")

    # Per-bundle breakdown
    print(f"\nPer-bundle:")
    print(f"  {'Bundle':45s} {'AB MB':>8s} {'#tex':>5s} {'PNG MB':>8s} {'ratio':>6s} {'fail':>5s}")
    for r in all_results:
        png_mb = sum(t.png_bytes for t in r.textures) / 1e6
        ratio = (png_mb / (r.ab_bytes / 1e6)) if r.ab_bytes else 0
        print(f"  {r.bundle_name:45s} {r.ab_bytes/1e6:8.2f} {len(r.textures):5d} {png_mb:8.2f} {ratio:6.2f} {len(r.failed):5d}")

    # Save full data as JSON for the report
    report_data = {
        "bundles": [
            {
                "name": r.bundle_name,
                "ab_bytes": r.ab_bytes,
                "elapsed_s": round(r.elapsed_s, 2),
                "texture_count": len(r.textures),
                "png_total_bytes": sum(t.png_bytes for t in r.textures),
                "failure_count": len(r.failed),
                "object_types": dict(r.other_objects),
                "failures": r.failed,
            }
            for r in all_results
        ],
        "textures": [
            {
                "name": t.name,
                "type": t.type,
                "width": t.width,
                "height": t.height,
                "format": t.texture_format,
                "has_alpha": t.has_alpha,
                "png_bytes": t.png_bytes,
                "source": t.source,
            }
            for t in all_tex
        ],
        "summary": {
            "total_bundles": len(all_results),
            "total_ab_bytes": total_ab,
            "total_png_bytes": total_png,
            "total_textures": len(all_tex),
            "total_failures": len(all_failed),
        },
    }
    (out_dir / "spike-data.json").write_text(
        json.dumps(report_data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"\nFull data saved to {out_dir / 'spike-data.json'}")

    return 0 if not all_failed else 1


if __name__ == "__main__":
    sys.exit(main())
