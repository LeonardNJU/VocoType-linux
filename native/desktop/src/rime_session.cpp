#include "vocotype/desktop/rime_session.hpp"
#include "vocotype/desktop/config.hpp"

#include <algorithm>
#include <cstring>
#include <fstream>
#include <mutex>
#include <regex>
#include <stdexcept>
#include <utility>

#include <rime_api.h>

namespace vocotype::desktop {
namespace {

std::filesystem::path shared_data_dir() {
  for (const auto &path :
       {std::filesystem::path("/usr/share/rime-data"),
        std::filesystem::path("/usr/local/share/rime-data")}) {
    if (std::filesystem::is_regular_file(path / "default.yaml"))
      return path;
  }
  return {};
}

std::filesystem::path deployed_user_dir() {
  for (const auto &path :
       {config_dir() / "rime", home_path() / ".config/ibus/rime"}) {
    if (std::filesystem::is_regular_file(path / "build/default.yaml"))
      return path;
  }
  return {};
}

std::string preferred_schema(const std::filesystem::path &user_dir) {
  for (const auto &path :
       {config_dir() / "rime/user.yaml", user_dir / "user.yaml"}) {
    std::ifstream input(path);
    if (!input)
      continue;
    std::string line;
    const std::regex pattern(
        R"(^\s*previously_selected_schema\s*:\s*['\"]?([^'\"\s#]+))");
    while (std::getline(input, line)) {
      std::smatch match;
      if (std::regex_search(line, match, pattern))
        return match[1].str();
    }
  }
  return "luna_pinyin";
}

struct Runtime {
  std::mutex mutex;
  bool initialized = false;
  bool usable = false;
  RimeApi *api = nullptr;
  std::filesystem::path shared;
  std::filesystem::path user;
  std::filesystem::path logs;
  std::string shared_string;
  std::string user_string;
  std::string logs_string;
  std::string prebuilt_string;
  std::string staging_string;
  const char *modules[2]{"default", nullptr};

  void initialize() {
    std::lock_guard lock(mutex);
    if (initialized)
      return;
    initialized = true;
    shared = shared_data_dir();
    user = deployed_user_dir();
    logs = home_path() / ".local/share/vocotype/rime";
    if (shared.empty() || user.empty())
      return;
    std::filesystem::create_directories(logs);
    shared_string = shared.string();
    user_string = user.string();
    logs_string = logs.string();
    prebuilt_string = (shared / "build").string();
    staging_string = (user / "build").string();
    RIME_STRUCT(RimeTraits, traits);
    traits.shared_data_dir = shared_string.c_str();
    traits.user_data_dir = user_string.c_str();
    traits.distribution_name = "VoCoType";
    traits.distribution_code_name = "vocotype";
    traits.distribution_version = "3";
    traits.app_name = "rime.vocotype";
    traits.modules = modules;
    traits.min_log_level = 2;
    traits.log_dir = logs_string.c_str();
    traits.prebuilt_data_dir = prebuilt_string.c_str();
    traits.staging_dir = staging_string.c_str();
    api = rime_get_api();
    if (!api || !RIME_API_AVAILABLE(api, setup) ||
        !RIME_API_AVAILABLE(api, initialize) ||
        !RIME_API_AVAILABLE(api, finalize) ||
        !RIME_API_AVAILABLE(api, create_session) ||
        !RIME_API_AVAILABLE(api, destroy_session) ||
        !RIME_API_AVAILABLE(api, process_key) ||
        !RIME_API_AVAILABLE(api, get_commit) ||
        !RIME_API_AVAILABLE(api, free_commit) ||
        !RIME_API_AVAILABLE(api, get_context) ||
        !RIME_API_AVAILABLE(api, free_context) ||
        !RIME_API_AVAILABLE(api, select_schema) ||
        !RIME_API_AVAILABLE(api, get_current_schema) ||
        !RIME_API_AVAILABLE(api, clear_composition))
      return;
    api->setup(&traits);
    api->initialize(&traits);
    usable = true;
  }

  ~Runtime() {
    if (usable && api)
      api->finalize();
  }
};

Runtime &runtime() {
  static Runtime value;
  value.initialize();
  return value;
}

} // namespace

struct RimeSession::Impl {
  RimeSessionId id = 0;
  Runtime *runtime = nullptr;
};

RimeSession::RimeSession() : impl_(std::make_unique<Impl>()) {
  auto &active = runtime();
  impl_->runtime = &active;
  std::lock_guard lock(active.mutex);
  if (!active.usable)
    return;
  impl_->id = active.api->create_session();
  if (!impl_->id)
    return;
  const std::string requested = preferred_schema(active.user);
  if (!active.api->select_schema(impl_->id, requested.c_str()) &&
      requested != "luna_pinyin")
    (void)active.api->select_schema(impl_->id, "luna_pinyin");
}

RimeSession::~RimeSession() {
  if (!impl_ || !impl_->runtime || !impl_->id)
    return;
  std::lock_guard lock(impl_->runtime->mutex);
  (void)impl_->runtime->api->destroy_session(impl_->id);
}

bool RimeSession::available() const {
  return impl_ && impl_->runtime && impl_->runtime->usable && impl_->id != 0;
}

bool RimeSession::process_key(int keyval, int mask) {
  if (!available())
    return false;
  std::lock_guard lock(impl_->runtime->mutex);
  return impl_->runtime->api->process_key(impl_->id, keyval, mask) != 0;
}

std::string RimeSession::take_commit() {
  if (!available())
    return {};
  std::lock_guard lock(impl_->runtime->mutex);
  RIME_STRUCT(RimeCommit, commit);
  if (!impl_->runtime->api->get_commit(impl_->id, &commit))
    return {};
  std::string result = commit.text ? commit.text : "";
  (void)impl_->runtime->api->free_commit(&commit);
  return result;
}

RimeContextView RimeSession::context() const {
  RimeContextView view;
  if (!available())
    return view;
  std::lock_guard lock(impl_->runtime->mutex);
  RIME_STRUCT(RimeContext, context);
  if (!impl_->runtime->api->get_context(impl_->id, &context))
    return view;
  if (context.composition.preedit)
    view.preedit = context.composition.preedit;
  view.cursor = std::max(0, context.composition.cursor_pos);
  view.page_size = std::clamp(context.menu.page_size, 1, 16);
  view.highlighted = std::max(0, context.menu.highlighted_candidate_index);
  const int count = std::clamp(context.menu.num_candidates, 0, 100);
  if (context.menu.candidates) {
    for (int index = 0; index < count; ++index) {
      const auto &candidate = context.menu.candidates[index];
      view.candidates.push_back({candidate.text ? candidate.text : "",
                                 candidate.comment ? candidate.comment : ""});
    }
  }
  (void)impl_->runtime->api->free_context(&context);
  return view;
}

void RimeSession::clear() {
  if (!available())
    return;
  std::lock_guard lock(impl_->runtime->mutex);
  impl_->runtime->api->clear_composition(impl_->id);
}

std::string RimeSession::schema() const {
  if (!available())
    return {};
  std::lock_guard lock(impl_->runtime->mutex);
  char buffer[1024]{};
  if (!impl_->runtime->api->get_current_schema(impl_->id, buffer,
                                               sizeof(buffer)))
    return {};
  return buffer;
}

bool deploy_rime_workspace(const std::filesystem::path &user_data_dir,
                           std::string &error) {
  const auto shared = shared_data_dir();
  if (shared.empty()) {
    error = "Rime shared data was not found";
    return false;
  }
  try {
    std::filesystem::create_directories(user_data_dir);
    std::filesystem::create_directories(user_data_dir / "build");
    std::filesystem::copy_file(
        shared / "default.yaml", user_data_dir / "default.yaml",
        std::filesystem::copy_options::overwrite_existing);
    for (const auto &name :
         {"default.yaml", "key_bindings.yaml", "punctuation.yaml",
          "symbols.yaml", "luna_pinyin.schema.yaml", "luna_pinyin.dict.yaml",
          "essay.txt"}) {
      const auto source = shared / name;
      if (std::filesystem::is_regular_file(source))
        std::filesystem::copy_file(
            source, user_data_dir / name,
            std::filesystem::copy_options::overwrite_existing);
    }
    const std::string shared_string = shared.string();
    const std::string user_string = user_data_dir.string();
    const std::string logs_string =
        (home_path() / ".local/share/vocotype/rime").string();
    std::filesystem::create_directories(logs_string);
    const std::string prebuilt_string = (shared / "build").string();
    const std::string staging_string = (user_data_dir / "build").string();
    const char *modules[] = {"default", nullptr};
    RIME_STRUCT(RimeTraits, traits);
    traits.shared_data_dir = shared_string.c_str();
    traits.user_data_dir = user_string.c_str();
    traits.distribution_name = "VoCoType";
    traits.distribution_code_name = "vocotype";
    traits.distribution_version = "3";
    traits.app_name = "rime.vocotype.deployer";
    traits.modules = modules;
    traits.min_log_level = 2;
    traits.log_dir = logs_string.c_str();
    traits.prebuilt_data_dir = prebuilt_string.c_str();
    traits.staging_dir = staging_string.c_str();
    RimeApi *api = rime_get_api();
    if (!api || !RIME_API_AVAILABLE(api, deployer_initialize) ||
        !RIME_API_AVAILABLE(api, deploy)) {
      error = "librime deployment API is unavailable";
      return false;
    }
    api->deployer_initialize(&traits);
    if (!api->deploy()) {
      error = "librime failed to deploy the workspace";
      return false;
    }
    return std::filesystem::is_regular_file(user_data_dir /
                                            "build/default.yaml");
  } catch (const std::exception &exception) {
    error = exception.what();
    return false;
  }
}

} // namespace vocotype::desktop
