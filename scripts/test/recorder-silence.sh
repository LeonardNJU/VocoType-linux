#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."

python3 - <<'PY'
from pathlib import Path
import subprocess
import tempfile

source = Path("src/desktop/src/audio_recorder_main.cpp").read_text()
branch = "if (!has_usable_microphone_signal(finished)) {" + source.split(
    "if (!has_usable_microphone_signal(finished)) {", 1)[1].split(
    "\n    const auto path = create_secure_wav_path();", 1)[0]
test = r'''
#include "vocotype/desktop/audio_quality.hpp"
#include <atomic>
#include <condition_variable>
#include <mutex>
#include <stdexcept>
#include <string>
#include <thread>
using namespace vocotype::desktop;
int main() {
  for (bool preview : {false, true}) {
    std::vector<std::int16_t> finished(16000, 0);
    std::atomic_bool preview_accepting{true};
    std::mutex queue_mutex;
    std::vector<int> preview_queue{1};
    bool preview_done = false;
    std::condition_variable queue_cv;
    std::thread preview_thread;
    if (preview)
      preview_thread = std::thread([] {});
    bool reported_silence = false;
    try {
''' + branch + r'''
    } catch (const std::exception &error) {
      reported_silence = std::string(error.what()).find("microphone signal is near zero") != std::string::npos;
    }
    if (preview_thread.joinable())
      preview_thread.join();
    if (!reported_silence || preview_accepting || !preview_queue.empty() || !preview_done)
      return 1;
  }
}
'''
with tempfile.TemporaryDirectory(prefix="vocotype-silence-test-") as directory:
    path = Path(directory) / "test.cpp"
    binary = Path(directory) / "test"
    path.write_text(test)
    subprocess.run(["c++", "-std=c++20", "-pthread", "-I", "src/desktop/include",
                    str(path), "-o", str(binary)], check=True)
    subprocess.run([str(binary)], check=True)
print("Recorder silence: original microphone error preserved with preview on and off")
PY
