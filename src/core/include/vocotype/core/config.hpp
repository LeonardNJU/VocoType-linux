#pragma once

#include <array>
#include <cstddef>
#include <filesystem>
#include <string>

#include <nlohmann/json.hpp>

namespace vocotype::core {

using Json = nlohmann::json;

std::string default_server_socket_path();

struct ServerConfig {
  std::string socket_path = default_server_socket_path();
  std::size_t max_request_bytes = 1024U * 1024U;
  int request_timeout_ms = 2000;
};

struct NormalizationConfig {
  bool enabled = true;
  bool compact_dates = true;
  bool compact_times = true;
  bool compact_distances = true;
  bool currency_symbols = true;
};

struct OfflineAsrConfig {
  bool enabled = false;
  std::string worker_path;
  std::string model = "iic/"
                      "speech_paraformer-large-contextual_asr_nat-zh-cn-16k-"
                      "common-vocab8404-onnx";
  std::string model_dir;
  bool use_vad = false;
  std::string vad_model = "iic/speech_fsmn_vad_zh-cn-16k-common-onnx";
  std::string vad_model_dir;
  bool use_punc = true;
  std::string punc_model =
      "iic/punc_ct-transformer_zh-cn-common-vocab272727-onnx";
  std::string punc_model_dir;
  std::string hotword;
  bool itn = true;
  int threads = 2;
  int idle_timeout_ms = 300000;
  int startup_timeout_ms = 30000;
  int request_timeout_ms = 120000;
};

struct StreamingAsrConfig {
  bool enabled = false;
  std::string model =
      "iic/"
      "speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-online-onnx";
  std::string model_dir;
  std::string worker_path;
  int threads = 1;
  std::array<int, 3> chunk_size{5, 10, 5};
  int idle_timeout_ms = 30000;
  int session_idle_timeout_ms = 15000;
  int startup_timeout_ms = 180000;
  int request_timeout_ms = 8000;
  std::size_t max_preview_chunk_bytes = 128U * 1024U;
};

struct SlmConfig {
  bool enabled = false;
  std::string endpoint = "http://127.0.0.1:18080/v1/chat/completions";
  std::string model = "Qwen/Qwen3.5-0.8B";
  int timeout_ms = 20000;
  bool remote_stream = true;
  int stream_idle_timeout_ms = 20000;
  int transport_timeout_ms = 0;
  int remote_max_tokens = 0;
  int min_chars = 8;
  int max_tokens = 128;
  double temperature = 0.0;
  double top_p = 0.9;
  int top_k = 20;
  bool enable_thinking = false;
  bool edit_enabled = true;
  int edit_max_tokens = 1024;
  std::string api_key;
  std::string api_key_env;
  Json extra_headers = Json::object();
  Json extra_body = Json::object();
};

struct AppConfig {
  ServerConfig server;
  OfflineAsrConfig offline_asr;
  NormalizationConfig normalization;
  StreamingAsrConfig streaming_asr;
  SlmConfig slm;
  Json raw = Json::object();
};

Json default_config_json();
Json deep_merge(Json base, const Json &overrides);
std::filesystem::path expand_user_path(const std::filesystem::path &path);
AppConfig parse_config(const Json &value);
AppConfig load_config(const std::filesystem::path &path,
                      bool missing_ok = true);

} // namespace vocotype::core
