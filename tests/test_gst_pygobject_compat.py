import os
from pathlib import Path
import subprocess
import sys
import textwrap


def test_pygobject_350_accepts_audioread_gst_init_none_after_vocotype_patch():
    """Reproduce the PyGObject 3.50 failure, then verify VoCoType fixes it."""
    repo_root = Path(__file__).resolve().parents[1]
    script = textwrap.dedent(
        """
        import importlib.metadata

        version = importlib.metadata.version("PyGObject")
        assert version == "3.50.2", f"expected PyGObject 3.50.2, got {version}"

        import gi

        gi.require_version("Gst", "1.0")
        from gi.repository import Gst

        try:
            Gst.init(None)
        except TypeError:
            pass
        else:
            raise AssertionError(
                "test environment did not reproduce PyGObject 3.50 Gst.init(None) failure"
            )

        from app import funasr_server

        patched_init = Gst.init
        Gst.init(None)
        Gst.init([])

        funasr_server._patch_audioread_gstreamer_compatibility()
        assert Gst.init is patched_init, "compatibility patch must be idempotent"
        """
    )
    env = os.environ.copy()
    env["PYTHONPATH"] = str(repo_root)
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
