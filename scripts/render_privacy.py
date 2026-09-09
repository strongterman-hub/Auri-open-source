"""Render the public policy from the same JSON document bundled with Android."""
import argparse
import html
import json
from pathlib import Path


def render(document: dict) -> str:
    esc = html.escape
    sections = []
    for section in document["sections"]:
        links = "".join(
            f'<p><a href="{esc(link["url"], quote=True)}" rel="noopener noreferrer">{esc(link["label"])}</a></p>'
            for link in section.get("links", [])
        )
        sections.append(f'<section><h2>{esc(section["title"])}</h2><p>{esc(section["body"])}</p>{links}</section>')
    return f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="theme-color" content="#12101f"><title>Auri 隐私政策</title>
<link rel="icon" href="/static/site/assets/auri-icon.png"><link rel="stylesheet" href="/static/site/privacy.css?v={esc(document['version'])}"></head>
<body><header><a href="/">Auri</a><nav><a href="/download">下载应用</a><a href="mailto:strongterman@gmail.com">联系我们</a></nav></header>
<main><h1>隐私政策</h1><p class="meta">版本 {esc(document['version'])} · 生效日期 {esc(document['effective_date'])}</p>
<p>{esc(document['introduction'])}</p>{''.join(sections)}</main>
<footer><a href="/download">返回下载页面</a> · © 2026 Auri</footer></body></html>
'''


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--document", type=Path, required=True)
    parser.add_argument("--site", type=Path, required=True)
    args = parser.parse_args()
    document = json.loads(args.document.read_text(encoding="utf-8"))
    args.site.mkdir(parents=True, exist_ok=True)
    (args.site / "privacy.html").write_text(render(document), encoding="utf-8")
    (args.site / "privacy-policy.json").write_bytes(args.document.read_bytes())
