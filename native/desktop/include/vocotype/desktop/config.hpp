#pragma once
#include <filesystem>
#include <initializer_list>
#include <nlohmann/json.hpp>
#include <optional>
#include <string>
namespace vocotype::desktop {
using Json = nlohmann::json;
struct AudioConfig {
  std::optional<int> device_id;
  std::string device_name;
  int sample_rate = 16000;
  int block_ms = 20;
};
std::filesystem::path home_path();
std::filesystem::path config_dir();
std::filesystem::path runtime_config_path();
std::filesystem::path audio_config_path();
std::filesystem::path terms_path();
std::filesystem::path expand_user(const std::filesystem::path &path);
Json read_json_file(const std::filesystem::path &path, bool missing_ok = true);
void write_json_file_atomic(const std::filesystem::path &path,
                            const Json &value);
AudioConfig load_audio_config(const std::filesystem::path &path = {});
std::string backend_socket_path();
std::string
find_executable(const std::string &name,
                const std::initializer_list<std::filesystem::path> &extra = {});
} // namespace vocotype::desktop
