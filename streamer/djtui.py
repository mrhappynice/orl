#!/usr/bin/env python3
import os
import sys
import subprocess
import json
import curses
import platform
from pathlib import Path
from typing import List, Dict, Optional

# --- Configuration & Constants ---

CONFIG_FILE = Path("stream_config.json")
MEDIA_EXTS = {
    ".mp3", ".aac", ".m4a", ".flac", ".wav", ".ogg", ".opus",
    ".mp4", ".mkv", ".mov", ".webm"
}

DEFAULT_CONFIG = {
    "srt_url": "",
    "audio_bitrate": "128k",
    "audio_rate": "48000",
    "selected_sources": [],  # List of IDs / names depending on driver; we normalize to names on Linux
    "audio_dir": str(Path.cwd() / "audio")
}

# --- Data Logic ---

class ConfigManager:
    def __init__(self):
        self.data = DEFAULT_CONFIG.copy()
        self.load()

    def load(self):
        if CONFIG_FILE.exists():
            try:
                content = json.loads(CONFIG_FILE.read_text())
                self.data.update(content)
            except Exception:
                pass

        # Ensure audio dir exists
        Path(self.data["audio_dir"]).mkdir(parents=True, exist_ok=True)

        # Normalize legacy config (Linux): if selected_sources contains "default" only, keep it.
        # If it contains numeric IDs from pactl output, we will map them at runtime.

    def save(self):
        CONFIG_FILE.write_text(json.dumps(self.data, indent=2))

    def get(self, key):
        return self.data.get(key)

    def set(self, key, value):
        self.data[key] = value
        self.save()

cfg = ConfigManager()

def get_local_files(folder_str: str) -> List[Path]:
    folder = Path(folder_str)
    if not folder.exists():
        return []
    files = []
    for p in folder.iterdir():
        if p.is_file() and p.suffix.lower() in MEDIA_EXTS:
            files.append(p)
    return sorted(files, key=lambda x: x.name.lower())

def write_concat_list(files: List[Path], out_path: Path) -> None:
    lines = []
    for f in files:
        fp = str(f.resolve()).replace("'", r"'\''")
        lines.append(f"file '{fp}'")
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

# --- Pulse/PipeWire autodetection (Linux) ---

def _run_cmd(cmd: List[str]) -> str:
    return subprocess.check_output(cmd, text=True, stderr=subprocess.STDOUT).strip()

def pactl_available() -> bool:
    try:
        subprocess.check_call(["pactl", "--version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except Exception:
        return False

def list_pactl_sources() -> List[Dict]:
    """
    Returns list of sources from: pactl list short sources
    Each item:
      {
        'pactl_id': '54',
        'name': 'alsa_input....',
        'driver': 'pulse',
        'kind': 'mic' | 'monitor' | 'other',
        'pretty': '...'
      }
    """
    out = _run_cmd(["pactl", "list", "short", "sources"])
    sources = []
    for line in out.splitlines():
        # Fields are tab-separated in "short" outputs.
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        sid = parts[0].strip()
        name = parts[1].strip()
        kind = "monitor" if name.endswith(".monitor") else ("mic" if name.startswith("alsa_input.") else "other")
        pretty = name
        if name.endswith(".monitor"):
            pretty = f"Desktop Audio (monitor) - {name}"
        elif name.startswith("alsa_input."):
            pretty = f"Mic / Input - {name}"
        sources.append({
            "pactl_id": sid,
            "name": name,
            "driver": "pulse",
            "kind": kind,
            "pretty": pretty
        })
    return sources

def get_default_source_name() -> Optional[str]:
    # Prefer get-default-source if available
    try:
        return _run_cmd(["pactl", "get-default-source"])
    except Exception:
        pass
    # Fallback parse pactl info
    try:
        info = _run_cmd(["pactl", "info"])
        for line in info.splitlines():
            if line.lower().startswith("default source:"):
                return line.split(":", 1)[1].strip()
    except Exception:
        return None
    return None

def get_default_sink_name() -> Optional[str]:
    try:
        return _run_cmd(["pactl", "get-default-sink"])
    except Exception:
        pass
    try:
        info = _run_cmd(["pactl", "info"])
        for line in info.splitlines():
            if line.lower().startswith("default sink:"):
                return line.split(":", 1)[1].strip()
    except Exception:
        return None
    return None

def autodetect_linux_sources(pactl_sources: List[Dict]) -> Dict[str, Optional[str]]:
    """
    Returns:
      { 'mic': <source name>, 'desktop': <monitor name> }
    """
    mic = None
    desktop = None

    default_source = get_default_source_name()
    default_sink = get_default_sink_name()

    # Mic: prefer default source if it's not a monitor
    if default_source and not default_source.endswith(".monitor"):
        if any(s["name"] == default_source for s in pactl_sources):
            mic = default_source

    if mic is None:
        # Prefer alsa_input.* first, then any non-monitor
        non_monitor = [s["name"] for s in pactl_sources if not s["name"].endswith(".monitor")]
        mic = next((n for n in non_monitor if n.startswith("alsa_input.")), None) or (non_monitor[0] if non_monitor else None)

    # Desktop: default sink's monitor if present
    if default_sink:
        candidate = default_sink + ".monitor"
        if any(s["name"] == candidate for s in pactl_sources):
            desktop = candidate

    if desktop is None:
        monitors = [s["name"] for s in pactl_sources if s["name"].endswith(".monitor")]
        desktop = next((m for m in monitors if m.startswith("alsa_output.")), None) or (monitors[0] if monitors else None)

    return {"mic": mic, "desktop": desktop}

# --- Device discovery for UI ---

def detect_audio_devices() -> List[Dict]:
    """
    Returns list of dicts:
      {
        'id': 'playlist' | <source name> | <special>,
        'name': display name,
        'driver': 'concat' | 'pulse' | ...
      }
    On Linux: uses pactl for real device/source names + autodetected defaults.
    """
    system = platform.system()
    devices: List[Dict] = []

    # Always add the File Playlist option
    devices.append({
        "id": "playlist",
        "name": f"[Internal] Local Playlist (from {os.path.basename(cfg.get('audio_dir'))}/)",
        "driver": "concat"
    })

    if system == "Linux":
        if pactl_available():
            try:
                sources = list_pactl_sources()
            except Exception:
                sources = []

            # Add “Auto” presets up top (these are convenience selectors)
            auto = autodetect_linux_sources(sources) if sources else {"mic": None, "desktop": None}

            if auto.get("mic"):
                devices.append({
                    "id": auto["mic"],
                    "name": f"[Auto] Default Mic: {auto['mic']}",
                    "driver": "pulse"
                })
            if auto.get("desktop"):
                devices.append({
                    "id": auto["desktop"],
                    "name": f"[Auto] Default Desktop Audio: {auto['desktop']}",
                    "driver": "pulse"
                })

            # Add all discovered sources
            if sources:
                devices.append({"id": "__sep__", "name": "---- Pulse/PipeWire Sources ----", "driver": ""})
                for s in sources:
                    devices.append({
                        "id": s["name"],         # IMPORTANT: store the NAME, not numeric pactl id
                        "name": s["pretty"],
                        "driver": "pulse"
                    })
            else:
                # Fallback: allow "default" capture source if pactl listing fails
                devices.append({"id": "default", "name": "[System] Default Input (Pulse)", "driver": "pulse"})
        else:
            # No pactl: fallback to ALSA default (user can custom hw: later)
            devices.append({"id": "default", "name": "[System] Default Input (ALSA/Pulse unknown)", "driver": "pulse"})

    elif system == "Darwin":  # macOS
        devices.append({"id": ":0", "name": "[System] Default Input", "driver": "avfoundation"})

    elif system == "Windows":
        # dshow names vary; manual entry
        devices.append({"id": "default", "name": "[System] Default Input (manual on Windows)", "driver": "dshow"})

    return devices

# --- FFmpeg command builder ---

def build_ffmpeg_command(selected_sources: List[Dict]) -> List[str]:
    """
    Builds ffmpeg command to mix 1+ audio inputs and stream to SRT.
    Inputs can include:
      - playlist (concat list)
      - pulse sources (mic/monitor)
      - manual custom strings
    """
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "warning",
        "-stats",
        "-re"
    ]

    filter_complex: List[str] = []
    input_count = 0

    # 1) Add inputs
    for src in selected_sources:
        sid = src.get("id", "")
        if sid == "__sep__":
            continue

        if sid == "playlist":
            audio_dir = Path(cfg.get("audio_dir"))
            files = get_local_files(str(audio_dir))
            if not files:
                raise ValueError("No files found in audio directory")

            concat_path = audio_dir / "playlist.concat.txt"
            write_concat_list(files, concat_path)

            cmd.extend([
                "-stream_loop", "-1",
                "-f", "concat",
                "-safe", "0",
                "-i", str(concat_path)
            ])
        else:
            driver = src.get("driver") or ("pulse" if platform.system() == "Linux" else "avfoundation")
            cmd.extend(["-f", driver, "-i", sid])

        input_count += 1

    if input_count == 0:
        raise ValueError("No inputs selected.")

    # 2) Mix if multiple inputs
    if input_count > 1:
        mix_inputs = ""
        for i in range(input_count):
            filter_complex.append(f"[{i}:a]volume=1.0[a{i}]")
            mix_inputs += f"[a{i}]"

        filter_complex.append(
            f"{mix_inputs}amix=inputs={input_count}:duration=longest:dropout_transition=2[mixed]"
        )
        cmd.extend(["-filter_complex", ";".join(filter_complex)])
        cmd.extend(["-map", "[mixed]"])
        cmd.extend(["-vn"])
    else:
        # Single input
        cmd.extend(["-vn"])

    # 3) Output
    cmd.extend([
        "-c:a", "aac",
        "-b:a", cfg.get("audio_bitrate"),
        "-ar", cfg.get("audio_rate"),

        "-f", "mpegts",
        "-mpegts_service_type", "digital_tv",
        "-muxdelay", "0",
        "-muxpreload", "0",

        cfg.get("srt_url")
    ])

    return cmd

# --- TUI Logic (Curses) ---

def draw_menu(stdscr, current_row, options):
    stdscr.clear()
    h, w = stdscr.getmaxyx()

    title = " STREAMING CONTROL CENTER "
    stdscr.addstr(1, w // 2 - len(title) // 2, title, curses.A_BOLD | curses.A_REVERSE)

    srt_url = cfg.get("srt_url")
    srt_status = srt_url if srt_url else "[NOT SET]"
    stdscr.addstr(3, 2, f"Target: {srt_status[:w - 15]}")

    sources = cfg.get("selected_sources")
    src_display = ", ".join(sources) if sources else "[NONE]"
    stdscr.addstr(4, 2, f"Inputs: {src_display[:w - 15]}")

    for idx, row in enumerate(options):
        x = w // 2 - len(row) // 2
        y = h // 2 - len(options) // 2 + idx
        if idx == current_row:
            stdscr.attron(curses.color_pair(1))
            stdscr.addstr(y, x, row)
            stdscr.attroff(curses.color_pair(1))
        else:
            stdscr.addstr(y, x, row)

    stdscr.refresh()

def input_screen(stdscr, prompt, key_name):
    curses.echo()
    stdscr.clear()
    h, w = stdscr.getmaxyx()
    stdscr.addstr(h // 2 - 2, 2, prompt)

    current_val = cfg.get(key_name)
    stdscr.addstr(h // 2 - 1, 2, f"Current: {current_val}")
    stdscr.addstr(h // 2 + 1, 2, "> ")

    val = stdscr.getstr(h // 2 + 1, 4).decode("utf-8").strip()
    curses.noecho()

    if val:
        cfg.set(key_name, val)

def source_selection_screen(stdscr):
    devices = detect_audio_devices()
    # Add custom option at the end
    devices.append({"id": "custom", "name": "Add Custom Device Manually...", "driver": "pulse"})

    current_row = 0
    selected_ids = set(cfg.get("selected_sources") or [])

    while True:
        stdscr.clear()
        h, w = stdscr.getmaxyx()
        stdscr.addstr(1, 2, "SELECT AUDIO SOURCES (Space=toggle, Enter=confirm):", curses.A_BOLD)
        stdscr.addstr(2, 2, "Tip: On Linux, select PipeWire sources by NAME (e.g. ...monitor, ...mono-fallback).")

        for idx, dev in enumerate(devices):
            y = 4 + idx
            if y >= h - 2:
                break

            if dev["id"] == "__sep__":
                stdscr.addstr(y, 2, dev["name"], curses.A_DIM)
                continue

            prefix = "[x]" if dev["id"] in selected_ids else "[ ]"
            display_str = f"{prefix} {dev['name']}"
            display_str = display_str[: max(0, w - 4)]

            if idx == current_row:
                stdscr.attron(curses.color_pair(1))
                stdscr.addstr(y, 2, display_str)
                stdscr.attroff(curses.color_pair(1))
            else:
                stdscr.addstr(y, 2, display_str)

        key = stdscr.getch()

        if key == curses.KEY_UP and current_row > 0:
            current_row -= 1
        elif key == curses.KEY_DOWN and current_row < len(devices) - 1:
            current_row += 1
        elif key == ord(" "):
            dev_id = devices[current_row]["id"]

            if dev_id == "__sep__":
                continue

            if dev_id == "custom":
                curses.echo()
                stdscr.addstr(h - 2, 2, "Enter device string (Linux: pulse source name; ALSA: hw:0,0): ")
                stdscr.clrtoeol()
                custom_id = stdscr.getstr(h - 2, 70).decode("utf-8").strip()
                curses.noecho()
                if custom_id:
                    if custom_id in selected_ids:
                        selected_ids.remove(custom_id)
                    else:
                        selected_ids.add(custom_id)
            else:
                if dev_id in selected_ids:
                    selected_ids.remove(dev_id)
                else:
                    selected_ids.add(dev_id)
        elif key == 10:  # Enter
            break

    cfg.set("selected_sources", list(selected_ids))

def run_stream_screen(stdscr):
    stdscr.clear()
    selected_ids = cfg.get("selected_sources") or []
    url = cfg.get("srt_url")

    if not url:
        stdscr.addstr(2, 2, "Error: No SRT URL configured!")
        stdscr.getch()
        return
    if not selected_ids:
        stdscr.addstr(2, 2, "Error: No sources selected!")
        stdscr.getch()
        return

    detected = detect_audio_devices()

    # Helper map: id->device record (for driver)
    dev_map = {d["id"]: d for d in detected if d.get("id") and d["id"] not in ("__sep__", "custom")}

    final_sources = []
    for sid in selected_ids:
        if sid in dev_map:
            final_sources.append(dev_map[sid])
        else:
            # Unknown/custom: assume pulse on Linux, avfoundation on mac, else user-provided
            sys_driver = "pulse" if platform.system() == "Linux" else ("avfoundation" if platform.system() == "Darwin" else "dshow")
            # If user entered hw:... we should use alsa driver on Linux
            if platform.system() == "Linux" and (sid.startswith("hw:") or sid.startswith("plughw:")):
                sys_driver = "alsa"
            final_sources.append({"id": sid, "driver": sys_driver})

    try:
        cmd = build_ffmpeg_command(final_sources)
    except Exception as e:
        stdscr.addstr(2, 2, f"Error building command: {e}")
        stdscr.getch()
        return

    stdscr.addstr(2, 2, "Stream Initializing...", curses.A_BOLD)
    stdscr.addstr(4, 2, "Press 'q' or Ctrl+C to stop streaming.")
    stdscr.addstr(6, 2, "FFmpeg Command:")
    cmd_str = " ".join(cmd)
    for i in range(0, len(cmd_str), 80):
        stdscr.addstr(7 + i // 80, 4, cmd_str[i:i + 80])

    stdscr.refresh()

    curses.endwin()

    print("\n" + "=" * 50)
    print("STREAMING ACTIVE - CHECK LOGS BELOW")
    print("=" * 50 + "\n")
    print(f"Command: {' '.join(cmd)}\n")

    try:
        proc = subprocess.Popen(cmd)
        proc.wait()
    except KeyboardInterrupt:
        print("\nStopping stream...")
        try:
            proc.terminate()
            proc.wait(timeout=2)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    input("\nStream ended. Press Enter to return to menu...")

def main(stdscr):
    curses.curs_set(0)
    curses.init_pair(1, curses.COLOR_BLACK, curses.COLOR_CYAN)

    options = [
        "Select Audio Sources (Files/Mic/Desktop)",
        "Set SRT URL",
        "Set Audio Bitrate",
        "START STREAMING",
        "Exit"
    ]
    current_row = 0

    while True:
        draw_menu(stdscr, current_row, options)
        key = stdscr.getch()

        if key == curses.KEY_UP and current_row > 0:
            current_row -= 1
        elif key == curses.KEY_DOWN and current_row < len(options) - 1:
            current_row += 1
        elif key == 10:  # Enter
            if current_row == 0:
                source_selection_screen(stdscr)
            elif current_row == 1:
                input_screen(stdscr, "Enter SRT URL (srt://...):", "srt_url")
            elif current_row == 2:
                input_screen(stdscr, "Enter Bitrate (e.g. 128k, 256k):", "audio_bitrate")
            elif current_row == 3:
                run_stream_screen(stdscr)
            elif current_row == 4:
                break

if __name__ == "__main__":
    if platform.system() == "Windows":
        try:
            import curses  # noqa: F401
        except ImportError:
            print("On Windows, please install: pip install windows-curses")
            sys.exit(1)

    try:
        curses.wrapper(main)
    except KeyboardInterrupt:
        sys.exit(0)
