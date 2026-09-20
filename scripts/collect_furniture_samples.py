"""Read-only fixture collection. Images and sheet data stay in ignored .local/."""

import argparse
import json
import os
import time
from pathlib import Path

import dotenv
import requests

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=30)
    parser.add_argument("--output", type=Path, default=ROOT / ".local/samples")
    args = parser.parse_args()
    dotenv.load_dotenv(ROOT / ".env")
    token = next(
        (
            os.environ.get(k)
            for k in ("DISCORD_TEAM_TOKEN", "DISCORD_TOKEN", "TOKEN")
            if os.environ.get(k)
        ),
        None,
    )
    if not token:
        raise SystemExit("Discord bot token is not configured")
    args.output.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    session.headers["Authorization"] = "Bot " + token
    params = {"limit": 100}
    collected = []
    for _ in range(10):
        response = session.get(
            "https://discord.com/api/v10/channels/1290587266695036958/messages",
            params=params,
            timeout=30,
        )
        if response.status_code != 200:
            raise SystemExit(f"Discord history HTTP {response.status_code}")
        messages = response.json()
        if not messages:
            break
        for message in messages:
            attachments = [
                a
                for a in message.get("attachments", [])
                if a.get("content_type", "").startswith("image/")
            ]
            content = message.get("content", "")
            if not attachments or not any(
                x in content for x in ("家具：", "雑貨：", "装飾：", "内観・外観：")
            ):
                continue
            directory = args.output / message["id"]
            directory.mkdir(exist_ok=True)
            record = {"id": message["id"], "content": content, "images": []}
            for a in attachments:
                # Do not send the Discord Authorization header to the CDN.
                path = directory / (a["id"] + Path(a["filename"]).suffix.lower())
                if not path.exists():
                    download = requests.get(a["url"], timeout=45)
                    download.raise_for_status()
                    path.write_bytes(download.content)
                record["images"].append(
                    {"id": a["id"], "file": str(path.relative_to(args.output))}
                )
            collected.append(record)
            if len(collected) >= args.count:
                break
        if len(collected) >= args.count:
            break
        params["before"] = messages[-1]["id"]
        time.sleep(0.5)
    (args.output / "manifest.json").write_text(
        json.dumps(collected, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        f"Collected {len(collected)} posts / {sum(len(p['images']) for p in collected)} images"
    )


if __name__ == "__main__":
    main()
