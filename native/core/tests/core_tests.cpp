#include <arpa/inet.h>
#include <netinet/in.h>
#include <poll.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>

#include <algorithm>
#include <atomic>
#include <chrono>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <mutex>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

#include "vocotype/core/config.hpp"
#include "vocotype/core/dispatcher.hpp"
#include "vocotype/core/server.hpp"
#include "vocotype/core/voice_edit.hpp"

namespace {

using vocotype::core::AppConfig;
using vocotype::core::CoreDispatcher;
using vocotype::core::Json;
using vocotype::core::UnixJsonServer;
using vocotype::core::VoiceEditPlanner;

void require(bool condition, const std::string &message) {
  if (!condition) {
    throw std::runtime_error(message);
  }
}

std::string request_socket(const std::string &path,
                           const std::string &request) {
  const int descriptor = ::socket(AF_UNIX, SOCK_STREAM, 0);
  if (descriptor < 0) {
    throw std::runtime_error("failed to create client socket");
  }
  sockaddr_un address{};
  address.sun_family = AF_UNIX;
  std::copy(path.begin(), path.end(), address.sun_path);
  if (::connect(descriptor, reinterpret_cast<sockaddr *>(&address),
                sizeof(address)) != 0) {
    ::close(descriptor);
    throw std::runtime_error("failed to connect client socket");
  }
  if (::send(descriptor, request.data(), request.size(), 0) < 0) {
    ::close(descriptor);
    throw std::runtime_error("failed to send request");
  }
  std::string response;
  char buffer[4096];
  while (true) {
    const ssize_t count = ::recv(descriptor, buffer, sizeof(buffer), 0);
    if (count < 0) {
      ::close(descriptor);
      throw std::runtime_error("failed to receive response");
    }
    if (count == 0) {
      break;
    }
    response.append(buffer, static_cast<std::size_t>(count));
  }
  ::close(descriptor);
  return response;
}

class FakeOpenAiServer {
public:
  FakeOpenAiServer(std::string content, int expected_requests)
      : content_(std::move(content)), expected_requests_(expected_requests) {
    listener_ = ::socket(AF_INET, SOCK_STREAM, 0);
    if (listener_ < 0) {
      throw std::runtime_error("failed to create fake HTTP listener");
    }
    const int enabled = 1;
    (void)::setsockopt(listener_, SOL_SOCKET, SO_REUSEADDR, &enabled,
                       sizeof(enabled));
    sockaddr_in address{};
    address.sin_family = AF_INET;
    address.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    address.sin_port = 0;
    if (::bind(listener_, reinterpret_cast<sockaddr *>(&address),
               sizeof(address)) != 0 ||
        ::listen(listener_, 8) != 0) {
      ::close(listener_);
      listener_ = -1;
      throw std::runtime_error("failed to bind fake HTTP listener");
    }
    socklen_t size = sizeof(address);
    if (::getsockname(listener_, reinterpret_cast<sockaddr *>(&address),
                      &size) != 0) {
      ::close(listener_);
      listener_ = -1;
      throw std::runtime_error("failed to read fake HTTP port");
    }
    port_ = ntohs(address.sin_port);
    thread_ = std::jthread([this](std::stop_token stop) { serve(stop); });
  }

  FakeOpenAiServer(const FakeOpenAiServer &) = delete;
  FakeOpenAiServer &operator=(const FakeOpenAiServer &) = delete;

  ~FakeOpenAiServer() {
    thread_.request_stop();
    if (listener_ >= 0) {
      ::close(listener_);
      listener_ = -1;
    }
  }

  [[nodiscard]] std::string endpoint() const {
    return "http://127.0.0.1:" + std::to_string(port_) + "/v1/chat/completions";
  }

  [[nodiscard]] int request_count() const noexcept {
    return request_count_.load();
  }

private:
  static void send_all(int descriptor, const std::string &payload) {
    std::size_t offset = 0;
    while (offset < payload.size()) {
      const ssize_t count = ::send(descriptor, payload.data() + offset,
                                   payload.size() - offset, MSG_NOSIGNAL);
      if (count <= 0) {
        return;
      }
      offset += static_cast<std::size_t>(count);
    }
  }

  static std::size_t content_length(const std::string &request) {
    const std::string key = "Content-Length:";
    const std::size_t start = request.find(key);
    if (start == std::string::npos) {
      return 0;
    }
    const std::size_t value_start = start + key.size();
    const std::size_t end = request.find("\r\n", value_start);
    return static_cast<std::size_t>(
        std::stoull(request.substr(value_start, end - value_start)));
  }

  static void read_request(int descriptor) {
    std::string request;
    char buffer[4096];
    std::size_t expected_size = 0;
    while (true) {
      const ssize_t count = ::recv(descriptor, buffer, sizeof(buffer), 0);
      if (count <= 0) {
        return;
      }
      request.append(buffer, static_cast<std::size_t>(count));
      const std::size_t header_end = request.find("\r\n\r\n");
      if (header_end != std::string::npos) {
        expected_size = header_end + 4U + content_length(request);
        if (request.size() >= expected_size) {
          return;
        }
      }
    }
  }

  void serve(std::stop_token stop) {
    while (!stop.stop_requested() &&
           request_count_.load() < expected_requests_) {
      pollfd descriptor{listener_, POLLIN, 0};
      const int ready = ::poll(&descriptor, 1, 50);
      if (ready <= 0 || (descriptor.revents & POLLIN) == 0) {
        continue;
      }
      const int client = ::accept(listener_, nullptr, nullptr);
      if (client < 0) {
        continue;
      }
      read_request(client);
      const Json body{
          {"choices", Json::array({{{"message", {{"content", content_}}}}})}};
      const std::string encoded = body.dump();
      const std::string response =
          "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
          "Connection: close\r\nContent-Length: " +
          std::to_string(encoded.size()) + "\r\n\r\n" + encoded;
      send_all(client, response);
      ::close(client);
      ++request_count_;
    }
  }

  std::string content_;
  int expected_requests_ = 0;
  int listener_ = -1;
  unsigned short port_ = 0;
  std::atomic<int> request_count_{0};
  std::jthread thread_;
};

void test_voice_edit_plan_validation() {
  const Json replace = VoiceEditPlanner::validate_model_output(
      "```json\n{\"mode\":\"replace\",\"new_text\":\"修改后文本\","
      "\"record_history\":true}\n``` trailing",
      "原文");
  require(replace.value("mode", "") == "replace",
          "replace edit mode was not parsed");
  require(replace.value("new_text", "") == "修改后文本",
          "replace edit text was not parsed");

  const Json actions = VoiceEditPlanner::validate_model_output(
      R"({"action":"keys","actions":[{"key":"pgdn","modifiers":["ctrl","ctrl"],"repeat":999}]})",
      "原文");
  require(actions.value("mode", "") == "key_actions",
          "key action alias was not normalized");
  require(actions["key_actions"][0].value("key", "") == "pagedown",
          "key alias was not normalized");
  require(actions["key_actions"][0].value("repeat", 0) == 100,
          "key repeat was not clamped");
  require(actions["key_actions"][0]["modifiers"].size() == 1,
          "duplicate modifiers were not removed");

  const Json no_op = VoiceEditPlanner::validate_model_output(
      R"({"mode":"no_op","hint":"无需修改"})", "保持原文");
  require(no_op.value("new_text", "") == "保持原文",
          "no-op plan did not preserve original text");
  require(!no_op.value("record_history", true),
          "no-op plan incorrectly records history");

  bool rejected = false;
  try {
    (void)VoiceEditPlanner::validate_model_output(
        R"({"mode":"key_actions","key_actions":[{"key":"f12"}]})", "原文");
  } catch (const std::exception &) {
    rejected = true;
  }
  require(rejected, "unsafe key action was accepted");
}

void test_config_merge() {
  const Json merged = vocotype::core::deep_merge(
      vocotype::core::default_config_json(),
      {{"slm", {{"enabled", true}, {"model", "test-model"}}},
       {"asr",
        {{"native_enabled", true},
         {"intra_op_num_threads", 4},
         {"hotword", "VoCoType"}}},
       {"asr_streaming",
        {{"enabled", true},
         {"intra_op_num_threads", 3},
         {"chunk_size", Json::array({4, 8, 4})}}}});
  const AppConfig config = vocotype::core::parse_config(merged);
  require(config.slm.enabled, "SLM enabled override was lost");
  require(config.slm.model == "test-model", "SLM model override was lost");
  require(config.slm.timeout_ms == 20000,
          "SLM default timeout was not preserved");
  require(config.offline_asr.enabled, "offline ASR enabled override was lost");
  require(config.offline_asr.threads == 4,
          "offline ASR thread override was lost");
  require(config.offline_asr.hotword == "VoCoType",
          "offline ASR hotword override was lost");
  require(config.streaming_asr.enabled,
          "streaming ASR enabled override was lost");
  require(config.streaming_asr.threads == 3,
          "streaming ASR thread override was lost");
  require(config.streaming_asr.chunk_size[1] == 8,
          "streaming ASR chunk override was lost");
}

void test_dispatcher() {
  AppConfig config = vocotype::core::parse_config(Json::object());
  CoreDispatcher dispatcher(config);
  const Json ping = dispatcher.dispatch({{"type", "ping"}});
  require(ping.value("pong", false), "ping did not return pong");
  require(ping.value("backend", "") == "cpp", "ping backend is not cpp");

  const Json polished = dispatcher.dispatch({
      {"type", "polish_text"},
      {"text", "原文"},
  });
  require(polished.value("success", false), "disabled polish should succeed");
  require(polished.value("text", "") == "原文", "disabled polish changed text");
  require(polished.value("reason", "") == "disabled", "wrong disabled reason");

  const Json transcribe = dispatcher.dispatch({{"type", "transcribe"}});
  require(!transcribe.value("success", true),
          "unimplemented ASR reported success");
  require(transcribe.value("error", "") == "native_final_asr_disabled",
          "disabled final ASR boundary is not explicit");
}

void test_offline_asr(const std::filesystem::path &worker_path) {
  const std::filesystem::path root =
      std::filesystem::temp_directory_path() /
      ("vocotype-fake-offline-" + std::to_string(::getpid()));
  const std::filesystem::path model_dir = root / "model";
  const std::filesystem::path audio_path = root / "sample.wav";
  std::filesystem::create_directories(model_dir);
  {
    std::ofstream audio(audio_path, std::ios::binary);
    audio << "RIFFfake";
  }

  AppConfig config = vocotype::core::parse_config(Json::object());
  config.offline_asr.enabled = true;
  config.offline_asr.worker_path = worker_path.string();
  config.offline_asr.model_dir = model_dir.string();
  config.offline_asr.use_vad = false;
  config.offline_asr.use_punc = false;
  config.offline_asr.hotword = "VoCoType 语音输入法";
  config.offline_asr.startup_timeout_ms = 2000;
  config.offline_asr.request_timeout_ms = 1000;

  {
    CoreDispatcher dispatcher(config);
    const Json capabilities = dispatcher.dispatch({{"type", "capabilities"}});
    require(capabilities["features"].value("final_asr", false),
            "final ASR capability was not exposed");

    const Json result = dispatcher.dispatch(
        {{"type", "transcribe"}, {"audio_path", audio_path.string()}});
    require(result.value("success", false), "fake final ASR failed");
    require(result.value("text", "") == "原生最终转写",
            "final ASR text was not forwarded");
    require(result.value("hotwords", "") == "VoCoType 语音输入法",
            "configured hotwords were not forwarded");
    require(result.value("backend", "") == "cpp",
            "final ASR backend marker is missing");

    const Json long_result =
        dispatcher.dispatch({{"type", "transcribe"},
                             {"audio_path", audio_path.string()},
                             {"long_mode", true},
                             {"hotwords", "临时热词"}});
    require(long_result.value("success", false),
            "long-mode final ASR failed with disabled SLM");
    require(long_result.value("text", "") == "原生最终转写",
            "disabled SLM changed final ASR text");
    require(long_result.value("slm_reason", "") == "disabled",
            "long-mode SLM fallback reason is missing");
    require(long_result.value("hotwords", "") == "临时热词",
            "request hotwords did not override configured hotwords");

    const std::filesystem::path async_audio = root / "async.wav";
    {
      std::ofstream audio(async_audio, std::ios::binary);
      audio << "RIFFasync";
    }
    const Json started =
        dispatcher.dispatch({{"type", "transcribe_start"},
                             {"audio_path", async_audio.string()},
                             {"long_mode", true}});
    require(started.value("success", false),
            "async final transcription did not start");
    const std::string task_id = started.value("task_id", "");
    require(!task_id.empty(), "async final transcription task ID is empty");

    Json poll;
    for (int attempt = 0; attempt < 100; ++attempt) {
      poll = dispatcher.dispatch(
          {{"type", "polish_poll"}, {"task_id", task_id}, {"after_seq", 0}});
      if (poll.value("status", "") != "running") {
        break;
      }
      std::this_thread::sleep_for(std::chrono::milliseconds(10));
    }
    require(poll.value("status", "") == "final",
            "async final transcription did not finish");
    require(poll.value("final_text", "") == "原生最终转写",
            "async final text was not forwarded");
    require(poll.value("original_text", "") == "原生最终转写",
            "async original text was not retained");
    require(poll.value("reason", "") == "disabled",
            "async disabled-SLM fallback reason is missing");
    require(poll.value("last_seq", 0) >= 2,
            "async status/final events were not emitted");
    require(!std::filesystem::exists(async_audio),
            "async recording was not removed after completion");
  }

  std::filesystem::remove_all(root);
}

void test_socket_offline(const std::filesystem::path &worker_path) {
  const std::filesystem::path root =
      std::filesystem::temp_directory_path() /
      ("vocotype-fake-offline-socket-" + std::to_string(::getpid()));
  const std::filesystem::path model_dir = root / "model";
  const std::filesystem::path audio_path = root / "sample.wav";
  std::filesystem::create_directories(model_dir);
  {
    std::ofstream audio(audio_path, std::ios::binary);
    audio << "RIFFfake";
  }

  AppConfig config = vocotype::core::parse_config(Json::object());
  config.server.socket_path =
      "/tmp/vocotype-core-final-test-" + std::to_string(::getpid()) + ".sock";
  config.offline_asr.enabled = true;
  config.offline_asr.worker_path = worker_path.string();
  config.offline_asr.model_dir = model_dir.string();
  config.offline_asr.use_vad = false;
  config.offline_asr.use_punc = false;
  config.offline_asr.startup_timeout_ms = 2000;
  config.offline_asr.request_timeout_ms = 1000;

  CoreDispatcher dispatcher(config);
  UnixJsonServer server(config.server, dispatcher);
  std::exception_ptr server_error;
  std::thread thread([&] {
    try {
      server.run();
    } catch (...) {
      server_error = std::current_exception();
    }
  });
  for (int attempt = 0; attempt < 100; ++attempt) {
    if (std::filesystem::exists(config.server.socket_path)) {
      break;
    }
    std::this_thread::sleep_for(std::chrono::milliseconds(10));
  }
  require(std::filesystem::exists(config.server.socket_path),
          "final ASR test socket was not created");

  const Json result = Json::parse(request_socket(
      config.server.socket_path,
      Json({{"type", "transcribe"}, {"audio_path", audio_path.string()}})
          .dump()));
  require(result.value("success", false),
          "socket final transcription request failed");
  require(result.value("text", "") == "原生最终转写",
          "socket final transcription text was not forwarded");

  const std::filesystem::path async_audio = root / "socket-async.wav";
  {
    std::ofstream audio(async_audio, std::ios::binary);
    audio << "RIFFasync";
  }
  const Json started = Json::parse(request_socket(
      config.server.socket_path, Json({{"type", "transcribe_start"},
                                       {"audio_path", async_audio.string()},
                                       {"long_mode", true}})
                                     .dump()));
  require(started.value("success", false),
          "socket async transcription did not start");
  const std::string task_id = started.value("task_id", "");
  Json poll;
  for (int attempt = 0; attempt < 100; ++attempt) {
    poll = Json::parse(request_socket(
        config.server.socket_path,
        Json({{"type", "polish_poll"}, {"task_id", task_id}, {"after_seq", 0}})
            .dump()));
    if (poll.value("status", "") != "running") {
      break;
    }
    std::this_thread::sleep_for(std::chrono::milliseconds(10));
  }
  require(poll.value("status", "") == "final",
          "socket async transcription did not finish");
  require(poll.value("final_text", "") == "原生最终转写",
          "socket async final text was not forwarded");

  server.stop();
  thread.join();
  if (server_error) {
    std::rethrow_exception(server_error);
  }
  std::filesystem::remove_all(root);
}

void test_voice_edit_disabled(const std::filesystem::path &worker_path) {
  const std::filesystem::path root =
      std::filesystem::temp_directory_path() /
      ("vocotype-edit-disabled-" + std::to_string(::getpid()));
  const std::filesystem::path model_dir = root / "model";
  const std::filesystem::path audio_path = root / "edit.wav";
  std::filesystem::create_directories(model_dir);
  {
    std::ofstream audio(audio_path, std::ios::binary);
    audio << "RIFFedit";
  }

  AppConfig config = vocotype::core::parse_config(Json::object());
  config.offline_asr.enabled = true;
  config.offline_asr.worker_path = worker_path.string();
  config.offline_asr.model_dir = model_dir.string();
  config.offline_asr.use_vad = false;
  config.offline_asr.use_punc = false;
  config.offline_asr.startup_timeout_ms = 2000;
  config.offline_asr.request_timeout_ms = 1000;

  {
    CoreDispatcher dispatcher(config);
    const Json started =
        dispatcher.dispatch({{"type", "edit_start"},
                             {"audio_path", audio_path.string()},
                             {"supports_surrounding", true},
                             {"replace_state", "supported"},
                             {"snapshot",
                              {{"text", "这是原文"},
                               {"cursor_pos", 12},
                               {"anchor_pos", 12},
                               {"selected_text", ""}}}});
    require(started.value("success", false),
            "disabled-SLM edit task did not start");
    const std::string task_id = started.value("task_id", "");

    Json poll;
    for (int attempt = 0; attempt < 100; ++attempt) {
      poll = dispatcher.dispatch({{"type", "edit_poll"}, {"task_id", task_id}});
      if (poll.value("status", "") != "running") {
        break;
      }
      std::this_thread::sleep_for(std::chrono::milliseconds(10));
    }
    require(poll.value("status", "") == "error",
            "disabled-SLM edit task did not fail explicitly");
    require(poll.value("phase", "") == "done",
            "failed edit task phase was not finalized");
    require(poll.value("instruction", "") == "原生最终转写",
            "recognized edit instruction was not retained");
    require(poll.value("reason", "") == "edit_disabled",
            "disabled edit reason was not preserved");
    for (int attempt = 0; attempt < 50 && std::filesystem::exists(audio_path);
         ++attempt) {
      std::this_thread::sleep_for(std::chrono::milliseconds(10));
    }
    require(!std::filesystem::exists(audio_path),
            "failed edit task did not remove its recording");
  }
  std::filesystem::remove_all(root);
}

void test_socket_voice_edit(const std::filesystem::path &worker_path) {
  FakeOpenAiServer api(
      R"({"mode":"replace","new_text":"修改后文本","record_history":true,"hint":"已修改"})",
      2);
  const std::filesystem::path root =
      std::filesystem::temp_directory_path() /
      ("vocotype-edit-socket-" + std::to_string(::getpid()));
  const std::filesystem::path model_dir = root / "model";
  const std::filesystem::path sync_audio = root / "sync-edit.wav";
  const std::filesystem::path async_audio = root / "async-edit.wav";
  std::filesystem::create_directories(model_dir);
  {
    std::ofstream audio(sync_audio, std::ios::binary);
    audio << "RIFFsync";
  }
  {
    std::ofstream audio(async_audio, std::ios::binary);
    audio << "RIFFasync";
  }

  AppConfig config = vocotype::core::parse_config(Json::object());
  config.server.socket_path =
      "/tmp/vocotype-core-edit-test-" + std::to_string(::getpid()) + ".sock";
  config.offline_asr.enabled = true;
  config.offline_asr.worker_path = worker_path.string();
  config.offline_asr.model_dir = model_dir.string();
  config.offline_asr.use_vad = false;
  config.offline_asr.use_punc = false;
  config.offline_asr.startup_timeout_ms = 2000;
  config.offline_asr.request_timeout_ms = 1000;
  config.slm.enabled = true;
  config.slm.edit_enabled = true;
  config.slm.endpoint = api.endpoint();
  config.slm.model = "fake-edit-model";
  config.slm.timeout_ms = 2000;

  CoreDispatcher dispatcher(config);
  UnixJsonServer server(config.server, dispatcher);
  std::exception_ptr server_error;
  std::thread thread([&] {
    try {
      server.run();
    } catch (...) {
      server_error = std::current_exception();
    }
  });
  for (int attempt = 0; attempt < 100; ++attempt) {
    if (std::filesystem::exists(config.server.socket_path)) {
      break;
    }
    std::this_thread::sleep_for(std::chrono::milliseconds(10));
  }
  require(std::filesystem::exists(config.server.socket_path),
          "voice edit test socket was not created");

  const Json capabilities = Json::parse(
      request_socket(config.server.socket_path, R"({"type":"capabilities"})"));
  require(capabilities["features"].value("voice_edit", false),
          "voice edit capability was not exposed");

  const auto edit_request = [](const std::string &type,
                               const std::filesystem::path &audio_path) {
    return Json{{"type", type},
                {"audio_path", audio_path.string()},
                {"context_id", "test-context"},
                {"replace_state", "supported"},
                {"supports_surrounding", true},
                {"snapshot",
                 {{"text", "这是原文"},
                  {"cursor_pos", 12},
                  {"anchor_pos", 12},
                  {"selected_text", ""}}}};
  };

  const Json sync = Json::parse(
      request_socket(config.server.socket_path,
                     edit_request("edit_audio", sync_audio).dump()));
  require(sync.value("success", false), "synchronous voice edit failed");
  require(sync.value("mode", "") == "replace",
          "synchronous edit mode was not forwarded");
  require(sync.value("new_text", "") == "修改后文本",
          "synchronous edit text was not forwarded");
  require(sync.value("expected_text", "") == "修改后文本",
          "synchronous expected text was not generated");
  require(sync.value("instruction", "") == "原生最终转写",
          "synchronous edit instruction was not forwarded");
  std::filesystem::remove(sync_audio);

  const Json started = Json::parse(
      request_socket(config.server.socket_path,
                     edit_request("edit_start", async_audio).dump()));
  require(started.value("success", false),
          "asynchronous voice edit did not start");
  const std::string task_id = started.value("task_id", "");
  Json poll;
  for (int attempt = 0; attempt < 100; ++attempt) {
    poll = Json::parse(request_socket(
        config.server.socket_path,
        Json{{"type", "edit_poll"}, {"task_id", task_id}}.dump()));
    if (poll.value("status", "") != "running") {
      break;
    }
    std::this_thread::sleep_for(std::chrono::milliseconds(10));
  }
  require(poll.value("status", "") == "final",
          "asynchronous voice edit did not finish");
  require(poll.value("phase", "") == "done",
          "asynchronous edit phase was not finalized");
  require(poll.value("instruction", "") == "原生最终转写",
          "asynchronous instruction was not retained");
  require(poll["result"].value("success", false),
          "asynchronous edit result was not successful");
  require(poll["result"].value("mode", "") == "replace",
          "asynchronous edit mode was not forwarded");
  require(poll["result"].value("new_text", "") == "修改后文本",
          "asynchronous edit text was not forwarded");
  for (int attempt = 0; attempt < 50 && std::filesystem::exists(async_audio);
       ++attempt) {
    std::this_thread::sleep_for(std::chrono::milliseconds(10));
  }
  require(!std::filesystem::exists(async_audio),
          "successful edit task did not remove its recording");
  require(api.request_count() == 2,
          "voice edit did not issue the expected API requests");

  server.stop();
  thread.join();
  if (server_error) {
    std::rethrow_exception(server_error);
  }
  std::filesystem::remove_all(root);
}

void test_streaming_asr(const std::filesystem::path &worker_path) {
  const std::filesystem::path model_dir =
      std::filesystem::temp_directory_path() /
      ("vocotype-fake-model-" + std::to_string(::getpid()));
  std::filesystem::create_directories(model_dir);

  AppConfig config = vocotype::core::parse_config(Json::object());
  config.streaming_asr.enabled = true;
  config.streaming_asr.worker_path = worker_path.string();
  config.streaming_asr.model_dir = model_dir.string();
  config.streaming_asr.startup_timeout_ms = 2000;
  config.streaming_asr.request_timeout_ms = 1000;

  {
    CoreDispatcher dispatcher(config);
    const Json capabilities = dispatcher.dispatch({{"type", "capabilities"}});
    require(capabilities["features"].value("streaming_asr", false),
            "streaming ASR capability was not exposed");

    const Json started = dispatcher.dispatch({{"type", "asr_preview_start"}});
    require(started.value("success", false),
            "fake streaming worker did not start");
    require(started.value("chunk_samples", 0) == 9600,
            "streaming worker chunk size was lost");
    const std::string session_id = started.value("session_id", "");
    require(!session_id.empty(), "streaming session ID is empty");

    const Json first = dispatcher.dispatch({{"type", "asr_preview_feed"},
                                            {"session_id", session_id},
                                            {"pcm16", "AAAAAA=="},
                                            {"is_final", false}});
    require(first.value("success", false), "first preview feed failed");
    require(first.value("text", "") == "预览",
            "first preview text was not forwarded");

    const Json second = dispatcher.dispatch({{"type", "asr_preview_feed"},
                                             {"session_id", session_id},
                                             {"pcm16", "AAAAAA=="},
                                             {"is_final", true}});
    require(second.value("text", "") == "预览预览",
            "streaming session state was not preserved");

    const Json closed = dispatcher.dispatch({{"type", "asr_preview_close"},
                                             {"session_id", session_id},
                                             {"flush", false}});
    require(closed.value("success", false), "preview close failed");
    require(closed.value("final", false), "preview close was not final");
  }

  std::filesystem::remove_all(model_dir);
}

void test_socket_streaming(const std::filesystem::path &worker_path) {
  const std::filesystem::path model_dir =
      std::filesystem::temp_directory_path() /
      ("vocotype-fake-socket-model-" + std::to_string(::getpid()));
  std::filesystem::create_directories(model_dir);

  AppConfig config = vocotype::core::parse_config(Json::object());
  config.server.socket_path =
      "/tmp/vocotype-core-stream-test-" + std::to_string(::getpid()) + ".sock";
  config.streaming_asr.enabled = true;
  config.streaming_asr.worker_path = worker_path.string();
  config.streaming_asr.model_dir = model_dir.string();
  config.streaming_asr.startup_timeout_ms = 2000;
  config.streaming_asr.request_timeout_ms = 1000;

  CoreDispatcher dispatcher(config);
  UnixJsonServer server(config.server, dispatcher);
  std::exception_ptr server_error;
  std::thread thread([&] {
    try {
      server.run();
    } catch (...) {
      server_error = std::current_exception();
    }
  });
  for (int attempt = 0; attempt < 100; ++attempt) {
    if (std::filesystem::exists(config.server.socket_path)) {
      break;
    }
    std::this_thread::sleep_for(std::chrono::milliseconds(10));
  }
  require(std::filesystem::exists(config.server.socket_path),
          "streaming test socket was not created");

  const Json started = Json::parse(request_socket(
      config.server.socket_path, R"({"type":"asr_preview_start"})"));
  require(started.value("success", false),
          "socket preview start request failed");
  const std::string session_id = started.value("session_id", "");
  const Json fed = Json::parse(request_socket(
      config.server.socket_path, Json({{"type", "asr_preview_feed"},
                                       {"session_id", session_id},
                                       {"pcm16", "AAAAAA=="},
                                       {"is_final", false}})
                                     .dump()));
  require(fed.value("text", "") == "预览",
          "socket preview feed response was not forwarded");
  const Json closed = Json::parse(request_socket(
      config.server.socket_path, Json({{"type", "asr_preview_close"},
                                       {"session_id", session_id},
                                       {"flush", false}})
                                     .dump()));
  require(closed.value("success", false),
          "socket preview close request failed");

  server.stop();
  thread.join();
  if (server_error) {
    std::rethrow_exception(server_error);
  }
  std::filesystem::remove_all(model_dir);
}

void test_socket_server() {
  AppConfig config = vocotype::core::parse_config(Json::object());
  config.server.socket_path =
      "/tmp/vocotype-core-test-" + std::to_string(::getpid()) + ".sock";
  CoreDispatcher dispatcher(config);
  UnixJsonServer server(config.server, dispatcher);
  std::exception_ptr server_error;
  std::thread thread([&] {
    try {
      server.run();
    } catch (...) {
      server_error = std::current_exception();
    }
  });

  for (int attempt = 0; attempt < 100; ++attempt) {
    if (std::filesystem::exists(config.server.socket_path)) {
      break;
    }
    std::this_thread::sleep_for(std::chrono::milliseconds(10));
  }
  require(std::filesystem::exists(config.server.socket_path),
          "server socket was not created");

  const Json response = Json::parse(
      request_socket(config.server.socket_path, R"({"type":"ping"})"));
  require(response.value("pong", false), "socket ping failed");

  server.stop();
  thread.join();
  if (server_error) {
    std::rethrow_exception(server_error);
  }
  require(!std::filesystem::exists(config.server.socket_path),
          "server socket was not removed");
}

} // namespace

int main(int argc, char **argv) {
  try {
    if (argc != 3) {
      throw std::runtime_error(
          "fake streaming and offline worker paths are required");
    }
    test_config_merge();
    test_voice_edit_plan_validation();
    test_dispatcher();
    test_offline_asr(argv[2]);
    test_socket_offline(argv[2]);
    test_voice_edit_disabled(argv[2]);
    test_socket_voice_edit(argv[2]);
    test_streaming_asr(argv[1]);
    test_socket_streaming(argv[1]);
    test_socket_server();
    std::cout << "vocotype-core tests passed\n";
    return 0;
  } catch (const std::exception &error) {
    std::cerr << "vocotype-core test failure: " << error.what() << '\n';
    return 1;
  }
}
