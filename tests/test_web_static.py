from __future__ import annotations

import re
from pathlib import Path

WEB = Path(__file__).resolve().parents[1] / "app" / "web"


def read(name: str) -> str:
    return (WEB / name).read_text(encoding="utf-8")


def test_required_files_exist():
    for name in ("index.html", "app.js", "styles.css", "manifest.webmanifest"):
        assert (WEB / name).exists(), name


def test_viewport_and_manifest():
    html = read("index.html")
    assert 'name="viewport"' in html
    assert "width=device-width" in html
    assert "viewport-fit=cover" in html
    assert 'rel="manifest"' in html
    assert 'lang="zh-CN"' in html


def test_responsive_breakpoints_present():
    css = read("styles.css")
    assert "@media (max-width: 1023px)" in css
    assert "@media (max-width: 767px)" in css
    assert "100dvh" in css
    assert "env(safe-area-inset-bottom)" in css
    assert "grid-template-columns" in css


def test_no_fixed_page_width():
    css = read("styles.css")
    # 不允许出现给页面主体写死像素宽度（如 width: 1200px）
    assert not re.search(r"\bbody\s*\{[^}]*width:\s*\d+px", css, re.S)
    assert not re.search(r"\.app\s*\{[^}]*width:\s*\d+px", css, re.S)


def test_touch_targets_and_input_font():
    css = read("styles.css")
    assert "min-height: 44px" in css
    assert "font-size: 16px" in css


def test_stream_uses_fetch_reader():
    js = read("app.js")
    assert "ReadableStream" not in js or "getReader" in js
    assert "getReader()" in js
    assert "/api/chat/stream" in js
    assert "EventSource" not in js


def test_aria_live_region():
    html = read("index.html")
    assert 'aria-live="polite"' in html
