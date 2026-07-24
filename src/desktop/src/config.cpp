#include "vocotype/desktop/config.hpp"
#include <algorithm>
#include <cstdlib>
#include <fstream>
#include <sstream>
#include <stdexcept>
#include <system_error>
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
std::filesystem::path shared_config_path() {
  return config_dir() / "config.json";
}
std::filesystem::path ibus_config_path() { return config_dir() / "ibus.json"; }
std::filesystem::path legacy_runtime_config_path() {
  return config_dir() / "fcitx5-backend.json";
}
std::filesystem::path runtime_config_path() {
  if (const char *value = std::getenv("VOCOTYPE_CONFIG"); value && *value)
    return expand_user(value);
  const auto shared = shared_config_path();
  if (std::filesystem::exists(shared))
    return shared;
  const auto legacy = legacy_runtime_config_path();
  if (std::filesystem::exists(legacy))
    return legacy;
  const auto ibus = ibus_config_path();
  if (std::filesystem::exists(ibus)) {
    try {
      const Json value = read_json_file(ibus, false);
      for (auto iterator = value.begin(); iterator != value.end(); ++iterator) {
        if (iterator.key() != "hotkeys")
          return ibus;
      }
    } catch (const std::exception &) {
    }
  }
  return shared;
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

ConfigLayoutMigration migrate_config_layout() {
  ConfigLayoutMigration result;
  if (const char *custom = std::getenv("VOCOTYPE_CONFIG"); custom && *custom)
    return result;

  const auto shared_path = shared_config_path();
  const auto ibus_path = ibus_config_path();
  const auto legacy_path = legacy_runtime_config_path();
  Json legacy = Json::object();
  Json existing_ibus = Json::object();

  if (std::filesystem::is_regular_file(legacy_path))
    legacy = read_json_file(legacy_path, false);
  if (std::filesystem::is_regular_file(ibus_path))
    existing_ibus = read_json_file(ibus_path, false);

  if (!std::filesystem::is_regular_file(shared_path)) {
    Json shared = !legacy.empty() ? legacy : existing_ibus;
    if (!shared.is_object())
      shared = Json::object();
    shared.erase("hotkeys");
    write_json_file_atomic(shared_path, shared);
    result.changed = true;
    result.shared_created = true;
  } else {
    Json shared = read_json_file(shared_path, false);
    if (shared.erase("hotkeys") > 0) {
      write_json_file_atomic(shared_path, shared);
      result.changed = true;
    }
  }

  const Json source_hotkeys =
      existing_ibus.contains("hotkeys") && existing_ibus["hotkeys"].is_object()
          ? existing_ibus["hotkeys"]
          : (legacy.contains("hotkeys") && legacy["hotkeys"].is_object()
                 ? legacy["hotkeys"]
                 : Json::object());
  const Json hotkeys{
      {"transcribe", source_hotkeys.value("transcribe", "F9")},
      {"polish", source_hotkeys.value("polish", "Shift+F9")},
      {"edit", source_hotkeys.value("edit", "Ctrl+F9")},
  };
  const Json normalized_ibus = {{"hotkeys", hotkeys}};
  if (existing_ibus != normalized_ibus) {
    write_json_file_atomic(ibus_path, normalized_ibus);
    result.changed = true;
    result.ibus_normalized = true;
  }

  if (std::filesystem::is_regular_file(legacy_path) &&
      std::filesystem::is_regular_file(shared_path)) {
    const auto archive = legacy_path.string() + ".migrated";
    std::error_code error;
    if (!std::filesystem::exists(archive)) {
      std::filesystem::rename(legacy_path, archive, error);
    } else {
      std::filesystem::remove(legacy_path, error);
    }
    if (error)
      throw std::runtime_error("cannot archive legacy config: " +
                               error.message());
    result.changed = true;
    result.legacy_archived = true;
  }
  return result;
}
Json read_shared_config(bool missing_ok) {
  return read_json_file(runtime_config_path(), missing_ok);
}
Json read_ibus_config(bool missing_ok) {
  return read_json_file(ibus_config_path(), missing_ok);
}
void write_shared_config(Json value) {
  if (!value.is_object())
    throw std::runtime_error("shared config root must be an object");
  value.erase("hotkeys");
  const char *custom = std::getenv("VOCOTYPE_CONFIG");
  write_json_file_atomic(
      custom && *custom ? expand_user(custom) : shared_config_path(), value);
}
void write_ibus_hotkeys(const Json &hotkeys) {
  if (!hotkeys.is_object())
    throw std::runtime_error("IBus hotkeys must be an object");
  const Json normalized{
      {"transcribe", hotkeys.value("transcribe", "F9")},
      {"polish", hotkeys.value("polish", "Shift+F9")},
      {"edit", hotkeys.value("edit", "Ctrl+F9")},
  };
  write_json_file_atomic(ibus_config_path(), Json{{"hotkeys", normalized}});
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
