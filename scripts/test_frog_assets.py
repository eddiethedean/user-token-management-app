#!/usr/bin/env python3
"""Run isolated SVG asset/export checks without loading the application."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from xml.etree import ElementTree as ET

from export_frog_assets import COLORS, NS, SOURCE, export, fixed_svg


class FrogAssetTests(unittest.TestCase):
    def test_sources_are_self_contained_vectors(self) -> None:
        for name in ("frog.svg", "app-icon.svg", "favicon.svg", "frog-mark.svg"):
            with self.subTest(name=name):
                root = ET.parse(SOURCE / name).getroot()
                self.assertIn("viewBox", root.attrib)
                self.assertGreater(len(root.findall(f".//{{{NS}}}path")), 0)
                self.assertIsNotNone(root.find(f"{{{NS}}}title"))
                ids = [e.get("id") for e in root.iter() if e.get("id")]
                self.assertEqual(len(ids), len(set(ids)))
                for element in root.iter():
                    self.assertNotIn(element.tag.rsplit("}", 1)[-1], {"script", "image", "foreignObject"})
                    for key, value in element.attrib.items():
                        self.assertFalse(key.lower().startswith("on"))
                        if key.rsplit("}", 1)[-1] == "href":
                            self.assertTrue(value.startswith("#"))

    def test_fixed_exports_resolve_themes_and_remove_css(self) -> None:
        for theme in ("light", "dark"):
            for name in ("frog", "app-icon", "favicon"):
                with self.subTest(theme=theme, name=name):
                    root = ET.fromstring(fixed_svg(SOURCE / f"{name}.svg", theme, 512))
                    self.assertEqual(root.get("width"), "512")
                    self.assertEqual(root.get("height"), "512")
                    self.assertEqual(root.findall(f".//{{{NS}}}style"), [])
                    self.assertTrue(any(e.get("fill") == COLORS[theme]["surface"] for e in root.iter()))
                    self.assertFalse(any(e.get("class") for e in root.iter()))

    def test_optical_sizes_change_geometry(self) -> None:
        for name in ("frog", "app-icon"):
            shapes = []
            for size in (24, 48, 512):
                root = ET.fromstring(fixed_svg(SOURCE / f"{name}.svg", "light", size))
                shapes.append([e.get("d") for e in root.findall(f".//{{{NS}}}path")])
            self.assertNotEqual(shapes[0], shapes[1])
            self.assertNotEqual(shapes[1], shapes[2])
            self.assertLess(len(shapes[0]), len(shapes[2]))

    def test_favicon_keeps_same_glyph_at_all_sizes(self) -> None:
        glyphs = []
        for size in (16, 32, 48, 512):
            root = ET.fromstring(fixed_svg(SOURCE / "favicon.svg", "dark", size))
            glyphs.append([e.get("d") for e in root.findall(f".//{{{NS}}}path")])
        self.assertTrue(all(g == glyphs[0] for g in glyphs))

    def test_svg_export_inventory_and_determinism(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)
            files = export(output)
            self.assertEqual(len(files), 14)
            before = {p.relative_to(output): p.read_bytes() for p in files}
            sentinel = output / "unrelated.txt"
            sentinel.write_text("preserve me", encoding="utf-8")
            after = {p.relative_to(output): p.read_bytes() for p in export(output)}
            self.assertEqual(before, after)
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "preserve me")
            for content in after.values():
                ET.fromstring(content)

    def test_invalid_size_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            fixed_svg(SOURCE / "frog.svg", "light", 0)


if __name__ == "__main__":
    unittest.main()
