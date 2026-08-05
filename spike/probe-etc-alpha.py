#!/usr/bin/env python3
"""Supplemental spike probe: verify ETC_RGB4+separate-alpha compositing and
test two valid recent operators. Also measure Sprite-only extraction (the
useful output, vs Texture2D atlas which has transparent padding).
"""

from __future__ import annotations

import asyncio
import io
import json
import time
from pathlib import Path

import UnityPy
from UnityPy.helpers import CompressionHelper
from UnityPy.enums.BundleFile import CompressionFlags

from arkprts.assets.bundle import decompress_lz4ak, asset_path_to_server_filename, unzip_only_file
from arkprts import network as netn

CompressionHelper.DECOMPRESSION_MAP[CompressionFlags.LZHAM] = decompress_lz4ak

BUNDLES = [
    "chararts/char_199_yak.ab",
    "chararts/char_617_sharp2.ab",
    "skinpack/char_199_yak.ab",
]

FORMAT_NAMES = {34: "ETC_RGB4", 49: "ASTC_RGB_5x5", 50: "ASTC_RGB_6x6"}


async def download_bundle(path: str, server: str = "cn") -> bytes:
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
            return unzip_only_file(await response.read())
    finally:
        await session.session.close()


def analyze(ab_data: bytes, name: str) -> dict:
    env = UnityPy.load(io.BytesIO(ab_data))

    tex2d = []
    sprites = []
    for obj in env.objects:
        if obj.type.name == "Texture2D":
            try:
                d = obj.read()
                fmt_code = getattr(d, "m_TextureFormat", None)
                fmt_code = int(fmt_code) if fmt_code is not None else 0
                img = d.image
                buf = io.BytesIO()
                img.save(buf, format="PNG", optimize=True)
                tex2d.append({
                    "name": d.m_Name,
                    "w": d.m_Width,
                    "h": d.m_Height,
                    "fmt": FORMAT_NAMES.get(fmt_code, f"raw_{fmt_code}"),
                    "png_kb": buf.tell() / 1e3,
                    "bands": img.getbands(),
                })
            except Exception as e:
                tex2d.append({"error": str(e)})
        elif obj.type.name == "Sprite":
            try:
                d = obj.read()
                img = d.image
                buf = io.BytesIO()
                img.save(buf, format="PNG", optimize=True)
                sprites.append({
                    "name": d.m_Name,
                    "w": img.width,
                    "h": img.height,
                    "png_kb": buf.tell() / 1e3,
                    "bands": img.getbands(),
                })
            except Exception as e:
                sprites.append({"error": str(e)})

    # Sort by size desc
    tex2d_valid = [t for t in tex2d if "name" in t]
    tex2d_valid.sort(key=lambda t: -t["png_kb"])
    sprites_valid = [s for s in sprites if "name" in s]
    sprites_valid.sort(key=lambda s: -s["png_kb"])

    return {
        "bundle": name,
        "ab_mb": len(ab_data) / 1e6,
        "tex2d_total_kb": sum(t.get("png_kb", 0) for t in tex2d_valid),
        "sprite_total_kb": sum(s.get("png_kb", 0) for s in sprites_valid),
        "tex2d": tex2d_valid[:10],
        "sprites": sprites_valid[:10],
    }


def main() -> None:
    out = Path("spike/image-output")
    out.mkdir(parents=True, exist_ok=True)

    results = []
    for b in BUNDLES:
        print(f"\nDownloading {b}...")
        try:
            ab = asyncio.run(download_bundle(b))
        except Exception as e:
            print(f"  FAILED: {e}")
            continue
        t0 = time.monotonic()
        r = analyze(ab, b)
        r["elapsed_s"] = round(time.monotonic() - t0, 2)
        print(f"  AB: {r['ab_mb']:.2f} MB")
        print(f"  Texture2D total: {r['tex2d_total_kb']/1e3:.2f} MB")
        print(f"  Sprite total:    {r['sprite_total_kb']/1e3:.2f} MB")
        print(f"  Top Texture2D:")
        for t in r["tex2d"][:5]:
            print(f"    {t['name']:40s} {t['w']:5d}x{t['h']:5d} {t['fmt']:12s} {t['png_kb']:8.0f}KB  bands={t['bands']}")
        print(f"  Top Sprites:")
        for s in r["sprites"][:5]:
            print(f"    {s['name']:40s} {s['w']:5d}x{s['h']:5d} {s['png_kb']:8.0f}KB  bands={s['bands']}")
        results.append(r)

    (out / "spike-data-2.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
