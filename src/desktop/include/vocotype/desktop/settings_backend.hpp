#pragma once

#include "vocotype/desktop/audio.hpp"
#include "vocotype/desktop/config.hpp"

#include <filesystem>
#include <functional>
#include <string>
#include <vector>

namespace vocotype::desktop::settings {

using WaveformCallback = std::function<void(double minimum, double maximum)>;

struct FeedbackRequest {
  std::string endpoint;
  std::string category;
  std::string contact;
  std::string message;
  std::string doctor;
  std::filesystem::path bundle;
  std::string version;
};

[[nodiscard]] Json run_process(const std::vector<std::string> &arguments);
[[nodiscard]] Json capture_recording(int duration_ms,
                                     const WaveformCallback &callback = {});
[[nodiscard]] Json play_recording(const std::filesystem::path &path,
                                  int output_device_id = -1);
[[nodiscard]] Json transcribe_recording(const std::filesystem::path &path,
                                        bool long_mode = false);
[[nodiscard]] Json normalize_text(const std::string &text);
[[nodiscard]] Json polish_text(const std::string &text);
[[nodiscard]] Json edit_recording(const std::filesystem::path &path,
                                  const std::string &context_text,
                                  const std::string &context_id =
                                      "settings-playground");
[[nodiscard]] Json test_ai();

[[nodiscard]] std::string load_terms();
[[nodiscard]] Json validate_and_save_terms(const std::string &content);
[[nodiscard]] Json append_term(const std::string &canonical,
                               const std::vector<std::string> &aliases,
                               bool hotword, bool protect);
[[nodiscard]] Json append_protected_phrase(const std::string &phrase);
[[nodiscard]] Json import_terms(const std::filesystem::path &path);
[[nodiscard]] Json reload_terms();

[[nodiscard]] Json query_latest_release(const std::string &version);
[[nodiscard]] Json model_status();
[[nodiscard]] Json download_models();
[[nodiscard]] Json overview_status(const std::string &version);
[[nodiscard]] Json run_doctor(const std::string &version);

[[nodiscard]] std::filesystem::path support_directory();
[[nodiscard]] Json create_support_bundle(const std::string &doctor,
                                         const std::string &version);
[[nodiscard]] Json submit_feedback(const FeedbackRequest &request);

} // namespace vocotype::desktop::settings
