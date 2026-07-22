#include "vocotype/core/offline_asr.hpp"

#include <unistd.h>

#include <array>
#include <cstdlib>
#include <filesystem>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

namespace vocotype::core {
namespace {

bool executable_file(const std::filesystem::path &path) {
  return std::filesystem::is_regular_file(path) &&
         ::access(path.c_str(), X_OK) == 0;
}

std::string environment_value(const char *name) {
  const char *value = std::getenv(name);
  return value == nullptr ? std::string() : std::string(value);
}

std::vector<std::filesystem::path> path_candidates(const std::string &name) {
  std::vector<std::filesystem::path> result;
  const std::string raw_path = environment_value("PATH");
  std::size_t offset = 0;
  while (offset <= raw_path.size()) {
    const std::size_t separator = raw_path.find(':', offset);
    const std::string directory = raw_path.substr(
        offset, separator == std::string::npos ? std::string::npos
                                               : separator - offset);
    if (!directory.empty()) {
      result.emplace_back(std::filesystem::path(directory) / name);
    }
    if (separator == std::string::npos) {
      break;
    }
    offset = separator + 1;
  }
  return result;
}

std::filesystem::path current_executable_dir() {
  std::array<char, 4096> buffer{};
  const ssize_t count =
      ::readlink("/proc/self/exe", buffer.data(), buffer.size() - 1);
  if (count <= 0) {
    return {};
  }
  buffer[static_cast<std::size_t>(count)] = '\0';
  return std::filesystem::path(buffer.data()).parent_path();
}

Json error_response(const std::string &error) {
  return {{"success", false}, {"error", error}};
}

} // namespace

OfflineAsrProcess::OfflineAsrProcess(OfflineAsrConfig config)
    : config_(std::move(config)) {}

bool OfflineAsrProcess::enabled() const noexcept { return config_.enabled; }

bool OfflineAsrProcess::ready() noexcept { return worker_.ready(); }

Json OfflineAsrProcess::initialize() { return ensure_worker(); }

std::filesystem::path OfflineAsrProcess::resolve_worker_path() const {
  std::vector<std::filesystem::path> candidates;
  const std::string from_environment =
      environment_value("VOCOTYPE_OFFLINE_WORKER");
  if (!from_environment.empty()) {
    candidates.emplace_back(expand_user_path(from_environment));
  }
  if (!config_.worker_path.empty()) {
    candidates.emplace_back(expand_user_path(config_.worker_path));
  }

  const std::filesystem::path executable_dir = current_executable_dir();
  if (!executable_dir.empty()) {
    candidates.emplace_back(executable_dir / "vocotype-offline-worker");
    candidates.emplace_back(executable_dir.parent_path() / "lib" / "vocotype" /
                            "vocotype-offline-worker");
  }
  const auto from_path = path_candidates("vocotype-offline-worker");
  candidates.insert(candidates.end(), from_path.begin(), from_path.end());
  candidates.emplace_back("/usr/libexec/vocotype-offline-worker");
  candidates.emplace_back("/usr/lib/vocotype/vocotype-offline-worker");
  candidates.emplace_back("/usr/lib64/vocotype/vocotype-offline-worker");
  candidates.emplace_back(
      "native/streaming_worker/build/bundle/bin/vocotype-offline-worker");

  for (const auto &candidate : candidates) {
    if (executable_file(candidate)) {
      return std::filesystem::canonical(candidate);
    }
  }
  throw std::runtime_error(
      "vocotype-offline-worker not found; set VOCOTYPE_OFFLINE_WORKER or "
      "asr.worker_path");
}

std::filesystem::path OfflineAsrProcess::resolve_model_dir(
    const std::string &environment_name, const std::string &configured_dir,
    const std::string &model_name, const std::string &label) const {
  std::vector<std::filesystem::path> candidates;
  const std::string from_environment =
      environment_value(environment_name.c_str());
  if (!from_environment.empty()) {
    candidates.emplace_back(expand_user_path(from_environment));
  }
  if (!configured_dir.empty()) {
    candidates.emplace_back(expand_user_path(configured_dir));
  }
  if (!model_name.empty()) {
    const std::string home = environment_value("HOME");
    if (!home.empty()) {
      candidates.emplace_back(std::filesystem::path(home) / ".cache" /
                              "modelscope" / "hub" / "models" / model_name);
    }
  }
  for (const auto &candidate : candidates) {
    if (std::filesystem::is_directory(candidate)) {
      return std::filesystem::canonical(candidate);
    }
  }
  throw std::runtime_error(label +
                           " model directory not found; configure an explicit "
                           "model_dir or install the ModelScope snapshot");
}

std::vector<std::string> OfflineAsrProcess::worker_arguments() const {
  std::vector<std::string> arguments{
      "--asr-model-dir",
      resolve_model_dir("FUNASR_ASR_MODEL_DIR", config_.model_dir,
                        config_.model, "ASR")
          .string(),
      "--threads",
      std::to_string(config_.threads),
      "--idle-timeout-ms",
      std::to_string(config_.idle_timeout_ms),
  };
  if (config_.use_vad) {
    arguments.push_back("--vad-model-dir");
    arguments.push_back(resolve_model_dir("FUNASR_VAD_MODEL_DIR",
                                          config_.vad_model_dir,
                                          config_.vad_model, "VAD")
                            .string());
  }
  if (config_.use_punc) {
    arguments.push_back("--punc-model-dir");
    arguments.push_back(resolve_model_dir("FUNASR_PUNC_MODEL_DIR",
                                          config_.punc_model_dir,
                                          config_.punc_model, "punctuation")
                            .string());
  }
  return arguments;
}

Json OfflineAsrProcess::ensure_worker() {
  if (!config_.enabled) {
    return error_response("native_final_asr_disabled");
  }
  try {
    return worker_.start(resolve_worker_path(), worker_arguments(),
                         config_.startup_timeout_ms);
  } catch (const std::exception &error) {
    return error_response(error.what());
  }
}

Json OfflineAsrProcess::transcribe(const Json &request) {
  if (!config_.enabled) {
    return error_response("native_final_asr_disabled");
  }
  const std::string audio_path = request.value("audio_path", "");
  if (audio_path.empty()) {
    return error_response("missing_audio_path");
  }
  if (!std::filesystem::is_regular_file(expand_user_path(audio_path))) {
    return error_response("audio_file_not_found");
  }
  const Json started = ensure_worker();
  if (!started.value("success", false)) {
    return started;
  }
  return worker_.request(
      {{"type", "transcribe"},
       {"audio_path",
        std::filesystem::canonical(expand_user_path(audio_path)).string()},
       {"hotwords", request.value("hotwords", config_.hotword)},
       {"sampling_rate", request.value("sampling_rate", 16000)},
       {"itn", request.value("itn", config_.itn)}},
      config_.request_timeout_ms);
}

} // namespace vocotype::core
