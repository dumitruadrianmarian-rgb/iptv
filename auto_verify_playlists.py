#!/usr/bin/env python3
"""Daily automated IPTV playlist verifier with 3-day failure threshold and GitHub auto-push."""

import argparse
import concurrent.futures
import datetime
import json
import os
import subprocess
from pathlib import Path

from check_all_streams import parse_m3u, test_channel

REPO_DIR = Path(__file__).resolve().parent
PLAYLISTS = ["romania.m3u", "international.m3u"]
HEALTH_STATE_FILE = REPO_DIR / "stream_health.json"
REPORT_FILE = REPO_DIR / "health_report.md"
MAX_CONSECUTIVE_FAILS = 3


def load_health_state() -> dict:
    if HEALTH_STATE_FILE.exists():
        try:
            return json.loads(HEALTH_STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"failures": {}}


def remove_urls_from_m3u(m3u_path: Path, urls_to_remove: set) -> int:
    if not urls_to_remove:
        return 0
    lines = m3u_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    new_lines = []
    block = []
    removed_count = 0

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#EXTINF:") or stripped.startswith("#EXTVLCOPT:"):
            block.append(line)
        elif stripped.startswith("http://") or stripped.startswith("https://"):
            if stripped in urls_to_remove:
                removed_count += 1
            else:
                new_lines.extend(block)
                new_lines.append(line)
            block = []
        else:
            if block:
                new_lines.extend(block)
                block = []
            new_lines.append(line)

    if block:
        new_lines.extend(block)

    m3u_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    return removed_count


def verify_playlist(m3u_name: str, state: dict, now_str: str) -> dict:
    m3u_path = REPO_DIR / m3u_name
    channels = parse_m3u(str(m3u_path))

    failed = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as executor:
        results = list(executor.map(test_channel, channels))

    failed_by_url = {}
    for res in results:
        if res is not None and res.get("error"):
            failed.append(res)
            failed_by_url[res["url"]] = res

    failures = state.setdefault("failures", {})
    urls_to_remove = set()
    warnings = []
    removed = []

    for ch in channels:
        url = ch["url"]
        key = f"{m3u_name}::{url}"
        if url in failed_by_url:
            err = failed_by_url[url]["error"]
            entry = failures.get(key, {"name": ch["name"], "url": url, "playlist": m3u_name, "count": 0})
            entry["count"] += 1
            entry["last_error"] = err
            entry["last_checked"] = now_str
            entry["name"] = ch["name"]
            if entry["count"] >= MAX_CONSECUTIVE_FAILS:
                urls_to_remove.add(url)
                removed.append({"name": ch["name"], "url": url, "error": err, "count": entry["count"]})
                failures.pop(key, None)
            else:
                failures[key] = entry
                warnings.append({"name": ch["name"], "url": url, "error": err, "count": entry["count"]})
        else:
            failures.pop(key, None)

    actually_removed = remove_urls_from_m3u(m3u_path, urls_to_remove)
    return {
        "playlist": m3u_name,
        "total": len(channels),
        "ok": len(channels) - len(failed),
        "failed": len(failed),
        "warnings": warnings,
        "removed": removed,
        "removed_count": actually_removed,
    }


def write_report(summaries: list, now_str: str) -> None:
    lines = [
        "# IPTV Playlist Health Report",
        "",
        f"Last checked: **{now_str}** (prag auto-eliminare: {MAX_CONSECUTIVE_FAILS} zile consecutive)",
        "",
    ]
    for s in summaries:
        lines.append(f"## `{s['playlist']}`")
        lines.append(f"- Total verificate: **{s['total']}** | OK: **{s['ok']}** | Eșuate: **{s['failed']}** | Eliminate azi: **{s['removed_count']}**")
        if s["warnings"]:
            lines.append("- **Avertismente (sub prag):**")
            for w in s["warnings"]:
                lines.append(f"  - {w['name']} ({w['count']}/{MAX_CONSECUTIVE_FAILS}): `{w['error']}`")
        if s["removed"]:
            lines.append("- **Eliminate automat:**")
            for r in s["removed"]:
                lines.append(f"  - {r['name']} ({r['count']}/{MAX_CONSECUTIVE_FAILS}): `{r['error']}`")
        lines.append("")

    REPORT_FILE.write_text("\n".join(lines), encoding="utf-8")


def git_commit_and_push(now_str: str) -> None:
    status = subprocess.check_output(["git", "-C", str(REPO_DIR), "status", "--porcelain"], text=True).strip()
    if not status:
        print("No changes to commit.")
        return
    subprocess.run(["git", "-C", str(REPO_DIR), "add", "romania.m3u", "international.m3u", "stream_health.json", "health_report.md"], check=True)
    subprocess.run(["git", "-C", str(REPO_DIR), "commit", "-m", f"Auto-verify IPTV playlists ({now_str})"], check=True)
    subprocess.run(["git", "-C", str(REPO_DIR), "push", "origin", "main"], check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify IPTV playlists and auto-clean dead streams.")
    parser.add_argument("--no-push", action="store_true", help="Skip git commit and push")
    args = parser.parse_args()

    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    state = load_health_state()
    summaries = [verify_playlist(p, state, now_str) for p in PLAYLISTS]

    HEALTH_STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    write_report(summaries, now_str)

    for s in summaries:
        print(f"[{s['playlist']}] total={s['total']} ok={s['ok']} failed={s['failed']} removed={s['removed_count']}")

    if not args.no_push:
        git_commit_and_push(now_str)


if __name__ == "__main__":
    main()
