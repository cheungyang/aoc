#!/usr/bin/env python3
"""Provision the runtime assets that are deliberately absent from git.

Several things this app needs at runtime are never committed (see `.gitignore`):
the faster-whisper STT weights, generated audio cues, the LanceDB vector index,
and the various runtime directories. A developer machine accumulates them
organically; a freshly built container has none of them, so the first request
that touches voice or vault search either stalls on a multi-hundred-megabyte
download or silently degrades.

This script centralises that provisioning in two stages:

  build    Runs inside `docker build`. Downloads/generates everything that
           needs the network and bakes it into the image under $AOC_ASSET_ROOT.
           Nothing is written into /app here on purpose: docker-compose
           bind-mounts the host checkout over /app, which would hide any file
           the build wrote there. Files that *must* live in /app are staged in
           the seed directory instead and restored by the runtime stage.

  runtime  Runs from entrypoint.sh once volumes are mounted. Restores seeded
           files that the bind mount hid, creates runtime directories, and
           bootstraps the vector index from the mounted PKM.

Usage:
    python3 scripts/provision_assets.py --stage build
    python3 scripts/provision_assets.py --stage runtime
    python3 scripts/provision_assets.py --stage runtime --only dirs,seed
    python3 scripts/provision_assets.py --stage build --dry-run

Exit code is 0 unless a task fails *and* AOC_PROVISION_STRICT=1, so a flaky
mirror cannot break an otherwise good image unless the operator asks for it.
"""

import argparse
import os
import shutil
import subprocess
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Big, regenerable downloads. docker-compose maps a named volume here so a
# rebuilt image does not re-fetch them, which also means the directory can
# outlive the image that created it — every consumer below therefore treats it
# as a cache that may be empty or stale, never as a guaranteed input.
ASSET_ROOT = os.environ.get("AOC_ASSET_ROOT", "/opt/aoc")
HF_CACHE = os.environ.get("HF_HOME", os.path.join(ASSET_ROOT, "hf"))

# Files destined for /app, staged because the bind mount hides whatever the
# build wrote there. Deliberately NOT under ASSET_ROOT: Docker only copies image
# content into a named volume when that volume is empty, so a seed living inside
# the volume would be frozen at the first image that ever populated it and no
# later build could correct it.
SEED_DIR = os.environ.get("AOC_SEED_DIR", "/opt/aoc-seed")

# Audio cues played back by BlurpGenerator. Tracked in git today, but a wheel
# of a fresh clone can still be missing them, and TTS regeneration is cheap.
BLURP_VOICE = os.environ.get("AOC_BLURP_VOICE", "en-US-AriaNeural")
REQUIRED_BLURPS = {
    "assets/sounds/blurps/default_ack.wav": "Okay.",
    "assets/sounds/blurps/coder_beep.wav": "On it.",
    "assets/sounds/blurps/concierge_ping.wav": "Yes?",
}

# Directories the app writes to that git cannot carry (empty or ignored).
RUNTIME_DIRS = [
    "sessions",
    "workspaces",
    "assets/sounds/bridge",
    ".gogcli/config",
    ".gogcli/data",
    ".gogcli/state",
    ".gogcli/cache",
]

OK, SKIP, FAIL = "ok", "skip", "fail"


class Reporter:
    """Collects per-task outcomes so one failure does not hide the others."""

    def __init__(self, stage, dry_run):
        self.stage = stage
        self.dry_run = dry_run
        self.results = []

    def record(self, task, status, detail=""):
        self.results.append((task, status, detail))
        icon = {OK: "✓", SKIP: "-", FAIL: "✗"}[status]
        prefix = "[provision:dry-run]" if self.dry_run else "[provision]"
        print(f"{prefix} {icon} {task}: {detail}", flush=True)

    @property
    def failed(self):
        return [r for r in self.results if r[1] == FAIL]


def _rel(path):
    return os.path.join(PROJECT_ROOT, path)


# --------------------------------------------------------------------------
# Build stage
# --------------------------------------------------------------------------

def _fetch_stt_model(model, local_files_only=False):
    """Resolves the faster-whisper weights, downloading them unless told not to.

    Returns the snapshot path. Raises if `local_files_only` is set and the cache
    does not already hold the model.
    """
    os.environ.setdefault("HF_HOME", HF_CACHE)
    try:
        from faster_whisper.utils import download_model
        return download_model(model, local_files_only=local_files_only)
    except ImportError:
        # Older faster-whisper exposes no download helper; instantiating the
        # model performs the same fetch.
        from faster_whisper import WhisperModel
        WhisperModel(model, device="cpu", compute_type="int8",
                     local_files_only=local_files_only)
        return HF_CACHE


def task_stt_model(rep):
    """Bakes the faster-whisper weights into the image.

    Without this the first transcription downloads ~150MB from HuggingFace,
    which both delays the first voice reply and makes the container depend on
    HF being reachable. The download honours HF_HOME, which the Dockerfile
    pins to $AOC_ASSET_ROOT/hf for both build and runtime so the cache the
    build populates is the cache STTEngine reads.
    """
    model = os.environ.get("AOC_STT_MODEL", "base.en")
    if rep.dry_run:
        rep.record("stt_model", SKIP, f"would download '{model}' into {HF_CACHE}")
        return

    os.makedirs(HF_CACHE, exist_ok=True)
    try:
        path = _fetch_stt_model(model)
        rep.record("stt_model", OK, f"'{model}' cached at {path}")
    except Exception as e:
        rep.record("stt_model", FAIL, f"could not fetch '{model}': {e}")


def task_vad_model(rep):
    """Verifies the Silero VAD graph that ships inside the faster-whisper wheel.

    VADSink loads it by path, so a wheel layout change is a startup crash we
    would rather see at build time.
    """
    try:
        import faster_whisper
        asset_dir = os.path.join(os.path.dirname(faster_whisper.__file__), "assets")
        found = [n for n in ("silero_vad_v6.onnx", "silero_vad_v5.onnx")
                 if os.path.exists(os.path.join(asset_dir, n))]
        if found:
            rep.record("vad_model", OK, f"{found[0]} present in {asset_dir}")
        else:
            rep.record("vad_model", FAIL, f"no silero_vad_v*.onnx under {asset_dir}")
    except Exception as e:
        rep.record("vad_model", FAIL, str(e))


def _synthesize(text, dest):
    """Renders `text` to a wav at `dest` using edge-tts (already a dependency)."""
    import asyncio
    import edge_tts

    tmp_mp3 = dest + ".mp3"

    async def run():
        await edge_tts.Communicate(text, BLURP_VOICE).save(tmp_mp3)

    asyncio.run(run())

    ffmpeg = None
    try:
        import imageio_ffmpeg
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        ffmpeg = shutil.which("ffmpeg")

    if not ffmpeg:
        raise RuntimeError("ffmpeg unavailable to transcode the generated cue")

    subprocess.run(
        [ffmpeg, "-y", "-loglevel", "error", "-i", tmp_mp3,
         "-ar", "48000", "-ac", "2", dest],
        check=True,
    )
    os.remove(tmp_mp3)


def task_seed_audio(rep):
    """Stages the audio cues in the seed directory, generating any that are absent.

    The seed copy exists because /app is replaced by a bind mount at run time;
    the runtime stage copies anything missing back in.
    """
    staged, generated, failed = 0, 0, 0
    for rel_path, phrase in REQUIRED_BLURPS.items():
        src = _rel(rel_path)
        dest = os.path.join(SEED_DIR, rel_path)
        if rep.dry_run:
            action = "stage" if os.path.exists(src) else "generate"
            rep.record(f"seed_audio:{os.path.basename(rel_path)}", SKIP,
                       f"would {action} {dest}")
            continue

        os.makedirs(os.path.dirname(dest), exist_ok=True)
        try:
            if os.path.exists(src):
                shutil.copy2(src, dest)
                staged += 1
            else:
                _synthesize(phrase, dest)
                generated += 1
        except Exception as e:
            failed += 1
            rep.record(f"seed_audio:{os.path.basename(rel_path)}", FAIL, str(e))

    if rep.dry_run:
        return
    if failed:
        return
    rep.record("seed_audio", OK,
               f"{staged} staged from repo, {generated} generated into {SEED_DIR}")


# --------------------------------------------------------------------------
# Runtime stage
# --------------------------------------------------------------------------

def task_stt_cache(rep):
    """Repopulates the model cache when the mapped volume does not hold it.

    ASSET_ROOT is a named volume, so the weights the build baked in are only
    copied into it while it is empty. A volume created by an older image, or one
    that predates an AOC_STT_MODEL change, would otherwise leave STTEngine to
    discover the gap mid-conversation. Checking here turns that into a slow
    start instead of a failed transcription.
    """
    model = os.environ.get("AOC_STT_MODEL", "base.en")
    try:
        path = _fetch_stt_model(model, local_files_only=True)
        rep.record("stt_cache", OK, f"'{model}' present at {path}")
        return
    except Exception:
        pass  # Not cached: fall through and fetch it.

    if rep.dry_run:
        rep.record("stt_cache", SKIP, f"would download '{model}' into {HF_CACHE}")
        return

    try:
        os.makedirs(HF_CACHE, exist_ok=True)
        path = _fetch_stt_model(model)
        rep.record("stt_cache", OK, f"'{model}' downloaded into {path}")
    except Exception as e:
        rep.record("stt_cache", FAIL, f"could not fetch '{model}': {e}")


def task_restore_seed(rep):
    """Copies seeded files into /app for anything the bind mount does not supply."""
    if not os.path.isdir(SEED_DIR):
        rep.record("restore_seed", SKIP, f"no seed directory at {SEED_DIR}")
        return

    restored = 0
    for dirpath, _, filenames in os.walk(SEED_DIR):
        for name in filenames:
            src = os.path.join(dirpath, name)
            rel_path = os.path.relpath(src, SEED_DIR)
            dest = _rel(rel_path)
            if os.path.exists(dest):
                continue
            if rep.dry_run:
                rep.record(f"restore_seed:{rel_path}", SKIP, f"would restore {dest}")
                continue
            try:
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                shutil.copy2(src, dest)
                restored += 1
            except Exception as e:
                rep.record(f"restore_seed:{rel_path}", FAIL, str(e))

    if not rep.dry_run:
        rep.record("restore_seed", OK, f"{restored} file(s) restored from seed")


def task_runtime_dirs(rep):
    """Creates the ignored working directories the app expects to already exist."""
    created = 0
    for rel_path in RUNTIME_DIRS:
        path = _rel(rel_path)
        if os.path.isdir(path):
            continue
        if rep.dry_run:
            rep.record(f"dirs:{rel_path}", SKIP, f"would create {path}")
            continue
        try:
            os.makedirs(path, exist_ok=True)
            created += 1
        except Exception as e:
            rep.record(f"dirs:{rel_path}", FAIL, str(e))

    if not rep.dry_run:
        rep.record("dirs", OK, f"{created} directory(ies) created")


def task_env_file(rep):
    """Warns when `.env` is absent rather than inventing one.

    A generated `.env` full of placeholders looks configured and fails much
    later with a confusing auth error, so surface the gap at startup instead.
    """
    if os.path.exists(_rel(".env")):
        rep.record("env_file", OK, ".env present")
    elif os.environ.get("GEMINI_API_KEY") or os.environ.get("OPENAI_API_KEY"):
        rep.record("env_file", OK, "no .env, but credentials are in the environment")
    else:
        rep.record("env_file", SKIP,
                   "no .env and no API keys in environment — copy .env.example and fill it in")


def _index_present(db_path):
    """True if the *active* backend already has a store at db_path.

    A non-empty directory is not enough. The two backends use different layouts,
    so a machine carrying an index written by the other one would look populated
    while the running backend sees nothing -- and `auto` mode would skip the
    build that would have fixed it.
    """
    if not os.path.isdir(db_path):
        return False

    try:
        # Only resolves a name. The probe runs in a child process and no native
        # library is imported here, so this is safe on any CPU.
        from core.knowledge.vector.backends import resolve_backend_name, LANCEDB
        backend = resolve_backend_name()
    except Exception:
        # Can't tell which backend will run; fall back to the old heuristic.
        return bool(os.listdir(db_path))

    if backend == LANCEDB:
        return os.path.isdir(os.path.join(db_path, "vault_chunks.lance"))
    return os.path.isfile(os.path.join(db_path, "vault_chunks", "chunks.parquet"))


def task_vector_index(rep):
    """Builds the vault index when the mounted PKM has notes but no database.

    Controlled by AOC_BOOTSTRAP_INDEX: `auto` (default) only builds when the
    database is missing, `always` rebuilds every boot, `never` disables it.
    """
    mode = os.environ.get("AOC_BOOTSTRAP_INDEX", "auto").lower()
    if mode == "never":
        rep.record("vector_index", SKIP, "disabled via AOC_BOOTSTRAP_INDEX=never")
        return

    pkm_dir = os.environ.get("PKM_DIR", os.path.expanduser("~/pkm"))
    db_path = os.environ.get("KNOWLEDGE_DB_PATH", os.path.join(pkm_dir, ".lancedb"))

    if not os.path.isdir(pkm_dir):
        rep.record("vector_index", SKIP, f"PKM not mounted at {pkm_dir}")
        return

    if mode == "auto" and _index_present(db_path):
        rep.record("vector_index", SKIP, f"index already present at {db_path}")
        return

    cmd = [sys.executable, _rel("scripts/regenerate_lancedb.py"),
           "--pkm-dir", pkm_dir, "--db-path", db_path, "--test-query", ""]
    if not (os.environ.get("GEMINI_API_KEY") or "").strip():
        # No key means live embeddings would fail over to deterministic vectors
        # anyway; asking for them up front keeps the log honest and fast.
        cmd.append("--skip-embedding")

    if rep.dry_run:
        rep.record("vector_index", SKIP, f"would run: {' '.join(cmd)}")
        return

    try:
        proc = subprocess.run(cmd, cwd=PROJECT_ROOT)
        if proc.returncode == 0:
            rep.record("vector_index", OK, f"index built at {db_path}")
        else:
            rep.record("vector_index", FAIL,
                       f"regenerate_lancedb.py exited {proc.returncode}")
    except Exception as e:
        rep.record("vector_index", FAIL, str(e))


BUILD_TASKS = {
    "stt": task_stt_model,
    "vad": task_vad_model,
    "audio": task_seed_audio,
}

RUNTIME_TASKS = {
    "stt": task_stt_cache,
    "seed": task_restore_seed,
    "dirs": task_runtime_dirs,
    "env": task_env_file,
    "index": task_vector_index,
}


def run_stage(stage, only=None, dry_run=False):
    """Runs a stage's tasks and returns the Reporter holding every outcome."""
    tasks = BUILD_TASKS if stage == "build" else RUNTIME_TASKS
    if only:
        unknown = [n for n in only if n not in tasks]
        if unknown:
            raise SystemExit(
                f"Unknown task(s) for stage '{stage}': {', '.join(unknown)}. "
                f"Available: {', '.join(tasks)}"
            )
        tasks = {n: tasks[n] for n in only}

    rep = Reporter(stage, dry_run)
    print(f"[provision] stage={stage} root={ASSET_ROOT} tasks={','.join(tasks)}", flush=True)
    for fn in tasks.values():
        fn(rep)
    return rep


def main():
    parser = argparse.ArgumentParser(
        description="Download or generate assets that are not checked into git."
    )
    parser.add_argument("--stage", choices=["build", "runtime"], required=True,
                        help="'build' bakes assets into the image; 'runtime' prepares mounted state")
    parser.add_argument("--only", type=str, default=None,
                        help="Comma-separated subset of tasks to run")
    parser.add_argument("--dry-run", action="store_true",
                        help="Report what would happen without writing anything")
    args = parser.parse_args()

    only = [n.strip() for n in args.only.split(",") if n.strip()] if args.only else None
    rep = run_stage(args.stage, only=only, dry_run=args.dry_run)

    if rep.failed:
        names = ", ".join(t for t, _, _ in rep.failed)
        strict = os.environ.get("AOC_PROVISION_STRICT", "").lower() in ("1", "true", "yes")
        print(f"[provision] {len(rep.failed)} task(s) failed: {names}", file=sys.stderr, flush=True)
        if strict:
            return 1
        print("[provision] continuing anyway (set AOC_PROVISION_STRICT=1 to fail instead)",
              file=sys.stderr, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
