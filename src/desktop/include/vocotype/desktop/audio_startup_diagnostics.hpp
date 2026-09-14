#pragma once
#include <string>
#include <string_view>

namespace vocotype::desktop {
// Presence is only a troubleshooting hint, never proof of a causal conflict.
// Do not terminate another application's processes from the recorder.
inline bool is_known_system_audio_capture_helper(std::string_view path) {
  return path.ends_with("/ChatGPT.app/Contents/Resources/native/system-audio-spectrum");
}
inline std::string microphone_startup_error(bool helper_detected,
                                           int timeout_ms = 8000) {
  const std::string timeout = timeout_ms % 1000 == 0
      ? std::to_string(timeout_ms / 1000) + " 秒"
      : std::to_string(timeout_ms) + " 毫秒";
  if (helper_detected)
    return "麦克风启动超过 " + timeout +
           "；检测到 ChatGPT 音频采集组件可能冲突，请关闭该应用的系统音频采集或退出该应用后重试";
  return "麦克风启动超过 " + timeout +
         "，未收到音频；请检查输入设备及其他音频应用后重试";
}
} // namespace vocotype::desktop
