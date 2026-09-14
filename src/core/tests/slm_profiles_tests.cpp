#include <arpa/inet.h>
#include <netinet/in.h>
#include <poll.h>
#include <sys/stat.h>
#include <sys/socket.h>
#include <unistd.h>

#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <mutex>
#include <optional>
#include <sstream>
#include <stdexcept>
#include <string>
#include <thread>

#include "vocotype/common/slm_profiles.hpp"
#include "vocotype/core/config.hpp"
#include "vocotype/core/slm_client.hpp"

namespace {

using vocotype::common::Json;
using vocotype::core::SlmClient;

void require(bool condition, const std::string &message) {
  if (!condition) {
    throw std::runtime_error(message);
  }
}

class EnvironmentValue final {
public:
  explicit EnvironmentValue(const char *name) : name_(name) {
    const char *value = std::getenv(name);
    if (value != nullptr) {
      previous_ = value;
    }
  }

  EnvironmentValue(const EnvironmentValue &) = delete;
  EnvironmentValue &operator=(const EnvironmentValue &) = delete;

  ~EnvironmentValue() {
    if (previous_.has_value()) {
      (void)::setenv(name_.c_str(), previous_->c_str(), 1);
    } else {
      (void)::unsetenv(name_.c_str());
    }
  }

  void set(const std::string &value) {
    (void)::setenv(name_.c_str(), value.c_str(), 1);
  }

private:
  std::string name_;
  std::optional<std::string> previous_;
};

class CaptureHttpServer final {
public:
  explicit CaptureHttpServer(bool stream) : stream_(stream) {
    listener_ = ::socket(AF_INET, SOCK_STREAM, 0);
    if (listener_ < 0) {
      throw std::runtime_error("创建 profile 测试 HTTP socket 失败");
    }
    const int reuse = 1;
    (void)::setsockopt(listener_, SOL_SOCKET, SO_REUSEADDR, &reuse,
                       sizeof(reuse));
    sockaddr_in address{};
    address.sin_family = AF_INET;
    address.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    address.sin_port = 0;
    if (::bind(listener_, reinterpret_cast<sockaddr *>(&address),
               sizeof(address)) != 0 ||
        ::listen(listener_, 2) != 0) {
      ::close(listener_);
      listener_ = -1;
      throw std::runtime_error("绑定 profile 测试 HTTP socket 失败");
    }
    socklen_t address_size = sizeof(address);
    if (::getsockname(listener_, reinterpret_cast<sockaddr *>(&address),
                      &address_size) != 0) {
      ::close(listener_);
      listener_ = -1;
      throw std::runtime_error("读取 profile 测试 HTTP 端口失败");
    }
    port_ = ntohs(address.sin_port);
    thread_ = std::thread([this] { serve(); });
  }

  CaptureHttpServer(const CaptureHttpServer &) = delete;
  CaptureHttpServer &operator=(const CaptureHttpServer &) = delete;

  ~CaptureHttpServer() {
    stop_.store(true, std::memory_order_release);
    if (listener_ >= 0) {
      (void)::shutdown(listener_, SHUT_RDWR);
      ::close(listener_);
    }
    if (thread_.joinable()) {
      thread_.join();
    }
    listener_ = -1;
  }

  [[nodiscard]] std::string endpoint() const {
    return "http://127.0.0.1:" + std::to_string(port_) +
           "/v1/chat/completions";
  }

  [[nodiscard]] bool wait_for_request() const {
    std::unique_lock lock(mutex_);
    return received_.wait_for(lock, std::chrono::seconds(2),
                              [this] { return request_payload_.has_value(); });
  }

  [[nodiscard]] Json request_payload() const {
    std::lock_guard lock(mutex_);
    return request_payload_.value_or(Json::object());
  }

private:
  static std::string read_request(int descriptor) {
    std::string request;
    char buffer[4096];
    std::size_t expected_size = 0;
    while (true) {
      const ssize_t count = ::recv(descriptor, buffer, sizeof(buffer), 0);
      if (count <= 0) {
        return request;
      }
      request.append(buffer, static_cast<std::size_t>(count));
      const std::size_t header_end = request.find("\r\n\r\n");
      if (header_end == std::string::npos) {
        continue;
      }
      const std::string key = "Content-Length:";
      const std::size_t start = request.find(key);
      if (start != std::string::npos) {
        const std::size_t value_start = start + key.size();
        const std::size_t value_end = request.find("\r\n", value_start);
        expected_size = header_end + 4U + static_cast<std::size_t>(
                                             std::stoull(request.substr(
                                                 value_start,
                                                 value_end - value_start)));
      }
      if (request.size() >= expected_size) {
        return request;
      }
    }
  }

  static void send_all(int descriptor, const std::string &text) {
    std::size_t offset = 0;
    while (offset < text.size()) {
      const ssize_t count = ::send(descriptor, text.data() + offset,
                                   text.size() - offset, MSG_NOSIGNAL);
      if (count <= 0) {
        return;
      }
      offset += static_cast<std::size_t>(count);
    }
  }

  void serve() {
    while (!stop_.load(std::memory_order_acquire)) {
      pollfd descriptor{listener_, POLLIN, 0};
      const int ready = ::poll(&descriptor, 1, 50);
      if (ready <= 0 || (descriptor.revents & POLLIN) == 0) {
        continue;
      }
      const int client = ::accept(listener_, nullptr, nullptr);
      if (client < 0) {
        continue;
      }
      const std::string request = read_request(client);
      const std::size_t body_start = request.find("\r\n\r\n");
      if (body_start != std::string::npos) {
        try {
          Json payload = Json::parse(request.substr(body_start + 4U));
          {
            std::lock_guard lock(mutex_);
            request_payload_ = std::move(payload);
          }
          received_.notify_all();
        } catch (const Json::exception &) {
        }
      }
      if (stream_) {
        send_all(client,
                 "HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n"
                 "Connection: close\r\n\r\n"
                 "data: {\"choices\":[{\"delta\":{\"content\":\"流式\"}}]}\n\n"
                 "data: {\"choices\":[{\"delta\":{\"content\":\"结果\"}}]}\n\n"
                 "data: [DONE]\n\n");
      } else {
        const std::string body =
            R"({"choices":[{"message":{"content":"同步结果"}}]})";
        send_all(client,
                 "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                 "Connection: close\r\nContent-Length: " +
                     std::to_string(body.size()) + "\r\n\r\n" + body);
      }
      ::close(client);
      return;
    }
  }

  bool stream_ = false;
  int listener_ = -1;
  unsigned short port_ = 0;
  std::atomic<bool> stop_{false};
  mutable std::mutex mutex_;
  mutable std::condition_variable received_;
  std::optional<Json> request_payload_;
  std::thread thread_;
};

void test_default_profile_fidelity() {
  constexpr std::string_view expected =
      R"PROMPT(你是中文语音转写文本的后处理器。

目标：在不改变原意、不新增事实的前提下，做最小必要修正，让文本通顺、自然、易读。

仅允许：
1. 补充/修改/删除标点
2. 调整断句与分句
3. 删除明显口头禅、重复词、无意义语气词
4. 修正明显同音/近音错词、漏字、多字
5. 原句明显不通顺时，做最小限度顺句

核心约束：
- 最小编辑：能不改就不改，能少改就少改
- 含义守恒：不新增事实、细节、观点、结论；不扩写、不解释、不总结
- 技术字符串保真：英文、缩写、模型名、版本号、路径、命令、参数、代码片段按原样优先保留
- 形式保真：技术标识中的大小写、数字、连字符(-)、斜杠(/)、下划线(_)、小数点(.)尽量不改写
- 技术词纠偏：若技术词存在明显转写偏差（同音/近形/单字符误差）且上下文可确定，可做最小字符级修正
- 混排保真：字母数字混合标识保持字母/数字角色，不把字母读音替换成数字或汉字
- 术语优先：若有多个近似写法，优先更常见的技术术语拼写
- 数字规范：默认保留阿拉伯数字，非固定汉语表达不要改成汉字
- 不确定时保留原样，避免误改

输出要求：只输出最终文本，不要任何说明。)PROMPT";
  const Json document = vocotype::common::default_profile_document();
  require(document.value("active", "") == "default",
          "默认 active profile 不正确");
  require(document["profiles"].contains("writer"),
          "内置 writer profile 缺失");
  require(document["vocabulary"].empty(), "默认词汇表不应预置词条");
  require(vocotype::common::compose_profile_prompt(document) == expected,
          "默认提示词与旧版本不一致");
}

void test_profile_validation_and_file_lifecycle(
    const std::filesystem::path &path) {
  EnvironmentValue profile_path("VOCOTYPE_PROFILE_CONFIG");
  profile_path.set(path.string());
  std::filesystem::remove_all(path.parent_path());

  const Json missing = vocotype::common::load_profile_document();
  require(vocotype::common::compose_profile_prompt(missing) ==
              vocotype::common::compose_profile_prompt(
                  vocotype::common::default_profile_document()),
          "缺失 profile 文件没有回退默认配置");

  Json custom = vocotype::common::default_profile_document();
  custom["active"] = "writer";
  custom["metadata"] = { {"owner", "user"}, {"revision", 7} };
  custom["vocabulary"] = Json::array({
      { {"canonical", "智谱"},
        {"aliases", Json::array({"质谱", "质朴"})},
        {"context", "谈论 AI 公司或模型时"},
        {"note", "保留用户扩展字段"} },
  });
  vocotype::common::save_profile_document(custom);
  const Json loaded = vocotype::common::load_profile_document();
  require(loaded.value("active", "") == "writer", "profile 热切换未生效");
  require(loaded["metadata"].value("revision", 0) == 7,
          "save 丢失了用户自定义字段");
  require(loaded["vocabulary"][0].value("note", "") == "保留用户扩展字段",
          "save 丢失了词条自定义字段");
  struct stat metadata {};
  require(::stat(path.c_str(), &metadata) == 0,
          "profile 文件没有写入磁盘");
  require((metadata.st_mode & 0777) == 0600,
          "profile 文件权限不是 0600");

  std::string original;
  {
    std::ifstream input(path, std::ios::binary);
    original.assign(std::istreambuf_iterator<char>(input),
                    std::istreambuf_iterator<char>());
  }
  {
    std::ofstream output(path, std::ios::binary | std::ios::trunc);
    output << "{\"version\":1,\"active\":\"writer\",\"profiles\":";
  }
  bool strict_rejected = false;
  try {
    (void)vocotype::common::load_profile_document_strict();
  } catch (const std::exception &) {
    strict_rejected = true;
  }
  require(strict_rejected, "严格 profile 加载没有报告坏文件");
  std::ostringstream warning;
  auto *old_stderr = std::cerr.rdbuf(warning.rdbuf());
  const Json fallback = vocotype::common::load_profile_document();
  std::cerr.rdbuf(old_stderr);
  require(fallback.value("active", "") == "default",
          "坏 profile 文件没有回退默认配置");
  require(warning.str().find("回退内置默认配置") != std::string::npos,
          "坏 profile 文件没有输出 stderr 警告");
  std::string damaged;
  {
    std::ifstream input(path, std::ios::binary);
    damaged.assign(std::istreambuf_iterator<char>(input),
                   std::istreambuf_iterator<char>());
  }
  require(damaged != original && damaged.find("profiles") != std::string::npos,
          "测试没有确实写入坏 profile 文件");

  bool rejected = false;
  try {
    Json invalid = vocotype::common::default_profile_document();
    invalid["profiles"]["default"]["system_prompt"] = " \n";
    vocotype::common::validate_profile_document(invalid);
  } catch (const std::invalid_argument &) {
    rejected = true;
  }
  require(rejected, "空 system_prompt 未被校验拒绝");
}

Json vocabulary_profile() {
  Json document = vocotype::common::default_profile_document();
  document["active"] = "writer";
  document["vocabulary"] = Json::array({
      { {"canonical", "智谱"},
        {"aliases", Json::array({"质谱", "质朴"})},
        {"context", "谈论 AI 公司或模型时"} },
      { {"canonical", "Claude Code"},
        {"aliases", Json::array({"cloud code"})},
        {"context", "谈论编程代理或代码工具时"} },
  });
  return document;
}

void test_profile_prompt_on_sync_stream_and_edit(
    const std::filesystem::path &path) {
  EnvironmentValue profile_path("VOCOTYPE_PROFILE_CONFIG");
  profile_path.set(path.string());
  const Json document = vocabulary_profile();
  vocotype::common::save_profile_document(document);
  const std::string expected_prompt =
      vocotype::common::compose_profile_prompt(document);

  {
    CaptureHttpServer server(false);
    vocotype::core::SlmConfig config;
    config.enabled = true;
    config.remote_stream = false;
    config.endpoint = server.endpoint();
    config.timeout_ms = 2000;
    SlmClient client(config);
    const auto result = client.polish("质谱公司的模型");
    require(result.success && result.text == "同步结果",
            "非流式 profile 润色失败");
    require(server.wait_for_request(), "非流式服务未收到请求");
    const Json payload = server.request_payload();
    require(payload["messages"][0].value("content", "") == expected_prompt,
            "非流式请求没有使用 profile 与词汇上下文");
  }

  {
    CaptureHttpServer server(true);
    vocotype::core::SlmConfig config;
    config.enabled = true;
    config.remote_stream = true;
    config.endpoint = server.endpoint();
    config.stream_idle_timeout_ms = 2000;
    config.transport_timeout_ms = 5000;
    SlmClient client(config);
    const auto result = client.stream_polish("cloud code 修改代码", false,
                                             [](const auto &) { return true; });
    require(result.success && result.text == "流式结果",
            "流式 profile 润色失败");
    require(server.wait_for_request(), "流式服务未收到请求");
    const Json payload = server.request_payload();
    require(payload["messages"][0].value("content", "") == expected_prompt,
            "流式请求没有使用 profile 与词汇上下文");
  }

  {
    CaptureHttpServer server(false);
    vocotype::core::SlmConfig config;
    config.enabled = true;
    config.endpoint = server.endpoint();
    config.timeout_ms = 2000;
    SlmClient client(config);
    const std::string edit_prompt = "编辑专用 system prompt";
    const auto result = client.complete(edit_prompt, "把原文改好", 32, false);
    require(result.success, "编辑 complete 请求失败");
    require(server.wait_for_request(), "编辑服务未收到请求");
    const Json payload = server.request_payload();
    require(payload["messages"][0].value("content", "") == edit_prompt,
            "编辑 complete 被 profile 污染");
    require(payload["messages"][0].value("content", "").find("智谱") ==
                std::string::npos,
            "编辑 complete 意外附加了词汇表");
  }
}

} // namespace

int main() {
  try {
    test_default_profile_fidelity();
    const std::filesystem::path root =
        std::filesystem::temp_directory_path() /
        ("vocotype-slm-profiles-" + std::to_string(::getpid()));
    const std::filesystem::path path = root / "config" / "slm-profiles.json";
    test_profile_validation_and_file_lifecycle(path);
    test_profile_prompt_on_sync_stream_and_edit(path);
    std::filesystem::remove_all(root);
    std::cout << "slm profile tests passed\n";
    return 0;
  } catch (const std::exception &error) {
    std::cerr << "slm profile tests failed: " << error.what() << '\n';
    return 1;
  }
}
