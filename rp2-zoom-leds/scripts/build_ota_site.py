#!/usr/bin/env python3
import argparse
import datetime as dt
import hashlib
import html
import json
import os
import shutil
from pathlib import Path
from urllib.parse import quote


EXCLUDED_DEVICE_FILES = {"secrets.py", "secrets.example.py"}


def discover_device_files(device_dir):
    return [
        path
        for path in sorted(Path(device_dir).glob("*.py"))
        if path.name not in EXCLUDED_DEVICE_FILES
    ]


def build_ota_site(device_dir, output_dir, base_url, version):
    device_dir = Path(device_dir)
    output_dir = Path(output_dir)
    base_url = base_url.rstrip("/")
    version_path = safe_path_part(version)
    generated_at = utc_now()

    if output_dir.exists():
        shutil.rmtree(output_dir)

    firmware_dir = output_dir / "firmware" / version_path
    firmware_dir.mkdir(parents=True)

    files = []
    for source in discover_device_files(device_dir):
        data = source.read_bytes()
        destination = firmware_dir / source.name
        destination.write_bytes(data)

        files.append(
            {
                "path": source.name,
                "size": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "url": "%s/firmware/%s/%s"
                % (base_url, quote(version_path), quote(source.name)),
            }
        )

    manifest = {
        "schema": 1,
        "version": version,
        "generated_at": generated_at,
        "files": files,
        "reset": True,
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (output_dir / "index.html").write_text(
        render_index(version, generated_at, files),
        encoding="utf-8",
    )
    return manifest


def render_index(version, generated_at, files):
    rows = "\n".join(
        "<li><code>%s</code> <small>%s bytes</small></li>"
        % (html.escape(item["path"]), item["size"])
        for item in files
    )
    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Pico W OTA Firmware</title>
</head>
<body>
  <h1>Pico W OTA Firmware</h1>
  <p>Version <code>{version}</code></p>
  <p>Generated <time>{generated_at}</time></p>
  <p>Manifest: <a href="manifest.json">manifest.json</a></p>
  <ul>
{rows}
  </ul>
</body>
</html>
""".format(
        version=html.escape(version),
        generated_at=html.escape(generated_at),
        rows=rows,
    )


def safe_path_part(value):
    value = str(value)
    safe = []
    for char in value:
        if char.isalnum() or char in "._-":
            safe.append(char)
        else:
            safe.append("-")
    return "".join(safe).strip(".") or "dev"


def utc_now():
    return (
        dt.datetime.now(dt.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def default_version():
    return os.environ.get("GITHUB_SHA") or utc_now()


def parse_args():
    parser = argparse.ArgumentParser(
        description="Build the static OTA manifest/site for Pico W app files.",
    )
    parser.add_argument("--device-dir", default="device")
    parser.add_argument("--output", default="build/ota-site")
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--version", default=default_version())
    return parser.parse_args()


def main():
    args = parse_args()
    manifest = build_ota_site(
        device_dir=args.device_dir,
        output_dir=args.output,
        base_url=args.base_url,
        version=args.version,
    )
    print(
        "Built OTA manifest version %s with %s files"
        % (manifest["version"], len(manifest["files"]))
    )


if __name__ == "__main__":
    main()
