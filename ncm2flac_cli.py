#!/usr/bin/env python3
"""
NCM2FLAC CLI — Command-line interface for universal audio conversion and AI vocal separation.

Usage:
  python ncm2flac_cli.py <input> [options]

Examples:
  python ncm2flac_cli.py song.flac                                    # Convert to MP3 (default)
  python ncm2flac_cli.py song.mp3 --format flac                        # Convert to FLAC
  python ncm2flac_cli.py song.mp3 --separate no_vocals                 # Extract accompaniment
  python ncm2flac_cli.py song.flac --format mp3 --separate both        # MP3 + both stems
  python ncm2flac_cli.py song.ncm --format flac --output ./out         # NCM decrypt + FLAC
"""

import sys
import argparse
from pathlib import Path

# ── CLI helper (no PyQt5 dependency) ──

def cli_log(msg: str):
    print(f"  {msg}")

def main():
    parser = argparse.ArgumentParser(
        description="NCM2FLAC — Universal Audio Converter + AI Vocal Separation",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Formats: NCM, WAV, FLAC, MP3, M4A, OGG, WMA, AAC, Opus, APE, WV, AIFF, AIF
Separation: no_vocals (伴奏), vocals (人声), both (两轨)
        """,
    )
    parser.add_argument("input", type=str, help="Input audio file path")
    parser.add_argument("--output", "-o", type=str, default=None,
                        help="Output directory (default: same as input)")
    parser.add_argument("--format", "-f", type=str, default="mp3",
                        choices=["flac", "mp3"], help="Output format (default: mp3)")
    parser.add_argument("--quality", "-q", type=str, default="320",
                        choices=["320", "192", "128"], help="MP3 bitrate in kbps (default: 320)")
    parser.add_argument("--separate", "-s", type=str, default=None,
                        choices=["vocals", "no_vocals", "both"],
                        help="AI vocal separation mode (requires Demucs)")
    
    args = parser.parse_args()

    # Lazy import — only import heavy deps when CLI actually runs
    sys.path.insert(0, str(Path(__file__).parent))
    from ncm2flac_gui import find_ffmpeg, convert_audio_file, run_demucs

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"ERROR: File not found: {input_path}")
        sys.exit(1)

    # Determine output directory
    if args.output:
        out_dir = Path(args.output)
    else:
        out_dir = input_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)

    # Check ffmpeg
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        print("ERROR: ffmpeg not found. Install ffmpeg or run from bundled EXE.")
        sys.exit(1)

    stem = input_path.stem
    fmt = args.format.upper()

    # ── Step 1: Convert ──
    if args.separate:
        # For separation, convert to WAV first (demucs needs uncompressed)
        tmp_wav = out_dir / f"{stem}_tmp.wav"
        print(f"Converting: {input_path.name} -> {tmp_wav.name} (WAV, for separation)")
        convert_audio_file(str(input_path), str(tmp_wav), "WAV", args.quality, cli_log)
        work_file = tmp_wav
    else:
        out_ext = {".flac": ".flac", ".mp3": ".mp3"}[f".{fmt.lower()}"]
        out_path = out_dir / f"{stem}{out_ext}"
        print(f"Converting: {input_path.name} -> {out_path.name} ({fmt})")
        convert_audio_file(str(input_path), str(out_path), fmt, args.quality, cli_log)
        print(f"Done: {out_path}")
        sys.exit(0)

    # ── Step 2: Separate ──
    sep_dir = out_dir / f"{stem}_separated"
    sep_dir.mkdir(parents=True, exist_ok=True)
    print(f"Separating vocals: {work_file.name}...")
    result = run_demucs(str(work_file), str(sep_dir), cli_log)

    # Cleanup tmp WAV
    if work_file.exists():
        work_file.unlink()

    if not result:
        print("ERROR: Vocal separation failed.")
        sys.exit(1)

    # ── Step 3: Process results ──
    ext = ".flac" if args.format == "flac" else ".mp3"

    if args.separate in ("vocals", "both"):
        v_in = result.get("vocals")
        if v_in:
            v_out = out_dir / f"{stem}_vocals{ext}"
            print(f"Converting vocals: -> {v_out.name} ({fmt})")
            convert_audio_file(v_in, str(v_out), fmt, args.quality, cli_log)

    if args.separate in ("no_vocals", "both"):
        nv_in = result.get("no_vocals")
        if nv_in:
            nv_out = out_dir / f"{stem}_no_vocals{ext}"
            print(f"Converting accompaniment: -> {nv_out.name} ({fmt})")
            convert_audio_file(nv_in, str(nv_out), fmt, args.quality, cli_log)

    print("\nAll done!")


if __name__ == "__main__":
    main()
