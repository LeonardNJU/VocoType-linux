#include "vocotype/feedback/service.hpp"

#include <cassert>
#include <chrono>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <string>

#include <unistd.h>

using vocotype::feedback::Attachment;
using vocotype::feedback::Config;
using vocotype::feedback::FeedbackError;
using vocotype::feedback::Json;
using vocotype::feedback::Store;

namespace {

std::filesystem::path temporary_directory(const std::string &name) {
  const auto path = std::filesystem::temp_directory_path() /
                    ("vocotype-feedback-test-" + name + "-" +
                     std::to_string(static_cast<long>(getpid())));
  std::filesystem::remove_all(path);
  std::filesystem::create_directories(path);
  return path;
}

Config config_for(const std::filesystem::path &path) {
  Config config;
  config.data_dir = path;
  config.hmac_secret = "0123456789abcdef0123456789abcdef0123456789abcdef";
  config.global_minute_limit = 100;
  config.install_hour_limit = 20;
  config.install_day_limit = 50;
  config.network_hour_limit = 50;
  config.duplicate_window_hours = 24;
  return config;
}

Json payload(const std::string &message = "候选框没有显示") {
  return {{"schema_version", 1},
          {"product", "VoCoType-linux"},
          {"version", "3.0.0b2"},
          {"category", "bug"},
          {"message", message},
          {"platform", "Linux test"},
          {"contact", ""},
          {"installation_id", "12345678-1234-1234-1234-123456789abc"},
          {"doctor", Json::array({{{"check_id", "native_core"},
                                   {"status", "fail"},
                                   {"title", "Native core"}}})}};
}

void test_validation() {
  const Json valid = vocotype::feedback::validate_payload(payload());
  assert(valid.at("message") == "候选框没有显示");
  bool rejected = false;
  try {
    Json invalid = payload();
    invalid["doctor"] = "not-an-array";
    (void)vocotype::feedback::validate_payload(invalid);
  } catch (const FeedbackError &error) {
    rejected = error.status_code() == 400;
  }
  assert(rejected);
}

void test_multipart_and_base64() {
  const std::string boundary = "----vocotype-native-boundary";
  const std::string body =
      "--" + boundary +
      "\r\nContent-Disposition: form-data; name=\"payload\"\r\n"
      "Content-Type: application/json\r\n\r\n"
      "{\"message\":\"hello\"}\r\n--" +
      boundary +
      "\r\nContent-Disposition: form-data; name=\"bundle\"; "
      "filename=\"support.tar.gz\"\r\n"
      "Content-Type: application/gzip\r\n\r\n" +
      std::string("\x1f\x8b", 2) + "data\r\n--" + boundary + "--\r\n";
  const auto parts = vocotype::feedback::parse_multipart(
      "multipart/form-data; boundary=" + boundary, body);
  assert(parts.size() == 2U);
  assert(parts[0].name == "payload");
  assert(parts[1].filename == "support.tar.gz");
  assert(vocotype::feedback::base64_decode("UEs=") == "PK");
}

void test_store_duplicate_and_status() {
  const auto directory = temporary_directory("duplicate");
  Store store(config_for(directory));
  const auto first = store.accept(payload(), "127.0.0.1");
  assert(!first.duplicate);
  assert(first.occurrence_count == 1);
  const auto second = store.accept(payload(), "127.0.0.1");
  assert(second.duplicate);
  assert(second.feedback_id == first.feedback_id);
  assert(second.occurrence_count == 2);
  const Json rows = store.list_feedback("new", 10);
  assert(rows.size() == 1U);
  assert(rows[0].at("occurrence_count") == 2);
  assert(store.update_status(first.feedback_id, "triaged", "reproduced"));
  const Json item = store.get_feedback(first.feedback_id);
  assert(item.at("status") == "triaged");
  assert(item.at("doctor").is_array());
  std::filesystem::remove_all(directory);
}

void test_attachment_and_maintenance() {
  const auto directory = temporary_directory("attachment");
  const auto backups = directory / "backups";
  Store store(config_for(directory));
  Attachment attachment = vocotype::feedback::validate_attachment(
      "support.tar.gz", std::string("\x1f\x8b", 2) + "payload");
  const auto old_time =
      std::chrono::system_clock::now() - std::chrono::hours(24 * 40);
  const auto accepted = store.accept(payload("attachment report"), "127.0.0.1",
                                     &attachment, old_time);
  const Json stored = store.get_feedback(accepted.feedback_id);
  assert(stored.at("attachment_path").is_string());
  assert(std::filesystem::is_regular_file(
      stored.at("attachment_path").get<std::string>()));
  const Json maintenance = store.maintenance(30, backups, 14);
  assert(maintenance.at("removed_attachments") == 1);
  assert(std::filesystem::is_regular_file(
      maintenance.at("backup_path").get<std::string>()));
  const Json after = store.get_feedback(accepted.feedback_id);
  assert(after.at("attachment_path").is_null());
  std::filesystem::remove_all(directory);
}

void test_rate_limit() {
  const auto directory = temporary_directory("limit");
  Config config = config_for(directory);
  config.install_hour_limit = 1;
  config.install_day_limit = 2;
  Store store(config);
  (void)store.accept(payload("first"), "127.0.0.1");
  bool limited = false;
  try {
    (void)store.accept(payload("second"), "127.0.0.1");
  } catch (const FeedbackError &error) {
    limited =
        error.status_code() == 429 && error.error_code() == "rate_limited";
  }
  assert(limited);
  std::filesystem::remove_all(directory);
}

} // namespace

int main() {
  test_validation();
  test_multipart_and_base64();
  test_store_duplicate_and_status();
  test_attachment_and_maintenance();
  test_rate_limit();
  std::cout << "feedback tests passed\n";
  return 0;
}
