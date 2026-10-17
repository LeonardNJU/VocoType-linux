#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."

python3 - <<'PY'
from pathlib import Path
import os
import subprocess
import tempfile

settings = Path("src/desktop/src/settings_main.cpp").read_text()
installer = Path("scripts/install/common/install-native-user.sh").read_text()
shell_restart = "restart_fcitx_desktop() {" + installer.split(
    "restart_fcitx_desktop() {", 1)[1].split("\n}\n", 1)[0] + "\n}\n"

for desktop, session, wayland, display, allowed in [
    ("KDE", "wayland", "wayland-0", ":0", True),
    ("GNOME", "wayland", "", ":0", False),
    ("KDE", "x11", "", ":0", True),
    ("GNOME", "x11", "", ":0", True),
    ("", "x11", "", ":0", False),
    ("KDE", "", "", ":0", False),
    ("KDE", "x11", "wayland-0", ":0", False),
    ("KDE", "x11", "", "", False),
]:
    for managed in (False, True):
        for failure in (0, 2, 42):
            env = dict(os.environ, XDG_CURRENT_DESKTOP=desktop,
                       XDG_SESSION_TYPE=session, WAYLAND_DISPLAY=wayland, DISPLAY=display,
                       SCRIPT_DIR="/fake")
            # Every command that could affect the real session is replaced.
            stubs = f'''
manager_available() {{ {"true" if managed else "false"}; }}
manager_user() {{
  case "$1" in
    show) echo loaded ;;
    show-environment) echo DISPLAY=:0 ;;
    restart) echo CALLED; return {failure} ;;
    *) exit 99 ;;
  esac
}}
fcitx5() {{ exit 99; }}
bash() {{ echo CALLED; return {failure}; }}
env() {{ echo CALLED; return {failure}; }}
log() {{ printf '%s\\n' "$*"; }}
'''
            result = subprocess.run(["bash", "-c", stubs + shell_restart + '''
restart_fcitx_desktop
status=$?
echo "STATUS=$status DEFERRED=$FCITX_RESTART_DEFERRED"
exit "$status"
'''], env=env, text=True, capture_output=True)
            deferred = not allowed or (desktop == "KDE" and session == "wayland" and failure == 2)
            assert result.returncode == (0 if deferred else failure), result
            assert ("CALLED" in result.stdout) == allowed, result.stdout
            assert f"DEFERRED={'true' if deferred else 'false'}" in result.stdout
            if not allowed:
                assert "重新" in result.stdout

cpp_restart = "Json restart_fcitx_desktop_session() {" + settings.split(
    "Json restart_fcitx_desktop_session() {", 1)[1].split("\n}\n", 1)[0] + "\n}\n"
cpp_update = "void update_fcitx_config(" + settings.split(
    "void update_fcitx_config(", 2)[2].split("\n}\n", 1)[0] + "\n}\n"
harness = r'''
#include <nlohmann/json.hpp>
#include <cstdlib>
#include <filesystem>
#include <iostream>
#include <string>
#include <stdexcept>
#include <vector>
using Json = nlohmann::json;
std::filesystem::path project_root() { return "/fake"; }
int calls = 0;
bool managed = true;
bool fail = false;
namespace vocotype::desktop {
std::string find_executable(const std::string &, const std::vector<std::string> &) {
  return managed ? "/fake/systemctl" : "";
}
}
Json run_command(const std::vector<std::string> &args) {
  ++calls;
  if (args.at(0) != "bash" && args.at(2) == "show")
    return {{"success", true}, {"output", "loaded\n"}};
  return {{"success", !fail}, {"error", "simulated failure"}};
}
bool installed = true;
bool live = false;
int config_writes = 0;
int live_writes = 0;
int verifications = 0;
bool framework_installed(const std::string &) { return installed; }
Json query_live_fcitx_config() { return {{"success", live}, {"error", "offline"}}; }
Json restart_fcitx_with_vocotype() { ++calls; return {{"success", true}}; }
void set_live_fcitx_config(const std::vector<std::pair<std::string, std::string>> &) {
  ++live_writes;
}
void write_fcitx_config_file(const std::vector<std::pair<std::string, std::string>> &) {
  ++config_writes;
}
void verify_fcitx_config_values(const Json &config,
                              const std::vector<std::pair<std::string, std::string>> &values) {
  ++verifications;
  if (config.at("values").at(values.at(0).first) != values.at(0).second)
    throw std::runtime_error("config did not round trip");
}
''' + cpp_restart + cpp_update + r'''
int main() {
  for (const auto &desktop : {"KDE", "GNOME", ""})
    for (const auto &session : {"wayland", "x11", ""})
      for (const auto &wayland : {"wayland-0", ""})
        for (const auto &display : {":0", ""})
          for (bool service : {false, true})
            for (bool failure : {false, true}) {
              setenv("XDG_CURRENT_DESKTOP", desktop, 1);
              setenv("XDG_SESSION_TYPE", session, 1);
              setenv("WAYLAND_DISPLAY", wayland, 1);
              setenv("DISPLAY", display, 1);
              calls = 0; managed = service; fail = failure;
              const auto result = restart_fcitx_desktop_session();
              const bool kde_wayland = std::string(desktop) == "KDE" &&
                                       std::string(session) == "wayland";
              const bool allowed = kde_wayland || (*desktop && std::string(session) == "x11" &&
                                                  !*wayland && *display);
              if (calls != (allowed ? (kde_wayland ? 1 : (service ? 2 : 1)) : 0) ||
                  result.at("success") != (allowed && !failure))
                return 1;
              if (!allowed && (result.at("error").get<std::string>().empty() ||
                               !result.value("manual_required", false)))
                return 2;
            }
  for (bool present : {false, true})
    for (bool running : {false, true}) {
      installed = present; live = running;
      calls = config_writes = live_writes = verifications = 0;
      bool threw = false;
      try {
        update_fcitx_config({{"PTTKey", "Alt_R"}});
      } catch (const std::runtime_error &) {
        threw = true;
      }
      if (calls || threw != (present && !running) || live_writes != int(running) ||
          config_writes != int(!present && !running) || verifications != config_writes)
        return 3;
    }
}
'''
with tempfile.TemporaryDirectory(prefix="vocotype-restart-test-") as directory:
    source = Path(directory) / "restart.cpp"
    binary = Path(directory) / "restart-test"
    source.write_text(harness)
    subprocess.run(["c++", "-std=c++20", str(source), "-o", str(binary)], check=True)
    subprocess.run([str(binary)], check=True)
    shell_install = "install_fcitx() {" + installer.split(
        "install_fcitx() {", 1)[1].split("\n}\n", 1)[0] + "\n}\n"
    for session, failure in [("wayland", 0), ("x11", 0), ("x11", 42)]:
        env = dict(os.environ, XDG_CURRENT_DESKTOP="KDE", XDG_SESSION_TYPE=session,
                   WAYLAND_DISPLAY="wayland-0" if session == "wayland" else "", DISPLAY=":0",
                   USER_SERVICE_DIR=directory, USER_BIN="/unused", SOURCE_MODE="false", SCRIPT_DIR="/fake")
        stubs = f'''
manager_available() {{ return 1; }}
migrate_legacy_fcitx_profile() {{ :; }}
verify_fcitx_addon_enabled() {{ echo VERIFIED; }}
fcitx5() {{ exit 99; }}
bash() {{ echo CALLED; return {failure}; }}
env() {{ echo CALLED; return {failure}; }}
log() {{ printf '%s\\n' "$*"; }}
'''
        result = subprocess.run(["bash", "-c", stubs + shell_restart + shell_install +
                                 "install_fcitx"], env=env, text=True, capture_output=True)
        assert result.returncode == failure, result
        assert ("VERIFIED" in result.stdout) == (not failure), result.stdout

assert 'if (selected_framework(window) == "fcitx5")\n    update_fcitx_config(fcitx_values);' in settings
print("Fcitx restart policy: session, failure and config-save checks passed")
PY

bash scripts/test/kde-input-method.sh
