#!/usr/bin/env python3
"""Export fixed-theme/fixed-detail SVGs; optionally render PNG and ICO assets.

The SVG-only path uses the Python standard library. Raster exports additionally
require CairoSVG and Pillow in the export environment, not in the application.
"""
from __future__ import annotations

import argparse
import io
import json
from pathlib import Path
from typing import Literal
from xml.etree import ElementTree as ET

Theme = Literal["light", "dark"]
NS = "http://www.w3.org/2000/svg"
ET.register_namespace("", NS)
ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "app" / "static" / "brand" / "frog"
SIZES = (16, 24, 32, 48, 64, 96, 128, 180, 192, 256, 512, 1024)
COLORS = {
    "light": {"surface": "#F4F7FA", "edge": "#12304B", "circuit": "#B58105", "secondary": "#8299AE"},
    "dark": {"surface": "#081F34", "edge": "#FFD12B", "circuit": "#FFD12B", "secondary": "#52758F"},
}


def fixed_svg(source: Path, theme: Theme, size: int) -> bytes:
    """Resolve our controlled CSS classes into portable presentation attributes."""
    if theme not in COLORS or size < 1:
        raise ValueError("Expected theme 'light'/'dark' and a positive size")
    tree = ET.fromstring(source.read_bytes())
    tree.set("width", str(size))
    tree.set("height", str(size))
    for parent in list(tree.iter()):
        for element in list(parent):
            tag = element.tag.rsplit("}", 1)[-1]
            classes = set(element.get("class", "").split())
            discard = (
                tag == "style"
                or element.get("id") in {"light", "dark"}
                or (bool(classes & {"circuit", "secondary"}) and size < 64)
                or ("full" in classes and size < 32)
                or ("micro" in classes and size >= 32)
            )
            if discard:
                parent.remove(element)
                continue
            for name, color in COLORS[theme].items():
                if name in classes:
                    element.set("fill" if name == "surface" else "stroke", color)
            if "edge" in classes and size < 32:
                element.set("stroke-width", "12")
            element.attrib.pop("class", None)
    for element in tree.iter():
        if element.tag == f"{{{NS}}}desc":
            element.text = f"Yellow-banded poison dart frog; fixed {theme} theme, {size}-pixel optical detail."
    return ET.tostring(tree, encoding="utf-8", xml_declaration=True) + b"\n"


def export(output: Path, *, raster: bool = False) -> list[Path]:
    """Write deterministic filenames beneath output; return the files written."""
    if raster:
        try:
            import cairosvg
            from PIL import Image
        except ImportError as exc:
            raise RuntimeError("PNG/ICO exports require CairoSVG and Pillow; SVG exports need neither.") from exc
    written: list[Path] = []
    svg_dir = output / "svg"
    svg_dir.mkdir(parents=True, exist_ok=True)
    for theme in ("light", "dark"):
        for name in ("frog", "app-icon"):
            for detail, size in (("primary", 512), ("compact", 48), ("micro", 24)):
                path = svg_dir / f"{name}-{theme}-{detail}.svg"
                path.write_bytes(fixed_svg(SOURCE / f"{name}.svg", theme, size))
                written.append(path)
        path = svg_dir / f"favicon-{theme}.svg"
        path.write_bytes(fixed_svg(SOURCE / "favicon.svg", theme, 32))
        written.append(path)
        if not raster:
            continue
        png_dir = output / "png"
        png_dir.mkdir(parents=True, exist_ok=True)
        for name in ("frog", "app-icon"):
            for size in SIZES:
                path = png_dir / f"{name}-{theme}-{size}.png"
                cairosvg.svg2png(bytestring=fixed_svg(SOURCE / f"{name}.svg", theme, size), write_to=str(path))
                written.append(path)
        # ICO uses the micro glyph at every embedded size, not the full-body mark.
        frames = []
        for size in (16, 32, 48):
            rendered = cairosvg.svg2png(bytestring=fixed_svg(SOURCE / "favicon.svg", theme, size))
            with Image.open(io.BytesIO(rendered)) as image:
                frames.append(image.convert("RGBA"))
        path = output / f"favicon-{theme}.ico"
        frames[-1].save(path, format="ICO", sizes=[(16, 16), (32, 32), (48, 48)], append_images=frames[:-1])
        written.append(path)
    # These manifests are export artifacts, not enabled app configuration.
    if raster:
        for theme in ("light", "dark"):
            manifest = {
                "name": "Data Mover", "short_name": "Data Mover",
                "background_color": COLORS[theme]["surface"],
                "theme_color": COLORS[theme]["surface"],
                "icons": [{"src": f"png/app-icon-{theme}-{size}.png", "sizes": f"{size}x{size}", "type": "image/png", "purpose": "any"} for size in (192, 512)],
            }
            path = output / f"manifest-{theme}.webmanifest"
            path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
            written.append(path)
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=SOURCE / "exports")
    parser.add_argument("--png", action="store_true", help="Also export PNGs, ICOs, and example manifests")
    args = parser.parse_args()
    try:
        files = export(args.output, raster=args.png)
    except (OSError, ET.ParseError, ValueError, RuntimeError) as exc:
        parser.exit(1, f"Export failed: {exc}\n")
    print(f"Exported {len(files)} files to {args.output}")


if __name__ == "__main__":
    main()
