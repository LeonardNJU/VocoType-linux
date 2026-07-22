#include "vocotype/desktop/audio.hpp"
#include "vocotype/desktop/config.hpp"
#include "vocotype/desktop/ipc.hpp"
#include "vocotype/desktop/wav.hpp"
#ifdef VOCOTYPE_HAVE_RIME
#include "vocotype/desktop/rime_session.hpp"
#endif

#include <curl/curl.h>
#include <gtk/gtk.h>
#include <yaml-cpp/yaml.h>

#include <algorithm>
#include <array>
#include <atomic>
#include <cerrno>
#include <chrono>
#include <csignal>
#include <dirent.h>
#include <filesystem>
#include <fstream>
#include <functional>
#include <iomanip>
#include <memory>
#include <mutex>
#include <sstream>
#include <string>
#include <sys/stat.h>
#include <sys/wait.h>
#include <thread>
#include <unistd.h>
#include <vector>

using vocotype::desktop::Json;

#ifndef VOCOTYPE_VERSION
#define VOCOTYPE_VERSION "development"
#endif

namespace {

struct SettingsWindow {
  GtkApplication *application = nullptr;
  GtkWidget *window = nullptr;
  GtkLabel *overview_status = nullptr;
  GtkLabel *recognition_status = nullptr;
  GtkComboBoxText *audio_device = nullptr;
  GtkSpinButton *audio_rate = nullptr;
  GtkSpinButton *minimum_recording = nullptr;
  GtkCheckButton *streaming_enabled = nullptr;
  GtkCheckButton *normalization_enabled = nullptr;
  GtkTextView *itn_input = nullptr;
  GtkTextView *itn_output = nullptr;
  GtkTextView *terms = nullptr;
  GtkLabel *terms_status = nullptr;
  GtkCheckButton *slm_enabled = nullptr;
  GtkEntry *slm_endpoint = nullptr;
  GtkEntry *slm_model = nullptr;
  GtkEntry *slm_api_key = nullptr;
  GtkSpinButton *slm_min_chars = nullptr;
  GtkSpinButton *slm_timeout = nullptr;
  GtkCheckButton *slm_streaming = nullptr;
  GtkCheckButton *slm_thinking = nullptr;
  GtkCheckButton *edit_enabled = nullptr;
  GtkTextView *slm_source = nullptr;
  GtkTextView *slm_result = nullptr;
  GtkLabel *slm_status = nullptr;
  GtkLabel *playground_status = nullptr;
  GtkComboBoxText *audio_output = nullptr;
  GtkDrawingArea *playground_waveform = nullptr;
  GtkTextView *playground_result = nullptr;
  GtkTextView *playground_edit_source = nullptr;
  GtkEntry *playground_edit_instruction = nullptr;
  GtkTextView *playground_edit_result = nullptr;
  GtkLabel *playground_edit_status = nullptr;
  GtkLabel *version_status = nullptr;
  GtkLabel *support_status = nullptr;
  GtkTextView *doctor_output = nullptr;
  GtkTextView *feedback_view = nullptr;
  GtkEntry *feedback_endpoint = nullptr;
  GtkEntry *rime_schema = nullptr;
  GtkCheckButton *feedback_include_doctor = nullptr;
  GtkCheckButton *feedback_include_bundle = nullptr;
  GtkLabel *feedback_status = nullptr;
  GtkComboBoxText *fcitx_panel_style = nullptr;
  GtkCheckButton *fcitx_block_composing = nullptr;
  GtkCheckButton *fcitx_strip_period = nullptr;
  std::vector<vocotype::desktop::AudioDevice> devices;
  std::vector<vocotype::desktop::AudioOutputDevice> output_devices;
  std::vector<std::pair<double, double>> waveform;
  std::filesystem::path last_recording;
  Json config = Json::object();
};

struct IdleClosure {
  std::function<void()> function;
};
gboolean run_idle(gpointer data) {
  std::unique_ptr<IdleClosure> closure(static_cast<IdleClosure *>(data));
  closure->function();
  return G_SOURCE_REMOVE;
}
void post_idle(std::function<void()> function) {
  g_idle_add_full(G_PRIORITY_DEFAULT, run_idle,
                  new IdleClosure{std::move(function)}, nullptr);
}

template <typename Work, typename Done> void run_async(Work work, Done done) {
  std::thread([work = std::move(work), done = std::move(done)]() mutable {
    try {
      auto result = work();
      post_idle([done = std::move(done), result = std::move(result)]() mutable {
        done(std::move(result));
      });
    } catch (const std::exception &error) {
      const std::string message = error.what();
      post_idle([done = std::move(done), message]() mutable {
        using Result = decltype(work());
        done(Result{{"success", false}, {"error", message}});
      });
    }
  }).detach();
}

std::string text_view_text(GtkTextView *view) {
  GtkTextBuffer *buffer = gtk_text_view_get_buffer(view);
  GtkTextIter begin{};
  GtkTextIter end{};
  gtk_text_buffer_get_bounds(buffer, &begin, &end);
  gchar *value = gtk_text_buffer_get_text(buffer, &begin, &end, false);
  std::string result = value ? value : "";
  g_free(value);
  return result;
}
void set_text(GtkTextView *view, const std::string &text) {
  gtk_text_buffer_set_text(gtk_text_view_get_buffer(view), text.c_str(), -1);
}
void set_label(GtkLabel *label, const std::string &text) {
  gtk_label_set_text(label, text.c_str());
}

GtkWidget *label(const char *text, bool title = false) {
  GtkWidget *widget = gtk_label_new(text);
  gtk_label_set_xalign(GTK_LABEL(widget), 0.0F);
  gtk_label_set_line_wrap(GTK_LABEL(widget), true);
  if (title) {
    PangoAttrList *attributes = pango_attr_list_new();
    pango_attr_list_insert(attributes,
                           pango_attr_weight_new(PANGO_WEIGHT_BOLD));
    pango_attr_list_insert(attributes, pango_attr_scale_new(1.35));
    gtk_label_set_attributes(GTK_LABEL(widget), attributes);
    pango_attr_list_unref(attributes);
  }
  return widget;
}

GtkWidget *page_box(const char *title, const char *subtitle) {
  GtkWidget *box = gtk_box_new(GTK_ORIENTATION_VERTICAL, 12);
  gtk_container_set_border_width(GTK_CONTAINER(box), 20);
  gtk_box_pack_start(GTK_BOX(box), label(title, true), false, false, 0);
  gtk_box_pack_start(GTK_BOX(box), label(subtitle), false, false, 0);
  return box;
}

GtkWidget *scrolled_text(GtkTextView **out, int height = 150) {
  GtkWidget *scroll = gtk_scrolled_window_new(nullptr, nullptr);
  gtk_widget_set_size_request(scroll, -1, height);
  gtk_scrolled_window_set_policy(GTK_SCROLLED_WINDOW(scroll),
                                 GTK_POLICY_AUTOMATIC, GTK_POLICY_AUTOMATIC);
  GtkWidget *view = gtk_text_view_new();
  gtk_text_view_set_wrap_mode(GTK_TEXT_VIEW(view), GTK_WRAP_WORD_CHAR);
  gtk_container_add(GTK_CONTAINER(scroll), view);
  *out = GTK_TEXT_VIEW(view);
  return scroll;
}

GtkWidget *row(const char *caption, GtkWidget *control) {
  GtkWidget *box = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 10);
  GtkWidget *caption_widget = label(caption);
  gtk_widget_set_size_request(caption_widget, 180, -1);
  gtk_box_pack_start(GTK_BOX(box), caption_widget, false, false, 0);
  gtk_box_pack_start(GTK_BOX(box), control, true, true, 0);
  return box;
}

Json load_config() {
  Json config = vocotype::desktop::read_json_file(
      vocotype::desktop::runtime_config_path(), true);
  if (!config.is_object())
    config = Json::object();
  if (!config.contains("audio") || !config["audio"].is_object())
    config["audio"] = Json::object();
  if (!config.contains("asr") || !config["asr"].is_object())
    config["asr"] = Json::object();
  if (!config.contains("asr_streaming") || !config["asr_streaming"].is_object())
    config["asr_streaming"] = Json::object();
  if (!config.contains("normalization") || !config["normalization"].is_object())
    config["normalization"] = Json::object();
  if (!config.contains("slm") || !config["slm"].is_object())
    config["slm"] = Json::object();
  config["asr"]["native_enabled"] = true;
  return config;
}

void save_config(SettingsWindow &window) {
  auto &audio = window.config["audio"];
  const int active =
      gtk_combo_box_get_active(GTK_COMBO_BOX(window.audio_device));
  if (active >= 0 && static_cast<std::size_t>(active) < window.devices.size()) {
    audio["device"] = window.devices[static_cast<std::size_t>(active)].id;
    audio["device_name"] =
        window.devices[static_cast<std::size_t>(active)].name;
  } else {
    audio["device"] = nullptr;
    audio.erase("device_name");
  }
  audio["sample_rate"] = gtk_spin_button_get_value_as_int(window.audio_rate);
  audio["block_ms"] = 20;
  audio["min_recording_ms"] =
      gtk_spin_button_get_value_as_int(window.minimum_recording);
  window.config["asr"]["native_enabled"] = true;
  window.config["asr_streaming"]["enabled"] =
      gtk_toggle_button_get_active(GTK_TOGGLE_BUTTON(window.streaming_enabled));
  window.config["normalization"]["enabled"] = gtk_toggle_button_get_active(
      GTK_TOGGLE_BUTTON(window.normalization_enabled));
  auto &slm = window.config["slm"];
  slm["enabled"] =
      gtk_toggle_button_get_active(GTK_TOGGLE_BUTTON(window.slm_enabled));
  slm["endpoint"] = gtk_entry_get_text(window.slm_endpoint);
  slm["model"] = gtk_entry_get_text(window.slm_model);
  const std::string api_key = gtk_entry_get_text(window.slm_api_key);
  if (!api_key.empty() && api_key != "••••••••")
    slm["api_key"] = api_key;
  slm["min_chars"] = gtk_spin_button_get_value_as_int(window.slm_min_chars);
  slm["timeout_ms"] = gtk_spin_button_get_value_as_int(window.slm_timeout);
  slm["remote_stream"] =
      gtk_toggle_button_get_active(GTK_TOGGLE_BUTTON(window.slm_streaming));
  slm["enable_thinking"] =
      gtk_toggle_button_get_active(GTK_TOGGLE_BUTTON(window.slm_thinking));
  slm["edit_enabled"] =
      gtk_toggle_button_get_active(GTK_TOGGLE_BUTTON(window.edit_enabled));
  slm["edit_max_tokens"] = std::max(1024, slm.value("edit_max_tokens", 1024));
  vocotype::desktop::write_json_file_atomic(
      vocotype::desktop::runtime_config_path(), window.config);
}

void refresh_devices(SettingsWindow &window) {
  gtk_combo_box_text_remove_all(window.audio_device);
  if (window.audio_output)
    gtk_combo_box_text_remove_all(window.audio_output);
  window.devices = vocotype::desktop::list_input_devices();
  window.output_devices = vocotype::desktop::list_output_devices();
  const auto configured = vocotype::desktop::load_audio_config();
  int selected = -1;
  for (std::size_t index = 0; index < window.devices.size(); ++index) {
    const auto &device = window.devices[index];
    std::string display = device.name;
    if (device.is_default)
      display += "（默认）";
    gtk_combo_box_text_append_text(window.audio_device, display.c_str());
    if ((configured.device_id && *configured.device_id == device.id) ||
        (!configured.device_name.empty() &&
         configured.device_name == device.name))
      selected = static_cast<int>(index);
  }
  if (selected < 0 && !window.devices.empty()) {
    auto found =
        std::find_if(window.devices.begin(), window.devices.end(),
                     [](const auto &device) { return device.is_default; });
    selected = found == window.devices.end()
                   ? 0
                   : static_cast<int>(found - window.devices.begin());
  }
  gtk_combo_box_set_active(GTK_COMBO_BOX(window.audio_device), selected);
  if (window.audio_output) {
    int selected_output = -1;
    for (std::size_t index = 0; index < window.output_devices.size(); ++index) {
      const auto &device = window.output_devices[index];
      std::string display = device.name;
      if (device.is_default)
        display += "（默认）";
      gtk_combo_box_text_append_text(window.audio_output, display.c_str());
      if (device.is_default)
        selected_output = static_cast<int>(index);
    }
    if (selected_output < 0 && !window.output_devices.empty())
      selected_output = 0;
    gtk_combo_box_set_active(GTK_COMBO_BOX(window.audio_output),
                             selected_output);
  }
  set_label(window.recognition_status,
            "输入设备：" + std::to_string(window.devices.size()) +
                "；输出设备：" + std::to_string(window.output_devices.size()));
}

bool terminate_owned_core() {
  DIR *directory = opendir("/proc");
  if (!directory)
    return false;
  bool found = false;
  while (dirent *entry = readdir(directory)) {
    char *end = nullptr;
    const long pid = std::strtol(entry->d_name, &end, 10);
    if (!end || *end != '\0' || pid <= 1)
      continue;
    const std::filesystem::path root =
        std::filesystem::path("/proc") / entry->d_name;
    struct stat status{};
    if (stat(root.c_str(), &status) != 0 || status.st_uid != getuid())
      continue;
    std::ifstream input(root / "comm");
    std::string name;
    std::getline(input, name);
    if (name == "vocotype-core") {
      (void)kill(static_cast<pid_t>(pid), SIGTERM);
      found = true;
    }
  }
  closedir(directory);
  return found;
}

void refresh_overview(SettingsWindow &window) {
  std::ostringstream output;
  output << "配置：" << vocotype::desktop::runtime_config_path() << "\n";
  output << "术语：" << vocotype::desktop::terms_path() << "\n";
  output << "Socket：" << vocotype::desktop::backend_socket_path() << "\n";
  try {
    const Json capabilities = vocotype::desktop::unix_json_request(
        vocotype::desktop::backend_socket_path(), {{"type", "capabilities"}},
        1500);
    output << "Core：原生 C++，协议 "
           << capabilities.value("protocol_version", 0) << "\n";
    const auto features = capabilities.value("features", Json::object());
    output << "最终 ASR："
           << (features.value("final_asr_ready", false) ? "就绪" : "未就绪")
           << "\n";
    output << "实时预览："
           << (features.value("streaming_asr_ready", false) ? "就绪"
                                                            : "按需加载")
           << "\n";
    output << "SLM："
           << (features.value("slm_enabled", false) ? "已启用" : "未启用")
           << "\n";
    output << "语音编辑："
           << (features.value("voice_edit", false) ? "已启用" : "未启用");
  } catch (const std::exception &error) {
    output << "Core：未连接（" << error.what() << "）";
  }
  set_label(window.overview_status, output.str());
}

Json capture_recording(
    int duration_ms,
    const std::function<void(double, double)> &waveform_callback = {}) {
  const auto config = vocotype::desktop::load_audio_config();
  const auto device = vocotype::desktop::resolve_input_device(config);
  const int rate =
      vocotype::desktop::resolve_sample_rate(device, config.sample_rate);
  std::atomic_bool stop{false};
  std::vector<std::int16_t> samples;
  std::string error;
  vocotype::desktop::AudioCapture capture(device, rate, config.block_ms);
  std::thread worker([&] {
    try {
      capture.run(stop, [&](const auto &block) {
        samples.insert(samples.end(), block.begin(), block.end());
        if (waveform_callback && !block.empty()) {
          const auto [minimum, maximum] =
              std::minmax_element(block.begin(), block.end());
          waveform_callback(static_cast<double>(*minimum) / 32768.0,
                            static_cast<double>(*maximum) / 32768.0);
        }
      });
    } catch (const std::exception &exception) {
      error = exception.what();
      stop.store(true);
    }
  });
  std::this_thread::sleep_for(std::chrono::milliseconds(duration_ms));
  stop.store(true);
  worker.join();
  if (!error.empty())
    return {{"success", false}, {"error", error}};
  if (samples.empty())
    return {{"success", false}, {"error", "没有采集到音频"}};
  const auto path = vocotype::desktop::create_secure_wav_path();
  vocotype::desktop::write_pcm16_wav(path, samples, rate);
  return {{"success", true},
          {"path", path.string()},
          {"sample_rate", rate},
          {"frames", samples.size()},
          {"device", device.name}};
}

Json play_recording(const std::filesystem::path &path, int output_id) {
  const auto wav = vocotype::desktop::read_pcm16_wav(path);
  const auto output = vocotype::desktop::resolve_output_device(output_id);
  vocotype::desktop::play_pcm16(wav.samples, wav.sample_rate, output);
  return {{"success", true},
          {"device", output.name},
          {"sample_rate", wav.sample_rate},
          {"frames", wav.samples.size()}};
}

gboolean draw_waveform(GtkWidget *widget, cairo_t *context, gpointer data) {
  auto *window = static_cast<SettingsWindow *>(data);
  const double width = gtk_widget_get_allocated_width(widget);
  const double height = gtk_widget_get_allocated_height(widget);
  cairo_set_source_rgb(context, 0.12, 0.12, 0.12);
  cairo_paint(context);
  cairo_set_source_rgb(context, 0.72, 0.78, 0.86);
  cairo_set_line_width(context, 1.0);
  cairo_move_to(context, 0.0, height / 2.0);
  cairo_line_to(context, width, height / 2.0);
  cairo_stroke(context);
  if (window->waveform.empty())
    return FALSE;
  cairo_set_source_rgb(context, 0.35, 0.68, 0.95);
  const std::size_t count = window->waveform.size();
  for (std::size_t index = 0; index < count; ++index) {
    const double x = count <= 1 ? 0.0
                                : width * static_cast<double>(index) /
                                      static_cast<double>(count - 1);
    const auto [minimum, maximum] = window->waveform[index];
    cairo_move_to(context, x, height * (0.5 - 0.45 * maximum));
    cairo_line_to(context, x, height * (0.5 - 0.45 * minimum));
  }
  cairo_stroke(context);
  return FALSE;
}

std::filesystem::path project_root() {
  if (const char *configured = std::getenv("VOCOTYPE_PROJECT_DIR");
      configured && *configured)
    return std::filesystem::path(configured);
  if (std::filesystem::is_regular_file("/usr/share/vocotype/.system-package"))
    return "/usr/share/vocotype";
  return std::filesystem::current_path();
}

Json run_command(const std::vector<std::string> &arguments) {
  if (arguments.empty())
    return {{"success", false}, {"error", "empty command"}};
  int output_pipe[2]{};
  if (pipe(output_pipe) != 0)
    return {{"success", false}, {"error", "cannot create command pipe"}};
  const pid_t child = fork();
  if (child < 0) {
    close(output_pipe[0]);
    close(output_pipe[1]);
    return {{"success", false}, {"error", "cannot fork command"}};
  }
  if (child == 0) {
    dup2(output_pipe[1], STDOUT_FILENO);
    dup2(output_pipe[1], STDERR_FILENO);
    close(output_pipe[0]);
    close(output_pipe[1]);
    std::vector<char *> argv;
    argv.reserve(arguments.size() + 1);
    for (const auto &argument : arguments)
      argv.push_back(const_cast<char *>(argument.c_str()));
    argv.push_back(nullptr);
    execvp(argv[0], argv.data());
    _exit(127);
  }
  close(output_pipe[1]);
  std::string output;
  std::array<char, 4096> buffer{};
  for (;;) {
    const ssize_t count = read(output_pipe[0], buffer.data(), buffer.size());
    if (count < 0 && errno == EINTR)
      continue;
    if (count <= 0)
      break;
    output.append(buffer.data(), static_cast<std::size_t>(count));
    if (output.size() > 2U * 1024U * 1024U)
      break;
  }
  close(output_pipe[0]);
  int status = 0;
  while (waitpid(child, &status, 0) < 0 && errno == EINTR) {
  }
  const bool success = WIFEXITED(status) && WEXITSTATUS(status) == 0;
  return {{"success", success},
          {"output", output},
          {"error", success ? "" : output}};
}

std::string file_text(const std::filesystem::path &path);
void write_text_atomic(const std::filesystem::path &path,
                       const std::string &text);
std::string doctor_report();

std::size_t curl_write(void *contents, std::size_t size, std::size_t count,
                       void *user_data) {
  const std::size_t bytes = size * count;
  static_cast<std::string *>(user_data)->append(
      static_cast<const char *>(contents), bytes);
  return bytes;
}

Json query_latest_release() {
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
  curl_easy_setopt(curl, CURLOPT_USERAGENT,
                   "VoCoType-native-settings/" VOCOTYPE_VERSION);
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
            {"current", VOCOTYPE_VERSION},
            {"latest", value.value("tag_name", "unknown")},
            {"url", value.value("html_url", "")},
            {"published_at", value.value("published_at", "")},
            {"http_status", status}};
  } catch (const std::exception &error) {
    return {{"success", false}, {"error", error.what()}};
  }
}

bool open_uri(GtkWindow *parent, const std::string &uri, std::string &error) {
  GError *gerror = nullptr;
  const gboolean opened =
      gtk_show_uri_on_window(parent, uri.c_str(), GDK_CURRENT_TIME, &gerror);
  if (!opened) {
    error = gerror && gerror->message ? gerror->message : "无法打开链接";
    if (gerror)
      g_error_free(gerror);
    return false;
  }
  return true;
}

std::string uri_escape(const std::string &text) {
  gchar *escaped = g_uri_escape_string(text.c_str(), nullptr, true);
  std::string result = escaped ? escaped : "";
  g_free(escaped);
  return result;
}

Json uninstall_integration(const std::string &framework) {
  const auto script = project_root() / "installers/uninstall-native-user.sh";
  if (!std::filesystem::is_regular_file(script))
    return {{"success", false},
            {"error", "native uninstaller was not found: " + script.string()}};
  return run_command(
      {"bash", script.string(), "--framework", framework, "--non-interactive"});
}

std::filesystem::path support_directory() {
  auto directory = vocotype::desktop::config_dir() / "support";
  std::filesystem::create_directories(directory);
  return directory;
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

Json create_support_bundle(const std::string &doctor) {
  const auto directory = support_directory();
  const auto stamp =
      std::to_string(std::chrono::duration_cast<std::chrono::seconds>(
                         std::chrono::system_clock::now().time_since_epoch())
                         .count());
  const auto staging = directory / (".bundle-" + stamp);
  const auto archive = directory / ("vocotype-support-" + stamp + ".tar.gz");
  std::filesystem::create_directories(staging);
  try {
    write_text_atomic(staging / "doctor.txt", doctor);
    write_text_atomic(staging / "version.txt",
                      std::string("version=") + VOCOTYPE_VERSION + "\n");
    write_text_atomic(staging / "runtime-config.redacted.json",
                      redact_config(load_config()) + "\n");
    const auto marker =
        std::filesystem::path("/usr/share/vocotype/.system-package");
    if (std::filesystem::is_regular_file(marker))
      write_text_atomic(staging / "system-package.txt", file_text(marker));
    const Json processes =
        run_command({"sh", "-c",
                     "ps -eo pid,ppid,lstart,comm,args | grep -E "
                     "'[v]ocotype|[f]citx5|[i]bus'"});
    write_text_atomic(staging / "processes.txt", processes.value("output", ""));
    const Json logs =
        run_command({"sh", "-c",
                     "journalctl --user -b --no-pager -n 500 2>/dev/null | "
                     "grep -i -E 'vocotype|fcitx|ibus' | tail -300"});
    write_text_atomic(staging / "journal.txt", logs.value("output", ""));
    const Json packed = run_command(
        {"tar", "-czf", archive.string(), "-C", staging.string(), "."});
    std::filesystem::remove_all(staging);
    if (!packed.value("success", false))
      return {{"success", false}, {"error", packed.value("error", "tar 失败")}};
    return {{"success", true}, {"path", archive.string()}};
  } catch (const std::exception &error) {
    std::filesystem::remove_all(staging);
    return {{"success", false}, {"error", error.what()}};
  }
}

std::filesystem::path fcitx_config_path() {
  return vocotype::desktop::home_path() / ".config/fcitx5/conf/vocotype.conf";
}

std::string config_value(const std::filesystem::path &path,
                         const std::string &key, const std::string &fallback) {
  std::istringstream input(file_text(path));
  std::string line;
  while (std::getline(input, line)) {
    if (line.rfind(key + "=", 0) == 0)
      return line.substr(key.size() + 1);
  }
  return fallback;
}

void update_fcitx_config(
    const std::vector<std::pair<std::string, std::string>> &values) {
  const auto path = fcitx_config_path();
  std::vector<std::string> lines;
  std::istringstream input(file_text(path));
  std::string line;
  while (std::getline(input, line))
    lines.push_back(line);
  for (const auto &[key, value] : values) {
    bool replaced = false;
    for (auto &existing : lines) {
      if (existing.rfind(key + "=", 0) == 0) {
        existing = key + "=" + value;
        replaced = true;
        break;
      }
    }
    if (!replaced)
      lines.push_back(key + "=" + value);
  }
  std::ostringstream output;
  for (const auto &entry : lines)
    output << entry << '\n';
  write_text_atomic(path, output.str());
}

std::string yaml_scalar_value(const std::filesystem::path &path,
                              const std::string &key,
                              const std::string &fallback) {
  std::istringstream input(file_text(path));
  std::string line;
  while (std::getline(input, line)) {
    const std::string prefix = key + ":";
    if (line.rfind(prefix, 0) != 0)
      continue;
    std::string value = line.substr(prefix.size());
    const auto first = value.find_first_not_of(" \t'\"");
    const auto last = value.find_last_not_of(" \t'\"");
    if (first != std::string::npos && last != std::string::npos)
      return value.substr(first, last - first + 1);
  }
  return fallback;
}

void update_rime_schema(const std::string &schema) {
  const auto path = vocotype::desktop::config_dir() / "rime/user.yaml";
  std::vector<std::string> lines;
  std::istringstream input(file_text(path));
  std::string line;
  bool replaced = false;
  while (std::getline(input, line)) {
    if (line.rfind("previously_selected_schema:", 0) == 0) {
      lines.push_back("previously_selected_schema: " + schema);
      replaced = true;
    } else {
      lines.push_back(line);
    }
  }
  if (!replaced)
    lines.push_back("previously_selected_schema: " + schema);
  std::ostringstream output;
  for (const auto &entry : lines)
    output << entry << '\n';
  write_text_atomic(path, output.str());
}

std::string installation_id() {
  const auto path = vocotype::desktop::config_dir() / "installation-id";
  std::string value = file_text(path);
  value.erase(std::remove(value.begin(), value.end(), '\n'), value.end());
  if (!value.empty())
    return value;
  gchar *generated = g_uuid_string_random();
  value = generated ? generated : "unknown";
  g_free(generated);
  write_text_atomic(path, value + "\n");
  return value;
}

bool valid_feedback_endpoint(const std::string &endpoint) {
  if (endpoint.rfind("https://", 0) == 0)
    return true;
  return endpoint.rfind("http://127.0.0.1", 0) == 0 ||
         endpoint.rfind("http://localhost", 0) == 0 ||
         endpoint.rfind("http://[::1]", 0) == 0;
}

Json submit_feedback(const std::string &endpoint, const std::string &message,
                     const std::string &doctor,
                     const std::filesystem::path &bundle) {
  if (message.empty())
    return {{"success", false}, {"error", "反馈内容不能为空"}};
  if (message.size() > 10000U)
    return {{"success", false}, {"error", "反馈内容不能超过 10000 字"}};
  if (!valid_feedback_endpoint(endpoint))
    return {{"success", false},
            {"error", "反馈端点必须使用 HTTPS（localhost 调试除外）"}};
  if (!bundle.empty() &&
      (!std::filesystem::is_regular_file(bundle) ||
       std::filesystem::file_size(bundle) > 5U * 1024U * 1024U))
    return {{"success", false}, {"error", "支持包不存在或超过 5 MiB"}};

  Json payload{{"schema_version", 1},
               {"product", "VoCoType-linux"},
               {"version", VOCOTYPE_VERSION},
               {"category", "other"},
               {"message", message},
               {"installation_id", installation_id()},
               {"doctor", doctor.empty()
                              ? Json(nullptr)
                              : Json::array({{{"check_id", "native_doctor"},
                                              {"status", "info"},
                                              {"title", "Native Doctor"},
                                              {"details", doctor}}})},
               {"contact", ""}};
  const Json platform = run_command({"uname", "-a"});
  payload["platform"] = platform.value("output", "Linux");

  CURL *curl = curl_easy_init();
  if (!curl)
    return {{"success", false}, {"error", "无法初始化 libcurl"}};
  curl_mime *mime = curl_mime_init(curl);
  curl_mimepart *part = curl_mime_addpart(mime);
  const std::string payload_text = payload.dump();
  curl_mime_name(part, "payload");
  curl_mime_type(part, "application/json; charset=utf-8");
  curl_mime_data(part, payload_text.c_str(), payload_text.size());
  if (!bundle.empty()) {
    part = curl_mime_addpart(mime);
    curl_mime_name(part, "bundle");
    curl_mime_type(part, "application/gzip");
    curl_mime_filedata(part, bundle.c_str());
  }
  std::string response;
  curl_easy_setopt(curl, CURLOPT_URL, endpoint.c_str());
  curl_easy_setopt(curl, CURLOPT_MIMEPOST, mime);
  curl_easy_setopt(curl, CURLOPT_CONNECTTIMEOUT, 10L);
  curl_easy_setopt(curl, CURLOPT_TIMEOUT, 30L);
  curl_easy_setopt(curl, CURLOPT_FAILONERROR, 1L);
  curl_easy_setopt(curl, CURLOPT_USERAGENT,
                   "VoCoType-native-settings/" VOCOTYPE_VERSION);
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

std::filesystem::path native_payload_directory() {
  const std::vector<std::filesystem::path> candidates = {
      vocotype::desktop::home_path() / ".local/lib/vocotype-streaming",
      "/usr/lib/vocotype", "/usr/lib64/vocotype"};
  for (const auto &candidate : candidates) {
    if (std::filesystem::is_regular_file(candidate / ".native-payload.sha256"))
      return candidate;
  }
  return {};
}

Json verify_native_payload() {
  const auto directory = native_payload_directory();
  if (directory.empty())
    return {{"success", false},
            {"error", "未找到 native payload checksum 清单"}};
  return run_command(
      {"sh", "-c",
       "cd " + directory.string() + " && sha256sum -c .native-payload.sha256"});
}

std::string native_model_manager() {
  return vocotype::desktop::find_executable(
      "vocotype-model-manager",
      {vocotype::desktop::home_path() /
           ".local/lib/vocotype-native/bin/vocotype-model-manager",
       "/usr/libexec/vocotype-model-manager",
       "/usr/lib/vocotype/vocotype-model-manager",
       "/usr/lib64/vocotype/vocotype-model-manager"});
}

Json install_integration(const std::string &framework) {
  const auto script = project_root() / "installers/install-native-user.sh";
  if (!std::filesystem::is_regular_file(script))
    return {{"success", false},
            {"error", "native installer was not found: " + script.string()}};
  return run_command(
      {"bash", script.string(), "--framework", framework, "--non-interactive"});
}

Json download_models() {
  const std::string manager = native_model_manager();
  if (manager.empty())
    return {{"success", false},
            {"error", "native model manager was not found"}};
  return run_command({manager, "--download", "--all"});
}

std::string file_text(const std::filesystem::path &path) {
  std::ifstream input(path, std::ios::binary);
  if (!input)
    return {};
  return std::string(std::istreambuf_iterator<char>(input),
                     std::istreambuf_iterator<char>());
}
void write_text_atomic(const std::filesystem::path &path,
                       const std::string &text) {
  std::filesystem::create_directories(path.parent_path());
  const auto temporary = path.string() + ".tmp." + std::to_string(getpid());
  {
    std::ofstream output(temporary, std::ios::trunc);
    if (!output)
      throw std::runtime_error("无法写入 " + temporary);
    output << text;
  }
  std::filesystem::permissions(temporary,
                               std::filesystem::perms::owner_read |
                                   std::filesystem::perms::owner_write,
                               std::filesystem::perm_options::replace);
  std::filesystem::rename(temporary, path);
}

void populate_from_config(SettingsWindow &window) {
  window.config = load_config();
  const auto &audio = window.config["audio"];
  gtk_spin_button_set_value(window.audio_rate,
                            audio.value("sample_rate", 16000));
  gtk_spin_button_set_value(window.minimum_recording,
                            audio.value("min_recording_ms", 1000));
  gtk_toggle_button_set_active(
      GTK_TOGGLE_BUTTON(window.streaming_enabled),
      window.config["asr_streaming"].value("enabled", false));
  gtk_toggle_button_set_active(
      GTK_TOGGLE_BUTTON(window.normalization_enabled),
      window.config["normalization"].value("enabled", true));
  const auto &slm = window.config["slm"];
  gtk_toggle_button_set_active(GTK_TOGGLE_BUTTON(window.slm_enabled),
                               slm.value("enabled", false));
  gtk_entry_set_text(
      window.slm_endpoint,
      slm.value("endpoint", "http://127.0.0.1:18080/v1/chat/completions")
          .c_str());
  gtk_entry_set_text(window.slm_model,
                     slm.value("model", "Qwen/Qwen3.5-0.8B").c_str());
  gtk_entry_set_text(window.slm_api_key,
                     slm.value("api_key", "").empty() ? "" : "••••••••");
  gtk_spin_button_set_value(window.slm_min_chars, slm.value("min_chars", 8));
  gtk_spin_button_set_value(window.slm_timeout, slm.value("timeout_ms", 20000));
  gtk_toggle_button_set_active(GTK_TOGGLE_BUTTON(window.slm_streaming),
                               slm.value("remote_stream", true));
  gtk_toggle_button_set_active(GTK_TOGGLE_BUTTON(window.slm_thinking),
                               slm.value("enable_thinking", false));
  gtk_toggle_button_set_active(GTK_TOGGLE_BUTTON(window.edit_enabled),
                               slm.value("edit_enabled", true));
  if (window.fcitx_panel_style) {
    const std::string style =
        config_value(fcitx_config_path(), "PanelStyle", "minimal");
    gtk_combo_box_set_active(GTK_COMBO_BOX(window.fcitx_panel_style),
                             style == "animated" ? 1 : 0);
  }
  if (window.fcitx_block_composing)
    gtk_toggle_button_set_active(
        GTK_TOGGLE_BUTTON(window.fcitx_block_composing),
        config_value(fcitx_config_path(), "BlockWhenComposing", "True") !=
            "False");
  if (window.fcitx_strip_period)
    gtk_toggle_button_set_active(GTK_TOGGLE_BUTTON(window.fcitx_strip_period),
                                 config_value(fcitx_config_path(),
                                              "StripTrailingPeriodOnCommit",
                                              "False") == "True");
  if (window.rime_schema)
    gtk_entry_set_text(
        window.rime_schema,
        yaml_scalar_value(vocotype::desktop::config_dir() / "rime/user.yaml",
                          "previously_selected_schema", "luna_pinyin")
            .c_str());
  refresh_devices(window);
  std::string terms = file_text(vocotype::desktop::terms_path());
  if (terms.empty())
    terms = "terms:\n  - canonical: VoCoType\n    aliases: [沃口泰普]\n    "
            "hotword: true\n    protect: true\n\nprotect:\n  - 三体问题\n";
  set_text(window.terms, terms);
  refresh_overview(window);
}

GtkWidget *build_overview(SettingsWindow &window) {
  GtkWidget *page = page_box(
      "概览", "所有运行路径均为编译后的本地程序，不需要 Python 虚拟环境。");
  window.overview_status = GTK_LABEL(label("正在检查…"));
  gtk_label_set_selectable(window.overview_status, true);
  gtk_box_pack_start(GTK_BOX(page), GTK_WIDGET(window.overview_status), false,
                     false, 0);
  GtkWidget *actions = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 8);
  GtkWidget *refresh = gtk_button_new_with_label("刷新状态");
  GtkWidget *restart = gtk_button_new_with_label("重启原生 Core");
  GtkWidget *rime = gtk_button_new_with_label("部署 Rime 数据");
  gtk_box_pack_start(GTK_BOX(actions), refresh, false, false, 0);
  gtk_box_pack_start(GTK_BOX(actions), restart, false, false, 0);
  gtk_box_pack_start(GTK_BOX(actions), rime, false, false, 0);
  gtk_box_pack_start(GTK_BOX(page), actions, false, false, 0);
  GtkWidget *lifecycle = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 8);
  GtkWidget *install_fcitx = gtk_button_new_with_label("安装 / 修复 Fcitx 5");
  GtkWidget *install_ibus = gtk_button_new_with_label("安装 / 修复 IBus");
  GtkWidget *uninstall_fcitx = gtk_button_new_with_label("卸载 Fcitx 5 集成");
  GtkWidget *uninstall_ibus = gtk_button_new_with_label("卸载 IBus 集成");
  GtkWidget *models = gtk_button_new_with_label("校验并下载模型");
  gtk_box_pack_start(GTK_BOX(lifecycle), install_fcitx, false, false, 0);
  gtk_box_pack_start(GTK_BOX(lifecycle), install_ibus, false, false, 0);
  gtk_box_pack_start(GTK_BOX(lifecycle), uninstall_fcitx, false, false, 0);
  gtk_box_pack_start(GTK_BOX(lifecycle), uninstall_ibus, false, false, 0);
  gtk_box_pack_start(GTK_BOX(lifecycle), models, false, false, 0);
  gtk_box_pack_start(GTK_BOX(page), lifecycle, false, false, 0);
  g_signal_connect_swapped(
      refresh, "clicked",
      G_CALLBACK(+[](SettingsWindow *self) { refresh_overview(*self); }),
      &window);
  g_signal_connect_swapped(
      restart, "clicked", G_CALLBACK(+[](SettingsWindow *self) {
        set_label(self->overview_status, "正在重启原生 Core…");
        run_async(
            [self] {
              terminate_owned_core();
              std::this_thread::sleep_for(std::chrono::milliseconds(300));
              const bool ready = vocotype::desktop::ensure_native_core(
                  vocotype::desktop::backend_socket_path(),
                  vocotype::desktop::runtime_config_path());
              return Json{{"success", ready},
                          {"error", ready ? "" : "启动失败"}};
            },
            [self](Json result) {
              refresh_overview(*self);
              if (!result.value("success", false))
                set_label(self->overview_status,
                          "Core 重启失败：" + result.value("error", "unknown"));
            });
      }),
      &window);
  g_signal_connect_swapped(
      rime, "clicked", G_CALLBACK(+[](SettingsWindow *self) {
#ifdef VOCOTYPE_HAVE_RIME
        set_label(self->overview_status, "正在部署 Rime…");
        run_async(
            [] {
              std::string error;
              const bool success = vocotype::desktop::deploy_rime_workspace(
                  vocotype::desktop::config_dir() / "rime", error);
              return Json{{"success", success}, {"error", error}};
            },
            [self](Json result) {
              refresh_overview(*self);
              if (!result.value("success", false))
                set_label(self->overview_status,
                          "Rime 部署失败：" + result.value("error", "unknown"));
            });
#else
    set_label(self->overview_status, "此构建不含 IBus/Rime 支持");
#endif
      }),
      &window);
  auto install_handler = +[](GtkButton *button, gpointer data) {
    auto *self = static_cast<SettingsWindow *>(data);
    const std::string framework =
        std::string(gtk_button_get_label(button)).find("IBus") !=
                std::string::npos
            ? "ibus"
            : "fcitx5";
    set_label(self->overview_status, "正在安装 / 修复 " + framework + "…");
    run_async([framework] { return install_integration(framework); },
              [self, framework](Json result) {
                refresh_overview(*self);
                if (!result.value("success", false))
                  set_label(self->overview_status,
                            framework + " 安装失败：\n" +
                                result.value("error", "unknown"));
              });
  };
  g_signal_connect(install_fcitx, "clicked", G_CALLBACK(install_handler),
                   &window);
  g_signal_connect(install_ibus, "clicked", G_CALLBACK(install_handler),
                   &window);
  auto uninstall_handler = +[](GtkButton *button, gpointer data) {
    auto *self = static_cast<SettingsWindow *>(data);
    const std::string framework =
        std::string(gtk_button_get_label(button)).find("IBus") !=
                std::string::npos
            ? "ibus"
            : "fcitx5";
    set_label(self->overview_status, "正在卸载 " + framework + " 集成…");
    run_async([framework] { return uninstall_integration(framework); },
              [self, framework](Json result) {
                refresh_overview(*self);
                set_label(self->overview_status,
                          result.value("success", false)
                              ? framework + " 集成已卸载；配置和模型已保留"
                              : framework + " 卸载失败：\n" +
                                    result.value("error", "unknown"));
              });
  };
  g_signal_connect(uninstall_fcitx, "clicked", G_CALLBACK(uninstall_handler),
                   &window);
  g_signal_connect(uninstall_ibus, "clicked", G_CALLBACK(uninstall_handler),
                   &window);
  g_signal_connect_swapped(
      models, "clicked", G_CALLBACK(+[](SettingsWindow *self) {
        set_label(self->overview_status, "正在校验并下载原生 ASR 模型…");
        run_async([] { return download_models(); },
                  [self](Json result) {
                    refresh_overview(*self);
                    if (!result.value("success", false))
                      set_label(self->overview_status,
                                "模型准备失败：\n" +
                                    result.value("error", "unknown"));
                  });
      }),
      &window);
  return page;
}

GtkWidget *build_recognition(SettingsWindow &window) {
  GtkWidget *page =
      page_box("识别与音频",
               "选择麦克风、采样率和实时预览；设置写入统一 runtime JSON。");
  window.audio_device = GTK_COMBO_BOX_TEXT(gtk_combo_box_text_new());
  window.audio_rate =
      GTK_SPIN_BUTTON(gtk_spin_button_new_with_range(8000, 192000, 1000));
  window.minimum_recording =
      GTK_SPIN_BUTTON(gtk_spin_button_new_with_range(0, 5000, 100));
  window.streaming_enabled = GTK_CHECK_BUTTON(
      gtk_check_button_new_with_label("启用实时 preedit 预览"));
  window.normalization_enabled = GTK_CHECK_BUTTON(
      gtk_check_button_new_with_label("启用 ITN / 文本规范化"));
  window.fcitx_panel_style = GTK_COMBO_BOX_TEXT(gtk_combo_box_text_new());
  gtk_combo_box_text_append_text(window.fcitx_panel_style, "minimal（简洁）");
  gtk_combo_box_text_append_text(window.fcitx_panel_style, "animated（动画）");
  window.fcitx_block_composing = GTK_CHECK_BUTTON(
      gtk_check_button_new_with_label("Fcitx：存在未提交预编辑时禁止录音"));
  window.fcitx_strip_period = GTK_CHECK_BUTTON(
      gtk_check_button_new_with_label("Fcitx：提交时移除尾部句号"));
  window.rime_schema = GTK_ENTRY(gtk_entry_new());
  gtk_entry_set_placeholder_text(window.rime_schema,
                                 "例如 luna_pinyin 或 rime_ice");
  gtk_box_pack_start(GTK_BOX(page),
                     row("输入设备", GTK_WIDGET(window.audio_device)), false,
                     false, 0);
  gtk_box_pack_start(GTK_BOX(page),
                     row("采样率", GTK_WIDGET(window.audio_rate)), false, false,
                     0);
  gtk_box_pack_start(
      GTK_BOX(page),
      row("最短录音（ms）", GTK_WIDGET(window.minimum_recording)), false, false,
      0);
  gtk_box_pack_start(GTK_BOX(page), GTK_WIDGET(window.streaming_enabled), false,
                     false, 0);
  gtk_box_pack_start(GTK_BOX(page), GTK_WIDGET(window.normalization_enabled),
                     false, false, 0);
  gtk_box_pack_start(
      GTK_BOX(page),
      row("Fcitx 状态样式", GTK_WIDGET(window.fcitx_panel_style)), false, false,
      0);
  gtk_box_pack_start(GTK_BOX(page), GTK_WIDGET(window.fcitx_block_composing),
                     false, false, 0);
  gtk_box_pack_start(GTK_BOX(page), GTK_WIDGET(window.fcitx_strip_period),
                     false, false, 0);
  gtk_box_pack_start(GTK_BOX(page),
                     row("IBus Rime schema", GTK_WIDGET(window.rime_schema)),
                     false, false, 0);
  GtkWidget *actions = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 8);
  GtkWidget *refresh = gtk_button_new_with_label("刷新设备");
  GtkWidget *save = gtk_button_new_with_label("保存识别设置");
  gtk_box_pack_start(GTK_BOX(actions), refresh, false, false, 0);
  gtk_box_pack_start(GTK_BOX(actions), save, false, false, 0);
  gtk_box_pack_start(GTK_BOX(page), actions, false, false, 0);
  window.recognition_status = GTK_LABEL(label(""));
  gtk_box_pack_start(GTK_BOX(page), GTK_WIDGET(window.recognition_status),
                     false, false, 0);
  g_signal_connect_swapped(
      refresh, "clicked",
      G_CALLBACK(+[](SettingsWindow *self) { refresh_devices(*self); }),
      &window);
  g_signal_connect_swapped(
      save, "clicked", G_CALLBACK(+[](SettingsWindow *self) {
        try {
          save_config(*self);
          const int style_index =
              gtk_combo_box_get_active(GTK_COMBO_BOX(self->fcitx_panel_style));
          update_fcitx_config({
              {"PanelStyle", style_index == 1 ? "animated" : "minimal"},
              {"BlockWhenComposing",
               gtk_toggle_button_get_active(
                   GTK_TOGGLE_BUTTON(self->fcitx_block_composing))
                   ? "True"
                   : "False"},
              {"StripTrailingPeriodOnCommit",
               gtk_toggle_button_get_active(
                   GTK_TOGGLE_BUTTON(self->fcitx_strip_period))
                   ? "True"
                   : "False"},
          });
          std::string schema = gtk_entry_get_text(self->rime_schema);
          if (schema.empty())
            schema = "luna_pinyin";
          update_rime_schema(schema);
          (void)run_command({"fcitx5-remote", "-r"});
          set_label(self->recognition_status,
                    "✓ 已保存；Core 与 Fcitx 配置将在重载后生效");
        } catch (const std::exception &error) {
          set_label(self->recognition_status,
                    std::string("保存失败：") + error.what());
        }
      }),
      &window);
  return page;
}

GtkWidget *build_itn(SettingsWindow &window) {
  GtkWidget *page =
      page_box("ITN 预览", "直接调用 C++ core 的完整文本规范化实现。");
  gtk_box_pack_start(GTK_BOX(page), label("输入"), false, false, 0);
  gtk_box_pack_start(GTK_BOX(page), scrolled_text(&window.itn_input, 120), true,
                     true, 0);
  GtkWidget *button = gtk_button_new_with_label("生成预览");
  gtk_box_pack_start(GTK_BOX(page), button, false, false, 0);
  gtk_box_pack_start(GTK_BOX(page), label("结果"), false, false, 0);
  gtk_box_pack_start(GTK_BOX(page), scrolled_text(&window.itn_output, 120),
                     true, true, 0);
  g_signal_connect_swapped(
      button, "clicked", G_CALLBACK(+[](SettingsWindow *self) {
        try {
          if (!vocotype::desktop::ensure_native_core())
            throw std::runtime_error("Core 未启动");
          const Json result = vocotype::desktop::unix_json_request(
              vocotype::desktop::backend_socket_path(),
              {{"type", "normalize_text"},
               {"text", text_view_text(self->itn_input)}},
              5000);
          set_text(self->itn_output,
                   result.value("text", result.value("error", "失败")));
        } catch (const std::exception &error) {
          set_text(self->itn_output, std::string("错误：") + error.what());
        }
      }),
      &window);
  return page;
}

GtkWidget *build_terms(SettingsWindow &window) {
  GtkWidget *page = page_box(
      "术语库",
      "编辑 aliases、canonical、hotword 和 protect；保存前由 yaml-cpp 验证。");
  gtk_box_pack_start(GTK_BOX(page), scrolled_text(&window.terms, 360), true,
                     true, 0);
  GtkWidget *actions = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 8);
  GtkWidget *reload = gtk_button_new_with_label("重新载入");
  GtkWidget *save = gtk_button_new_with_label("验证并保存");
  gtk_box_pack_start(GTK_BOX(actions), reload, false, false, 0);
  gtk_box_pack_start(GTK_BOX(actions), save, false, false, 0);
  gtk_box_pack_start(GTK_BOX(page), actions, false, false, 0);
  window.terms_status = GTK_LABEL(label(""));
  gtk_box_pack_start(GTK_BOX(page), GTK_WIDGET(window.terms_status), false,
                     false, 0);
  g_signal_connect_swapped(
      reload, "clicked", G_CALLBACK(+[](SettingsWindow *self) {
        set_text(self->terms, file_text(vocotype::desktop::terms_path()));
        set_label(self->terms_status, "已重新载入");
      }),
      &window);
  g_signal_connect_swapped(
      save, "clicked", G_CALLBACK(+[](SettingsWindow *self) {
        try {
          const std::string content = text_view_text(self->terms);
          const YAML::Node root = YAML::Load(content);
          if (!root.IsMap())
            throw std::runtime_error("YAML 顶层必须是映射");
          write_text_atomic(vocotype::desktop::terms_path(), content);
          set_label(self->terms_status, "✓ 术语库已保存；下次识别自动热重载");
        } catch (const std::exception &error) {
          set_label(self->terms_status, std::string("未保存：") + error.what());
        }
      }),
      &window);
  return page;
}

GtkWidget *build_slm(SettingsWindow &window) {
  GtkWidget *page = page_box(
      "AI 润色与语音编辑",
      "配置 OpenAI-compatible 端点；请求由 C++ libcurl/SSE 客户端执行。");
  window.slm_enabled =
      GTK_CHECK_BUTTON(gtk_check_button_new_with_label("启用 Shift+F9 润色"));
  window.slm_endpoint = GTK_ENTRY(gtk_entry_new());
  window.slm_model = GTK_ENTRY(gtk_entry_new());
  window.slm_api_key = GTK_ENTRY(gtk_entry_new());
  gtk_entry_set_visibility(window.slm_api_key, false);
  window.slm_min_chars =
      GTK_SPIN_BUTTON(gtk_spin_button_new_with_range(0, 2000, 1));
  window.slm_timeout =
      GTK_SPIN_BUTTON(gtk_spin_button_new_with_range(1000, 180000, 1000));
  window.slm_streaming =
      GTK_CHECK_BUTTON(gtk_check_button_new_with_label("启用 SSE 增量输出"));
  window.slm_thinking = GTK_CHECK_BUTTON(
      gtk_check_button_new_with_label("允许模型思考（不会显示 reasoning）"));
  window.edit_enabled = GTK_CHECK_BUTTON(
      gtk_check_button_new_with_label("启用 Ctrl+F9 语音编辑"));
  gtk_box_pack_start(GTK_BOX(page), GTK_WIDGET(window.slm_enabled), false,
                     false, 0);
  gtk_box_pack_start(GTK_BOX(page),
                     row("Endpoint", GTK_WIDGET(window.slm_endpoint)), false,
                     false, 0);
  gtk_box_pack_start(GTK_BOX(page), row("Model", GTK_WIDGET(window.slm_model)),
                     false, false, 0);
  gtk_box_pack_start(GTK_BOX(page),
                     row("API Key", GTK_WIDGET(window.slm_api_key)), false,
                     false, 0);
  gtk_box_pack_start(GTK_BOX(page),
                     row("最短字符数", GTK_WIDGET(window.slm_min_chars)), false,
                     false, 0);
  gtk_box_pack_start(GTK_BOX(page),
                     row("超时（ms）", GTK_WIDGET(window.slm_timeout)), false,
                     false, 0);
  gtk_box_pack_start(GTK_BOX(page), GTK_WIDGET(window.slm_streaming), false,
                     false, 0);
  gtk_box_pack_start(GTK_BOX(page), GTK_WIDGET(window.slm_thinking), false,
                     false, 0);
  gtk_box_pack_start(GTK_BOX(page), GTK_WIDGET(window.edit_enabled), false,
                     false, 0);
  GtkWidget *save = gtk_button_new_with_label("保存 AI 设置");
  gtk_box_pack_start(GTK_BOX(page), save, false, false, 0);
  gtk_box_pack_start(GTK_BOX(page), label("测试文本"), false, false, 0);
  gtk_box_pack_start(GTK_BOX(page), scrolled_text(&window.slm_source, 90), true,
                     true, 0);
  GtkWidget *test = gtk_button_new_with_label("测试润色");
  gtk_box_pack_start(GTK_BOX(page), test, false, false, 0);
  gtk_box_pack_start(GTK_BOX(page), scrolled_text(&window.slm_result, 90), true,
                     true, 0);
  window.slm_status = GTK_LABEL(label(""));
  gtk_box_pack_start(GTK_BOX(page), GTK_WIDGET(window.slm_status), false, false,
                     0);
  g_signal_connect_swapped(
      save, "clicked", G_CALLBACK(+[](SettingsWindow *self) {
        try {
          save_config(*self);
          set_label(self->slm_status,
                    "✓ 已保存；点击概览中的重启 Core 立即应用");
        } catch (const std::exception &error) {
          set_label(self->slm_status, std::string("保存失败：") + error.what());
        }
      }),
      &window);
  g_signal_connect_swapped(
      test, "clicked", G_CALLBACK(+[](SettingsWindow *self) {
        set_label(self->slm_status, "正在请求…");
        const std::string source = text_view_text(self->slm_source);
        run_async(
            [source] {
              if (!vocotype::desktop::ensure_native_core())
                return Json{{"success", false}, {"error", "Core 未启动"}};
              return vocotype::desktop::unix_json_request(
                  vocotype::desktop::backend_socket_path(),
                  {{"type", "polish_text"}, {"text", source}}, 180000);
            },
            [self](Json result) {
              set_text(self->slm_result,
                       result.value("text", result.value("error", "失败")));
              set_label(self->slm_status, result.value("success", false)
                                              ? "✓ 请求成功"
                                              : "请求失败");
            });
      }),
      &window);
  return page;
}

GtkWidget *build_playground(SettingsWindow &window) {
  GtkWidget *page = page_box(
      "Playground", "原生 PortAudio 录音、实时波形、指定输出回放、ASR、AI "
                    "润色和语音编辑测试。");

  window.audio_output = GTK_COMBO_BOX_TEXT(gtk_combo_box_text_new());
  gtk_box_pack_start(GTK_BOX(page),
                     row("回放输出", GTK_WIDGET(window.audio_output)), false,
                     false, 0);

  GtkWidget *actions = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 8);
  GtkWidget *record = gtk_button_new_with_label("录制 3 秒");
  GtkWidget *play = gtk_button_new_with_label("回放上次录音");
  GtkWidget *transcribe = gtk_button_new_with_label("转录上次录音");
  gtk_box_pack_start(GTK_BOX(actions), record, false, false, 0);
  gtk_box_pack_start(GTK_BOX(actions), play, false, false, 0);
  gtk_box_pack_start(GTK_BOX(actions), transcribe, false, false, 0);
  gtk_box_pack_start(GTK_BOX(page), actions, false, false, 0);

  window.playground_status = GTK_LABEL(label("尚未录音"));
  gtk_box_pack_start(GTK_BOX(page), GTK_WIDGET(window.playground_status), false,
                     false, 0);
  window.playground_waveform = GTK_DRAWING_AREA(gtk_drawing_area_new());
  gtk_widget_set_size_request(GTK_WIDGET(window.playground_waveform), -1, 110);
  g_signal_connect(window.playground_waveform, "draw",
                   G_CALLBACK(draw_waveform), &window);
  gtk_box_pack_start(GTK_BOX(page), GTK_WIDGET(window.playground_waveform),
                     false, true, 0);
  gtk_box_pack_start(GTK_BOX(page), label("ASR 结果"), false, false, 0);
  gtk_box_pack_start(GTK_BOX(page),
                     scrolled_text(&window.playground_result, 100), false, true,
                     0);

  gtk_box_pack_start(GTK_BOX(page), label("语音编辑上下文"), false, false, 0);
  gtk_box_pack_start(GTK_BOX(page),
                     scrolled_text(&window.playground_edit_source, 100), false,
                     true, 0);
  set_text(window.playground_edit_source, "勾股定理是一项伟大的发明");
  window.playground_edit_instruction = GTK_ENTRY(gtk_entry_new());
  gtk_entry_set_text(window.playground_edit_instruction,
                     "把勾股定理翻译为英文");
  gtk_box_pack_start(
      GTK_BOX(page),
      row("请朗读编辑指令", GTK_WIDGET(window.playground_edit_instruction)),
      false, false, 0);
  GtkWidget *edit = gtk_button_new_with_label("录制 3 秒编辑指令并测试");
  gtk_box_pack_start(GTK_BOX(page), edit, false, false, 0);
  window.playground_edit_status = GTK_LABEL(label(""));
  gtk_box_pack_start(GTK_BOX(page), GTK_WIDGET(window.playground_edit_status),
                     false, false, 0);
  gtk_box_pack_start(GTK_BOX(page), label("编辑计划 / 结果"), false, false, 0);
  gtk_box_pack_start(GTK_BOX(page),
                     scrolled_text(&window.playground_edit_result, 120), false,
                     true, 0);

  g_signal_connect_swapped(
      record, "clicked", G_CALLBACK(+[](SettingsWindow *self) {
        self->waveform.clear();
        gtk_widget_queue_draw(GTK_WIDGET(self->playground_waveform));
        set_label(self->playground_status, "🎤 正在录制 3 秒…");
        run_async(
            [self] {
              return capture_recording(3000, [self](double minimum,
                                                    double maximum) {
                post_idle([self, minimum, maximum] {
                  self->waveform.emplace_back(minimum, maximum);
                  if (self->waveform.size() > 240)
                    self->waveform.erase(self->waveform.begin());
                  gtk_widget_queue_draw(GTK_WIDGET(self->playground_waveform));
                });
              });
            },
            [self](Json result) {
              if (result.value("success", false)) {
                if (!self->last_recording.empty())
                  std::filesystem::remove(self->last_recording);
                self->last_recording = result.value("path", "");
                set_label(self->playground_status,
                          "✓ 已录制：" + result.value("device", "") + "，" +
                              std::to_string(result.value("sample_rate", 0)) +
                              " Hz");
              } else {
                set_label(self->playground_status,
                          "录音失败：" + result.value("error", "unknown"));
              }
            });
      }),
      &window);

  g_signal_connect_swapped(
      play, "clicked", G_CALLBACK(+[](SettingsWindow *self) {
        if (self->last_recording.empty()) {
          set_label(self->playground_status, "请先录音");
          return;
        }
        const int active =
            gtk_combo_box_get_active(GTK_COMBO_BOX(self->audio_output));
        const int output_id =
            active >= 0 && static_cast<std::size_t>(active) <
                               self->output_devices.size()
                ? self->output_devices[static_cast<std::size_t>(active)].id
                : -1;
        const auto path = self->last_recording;
        set_label(self->playground_status, "🔊 正在回放…");
        run_async([path, output_id] { return play_recording(path, output_id); },
                  [self](Json result) {
                    set_label(self->playground_status,
                              result.value("success", false)
                                  ? "✓ 已回放到：" + result.value("device", "")
                                  : "回放失败：" +
                                        result.value("error", "unknown"));
                  });
      }),
      &window);

  g_signal_connect_swapped(
      transcribe, "clicked", G_CALLBACK(+[](SettingsWindow *self) {
        if (self->last_recording.empty()) {
          set_label(self->playground_status, "请先录音");
          return;
        }
        const auto path = self->last_recording;
        set_label(self->playground_status, "⏳ 正在识别…");
        run_async(
            [path] {
              if (!vocotype::desktop::ensure_native_core())
                return Json{{"success", false}, {"error", "Core 未启动"}};
              return vocotype::desktop::unix_json_request(
                  vocotype::desktop::backend_socket_path(),
                  {{"type", "transcribe"},
                   {"audio_path", path.string()},
                   {"long_mode", false}},
                  180000);
            },
            [self](Json result) {
              set_text(self->playground_result,
                       result.value("text", result.value("error", "失败")));
              set_label(self->playground_status, result.value("success", false)
                                                     ? "✓ 识别完成"
                                                     : "识别失败");
            });
      }),
      &window);

  g_signal_connect_swapped(
      edit, "clicked", G_CALLBACK(+[](SettingsWindow *self) {
        const std::string source = text_view_text(self->playground_edit_source);
        if (source.empty()) {
          set_label(self->playground_edit_status, "请输入编辑上下文");
          return;
        }
        const std::string spoken_hint =
            gtk_entry_get_text(self->playground_edit_instruction);
        self->waveform.clear();
        gtk_widget_queue_draw(GTK_WIDGET(self->playground_waveform));
        set_label(self->playground_edit_status,
                  "🎤 请朗读：" + spoken_hint + "（录制 3 秒）");
        run_async(
            [self, source] {
              Json recording = capture_recording(3000, [self](double minimum,
                                                              double maximum) {
                post_idle([self, minimum, maximum] {
                  self->waveform.emplace_back(minimum, maximum);
                  if (self->waveform.size() > 240)
                    self->waveform.erase(self->waveform.begin());
                  gtk_widget_queue_draw(GTK_WIDGET(self->playground_waveform));
                });
              });
              if (!recording.value("success", false))
                return recording;
              const std::filesystem::path audio = recording.value("path", "");
              try {
                if (!vocotype::desktop::ensure_native_core())
                  throw std::runtime_error("Core 未启动");
                const int cursor =
                    static_cast<int>(g_utf8_strlen(source.c_str(), -1));
                Json result = vocotype::desktop::unix_json_request(
                    vocotype::desktop::backend_socket_path(),
                    {{"type", "edit_audio"},
                     {"audio_path", audio.string()},
                     {"context_id", "settings-playground"},
                     {"replace_state", "supported"},
                     {"supports_surrounding", true},
                     {"snapshot",
                      {{"text", source},
                       {"cursor_pos", cursor},
                       {"anchor_pos", cursor},
                       {"selected_text", ""}}}},
                    180000);
                std::filesystem::remove(audio);
                return result;
              } catch (...) {
                std::filesystem::remove(audio);
                throw;
              }
            },
            [self, source](Json result) {
              if (!result.value("success", false)) {
                set_text(self->playground_edit_result,
                         result.value("error", "语音编辑失败"));
                set_label(self->playground_edit_status, "❌ 语音编辑失败");
                return;
              }
              const std::string mode = result.value("mode", "no_op");
              std::string output;
              if (mode == "replace")
                output = result.value("new_text", source);
              else if (mode == "commit_only")
                output = source + result.value("new_text", "");
              else
                output = result.dump(2);
              set_text(self->playground_edit_result, output);
              set_label(self->playground_edit_status,
                        "✓ 编辑计划已生成：" + mode);
            });
      }),
      &window);
  return page;
}

std::string doctor_report() {
  std::ostringstream report;
  report << "VoCoType Native Doctor\n\n";
  auto check = [&](const std::string &name, bool pass,
                   const std::string &details) {
    report << (pass ? "[PASS] " : "[FAIL] ") << name << " — " << details
           << "\n";
  };
  const std::string core = vocotype::desktop::find_executable(
      "vocotype-core",
      {vocotype::desktop::home_path() /
           ".local/lib/vocotype-streaming/bin/vocotype-core",
       "/usr/libexec/vocotype-core", "/usr/lib/vocotype/vocotype-core"});
  const std::string recorder = vocotype::desktop::find_executable(
      "vocotype-audio-recorder",
      {vocotype::desktop::home_path() /
           ".local/lib/vocotype-native/bin/vocotype-audio-recorder",
       "/usr/libexec/vocotype-audio-recorder"});
  check("C++ core", !core.empty(), core.empty() ? "未找到" : core);
  check("原生录音器", !recorder.empty(),
        recorder.empty() ? "未找到" : recorder);
  check("runtime config",
        std::filesystem::is_regular_file(
            vocotype::desktop::runtime_config_path()),
        vocotype::desktop::runtime_config_path().string());
  check("terms",
        std::filesystem::is_regular_file(vocotype::desktop::terms_path()),
        vocotype::desktop::terms_path().string());
  try {
    const auto devices = vocotype::desktop::list_input_devices();
    check("音频设备", !devices.empty(),
          std::to_string(devices.size()) + " 个输入设备");
  } catch (const std::exception &error) {
    check("音频设备", false, error.what());
  }
  check("native core socket", vocotype::desktop::native_core_ready(),
        vocotype::desktop::backend_socket_path());
  const Json integrity = verify_native_payload();
  check("native payload 完整性", integrity.value("success", false),
        integrity.value("success", false)
            ? "所有已安装 ELF/共享库 checksum 正确"
            : integrity.value("error", integrity.value("output", "校验失败")));
  bool python_runtime = false;
  DIR *directory = opendir("/proc");
  if (directory) {
    while (dirent *entry = readdir(directory)) {
      char *end = nullptr;
      const long pid = std::strtol(entry->d_name, &end, 10);
      if (!end || *end != '\0' || pid <= 1)
        continue;
      std::ifstream command(std::filesystem::path("/proc") / entry->d_name /
                            "cmdline");
      std::string bytes((std::istreambuf_iterator<char>(command)),
                        std::istreambuf_iterator<char>());
      if (bytes.find("vocotype") != std::string::npos &&
          bytes.find("python") != std::string::npos)
        python_runtime = true;
    }
    closedir(directory);
  }
  check("零 Python 运行时", !python_runtime,
        python_runtime ? "发现 VoCoType Python 进程"
                       : "未发现 VoCoType Python 进程");
  return report.str();
}

GtkWidget *build_feedback(SettingsWindow &window) {
  GtkWidget *page =
      page_box("反馈", "可发送到 VoCoType 私有反馈服务，或在 GitHub 创建公开 "
                       "Issue。诊断信息默认不附带。");
  gtk_box_pack_start(GTK_BOX(page), label("反馈内容（最多 10000 字）"), false,
                     false, 0);
  gtk_box_pack_start(GTK_BOX(page), scrolled_text(&window.feedback_view, 220),
                     true, true, 0);
  window.feedback_include_doctor =
      GTK_CHECK_BUTTON(gtk_check_button_new_with_label("附带 Doctor 结果"));
  window.feedback_include_bundle = GTK_CHECK_BUTTON(
      gtk_check_button_new_with_label("附带脱敏支持包（最大 5 MiB）"));
  gtk_box_pack_start(GTK_BOX(page), GTK_WIDGET(window.feedback_include_doctor),
                     false, false, 0);
  gtk_box_pack_start(GTK_BOX(page), GTK_WIDGET(window.feedback_include_bundle),
                     false, false, 0);
  window.feedback_endpoint = GTK_ENTRY(gtk_entry_new());
  gtk_entry_set_text(
      window.feedback_endpoint,
      "https://feedback.vocotype-linux.lsamc.website/v1/feedback");
  gtk_box_pack_start(GTK_BOX(page),
                     row("反馈端点", GTK_WIDGET(window.feedback_endpoint)),
                     false, false, 0);
  GtkWidget *actions = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 8);
  GtkWidget *send = gtk_button_new_with_label("发送给 VoCoType 维护者");
  GtkWidget *github = gtk_button_new_with_label("在 GitHub 创建公开 Issue");
  gtk_box_pack_start(GTK_BOX(actions), send, false, false, 0);
  gtk_box_pack_start(GTK_BOX(actions), github, false, false, 0);
  gtk_box_pack_start(GTK_BOX(page), actions, false, false, 0);
  window.feedback_status = GTK_LABEL(label(""));
  gtk_label_set_selectable(window.feedback_status, true);
  gtk_box_pack_start(GTK_BOX(page), GTK_WIDGET(window.feedback_status), false,
                     false, 0);

  g_signal_connect_swapped(
      send, "clicked", G_CALLBACK(+[](SettingsWindow *self) {
        const std::string message = text_view_text(self->feedback_view);
        const std::string endpoint =
            gtk_entry_get_text(self->feedback_endpoint);
        const bool include_doctor = gtk_toggle_button_get_active(
            GTK_TOGGLE_BUTTON(self->feedback_include_doctor));
        const bool include_bundle = gtk_toggle_button_get_active(
            GTK_TOGGLE_BUTTON(self->feedback_include_bundle));
        if (message.empty()) {
          set_label(self->feedback_status, "反馈内容不能为空");
          return;
        }
        set_label(self->feedback_status, "正在发送反馈…");
        run_async(
            [message, endpoint, include_doctor, include_bundle] {
              const std::string report = include_doctor ? doctor_report() : "";
              std::filesystem::path bundle;
              if (include_bundle) {
                const Json generated = create_support_bundle(doctor_report());
                if (!generated.value("success", false))
                  return generated;
                bundle = generated.value("path", "");
              }
              Json result = submit_feedback(endpoint, message, report, bundle);
              if (!bundle.empty())
                result["bundle"] = bundle.string();
              return result;
            },
            [self](Json result) {
              set_label(
                  self->feedback_status,
                  result.value("success", false)
                      ? "✓ 反馈已发送" + (result.value("bundle", "").empty()
                                              ? ""
                                              : "；支持包保留于 " +
                                                    result.value("bundle", ""))
                      : "反馈发送失败：" + result.value("error", "unknown"));
            });
      }),
      &window);

  g_signal_connect_swapped(
      github, "clicked", G_CALLBACK(+[](SettingsWindow *self) {
        const std::string message = text_view_text(self->feedback_view);
        if (message.empty()) {
          set_label(self->feedback_status, "反馈内容不能为空");
          return;
        }
        std::string body = message;
        if (gtk_toggle_button_get_active(
                GTK_TOGGLE_BUTTON(self->feedback_include_doctor)))
          body +=
              "\n\n<details><summary>VoCoType Doctor</summary>\n\n```text\n" +
              doctor_report() + "\n```\n</details>";
        const std::string uri = "https://github.com/LeonardNJU/VocoType-linux/"
                                "issues/new?labels=feedback&title=" +
                                uri_escape("[Feedback] ") +
                                "&body=" + uri_escape(body);
        std::string error;
        if (!open_uri(GTK_WINDOW(self->window), uri, error))
          set_label(self->feedback_status, "打开 GitHub 失败：" + error);
      }),
      &window);
  return page;
}

GtkWidget *build_doctor(SettingsWindow &window) {
  GtkWidget *page = page_box(
      "Doctor 与支持",
      "检查原生运行时、查询版本、生成脱敏支持包，并创建 GitHub 反馈。");
  GtkWidget *actions = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 8);
  GtkWidget *run = gtk_button_new_with_label("运行检查");
  GtkWidget *latest = gtk_button_new_with_label("查询最新版本");
  GtkWidget *bundle = gtk_button_new_with_label("生成支持包");
  GtkWidget *open_support = gtk_button_new_with_label("打开支持目录");
  GtkWidget *github = gtk_button_new_with_label("在 GitHub 创建 Issue");
  for (GtkWidget *button : {run, latest, bundle, open_support, github})
    gtk_box_pack_start(GTK_BOX(actions), button, false, false, 0);
  gtk_box_pack_start(GTK_BOX(page), actions, false, false, 0);

  window.version_status =
      GTK_LABEL(label((std::string("当前版本：") + VOCOTYPE_VERSION).c_str()));
  gtk_box_pack_start(GTK_BOX(page), GTK_WIDGET(window.version_status), false,
                     false, 0);
  window.support_status = GTK_LABEL(label("尚未生成支持包"));
  gtk_label_set_selectable(window.support_status, true);
  gtk_box_pack_start(GTK_BOX(page), GTK_WIDGET(window.support_status), false,
                     false, 0);
  gtk_box_pack_start(GTK_BOX(page), scrolled_text(&window.doctor_output, 400),
                     true, true, 0);
  gtk_text_view_set_editable(window.doctor_output, false);
  gtk_text_view_set_monospace(window.doctor_output, true);

  g_signal_connect_swapped(run, "clicked",
                           G_CALLBACK(+[](SettingsWindow *self) {
                             set_text(self->doctor_output, doctor_report());
                           }),
                           &window);
  g_signal_connect_swapped(
      latest, "clicked", G_CALLBACK(+[](SettingsWindow *self) {
        set_label(self->version_status, "正在查询 GitHub release…");
        run_async(
            [] { return query_latest_release(); },
            [self](Json result) {
              if (!result.value("success", false)) {
                set_label(self->version_status,
                          "版本查询失败：" + result.value("error", "unknown"));
                return;
              }
              const std::string latest_tag = result.value("latest", "unknown");
              set_label(self->version_status,
                        "当前版本：" VOCOTYPE_VERSION "；最新 release：" +
                            latest_tag +
                            (result.value("published_at", "").empty()
                                 ? ""
                                 : "；发布时间：" +
                                       result.value("published_at", "")));
            });
      }),
      &window);
  g_signal_connect_swapped(
      bundle, "clicked", G_CALLBACK(+[](SettingsWindow *self) {
        const std::string report = doctor_report();
        set_text(self->doctor_output, report);
        set_label(self->support_status, "正在生成脱敏支持包…");
        run_async([report] { return create_support_bundle(report); },
                  [self](Json result) {
                    set_label(self->support_status,
                              result.value("success", false)
                                  ? "✓ 支持包：" + result.value("path", "")
                                  : "支持包生成失败：" +
                                        result.value("error", "unknown"));
                  });
      }),
      &window);
  g_signal_connect_swapped(
      open_support, "clicked", G_CALLBACK(+[](SettingsWindow *self) {
        std::string error;
        const std::string uri =
            std::string("file://") + support_directory().string();
        if (!open_uri(GTK_WINDOW(self->window), uri, error))
          set_label(self->support_status, "打开目录失败：" + error);
      }),
      &window);
  g_signal_connect_swapped(
      github, "clicked", G_CALLBACK(+[](SettingsWindow *self) {
        const std::string report = doctor_report();
        set_text(self->doctor_output, report);
        const std::string title = "[Bug] VoCoType " VOCOTYPE_VERSION;
        const std::string body =
            "请描述问题、复现步骤和预期行为。\n\n```text\n" + report +
            "\n```\n";
        const std::string uri =
            "https://github.com/LeonardNJU/VocoType-linux/issues/new?title=" +
            uri_escape(title) + "&body=" + uri_escape(body);
        std::string error;
        if (!open_uri(GTK_WINDOW(self->window), uri, error))
          set_label(self->support_status, "打开 GitHub 失败：" + error);
      }),
      &window);
  return page;
}

void activate(GtkApplication *application, gpointer user_data) {
  auto *window = static_cast<SettingsWindow *>(user_data);
  window->application = application;
  window->window = gtk_application_window_new(application);
  gtk_window_set_title(GTK_WINDOW(window->window), "VoCoType 设置");
  gtk_window_set_default_size(GTK_WINDOW(window->window), 900, 720);
  GtkWidget *notebook = gtk_notebook_new();
  gtk_notebook_set_tab_pos(GTK_NOTEBOOK(notebook), GTK_POS_LEFT);
  gtk_container_add(GTK_CONTAINER(window->window), notebook);
  const std::vector<std::pair<const char *, GtkWidget *>> pages = {
      {"概览", build_overview(*window)},
      {"识别", build_recognition(*window)},
      {"ITN", build_itn(*window)},
      {"术语", build_terms(*window)},
      {"AI", build_slm(*window)},
      {"Playground", build_playground(*window)},
      {"反馈", build_feedback(*window)},
      {"Doctor", build_doctor(*window)},
  };
  for (const auto &[name, page] : pages)
    gtk_notebook_append_page(GTK_NOTEBOOK(notebook), page, gtk_label_new(name));
  populate_from_config(*window);
  gtk_widget_show_all(window->window);
}

} // namespace

int main(int argc, char **argv) {
  GtkApplication *application = gtk_application_new(
      "io.github.LeonardNJU.VoCoType.Settings", G_APPLICATION_DEFAULT_FLAGS);
  auto window = std::make_unique<SettingsWindow>();
  g_signal_connect(application, "activate", G_CALLBACK(activate), window.get());
  const int status = g_application_run(G_APPLICATION(application), argc, argv);
  if (!window->last_recording.empty())
    std::filesystem::remove(window->last_recording);
  g_object_unref(application);
  return status;
}
