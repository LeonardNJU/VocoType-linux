#!/usr/bin/env python3
"""Exercise VoCoType's Rime Shift handling in an isolated IBus daemon.

Usage: ibus-shift-integration.py /absolute/path/to/vocotype-ibus-engine

The component is created inside the temporary XDG data directory, so the test
always starts the supplied binary and never falls back to an installed engine.
"""

import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time

if len(sys.argv) != 2:
    raise SystemExit(f"usage: {pathlib.Path(sys.argv[0]).name} ENGINE_BINARY")

engine_binary = pathlib.Path(sys.argv[1]).resolve()
if not engine_binary.is_file() or not os.access(engine_binary, os.X_OK):
    raise SystemExit(f"engine is not executable: {engine_binary}")

root = pathlib.Path(tempfile.mkdtemp(prefix="vocotype-ibus-shift-"))
config = root / "config"
data = root / "data"
component = data / "ibus" / "component" / "vocotype.xml"
engine_wrapper = root / "run-engine"
engine_pid = root / "engine.pid"
engine_wrapper.write_text(
    "#!/usr/bin/env bash\n"
    f"export IBUS_ADDRESS='unix:path={root / 'ibus.sock'}'\n"
    f"printf '%s\\n' \"$$\" > {engine_pid}\n"
    f"exec {engine_binary} \"$@\"\n",
    encoding="utf-8",
)
engine_wrapper.chmod(0o755)
component.parent.mkdir(parents=True)
component.write_text(
    "<?xml version=\"1.0\" encoding=\"utf-8\"?>\n"
    "<component><name>org.vocotype.IBus.VoCoType</name>"
    "<description>VoCoType Voice Input Method</description>"
    f"<exec>{engine_wrapper} --ibus</exec><version>test</version>"
    "<author>VoCoType</author><license>GPL</license>"
    "<homepage>https://github.com/LeonardNJU/VocoType-linux</homepage>"
    "<textdomain>vocotype</textdomain><engines><engine><name>vocotype</name>"
    "<language>zh</language><license>GPL</license><author>VoCoType</author>"
    "<layout>default</layout><longname>VoCoType Voice Input</longname>"
    "<description>Configurable Push-to-Talk Voice Input</description>"
    "<rank>50</rank><symbol>V</symbol></engine></engines></component>\n",
    encoding="utf-8",
)
shutil.copytree(
    pathlib.Path.home() / ".config" / "vocotype" / "rime",
    config / "vocotype" / "rime",
    ignore=shutil.ignore_patterns("*.userdb", "*.log"),
)
os.environ.update(
    HOME=str(root),
    XDG_CONFIG_HOME=str(config),
    XDG_CACHE_HOME=str(root / "cache"),
    XDG_DATA_HOME=str(data),
    # Prevent a system component with the same engine name from winning the
    # registry scan over the component that names engine_binary above.
    XDG_DATA_DIRS=str(root / "system-share"),
    IBUS_COMPONENT_PATH=str(component.parent),
    IBUS_ADDRESS="unix:path=" + str(root / "ibus.sock"),
)
os.environ.pop("IBUS_DAEMON_ADDRESS", None)

import gi  # noqa: E402

gi.require_version("IBus", "1.0")
from gi.repository import GLib, IBus  # noqa: E402

IBus.init()
log = open(root / "daemon.log", "w", encoding="utf-8")
daemon = subprocess.Popen(
    [
        "ibus-daemon",
        "--single",
        "--cache",
        "refresh",
        "--panel=disable",
        "--config=disable",
        "--emoji-extension=disable",
        "--address=" + os.environ["IBUS_ADDRESS"],
    ],
    stdout=log,
    stderr=log,
)
engine_process = None


def drain(seconds=0.15):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        while GLib.MainContext.default().pending():
            GLib.MainContext.default().iteration(False)
        time.sleep(0.01)


def expect_chinese(sample):
    assert all(sample), f"expected Chinese Rime handling, got {sample}"


def expect_ascii(sample):
    assert not any(sample), f"expected ASCII pass-through, got {sample}"


try:
    drain(1)
    bus = IBus.Bus.new()
    if not bus.is_connected():
        raise RuntimeError("isolated IBus connection failed")
    context = bus.create_input_context("vocotype-shift-integration")
    context.set_capabilities(
        int(
            IBus.Capabilite.PREEDIT_TEXT
            | IBus.Capabilite.FOCUS
            | IBus.Capabilite.LOOKUP_TABLE
        )
    )
    context.focus_in()
    activated = False
    for _ in range(50):
        activated = bus.set_global_engine("vocotype")
        if activated:
            break
        drain(0.1)
    assert activated, "VoCoType global engine activation failed"
    context.set_engine("vocotype")
    drain(2)
    engine = context.get_engine()
    assert engine and engine.get_name() == "vocotype", "VoCoType was not activated"
    for _ in range(50):
        if engine_pid.exists():
            break
        drain(0.02)
    assert engine_pid.exists(), "registered component did not launch the supplied engine"
    launched_executable = pathlib.Path(f"/proc/{engine_pid.read_text().strip()}/exe").resolve()
    assert launched_executable == engine_binary, (
        f"test started {launched_executable}, not supplied {engine_binary}"
    )
    print("engine executable:", launched_executable, flush=True)

    events = []
    context.connect("commit-text", lambda _c, text: events.append(("commit", text.get_text())))
    context.connect(
        "update-preedit-text",
        lambda _c, text, _pos, visible: events.append(("preedit", text.get_text(), visible)),
    )

    def key(keyval, mask=0):
        handled = context.process_key_event(keyval, 0, mask)
        drain(0.03)
        return handled

    def release(keyval, mask=0):
        return key(keyval, int(IBus.ModifierType.RELEASE_MASK) | mask)

    def sample(label):
        events.clear()
        handled = [key(ord(char)) for char in "nihao"]
        print(label, "handled=", handled, "events=", events, flush=True)
        key(IBus.KEY_Escape)
        return handled

    def tap_shift(keyval, modifier=0):
        return key(keyval, modifier), release(keyval, modifier | int(IBus.ModifierType.SHIFT_MASK))

    expect_chinese(sample("initial"))
    for name, shift in (("left", IBus.KEY_Shift_L), ("right", IBus.KEY_Shift_R)):
        print(name, "Shift to ASCII=", tap_shift(shift), flush=True)
        expect_ascii(sample(f"{name} Shift Chinese-to-ASCII"))
        print(name, "Shift to Chinese=", tap_shift(shift), flush=True)
        expect_chinese(sample(f"{name} Shift ASCII-to-Chinese"))

    # Shift used as a modifier must not leave the engine in ASCII mode.
    key(IBus.KEY_Shift_L)
    key(ord("a"), int(IBus.ModifierType.SHIFT_MASK))
    release(ord("a"), int(IBus.ModifierType.SHIFT_MASK))
    release(IBus.KEY_Shift_L, int(IBus.ModifierType.SHIFT_MASK))
    expect_chinese(sample("after Shift+A"))

    # The Rime schema reserves Ctrl+Shift+2; it must not toggle the ASCII mode.
    key(IBus.KEY_Control_L)
    key(IBus.KEY_Shift_L, int(IBus.ModifierType.CONTROL_MASK))
    key(ord("2"), int(IBus.ModifierType.CONTROL_MASK | IBus.ModifierType.SHIFT_MASK))
    release(ord("2"), int(IBus.ModifierType.CONTROL_MASK | IBus.ModifierType.SHIFT_MASK))
    release(IBus.KEY_Shift_L, int(IBus.ModifierType.CONTROL_MASK | IBus.ModifierType.SHIFT_MASK))
    release(IBus.KEY_Control_L, int(IBus.ModifierType.CONTROL_MASK))
    expect_chinese(sample("after Ctrl+Shift+2"))

    context.focus_out()
    context.destroy()
    print("IBUS_SHIFT_INTEGRATION_OK", flush=True)
finally:
    if engine_process is not None:
        engine_process.terminate()
        try:
            engine_process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            engine_process.kill()
    daemon.terminate()
    try:
        daemon.wait(timeout=3)
    except subprocess.TimeoutExpired:
        daemon.kill()
    log.close()
    shutil.rmtree(root)
    print("Isolated IBus and temporary Rime data removed.", flush=True)
