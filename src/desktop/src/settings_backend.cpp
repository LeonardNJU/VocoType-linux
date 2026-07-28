#include "vocotype/desktop/settings_backend.hpp"

#include "vocotype/common/posix.hpp"
#include "vocotype/common/terms_yaml.hpp"
#include "vocotype/desktop/ipc.hpp"
#include "vocotype/desktop/wav.hpp"

#include <algorithm>
#include <array>
#include <atomic>
#include <chrono>
#include <cerrno>
#include <csignal>
#include <cstdio>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iterator>
#include <random>
#include <sstream>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

#include <curl/curl.h>
#include <sys/wait.h>
#include <unistd.h>

namespace vocotype::desktop::settings {
namespace {

std::string read_file(const std::filesystem::path &path) {
  std::ifstream input(path, std::ios::binary);
  if (!input)
    return {};
  return {std::istreambuf_iterator<char>(input),
          std::istreambuf_iterator<char>()};
}

void write_file_atomic(const std::filesystem::path &path,
                       const std::string &content) {
  std::filesystem::create_directories(path.parent_path());
  const auto temporary = path.string() + ".tmp." + std::to_string(::getpid());
  {
    std::ofstream output(temporary, std::ios::binary | std::ios::trunc);
    if (!output)
      throw std::runtime_error("cannot write " + temporary);
    output.write(content.data(), static_cast<std::streamsize>(content.size()));
    output.flush();
    if (!output)
      throw std::runtime_error("cannot flush " + temporary);
  }
  std::filesystem::permissions(
      temporary,
      std::filesystem::perms::owner_read |
          std::filesystem::perms::owner_write,
      std::filesystem::perm_options::replace);
  std::filesystem::rename(temporary, path);
}

std::string trim_dictionary_value(std::string value) {
  const auto is_space = [](unsigned char character) {
    return character == ' ' || character == '\t' || character == '\r' ||
           character == '\n';
  };
  const auto first = std::find_if_not(value.begin(), value.end(), [&](char ch) {
    return is_space(static_cast<unsigned char>(ch));
  });
  const auto last =
      std::find_if_not(value.rbegin(), value.rend(), [&](char ch) {
        return is_space(static_cast<unsigned char>(ch));
      }).base();
  return first < last ? std::string(first, last) : std::string();
}

std::string default_terms_content() {
  return "# VoCoType-linux 用户词典\nterms: []\nprotect: []\n";
}

std::string yaml_scalar(const std::string &raw) {
  const std::string value = trim_dictionary_value(raw);
  if (value.empty())
    throw std::runtime_error("词条不能为空");
  if (value.find_first_of("\r\n\t") != std::string::npos)
    throw std::runtime_error("词条不能包含换行或制表符");
  const bool needs_quotes = value.front() == '#' || value.back() == ' ' ||
                            value.find(" #") != std::string::npos;
  if (!needs_quotes)
    return value;
  if (value.find('\'') == std::string::npos)
    return "'" + value + "'";
  if (value.find('"') == std::string::npos)
    return "\"" + value + "\"";
  throw std::runtime_error("词条同时包含单双引号和 YAML 注释符，无法安全写入");
}

struct TopLevelSection {
  bool found = false;
  std::size_t header_start = 0;
  std::size_t header_end = 0;
  std::size_t body_end = 0;
  std::string value;
};

bool top_level_mapping(const std::string &line, std::string *key,
                       std::string *value) {
  if (line.empty() || line.front() == ' ' || line.front() == '\t' ||
      line.front() == '#')
    return false;
  const std::size_t colon = line.find(':');
  if (colon == std::string::npos)
    return false;
  if (key)
    *key = trim_dictionary_value(line.substr(0, colon));
  if (value)
    *value = trim_dictionary_value(line.substr(colon + 1));
  return true;
}

TopLevelSection find_top_level_section(const std::string &content,
                                       const std::string &wanted) {
  TopLevelSection result;
  std::size_t line_start = 0;
  while (line_start < content.size()) {
    const std::size_t newline = content.find('\n', line_start);
    const std::size_t line_end =
        newline == std::string::npos ? content.size() : newline;
    std::string line = content.substr(line_start, line_end - line_start);
    if (!line.empty() && line.back() == '\r')
      line.pop_back();
    std::string key;
    std::string value;
    if (top_level_mapping(line, &key, &value) && key == wanted) {
      result.found = true;
      result.header_start = line_start;
      result.header_end =
          newline == std::string::npos ? content.size() : newline + 1;
      result.value = value;
      result.body_end = content.size();
      std::size_t next = result.header_end;
      while (next < content.size()) {
        const std::size_t next_newline = content.find('\n', next);
        const std::size_t next_end =
            next_newline == std::string::npos ? content.size() : next_newline;
        std::string next_line = content.substr(next, next_end - next);
        if (!next_line.empty() && next_line.back() == '\r')
          next_line.pop_back();
        if (top_level_mapping(next_line, nullptr, nullptr)) {
          result.body_end = next;
          break;
        }
        next = next_newline == std::string::npos ? content.size()
                                                  : next_newline + 1;
      }
      return result;
    }
    line_start = newline == std::string::npos ? content.size() : newline + 1;
  }
  return result;
}

void ensure_trailing_newline(std::string &content) {
  if (!content.empty() && content.back() != '\n')
    content.push_back('\n');
}

void append_sequence_block(std::string &content, const std::string &section_name,
                           const std::string &block) {
  TopLevelSection section = find_top_level_section(content, section_name);
  if (!section.found) {
    ensure_trailing_newline(content);
    content += section_name + ":\n" + block;
    return;
  }
  if (section.value == "[]") {
    content.replace(section.header_start, section.header_end - section.header_start,
                    section_name + ":\n" + block);
    return;
  }
  if (!section.value.empty())
    throw std::runtime_error(section_name + " 必须是 YAML 列表");
  std::string insertion = block;
  if (section.body_end > 0 && content[section.body_end - 1] != '\n')
    insertion.insert(insertion.begin(), '\n');
  content.insert(section.body_end, insertion);
}

std::vector<std::string>
normalized_aliases(const std::vector<std::string> &aliases,
                   const std::string &canonical) {
  std::vector<std::string> result;
  for (const auto &raw : aliases) {
    const std::string alias = trim_dictionary_value(raw);
    if (alias.empty() || alias == canonical ||
        std::find(result.begin(), result.end(), alias) != result.end())
      continue;
    (void)yaml_scalar(alias);
    result.push_back(alias);
  }
  return result;
}

Json save_terms_content(const std::string &content) {
  const auto parsed = vocotype::common::parse_terms_yaml_content(content);
  write_file_atomic(terms_path(), content);
  return {{"success", true},
          {"terms", parsed.terms.size()},
          {"protected_phrases", parsed.protected_phrases.size()},
          {"path", terms_path().string()}};
}

std::size_t curl_write(void *contents, std::size_t size, std::size_t count,
                       void *user_data) {
  const std::size_t bytes = size * count;
  static_cast<std::string *>(user_data)->append(
      static_cast<const char *>(contents), bytes);
  return bytes;
}

std::filesystem::path model_manager_path() {
  const auto bundled = runtime_root() / "bin/vocotype-model-manager";
  if (std::filesystem::is_regular_file(bundled))
    return bundled;
  return {};
}

std::string redact_config(Json config) {
  if (config.contains("slm") && config["slm"].is_object()) {
    for (const char *key : {"api_key", "token", "password"}) {
      if (config["slm"].contains(key))
        config["slm"][key] = "<redacted>";
    }
  }
  return config.dump(2);
}

std::string random_identifier() {
  std::array<unsigned char, 16> bytes{};
  std::random_device random;
  for (auto &byte : bytes)
    byte = static_cast<unsigned char>(random());
  bytes[6] = static_cast<unsigned char>((bytes[6] & 0x0fU) | 0x40U);
  bytes[8] = static_cast<unsigned char>((bytes[8] & 0x3fU) | 0x80U);
  std::ostringstream output;
  output << std::hex << std::setfill('0');
  for (std::size_t index = 0; index < bytes.size(); ++index) {
    output << std::setw(2) << static_cast<unsigned int>(bytes[index]);
    if (index == 3 || index == 5 || index == 7 || index == 9)
      output << '-';
  }
  return output.str();
}

std::string installation_id() {
  const auto path = config_dir() / "installation-id";
  std::string value = read_file(path);
  value.erase(std::remove(value.begin(), value.end(), '\n'), value.end());
  value.erase(std::remove(value.begin(), value.end(), '\r'), value.end());
  if (!value.empty())
    return value;
  value = random_identifier();
  write_file_atomic(path, value + "\n");
  return value;
}

bool valid_feedback_endpoint(const std::string &endpoint) {
  return endpoint.starts_with("https://") ||
         endpoint.starts_with("http://127.0.0.1") ||
         endpoint.starts_with("http://localhost") ||
         endpoint.starts_with("http://[::1]");
}

Json core_request(const Json &request, int timeout_ms) {
  if (!ensure_native_core(backend_socket_path(), runtime_config_path(), 45000))
    return {{"success", false}, {"error", "Core 未启动"}};
  try {
    return unix_json_request(backend_socket_path(), request, timeout_ms);
  } catch (const std::exception &error) {
    return {{"success", false}, {"error", error.what()}};
  }
}

void add_check(Json &checks, const std::string &id, const std::string &title,
               bool success, const std::string &details) {
  checks.push_back({{"check_id", id},
                    {"title", title},
                    {"status", success ? "pass" : "fail"},
                    {"details", details}});
}

std::string checks_report(const Json &checks) {
  std::ostringstream output;
  output << "VoCoType Native Doctor\n\n";
  for (const auto &check : checks) {
    output << (check.value("status", "fail") == "pass" ? "[PASS] "
                                                           : "[FAIL] ")
           << check.value("title", "") << " — "
           << check.value("details", "") << '\n';
  }
  return output.str();
}

} // namespace

Json run_process(const std::vector<std::string> &arguments) {
  if (arguments.empty())
    return {{"success", false}, {"error", "empty command"}};
  int descriptors[2];
  if (vocotype::common::create_pipe_close_on_exec(descriptors) != 0)
    return {{"success", false}, {"error", "cannot create process pipe"}};
  const pid_t pid = ::fork();
  if (pid < 0) {
    ::close(descriptors[0]);
    ::close(descriptors[1]);
    return {{"success", false}, {"error", "cannot fork process"}};
  }
  if (pid == 0) {
    ::close(descriptors[0]);
    (void)::dup2(descriptors[1], STDOUT_FILENO);
    (void)::dup2(descriptors[1], STDERR_FILENO);
    ::close(descriptors[1]);
    std::vector<char *> argv;
    argv.reserve(arguments.size() + 1);
    for (const auto &argument : arguments)
      argv.push_back(const_cast<char *>(argument.c_str()));
    argv.push_back(nullptr);
    ::execvp(argv.front(), argv.data());
    _exit(127);
  }
  ::close(descriptors[1]);
  std::string output;
  std::array<char, 16384> buffer{};
  while (true) {
    const ssize_t count = ::read(descriptors[0], buffer.data(), buffer.size());
    if (count > 0) {
      output.append(buffer.data(), static_cast<std::size_t>(count));
      continue;
    }
    break;
  }
  ::close(descriptors[0]);
  int status = 0;
  while (::waitpid(pid, &status, 0) < 0 && errno == EINTR) {
  }
  const int exit_code = WIFEXITED(status) ? WEXITSTATUS(status) : 128;
  return {{"success", exit_code == 0},
          {"exit_code", exit_code},
          {"output", output},
          {"error", exit_code == 0 ? "" : output}};
}

Json capture_recording(int duration_ms, const WaveformCallback &callback) {
  try {
    const auto config = load_audio_config();
    const auto device = resolve_input_device(config);
    const int rate = resolve_sample_rate(device, config.sample_rate);
    std::atomic_bool stop{false};
    std::vector<std::int16_t> samples;
    std::string capture_error;
    AudioCapture capture(device, rate, config.block_ms);
    std::thread worker([&] {
      try {
        capture.run(stop, [&](const auto &block) {
          samples.insert(samples.end(), block.begin(), block.end());
          if (callback && !block.empty()) {
            const auto [minimum, maximum] =
                std::minmax_element(block.begin(), block.end());
            callback(static_cast<double>(*minimum) / 32768.0,
                     static_cast<double>(*maximum) / 32768.0);
          }
        });
      } catch (const std::exception &error) {
        capture_error = error.what();
        stop.store(true);
      }
    });
    std::this_thread::sleep_for(
        std::chrono::milliseconds(std::max(1, duration_ms)));
    stop.store(true);
    worker.join();
    if (!capture_error.empty())
      return {{"success", false}, {"error", capture_error}};
    if (samples.empty())
      return {{"success", false}, {"error", "没有采集到音频"}};
    const auto path = create_secure_wav_path();
    write_pcm16_wav(path, samples, rate);
    return {{"success", true},
            {"path", path.string()},
            {"sample_rate", rate},
            {"frames", samples.size()},
            {"device_id", device.id},
            {"device", device.name}};
  } catch (const std::exception &error) {
    return {{"success", false}, {"error", error.what()}};
  }
}

Json play_recording(const std::filesystem::path &path, int output_device_id) {
  try {
    const auto wav = read_pcm16_wav(path);
    const auto output = resolve_output_device(output_device_id);
    play_pcm16(wav.samples, wav.sample_rate, output);
    return {{"success", true},
            {"device", output.name},
            {"sample_rate", wav.sample_rate},
            {"frames", wav.samples.size()}};
  } catch (const std::exception &error) {
    return {{"success", false}, {"error", error.what()}};
  }
}

Json transcribe_recording(const std::filesystem::path &path, bool long_mode) {
  return core_request({{"type", "transcribe"},
                       {"audio_path", path.string()},
                       {"long_mode", long_mode}},
                      180000);
}

Json normalize_text(const std::string &text) {
  if (text.empty())
    return {{"success", false}, {"error", "待规范化文本不能为空"}};
  return core_request({{"type", "normalize_text"}, {"text", text}}, 30000);
}

Json polish_text(const std::string &text) {
  if (text.empty())
    return {{"success", false}, {"error", "待润色文本不能为空"}};
  return core_request({{"type", "polish_text"}, {"text", text}}, 180000);
}

Json edit_recording(const std::filesystem::path &path,
                    const std::string &context_text,
                    const std::string &context_id) {
  const auto code_points = static_cast<int>(std::count_if(
      context_text.begin(), context_text.end(), [](unsigned char byte) {
        return (byte & 0xc0U) != 0x80U;
      }));
  const int cursor = code_points;
  return core_request(
      {{"type", "edit_audio"},
       {"audio_path", path.string()},
       {"context_id", context_id},
       {"replace_state", "supported"},
       {"supports_surrounding", true},
       {"snapshot",
        {{"text", context_text},
         {"cursor_pos", cursor},
         {"anchor_pos", cursor},
         {"selected_text", ""}}}},
      180000);
}

Json test_ai() {
  return polish_text("这是一次 VoCoType AI 润色连接测试。");
}

std::string load_terms() { return read_file(terms_path()); }

Json validate_and_save_terms(const std::string &content) {
  try {
    return save_terms_content(content);
  } catch (const std::exception &error) {
    return {{"success", false}, {"error", error.what()}};
  }
}

Json append_term(const std::string &canonical_raw,
                 const std::vector<std::string> &aliases_raw, bool hotword,
                 bool protect) {
  try {
    const std::string canonical = trim_dictionary_value(canonical_raw);
    const std::string canonical_yaml = yaml_scalar(canonical);
    std::string content = load_terms();
    if (trim_dictionary_value(content).empty())
      content = default_terms_content();
    const auto current = vocotype::common::parse_terms_yaml_content(content);
    const auto duplicate = std::find_if(
        current.terms.begin(), current.terms.end(), [&](const auto &term) {
          return term.canonical == canonical;
        });
    if (duplicate != current.terms.end())
      return {{"success", false}, {"error", "该标准词已存在"}};

    const auto aliases = normalized_aliases(aliases_raw, canonical);
    std::string block = "  - canonical: " + canonical_yaml + "\n";
    if (aliases.empty()) {
      block += "    aliases: []\n";
    } else {
      block += "    aliases:\n";
      for (const auto &alias : aliases)
        block += "      - " + yaml_scalar(alias) + "\n";
    }
    block += std::string("    hotword: ") + (hotword ? "true\n" : "false\n");
    block += std::string("    protect: ") + (protect ? "true\n" : "false\n");
    append_sequence_block(content, "terms", block);
    Json result = save_terms_content(content);
    result["canonical"] = canonical;
    result["aliases"] = aliases.size();
    result["hotword"] = hotword;
    result["protect"] = protect;
    return result;
  } catch (const std::exception &error) {
    return {{"success", false}, {"error", error.what()}};
  }
}

Json append_protected_phrase(const std::string &phrase_raw) {
  try {
    const std::string phrase = trim_dictionary_value(phrase_raw);
    const std::string phrase_yaml = yaml_scalar(phrase);
    std::string content = load_terms();
    if (trim_dictionary_value(content).empty())
      content = default_terms_content();
    const auto current = vocotype::common::parse_terms_yaml_content(content);
    if (std::find(current.protected_phrases.begin(),
                  current.protected_phrases.end(), phrase) !=
        current.protected_phrases.end())
      return {{"success", false}, {"error", "该保护词已存在"}};
    append_sequence_block(content, "protect", "  - " + phrase_yaml + "\n");
    Json result = save_terms_content(content);
    result["phrase"] = phrase;
    return result;
  } catch (const std::exception &error) {
    return {{"success", false}, {"error", error.what()}};
  }
}

Json import_terms(const std::filesystem::path &path) {
  try {
    if (!std::filesystem::is_regular_file(path))
      throw std::runtime_error("所选文件不存在");
    const std::string content = read_file(path);
    if (trim_dictionary_value(content).empty())
      throw std::runtime_error("所选词典为空");
    Json result = save_terms_content(content);
    result["source"] = path.string();
    return result;
  } catch (const std::exception &error) {
    return {{"success", false}, {"error", error.what()}};
  }
}

Json reload_terms() {
  try {
    std::string content = load_terms();
    if (trim_dictionary_value(content).empty())
      content = default_terms_content();
    Json result = save_terms_content(content);
    result["reloaded"] = true;
    return result;
  } catch (const std::exception &error) {
    return {{"success", false}, {"error", error.what()}};
  }
}

Json query_latest_release(const std::string &version) {
  CURL *curl = curl_easy_init();
  if (!curl)
    return {{"success", false}, {"error", "无法初始化 libcurl"}};
  std::string response;
  curl_easy_setopt(
      curl, CURLOPT_URL,
      "https://api.github.com/repos/LeonardNJU/VocoType-linux/releases/latest");
  curl_easy_setopt(curl, CURLOPT_FOLLOWLOCATION, 1L);
  curl_easy_setopt(curl, CURLOPT_FAILONERROR, 1L);
  curl_easy_setopt(curl, CURLOPT_CONNECTTIMEOUT, 10L);
  curl_easy_setopt(curl, CURLOPT_TIMEOUT, 20L);
  const std::string agent = "VoCoType-native-settings/" + version;
  curl_easy_setopt(curl, CURLOPT_USERAGENT, agent.c_str());
  curl_easy_setopt(curl, CURLOPT_WRITEFUNCTION, curl_write);
  curl_easy_setopt(curl, CURLOPT_WRITEDATA, &response);
  const CURLcode result = curl_easy_perform(curl);
  long status = 0;
  curl_easy_getinfo(curl, CURLINFO_RESPONSE_CODE, &status);
  curl_easy_cleanup(curl);
  if (result != CURLE_OK)
    return {{"success", false}, {"error", curl_easy_strerror(result)}};
  try {
    const Json value = Json::parse(response);
    return {{"success", true},
            {"current", version},
            {"latest", value.value("tag_name", "unknown")},
            {"url", value.value("html_url", "")},
            {"published_at", value.value("published_at", "")},
            {"http_status", status}};
  } catch (const std::exception &error) {
    return {{"success", false}, {"error", error.what()}};
  }
}

Json model_status() {
  const auto manager = model_manager_path();
  if (manager.empty())
    return {{"success", false}, {"error", "找不到模型管理器"}};
  return run_process({manager.string(), "--check", "--all"});
}

Json download_models() {
  const auto manager = model_manager_path();
  if (manager.empty())
    return {{"success", false}, {"error", "找不到模型管理器"}};
  return run_process({manager.string(), "--download", "--all"});
}

Json overview_status(const std::string &version) {
  const auto resources = runtime_root();
  const bool core = std::filesystem::is_regular_file(resources / "bin/vocotype-core");
  const bool recorder =
      std::filesystem::is_regular_file(resources / "bin/vocotype-audio-recorder");
  const bool offline =
      std::filesystem::is_regular_file(resources / "bin/vocotype-offline-worker");
  const bool streaming = std::filesystem::is_regular_file(
      resources / "bin/vocotype-streaming-worker");
  Json result{{"success", core && recorder && offline},
              {"version", version},
              {"runtime_root", resources.string()},
              {"core_present", core},
              {"recorder_present", recorder},
              {"offline_worker_present", offline},
              {"streaming_worker_present", streaming},
              {"core_ready", native_core_ready()},
              {"config_path", runtime_config_path().string()},
              {"terms_path", terms_path().string()}};
  try {
    const auto inventory = list_audio_devices();
    result["input_devices"] = inventory.inputs.size();
    result["output_devices"] = inventory.outputs.size();
  } catch (const std::exception &error) {
    result["audio_error"] = error.what();
  }
  return result;
}

Json run_doctor(const std::string &version) {
  Json checks = Json::array();
  const auto resources = runtime_root();
  const auto check_file = [&](const std::string &id, const std::string &title,
                              const std::filesystem::path &path) {
    add_check(checks, id, title, std::filesystem::is_regular_file(path),
              path.string());
  };
  check_file("native_core", "C++ core", resources / "bin/vocotype-core");
  check_file("audio_recorder", "原生录音器",
             resources / "bin/vocotype-audio-recorder");
  check_file("offline_worker", "离线 ASR worker",
             resources / "bin/vocotype-offline-worker");
  check_file("streaming_worker", "流式 ASR worker",
             resources / "bin/vocotype-streaming-worker");
  add_check(checks, "runtime_config", "runtime config",
            std::filesystem::is_regular_file(runtime_config_path()),
            runtime_config_path().string());
  add_check(checks, "terms", "用户词典",
            std::filesystem::is_regular_file(terms_path()),
            terms_path().string());
  try {
    const auto devices = list_input_devices();
    add_check(checks, "audio_devices", "音频设备", !devices.empty(),
              std::to_string(devices.size()) + " 个输入设备");
  } catch (const std::exception &error) {
    add_check(checks, "audio_devices", "音频设备", false, error.what());
  }
  add_check(checks, "core_socket", "native core socket", native_core_ready(),
            backend_socket_path());
  const Json models = model_status();
  add_check(checks, "models", "ASR 模型", models.value("success", false),
            models.value("success", false) ? "全部模型通过 SHA-256 校验"
                                             : models.value("error", models.value("output", "校验失败")));
  const Json processes = run_process({"ps", "-axo", "command="});
  const std::string process_text = processes.value("output", "");
  const bool python_runtime = process_text.find("python") != std::string::npos &&
                              process_text.find("vocotype") != std::string::npos;
  add_check(checks, "zero_python", "零 Python 运行时", !python_runtime,
            python_runtime ? "发现 VoCoType Python 进程"
                           : "未发现 VoCoType Python 进程");
  const std::string report = checks_report(checks);
  const bool success = std::none_of(checks.begin(), checks.end(), [](const Json &check) {
    return check.value("status", "fail") != "pass";
  });
  return {{"success", success},
          {"version", version},
          {"checks", checks},
          {"report", report}};
}

std::filesystem::path support_directory() {
  auto directory = config_dir() / "support";
  std::filesystem::create_directories(directory);
  return directory;
}

Json create_support_bundle(const std::string &doctor,
                           const std::string &version) {
  const auto directory = support_directory();
  const auto stamp = std::to_string(
      std::chrono::duration_cast<std::chrono::seconds>(
          std::chrono::system_clock::now().time_since_epoch())
          .count());
  const auto staging = directory / (".bundle-" + stamp);
  const auto archive = directory / ("vocotype-support-" + stamp + ".tar.gz");
  try {
    std::filesystem::create_directories(staging);
    write_file_atomic(staging / "doctor.txt", doctor);
    write_file_atomic(staging / "version.txt", "version=" + version + "\n");
    write_file_atomic(staging / "runtime-config.redacted.json",
                      redact_config(read_shared_config(true)) + "\n");
    const Json processes = run_process({"ps", "-axo", "pid,ppid,lstart,command"});
    write_file_atomic(staging / "processes.txt", processes.value("output", ""));
#if defined(__APPLE__)
    const Json logs = run_process({"log", "show", "--last", "30m", "--style",
                                   "compact", "--predicate",
                                   "process CONTAINS[c] \"VoCoType\""});
#else
    const Json logs = run_process(
        {"journalctl", "--user", "-b", "--no-pager", "-n", "500"});
#endif
    write_file_atomic(staging / "logs.txt", logs.value("output", ""));
    const Json packed = run_process(
        {"tar", "-czf", archive.string(), "-C", staging.string(), "."});
    std::filesystem::remove_all(staging);
    if (!packed.value("success", false))
      return {{"success", false},
              {"error", packed.value("error", "tar 失败")}};
    return {{"success", true}, {"path", archive.string()}};
  } catch (const std::exception &error) {
    std::filesystem::remove_all(staging);
    return {{"success", false}, {"error", error.what()}};
  }
}

Json submit_feedback(const FeedbackRequest &request) {
  if (request.message.empty())
    return {{"success", false}, {"error", "反馈内容不能为空"}};
  if (request.message.size() > 10000U)
    return {{"success", false}, {"error", "反馈内容不能超过 10000 字"}};
  if (!valid_feedback_endpoint(request.endpoint))
    return {{"success", false},
            {"error", "反馈端点必须使用 HTTPS（localhost 调试除外）"}};
  if (!request.bundle.empty() &&
      (!std::filesystem::is_regular_file(request.bundle) ||
       std::filesystem::file_size(request.bundle) > 5U * 1024U * 1024U))
    return {{"success", false}, {"error", "支持包不存在或超过 5 MiB"}};

  Json payload{{"schema_version", 1},
               {"product", "VoCoType-linux"},
               {"version", request.version},
               {"category", request.category.empty() ? "other" : request.category},
               {"message", request.message},
               {"installation_id", installation_id()},
               {"doctor", request.doctor.empty()
                              ? Json(nullptr)
                              : Json::array({{{"check_id", "native_doctor"},
                                              {"status", "info"},
                                              {"title", "Native Doctor"},
                                              {"details", request.doctor}}})},
               {"contact", request.contact}};
  payload["platform"] = run_process({"uname", "-a"}).value("output", "unknown");

  CURL *curl = curl_easy_init();
  if (!curl)
    return {{"success", false}, {"error", "无法初始化 libcurl"}};
  curl_mime *mime = curl_mime_init(curl);
  curl_mimepart *part = curl_mime_addpart(mime);
  const std::string payload_text = payload.dump();
  curl_mime_name(part, "payload");
  curl_mime_type(part, "application/json; charset=utf-8");
  curl_mime_data(part, payload_text.c_str(), payload_text.size());
  if (!request.bundle.empty()) {
    part = curl_mime_addpart(mime);
    curl_mime_name(part, "bundle");
    curl_mime_type(part, "application/gzip");
    curl_mime_filedata(part, request.bundle.c_str());
  }
  std::string response;
  curl_easy_setopt(curl, CURLOPT_URL, request.endpoint.c_str());
  curl_easy_setopt(curl, CURLOPT_MIMEPOST, mime);
  curl_easy_setopt(curl, CURLOPT_CONNECTTIMEOUT, 10L);
  curl_easy_setopt(curl, CURLOPT_TIMEOUT, 30L);
  curl_easy_setopt(curl, CURLOPT_FAILONERROR, 1L);
  const std::string agent = "VoCoType-native-settings/" + request.version;
  curl_easy_setopt(curl, CURLOPT_USERAGENT, agent.c_str());
  curl_easy_setopt(curl, CURLOPT_WRITEFUNCTION, curl_write);
  curl_easy_setopt(curl, CURLOPT_WRITEDATA, &response);
  const CURLcode result = curl_easy_perform(curl);
  long status = 0;
  curl_easy_getinfo(curl, CURLINFO_RESPONSE_CODE, &status);
  curl_mime_free(mime);
  curl_easy_cleanup(curl);
  if (result != CURLE_OK)
    return {{"success", false},
            {"error", std::string(curl_easy_strerror(result))},
            {"http_status", status}};
  return {{"success", true}, {"http_status", status}, {"response", response}};
}

} // namespace vocotype::desktop::settings
