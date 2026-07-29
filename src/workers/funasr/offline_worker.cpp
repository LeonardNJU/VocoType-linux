/**
 * VoCoType native final ASR worker.
 *
 * This process owns the official FunASR offline runtime, including contextual
 * Paraformer hotword embeddings, optional VAD, and punctuation. The core
 * daemon communicates with it over JSON-lines and can reclaim all model memory
 * by allowing the worker to exit after an idle timeout.
 */

#include <poll.h>
#include <unistd.h>

#include <algorithm>
#include <cerrno>
#include <chrono>
#include <cstdlib>
#include <exception>
#include <filesystem>
#include <iostream>
#include <map>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

#include <glog/logging.h>
#include <nlohmann/json.hpp>

#include "com-define.h"
#include "funasrruntime.h"

namespace {

using Json = nlohmann::json;
using Clock = std::chrono::steady_clock;

struct Options {
  std::string asr_model_dir;
  std::string vad_model_dir;
  std::string punc_model_dir;
  int threads = 2;
  int idle_timeout_ms = 60000;
};

std::string next_arg(int &index, int argc, char **argv, const char *flag) {
  if (index + 1 >= argc) {
    throw std::runtime_error(std::string("missing value for ") + flag);
  }
  return argv[++index];
}

Options parse_options(int argc, char **argv) {
  Options options;
  for (int index = 1; index < argc; ++index) {
    const std::string arg = argv[index];
    if (arg == "--asr-model-dir") {
      options.asr_model_dir = next_arg(index, argc, argv, "--asr-model-dir");
    } else if (arg == "--vad-model-dir") {
      options.vad_model_dir = next_arg(index, argc, argv, "--vad-model-dir");
    } else if (arg == "--punc-model-dir") {
      options.punc_model_dir = next_arg(index, argc, argv, "--punc-model-dir");
    } else if (arg == "--threads") {
      options.threads = std::stoi(next_arg(index, argc, argv, "--threads"));
    } else if (arg == "--idle-timeout-ms") {
      options.idle_timeout_ms =
          std::stoi(next_arg(index, argc, argv, "--idle-timeout-ms"));
    } else if (arg == "--help") {
      std::cout << "Usage: vocotype-offline-worker --asr-model-dir DIR "
                   "[--vad-model-dir DIR] [--punc-model-dir DIR] "
                   "[--threads 2] [--idle-timeout-ms 60000]\n";
      std::exit(0);
    } else {
      throw std::runtime_error("unknown argument: " + arg);
    }
  }
  if (options.asr_model_dir.empty()) {
    throw std::runtime_error("--asr-model-dir is required");
  }
  if (options.threads < 1 || options.threads > 8) {
    throw std::runtime_error("--threads must be between 1 and 8");
  }
  options.idle_timeout_ms = std::max(1000, options.idle_timeout_ms);
  return options;
}

void require_directory(const std::string &path, const char *name) {
  if (!path.empty() && !std::filesystem::is_directory(path)) {
    throw std::runtime_error(std::string(name) +
                             " is not a directory: " + path);
  }
}

void emit(const Json &response) {
  std::cout << response.dump() << '\n' << std::flush;
}

class Worker {
public:
  explicit Worker(Options options) : options_(std::move(options)) {
    require_directory(options_.asr_model_dir, "ASR model directory");
    require_directory(options_.vad_model_dir, "VAD model directory");
    require_directory(options_.punc_model_dir, "punctuation model directory");

    std::map<std::string, std::string> model_path{
        {MODEL_DIR, options_.asr_model_dir},
        {QUANTIZE, "true"},
    };
    if (!options_.vad_model_dir.empty()) {
      model_path[VAD_DIR] = options_.vad_model_dir;
      model_path[VAD_QUANT] = "true";
    }
    if (!options_.punc_model_dir.empty()) {
      model_path[PUNC_DIR] = options_.punc_model_dir;
      model_path[PUNC_QUANT] = "true";
    }
    handle_ = FunOfflineInit(model_path, options_.threads, false, 1);
    if (!handle_) {
      throw std::runtime_error("FunOfflineInit returned null");
    }
    compile_hotwords("");
    last_activity_ = Clock::now();
  }

  Worker(const Worker &) = delete;
  Worker &operator=(const Worker &) = delete;

  ~Worker() {
    if (handle_) {
      FunOfflineUninit(handle_);
    }
  }

  int run() {
    emit({{"type", "ready"},
          {"success", true},
          {"sample_rate", 16000},
          {"contextual_hotword", true},
          {"vad", !options_.vad_model_dir.empty()},
          {"punctuation", !options_.punc_model_dir.empty()}});

    while (true) {
      const int remaining = remaining_idle_ms();
      if (remaining <= 0) {
        return 0;
      }
      pollfd descriptor{STDIN_FILENO, POLLIN | POLLHUP, 0};
      const int poll_result = ::poll(&descriptor, 1, remaining);
      if (poll_result < 0) {
        if (errno == EINTR) {
          continue;
        }
        throw std::runtime_error("poll failed");
      }
      if (poll_result == 0) {
        return 0;
      }
      if (descriptor.revents & (POLLERR | POLLNVAL)) {
        return 1;
      }

      std::string line;
      if (!std::getline(std::cin, line)) {
        return 0;
      }
      if (line.empty()) {
        continue;
      }
      last_activity_ = Clock::now();
      if (!handle_line(line)) {
        return 0;
      }
    }
  }

private:
  int remaining_idle_ms() const {
    const auto elapsed = std::chrono::duration_cast<std::chrono::milliseconds>(
        Clock::now() - last_activity_);
    return options_.idle_timeout_ms - static_cast<int>(elapsed.count());
  }

  void compile_hotwords(const std::string &hotwords) {
    if (hotwords_initialized_ && hotwords == active_hotwords_) {
      return;
    }
    std::string mutable_hotwords = hotwords;
    hotword_embedding_ =
        CompileHotwordEmbedding(handle_, mutable_hotwords, ASR_OFFLINE);
    if (hotword_embedding_.empty()) {
      throw std::runtime_error("CompileHotwordEmbedding returned no tensors");
    }
    active_hotwords_ = hotwords;
    hotwords_initialized_ = true;
  }

  bool handle_line(const std::string &line) {
    try {
      const Json request = Json::parse(line);
      const std::string type = request.value("type", "");
      if (type == "transcribe") {
        transcribe(request);
      } else if (type == "prepare") {
        const std::string hotwords = request.value("hotwords", "");
        compile_hotwords(hotwords);
        emit({{"success", true}, {"prepared", true}, {"hotwords", hotwords}});
      } else if (type == "ping") {
        emit({{"success", true}});
      } else if (type == "stop") {
        emit({{"success", true}});
        return false;
      } else {
        emit({{"success", false}, {"error", "unknown_request"}});
      }
    } catch (const std::exception &error) {
      emit({{"success", false}, {"error", error.what()}});
    }
    return true;
  }

  void transcribe(const Json &request) {
    const std::string audio_path = request.value("audio_path", "");
    if (audio_path.empty()) {
      throw std::runtime_error("audio_path is required");
    }
    if (!std::filesystem::is_regular_file(audio_path)) {
      throw std::runtime_error("audio file not found: " + audio_path);
    }
    const std::string hotwords = request.value("hotwords", "");
    compile_hotwords(hotwords);
    const int sampling_rate =
        std::clamp(request.value("sampling_rate", 16000), 8000, 192000);
    const bool itn = request.value("itn", true);

    const auto started = Clock::now();
    FUNASR_RESULT result =
        FunOfflineInfer(handle_, audio_path.c_str(), RASR_NONE, nullptr,
                        hotword_embedding_, sampling_rate, itn, nullptr);
    if (!result) {
      throw std::runtime_error("FunOfflineInfer returned null");
    }
    const char *raw_text = FunASRGetResult(result, 0);
    const std::string text = raw_text ? raw_text : "";
    const float snippet_time = FunASRGetRetSnippetTime(result);
    const int result_count = FunASRGetRetNumber(result);
    FunASRFreeResult(result);

    const double latency_ms =
        std::chrono::duration<double, std::milli>(Clock::now() - started)
            .count();
    emit({{"success", true},
          {"text", text},
          {"raw_text", text},
          {"latency_ms", latency_ms},
          {"snippet_time", snippet_time},
          {"result_count", result_count},
          {"hotwords", hotwords}});
  }

  Options options_;
  FUNASR_HANDLE handle_ = nullptr;
  std::string active_hotwords_;
  bool hotwords_initialized_ = false;
  std::vector<std::vector<float>> hotword_embedding_;
  Clock::time_point last_activity_{};
};

} // namespace

int main(int argc, char **argv) {
  google::InitGoogleLogging(argv[0]);
  FLAGS_logtostderr = true;
  try {
    Worker worker(parse_options(argc, argv));
    return worker.run();
  } catch (const std::exception &error) {
    emit({{"type", "ready"}, {"success", false}, {"error", error.what()}});
    return 1;
  }
}
