#include "vocotype/desktop/recorder_process.hpp"

#include "vocotype/common/posix.hpp"

#include <cerrno>
#include <condition_variable>
#include <cstdio>
#include <cstdlib>
#include <memory>
#include <mutex>
#include <nlohmann/json.hpp>
#include <signal.h>
#include <stdexcept>
#include <sys/wait.h>
#include <thread>
#include <unistd.h>

namespace vocotype::desktop {

struct RecorderProcess::State {
  mutable std::mutex mutex;
  std::condition_variable changed;
  pid_t pid = -1;
  int stdin_fd = -1;
  int stdout_fd = -1;
  std::string audio_path;
  bool stop_requested = false;
  bool cancel_requested = false;
  bool finished = false;
};

RecorderProcess::~RecorderProcess() {
  const auto state = state_;
  if (!state)
    return;
  bool graceful = false;
  {
    std::lock_guard lock(state->mutex);
    graceful = state->stop_requested;
  }
  if (!graceful)
    cancel();
}

bool RecorderProcess::running() const {
  const auto state = state_;
  if (!state)
    return false;
  std::lock_guard lock(state->mutex);
  return state->pid > 0 && !state->finished;
}

void RecorderProcess::start(const std::string &executable,
                            EventCallback callback) {
  if (running())
    throw std::runtime_error("recorder is already running");

  int input_pipe[2]{};
  int output_pipe[2]{};
  if (vocotype::common::create_pipe_close_on_exec(input_pipe) != 0)
    throw std::runtime_error("cannot create recorder input pipe");
  if (vocotype::common::create_pipe_close_on_exec(output_pipe) != 0) {
    close(input_pipe[0]);
    close(input_pipe[1]);
    throw std::runtime_error("cannot create recorder output pipe");
  }

  const pid_t child = fork();
  if (child < 0) {
    close(input_pipe[0]);
    close(input_pipe[1]);
    close(output_pipe[0]);
    close(output_pipe[1]);
    throw std::runtime_error("cannot fork recorder");
  }
  if (child == 0) {
    dup2(input_pipe[0], STDIN_FILENO);
    dup2(output_pipe[1], STDOUT_FILENO);
    close(input_pipe[0]);
    close(input_pipe[1]);
    close(output_pipe[0]);
    close(output_pipe[1]);
    execl(executable.c_str(), executable.c_str(), static_cast<char *>(nullptr));
    _exit(127);
  }

  close(input_pipe[0]);
  close(output_pipe[1]);
  auto state = std::make_shared<State>();
  state->pid = child;
  state->stdin_fd = input_pipe[1];
  state->stdout_fd = output_pipe[0];
  state_ = state;

  std::thread([state, callback = std::move(callback)] {
    int output_fd = -1;
    pid_t child_pid = -1;
    {
      std::lock_guard lock(state->mutex);
      output_fd = state->stdout_fd;
      child_pid = state->pid;
    }

    FILE *file = fdopen(output_fd, "r");
    if (file) {
      char *line = nullptr;
      std::size_t capacity = 0;
      while (getline(&line, &capacity, file) >= 0) {
        try {
          const auto value = nlohmann::json::parse(line);
          const std::string type = value.value("type", "");
          const std::string payload =
              type == "audio"       ? value.value("path", "")
              : type == "partial"   ? value.value("text", "")
              : type == "recording" ? value.value("device_name", "")
              : type.starts_with("preview_")
                  ? value.value("message", "")
                  : value.value("error", "");
          if (type == "audio") {
            bool discard = false;
            {
              std::lock_guard lock(state->mutex);
              discard = state->cancel_requested;
              if (!discard)
                state->audio_path = payload;
              state->changed.notify_all();
            }
            if (discard && !payload.empty())
              std::remove(payload.c_str());
          }
          bool deliver = true;
          {
            std::lock_guard lock(state->mutex);
            deliver = !state->cancel_requested &&
                      (!state->stop_requested || type == "audio" ||
                       type == "error");
          }
          if (deliver && callback)
            callback(type, payload);
        } catch (const std::exception &) {
        }
      }
      free(line);
      fclose(file);
    } else {
      close(output_fd);
    }

    if (child_pid > 0) {
      while (waitpid(child_pid, nullptr, 0) < 0 && errno == EINTR) {
      }
    }
    {
      std::lock_guard lock(state->mutex);
      state->stdout_fd = -1;
      state->pid = -1;
      state->finished = true;
    }
    state->changed.notify_all();
  }).detach();
}

std::string RecorderProcess::stop() {
  const auto state = state_;
  if (!state)
    return {};

  std::unique_lock lock(state->mutex);
  state->stop_requested = true;
  if (state->stdin_fd >= 0) {
    close(state->stdin_fd);
    state->stdin_fd = -1;
  }
  state->changed.wait(lock, [&] {
    return !state->audio_path.empty() || state->finished;
  });
  return state->audio_path;
}

void RecorderProcess::cancel_async() {
  const auto state = state_;
  if (!state)
    return;

  std::string audio_path;
  pid_t child_pid = -1;
  {
    std::lock_guard lock(state->mutex);
    state->stop_requested = true;
    state->cancel_requested = true;
    audio_path = std::move(state->audio_path);
    if (state->stdin_fd >= 0) {
      close(state->stdin_fd);
      state->stdin_fd = -1;
    }
    child_pid = state->pid;
    if (child_pid > 0)
      kill(child_pid, SIGTERM);
  }
  if (!audio_path.empty())
    std::remove(audio_path.c_str());
  if (child_pid > 0) {
    std::thread([state, child_pid] {
      std::this_thread::sleep_for(std::chrono::milliseconds(250));
      std::lock_guard lock(state->mutex);
      if (!state->finished && state->pid == child_pid)
        kill(child_pid, SIGKILL);
    }).detach();
  }
}

void RecorderProcess::cancel() {
  const auto state = state_;
  if (!state)
    return;

  cancel_async();
  std::unique_lock lock(state->mutex);
  state->changed.wait(lock, [&] { return state->finished; });
  const std::string audio_path = std::move(state->audio_path);
  lock.unlock();
  if (!audio_path.empty())
    std::remove(audio_path.c_str());
}

} // namespace vocotype::desktop
