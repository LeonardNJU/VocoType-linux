#include "vocotype/desktop/config.hpp"
#include <algorithm>
#include <cstdlib>
#include <fstream>
#include <sstream>
#include <stdexcept>
#include <unistd.h>
namespace vocotype::desktop {
namespace {
std::string trim(std::string value) {
  const auto first = value.find_first_not_of(" \t\r\n");
  if (first == std::string::npos)
    return {};
  const auto last = value.find_last_not_of(" \t\r\n");
  return value.substr(first, last - first + 1);
}
} // namespace
std::filesystem::path home_path() {
  if (const char *home = std::getenv("HOME"); home && *home)
    return home;
  throw std::runtime_error("HOME is not set");
}
std::filesystem::path config_dir() {
  if (const char *xdg = std::getenv("XDG_CONFIG_HOME"); xdg && *xdg)
    return std::filesystem::path(xdg) / "vocotype";
  return home_path() / ".config/vocotype";
}
std::filesystem::path runtime_config_path() {
  if (const char *value = std::getenv("VOCOTYPE_CONFIG"); value && *value)
    return expand_user(value);
  const auto fcitx = config_dir() / "fcitx5-backend.json";
  const auto ibus = config_dir() / "ibus.json";
  if (std::filesystem::exists(fcitx))
    return fcitx;
  return ibus;
}
std::filesystem::path audio_config_path() {
  return config_dir() / "audio.conf";
}
std::filesystem::path terms_path() { return config_dir() / "terms.yaml"; }
std::filesystem::path expand_user(const std::filesystem::path &path) {
  const std::string value = path.string();
  if (value == "~")
    return home_path();
  if (value.starts_with("~/"))
    return home_path() / value.substr(2);
  return path;
}
Json read_json_file(const std::filesystem::path &path, bool missing_ok) {
  std::ifstream input(expand_user(path));
  if (!input) {
    if (missing_ok)
      return Json::object();
    throw std::runtime_error("cannot open JSON file: " + path.string());
  }
  Json value;
  input >> value;
  if (!value.is_object())
    throw std::runtime_error("JSON root must be an object");
  return value;
}
void write_json_file_atomic(const std::filesystem::path &path,
                            const Json &value) {
  const auto expanded = expand_user(path);
  std::filesystem::create_directories(expanded.parent_path());
  const auto temporary = expanded.string() + ".tmp." + std::to_string(getpid());
  {
    std::ofstream output(temporary, std::ios::trunc);
    if (!output)
      throw std::runtime_error("cannot write config: " + temporary);
    output << value.dump(2) << '\n';
    output.flush();
    if (!output)
      throw std::runtime_error("failed to flush config: " + temporary);
  }
  std::filesystem::permissions(temporary,
                               std::filesystem::perms::owner_read |
                                   std::filesystem::perms::owner_write,
                               std::filesystem::perm_options::replace);
  std::filesystem::rename(temporary, expanded);
}
AudioConfig load_audio_config(const std::filesystem::path &requested) {
  AudioConfig config;
  const auto json = read_json_file(runtime_config_path(), true);
  if (json.contains("audio") && json["audio"].is_object()) {
    const auto &audio = json["audio"];
    if (audio.contains("device") && audio["device"].is_number_integer())
      config.device_id = audio["device"].get<int>();
    if (audio.contains("device_name") && audio["device_name"].is_string())
      config.device_name = audio["device_name"].get<std::string>();
    config.sample_rate = audio.value("sample_rate", config.sample_rate);
    config.block_ms = audio.value("block_ms", config.block_ms);
  }
  const auto path = requested.empty() ? audio_config_path() : requested;
  std::ifstream input(expand_user(path));
  std::string line;
  bool in_audio = false;
  while (std::getline(input, line)) {
    line = trim(line);
    if (line.empty() || line[0] == '#' || line[0] == ';')
      continue;
    if (line.front() == '[' && line.back() == ']') {
      in_audio = trim(line.substr(1, line.size() - 2)) == "audio";
      continue;
    }
    if (!in_audio)
      continue;
    const auto equals = line.find('=');
    if (equals == std::string::npos)
      continue;
    const auto key = trim(line.substr(0, equals));
    const auto value = trim(line.substr(equals + 1));
    try {
      if (key == "device_id" && !value.empty())
        config.device_id = std::stoi(value);
      else if (key == "device_name")
        config.device_name = value;
      else if (key == "sample_rate")
        config.sample_rate = std::stoi(value);
      else if (key == "block_ms")
        config.block_ms = std::stoi(value);
    } catch (const std::exception &) {
    }
  }
  config.sample_rate = std::clamp(config.sample_rate, 8000, 192000);
  config.block_ms = std::clamp(config.block_ms, 5, 200);
  return config;
}
std::string backend_socket_path() {
  if (const char *value = std::getenv("VOCOTYPE_FCITX5_SOCKET");
      value && *value)
    return value;
  if (const char *value = std::getenv("VOCOTYPE_SOCKET"); value && *value)
    return value;
  return "/tmp/vocotype-fcitx5.sock";
}
std::string
find_executable(const std::string &name,
                const std::initializer_list<std::filesystem::path> &extra) {
  for (const auto &path : extra) {
    const auto expanded = expand_user(path);
    if (::access(expanded.c_str(), X_OK) == 0)
      return expanded.string();
  }
  if (const char *path_env = std::getenv("PATH")) {
    std::stringstream paths(path_env);
    std::string part;
    while (std::getline(paths, part, ':')) {
      const auto candidate = std::filesystem::path(part) / name;
      if (::access(candidate.c_str(), X_OK) == 0)
        return candidate.string();
    }
  }
  return {};
}
} // namespace vocotype::desktop
