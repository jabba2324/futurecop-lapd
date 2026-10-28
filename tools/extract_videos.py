#!/usr/bin/env python3
"""Decode Future Cop: L.A.P.D. FMV movies (.WVE, plus the EA_LOGO.FSV logo) to Ogg Theora for Godot.

EA WVE container:
  VLC0        custom MDEC quantisation tables
  au00/au01   EA ADPCM audio (22050 Hz stereo)
  MDEC        PS1 BS v2 video frames, 320 x 192 @ 15 fps

Decoding is done by jPSXdec (which has a dedicated EA video module) to MJPEG/PCM
AVI, then ffmpeg re-encodes to Theora/Vorbis, the only video format Godot 4 plays
natively (VideoStreamTheora). jPSXdec v2.1-beta is downloaded on first use from its
GitHub release, checked against a pinned SHA-256, and cached in
$XDG_CACHE_HOME/fc-lapd/ (default ~/.cache/fc-lapd/); set JPSXDEC_JAR to use a local
jpsxdec.jar instead.

Input: MOVIES/*.WVE and EA_LOGO.FSV in the extracted PS1 disc (iff_utils.ps1_dir()).
EA_LOGO.FSV in the disc root is the same EA video format (320 x 224, 74 frames).

Output: parsed/video/<NAME>.ogv (one per movie). Pass --avi to also keep the
intermediate AVI files next to them.

jPSXdec CLI note: the index-file flag is -x (older docs say -id). The index can't be
skipped: -f FILE -x IDX builds it, then -x IDX -i 0 decodes item 0.

Requires: java (any recent JRE; tested with 25) and ffmpeg built with
libtheora + libvorbis. Set JAVA=/path/to/java to override the java binary.
"""

import glob
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib.iff_utils import ps1_dir

_JPSXDEC_URL = "https://github.com/m35/jpsxdec/releases/download/v2.1/jpsxdec_v2.1-beta.zip"
_JPSXDEC_SHA256 = "e11787a2e05b6a4b6ee07972439e50da9e6798f0ca505e748f4969b3d91cd45b"
# jpsxdec.jar needs jpsxdec-lib.jar next to it.
_JPSXDEC_JARS = ("jpsxdec_v2.1-beta/jpsxdec.jar", "jpsxdec_v2.1-beta/jpsxdec-lib.jar")


def _jpsxdec_jar():
    """Path to jpsxdec.jar: $JPSXDEC_JAR, else the cached (downloaded on first use) release."""
    if os.environ.get("JPSXDEC_JAR"):
        return os.environ["JPSXDEC_JAR"]
    cache = os.path.join(os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"),
                         "fc-lapd", "jpsxdec_v2.1-beta")
    jar = os.path.join(cache, "jpsxdec.jar")
    if os.path.exists(jar):
        return jar
    print(f"Downloading jPSXdec from {_JPSXDEC_URL} …")
    with urllib.request.urlopen(_JPSXDEC_URL) as r:
        data = r.read()
    if hashlib.sha256(data).hexdigest() != _JPSXDEC_SHA256:
        raise RuntimeError("jPSXdec download does not match the pinned SHA-256")
    os.makedirs(cache, exist_ok=True)
    with tempfile.TemporaryFile() as tmp:
        tmp.write(data)
        with zipfile.ZipFile(tmp) as z:
            for member in _JPSXDEC_JARS:
                with open(os.path.join(cache, os.path.basename(member)), "wb") as out:
                    out.write(z.read(member))
    return jar


def _run(cmd, cwd=None):
    res = subprocess.run(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"{cmd[0]} failed:\n{res.stdout[-2000:]}")
    return res.stdout


def _decode(java, jar, wve, out_dir, keep_avi):
    name = os.path.splitext(os.path.basename(wve))[0]
    with tempfile.TemporaryDirectory() as tmp:
        idx = os.path.join(tmp, "index.idx")
        # Run jPSXdec inside the temp dir: it drops debug00.log/index.log in its cwd.
        _run([java, "-jar", jar, "-f", wve, "-x", idx], cwd=tmp)
        # Item 0 is the only item in every Future Cop WVE.
        _run([java, "-jar", jar, "-x", idx, "-i", "0", "-vf", "avi:mjpg", "-dir", tmp], cwd=tmp)
        avis = glob.glob(os.path.join(tmp, "*.avi"))
        if not avis:
            raise RuntimeError(f"jPSXdec produced no AVI for {wve}")
        ogv = os.path.join(out_dir, name + ".ogv")
        _run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", avis[0],
              "-c:v", "libtheora", "-q:v", "8", "-c:a", "libvorbis", "-q:a", "5", ogv])
        if keep_avi:
            shutil.copy(avis[0], os.path.join(out_dir, name + ".avi"))
    return ogv


def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.dirname(script_dir)
    os.chdir(repo_root)
    java = os.environ.get("JAVA", "java")
    keep_avi = "--avi" in sys.argv[1:]

    for tool in (java, "ffmpeg"):
        if shutil.which(tool) is None:
            print(f"{tool} not found on PATH", file=sys.stderr)
            sys.exit(1)
    jar = _jpsxdec_jar()

    disc = ps1_dir()
    movies = sorted(glob.glob(os.path.join(disc, "MOVIES", "*.WVE")) +
                    glob.glob(os.path.join(disc, "*.FSV")))
    if not movies:
        print(f"No .WVE files found in {disc}/MOVIES (or set FCOP_PS1_DIR)", file=sys.stderr)
        sys.exit(1)

    out_dir = os.path.join("parsed", "video")
    os.makedirs(out_dir, exist_ok=True)
    for wve in movies:
        ogv = _decode(java, os.path.abspath(jar), os.path.abspath(wve), out_dir, keep_avi)
        print(f"{os.path.relpath(wve, disc)} → {ogv}  ({os.path.getsize(ogv) // 1024} KB)")
    print(f"\nDone — {len(movies)} movies decoded.")


if __name__ == "__main__":
    main()
