#!/usr/bin/env python3
import os
import sys
import signal
import subprocess
from pathlib import Path
from typing import List

MEDIA_EXTS = {
    ".mp3", ".aac", ".m4a", ".flac", ".wav", ".ogg", ".opus",
    ".mp4", ".mkv", ".mov", ".webm"
}

def list_media_files(folder: Path) -> List[Path]:
    files = []
    for p in folder.iterdir():
        if p.is_file() and p.suffix.lower() in MEDIA_EXTS:
            files.append(p)
    return sorted(files, key=lambda x: x.name.lower())

def write_concat_list(files: List[Path], out_path: Path) -> None:
    # ffmpeg concat demuxer format
    lines = []
    for f in files:
        fp = str(f.resolve()).replace("'", r"'\''")
        lines.append(f"file '{fp}'")
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

def main():
    # Put files in ./audio (recommended) OR change to Path.cwd() to use current directory
    audio_dir = Path(os.environ.get("AUDIO_DIR", "./audio")).resolve()

    srt_url = os.environ.get("SRT_URL", "").strip()
    if not srt_url:
        print("Set SRT_URL env var, e.g.")
        print("  export SRT_URL='srt://openradio.live:9000?mode=caller&transtype=live&streamid=live&passphrase=...&pbkeylen=32'")
        sys.exit(1)

    files = list_media_files(audio_dir)
    if not files:
        print(f"No media files found in: {audio_dir}")
        sys.exit(1)

    concat_list = (audio_dir / "playlist.concat.txt")
    write_concat_list(files, concat_list)

    # One long-lived ffmpeg process, loops forever, “realtime” pacing, outputs MPEG-TS over SRT
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "info",

        "-re",                      # pace input in realtime-ish
        "-stream_loop", "-1",       # loop forever

        "-f", "concat",
        "-safe", "0",
        "-i", str(concat_list),

        "-vn",                      # audio-only
        "-c:a", "aac",
        "-b:a", os.environ.get("AUDIO_BR", "128k"),
        "-ar", os.environ.get("AUDIO_AR", "48000"),

        # MPEG-TS is the common “transport” for SRT streaming
        "-f", "mpegts",
        "-mpegts_service_type", "digital_tv",
        "-muxdelay", "0",
        "-muxpreload", "0",

        srt_url
    ]

    print("Launching ffmpeg:\n  " + " ".join(cmd))
    proc = subprocess.Popen(cmd)

    def shutdown(*_):
        try:
            proc.terminate()
        except Exception:
            pass
        try:
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    code = proc.wait()
    sys.exit(code if code is not None else 1)

if __name__ == "__main__":
    main()
