"""Write the UI as one self-contained HTML file with the dev data built in.

    uv run python scripts/export_ui_preview.py --out reports/ui-preview.html

The file opens in any browser with no server, so the UI can be shared before
anything is deployed. It holds synthetic dev data only. Overrides made in it
last until the page is reloaded. Pass --fragment for a file without the
<html> and <head> wrapper, for hosts that add their own.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from copilot.config import get_settings
from copilot.web.app import STATIC, page
from copilot.web.data import snapshot


def preview_settings() -> dict:
    s = get_settings()
    return {
        "model": s.llm_model,
        "provider": s.llm_model.split("/", 1)[0],
        "api_base": None,
        "api_key_set": False,  # a preview never says anything about a real key
        "model_configured": False,
        "spend_cap_usd": s.spend_cap_usd,
        "rate_limit_per_minute": s.rate_limit_per_minute,
    }


def build(fragment: bool = False, limit: int | None = None) -> str:
    data = json.dumps(snapshot(limit), separators=(",", ":")).replace("</", "<\\/")
    settings = json.dumps(preview_settings())
    style = f"<style>\n{(STATIC / 'app.css').read_text(encoding='utf-8')}</style>"
    script = (
        f"<script>window.PREVIEW_DATA={data};window.PREVIEW_SETTINGS={settings};</script>\n"
        f"<script>\n{(STATIC / 'app.js').read_text(encoding='utf-8')}</script>"
    )
    if fragment:
        shell = (STATIC / "shell.html").read_text(encoding="utf-8")
        return shell.replace("<!--STYLE-->", style).replace("<!--SCRIPT-->", script)
    return page(style, script)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=Path("reports/ui-preview.html"))
    parser.add_argument("--fragment", action="store_true")
    parser.add_argument("--limit", type=int, help="Only the most recent N tickets.")
    args = parser.parse_args()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(build(args.fragment, args.limit), encoding="utf-8")
    print(f"Wrote {args.out} ({args.out.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
