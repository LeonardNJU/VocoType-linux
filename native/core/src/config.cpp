#include "vocotype/core/config.hpp"

#include <algorithm>
#include <cstdlib>
#include <fstream>
#include <stdexcept>

namespace vocotype::core {
namespace {

template <typename T>
T value_or(const Json &object, const char *key, T fallback) {
  if (!object.is_object()) {
    return fallback;
  }
  const auto found = object.find(key);
  if (found == object.end() || found->is_null()) {
    return fallback;
  }
  try {
    return found->get<T>();
  } catch (const Json::exception &) {
    return fallback;
  }
}

std::size_t positive_size(const Json &object, const char *key,
                          std::size_t fallback) {
  const auto value =
      value_or<long long>(object, key, static_cast<long long>(fallback));
  return value > 0 ? static_cast<std::size_t>(value) : fallback;
}

} // namespace

Json default_config_json() {
  return {
      {"core",
       {
           {"socket_path", "/tmp/vocotype-fcitx5.sock"},
           {"max_request_bytes", 1024 * 1024},
           {"request_timeout_ms", 2000},
       }},
      {"asr",
       {
           {"native_enabled", false},
           {"worker_path", ""},
           {"model", "iic/"
                     "speech_paraformer-large-contextual_asr_nat-zh-cn-16k-"
                     "common-vocab8404-onnx"},
           {"model_dir", ""},
           {"use_vad", false},
           {"vad_model", "iic/speech_fsmn_vad_zh-cn-16k-common-onnx"},
           {"vad_model_dir", ""},
           {"use_punc", true},
           {"punc_model",
            "iic/punc_ct-transformer_zh-cn-common-vocab272727-onnx"},
           {"punc_model_dir", ""},
           {"hotword", ""},
           {"itn", true},
           {"intra_op_num_threads", 2},
           {"idle_timeout_s", 60},
           {"startup_timeout_s", 30},
           {"request_timeout_s", 120},
       }},
      {"normalization",
       {
           {"enabled", true},
           {"compact_dates", true},
           {"compact_times", true},
           {"compact_distances", true},
           {"currency_symbols", true},
       }},
      {"asr_streaming",
       {
           {"enabled", false},
           {"model", "iic/"
                     "speech_paraformer-large_asr_nat-zh-cn-16k-common-"
                     "vocab8404-online-onnx"},
           {"model_dir", ""},
           {"worker_path", ""},
           {"intra_op_num_threads", 1},
           {"chunk_size", Json::array({5, 10, 5})},
           {"idle_timeout_s", 30},
           {"session_idle_timeout_s", 15},
           {"startup_timeout_s", 180},
           {"request_timeout_s", 2},
           {"max_preview_chunk_bytes", 128 * 1024},
       }},
      {"slm",
       {
           {"enabled", false},
           {"endpoint", "http://127.0.0.1:18080/v1/chat/completions"},
           {"model", "Qwen/Qwen3.5-0.8B"},
           {"timeout_ms", 20000},
           {"remote_stream", true},
           {"stream_idle_timeout_ms", 20000},
           {"transport_timeout_ms", 0},
           {"remote_max_tokens", 0},
           {"min_chars", 8},
           {"max_tokens", 128},
           {"temperature", 0.0},
           {"top_p", 0.9},
           {"top_k", 20},
           {"enable_thinking", false},
           {"edit_enabled", true},
           {"edit_max_tokens", 1024},
           {"api_key", ""},
           {"api_key_env", ""},
           {"extra_headers", Json::object()},
           {"extra_body", Json::object()},
       }},
  };
}

Json deep_merge(Json base, const Json &overrides) {
  if (!base.is_object() || !overrides.is_object()) {
    return overrides;
  }
  for (const auto &[key, value] : overrides.items()) {
    if (base.contains(key) && base[key].is_object() && value.is_object()) {
      base[key] = deep_merge(base[key], value);
    } else {
      base[key] = value;
    }
  }
  return base;
}

std::filesystem::path expand_user_path(const std::filesystem::path &path) {
  const std::string text = path.string();
  if (text == "~" || text.starts_with("~/")) {
    const char *home = std::getenv("HOME");
    if (home == nullptr || *home == '\0') {
      throw std::runtime_error("HOME is not set; cannot expand config path");
    }
    if (text == "~") {
      return std::filesystem::path(home);
    }
    return std::filesystem::path(home) / text.substr(2);
  }
  return path;
}

AppConfig parse_config(const Json &value) {
  const Json merged = deep_merge(default_config_json(), value);
  AppConfig config;
  config.raw = merged;

  const Json core = merged.value("core", Json::object());
  config.server.socket_path =
      value_or<std::string>(core, "socket_path", config.server.socket_path);
  config.server.max_request_bytes =
      positive_size(core, "max_request_bytes", config.server.max_request_bytes);
  config.server.request_timeout_ms =
      std::max(100, value_or<int>(core, "request_timeout_ms",
                                  config.server.request_timeout_ms));

  const Json normalization = merged.value("normalization", Json::object());
  config.normalization.enabled =
      value_or<bool>(normalization, "enabled", config.normalization.enabled);
  config.normalization.compact_dates = value_or<bool>(
      normalization, "compact_dates", config.normalization.compact_dates);
  config.normalization.compact_times = value_or<bool>(
      normalization, "compact_times", config.normalization.compact_times);
  config.normalization.compact_distances =
      value_or<bool>(normalization, "compact_distances",
                     config.normalization.compact_distances);
  config.normalization.currency_symbols = value_or<bool>(
      normalization, "currency_symbols", config.normalization.currency_symbols);

  const Json streaming = merged.value("asr_streaming", Json::object());
  config.streaming_asr.enabled =
      value_or<bool>(streaming, "enabled", config.streaming_asr.enabled);
  config.streaming_asr.model =
      value_or<std::string>(streaming, "model", config.streaming_asr.model);
  config.streaming_asr.model_dir =
      value_or<std::string>(streaming, "model_dir", "");
  config.streaming_asr.worker_path =
      value_or<std::string>(streaming, "worker_path", "");
  config.streaming_asr.threads =
      std::clamp(value_or<int>(streaming, "intra_op_num_threads", 1), 1, 4);
  const Json chunk_size = streaming.value("chunk_size", Json::array());
  if (chunk_size.is_array() && chunk_size.size() == 3) {
    try {
      const std::array<int, 3> parsed{chunk_size[0].get<int>(),
                                      chunk_size[1].get<int>(),
                                      chunk_size[2].get<int>()};
      if (parsed[0] >= 0 && parsed[1] > 0 && parsed[2] >= 0) {
        config.streaming_asr.chunk_size = parsed;
      }
    } catch (const Json::exception &) {
    }
  }
  const auto seconds_to_ms = [](double seconds, int fallback) {
    if (seconds <= 0.0) {
      return fallback;
    }
    return static_cast<int>(std::clamp(seconds * 1000.0, 1.0, 3600000.0));
  };
  config.streaming_asr.idle_timeout_ms =
      seconds_to_ms(value_or<double>(streaming, "idle_timeout_s", 30.0), 30000);
  config.streaming_asr.session_idle_timeout_ms = seconds_to_ms(
      value_or<double>(streaming, "session_idle_timeout_s", 15.0), 15000);
  config.streaming_asr.startup_timeout_ms = seconds_to_ms(
      value_or<double>(streaming, "startup_timeout_s", 180.0), 180000);
  config.streaming_asr.request_timeout_ms = seconds_to_ms(
      value_or<double>(streaming, "request_timeout_s", 2.0), 2000);
  config.streaming_asr.max_preview_chunk_bytes =
      positive_size(streaming, "max_preview_chunk_bytes",
                    config.streaming_asr.max_preview_chunk_bytes);

  const Json asr = merged.value("asr", Json::object());
  config.offline_asr.enabled =
      value_or<bool>(asr, "native_enabled", config.offline_asr.enabled);
  config.offline_asr.worker_path =
      value_or<std::string>(asr, "worker_path", "");
  config.offline_asr.model =
      value_or<std::string>(asr, "model", config.offline_asr.model);
  config.offline_asr.model_dir = value_or<std::string>(asr, "model_dir", "");
  config.offline_asr.use_vad =
      value_or<bool>(asr, "use_vad", config.offline_asr.use_vad);
  config.offline_asr.vad_model =
      value_or<std::string>(asr, "vad_model", config.offline_asr.vad_model);
  config.offline_asr.vad_model_dir =
      value_or<std::string>(asr, "vad_model_dir", "");
  config.offline_asr.use_punc =
      value_or<bool>(asr, "use_punc", config.offline_asr.use_punc);
  config.offline_asr.punc_model =
      value_or<std::string>(asr, "punc_model", config.offline_asr.punc_model);
  config.offline_asr.punc_model_dir =
      value_or<std::string>(asr, "punc_model_dir", "");
  config.offline_asr.hotword = value_or<std::string>(asr, "hotword", "");
  config.offline_asr.itn = value_or<bool>(asr, "itn", config.offline_asr.itn);
  config.offline_asr.threads =
      std::clamp(value_or<int>(asr, "intra_op_num_threads", 2), 1, 8);
  config.offline_asr.idle_timeout_ms =
      seconds_to_ms(value_or<double>(asr, "idle_timeout_s", 60.0), 60000);
  config.offline_asr.startup_timeout_ms =
      seconds_to_ms(value_or<double>(asr, "startup_timeout_s", 30.0), 30000);
  config.offline_asr.request_timeout_ms =
      seconds_to_ms(value_or<double>(asr, "request_timeout_s", 120.0), 120000);

  const Json slm = merged.value("slm", Json::object());
  config.slm.enabled = value_or<bool>(slm, "enabled", config.slm.enabled);
  config.slm.endpoint =
      value_or<std::string>(slm, "endpoint", config.slm.endpoint);
  config.slm.model = value_or<std::string>(slm, "model", config.slm.model);
  config.slm.timeout_ms =
      std::max(100, value_or<int>(slm, "timeout_ms", config.slm.timeout_ms));
  config.slm.remote_stream =
      value_or<bool>(slm, "remote_stream", config.slm.remote_stream);
  config.slm.stream_idle_timeout_ms =
      std::max(50, value_or<int>(slm, "stream_idle_timeout_ms",
                                 config.slm.stream_idle_timeout_ms));
  config.slm.transport_timeout_ms =
      std::max(0, value_or<int>(slm, "transport_timeout_ms",
                                config.slm.transport_timeout_ms));
  config.slm.remote_max_tokens = std::max(
      0, value_or<int>(slm, "remote_max_tokens", config.slm.remote_max_tokens));
  config.slm.min_chars =
      std::max(0, value_or<int>(slm, "min_chars", config.slm.min_chars));
  config.slm.max_tokens =
      std::max(1, value_or<int>(slm, "max_tokens", config.slm.max_tokens));
  config.slm.temperature =
      value_or<double>(slm, "temperature", config.slm.temperature);
  config.slm.top_p = value_or<double>(slm, "top_p", config.slm.top_p);
  config.slm.top_k = value_or<int>(slm, "top_k", config.slm.top_k);
  config.slm.enable_thinking =
      value_or<bool>(slm, "enable_thinking", config.slm.enable_thinking);
  config.slm.edit_enabled =
      value_or<bool>(slm, "edit_enabled", config.slm.edit_enabled);
  config.slm.edit_max_tokens = std::max(
      1, value_or<int>(slm, "edit_max_tokens", config.slm.edit_max_tokens));
  config.slm.api_key = value_or<std::string>(slm, "api_key", "");
  config.slm.api_key_env = value_or<std::string>(slm, "api_key_env", "");
  config.slm.extra_headers = slm.value("extra_headers", Json::object());
  if (!config.slm.extra_headers.is_object()) {
    config.slm.extra_headers = Json::object();
  }
  config.slm.extra_body = slm.value("extra_body", Json::object());
  if (!config.slm.extra_body.is_object()) {
    config.slm.extra_body = Json::object();
  }
  return config;
}

AppConfig load_config(const std::filesystem::path &path, bool missing_ok) {
  const auto expanded = expand_user_path(path);
  if (!std::filesystem::exists(expanded)) {
    if (missing_ok) {
      return parse_config(Json::object());
    }
    throw std::runtime_error("config file not found: " + expanded.string());
  }

  std::ifstream stream(expanded);
  if (!stream) {
    throw std::runtime_error("failed to open config file: " +
                             expanded.string());
  }
  Json value;
  try {
    stream >> value;
  } catch (const Json::exception &error) {
    throw std::runtime_error("invalid JSON config " + expanded.string() + ": " +
                             error.what());
  }
  if (!value.is_object()) {
    throw std::runtime_error("config root must be a JSON object");
  }
  return parse_config(value);
}

} // namespace vocotype::core
