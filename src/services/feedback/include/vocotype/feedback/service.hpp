#ifndef VOCOTYPE_FEEDBACK_SERVICE_HPP
#define VOCOTYPE_FEEDBACK_SERVICE_HPP

#include <nlohmann/json.hpp>

#include <chrono>
#include <cstddef>
#include <filesystem>
#include <stdexcept>
#include <string>
#include <string_view>
#include <vector>

namespace vocotype::feedback {

using Json = nlohmann::json;

inline constexpr std::size_t kMaxMessageCharacters = 10'000;
inline constexpr std::size_t kMaxContactCharacters = 320;
inline constexpr std::size_t kMaxPlatformCharacters = 512;
inline constexpr std::size_t kMaxDoctorBytes = 128U * 1024U;
inline constexpr std::size_t kMaxBundleBytes = 5U * 1024U * 1024U;
inline constexpr std::size_t kMaxRequestBytes =
    6U * 1024U * 1024U + 256U * 1024U;

class FeedbackError final : public std::runtime_error {
public:
  FeedbackError(std::string message, int status_code = 400,
                std::string error_code = "invalid_request");

  [[nodiscard]] int status_code() const noexcept { return status_code_; }
  [[nodiscard]] const std::string &error_code() const noexcept {
    return error_code_;
  }

private:
  int status_code_;
  std::string error_code_;
};

struct Config {
  std::filesystem::path data_dir = "/var/lib/vocotype-feedback";
  std::string hmac_secret;
  int install_hour_limit = 3;
  int install_day_limit = 10;
  int network_hour_limit = 20;
  int global_minute_limit = 100;
  int duplicate_window_hours = 24;

  [[nodiscard]] static Config from_environment();
  void validate() const;
};

struct Attachment {
  std::string original_name;
  std::string suffix;
  std::string data;
};

struct MultipartPart {
  std::string name;
  std::string filename;
  std::string content_type;
  std::string data;
};

struct AcceptedFeedback {
  std::string feedback_id;
  bool duplicate = false;
  int occurrence_count = 1;

  [[nodiscard]] Json to_json() const;
};

[[nodiscard]] Json validate_payload(const Json &raw);
[[nodiscard]] Attachment validate_attachment(std::string filename,
                                             std::string data);
[[nodiscard]] std::vector<MultipartPart>
parse_multipart(std::string_view content_type, std::string_view body);
[[nodiscard]] std::string base64_decode(std::string_view encoded);
[[nodiscard]] std::string now_iso();

class Store {
public:
  explicit Store(Config config);

  [[nodiscard]] AcceptedFeedback
  accept(const Json &raw_payload, const std::string &source_ip,
         const Attachment *attachment = nullptr,
         std::chrono::system_clock::time_point now =
             std::chrono::system_clock::now());

  [[nodiscard]] Json list_feedback(const std::string &status = {},
                                   int limit = 50) const;
  [[nodiscard]] Json get_feedback(const std::string &feedback_id) const;
  [[nodiscard]] bool update_status(const std::string &feedback_id,
                                   const std::string &status,
                                   const std::string &note = {});
  [[nodiscard]] Json maintenance(int attachment_days = 30,
                                 const std::filesystem::path &backup_dir =
                                     "/var/backups/vocotype-feedback",
                                 int backup_days = 14,
                                 std::chrono::system_clock::time_point now =
                                     std::chrono::system_clock::now());

  [[nodiscard]] const Config &config() const noexcept { return config_; }

private:
  Config config_;
};

} // namespace vocotype::feedback

#endif
