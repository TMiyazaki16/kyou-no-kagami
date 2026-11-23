#!/usr/bin/env python3
"""verify-app 用：全画面のスクリーンショットとコンソールエラーの集計（Playwright・Python）。

使い方:
  python screenshots.py --base http://127.0.0.1:5173 --out /tmp/shots --nav frontend/src/navigation.ts

- 画面の一覧は navigation.ts の NAV（label）から読む。下部タブに無い画面は「その他」シートから開く
- デモアカウント（公開済みの認証情報）でログインする。サーバー・DB の用意は SKILL.md の 3-2 を参照
- アプリの依存ではない。検品用の別 venv に playwright を入れて実行する
"""
import argparse
import os
import re
import sys

from playwright.sync_api import sync_playwright

DEMO_EMAIL = "haha@demo.local"
DEMO_PASSWORD = "demo-mama-2026"


def read_nav(path):
    src = open(path, encoding="utf-8").read()
    m = re.search(r"export const NAV[^=]*=\s*\[(.*?)\];", src, re.S)
    if not m:
        sys.exit(f"NAV が見つからない: {path}")
    labels = re.findall(r'label:\s*"([^"]+)"', m.group(1))
    more = re.search(r'MORE_LABEL\s*=\s*"([^"]+)"', src)
    return labels, (more.group(1) if more else "その他")


def go_to(page, label, more_label):
    direct = page.locator("nav").get_by_role("button", name=label, exact=True)
    if direct.count():
        direct.first.click()
        return
    page.get_by_role("button", name=more_label, exact=True).click()
    page.wait_for_timeout(300)
    page.get_by_role("button", name=label, exact=True).last.click()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:5173")
    ap.add_argument("--out", required=True)
    ap.add_argument("--nav", default="frontend/src/navigation.ts")
    args = ap.parse_args()

    labels, more_label = read_nav(args.nav)
    os.makedirs(args.out, exist_ok=True)
    errors = []

    with sync_playwright() as p:
        browser = p.chromium.launch()
        for width, suffix in ((390, ""), (1280, "-desktop")):
            ctx = browser.new_context(viewport={"width": width, "height": 844},
                                      device_scale_factor=2, locale="ja-JP")
            page = ctx.new_page()
            page.on("pageerror", lambda e, s=suffix: errors.append(f"pageerror{s}: {e}"))
            page.on("console", lambda m, s=suffix: m.type == "error" and errors.append(f"console{s}: {m.text}"))
            page.goto(args.base, wait_until="networkidle")
            page.evaluate("localStorage.clear()")
            page.reload(wait_until="networkidle")
            page.screenshot(path=f"{args.out}/00-login{suffix}.png", full_page=True)
            page.fill("#email", DEMO_EMAIL)
            page.fill("#password", DEMO_PASSWORD)
            page.locator("form button[type=submit]").click()
            page.wait_for_timeout(1200)
            for i, label in enumerate(labels, start=1):
                try:
                    go_to(page, label, more_label)
                    page.wait_for_timeout(900)
                    name = f"{i:02d}-{label}{suffix}.png"
                    page.screenshot(path=f"{args.out}/{name}", full_page=True)
                    print("shot", name)
                except Exception as e:  # 1 画面の失敗で全体を止めない
                    errors.append(f"navigate{suffix}: {label}: {e}")
            ctx.close()
        browser.close()

    print(f"screens: {len(labels)} × 2 widths -> {args.out}")
    print(f"errors: {len(errors)}")
    for e in errors:
        print("  " + e)
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
