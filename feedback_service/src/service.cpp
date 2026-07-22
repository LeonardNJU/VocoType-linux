#include "vocotype/feedback/service.hpp"

#include <openssl/evp.h>
#include <openssl/hmac.h>
#include <openssl/rand.h>
#include <sqlite3.h>

#include <algorithm>
#include <array>
#include <cctype>
#include <charconv>
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <iomanip>
#include <optional>
#include <regex>
#include <set>
#include <sstream>
#include <string>
#include <system_error>
#include <vector>

#include <sys/stat.h>
#include <unistd.h>

namespace vocotype::feedback {
namespace {

std::string environment_value(const char *name,
                              const std::string &fallback = {}) {
  const char *value = std::getenv(name);
  return value && *value ? value : fallback;
}

int environment_integer(const char *name, int fallback) {
  const std::string value = environment_value(name);
  if (value.empty())
    return fallback;
  int parsed = fallback;
  const auto result =
      std::from_chars(value.data(), value.data() + value.size(), parsed);
  return result.ec == std::errc{} && result.ptr == value.data() + value.size()
             ? parsed
             : fallback;
}

std::string trim(std::string value) {
  const auto is_space = [](unsigned char character) {
    return std::isspace(character) != 0;
  };
  value.erase(value.begin(),
              std::find_if_not(value.begin(), value.end(), is_space));
  value.erase(std::find_if_not(value.rbegin(), value.rend(), is_space).base(),
              value.end());
  return value;
}

std::string lowercase(std::string value) {
  std::transform(value.begin(), value.end(), value.begin(),
                 [](unsigned char character) {
                   return static_cast<char>(std::tolower(character));
                 });
  return value;
}

std::size_t utf8_character_count(std::string_view value) {
  std::size_t count = 0;
  for (const unsigned char character : value) {
    if ((character & 0xC0U) != 0x80U)
      ++count;
  }
  return count;
}

std::string format_time(std::chrono::system_clock::time_point value,
                        const char *format) {
  const std::time_t raw = std::chrono::system_clock::to_time_t(value);
  std::tm utc{};
  gmtime_r(&raw, &utc);
  std::ostringstream output;
  output << std::put_time(&utc, format);
  return output.str();
}

std::string hex_string(const unsigned char *data, std::size_t size) {
  std::ostringstream output;
  output << std::hex << std::setfill('0');
  for (std::size_t index = 0; index < size; ++index)
    output << std::setw(2) << static_cast<unsigned int>(data[index]);
  return output.str();
}

std::string digest_sha256(std::string_view value) {
  std::array<unsigned char, EVP_MAX_MD_SIZE> digest{};
  unsigned int size = 0;
  if (EVP_Digest(value.data(), value.size(), digest.data(), &size, EVP_sha256(),
                 nullptr) != 1)
    throw std::runtime_error("SHA-256 failed");
  return hex_string(digest.data(), size);
}

std::string digest_hmac(const std::string &secret, std::string_view value) {
  std::array<unsigned char, EVP_MAX_MD_SIZE> digest{};
  unsigned int size = 0;
  if (!HMAC(EVP_sha256(), secret.data(), static_cast<int>(secret.size()),
            reinterpret_cast<const unsigned char *>(value.data()), value.size(),
            digest.data(), &size))
    throw std::runtime_error("HMAC-SHA256 failed");
  return hex_string(digest.data(), size);
}

std::string random_hex(std::size_t byte_count) {
  std::vector<unsigned char> bytes(byte_count);
  if (RAND_bytes(bytes.data(), static_cast<int>(bytes.size())) != 1)
    throw std::runtime_error("secure random generation failed");
  return hex_string(bytes.data(), bytes.size());
}

std::string bounded_text(const Json &raw, const char *field,
                         std::size_t maximum, std::string fallback = {},
                         bool required = false) {
  Json value = raw.contains(field) ? raw.at(field) : Json(std::move(fallback));
  if (value.is_null())
    value = "";
  if (!value.is_string())
    throw FeedbackError(std::string(field) + " 必须是字符串");
  std::string text = trim(value.get<std::string>());
  if (required && text.empty())
    throw FeedbackError(std::string(field) + " 不能为空");
  if (utf8_character_count(text) > maximum)
    throw FeedbackError(std::string(field) + " 超出允许长度", 413,
                        "payload_too_large");
  return text;
}

std::string normalize_message(std::string value) {
  value = lowercase(std::move(value));
  std::string normalized;
  normalized.reserve(value.size());
  bool pending_space = false;
  for (const unsigned char character : value) {
    if (std::isspace(character)) {
      pending_space = !normalized.empty();
      continue;
    }
    if (pending_space) {
      normalized.push_back(' ');
      pending_space = false;
    }
    normalized.push_back(static_cast<char>(character));
  }
  return normalized;
}

std::vector<std::string> doctor_error_ids(const Json &doctor) {
  std::vector<std::string> result;
  if (!doctor.is_array())
    return result;
  for (const auto &entry : doctor) {
    if (!entry.is_object())
      continue;
    const std::string status = lowercase(entry.value("status", ""));
    if (status != "warn" && status != "fail")
      continue;
    std::string identifier = entry.value("check_id", "");
    if (identifier.empty())
      identifier = entry.value("title", "unknown");
    if (identifier.size() > 128U)
      identifier.resize(128U);
    result.push_back(std::move(identifier));
  }
  std::sort(result.begin(), result.end());
  result.erase(std::unique(result.begin(), result.end()), result.end());
  return result;
}

std::string duplicate_key(const Json &payload) {
  Json material{
      {"message", normalize_message(payload.at("message").get<std::string>())},
      {"version", payload.at("version")},
      {"errors", doctor_error_ids(payload.at("doctor"))}};
  return digest_sha256(material.dump());
}

std::string safe_attachment_name(const std::string &name) {
  const std::filesystem::path path(name.empty() ? "support.tar.gz" : name);
  return path.filename().string();
}

class Database final {
public:
  explicit Database(const std::filesystem::path &path) {
    if (sqlite3_open_v2(path.c_str(), &database_,
                        SQLITE_OPEN_READWRITE | SQLITE_OPEN_CREATE,
                        nullptr) != SQLITE_OK) {
      const std::string error =
          database_ ? sqlite3_errmsg(database_) : "unknown SQLite error";
      if (database_)
        sqlite3_close(database_);
      database_ = nullptr;
      throw std::runtime_error("cannot open feedback database: " + error);
    }
    sqlite3_busy_timeout(database_, 10'000);
    execute("PRAGMA foreign_keys=ON; PRAGMA journal_mode=WAL;");
  }

  ~Database() {
    if (database_)
      sqlite3_close(database_);
  }

  Database(const Database &) = delete;
  Database &operator=(const Database &) = delete;

  [[nodiscard]] sqlite3 *get() const noexcept { return database_; }

  void execute(const std::string &sql) {
    char *error = nullptr;
    if (sqlite3_exec(database_, sql.c_str(), nullptr, nullptr, &error) !=
        SQLITE_OK) {
      const std::string message = error ? error : sqlite3_errmsg(database_);
      sqlite3_free(error);
      throw std::runtime_error(message);
    }
  }

private:
  sqlite3 *database_ = nullptr;
};

class Statement final {
public:
  Statement(sqlite3 *database, const char *sql) : database_(database) {
    if (sqlite3_prepare_v2(database, sql, -1, &statement_, nullptr) !=
        SQLITE_OK)
      throw std::runtime_error(sqlite3_errmsg(database));
  }

  ~Statement() { sqlite3_finalize(statement_); }

  Statement(const Statement &) = delete;
  Statement &operator=(const Statement &) = delete;

  void bind_text(int index, const std::string &value) {
    if (sqlite3_bind_text(statement_, index, value.c_str(), -1,
                          SQLITE_TRANSIENT) != SQLITE_OK)
      throw std::runtime_error(sqlite3_errmsg(database_));
  }

  void bind_integer(int index, int value) {
    if (sqlite3_bind_int(statement_, index, value) != SQLITE_OK)
      throw std::runtime_error(sqlite3_errmsg(database_));
  }

  void bind_null(int index) {
    if (sqlite3_bind_null(statement_, index) != SQLITE_OK)
      throw std::runtime_error(sqlite3_errmsg(database_));
  }

  [[nodiscard]] bool step_row() {
    const int result = sqlite3_step(statement_);
    if (result == SQLITE_ROW)
      return true;
    if (result == SQLITE_DONE)
      return false;
    throw std::runtime_error(sqlite3_errmsg(database_));
  }

  void finish() {
    if (step_row())
      throw std::runtime_error("unexpected SQLite row");
  }

  [[nodiscard]] int integer(int column) const {
    return sqlite3_column_int(statement_, column);
  }

  [[nodiscard]] std::string text(int column) const {
    const auto *value = sqlite3_column_text(statement_, column);
    return value ? reinterpret_cast<const char *>(value) : "";
  }

  [[nodiscard]] bool is_null(int column) const {
    return sqlite3_column_type(statement_, column) == SQLITE_NULL;
  }

private:
  sqlite3 *database_;
  sqlite3_stmt *statement_ = nullptr;
};

int scalar_count(sqlite3 *database, const char *sql,
                 const std::vector<std::string> &arguments) {
  Statement statement(database, sql);
  for (std::size_t index = 0; index < arguments.size(); ++index)
    statement.bind_text(static_cast<int>(index + 1U), arguments[index]);
  if (!statement.step_row())
    return 0;
  return statement.integer(0);
}

void initialize_database(const Config &config) {
  std::filesystem::create_directories(config.data_dir / "attachments");
  (void)chmod(config.data_dir.c_str(), 0750);
  (void)chmod((config.data_dir / "attachments").c_str(), 0700);
  const auto database_path = config.data_dir / "feedback.db";
  Database database(database_path);
  database.execute(R"SQL(
CREATE TABLE IF NOT EXISTS feedback(
  id TEXT PRIMARY KEY,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  product_version TEXT NOT NULL,
  category TEXT NOT NULL,
  message TEXT NOT NULL,
  platform TEXT NOT NULL,
  contact TEXT NOT NULL,
  doctor_json TEXT,
  attachment_name TEXT,
  attachment_path TEXT,
  status TEXT NOT NULL DEFAULT 'new',
  internal_note TEXT NOT NULL DEFAULT '',
  occurrence_count INTEGER NOT NULL DEFAULT 1,
  duplicate_key TEXT NOT NULL,
  installation_hash TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS feedback_created_idx
  ON feedback(created_at DESC);
CREATE INDEX IF NOT EXISTS feedback_status_idx
  ON feedback(status, created_at DESC);
CREATE INDEX IF NOT EXISTS feedback_duplicate_idx
  ON feedback(duplicate_key, created_at DESC);
CREATE TABLE IF NOT EXISTS request_events(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at TEXT NOT NULL,
  installation_hash TEXT NOT NULL,
  network_hash TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS request_events_created_idx
  ON request_events(created_at);
CREATE INDEX IF NOT EXISTS request_events_install_idx
  ON request_events(installation_hash, created_at);
CREATE INDEX IF NOT EXISTS request_events_network_idx
  ON request_events(network_hash, created_at);
)SQL");
  (void)chmod(database_path.c_str(), 0600);
}

std::filesystem::path database_path(const Config &config) {
  return config.data_dir / "feedback.db";
}

void rate_limit(const Config &config, sqlite3 *database,
                std::chrono::system_clock::time_point now,
                const std::string &installation_hash,
                const std::string &network_hash) {
  const std::string minute =
      format_time(now - std::chrono::minutes(1), "%Y-%m-%dT%H:%M:%SZ");
  const std::string hour =
      format_time(now - std::chrono::hours(1), "%Y-%m-%dT%H:%M:%SZ");
  const std::string day =
      format_time(now - std::chrono::hours(24), "%Y-%m-%dT%H:%M:%SZ");

  if (scalar_count(database,
                   "SELECT COUNT(*) FROM request_events WHERE created_at>=?",
                   {minute}) >= config.global_minute_limit)
    throw FeedbackError("服务暂时繁忙，请稍后重试", 429, "rate_limited");
  if (scalar_count(database,
                   "SELECT COUNT(*) FROM request_events "
                   "WHERE installation_hash=? AND created_at>=?",
                   {installation_hash, hour}) >= config.install_hour_limit ||
      scalar_count(database,
                   "SELECT COUNT(*) FROM request_events "
                   "WHERE installation_hash=? AND created_at>=?",
                   {installation_hash, day}) >= config.install_day_limit)
    throw FeedbackError("本设备提交过于频繁，请稍后重试", 429, "rate_limited");
  if (scalar_count(database,
                   "SELECT COUNT(*) FROM request_events "
                   "WHERE network_hash=? AND created_at>=?",
                   {network_hash, hour}) >= config.network_hour_limit)
    throw FeedbackError("当前网络提交过于频繁，请稍后重试", 429,
                        "rate_limited");

  Statement insert(
      database,
      "INSERT INTO request_events(created_at,installation_hash,network_hash) "
      "VALUES(?,?,?)");
  insert.bind_text(1, format_time(now, "%Y-%m-%dT%H:%M:%SZ"));
  insert.bind_text(2, installation_hash);
  insert.bind_text(3, network_hash);
  insert.finish();

  Statement cleanup(database, "DELETE FROM request_events WHERE created_at<?");
  cleanup.bind_text(
      1, format_time(now - std::chrono::hours(48), "%Y-%m-%dT%H:%M:%SZ"));
  cleanup.finish();
}

std::filesystem::path write_attachment(const Config &config,
                                       const std::string &feedback_id,
                                       const Attachment &attachment) {
  const auto target =
      config.data_dir / "attachments" / (feedback_id + attachment.suffix);
  const auto temporary =
      target.string() + ".tmp." + std::to_string(static_cast<long>(getpid()));
  {
    std::ofstream output(temporary, std::ios::binary | std::ios::trunc);
    if (!output)
      throw std::runtime_error("cannot create feedback attachment");
    output.write(attachment.data.data(),
                 static_cast<std::streamsize>(attachment.data.size()));
    output.flush();
    if (!output)
      throw std::runtime_error("cannot write feedback attachment");
  }
  (void)chmod(temporary.c_str(), 0600);
  std::filesystem::rename(temporary, target);
  return target;
}

} // namespace

FeedbackError::FeedbackError(std::string message, int status_code,
                             std::string error_code)
    : std::runtime_error(std::move(message)), status_code_(status_code),
      error_code_(std::move(error_code)) {}

Config Config::from_environment() {
  Config config;
  config.data_dir = environment_value("VOCOTYPE_FEEDBACK_DATA_DIR",
                                      "/var/lib/vocotype-feedback");
  config.hmac_secret = environment_value("VOCOTYPE_FEEDBACK_HMAC_SECRET");
  config.install_hour_limit =
      environment_integer("VOCOTYPE_FEEDBACK_INSTALL_HOUR_LIMIT", 3);
  config.install_day_limit =
      environment_integer("VOCOTYPE_FEEDBACK_INSTALL_DAY_LIMIT", 10);
  config.network_hour_limit =
      environment_integer("VOCOTYPE_FEEDBACK_NETWORK_HOUR_LIMIT", 20);
  config.global_minute_limit =
      environment_integer("VOCOTYPE_FEEDBACK_GLOBAL_MINUTE_LIMIT", 100);
  config.duplicate_window_hours =
      environment_integer("VOCOTYPE_FEEDBACK_DUPLICATE_WINDOW_HOURS", 24);
  config.validate();
  return config;
}

void Config::validate() const {
  if (hmac_secret.size() < 32U)
    throw std::runtime_error(
        "VOCOTYPE_FEEDBACK_HMAC_SECRET must contain at least 32 characters");
  if (install_hour_limit <= 0 || install_day_limit <= 0 ||
      network_hour_limit <= 0 || global_minute_limit <= 0 ||
      duplicate_window_hours <= 0)
    throw std::runtime_error("feedback rate limits must be positive");
}

Json AcceptedFeedback::to_json() const {
  return {{"ok", true},
          {"feedback_id", feedback_id},
          {"duplicate", duplicate},
          {"occurrence_count", occurrence_count},
          {"message", "反馈已收到"}};
}

Json validate_payload(const Json &raw) {
  if (!raw.is_object())
    throw FeedbackError("payload 必须是 JSON 对象");
  if (raw.value("schema_version", 1) != 1)
    throw FeedbackError("不支持的 schema_version", 400, "unsupported_schema");

  Json payload;
  payload["schema_version"] = 1;
  payload["product"] = bounded_text(raw, "product", 64, "VoCoType-linux", true);
  if (payload["product"] != "VoCoType-linux")
    throw FeedbackError("product 必须是 VoCoType-linux");
  payload["category"] = bounded_text(raw, "category", 32, "other", true);
  if (!std::set<std::string>{"bug", "feature", "installation", "compatibility",
                             "usability", "other"}
           .contains(payload["category"].get<std::string>()))
    throw FeedbackError("未知反馈类别");
  payload["message"] =
      bounded_text(raw, "message", kMaxMessageCharacters, {}, true);
  payload["version"] = bounded_text(raw, "version", 64, "unknown", true);
  payload["platform"] = bounded_text(raw, "platform", kMaxPlatformCharacters);
  payload["contact"] = bounded_text(raw, "contact", kMaxContactCharacters);
  payload["installation_id"] = bounded_text(raw, "installation_id", 128);

  const std::string installation_id = payload["installation_id"];
  if (!installation_id.empty() &&
      !std::regex_match(installation_id, std::regex("[0-9a-fA-F-]{32,36}")))
    throw FeedbackError("installation_id 格式无效");

  payload["doctor"] = raw.value("doctor", Json(nullptr));
  if (!payload["doctor"].is_null() && !payload["doctor"].is_array())
    throw FeedbackError("doctor 必须是数组或 null");
  if (payload["doctor"].dump().size() > kMaxDoctorBytes)
    throw FeedbackError("Doctor 数据过大", 413, "payload_too_large");
  return payload;
}

Attachment validate_attachment(std::string filename, std::string data) {
  filename = safe_attachment_name(filename);
  const std::string lowered = lowercase(filename);
  std::string suffix;
  if (lowered.ends_with(".tar.gz"))
    suffix = ".tar.gz";
  else if (lowered.ends_with(".tgz"))
    suffix = ".tgz";
  else if (lowered.ends_with(".zip"))
    suffix = ".zip";
  else
    throw FeedbackError("支持包只接受 .tar.gz、.tgz 或 .zip", 400,
                        "unsupported_bundle");

  if (data.size() > kMaxBundleBytes)
    throw FeedbackError("支持包超过 5 MiB", 413, "payload_too_large");
  if ((suffix == ".tar.gz" || suffix == ".tgz") &&
      !(data.size() >= 2U && static_cast<unsigned char>(data[0]) == 0x1FU &&
        static_cast<unsigned char>(data[1]) == 0x8BU))
    throw FeedbackError("支持包扩展名与 gzip 内容不匹配", 400,
                        "invalid_bundle");
  if (suffix == ".zip" && !data.starts_with("PK"))
    throw FeedbackError("支持包扩展名与 zip 内容不匹配", 400, "invalid_bundle");
  return {std::move(filename), std::move(suffix), std::move(data)};
}

std::vector<MultipartPart> parse_multipart(std::string_view content_type,
                                           std::string_view body) {
  const std::string lowered = lowercase(std::string(content_type));
  const auto boundary_position = lowered.find("boundary=");
  if (boundary_position == std::string::npos)
    throw FeedbackError("multipart boundary 缺失");
  std::string boundary =
      trim(std::string(content_type.substr(boundary_position + 9U)));
  if (const auto semicolon = boundary.find(';'); semicolon != std::string::npos)
    boundary.resize(semicolon);
  boundary = trim(std::move(boundary));
  if (boundary.size() >= 2U && boundary.front() == '"' &&
      boundary.back() == '"')
    boundary = boundary.substr(1U, boundary.size() - 2U);
  if (boundary.empty() || boundary.size() > 200U)
    throw FeedbackError("multipart boundary 无效");

  const std::string delimiter = "--" + boundary;
  std::vector<MultipartPart> parts;
  std::size_t position = 0;
  while ((position = body.find(delimiter, position)) !=
         std::string_view::npos) {
    position += delimiter.size();
    if (body.substr(position, 2U) == "--")
      break;
    if (body.substr(position, 2U) == "\r\n")
      position += 2U;
    const auto header_end = body.find("\r\n\r\n", position);
    if (header_end == std::string_view::npos)
      throw FeedbackError("multipart header 无效");
    const std::string headers(body.substr(position, header_end - position));
    const std::size_t data_begin = header_end + 4U;
    auto data_end = body.find("\r\n" + delimiter, data_begin);
    if (data_end == std::string_view::npos)
      throw FeedbackError("multipart body 无效");

    MultipartPart part;
    part.data.assign(body.substr(data_begin, data_end - data_begin));
    std::istringstream header_stream(headers);
    std::string line;
    while (std::getline(header_stream, line)) {
      if (!line.empty() && line.back() == '\r')
        line.pop_back();
      const std::string lowered_line = lowercase(line);
      if (lowered_line.rfind("content-type:", 0) == 0)
        part.content_type = trim(line.substr(line.find(':') + 1U));
      if (lowered_line.rfind("content-disposition:", 0) != 0)
        continue;
      const auto extract = [&](const std::string &key) {
        const auto start = line.find(key + "=\"");
        if (start == std::string::npos)
          return std::string();
        const auto value_start = start + key.size() + 2U;
        const auto value_end = line.find('"', value_start);
        return value_end == std::string::npos
                   ? std::string()
                   : line.substr(value_start, value_end - value_start);
      };
      part.name = extract("name");
      part.filename = extract("filename");
    }
    if (!part.name.empty())
      parts.push_back(std::move(part));
    position = data_end + 2U;
  }
  return parts;
}

std::string base64_decode(std::string_view encoded) {
  if (encoded.empty())
    return {};
  if (encoded.size() > (kMaxBundleBytes * 4U / 3U + 16U))
    throw FeedbackError("支持包编码过大", 413, "payload_too_large");
  if (encoded.size() % 4U != 0U)
    throw FeedbackError("支持包 base64 无效", 400, "invalid_bundle");
  std::string output(encoded.size() / 4U * 3U, '\0');
  const int decoded =
      EVP_DecodeBlock(reinterpret_cast<unsigned char *>(output.data()),
                      reinterpret_cast<const unsigned char *>(encoded.data()),
                      static_cast<int>(encoded.size()));
  if (decoded < 0)
    throw FeedbackError("支持包 base64 无效", 400, "invalid_bundle");
  std::size_t size = static_cast<std::size_t>(decoded);
  if (!encoded.empty() && encoded.back() == '=')
    --size;
  if (encoded.size() >= 2U && encoded[encoded.size() - 2U] == '=')
    --size;
  output.resize(size);
  return output;
}

std::string now_iso() {
  return format_time(std::chrono::system_clock::now(), "%Y-%m-%dT%H:%M:%SZ");
}

Store::Store(Config config) : config_(std::move(config)) {
  config_.validate();
  initialize_database(config_);
}

AcceptedFeedback Store::accept(const Json &raw_payload,
                               const std::string &source_ip,
                               const Attachment *attachment,
                               std::chrono::system_clock::time_point now) {
  const Json payload = validate_payload(raw_payload);
  const std::string installation_id = payload.at("installation_id");
  const std::string installation_value =
      installation_id.empty() ? "network:" + source_ip : installation_id;
  const std::string installation_hash =
      digest_hmac(config_.hmac_secret, "installation:" + installation_value);
  const std::string network_hash =
      digest_hmac(config_.hmac_secret, "network:" + source_ip);
  const std::string key = duplicate_key(payload);
  const std::string timestamp = format_time(now, "%Y-%m-%dT%H:%M:%SZ");

  Database database(database_path(config_));
  database.execute("BEGIN IMMEDIATE");
  try {
    rate_limit(config_, database.get(), now, installation_hash, network_hash);

    if (!attachment) {
      Statement duplicate(database.get(),
                          "SELECT id,occurrence_count FROM feedback "
                          "WHERE duplicate_key=? AND created_at>=? "
                          "ORDER BY created_at DESC LIMIT 1");
      duplicate.bind_text(1, key);
      duplicate.bind_text(
          2,
          format_time(now - std::chrono::hours(config_.duplicate_window_hours),
                      "%Y-%m-%dT%H:%M:%SZ"));
      if (duplicate.step_row()) {
        AcceptedFeedback accepted{duplicate.text(0), true,
                                  duplicate.integer(1) + 1};
        Statement update(
            database.get(),
            "UPDATE feedback SET occurrence_count=?,updated_at=? WHERE id=?");
        update.bind_integer(1, accepted.occurrence_count);
        update.bind_text(2, timestamp);
        update.bind_text(3, accepted.feedback_id);
        update.finish();
        database.execute("COMMIT");
        return accepted;
      }
    }

    AcceptedFeedback accepted{"fb_" + format_time(now, "%Y%m%d%H%M%S") + "_" +
                                  random_hex(5),
                              false, 1};
    std::string attachment_path;
    if (attachment)
      attachment_path =
          write_attachment(config_, accepted.feedback_id, *attachment).string();

    Statement insert(
        database.get(),
        "INSERT INTO feedback("
        "id,created_at,updated_at,product_version,category,message,platform,"
        "contact,doctor_json,attachment_name,attachment_path,status,"
        "occurrence_count,duplicate_key,installation_hash) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,'new',1,?,?)");
    insert.bind_text(1, accepted.feedback_id);
    insert.bind_text(2, timestamp);
    insert.bind_text(3, timestamp);
    insert.bind_text(4, payload.at("version"));
    insert.bind_text(5, payload.at("category"));
    insert.bind_text(6, payload.at("message"));
    insert.bind_text(7, payload.at("platform"));
    insert.bind_text(8, payload.at("contact"));
    if (payload.at("doctor").is_null())
      insert.bind_null(9);
    else
      insert.bind_text(9, payload.at("doctor").dump());
    if (attachment)
      insert.bind_text(10, attachment->original_name);
    else
      insert.bind_null(10);
    if (attachment)
      insert.bind_text(11, attachment_path);
    else
      insert.bind_null(11);
    insert.bind_text(12, key);
    insert.bind_text(13, installation_hash);
    insert.finish();
    database.execute("COMMIT");
    return accepted;
  } catch (...) {
    try {
      database.execute("ROLLBACK");
    } catch (...) {
    }
    throw;
  }
}

Json Store::list_feedback(const std::string &status, int limit) const {
  limit = std::clamp(limit, 1, 500);
  Database database(database_path(config_));
  std::string sql =
      "SELECT id,created_at,category,product_version,status,occurrence_count,"
      "substr(message,1,160) FROM feedback";
  if (!status.empty())
    sql += " WHERE status=?";
  sql += " ORDER BY created_at DESC LIMIT ?";
  Statement statement(database.get(), sql.c_str());
  int bind = 1;
  if (!status.empty())
    statement.bind_text(bind++, status);
  statement.bind_integer(bind, limit);
  Json result = Json::array();
  while (statement.step_row()) {
    result.push_back({{"id", statement.text(0)},
                      {"created_at", statement.text(1)},
                      {"category", statement.text(2)},
                      {"product_version", statement.text(3)},
                      {"status", statement.text(4)},
                      {"occurrence_count", statement.integer(5)},
                      {"summary", statement.text(6)}});
  }
  return result;
}

Json Store::get_feedback(const std::string &feedback_id) const {
  Database database(database_path(config_));
  Statement statement(
      database.get(),
      "SELECT id,created_at,updated_at,product_version,category,message,"
      "platform,contact,doctor_json,attachment_name,attachment_path,status,"
      "internal_note,occurrence_count FROM feedback WHERE id=?");
  statement.bind_text(1, feedback_id);
  if (!statement.step_row())
    return nullptr;
  Json result{{"id", statement.text(0)},
              {"created_at", statement.text(1)},
              {"updated_at", statement.text(2)},
              {"product_version", statement.text(3)},
              {"category", statement.text(4)},
              {"message", statement.text(5)},
              {"platform", statement.text(6)},
              {"contact", statement.text(7)},
              {"attachment_name",
               statement.is_null(9) ? Json(nullptr) : Json(statement.text(9))},
              {"attachment_path", statement.is_null(10)
                                      ? Json(nullptr)
                                      : Json(statement.text(10))},
              {"status", statement.text(11)},
              {"internal_note", statement.text(12)},
              {"occurrence_count", statement.integer(13)}};
  result["doctor"] =
      statement.is_null(8) ? Json(nullptr) : Json::parse(statement.text(8));
  return result;
}

bool Store::update_status(const std::string &feedback_id,
                          const std::string &status, const std::string &note) {
  if (!std::set<std::string>{"new", "triaged", "resolved", "spam"}.contains(
          status))
    throw std::invalid_argument("invalid feedback status");
  Database database(database_path(config_));
  Statement update(database.get(),
                   "UPDATE feedback SET status=?,internal_note=?,updated_at=? "
                   "WHERE id=?");
  update.bind_text(1, status);
  update.bind_text(2, note.substr(0U, 10'000U));
  update.bind_text(3, now_iso());
  update.bind_text(4, feedback_id);
  update.finish();
  return sqlite3_changes(database.get()) > 0;
}

Json Store::maintenance(int attachment_days,
                        const std::filesystem::path &backup_dir,
                        int backup_days,
                        std::chrono::system_clock::time_point now) {
  attachment_days = std::max(1, attachment_days);
  backup_days = std::max(1, backup_days);
  Database database(database_path(config_));
  Statement old_attachments(
      database.get(), "SELECT id,attachment_path FROM feedback "
                      "WHERE attachment_path IS NOT NULL AND created_at<?");
  old_attachments.bind_text(
      1, format_time(now - std::chrono::hours(24 * attachment_days),
                     "%Y-%m-%dT%H:%M:%SZ"));
  std::vector<std::string> removed_ids;
  while (old_attachments.step_row()) {
    std::error_code error;
    std::filesystem::remove(old_attachments.text(1), error);
    if (!error)
      removed_ids.push_back(old_attachments.text(0));
  }
  for (const auto &identifier : removed_ids) {
    Statement clear(
        database.get(),
        "UPDATE feedback SET attachment_name=NULL,attachment_path=NULL,"
        "updated_at=? WHERE id=?");
    clear.bind_text(1, format_time(now, "%Y-%m-%dT%H:%M:%SZ"));
    clear.bind_text(2, identifier);
    clear.finish();
  }

  std::filesystem::create_directories(backup_dir);
  (void)chmod(backup_dir.c_str(), 0700);
  const auto backup_path =
      backup_dir / ("feedback-" + format_time(now, "%Y%m%d-%H%M%S") + ".db");
  sqlite3 *destination = nullptr;
  if (sqlite3_open(backup_path.c_str(), &destination) != SQLITE_OK) {
    if (destination)
      sqlite3_close(destination);
    throw std::runtime_error("cannot create feedback backup");
  }
  sqlite3_backup *backup =
      sqlite3_backup_init(destination, "main", database.get(), "main");
  if (!backup) {
    sqlite3_close(destination);
    throw std::runtime_error("cannot initialize feedback backup");
  }
  const int backup_result = sqlite3_backup_step(backup, -1);
  sqlite3_backup_finish(backup);
  sqlite3_close(destination);
  if (backup_result != SQLITE_DONE)
    throw std::runtime_error("feedback backup failed");
  (void)chmod(backup_path.c_str(), 0600);

  int removed_backups = 0;
  const auto file_cutoff = std::filesystem::file_time_type::clock::now() -
                           std::chrono::hours(24 * backup_days);
  for (const auto &entry : std::filesystem::directory_iterator(backup_dir)) {
    if (!entry.is_regular_file() || entry.path() == backup_path ||
        !entry.path().filename().string().starts_with("feedback-") ||
        entry.path().extension() != ".db")
      continue;
    std::error_code error;
    const auto modified = entry.last_write_time(error);
    if (!error && modified < file_cutoff) {
      std::filesystem::remove(entry.path(), error);
      if (!error)
        ++removed_backups;
    }
  }

  return {{"removed_attachments", removed_ids.size()},
          {"backup_path", backup_path.string()},
          {"removed_backups", removed_backups}};
}

} // namespace vocotype::feedback
