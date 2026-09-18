import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from scripts import provision_assets
from core.util.config import Config


class TestProvisionAssetsStages(unittest.TestCase):
    """The stage wiring is what the Dockerfile and entrypoint depend on."""

    def test_build_and_runtime_task_registries(self):
        # Build-stage work must never touch mounted state, and runtime work must
        # be safe to repeat on every boot, so the two registries stay separate.
        self.assertEqual(
            set(provision_assets.BUILD_TASKS), {"stt", "vad", "audio"}
        )
        self.assertEqual(
            set(provision_assets.RUNTIME_TASKS), {"stt", "seed", "dirs", "env", "index"}
        )

    def test_seed_dir_is_outside_the_mapped_volume(self):
        # docker-compose maps a named volume at ASSET_ROOT. Docker only copies
        # image content into such a volume while it is empty, so a seed stored
        # inside it would be frozen at the first image that populated it and no
        # rebuild could ever correct it.
        self.assertFalse(
            provision_assets.SEED_DIR.startswith(provision_assets.ASSET_ROOT + os.sep),
            f"SEED_DIR {provision_assets.SEED_DIR} must not live under "
            f"ASSET_ROOT {provision_assets.ASSET_ROOT}"
        )

    def test_hf_cache_is_inside_the_mapped_volume(self):
        # The opposite requirement: the model cache is exactly what the volume
        # exists to persist across rebuilds.
        self.assertTrue(
            provision_assets.HF_CACHE.startswith(provision_assets.ASSET_ROOT)
        )

    def test_unknown_task_is_rejected(self):
        with self.assertRaises(SystemExit):
            provision_assets.run_stage("build", only=["does-not-exist"])

    def test_dry_run_build_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(provision_assets, "ASSET_ROOT", tmp), \
                 patch.object(provision_assets, "SEED_DIR", os.path.join(tmp, "seed")):
                rep = provision_assets.run_stage("build", only=["audio"], dry_run=True)
        self.assertFalse(rep.failed)
        self.assertEqual(os.listdir(tmp) if os.path.isdir(tmp) else [], [])


class TestSttCacheSelfHeal(unittest.TestCase):
    """A persistent volume can outlive the image that filled it."""

    def test_cached_model_is_not_redownloaded(self):
        with patch.object(provision_assets, "_fetch_stt_model",
                          return_value="/opt/aoc/hf/snap") as mock_fetch:
            rep = provision_assets.run_stage("runtime", only=["stt"])
        mock_fetch.assert_called_once()
        self.assertTrue(mock_fetch.call_args.kwargs["local_files_only"])
        self.assertFalse(rep.failed)

    def test_empty_volume_triggers_a_download(self):
        # First call probes the cache and misses; second must actually fetch.
        calls = []

        def fake_fetch(model, local_files_only=False):
            calls.append(local_files_only)
            if local_files_only:
                raise RuntimeError("not cached")
            return "/opt/aoc/hf/snap"

        with patch.object(provision_assets, "_fetch_stt_model", side_effect=fake_fetch), \
             patch("os.makedirs"):
            rep = provision_assets.run_stage("runtime", only=["stt"])

        self.assertEqual(calls, [True, False])
        self.assertFalse(rep.failed)

    def test_download_failure_is_reported(self):
        with patch.object(provision_assets, "_fetch_stt_model",
                          side_effect=RuntimeError("offline")), \
             patch("os.makedirs"):
            rep = provision_assets.run_stage("runtime", only=["stt"])
        self.assertTrue(rep.failed)

    def test_dry_run_never_downloads(self):
        def fake_fetch(model, local_files_only=False):
            if local_files_only:
                raise RuntimeError("not cached")
            raise AssertionError("dry run must not download")

        with patch.object(provision_assets, "_fetch_stt_model", side_effect=fake_fetch):
            rep = provision_assets.run_stage("runtime", only=["stt"], dry_run=True)
        self.assertFalse(rep.failed)


class TestSeedRestore(unittest.TestCase):
    """Seeding is the mechanism that survives the /app bind mount."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.seed = os.path.join(self.tmp, "seed")
        self.project = os.path.join(self.tmp, "project")
        os.makedirs(os.path.join(self.seed, "assets", "sounds", "blurps"))
        os.makedirs(self.project)
        self.seeded_file = os.path.join(self.seed, "assets", "sounds", "blurps", "default_ack.wav")
        with open(self.seeded_file, "wb") as f:
            f.write(b"RIFFseed")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, dry_run=False):
        with patch.object(provision_assets, "SEED_DIR", self.seed), \
             patch.object(provision_assets, "PROJECT_ROOT", self.project):
            return provision_assets.run_stage("runtime", only=["seed"], dry_run=dry_run)

    def test_missing_file_is_restored_from_seed(self):
        rep = self._run()
        restored = os.path.join(self.project, "assets", "sounds", "blurps", "default_ack.wav")
        self.assertTrue(os.path.exists(restored))
        self.assertEqual(open(restored, "rb").read(), b"RIFFseed")
        self.assertFalse(rep.failed)

    def test_existing_file_is_never_overwritten(self):
        # A mounted checkout is authoritative; the seed is only a fallback.
        dest = os.path.join(self.project, "assets", "sounds", "blurps", "default_ack.wav")
        os.makedirs(os.path.dirname(dest))
        with open(dest, "wb") as f:
            f.write(b"RIFFmounted")

        self._run()
        self.assertEqual(open(dest, "rb").read(), b"RIFFmounted")

    def test_dry_run_restores_nothing(self):
        self._run(dry_run=True)
        self.assertFalse(os.path.exists(os.path.join(self.project, "assets")))


class TestRuntimeDirs(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_ignored_directories_are_created(self):
        with patch.object(provision_assets, "PROJECT_ROOT", self.tmp):
            rep = provision_assets.run_stage("runtime", only=["dirs"])
        self.assertFalse(rep.failed)
        for rel in provision_assets.RUNTIME_DIRS:
            self.assertTrue(os.path.isdir(os.path.join(self.tmp, rel)), rel)


class TestVectorIndexBootstrap(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.pkm = os.path.join(self.tmp, "pkm")
        os.makedirs(self.pkm)
        self.db = os.path.join(self.pkm, ".lancedb")
        Config().reset()

    def tearDown(self):
        Config().reset()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _seed_lancedb_index(self):
        os.makedirs(os.path.join(self.db, "vault_chunks.lance"), exist_ok=True)

    def _seed_numpy_index(self):
        table = os.path.join(self.db, "vault_chunks")
        os.makedirs(table, exist_ok=True)
        open(os.path.join(table, "chunks.parquet"), "w").close()

    def _run(self, env):
        base = {
            "PKM_DIR": self.pkm,
            "KNOWLEDGE_DB_PATH": self.db,
            # Pinned so the presence check is deterministic. Left on 'auto' it
            # would probe, and the probe uses the subprocess.run this patches.
            "KNOWLEDGE_BACKEND": "numpy",
        }
        base.update(env)
        with patch.dict(os.environ, base, clear=False), \
             patch.object(provision_assets, "PROJECT_ROOT", self.tmp), \
             patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            rep = provision_assets.run_stage("runtime", only=["index"])
        return rep, mock_run

    def test_never_mode_skips_entirely(self):
        _, mock_run = self._run({"AOC_BOOTSTRAP_INDEX": "never"})
        mock_run.assert_not_called()

    def test_auto_mode_builds_when_database_absent(self):
        rep, mock_run = self._run({"AOC_BOOTSTRAP_INDEX": "auto", "GEMINI_API_KEY": "key"})
        mock_run.assert_called_once()
        self.assertNotIn("--skip-embedding", mock_run.call_args[0][0])
        self.assertFalse(rep.failed)

    def test_auto_mode_skips_when_database_present(self):
        self._seed_numpy_index()
        _, mock_run = self._run({"AOC_BOOTSTRAP_INDEX": "auto"})
        mock_run.assert_not_called()

    def test_auto_mode_rebuilds_when_only_the_other_backend_has_an_index(self):
        # A NAS that fell back to numpy still has the old LanceDB directory. It
        # is non-empty, but the running backend cannot read a row of it, so
        # treating it as "already indexed" would boot with an empty vault.
        self._seed_lancedb_index()
        _, mock_run = self._run({"AOC_BOOTSTRAP_INDEX": "auto", "KNOWLEDGE_BACKEND": "numpy"})
        mock_run.assert_called_once()

    def test_auto_mode_skips_lancedb_index_when_lancedb_is_the_backend(self):
        self._seed_lancedb_index()
        _, mock_run = self._run({"AOC_BOOTSTRAP_INDEX": "auto", "KNOWLEDGE_BACKEND": "lancedb"})
        mock_run.assert_not_called()

    def test_empty_database_directory_is_not_treated_as_an_index(self):
        # init_knowledge_db creates the table directory eagerly, so its mere
        # existence says nothing about whether anything was ever written.
        os.makedirs(os.path.join(self.db, "vault_chunks"), exist_ok=True)
        _, mock_run = self._run({"AOC_BOOTSTRAP_INDEX": "auto"})
        mock_run.assert_called_once()

    def test_always_mode_rebuilds_over_an_existing_index(self):
        self._seed_numpy_index()
        _, mock_run = self._run({"AOC_BOOTSTRAP_INDEX": "always"})
        mock_run.assert_called_once()

    def test_missing_key_falls_back_to_offline_embeddings(self):
        # Live embeddings without a key would degrade to deterministic vectors
        # anyway; asking for them explicitly avoids a slow, pointless API pass.
        _, mock_run = self._run({"AOC_BOOTSTRAP_INDEX": "always", "GEMINI_API_KEY": ""})
        self.assertIn("--skip-embedding", mock_run.call_args[0][0])

    def test_nonzero_exit_is_reported_as_failure(self):
        with patch.dict(os.environ, {"PKM_DIR": self.pkm, "AOC_BOOTSTRAP_INDEX": "always"}), \
             patch.object(provision_assets, "PROJECT_ROOT", self.tmp), \
             patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 3
            rep = provision_assets.run_stage("runtime", only=["index"])
        self.assertTrue(rep.failed)

    def test_unmounted_pkm_is_skipped_not_failed(self):
        with patch.dict(os.environ, {"PKM_DIR": os.path.join(self.tmp, "absent")}), \
             patch.object(provision_assets, "PROJECT_ROOT", self.tmp):
            rep = provision_assets.run_stage("runtime", only=["index"])
        self.assertFalse(rep.failed)


if __name__ == "__main__":
    unittest.main()
