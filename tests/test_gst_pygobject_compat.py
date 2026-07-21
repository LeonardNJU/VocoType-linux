import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest


def _require_gstreamer_typelib() -> None:
    try:
        import gi

        gi.require_version("Gst", "1.0")
        from gi.repository import Gst  # noqa: F401
    except (ImportError, ValueError) as exc:
        pytest.skip(f"GStreamer typelib is not installed: {exc}")


def _run_isolated(script: str) -> subprocess.CompletedProcess[str]:
    repo_root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(repo_root)
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(script)],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_pygobject_350_gst_init_calls_remain_valid_after_vocotype_patch():
    """Exercise the real PyGObject/GStreamer binding available on the runner."""
    _require_gstreamer_typelib()
    completed = _run_isolated(
        """
        import importlib.metadata

        version = importlib.metadata.version("PyGObject")
        assert version == "3.50.2", f"expected PyGObject 3.50.2, got {version}"

        import gi

        gi.require_version("Gst", "1.0")
        from gi.repository import Gst
        from app import funasr_server

        patched_init = Gst.init
        Gst.init(None)
        Gst.init([])

        funasr_server._patch_audioread_gstreamer_compatibility()
        assert Gst.init is patched_init, "compatibility patch must be idempotent"
        """
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_gst_compatibility_patch_normalizes_none_for_strict_bindings():
    """Model the strict typelib behavior reported on affected distributions."""
    _require_gstreamer_typelib()
    completed = _run_isolated(
        """
        import gi

        gi.require_version("Gst", "1.0")
        from gi.repository import Gst
        from app import funasr_server

        calls = []

        def strict_init(args):
            if args is None:
                raise TypeError("Argument 1 does not allow None as a value")
            calls.append(args)
            return "initialized"

        Gst.init = strict_init
        funasr_server._patch_audioread_gstreamer_compatibility()

        assert Gst.init(None) == "initialized"
        assert calls == [[]]

        explicit_args = ["--gst-debug=2"]
        assert Gst.init(explicit_args) == "initialized"
        assert calls[-1] is explicit_args

        patched_init = Gst.init
        funasr_server._patch_audioread_gstreamer_compatibility()
        assert Gst.init is patched_init, "compatibility patch must be idempotent"
        """
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
