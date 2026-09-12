#include "vocotype/desktop/audio.hpp"
#include "vocotype/desktop/config.hpp"
#include "vocotype/desktop/ipc.hpp"
#include "vocotype/desktop/streaming_preview.hpp"
#include "vocotype/desktop/wav.hpp"
#include <algorithm>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <csignal>
#include <cstdint>
#include <cstdlib>
#include <deque>
#include <filesystem>
#include <iostream>
#include <mutex>
#include <optional>
#include <poll.h>
#include <string>
#include <thread>
#include <unistd.h>
using namespace vocotype::desktop;
namespace {
std::atomic_bool *global_stop = nullptr;
void signal_handler(int) {
  if (global_stop)
    global_stop->store(true);
}
std::mutex output_mutex;
void emit(const Json &value) {
  std::lock_guard lock(output_mutex);
  std::cout << value.dump() << '\n' << std::flush;
}
struct Options {
  bool list_devices = false;
  bool probe = false;
  bool emit_levels = false;
  bool preview = true;
  int duration_ms = 0;
  std::filesystem::path config;
  std::string socket = backend_socket_path();
};
Options parse(int argc, char **argv) {
  Options options;
  for (int i = 1; i < argc; ++i) {
    const std::string arg = argv[i];
    if (arg == "--list-devices")
      options.list_devices = true;
    else if (arg == "--probe")
      options.probe = true;
    else if (arg == "--emit-levels")
      options.emit_levels = true;
    else if (arg == "--no-preview")
      options.preview = false;
    else if (arg == "--duration-ms" && i + 1 < argc)
      options.duration_ms = std::stoi(argv[++i]);
    else if (arg == "--config" && i + 1 < argc)
      options.config = argv[++i];
    else if (arg == "--socket" && i + 1 < argc)
      options.socket = argv[++i];
    else if (arg == "--help") {
      std::cout << "Usage: vocotype-audio-recorder [--list-devices|--probe] "
                   "[--emit-levels] [--no-preview] [--duration-ms N] "
                   "[--config PATH] [--socket PATH]\n";
      std::exit(0);
    } else
      throw std::runtime_error("unknown argument: " + arg);
  }
  return options;
}
} // namespace
int main(int argc, char **argv) {
  try {
    const Options options = parse(argc, argv);
    if (options.list_devices || options.probe) {
      Json devices = Json::array();
      for (const auto &device : list_input_devices()) {
        devices.push_back({{"id", device.id},
                           {"name", device.name},
                           {"channels", device.max_input_channels},
                           {"sample_rate", device.default_sample_rate},
                           {"default", device.is_default}});
      }
      emit({{"type", options.probe ? "probe" : "devices"},
            {"success", true},
            {"devices", devices}});
      return 0;
    }
    AudioConfig config = load_audio_config(options.config);
    const AudioInputSelection input = resolve_input_capture(config);
    const AudioDevice device = input.device;
    const int sample_rate = input.sample_rate;
    std::atomic_bool stop{false};
    global_stop = &stop;
    std::signal(SIGINT, signal_handler);
    std::signal(SIGTERM, signal_handler);

    std::mutex samples_mutex;
    std::vector<std::int16_t> samples;
    std::mutex queue_mutex;
    std::condition_variable queue_cv;
    std::deque<std::vector<std::int16_t>> preview_queue;
    bool preview_done = false;
    std::string preview_session;
    std::atomic_bool preview_accepting{true};
    int preview_chunk_samples = 9600;

    std::thread preview_thread;
    if (options.preview) {
      preview_thread = std::thread([&] {
      StreamingPreviewTranscript transcript;
      std::deque<std::vector<std::int16_t>> recent_chunks;
      constexpr std::size_t kRecentChunkLimit = 3;
      std::string last_preview_error;

      auto emit_preview_state = [&](const char *type,
                                    const std::string &message) {
        emit({{"type", type}, {"message", message}});
      };

      auto start_preview = [&](int attempts, int timeout_ms) -> bool {
        preview_session.clear();
        for (int attempt = 0; attempt < attempts; ++attempt) {
          {
            std::lock_guard lock(queue_mutex);
            if (preview_done)
              return false;
          }
          try {
            const Json started = unix_json_request(
                options.socket, {{"type", "asr_preview_start"}}, timeout_ms);
            if (started.value("success", false)) {
              preview_session = started.value("session_id", "");
              preview_chunk_samples =
                  std::max(1600, started.value("chunk_samples", 9600));
              if (!preview_session.empty())
                return true;
            }
            last_preview_error = started.value("error", "preview_start_failed");
          } catch (const std::exception &error) {
            last_preview_error = error.what();
          }
          std::this_thread::sleep_for(std::chrono::milliseconds(150));
        }
        return false;
      };

      auto request_feed = [&](const std::vector<std::int16_t> &chunk,
                              bool is_final) -> std::optional<Json> {
        if (preview_session.empty())
          return std::nullopt;
        try {
          const auto bytes =
              reinterpret_cast<const unsigned char *>(chunk.data());
          const Json response = unix_json_request(
              options.socket,
              {{"type", "asr_preview_feed"},
               {"session_id", preview_session},
               {"pcm16",
                base64_encode(bytes, chunk.size() * sizeof(std::int16_t))},
               {"is_final", is_final}},
              10000);
          if (!response.value("success", false)) {
            last_preview_error = response.value("error", "preview_feed_failed");
            return std::nullopt;
          }
          return std::optional<Json>{std::in_place, response};
        } catch (const std::exception &error) {
          last_preview_error = error.what();
          return std::nullopt;
        }
      };

      auto accept_response = [&](const Json &response)
          -> std::optional<std::string> {
        return transcript.update_session_text(response.value("text", ""));
      };

      auto recover_and_replay = [&]() -> bool {
        transcript.begin_recovery();
        emit_preview_state("preview_recovering", last_preview_error);
        for (int recovery = 0; recovery < 2; ++recovery) {
          if (!start_preview(2, 30000))
            continue;
          bool replay_ok = true;
          for (const auto &context : recent_chunks) {
            const auto response = request_feed(context, false);
            if (!response) {
              replay_ok = false;
              preview_session.clear();
              break;
            }
            (void)accept_response(*response);
          }
          if (replay_ok) {
            emit_preview_state("preview_recovered", transcript.display_text());
            if (transcript.has_text())
              emit({{"type", "partial"}, {"text", transcript.display_text()}});
            return true;
          }
        }
        emit_preview_state("preview_unavailable", last_preview_error);
        return false;
      };

      if (!start_preview(20, 5000)) {
        preview_accepting.store(false);
        emit_preview_state("preview_unavailable", last_preview_error);
        std::lock_guard lock(queue_mutex);
        preview_queue.clear();
        return;
      }

      auto feed_preview = [&](const std::vector<std::int16_t> &chunk,
                              bool is_final) -> bool {
        if (!chunk.empty()) {
          recent_chunks.push_back(chunk);
          while (recent_chunks.size() > kRecentChunkLimit)
            recent_chunks.pop_front();
        }
        const auto response = request_feed(chunk, is_final);
        if (!response) {
          preview_session.clear();
          return recover_and_replay();
        }
        const auto updated = accept_response(*response);
        if (updated)
          emit({{"type", "partial"}, {"text", *updated}});
        return true;
      };

      std::vector<std::int16_t> pending;
      while (true) {
        std::vector<std::int16_t> block;
        {
          std::unique_lock lock(queue_mutex);
          queue_cv.wait(lock,
                        [&] { return preview_done || !preview_queue.empty(); });
          if (!preview_queue.empty()) {
            block = std::move(preview_queue.front());
            preview_queue.pop_front();
          } else if (preview_done) {
            break;
          }
        }
        pending.insert(pending.end(), block.begin(), block.end());
        while (static_cast<int>(pending.size()) >= preview_chunk_samples) {
          std::vector<std::int16_t> chunk(
              pending.begin(), pending.begin() + preview_chunk_samples);
          pending.erase(pending.begin(),
                        pending.begin() + preview_chunk_samples);
          if (!feed_preview(chunk, false)) {
            preview_accepting.store(false);
            std::lock_guard lock(queue_mutex);
            preview_queue.clear();
            pending.clear();
            break;
          }
        }
        if (!preview_accepting.load())
          break;
      }

      if (preview_accepting.load() && !pending.empty())
        (void)feed_preview(pending, true);
      if (!preview_session.empty()) {
        try {
          (void)unix_json_request(options.socket,
                                  {{"type", "asr_preview_close"},
                                   {"session_id", preview_session},
                                   {"flush", false}},
                                  5000);
        } catch (const std::exception &) {
        }
      }
      preview_accepting.store(false);
      });
    } else {
      preview_accepting.store(false);
    }

    AudioCapture capture(device, sample_rate, config.block_ms,
                         input.native_capture_name);
    std::string capture_error;
    std::atomic_bool first_audio_block{false};
    std::atomic_int64_t first_audio_started_ns{0};
    std::atomic_bool capture_finished{false};
#ifdef __APPLE__
    // CoreAudio can occasionally wedge inside AudioDeviceStart without
    // returning an error. Do not leave the input method stuck in a permanent
    // "recording" state when no PCM callback ever arrives. The stop flag is
    // not sufficient here: a timed recording may request stop while the
    // capture thread is still blocked inside AudioDeviceStart.
    std::thread([&first_audio_block, &capture_finished] {
      // A built-in microphone that has been idle for a long time can take just
      // over five seconds to leave its deep CoreAudio power state on macOS 26.
      // Keep enough margin to avoid killing the recorder exactly as the HAL
      // reports the device Running, while still bounding genuine startup hangs.
      constexpr auto kMicrophoneStartupTimeout = std::chrono::seconds(8);
      std::this_thread::sleep_for(kMicrophoneStartupTimeout);
      if (!first_audio_block.load(std::memory_order_acquire) &&
          !capture_finished.load(std::memory_order_acquire)) {
        emit({{"type", "error"},
              {"error", "麦克风启动超过 8 秒（CoreAudio 未返回音频）；请重试，必要时重启系统音频服务"}});
        std::_Exit(2);
      }
    }).detach();
#endif
    std::thread capture_thread([&] {
      try {
        capture.run(stop, [&](const std::vector<std::int16_t> &block) {
          if (!first_audio_block.exchange(true)) {
            first_audio_started_ns.store(
                std::chrono::duration_cast<std::chrono::nanoseconds>(
                    std::chrono::steady_clock::now().time_since_epoch())
                    .count(),
                std::memory_order_release);
            emit({{"type", "recording"},
                  {"device_id", device.id},
                  {"device_name", device.name},
                  {"sample_rate", sample_rate}});
          }
          {
            std::lock_guard lock(samples_mutex);
            samples.insert(samples.end(), block.begin(), block.end());
          }
          if (options.emit_levels && !block.empty()) {
            const auto [minimum, maximum] =
                std::minmax_element(block.begin(), block.end());
            emit({{"type", "level"},
                  {"minimum", static_cast<double>(*minimum) / 32768.0},
                  {"maximum", static_cast<double>(*maximum) / 32768.0}});
          }
          if (preview_accepting.load(std::memory_order_relaxed)) {
            auto converted = resample_linear(block, sample_rate, 16000);
            std::lock_guard lock(queue_mutex);
            if (preview_queue.size() < 1024)
              preview_queue.push_back(std::move(converted));
            queue_cv.notify_one();
          }
        });
      } catch (const std::exception &error) {
        capture_error = error.what();
        stop.store(true);
      }
      capture_finished.store(true, std::memory_order_release);
    });

    while (!stop.load()) {
      if (options.duration_ms > 0) {
        const std::int64_t audio_started_ns =
            first_audio_started_ns.load(std::memory_order_acquire);
        if (audio_started_ns > 0) {
          const std::int64_t now_ns =
              std::chrono::duration_cast<std::chrono::nanoseconds>(
                  std::chrono::steady_clock::now().time_since_epoch())
                  .count();
          if (now_ns - audio_started_ns >=
              static_cast<std::int64_t>(options.duration_ms) * 1000000)
            break;
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(20));
        continue;
      }
      pollfd descriptor{STDIN_FILENO,
                        static_cast<short>(POLLIN | POLLHUP | POLLERR), 0};
      const int result = poll(&descriptor, 1, 50);
      if (result > 0 && (descriptor.revents & (POLLHUP | POLLERR)))
        break;
      if (result > 0 && (descriptor.revents & POLLIN)) {
        char discard[64];
        const ssize_t count = read(STDIN_FILENO, discard, sizeof(discard));
        if (count == 0)
          break;
      }
    }
    stop.store(true);
    capture_thread.join();
    if (!capture_error.empty()) {
      preview_accepting.store(false);
      {
        std::lock_guard lock(queue_mutex);
        preview_queue.clear();
        preview_done = true;
      }
      queue_cv.notify_all();
      if (preview_thread.joinable())
        preview_thread.join();
      throw std::runtime_error(capture_error);
    }

    std::vector<std::int16_t> finished;
    {
      std::lock_guard lock(samples_mutex);
      finished = std::move(samples);
    }
    if (finished.empty()) {
      preview_accepting.store(false);
      {
        std::lock_guard lock(queue_mutex);
        preview_queue.clear();
        preview_done = true;
      }
      queue_cv.notify_all();
      if (preview_thread.joinable())
        preview_thread.join();
      throw std::runtime_error("recording produced no audio samples");
    }

    const auto path = create_secure_wav_path();
    write_pcm16_wav(path, finished, sample_rate);
    emit({{"type", "audio"},
          {"path", path.string()},
          {"sample_rate", sample_rate},
          {"frames", finished.size()},
          {"device_id", device.id},
          {"device_name", device.name}});

    preview_accepting.store(false);
    {
      std::lock_guard lock(queue_mutex);
      preview_queue.clear();
      preview_done = true;
    }
    queue_cv.notify_all();
    if (preview_thread.joinable())
      preview_thread.join();
    return 0;
  } catch (const std::exception &error) {
    emit({{"type", "error"}, {"error", error.what()}});
    return 1;
  }
}
