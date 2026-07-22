#pragma once

#include <cstddef>
#include <filesystem>
#include <string>

#include <nlohmann/json.hpp>

namespace vocotype::core {

using Json = nlohmann::json;

struct ServerConfig {
  std::string socket_path = "/tmp/vocotype-fcitx5.sock";
  std::size_t max_request_bytes = 1024U * 1024U;
  int request_timeout_ms = 2000;
};

struct SlmConfig {
  bool enabled = false;
  std::string endpoint = "http://127.0.0.1:18080/v1/chat/completions";
  std::string model = "Qwen/Qwen3.5-0.8B";
  int timeout_ms = 20000;
  int max_tokens = 128;
  double temperature = 0.0;
  double top_p = 0.9;
  int top_k = 20;
  bool enable_thinking = false;
  std::string api_key;
  std::string api_key_env;
  Json extra_headers = Json::object();
  Json extra_body = Json::object();
};

struct AppConfig {
  ServerConfig server;
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
