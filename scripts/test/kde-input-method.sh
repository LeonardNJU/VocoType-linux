#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."

python3 - <<'PY'
import json
import fcntl
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time

helper = Path("scripts/install/common/reapply-kde-input-method.sh").resolve()
fake_tool = r'''#!/usr/bin/python3
import json
import os
from pathlib import Path
import sys
import time

state_file = Path(os.environ["FAKE_KDE_STATE"])
state = json.loads(state_file.read_text())
tool = Path(sys.argv[0]).name
args = sys.argv[1:]
if tool == "kreadconfig6":
    print(state["selection"])
elif tool == "kwriteconfig6":
    assert args[args.index("--type") + 1] == "path", args
    assert "--notify" in args, args
    value = args[-1]
    state["writes"].append(value)
    if value and state["scenario"] == "restore_retry" and state["writes"].count(value) == 1:
        state_file.write_text(json.dumps(state))
        sys.exit(42)
    state["selection"] = value
    if value and state["scenario"] != "owner_stale":
        state["owner"] = "new"
    state_file.write_text(json.dumps(state))
    if not value and state["scenario"] == "clear_failure":
        sys.exit(42)
    if not value and state["scenario"] == "interrupted":
        time.sleep(0.5)
elif tool == "busctl":
    if "get-property" in args:
        if args[-1] == "enabled":
            print("b false" if state["scenario"] == "disabled" else "b true")
        elif args[-1] == "available":
            if not state["selection"] and state["scenario"] == "clear_query_failure":
                sys.exit(42)
            print("b true" if state["selection"] else "b false")
        else:
            sys.exit(99)
    elif "GetNameOwner" in args:
        print('s "' + state["owner"] + '"')
    else:
        sys.exit(99)
else:
    sys.exit(99)
'''

with tempfile.TemporaryDirectory(prefix="vocotype-kde-test-") as directory:
    root = Path(directory)
    tools = root / "bin"
    tools.mkdir()
    for name in ("timeout", "flock", "sleep"):
        (tools / name).symlink_to(shutil.which(name))
    for name in ("kreadconfig6", "kwriteconfig6", "busctl"):
        path = tools / name
        path.write_text(fake_tool)
        path.chmod(0o755)
    original = root / "fcitx5-wayland-launcher.desktop"
    original.touch()
    state_file = root / "state.json"
    env = dict(os.environ, PATH=str(tools), XDG_CURRENT_DESKTOP="KDE",
               XDG_SESSION_TYPE="wayland", XDG_RUNTIME_DIR=str(root),
               FAKE_KDE_STATE=str(state_file))
    for scenario, status in [("success", 0), ("clear_failure", 1),
                             ("clear_query_failure", 1), ("restore_retry", 0),
                             ("disabled", 2), ("foreign_selection", 2),
                             ("interrupted", 143), ("owner_stale", 1)]:
        selection = str(root / "other.desktop") if scenario == "foreign_selection" else str(original)
        state_file.write_text(json.dumps(dict(selection=selection, writes=[], owner="old", scenario=scenario)))
        process = subprocess.Popen(["/usr/bin/bash", str(helper)], env=env,
                                   text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            if scenario == "interrupted":
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    try:
                        if json.loads(state_file.read_text())["selection"] == "":
                            process.terminate()
                            break
                    except json.JSONDecodeError:
                        pass
                    time.sleep(0.01)
                else:
                    raise AssertionError("test never reached the clear operation")
            stdout, stderr = process.communicate(timeout=15)
        except BaseException:
            process.kill()
            process.communicate()
            raise
        state = json.loads(state_file.read_text())
        assert process.returncode == status, (scenario, process.returncode, stdout, stderr)
        assert state["selection"] == selection, (scenario, state)
        if status == 2:
            assert not state["writes"], state
        else:
            assert state["writes"][0] == "" and state["writes"][-1] == selection, state
        if status == 0:
            assert state["owner"] == "new" and "实例已更换" in stdout, (state, stdout)
    # Unsupported desktops must not invoke any KDE command.
    state_file.write_text(json.dumps(dict(selection=str(original), writes=[], owner="old", scenario="success")))
    result = subprocess.run(["/usr/bin/bash", str(helper)], env=dict(env, XDG_CURRENT_DESKTOP="GNOME"),
                            text=True, capture_output=True, timeout=5)
    assert result.returncode == 2 and not json.loads(state_file.read_text())["writes"]
    with (root / "vocotype-kde-input-method.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = subprocess.run(["/usr/bin/bash", str(helper)], env=env,
                                text=True, capture_output=True, timeout=5)
        assert result.returncode == 1 and not json.loads(state_file.read_text())["writes"]
print("KDE reapply: success, restoration, interruption and preflight checks passed")
PY
