#pragma once
#include <cstddef>
#include <filesystem>
#include <nlohmann/json.hpp>
#include <string>
#include <sys/types.h>
namespace vocotype::desktop {
using Json = nlohmann::json;
Json unix_json_request(const std::string &socket_path, const Json &request,
                       int timeout_ms = 2000);
std::string base64_encode(const unsigned char *data, std::size_t size);
bool native_core_ready(const std::string &socket_path = {},
                       int timeout_ms = 500);
pid_t start_native_core(const std::string &socket_path = {},
                        const std::filesystem::path &config_path = {});
bool ensure_native_core(const std::string &socket_path = {},
                        const std::filesystem::path &config_path = {},
                        int wait_ms = 45000);
} // namespace vocotype::desktop
