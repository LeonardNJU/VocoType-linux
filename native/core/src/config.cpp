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
      {"slm",
       {
           {"enabled", false},
           {"endpoint", "http://127.0.0.1:18080/v1/chat/completions"},
           {"model", "Qwen/Qwen3.5-0.8B"},
           {"timeout_ms", 20000},
           {"max_tokens", 128},
           {"temperature", 0.0},
           {"top_p", 0.9},
           {"top_k", 20},
           {"enable_thinking", false},
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

  const Json slm = merged.value("slm", Json::object());
  config.slm.enabled = value_or<bool>(slm, "enabled", config.slm.enabled);
  config.slm.endpoint =
      value_or<std::string>(slm, "endpoint", config.slm.endpoint);
  config.slm.model = value_or<std::string>(slm, "model", config.slm.model);
  config.slm.timeout_ms =
      std::max(100, value_or<int>(slm, "timeout_ms", config.slm.timeout_ms));
  config.slm.max_tokens =
      std::max(1, value_or<int>(slm, "max_tokens", config.slm.max_tokens));
  config.slm.temperature =
      value_or<double>(slm, "temperature", config.slm.temperature);
  config.slm.top_p = value_or<double>(slm, "top_p", config.slm.top_p);
  config.slm.top_k = value_or<int>(slm, "top_k", config.slm.top_k);
  config.slm.enable_thinking =
      value_or<bool>(slm, "enable_thinking", config.slm.enable_thinking);
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
