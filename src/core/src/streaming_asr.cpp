#include "vocotype/core/streaming_asr.hpp"

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

std::size_t maximum_base64_length(std::size_t decoded_bytes) {
  return 4U * ((decoded_bytes + 2U) / 3U);
}

} // namespace

StreamingAsrProcess::StreamingAsrProcess(StreamingAsrConfig config)
    : config_(std::move(config)) {}

bool StreamingAsrProcess::enabled() const noexcept { return config_.enabled; }

bool StreamingAsrProcess::ready() noexcept { return worker_.ready(); }

Json StreamingAsrProcess::initialize() { return ensure_worker(); }

std::filesystem::path StreamingAsrProcess::resolve_worker_path() const {
  std::vector<std::filesystem::path> candidates;
  const std::string from_environment =
      environment_value("VOCOTYPE_STREAMING_WORKER");
  if (!from_environment.empty()) {
    candidates.emplace_back(expand_user_path(from_environment));
  }
  if (!config_.worker_path.empty()) {
    candidates.emplace_back(expand_user_path(config_.worker_path));
  }

  const std::filesystem::path executable_dir = current_executable_dir();
  if (!executable_dir.empty()) {
    candidates.emplace_back(executable_dir / "vocotype-streaming-worker");
    candidates.emplace_back(executable_dir.parent_path() / "lib" / "vocotype" /
                            "vocotype-streaming-worker");
  }
  const auto from_path = path_candidates("vocotype-streaming-worker");
  candidates.insert(candidates.end(), from_path.begin(), from_path.end());
  candidates.emplace_back("/usr/libexec/vocotype-streaming-worker");
  candidates.emplace_back("/usr/lib/vocotype/vocotype-streaming-worker");
  candidates.emplace_back("/usr/lib64/vocotype/vocotype-streaming-worker");
  candidates.emplace_back(
      "src/workers/funasr/build/bundle/bin/vocotype-streaming-worker");

  for (const auto &candidate : candidates) {
    if (executable_file(candidate)) {
      return std::filesystem::canonical(candidate);
    }
  }
  throw std::runtime_error(
      "vocotype-streaming-worker not found; set VOCOTYPE_STREAMING_WORKER or "
      "asr_streaming.worker_path");
}

std::filesystem::path StreamingAsrProcess::resolve_model_dir() const {
  const std::string from_environment =
      environment_value("FUNASR_STREAMING_MODEL_DIR");
  std::vector<std::filesystem::path> candidates;
  if (!from_environment.empty()) {
    candidates.emplace_back(expand_user_path(from_environment));
  }
  if (!config_.model_dir.empty()) {
    candidates.emplace_back(expand_user_path(config_.model_dir));
  }
  if (!config_.model.empty()) {
    const std::string home = environment_value("HOME");
    if (!home.empty()) {
      candidates.emplace_back(std::filesystem::path(home) / ".cache" /
                              "modelscope" / "hub" / "models" / config_.model);
    }
  }
  for (const auto &candidate : candidates) {
    if (std::filesystem::is_directory(candidate)) {
      return std::filesystem::canonical(candidate);
    }
  }
  throw std::runtime_error(
      "streaming model directory not found; set FUNASR_STREAMING_MODEL_DIR or "
      "asr_streaming.model_dir");
}

std::vector<std::string> StreamingAsrProcess::worker_arguments(
    const std::filesystem::path &model_dir) const {
  return {
      "--model-dir",
      model_dir.string(),
      "--threads",
      std::to_string(config_.threads),
      "--chunk-size",
      std::to_string(config_.chunk_size[0]),
      std::to_string(config_.chunk_size[1]),
      std::to_string(config_.chunk_size[2]),
      "--idle-timeout-ms",
      std::to_string(config_.idle_timeout_ms),
      "--session-idle-timeout-ms",
      std::to_string(config_.session_idle_timeout_ms),
  };
}

Json StreamingAsrProcess::ensure_worker() {
  if (!config_.enabled) {
    return error_response("streaming_disabled");
  }
  try {
    const auto executable = resolve_worker_path();
    const auto model_dir = resolve_model_dir();
    return worker_.start(executable, worker_arguments(model_dir),
                         config_.startup_timeout_ms);
  } catch (const std::exception &error) {
    return error_response(error.what());
  }
}

Json StreamingAsrProcess::start_session() {
  const Json started = ensure_worker();
  if (!started.value("success", false)) {
    return started;
  }
  Json response =
      worker_.request({{"type", "start"}}, config_.startup_timeout_ms);
  if (response.value("success", false)) {
    if (!response.contains("sample_rate")) {
      response["sample_rate"] = 16000;
    }
    if (!response.contains("chunk_samples")) {
      response["chunk_samples"] = config_.chunk_size[1] * 960;
    }
  }
  return response;
}

Json StreamingAsrProcess::feed(const Json &request) {
  const std::string session_id = request.value("session_id", "");
  if (session_id.empty()) {
    return error_response("missing_session_id");
  }
  const auto encoded = request.find("pcm16");
  if (encoded == request.end() || !encoded->is_string()) {
    return error_response("pcm16_must_be_base64_string");
  }
  if (encoded->get_ref<const std::string &>().size() >
      maximum_base64_length(config_.max_preview_chunk_bytes)) {
    return error_response("preview_audio_chunk_too_large");
  }
  if (!worker_.ready()) {
    const Json started = ensure_worker();
    if (!started.value("success", false)) {
      return started;
    }
  }
  return worker_.request({{"type", "feed"},
                          {"session_id", session_id},
                          {"pcm16", *encoded},
                          {"is_final", request.value("is_final", false)}},
                         config_.request_timeout_ms);
}

Json StreamingAsrProcess::close_session(const Json &request) {
  const std::string session_id = request.value("session_id", "");
  if (session_id.empty()) {
    return error_response("missing_session_id");
  }
  if (!worker_.ready()) {
    return error_response("streaming_not_ready");
  }
  return worker_.request({{"type", "close"},
                          {"session_id", session_id},
                          {"flush", request.value("flush", false)}},
                         config_.request_timeout_ms);
}

} // namespace vocotype::core
