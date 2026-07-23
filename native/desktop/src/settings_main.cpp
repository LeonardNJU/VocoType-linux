#include "vocotype/desktop/audio.hpp"
#include "vocotype/desktop/config.hpp"
#include "vocotype/desktop/ipc.hpp"
#include "vocotype/desktop/settings_ui.hpp"
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
#include <iostream>
#include <map>
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
namespace sui = vocotype::desktop::settings_ui;

#ifndef VOCOTYPE_VERSION
#define VOCOTYPE_VERSION "development"
#endif

namespace {

struct SettingsWindow {
  GtkApplication *application = nullptr;
  GtkWidget *window = nullptr;
  GtkStack *stack = nullptr;
  GtkStack *lifecycle_stack = nullptr;
  GtkStack *tutorial_stack = nullptr;

  GtkLabel *overview_status = nullptr;
  GtkLabel *install_environment_status = nullptr;
  GtkLabel *ibus_choice_status = nullptr;
  GtkLabel *fcitx_choice_status = nullptr;
  GtkLabel *ibus_install_status = nullptr;
  GtkLabel *fcitx_install_status = nullptr;
  GtkLabel *overview_summary = nullptr;
  GtkRadioButton *ibus_framework_radio = nullptr;
  GtkRadioButton *fcitx_framework_radio = nullptr;

  GtkLabel *recognition_status = nullptr;
  GtkLabel *panel_style_status = nullptr;
  GtkComboBoxText *audio_device = nullptr;
  GtkSpinButton *audio_rate = nullptr;
  GtkSpinButton *minimum_recording = nullptr;
  GtkSwitch *streaming_enabled = nullptr;
  GtkSwitch *normalization_enabled = nullptr;
  GtkSwitch *compact_dates = nullptr;
  GtkSwitch *compact_times = nullptr;
  GtkSwitch *compact_distances = nullptr;
  GtkSwitch *currency_symbols = nullptr;
  GtkEntry *itn_preview_input = nullptr;
  GtkLabel *itn_preview_output = nullptr;
  GtkComboBoxText *fcitx_panel_style = nullptr;
  GtkSwitch *fcitx_block_composing = nullptr;
  GtkSwitch *fcitx_strip_period = nullptr;
  GtkComboBoxText *rime_schema = nullptr;
  GtkWidget *rime_resource_row = nullptr;
  GtkWidget *rime_schema_row = nullptr;
  GtkWidget *fcitx_composing_row = nullptr;
  GtkWidget *fcitx_panel_section = nullptr;
  GtkWidget *fcitx_panel_card = nullptr;
  GtkWidget *fcitx_output_section = nullptr;
  GtkWidget *fcitx_output_card = nullptr;

  GtkTextView *terms = nullptr;
  GtkLabel *terms_status = nullptr;

  GtkSwitch *slm_enabled = nullptr;
  GtkEntry *slm_endpoint = nullptr;
  GtkEntry *slm_model = nullptr;
  GtkEntry *slm_api_key_env = nullptr;
  GtkEntry *slm_api_key = nullptr;
  GtkCheckButton *slm_clear_api_key = nullptr;
  GtkSpinButton *slm_min_chars = nullptr;
  GtkSpinButton *slm_timeout = nullptr;
  GtkSwitch *slm_streaming = nullptr;
  GtkSwitch *slm_thinking = nullptr;
  GtkSwitch *edit_enabled = nullptr;
  GtkLabel *slm_status = nullptr;

  GtkComboBoxText *playground_audio_device = nullptr;
  GtkSpinButton *playground_audio_rate = nullptr;
  GtkComboBoxText *audio_output = nullptr;
  GtkLabel *playground_status = nullptr;
  GtkDrawingArea *playground_waveform = nullptr;
  GtkTextView *playground_result = nullptr;
  GtkWidget *playground_ai_controls = nullptr;
  GtkLabel *playground_ai_gate_status = nullptr;
  GtkTextView *playground_polish_source = nullptr;
  GtkTextView *playground_polish_result = nullptr;
  GtkLabel *playground_polish_status = nullptr;
  GtkTextView *playground_edit_source = nullptr;
  GtkEntry *playground_edit_instruction = nullptr;
  GtkTextView *playground_edit_result = nullptr;
  GtkLabel *playground_edit_status = nullptr;

  GtkLabel *version_status = nullptr;
  GtkLabel *support_status = nullptr;
  GtkBox *doctor_list = nullptr;
  GtkLabel *doctor_summary = nullptr;
  GtkTextView *doctor_output = nullptr;
  GtkLabel *tutorial_title = nullptr;

  GtkComboBoxText *feedback_category = nullptr;
  GtkEntry *feedback_contact = nullptr;
  GtkTextView *feedback_view = nullptr;
  GtkEntry *feedback_endpoint = nullptr;
  GtkCheckButton *feedback_custom_endpoint = nullptr;
  GtkCheckButton *feedback_include_doctor = nullptr;
  GtkCheckButton *feedback_include_bundle = nullptr;
  GtkLabel *feedback_status = nullptr;

  std::vector<vocotype::desktop::AudioDevice> devices;
  std::vector<vocotype::desktop::AudioOutputDevice> output_devices;
  std::vector<std::pair<double, double>> waveform;
  std::filesystem::path last_recording;
  Json config = Json::object();
};

std::filesystem::path fcitx_config_path();
std::string config_value(const std::filesystem::path &path,
                         const std::string &key, const std::string &fallback);
void update_fcitx_config(
    const std::vector<std::pair<std::string, std::string>> &values);
std::string yaml_scalar_value(const std::filesystem::path &path,
                              const std::string &key,
                              const std::string &fallback);
void update_rime_schema(const std::string &schema);
std::string selected_framework(const SettingsWindow &window);
std::string doctor_report();

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
  if (!config.contains("ui") || !config["ui"].is_object())
    config["ui"] = Json::object();
  config["asr"]["native_enabled"] = true;
  return config;
}

bool json_bool(const Json &object, const char *key, bool fallback) {
  if (!object.is_object())
    return fallback;
  const auto iterator = object.find(key);
  if (iterator == object.end() || iterator->is_null())
    return fallback;
  if (iterator->is_boolean())
    return iterator->get<bool>();
  if (iterator->is_number_integer() || iterator->is_number_unsigned())
    return iterator->get<long long>() != 0;
  if (iterator->is_string()) {
    std::string value = iterator->get<std::string>();
    std::transform(value.begin(), value.end(), value.begin(),
                   [](unsigned char character) {
                     return static_cast<char>(std::tolower(character));
                   });
    if (value == "true" || value == "1" || value == "yes" || value == "on")
      return true;
    if (value == "false" || value == "0" || value == "no" || value == "off")
      return false;
  }
  return fallback;
}

std::vector<std::pair<std::string, std::string>> discover_rime_schemas() {
  std::map<std::string, std::string> schemas;
  const std::vector<std::filesystem::path> roots = {
      "/usr/share/rime-data",
      "/usr/local/share/rime-data",
      vocotype::desktop::home_path() / ".local/share/rime-data",
      vocotype::desktop::config_dir() / "rime",
  };
  for (const auto &root : roots) {
    std::error_code error;
    if (!std::filesystem::is_directory(root, error))
      continue;
    for (std::filesystem::recursive_directory_iterator iterator(
             root, std::filesystem::directory_options::skip_permission_denied,
             error),
         end;
         iterator != end; iterator.increment(error)) {
      if (error) {
        error.clear();
        continue;
      }
      if (!iterator->is_regular_file(error))
        continue;
      const std::string filename = iterator->path().filename().string();
      constexpr std::string_view suffix = ".schema.yaml";
      if (filename.size() <= suffix.size() ||
          filename.compare(filename.size() - suffix.size(), suffix.size(),
                           suffix) != 0)
        continue;
      std::string id = filename.substr(0, filename.size() - suffix.size());
      std::string name = id;
      try {
        const YAML::Node document = YAML::LoadFile(iterator->path().string());
        if (document["schema"] && document["schema"]["schema_id"])
          id = document["schema"]["schema_id"].as<std::string>();
        if (document["schema"] && document["schema"]["name"])
          name = document["schema"]["name"].as<std::string>();
      } catch (const std::exception &) {
      }
      if (!id.empty())
        schemas[id] = name.empty() ? id : name;
    }
  }
  if (schemas.empty()) {
    schemas.emplace("luna_pinyin", "朙月拼音");
    schemas.emplace("rime_ice", "雾凇拼音");
  }
  std::vector<std::pair<std::string, std::string>> result;
  result.reserve(schemas.size());
  for (const auto &[id, name] : schemas)
    result.emplace_back(id, name == id ? id : name + "（" + id + "）");
  return result;
}

void populate_rime_schema_combo(SettingsWindow &window,
                                const std::string &selected) {
  gtk_combo_box_text_remove_all(window.rime_schema);
  bool found = false;
  for (const auto &[id, label] : discover_rime_schemas()) {
    gtk_combo_box_text_append(window.rime_schema, id.c_str(), label.c_str());
    if (id == selected)
      found = true;
  }
  if (!selected.empty() && !found)
    gtk_combo_box_text_append(window.rime_schema, selected.c_str(),
                              (selected + "（当前配置）").c_str());
  const std::string preferred = selected.empty() ? "luna_pinyin" : selected;
  if (!gtk_combo_box_set_active_id(GTK_COMBO_BOX(window.rime_schema),
                                   preferred.c_str()))
    gtk_combo_box_set_active(GTK_COMBO_BOX(window.rime_schema), 0);
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
      gtk_switch_get_active(window.streaming_enabled);
  auto &normalization = window.config["normalization"];
  normalization["enabled"] =
      gtk_switch_get_active(window.normalization_enabled);
  normalization["compact_dates"] = gtk_switch_get_active(window.compact_dates);
  normalization["compact_times"] = gtk_switch_get_active(window.compact_times);
  normalization["compact_distances"] =
      gtk_switch_get_active(window.compact_distances);
  normalization["currency_symbols"] =
      gtk_switch_get_active(window.currency_symbols);

  auto &slm = window.config["slm"];
  slm["enabled"] = gtk_switch_get_active(window.slm_enabled);
  slm["endpoint"] = gtk_entry_get_text(window.slm_endpoint);
  slm["model"] = gtk_entry_get_text(window.slm_model);
  slm["api_key_env"] = gtk_entry_get_text(window.slm_api_key_env);
  const std::string api_key = gtk_entry_get_text(window.slm_api_key);
  if (gtk_toggle_button_get_active(
          GTK_TOGGLE_BUTTON(window.slm_clear_api_key))) {
    slm["api_key"] = "";
  } else if (!api_key.empty() && api_key != "••••••••") {
    slm["api_key"] = api_key;
  }
  slm["min_chars"] = gtk_spin_button_get_value_as_int(window.slm_min_chars);
  slm["timeout_ms"] = gtk_spin_button_get_value_as_int(window.slm_timeout);
  slm["remote_stream"] = gtk_switch_get_active(window.slm_streaming);
  slm["enable_thinking"] = gtk_switch_get_active(window.slm_thinking);
  slm["edit_enabled"] = gtk_switch_get_active(window.edit_enabled);
  slm["edit_max_tokens"] = std::max(1024, slm.value("edit_max_tokens", 1024));

  const int style_index =
      gtk_combo_box_get_active(GTK_COMBO_BOX(window.fcitx_panel_style));
  window.config["ui"]["lifecycle_framework"] =
      window.fcitx_framework_radio &&
              gtk_toggle_button_get_active(
                  GTK_TOGGLE_BUTTON(window.fcitx_framework_radio))
          ? "fcitx5"
          : "ibus";

  const auto directory = vocotype::desktop::config_dir();
  vocotype::desktop::write_json_file_atomic(directory / "fcitx5-backend.json",
                                            window.config);
  vocotype::desktop::write_json_file_atomic(directory / "ibus.json",
                                            window.config);

  update_fcitx_config({
      {"PanelStyle", style_index == 1 ? "animated" : "minimal"},
      {"BlockWhenComposing",
       gtk_switch_get_active(window.fcitx_block_composing) ? "True" : "False"},
      {"StripTrailingPeriodOnCommit",
       gtk_switch_get_active(window.fcitx_strip_period) ? "True" : "False"},
  });
  if (selected_framework(window) == "ibus") {
    const char *active =
        gtk_combo_box_get_active_id(GTK_COMBO_BOX(window.rime_schema));
    update_rime_schema(active && *active ? active : "luna_pinyin");
  }
}

void refresh_devices(SettingsWindow &window) {
  gtk_combo_box_text_remove_all(window.audio_device);
  if (window.playground_audio_device)
    gtk_combo_box_text_remove_all(window.playground_audio_device);
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
    if (window.playground_audio_device)
      gtk_combo_box_text_append_text(window.playground_audio_device,
                                     display.c_str());
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
  if (window.playground_audio_device)
    gtk_combo_box_set_active(GTK_COMBO_BOX(window.playground_audio_device),
                             selected);

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

  const std::string status =
      window.devices.empty()
          ? "未发现输入设备"
          : "已发现 " + std::to_string(window.devices.size()) +
                " 个输入设备、" + std::to_string(window.output_devices.size()) +
                " 个输出设备";
  set_label(window.recognition_status, status);
  if (window.playground_status && window.last_recording.empty())
    set_label(window.playground_status, status);
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

bool restart_core_for_settings() {
  const std::string socket = vocotype::desktop::backend_socket_path();
  if (vocotype::desktop::native_core_service_available())
    return vocotype::desktop::start_native_core_service(true, socket, 45000);
  (void)terminate_owned_core();
  std::this_thread::sleep_for(std::chrono::milliseconds(250));
  return vocotype::desktop::ensure_native_core(
      socket, vocotype::desktop::runtime_config_path(), 45000);
}

bool framework_installed(const std::string &framework) {
  const auto home = vocotype::desktop::home_path();
  if (framework == "ibus") {
    return std::filesystem::is_regular_file(
               home / ".local/share/ibus/component/vocotype.xml") &&
           std::filesystem::is_regular_file(
               home / ".local/libexec/ibus-engine-vocotype");
  }
  return (std::filesystem::is_regular_file(home /
                                           ".local/lib/fcitx5/vocotype.so") ||
          std::filesystem::is_regular_file("/usr/lib/fcitx5/vocotype.so") ||
          std::filesystem::is_regular_file("/usr/lib64/fcitx5/vocotype.so"));
}

std::string selected_framework(const SettingsWindow &window) {
  return window.fcitx_framework_radio &&
                 gtk_toggle_button_get_active(
                     GTK_TOGGLE_BUTTON(window.fcitx_framework_radio))
             ? "fcitx5"
             : "ibus";
}

void apply_framework_selection(SettingsWindow &window,
                               const std::string &framework) {
  const char *name = framework == "ibus" ? "ibus" : "fcitx5";
  if (window.lifecycle_stack)
    gtk_stack_set_visible_child_name(window.lifecycle_stack, name);
  if (window.tutorial_stack)
    gtk_stack_set_visible_child_name(window.tutorial_stack, name);
  if (window.tutorial_title) {
    set_label(window.tutorial_title, framework == "ibus"
                                         ? "当前教程：IBus（独立输入法引擎）"
                                         : "当前教程：Fcitx 5（全局 Module）");
  }
  const bool ibus = framework == "ibus";
  if (window.rime_resource_row)
    gtk_widget_set_visible(window.rime_resource_row, ibus);
  if (window.rime_schema_row)
    gtk_widget_set_visible(window.rime_schema_row, ibus);
  if (window.fcitx_composing_row)
    gtk_widget_set_visible(window.fcitx_composing_row, !ibus);
  for (GtkWidget *widget :
       {window.fcitx_panel_section, window.fcitx_panel_card,
        window.fcitx_output_section, window.fcitx_output_card}) {
    if (widget)
      gtk_widget_set_visible(widget, !ibus);
  }
}

void refresh_overview(SettingsWindow &window) {
  const bool core_ready = vocotype::desktop::native_core_ready();
  const bool ibus_installed = framework_installed("ibus");
  const bool fcitx_installed = framework_installed("fcitx5");
  const bool package_present =
      std::filesystem::is_regular_file("/usr/share/vocotype/.system-package");
  const std::string core = vocotype::desktop::find_executable(
      "vocotype-core",
      {vocotype::desktop::home_path() /
           ".local/lib/vocotype-streaming/bin/vocotype-core",
       "/usr/libexec/vocotype-core", "/usr/lib/vocotype/vocotype-core",
       "/usr/lib64/vocotype/vocotype-core"});
  const std::string recorder = vocotype::desktop::find_executable(
      "vocotype-audio-recorder",
      {vocotype::desktop::home_path() /
           ".local/lib/vocotype-native/bin/vocotype-audio-recorder",
       "/usr/libexec/vocotype-audio-recorder",
       "/usr/lib/vocotype/vocotype-audio-recorder"});

  std::ostringstream environment;
  environment << (package_present ? "✅ 原生软件包已安装"
                                  : "ℹ️ 当前使用源码 / 用户级安装")
              << "\n";
  environment << (!core.empty() ? "✅ C++ Core：" + core : "❌ 未找到 C++ Core")
              << "\n";
  environment << (!recorder.empty() ? "✅ 原生录音器：" + recorder
                                    : "❌ 未找到原生录音器")
              << "\n";
  environment << (core_ready ? "✅ Core socket 已就绪"
                             : "⚠️ Core 尚未启动；首次 F9 时会按需启动");

  if (window.install_environment_status)
    set_label(window.install_environment_status, environment.str());
  if (window.ibus_choice_status)
    set_label(window.ibus_choice_status, ibus_installed
                                             ? "✅ 已为当前用户安装"
                                             : "○ 尚未安装，可选择后执行安装");
  if (window.fcitx_choice_status)
    set_label(window.fcitx_choice_status, fcitx_installed
                                              ? "✅ 已安装全局 Module"
                                              : "○ 尚未安装，可选择后执行安装");
  if (window.ibus_install_status)
    set_label(window.ibus_install_status,
              ibus_installed
                  ? "✅ VoCoType（IBus）安装完整；可在系统输入源中添加。"
                  : "○ 尚未安装 VoCoType（IBus）。");
  if (window.fcitx_install_status)
    set_label(window.fcitx_install_status,
              fcitx_installed
                  ? "✅ VoCoType（Fcitx 5）Module 已安装；无需添加输入源。"
                  : "○ 尚未安装 VoCoType（Fcitx 5）。");

  std::ostringstream summary;
  summary << "Core：" << (core_ready ? "运行中" : "按需启动") << "；IBus："
          << (ibus_installed ? "已安装" : "未安装") << "；Fcitx 5："
          << (fcitx_installed ? "已安装" : "未安装");
  if (window.overview_summary)
    set_label(window.overview_summary, summary.str());
  if (window.overview_status)
    set_label(window.overview_status, environment.str() + "\n" + summary.str());
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

Json submit_feedback(const std::string &endpoint, const std::string &category,
                     const std::string &contact, const std::string &message,
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
               {"category", category.empty() ? "other" : category},
               {"message", message},
               {"installation_id", installation_id()},
               {"doctor", doctor.empty()
                              ? Json(nullptr)
                              : Json::array({{{"check_id", "native_doctor"},
                                              {"status", "info"},
                                              {"title", "Native Doctor"},
                                              {"details", doctor}}})},
               {"contact", contact}};
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
                            audio.value("sample_rate", 44100));
  gtk_spin_button_set_value(window.minimum_recording,
                            audio.value("min_recording_ms", 1000));
  if (window.playground_audio_rate)
    gtk_spin_button_set_value(window.playground_audio_rate,
                              audio.value("sample_rate", 44100));

  gtk_switch_set_active(
      window.streaming_enabled,
      json_bool(window.config["asr_streaming"], "enabled", true));
  const auto &normalization = window.config["normalization"];
  gtk_switch_set_active(window.normalization_enabled,
                        json_bool(normalization, "enabled", true));
  gtk_switch_set_active(window.compact_dates,
                        json_bool(normalization, "compact_dates", true));
  gtk_switch_set_active(window.compact_times,
                        json_bool(normalization, "compact_times", true));
  gtk_switch_set_active(window.compact_distances,
                        json_bool(normalization, "compact_distances", true));
  gtk_switch_set_active(window.currency_symbols,
                        json_bool(normalization, "currency_symbols", true));

  const auto &slm = window.config["slm"];
  gtk_switch_set_active(window.slm_enabled, json_bool(slm, "enabled", false));
  gtk_entry_set_text(
      window.slm_endpoint,
      slm.value("endpoint", "http://127.0.0.1:18080/v1/chat/completions")
          .c_str());
  gtk_entry_set_text(window.slm_model,
                     slm.value("model", "Qwen/Qwen3.5-0.8B").c_str());
  gtk_entry_set_text(window.slm_api_key_env,
                     slm.value("api_key_env", "").c_str());
  gtk_entry_set_text(window.slm_api_key,
                     slm.value("api_key", "").empty() ? "" : "••••••••");
  gtk_spin_button_set_value(window.slm_min_chars, slm.value("min_chars", 8));
  gtk_spin_button_set_value(window.slm_timeout, slm.value("timeout_ms", 20000));
  gtk_switch_set_active(window.slm_streaming,
                        json_bool(slm, "remote_stream", true));
  gtk_switch_set_active(window.slm_thinking,
                        json_bool(slm, "enable_thinking", false));
  gtk_switch_set_active(window.edit_enabled,
                        json_bool(slm, "edit_enabled", true));

  const std::string style =
      config_value(fcitx_config_path(), "PanelStyle", "minimal");
  gtk_combo_box_set_active(GTK_COMBO_BOX(window.fcitx_panel_style),
                           style == "animated" ? 1 : 0);
  gtk_switch_set_active(window.fcitx_block_composing,
                        config_value(fcitx_config_path(), "BlockWhenComposing",
                                     "True") != "False");
  gtk_switch_set_active(window.fcitx_strip_period,
                        config_value(fcitx_config_path(),
                                     "StripTrailingPeriodOnCommit",
                                     "False") == "True");
  populate_rime_schema_combo(
      window,
      yaml_scalar_value(vocotype::desktop::config_dir() / "rime/user.yaml",
                        "previously_selected_schema", "luna_pinyin"));

  const std::string framework =
      window.config["ui"].value("lifecycle_framework", "fcitx5");
  if (framework == "ibus")
    gtk_toggle_button_set_active(GTK_TOGGLE_BUTTON(window.ibus_framework_radio),
                                 TRUE);
  else
    gtk_toggle_button_set_active(
        GTK_TOGGLE_BUTTON(window.fcitx_framework_radio), TRUE);
  if (window.lifecycle_stack)
    gtk_stack_set_visible_child_name(window.lifecycle_stack,
                                     framework == "ibus" ? "ibus" : "fcitx5");
  if (window.tutorial_stack)
    gtk_stack_set_visible_child_name(window.tutorial_stack,
                                     framework == "ibus" ? "ibus" : "fcitx5");

  refresh_devices(window);
  std::string terms = file_text(vocotype::desktop::terms_path());
  if (terms.empty())
    terms =
        "# VoCoType 统一术语库\nterms:\n  - canonical: Ghostty\n    "
        "aliases: [鬼斯提, 格斯提]\n    hotword: true\n    protect: true\n\n"
        "protect:\n  - 三体问题\n  - 一加手机\n";
  set_text(window.terms, terms);
  refresh_overview(window);
}

GtkWidget *build_overview(SettingsWindow &window) {
  const auto page = sui::make_page(
      "概览与安装",
      "从这里完成首次安装、升级或修复。配置、术语和模型缓存会被保留。");
  GtkWidget *install_card = sui::make_card();
  GtkWidget *summary = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 22);
  gtk_container_set_border_width(GTK_CONTAINER(summary), 14);
  gtk_widget_set_hexpand(summary, TRUE);

  GtkWidget *environment_box = gtk_box_new(GTK_ORIENTATION_VERTICAL, 8);
  gtk_widget_set_hexpand(environment_box, TRUE);
  GtkWidget *environment_header = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 8);
  GtkWidget *environment_title = gtk_label_new("安装环境检查");
  gtk_label_set_xalign(GTK_LABEL(environment_title), 0.0F);
  gtk_widget_set_hexpand(environment_title, TRUE);
  sui::add_class(environment_title, "row-title");
  GtkWidget *refresh = gtk_button_new_with_label("刷新状态");
  gtk_box_pack_start(GTK_BOX(environment_header), environment_title, TRUE, TRUE,
                     0);
  gtk_box_pack_end(GTK_BOX(environment_header), refresh, FALSE, FALSE, 0);
  window.install_environment_status =
      GTK_LABEL(sui::make_status_label("正在检查安装环境…"));
  window.overview_status = window.install_environment_status;
  gtk_box_pack_start(GTK_BOX(environment_box), environment_header, FALSE, FALSE,
                     0);
  gtk_box_pack_start(GTK_BOX(environment_box),
                     GTK_WIDGET(window.install_environment_status), FALSE,
                     FALSE, 0);

  GtkWidget *framework_box = gtk_box_new(GTK_ORIENTATION_VERTICAL, 10);
  gtk_widget_set_hexpand(framework_box, TRUE);
  GtkWidget *framework_title = gtk_label_new("选择输入法框架");
  gtk_label_set_xalign(GTK_LABEL(framework_title), 0.0F);
  sui::add_class(framework_title, "row-title");
  gtk_box_pack_start(GTK_BOX(framework_box), framework_title, FALSE, FALSE, 0);
  window.ibus_framework_radio =
      GTK_RADIO_BUTTON(gtk_radio_button_new_with_label(nullptr, "IBus"));
  window.fcitx_framework_radio =
      GTK_RADIO_BUTTON(gtk_radio_button_new_with_label_from_widget(
          window.ibus_framework_radio, "Fcitx 5"));
  window.ibus_choice_status =
      GTK_LABEL(sui::make_status_label("正在检查安装状态…"));
  window.fcitx_choice_status =
      GTK_LABEL(sui::make_status_label("正在检查安装状态…"));

  auto framework_choice = [](GtkRadioButton *radio, const char *description,
                             GtkLabel *status) {
    GtkWidget *box = gtk_box_new(GTK_ORIENTATION_VERTICAL, 3);
    gtk_widget_set_halign(GTK_WIDGET(radio), GTK_ALIGN_START);
    gtk_box_pack_start(GTK_BOX(box), GTK_WIDGET(radio), FALSE, FALSE, 0);
    GtkWidget *description_label = gtk_label_new(description);
    gtk_label_set_xalign(GTK_LABEL(description_label), 0.0F);
    gtk_label_set_line_wrap(GTK_LABEL(description_label), TRUE);
    gtk_widget_set_margin_start(description_label, 28);
    sui::add_class(description_label, "row-subtitle");
    gtk_widget_set_margin_start(GTK_WIDGET(status), 28);
    gtk_box_pack_start(GTK_BOX(box), description_label, FALSE, FALSE, 0);
    gtk_box_pack_start(GTK_BOX(box), GTK_WIDGET(status), FALSE, FALSE, 0);
    return box;
  };
  gtk_box_pack_start(
      GTK_BOX(framework_box),
      framework_choice(
          window.ibus_framework_radio,
          "独立输入法引擎；安装后需要添加并切换到 VoCoType 输入源。",
          window.ibus_choice_status),
      FALSE, FALSE, 0);
  gtk_box_pack_start(
      GTK_BOX(framework_box),
      framework_choice(
          window.fcitx_framework_radio,
          "全局 Module；继续使用现有输入法，无需添加 VoCoType 输入源。",
          window.fcitx_choice_status),
      FALSE, FALSE, 0);

  gtk_box_pack_start(GTK_BOX(summary), environment_box, TRUE, TRUE, 0);
  gtk_box_pack_start(GTK_BOX(summary),
                     gtk_separator_new(GTK_ORIENTATION_VERTICAL), FALSE, FALSE,
                     0);
  gtk_box_pack_start(GTK_BOX(summary), framework_box, TRUE, TRUE, 0);
  gtk_box_pack_start(GTK_BOX(install_card), summary, FALSE, TRUE, 0);
  gtk_box_pack_start(GTK_BOX(install_card),
                     gtk_separator_new(GTK_ORIENTATION_HORIZONTAL), FALSE,
                     FALSE, 0);

  window.lifecycle_stack = GTK_STACK(gtk_stack_new());
  gtk_stack_set_transition_type(window.lifecycle_stack,
                                GTK_STACK_TRANSITION_TYPE_CROSSFADE);
  gtk_stack_set_transition_duration(window.lifecycle_stack, 120);
  gtk_widget_set_hexpand(GTK_WIDGET(window.lifecycle_stack), TRUE);

  auto lifecycle_page = [&window](const char *framework, const char *title,
                                  GtkLabel **status_slot) {
    GtkWidget *panel = gtk_box_new(GTK_ORIENTATION_VERTICAL, 12);
    gtk_container_set_border_width(GTK_CONTAINER(panel), 14);
    GtkWidget *status = sui::make_status_label(
        (std::string("正在检查 VoCoType（") + title + "）…").c_str());
    *status_slot = GTK_LABEL(status);
    GtkWidget *actions = gtk_grid_new();
    gtk_grid_set_row_spacing(GTK_GRID(actions), 8);
    gtk_grid_set_column_spacing(GTK_GRID(actions), 8);
    gtk_grid_set_column_homogeneous(GTK_GRID(actions), TRUE);
    gtk_widget_set_hexpand(actions, TRUE);

    GtkWidget *install = gtk_button_new_with_label(
        (std::string("安装 / 修复 VoCoType（") + title + "）").c_str());
    sui::set_button_suggested(install);
    GtkWidget *uninstall = gtk_button_new_with_label(
        (std::string("卸载 VoCoType（") + title + "）").c_str());
    GtkWidget *restart_core = gtk_button_new_with_label("重启 VoCoType 后台");
    GtkWidget *restart_framework =
        gtk_button_new_with_label((std::string("重启 ") + title).c_str());
    for (GtkWidget *button :
         {install, uninstall, restart_core, restart_framework}) {
      gtk_widget_set_hexpand(button, TRUE);
      gtk_widget_set_halign(button, GTK_ALIGN_FILL);
      g_object_set_data_full(G_OBJECT(button), "vocotype-framework",
                             g_strdup(framework), g_free);
    }
    gtk_grid_attach(GTK_GRID(actions), install, 0, 0, 1, 1);
    gtk_grid_attach(GTK_GRID(actions), uninstall, 1, 0, 1, 1);
    gtk_grid_attach(GTK_GRID(actions), restart_core, 0, 1, 1, 1);
    gtk_grid_attach(GTK_GRID(actions), restart_framework, 1, 1, 1, 1);
    gtk_box_pack_start(GTK_BOX(panel), status, FALSE, FALSE, 0);
    gtk_box_pack_start(GTK_BOX(panel), actions, FALSE, FALSE, 0);

    g_signal_connect(
        install, "clicked", G_CALLBACK(+[](GtkButton *button, gpointer data) {
          auto *self = static_cast<SettingsWindow *>(data);
          const char *framework = static_cast<const char *>(
              g_object_get_data(G_OBJECT(button), "vocotype-framework"));
          set_label(self->overview_status,
                    std::string("正在安装 / 修复 ") + framework + "…");
          run_async(
              [framework = std::string(framework)] {
                return install_integration(framework);
              },
              [self](Json result) {
                refresh_overview(*self);
                if (!result.value("success", false))
                  set_label(self->overview_status,
                            "安装失败：\n" + result.value("error", "unknown"));
              });
        }),
        &window);
    g_signal_connect(
        uninstall, "clicked", G_CALLBACK(+[](GtkButton *button, gpointer data) {
          auto *self = static_cast<SettingsWindow *>(data);
          const char *framework = static_cast<const char *>(
              g_object_get_data(G_OBJECT(button), "vocotype-framework"));
          set_label(self->overview_status,
                    std::string("正在卸载 ") + framework + "…");
          run_async(
              [framework = std::string(framework)] {
                return uninstall_integration(framework);
              },
              [self](Json result) {
                refresh_overview(*self);
                if (!result.value("success", false))
                  set_label(self->overview_status,
                            "卸载失败：\n" + result.value("error", "unknown"));
              });
        }),
        &window);
    g_signal_connect_swapped(
        restart_core, "clicked", G_CALLBACK(+[](SettingsWindow *self) {
          set_label(self->overview_status, "正在重启原生 Core…");
          run_async(
              [] {
                const bool ready = restart_core_for_settings();
                return Json{{"success", ready},
                            {"error", ready ? "" : "用户服务启动失败"}};
              },
              [self](Json result) {
                refresh_overview(*self);
                if (!result.value("success", false))
                  set_label(self->overview_status,
                            "Core 重启失败：" +
                                result.value("error", "unknown"));
              });
        }),
        &window);
    g_signal_connect(
        restart_framework, "clicked",
        G_CALLBACK(+[](GtkButton *button, gpointer data) {
          auto *self = static_cast<SettingsWindow *>(data);
          const std::string framework = static_cast<const char *>(
              g_object_get_data(G_OBJECT(button), "vocotype-framework"));
          Json result = framework == "ibus"
                            ? run_command({"ibus", "restart"})
                            : run_command({"fcitx5-remote", "-r"});
          set_label(self->overview_status,
                    result.value("success", false)
                        ? "✓ 输入法框架已请求重启"
                        : "重启失败：" + result.value("error", "unknown"));
        }),
        &window);
    return panel;
  };

  GtkWidget *ibus_lifecycle =
      lifecycle_page("ibus", "IBus", &window.ibus_install_status);
  GtkWidget *fcitx_lifecycle =
      lifecycle_page("fcitx5", "Fcitx 5", &window.fcitx_install_status);
  gtk_stack_add_titled(window.lifecycle_stack, ibus_lifecycle, "ibus", "IBus");
  gtk_stack_add_titled(window.lifecycle_stack, fcitx_lifecycle, "fcitx5",
                       "Fcitx 5");
  gtk_box_pack_start(GTK_BOX(install_card), GTK_WIDGET(window.lifecycle_stack),
                     FALSE, TRUE, 0);
  gtk_box_pack_start(GTK_BOX(page.content), install_card, FALSE, FALSE, 0);

  GtkWidget *resource_card = sui::make_card();
  GtkWidget *models = gtk_button_new_with_label("校验并下载模型");
  gtk_box_pack_start(GTK_BOX(resource_card),
                     sui::make_row("ASR 模型",
                                   "由原生模型管理器校验并下载识别所需模型。",
                                   models),
                     FALSE, FALSE, 0);
  GtkWidget *rime = gtk_button_new_with_label("初始化 IBus 内置 Rime");
  window.rime_resource_row =
      sui::make_row("IBus 内置 Rime",
                    "初始化 VoCoType IBus 独立引擎使用的 schema "
                    "与用户数据；Fcitx 5 不需要此步骤。",
                    rime);
  gtk_widget_set_no_show_all(window.rime_resource_row, TRUE);
  gtk_box_pack_start(GTK_BOX(resource_card), window.rime_resource_row, FALSE,
                     FALSE, 0);
  gtk_box_pack_start(GTK_BOX(page.content), resource_card, FALSE, FALSE, 0);

  GtkWidget *doctor_card = sui::make_card();
  GtkWidget *doctor_actions = sui::make_button_row();
  GtkWidget *quick_doctor = gtk_button_new_with_label("运行快速检查");
  GtkWidget *open_doctor = gtk_button_new_with_label("查看详情");
  gtk_box_pack_start(GTK_BOX(doctor_actions), quick_doctor, FALSE, FALSE, 0);
  gtk_box_pack_start(GTK_BOX(doctor_actions), open_doctor, FALSE, FALSE, 0);
  window.overview_summary = GTK_LABEL(sui::make_status_label("尚未运行检查"));
  GtkWidget *doctor_panel = gtk_box_new(GTK_ORIENTATION_VERTICAL, 8);
  gtk_box_pack_start(GTK_BOX(doctor_panel), doctor_actions, FALSE, FALSE, 0);
  gtk_box_pack_start(GTK_BOX(doctor_panel), GTK_WIDGET(window.overview_summary),
                     FALSE, FALSE, 0);
  gtk_box_pack_start(
      GTK_BOX(doctor_card),
      sui::make_row("运行状态",
                    "快速检查后仅显示摘要；详细结果和支持包位于诊断页。",
                    doctor_panel),
      FALSE, FALSE, 0);
  gtk_box_pack_start(GTK_BOX(page.content), doctor_card, FALSE, FALSE, 0);

  g_signal_connect_swapped(
      refresh, "clicked",
      G_CALLBACK(+[](SettingsWindow *self) { refresh_overview(*self); }),
      &window);
  auto framework_toggled = +[](GtkToggleButton *button, gpointer data) {
    if (!gtk_toggle_button_get_active(button))
      return;
    auto *self = static_cast<SettingsWindow *>(data);
    const std::string framework =
        GTK_WIDGET(button) == GTK_WIDGET(self->ibus_framework_radio) ? "ibus"
                                                                     : "fcitx5";
    self->config["ui"]["lifecycle_framework"] = framework;
    apply_framework_selection(*self, framework);
  };
  g_signal_connect(window.ibus_framework_radio, "toggled",
                   G_CALLBACK(framework_toggled), &window);
  g_signal_connect(window.fcitx_framework_radio, "toggled",
                   G_CALLBACK(framework_toggled), &window);
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
  g_signal_connect_swapped(
      rime, "clicked", G_CALLBACK(+[](SettingsWindow *self) {
#ifdef VOCOTYPE_HAVE_RIME
        set_label(self->overview_status, "正在初始化 IBus 内置 Rime…");
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
                          "IBus 内置 Rime 初始化失败：" +
                              result.value("error", "unknown"));
            });
#else
        set_label(self->overview_status, "此构建不含 IBus/Rime 支持");
#endif
      }),
      &window);
  g_signal_connect_swapped(
      quick_doctor, "clicked", G_CALLBACK(+[](SettingsWindow *self) {
        const std::string report = doctor_report();
        set_label(self->overview_summary,
                  report.find("[FAIL]") == std::string::npos
                      ? "✅ 快速检查未发现阻断性问题"
                      : "⚠️ 检查发现问题；请进入诊断页查看详情");
        if (self->doctor_output)
          set_text(self->doctor_output, report);
      }),
      &window);
  g_signal_connect_swapped(
      open_doctor, "clicked", G_CALLBACK(+[](SettingsWindow *self) {
        gtk_stack_set_visible_child_name(self->stack, "doctor");
      }),
      &window);
  return page.scroller;
}

GtkWidget *build_recognition(SettingsWindow &window) {
  const auto page = sui::make_page(
      "通用设置", "集中配置麦克风、F9 状态样式、实时识别预览与 ITN。Playground "
                  "中的麦克风控件与这里双向同步。");

  gtk_box_pack_start(
      GTK_BOX(page.content),
      sui::make_section_heading(
          "麦克风与采样率",
          "这组设置同时供 F9、IBus、Fcitx 5 与 Playground 使用。"),
      FALSE, FALSE, 0);
  GtkWidget *audio_card = sui::make_card();
  window.audio_device = GTK_COMBO_BOX_TEXT(gtk_combo_box_text_new());
  gtk_widget_set_hexpand(GTK_WIDGET(window.audio_device), TRUE);
  window.audio_rate =
      GTK_SPIN_BUTTON(gtk_spin_button_new_with_range(8000, 192000, 1000));
  gtk_spin_button_set_value(window.audio_rate, 44100);
  window.minimum_recording =
      GTK_SPIN_BUTTON(gtk_spin_button_new_with_range(0, 5000, 100));
  gtk_spin_button_set_value(window.minimum_recording, 1000);
  gtk_spin_button_set_numeric(window.minimum_recording, TRUE);
  GtkWidget *refresh_audio = gtk_button_new_with_label("刷新音频设备");
  window.recognition_status =
      GTK_LABEL(sui::make_status_label("尚未枚举音频设备"));
  GtkWidget *audio_actions = sui::make_button_row();
  gtk_box_pack_start(GTK_BOX(audio_actions), refresh_audio, FALSE, FALSE, 0);
  gtk_box_pack_start(GTK_BOX(audio_actions),
                     GTK_WIDGET(window.recognition_status), TRUE, TRUE, 0);
  gtk_box_pack_start(
      GTK_BOX(audio_card),
      sui::make_row("输入设备",
                    "选择后会同步到 Playground；成功录音时保存为 F9 使用设备。",
                    GTK_WIDGET(window.audio_device)),
      FALSE, FALSE, 0);
  gtk_box_pack_start(
      GTK_BOX(audio_card),
      sui::make_row(
          "原生采样率",
          "按设备原生采样率采集；ASR 会在内部重采样到模型需要的采样率。",
          GTK_WIDGET(window.audio_rate)),
      FALSE, FALSE, 0);
  gtk_box_pack_start(
      GTK_BOX(audio_card),
      sui::make_row("最短有效录音（毫秒）",
                    "不足此时长的 F9 / Shift+F9 / Ctrl+F9 录音会直接丢弃；默认 "
                    "1000 ms，0 表示关闭限制。",
                    GTK_WIDGET(window.minimum_recording)),
      FALSE, FALSE, 0);
  gtk_box_pack_start(GTK_BOX(audio_card),
                     sui::make_row("设备状态", "", audio_actions), FALSE, FALSE,
                     0);
  gtk_box_pack_start(GTK_BOX(page.content), audio_card, FALSE, FALSE, 0);

  window.fcitx_panel_section = sui::make_section_heading(
      "Fcitx 5：F9 交互样式",
      "控制 Fcitx 候选框中第一行状态和第二行实时预览的样式。");
  gtk_widget_set_no_show_all(window.fcitx_panel_section, TRUE);
  gtk_box_pack_start(GTK_BOX(page.content), window.fcitx_panel_section, FALSE,
                     FALSE, 0);
  GtkWidget *panel_card = sui::make_card();
  window.fcitx_panel_card = panel_card;
  gtk_widget_set_no_show_all(window.fcitx_panel_card, TRUE);
  window.fcitx_panel_style = GTK_COMBO_BOX_TEXT(gtk_combo_box_text_new());
  gtk_combo_box_text_append(window.fcitx_panel_style, "minimal",
                            "极简：🎤 录音中 / ⏳ 识别中");
  gtk_combo_box_text_append(window.fcitx_panel_style, "animated",
                            "动画：正在听状态动画");
  gtk_combo_box_set_active_id(GTK_COMBO_BOX(window.fcitx_panel_style),
                              "minimal");
  window.panel_style_status =
      GTK_LABEL(sui::make_status_label("极简模式：第一行保持简洁状态"));
  GtkWidget *panel_control = gtk_box_new(GTK_ORIENTATION_VERTICAL, 4);
  gtk_box_pack_start(GTK_BOX(panel_control),
                     GTK_WIDGET(window.fcitx_panel_style), FALSE, FALSE, 0);
  gtk_box_pack_start(GTK_BOX(panel_control),
                     GTK_WIDGET(window.panel_style_status), FALSE, FALSE, 0);
  gtk_box_pack_start(GTK_BOX(panel_card),
                     sui::make_row("状态样式",
                                   "极简模式保持“录音中”；动画模式保持“正在听”"
                                   "动画。流式 partial 始终显示在第二行。",
                                   panel_control),
                     FALSE, FALSE, 0);
  gtk_box_pack_start(GTK_BOX(page.content), panel_card, FALSE, FALSE, 0);

  window.fcitx_output_section = sui::make_section_heading(
      "Fcitx 5：输出文本",
      "控制 F9 与 Shift+F9 最终通过 Fcitx 提交到当前输入框的文本格式。");
  gtk_widget_set_no_show_all(window.fcitx_output_section, TRUE);
  gtk_box_pack_start(GTK_BOX(page.content), window.fcitx_output_section, FALSE,
                     FALSE, 0);
  GtkWidget *output_card = sui::make_card();
  window.fcitx_output_card = output_card;
  gtk_widget_set_no_show_all(window.fcitx_output_card, TRUE);
  window.fcitx_strip_period = GTK_SWITCH(sui::make_switch());
  gtk_box_pack_start(GTK_BOX(output_card),
                     sui::make_row("取消句尾句号",
                                   "开启后移除末尾中文句号“。”或英文句点“."
                                   "”；问号、叹号和语音编辑结果不受影响。",
                                   GTK_WIDGET(window.fcitx_strip_period)),
                     FALSE, FALSE, 0);
  gtk_box_pack_start(GTK_BOX(page.content), output_card, FALSE, FALSE, 0);

  gtk_box_pack_start(
      GTK_BOX(page.content),
      sui::make_section_heading("实时识别预览（2-pass）",
                                "在线模型只负责录音期间的第二行预览；松键后仍由"
                                "高精度离线模型给出最终结果。"),
      FALSE, FALSE, 0);
  GtkWidget *streaming_card = sui::make_card();
  window.streaming_enabled = GTK_SWITCH(sui::make_switch());
  gtk_box_pack_start(GTK_BOX(streaming_card),
                     sui::make_row("启用实时识别预览",
                                   "首次使用会按需下载官方在线模型；native "
                                   "worker 空闲后自动退出并释放内存。",
                                   GTK_WIDGET(window.streaming_enabled)),
                     FALSE, FALSE, 0);
  gtk_box_pack_start(GTK_BOX(page.content), streaming_card, FALSE, FALSE, 0);

  gtk_box_pack_start(GTK_BOX(page.content),
                     sui::make_section_heading(
                         "数字与格式（ITN）",
                         "控制识别后的数字、日期、时间、距离和金额格式。"),
                     FALSE, FALSE, 0);
  GtkWidget *itn_card = sui::make_card();
  window.normalization_enabled = GTK_SWITCH(sui::make_switch());
  window.compact_dates = GTK_SWITCH(sui::make_switch());
  window.compact_times = GTK_SWITCH(sui::make_switch());
  window.compact_distances = GTK_SWITCH(sui::make_switch());
  window.currency_symbols = GTK_SWITCH(sui::make_switch());
  gtk_box_pack_start(GTK_BOX(itn_card),
                     sui::make_row("启用数字与 ITN",
                                   "关闭后保留用户词典替换，但不改写中文数字。",
                                   GTK_WIDGET(window.normalization_enabled)),
                     FALSE, FALSE, 0);
  gtk_box_pack_start(GTK_BOX(itn_card),
                     sui::make_row("紧凑日期",
                                   "例如：二零二六年五月十一号 → 2026/05/11",
                                   GTK_WIDGET(window.compact_dates)),
                     FALSE, FALSE, 0);
  gtk_box_pack_start(GTK_BOX(itn_card),
                     sui::make_row("24 小时时间",
                                   "例如：下午三点二十分 → 15:20",
                                   GTK_WIDGET(window.compact_times)),
                     FALSE, FALSE, 0);
  gtk_box_pack_start(GTK_BOX(itn_card),
                     sui::make_row("路程单位缩写", "例如：三百二十米 → 320m",
                                   GTK_WIDGET(window.compact_distances)),
                     FALSE, FALSE, 0);
  gtk_box_pack_start(GTK_BOX(itn_card),
                     sui::make_row("金额符号", "例如：一百二十八元 → ¥128",
                                   GTK_WIDGET(window.currency_symbols)),
                     FALSE, FALSE, 0);
  gtk_box_pack_start(GTK_BOX(page.content), itn_card, FALSE, FALSE, 0);

  GtkWidget *preview_card = sui::make_card();
  window.itn_preview_input = GTK_ENTRY(sui::make_entry(42));
  gtk_entry_set_text(
      window.itn_preview_input,
      "二零二六年五月十一号下午三点二十分跑了三百二十米，花了一百二十八元");
  GtkWidget *preview_button = gtk_button_new_with_label("生成 ITN 预览");
  GtkWidget *input_box = sui::make_button_row();
  gtk_widget_set_hexpand(GTK_WIDGET(window.itn_preview_input), TRUE);
  gtk_box_pack_start(GTK_BOX(input_box), GTK_WIDGET(window.itn_preview_input),
                     TRUE, TRUE, 0);
  gtk_box_pack_start(GTK_BOX(input_box), preview_button, FALSE, FALSE, 0);
  window.itn_preview_output =
      GTK_LABEL(sui::make_preview_label("点击“生成 ITN 预览”查看结果"));
  gtk_box_pack_start(GTK_BOX(preview_card),
                     sui::make_row("测试文本", "", input_box), FALSE, FALSE, 0);
  gtk_box_pack_start(
      GTK_BOX(preview_card),
      sui::make_row("预览结果", "", GTK_WIDGET(window.itn_preview_output)),
      FALSE, FALSE, 0);
  gtk_box_pack_start(GTK_BOX(page.content), preview_card, FALSE, FALSE, 0);

  gtk_box_pack_start(
      GTK_BOX(page.content),
      sui::make_section_heading(
          "输入法高级选项",
          "这些设置通常不需要修改；保留当前 C++ 版本新增的输入法能力。"),
      FALSE, FALSE, 0);
  GtkWidget *advanced_card = sui::make_card();
  window.fcitx_block_composing = GTK_SWITCH(sui::make_switch());
  window.rime_schema = GTK_COMBO_BOX_TEXT(gtk_combo_box_text_new());
  gtk_widget_set_hexpand(GTK_WIDGET(window.rime_schema), TRUE);
  window.fcitx_composing_row = sui::make_row(
      "Fcitx：组合中阻止录音",
      "当前输入法仍有未提交 preedit 时，避免 F9 与键盘组合互相干扰。",
      GTK_WIDGET(window.fcitx_block_composing));
  gtk_widget_set_no_show_all(window.fcitx_composing_row, TRUE);
  gtk_box_pack_start(GTK_BOX(advanced_card), window.fcitx_composing_row, FALSE,
                     FALSE, 0);
  window.rime_schema_row = sui::make_row(
      "IBus Rime schema", "VoCoType IBus 独立引擎内置 Rime 使用的 schema。",
      GTK_WIDGET(window.rime_schema));
  gtk_widget_set_no_show_all(window.rime_schema_row, TRUE);
  gtk_box_pack_start(GTK_BOX(advanced_card), window.rime_schema_row, FALSE,
                     FALSE, 0);
  gtk_box_pack_start(GTK_BOX(page.content), advanced_card, FALSE, FALSE, 0);

  g_signal_connect_swapped(
      refresh_audio, "clicked",
      G_CALLBACK(+[](SettingsWindow *self) { refresh_devices(*self); }),
      &window);
  g_signal_connect(window.audio_device, "changed",
                   G_CALLBACK(+[](GtkComboBox *box, gpointer data) {
                     auto *self = static_cast<SettingsWindow *>(data);
                     if (self->playground_audio_device)
                       gtk_combo_box_set_active(
                           GTK_COMBO_BOX(self->playground_audio_device),
                           gtk_combo_box_get_active(box));
                   }),
                   &window);
  g_signal_connect(window.audio_rate, "value-changed",
                   G_CALLBACK(+[](GtkSpinButton *spin, gpointer data) {
                     auto *self = static_cast<SettingsWindow *>(data);
                     if (self->playground_audio_rate)
                       gtk_spin_button_set_value(
                           self->playground_audio_rate,
                           gtk_spin_button_get_value(spin));
                   }),
                   &window);
  g_signal_connect(window.fcitx_panel_style, "changed",
                   G_CALLBACK(+[](GtkComboBox *box, gpointer data) {
                     auto *self = static_cast<SettingsWindow *>(data);
                     const char *active = gtk_combo_box_get_active_id(box);
                     set_label(self->panel_style_status,
                               active && std::string(active) == "animated"
                                   ? "动画模式：第一行显示正在听状态动画"
                                   : "极简模式：第一行保持简洁状态");
                   }),
                   &window);
  auto preview = +[](SettingsWindow *self) {
    try {
      if (!vocotype::desktop::ensure_native_core())
        throw std::runtime_error("Core 未启动");
      const Json normalization{
          {"enabled", gtk_switch_get_active(self->normalization_enabled)},
          {"compact_dates", gtk_switch_get_active(self->compact_dates)},
          {"compact_times", gtk_switch_get_active(self->compact_times)},
          {"compact_distances", gtk_switch_get_active(self->compact_distances)},
          {"currency_symbols", gtk_switch_get_active(self->currency_symbols)},
      };
      const Json result = vocotype::desktop::unix_json_request(
          vocotype::desktop::backend_socket_path(),
          {{"type", "normalize_text"},
           {"text", gtk_entry_get_text(self->itn_preview_input)},
           {"normalization", normalization}},
          5000);
      set_label(self->itn_preview_output,
                result.value("text", result.value("error", "失败")));
    } catch (const std::exception &error) {
      set_label(self->itn_preview_output, std::string("错误：") + error.what());
    }
  };
  g_signal_connect_swapped(preview_button, "clicked", G_CALLBACK(preview),
                           &window);
  g_signal_connect_swapped(window.itn_preview_input, "activate",
                           G_CALLBACK(preview), &window);
  return page.scroller;
}

GtkWidget *build_terms(SettingsWindow &window) {
  const auto page =
      sui::make_page("用户词典", "同一份术语库同时用于 Contextual Paraformer "
                                 "原生 hotword、识别后标准化和 ITN 保护。");
  GtkWidget *toolbar = sui::make_button_row();
  GtkWidget *reload = gtk_button_new_with_label("重新载入");
  GtkWidget *save = gtk_button_new_with_label("验证并保存");
  GtkWidget *open = gtk_button_new_with_label("在文件管理器中显示");
  sui::set_button_suggested(save);
  gtk_box_pack_start(GTK_BOX(toolbar), reload, FALSE, FALSE, 0);
  gtk_box_pack_start(GTK_BOX(toolbar), save, FALSE, FALSE, 0);
  gtk_box_pack_start(GTK_BOX(toolbar), open, FALSE, FALSE, 0);
  gtk_box_pack_start(GTK_BOX(page.content), toolbar, FALSE, FALSE, 0);

  GtkWidget *editor =
      sui::make_scrolled_text(&window.terms, 440, true, GTK_WRAP_NONE);
  gtk_widget_set_vexpand(editor, TRUE);
  gtk_box_pack_start(GTK_BOX(page.content), editor, TRUE, TRUE, 0);
  window.terms_status = GTK_LABEL(sui::make_status_label(""));
  gtk_box_pack_start(GTK_BOX(page.content), GTK_WIDGET(window.terms_status),
                     FALSE, FALSE, 0);

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
          set_label(self->terms_status,
                    "✓ 术语库已保存；下一次识别会自动热重载");
        } catch (const std::exception &error) {
          set_label(self->terms_status, std::string("未保存：") + error.what());
        }
      }),
      &window);
  g_signal_connect_swapped(
      open, "clicked", G_CALLBACK(+[](SettingsWindow *self) {
        std::string error;
        const std::string uri =
            "file://" + vocotype::desktop::terms_path().parent_path().string();
        if (!open_uri(GTK_WINDOW(self->window), uri, error))
          set_label(self->terms_status, "打开目录失败：" + error);
      }),
      &window);
  return page.scroller;
}

GtkWidget *build_slm(SettingsWindow &window) {
  const auto page = sui::make_page(
      "AI 润色与语音编辑",
      "连接任意 OpenAI-compatible "
      "API。服务可运行在本机、局域网或云端；VoCoType "
      "只发起请求，不管理模型进程。Shift+F9 用于润色，Ctrl+F9 用于语音编辑。");
  GtkWidget *card = sui::make_card();
  window.slm_enabled = GTK_SWITCH(sui::make_switch());
  window.slm_streaming = GTK_SWITCH(sui::make_switch());
  window.slm_thinking = GTK_SWITCH(sui::make_switch());
  window.edit_enabled = GTK_SWITCH(sui::make_switch());
  window.slm_endpoint = GTK_ENTRY(sui::make_entry());
  window.slm_model = GTK_ENTRY(sui::make_entry());
  window.slm_api_key_env = GTK_ENTRY(sui::make_entry());
  gtk_entry_set_placeholder_text(window.slm_api_key_env,
                                 "例如 DEEPSEEK_API_KEY（这里只填变量名）");
  window.slm_api_key = GTK_ENTRY(sui::make_entry());
  gtk_entry_set_visibility(window.slm_api_key, FALSE);
  gtk_entry_set_placeholder_text(window.slm_api_key,
                                 "直接粘贴 sk-...；留空则保留现有凭据");
  window.slm_clear_api_key = GTK_CHECK_BUTTON(
      gtk_check_button_new_with_label("清除已保存的直接 API Key"));
  window.slm_min_chars =
      GTK_SPIN_BUTTON(gtk_spin_button_new_with_range(0, 2000, 1));
  window.slm_timeout =
      GTK_SPIN_BUTTON(gtk_spin_button_new_with_range(1000, 120000, 1000));

  GtkWidget *test_actions = sui::make_button_row();
  GtkWidget *test = gtk_button_new_with_label("测活 AI 端点 / 模型");
  window.slm_status = GTK_LABEL(sui::make_status_label("尚未测活"));
  gtk_box_pack_start(GTK_BOX(test_actions), test, FALSE, FALSE, 0);
  gtk_box_pack_start(GTK_BOX(test_actions), GTK_WIDGET(window.slm_status), TRUE,
                     TRUE, 0);
  gtk_box_pack_start(GTK_BOX(card),
                     sui::make_row("AI 测活",
                                   "对当前端点和模型发起一次真实请求；成功后即"
                                   "可在 Playground 测试润色与语音编辑。",
                                   test_actions),
                     FALSE, FALSE, 0);
  gtk_box_pack_start(
      GTK_BOX(card),
      sui::make_row("启用 AI 功能",
                    "F9 始终直接输出；Shift+F9 调用润色；Ctrl+F9 结合 "
                    "surrounding context 生成安全编辑计划。",
                    GTK_WIDGET(window.slm_enabled)),
      FALSE, FALSE, 0);
  gtk_box_pack_start(
      GTK_BOX(card),
      sui::make_row(
          "API 地址",
          "可填写服务根地址或 /v1/chat/completions；本机服务同样填写这里。",
          GTK_WIDGET(window.slm_endpoint)),
      FALSE, FALSE, 0);
  gtk_box_pack_start(GTK_BOX(card),
                     sui::make_row("模型", "", GTK_WIDGET(window.slm_model)),
                     FALSE, FALSE, 0);
  gtk_box_pack_start(GTK_BOX(card),
                     sui::make_row("API Key 环境变量名（高级）",
                                   "这里只填写变量名，不要粘贴实际密钥。",
                                   GTK_WIDGET(window.slm_api_key_env)),
                     FALSE, FALSE, 0);
  gtk_box_pack_start(
      GTK_BOX(card),
      sui::make_row("直接 API Key",
                    "可选；无鉴权的本地服务可留空。配置文件权限固定为 0600。",
                    GTK_WIDGET(window.slm_api_key)),
      FALSE, FALSE, 0);
  gtk_box_pack_start(GTK_BOX(card),
                     sui::make_row("清除直接凭据",
                                   "切换到环境变量凭据时可清除旧值。",
                                   GTK_WIDGET(window.slm_clear_api_key)),
                     FALSE, FALSE, 0);
  gtk_box_pack_start(GTK_BOX(card),
                     sui::make_row("最少润色字符数", "0 表示不限制。",
                                   GTK_WIDGET(window.slm_min_chars)),
                     FALSE, FALSE, 0);
  gtk_box_pack_start(GTK_BOX(card),
                     sui::make_row("流式空闲超时（毫秒）",
                                   "超过该时间未收到增量则终止请求。",
                                   GTK_WIDGET(window.slm_timeout)),
                     FALSE, FALSE, 0);
  gtk_box_pack_start(
      GTK_BOX(card),
      sui::make_row("流式输出", "支持 SSE 的端点可在候选框实时显示可见增量。",
                    GTK_WIDGET(window.slm_streaming)),
      FALSE, FALSE, 0);
  gtk_box_pack_start(GTK_BOX(card),
                     sui::make_row("允许 reasoning/thinking",
                                   "思考内容不会进入最终提交。",
                                   GTK_WIDGET(window.slm_thinking)),
                     FALSE, FALSE, 0);
  gtk_box_pack_start(GTK_BOX(card),
                     sui::make_row("启用 Ctrl+F9 语音编辑",
                                   "模型只会返回受限替换、提交或按键计划。",
                                   GTK_WIDGET(window.edit_enabled)),
                     FALSE, FALSE, 0);
  gtk_box_pack_start(GTK_BOX(page.content), card, FALSE, FALSE, 0);

  g_signal_connect_swapped(
      test, "clicked", G_CALLBACK(+[](SettingsWindow *self) {
        set_label(self->slm_status, "⏳ 正在测活；成功后即可使用 AI 功能…");
        try {
          save_config(*self);
        } catch (const std::exception &error) {
          set_label(self->slm_status,
                    std::string("保存配置失败：") + error.what());
          return;
        }
        run_async(
            [] {
              if (!restart_core_for_settings())
                return Json{{"success", false},
                            {"error", "Core 用户服务未能启动"}};
              return vocotype::desktop::unix_json_request(
                  vocotype::desktop::backend_socket_path(),
                  {{"type", "polish_text"},
                   {"text", "这是一次 VoCoType AI 润色连接测试。"}},
                  120000);
            },
            [self](Json result) {
              if (result.value("success", false)) {
                gtk_switch_set_active(self->slm_enabled, TRUE);
                set_label(self->slm_status,
                          "✅ 测活成功：" + result.value("text", "服务已响应"));
                if (self->playground_ai_controls)
                  gtk_widget_set_sensitive(self->playground_ai_controls, TRUE);
                if (self->playground_ai_gate_status)
                  set_label(self->playground_ai_gate_status,
                            "✅ AI 配置已启用，可测试润色与语音编辑");
              } else {
                gtk_switch_set_active(self->slm_enabled, FALSE);
                set_label(self->slm_status,
                          "❌ 测活失败：" + result.value("error", "unknown"));
              }
            });
      }),
      &window);
  return page.scroller;
}

GtkWidget *build_playground(SettingsWindow &window) {
  const auto page = sui::make_page(
      "Playground", "依次测试麦克风、真实转录、AI "
                    "润色与语音编辑。输入设备和采样率与“通用设置”双向同步。");

  gtk_box_pack_start(
      GTK_BOX(page.content),
      sui::make_section_heading("1. 测试麦克风",
                                "录音、查看波形，并从指定输出设备回放。"),
      FALSE, FALSE, 0);
  GtkWidget *audio_card = sui::make_card();
  window.playground_audio_device = GTK_COMBO_BOX_TEXT(gtk_combo_box_text_new());
  gtk_widget_set_hexpand(GTK_WIDGET(window.playground_audio_device), TRUE);
  window.playground_audio_rate =
      GTK_SPIN_BUTTON(gtk_spin_button_new_with_range(8000, 192000, 1000));
  gtk_spin_button_set_value(window.playground_audio_rate, 44100);
  window.audio_output = GTK_COMBO_BOX_TEXT(gtk_combo_box_text_new());
  gtk_widget_set_hexpand(GTK_WIDGET(window.audio_output), TRUE);
  GtkWidget *refresh = gtk_button_new_with_label("刷新设备");
  GtkWidget *record = gtk_button_new_with_label("录音 3 秒");
  GtkWidget *play = gtk_button_new_with_label("回放上次录音");
  sui::set_button_suggested(record);
  GtkWidget *audio_actions = sui::make_button_row();
  gtk_box_pack_start(GTK_BOX(audio_actions), refresh, FALSE, FALSE, 0);
  gtk_box_pack_start(GTK_BOX(audio_actions), record, FALSE, FALSE, 0);
  gtk_box_pack_start(GTK_BOX(audio_actions), play, FALSE, FALSE, 0);
  window.playground_status =
      GTK_LABEL(sui::make_status_label("尚未枚举音频设备"));
  gtk_box_pack_start(
      GTK_BOX(audio_card),
      sui::make_row("输入设备",
                    "与通用设置同步；成功录音后保存为 F9 使用设备。",
                    GTK_WIDGET(window.playground_audio_device)),
      FALSE, FALSE, 0);
  gtk_box_pack_start(GTK_BOX(audio_card),
                     sui::make_row("采样率", "与通用设置同步。",
                                   GTK_WIDGET(window.playground_audio_rate)),
                     FALSE, FALSE, 0);
  gtk_box_pack_start(GTK_BOX(audio_card),
                     sui::make_row("输出设备",
                                   "回放会明确发送到所选音频输出设备。",
                                   GTK_WIDGET(window.audio_output)),
                     FALSE, FALSE, 0);
  gtk_box_pack_start(GTK_BOX(audio_card),
                     sui::make_row("录音与回放", "", audio_actions), FALSE,
                     FALSE, 0);
  GtkWidget *waveform_section = gtk_box_new(GTK_ORIENTATION_VERTICAL, 8);
  sui::add_class(waveform_section, "card-row");
  GtkWidget *waveform_title = gtk_label_new("实时波形");
  gtk_label_set_xalign(GTK_LABEL(waveform_title), 0.0F);
  sui::add_class(waveform_title, "row-title");
  GtkWidget *waveform_hint =
      gtk_label_new("录音期间滚动显示并自动放大；原始 WAV 不会被修改。");
  gtk_label_set_xalign(GTK_LABEL(waveform_hint), 0.0F);
  gtk_label_set_line_wrap(GTK_LABEL(waveform_hint), TRUE);
  sui::add_class(waveform_hint, "row-subtitle");
  window.playground_waveform = GTK_DRAWING_AREA(gtk_drawing_area_new());
  gtk_widget_set_size_request(GTK_WIDGET(window.playground_waveform), -1, 110);
  gtk_widget_set_hexpand(GTK_WIDGET(window.playground_waveform), TRUE);
  sui::add_class(GTK_WIDGET(window.playground_waveform), "waveform");
  g_signal_connect(window.playground_waveform, "draw",
                   G_CALLBACK(draw_waveform), &window);
  gtk_box_pack_start(GTK_BOX(waveform_section), waveform_title, FALSE, FALSE,
                     0);
  gtk_box_pack_start(GTK_BOX(waveform_section), waveform_hint, FALSE, FALSE, 0);
  gtk_box_pack_start(GTK_BOX(waveform_section),
                     GTK_WIDGET(window.playground_waveform), FALSE, TRUE, 0);
  gtk_box_pack_start(GTK_BOX(audio_card), waveform_section, FALSE, FALSE, 0);
  gtk_box_pack_start(
      GTK_BOX(audio_card),
      sui::make_row("状态", "", GTK_WIDGET(window.playground_status)), FALSE,
      FALSE, 0);
  gtk_box_pack_start(GTK_BOX(page.content), audio_card, FALSE, FALSE, 0);

  gtk_box_pack_start(GTK_BOX(page.content),
                     sui::make_section_heading(
                         "2. 测试转录", "调用当前已安装的真实 ASR 后台。"),
                     FALSE, FALSE, 0);
  GtkWidget *asr_card = sui::make_card();
  GtkWidget *transcribe = gtk_button_new_with_label("转录上次录音");
  GtkWidget *asr_actions = sui::make_button_row();
  GtkWidget *asr_status = sui::make_status_label(
      "录音完成后可调用当前 VoCoType ASR 后台检查识别内容。");
  gtk_box_pack_start(GTK_BOX(asr_actions), transcribe, FALSE, FALSE, 0);
  gtk_box_pack_start(GTK_BOX(asr_actions), asr_status, TRUE, TRUE, 0);
  gtk_box_pack_start(GTK_BOX(asr_card),
                     sui::make_row("真实 ASR 转录", "", asr_actions), FALSE,
                     FALSE, 0);
  GtkWidget *transcript_scroll =
      sui::make_scrolled_text(&window.playground_result, 110);
  set_text(window.playground_result,
           "转录结果会显示在这里；你可以直接编辑以对照实际口述。");
  gtk_box_pack_start(GTK_BOX(asr_card), transcript_scroll, FALSE, FALSE, 12);
  gtk_box_pack_start(GTK_BOX(page.content), asr_card, FALSE, FALSE, 0);

  window.playground_ai_gate_status =
      GTK_LABEL(sui::make_status_label("AI 功能尚未启用或未测活。"));
  gtk_box_pack_start(GTK_BOX(page.content),
                     GTK_WIDGET(window.playground_ai_gate_status), FALSE, FALSE,
                     0);
  window.playground_ai_controls = gtk_box_new(GTK_ORIENTATION_VERTICAL, 16);
  gtk_widget_set_sensitive(window.playground_ai_controls, FALSE);

  gtk_box_pack_start(
      GTK_BOX(window.playground_ai_controls),
      sui::make_section_heading(
          "3. 测试 AI 润色",
          "使用已启用且已测活的当前模型整理文本，不带编辑指令。"),
      FALSE, FALSE, 0);
  GtkWidget *polish_card = sui::make_card();
  window.playground_polish_source = GTK_TEXT_VIEW(gtk_text_view_new());
  gtk_text_view_set_wrap_mode(window.playground_polish_source,
                              GTK_WRAP_WORD_CHAR);
  set_text(window.playground_polish_source,
           "这是一段有一点啰嗦而且表达不够自然的测试文本，希望 AI "
           "帮我整理得更清楚。");
  GtkWidget *polish_source_scroll = gtk_scrolled_window_new(nullptr, nullptr);
  gtk_scrolled_window_set_min_content_height(
      GTK_SCROLLED_WINDOW(polish_source_scroll), 100);
  gtk_container_add(GTK_CONTAINER(polish_source_scroll),
                    GTK_WIDGET(window.playground_polish_source));
  GtkWidget *polish_button = gtk_button_new_with_label("测试 AI 润色");
  window.playground_polish_status = GTK_LABEL(sui::make_status_label(""));
  GtkWidget *polish_actions = sui::make_button_row();
  gtk_box_pack_start(GTK_BOX(polish_actions), polish_button, FALSE, FALSE, 0);
  gtk_box_pack_start(GTK_BOX(polish_actions),
                     GTK_WIDGET(window.playground_polish_status), TRUE, TRUE,
                     0);
  GtkWidget *polish_result_scroll =
      sui::make_scrolled_text(&window.playground_polish_result, 110);
  set_text(window.playground_polish_result, "AI 润色结果会显示在这里。");
  GtkWidget *polish_source_label = gtk_label_new("待润色文本");
  gtk_label_set_xalign(GTK_LABEL(polish_source_label), 0.0F);
  GtkWidget *polish_result_label = gtk_label_new("润色结果");
  gtk_label_set_xalign(GTK_LABEL(polish_result_label), 0.0F);
  gtk_box_pack_start(GTK_BOX(polish_card), polish_source_label, FALSE, FALSE,
                     12);
  gtk_box_pack_start(GTK_BOX(polish_card), polish_source_scroll, FALSE, FALSE,
                     12);
  gtk_box_pack_start(GTK_BOX(polish_card), polish_actions, FALSE, FALSE, 12);
  gtk_box_pack_start(GTK_BOX(polish_card), polish_result_label, FALSE, FALSE,
                     12);
  gtk_box_pack_start(GTK_BOX(polish_card), polish_result_scroll, FALSE, FALSE,
                     12);
  gtk_box_pack_start(GTK_BOX(window.playground_ai_controls), polish_card, FALSE,
                     FALSE, 0);

  gtk_box_pack_start(
      GTK_BOX(window.playground_ai_controls),
      sui::make_section_heading("4. 测试语音编辑",
                                "所有指令都会把 ASR "
                                "结果、上下文、光标和选区交给模型生成受限编辑计"
                                "划；可验证替换、翻译、LaTeX 与文本生成。"),
      FALSE, FALSE, 0);
  GtkWidget *edit_card = sui::make_card();
  GtkWidget *edit_source_scroll =
      sui::make_scrolled_text(&window.playground_edit_source, 110);
  set_text(window.playground_edit_source, "勾股定理是一项伟大的发明");
  window.playground_edit_instruction = GTK_ENTRY(sui::make_entry());
  gtk_entry_set_text(window.playground_edit_instruction,
                     "把勾股定理翻译为英文");
  GtkWidget *examples = sui::make_button_row();
  struct Example {
    const char *label;
    const char *source;
    const char *instruction;
  };
  const std::array<Example, 4> examples_data{{
      {"把 A 替换成 B", "A 是旧版本标记，后文仍然引用 A。", "把 A 替换成 B"},
      {"翻译成英语", "勾股定理是一项伟大的发明", "把勾股定理翻译为英文"},
      {"转成 LaTeX", "x 的平方加 y 的平方等于 z 的平方", "转成 LaTeX"},
      {"写一条好评", "这个语音输入工具识别快、隐私好、安装方便。",
       "写一条好评"},
  }};
  for (const auto &example : examples_data) {
    GtkWidget *button = gtk_button_new_with_label(example.label);
    g_object_set_data_full(G_OBJECT(button), "vocotype-source",
                           g_strdup(example.source), g_free);
    g_object_set_data_full(G_OBJECT(button), "vocotype-instruction",
                           g_strdup(example.instruction), g_free);
    g_signal_connect(
        button, "clicked", G_CALLBACK(+[](GtkButton *button, gpointer data) {
          auto *self = static_cast<SettingsWindow *>(data);
          set_text(self->playground_edit_source,
                   static_cast<const char *>(
                       g_object_get_data(G_OBJECT(button), "vocotype-source")));
          gtk_entry_set_text(self->playground_edit_instruction,
                             static_cast<const char *>(g_object_get_data(
                                 G_OBJECT(button), "vocotype-instruction")));
        }),
        &window);
    gtk_box_pack_start(GTK_BOX(examples), button, FALSE, FALSE, 0);
  }
  GtkWidget *edit_button = gtk_button_new_with_label("录制语音指令 3 秒并测试");
  window.playground_edit_status = GTK_LABEL(sui::make_status_label(""));
  GtkWidget *edit_actions = sui::make_button_row();
  gtk_box_pack_start(GTK_BOX(edit_actions), edit_button, FALSE, FALSE, 0);
  gtk_box_pack_start(GTK_BOX(edit_actions),
                     GTK_WIDGET(window.playground_edit_status), TRUE, TRUE, 0);
  GtkWidget *edit_result_scroll =
      sui::make_scrolled_text(&window.playground_edit_result, 120);
  set_text(window.playground_edit_result, "语音编辑结果会显示在这里。");
  GtkWidget *context_label = gtk_label_new("上下文文本");
  gtk_label_set_xalign(GTK_LABEL(context_label), 0.0F);
  GtkWidget *edit_result_label = gtk_label_new("编辑结果");
  gtk_label_set_xalign(GTK_LABEL(edit_result_label), 0.0F);
  gtk_box_pack_start(GTK_BOX(edit_card), context_label, FALSE, FALSE, 12);
  gtk_box_pack_start(GTK_BOX(edit_card), edit_source_scroll, FALSE, FALSE, 12);
  gtk_box_pack_start(
      GTK_BOX(edit_card),
      sui::make_row("请对麦克风说",
                    "此文本仅作为口述提示；真正的指令来自麦克风录音与 ASR。",
                    GTK_WIDGET(window.playground_edit_instruction)),
      FALSE, FALSE, 0);
  gtk_box_pack_start(GTK_BOX(edit_card), sui::make_row("范例", "", examples),
                     FALSE, FALSE, 0);
  gtk_box_pack_start(GTK_BOX(edit_card), edit_actions, FALSE, FALSE, 12);
  gtk_box_pack_start(GTK_BOX(edit_card), edit_result_label, FALSE, FALSE, 12);
  gtk_box_pack_start(GTK_BOX(edit_card), edit_result_scroll, FALSE, FALSE, 12);
  gtk_box_pack_start(GTK_BOX(window.playground_ai_controls), edit_card, FALSE,
                     FALSE, 0);
  gtk_box_pack_start(GTK_BOX(page.content), window.playground_ai_controls,
                     FALSE, FALSE, 0);

  g_signal_connect_swapped(
      refresh, "clicked",
      G_CALLBACK(+[](SettingsWindow *self) { refresh_devices(*self); }),
      &window);
  g_signal_connect(window.playground_audio_device, "changed",
                   G_CALLBACK(+[](GtkComboBox *box, gpointer data) {
                     auto *self = static_cast<SettingsWindow *>(data);
                     gtk_combo_box_set_active(GTK_COMBO_BOX(self->audio_device),
                                              gtk_combo_box_get_active(box));
                   }),
                   &window);
  g_signal_connect(window.playground_audio_rate, "value-changed",
                   G_CALLBACK(+[](GtkSpinButton *spin, gpointer data) {
                     auto *self = static_cast<SettingsWindow *>(data);
                     gtk_spin_button_set_value(self->audio_rate,
                                               gtk_spin_button_get_value(spin));
                   }),
                   &window);
  g_signal_connect_swapped(
      record, "clicked", G_CALLBACK(+[](SettingsWindow *self) {
        try {
          save_config(*self);
        } catch (const std::exception &error) {
          set_label(self->playground_status,
                    std::string("保存音频设置失败：") + error.what());
          return;
        }
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
      polish_button, "clicked", G_CALLBACK(+[](SettingsWindow *self) {
        const std::string source =
            text_view_text(self->playground_polish_source);
        if (source.empty()) {
          set_label(self->playground_polish_status, "请输入待润色文本");
          return;
        }
        set_label(self->playground_polish_status, "⏳ 正在调用 AI 润色…");
        run_async(
            [source] {
              if (!vocotype::desktop::ensure_native_core())
                return Json{{"success", false}, {"error", "Core 未启动"}};
              return vocotype::desktop::unix_json_request(
                  vocotype::desktop::backend_socket_path(),
                  {{"type", "polish_text"}, {"text", source}}, 180000);
            },
            [self](Json result) {
              set_text(self->playground_polish_result,
                       result.value("text", result.value("error", "失败")));
              set_label(self->playground_polish_status,
                        result.value("success", false) ? "✅ 润色完成"
                                                       : "❌ 润色失败");
            });
      }),
      &window);
  g_signal_connect_swapped(
      edit_button, "clicked", G_CALLBACK(+[](SettingsWindow *self) {
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
                        "✅ 语音编辑完成；计划：" + mode);
            });
      }),
      &window);
  return page.scroller;
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
  const auto page = sui::make_page(
      "反馈", "可直接发送给 VoCoType 维护者，也可以创建公开 GitHub "
              "Issue。诊断信息默认不附带，发送前请检查内容。");

  GtkWidget *form_card = sui::make_card();
  window.feedback_category = GTK_COMBO_BOX_TEXT(gtk_combo_box_text_new());
  gtk_combo_box_text_append(window.feedback_category, "bug", "问题 / Bug");
  gtk_combo_box_text_append(window.feedback_category, "installation",
                            "安装与升级");
  gtk_combo_box_text_append(window.feedback_category, "compatibility",
                            "兼容性");
  gtk_combo_box_text_append(window.feedback_category, "usability", "易用性");
  gtk_combo_box_text_append(window.feedback_category, "feature", "功能建议");
  gtk_combo_box_text_append(window.feedback_category, "other", "其他");
  gtk_combo_box_set_active_id(GTK_COMBO_BOX(window.feedback_category), "bug");
  window.feedback_contact = GTK_ENTRY(sui::make_entry());
  gtk_entry_set_placeholder_text(window.feedback_contact,
                                 "可选：邮箱或 GitHub 用户名");
  gtk_box_pack_start(GTK_BOX(form_card),
                     sui::make_row("反馈类型", "用于维护者分类和合并重复报告。",
                                   GTK_WIDGET(window.feedback_category)),
                     FALSE, FALSE, 0);
  gtk_box_pack_start(
      GTK_BOX(form_card),
      sui::make_row("联系方式",
                    "不填写也可匿名提交；不填写时维护者无法追问复现细节。",
                    GTK_WIDGET(window.feedback_contact)),
      FALSE, FALSE, 0);

  GtkWidget *message_box = gtk_box_new(GTK_ORIENTATION_VERTICAL, 6);
  gtk_container_set_border_width(GTK_CONTAINER(message_box), 14);
  GtkWidget *message_title = gtk_label_new("反馈内容");
  gtk_label_set_xalign(GTK_LABEL(message_title), 0.0F);
  sui::add_class(message_title, "row-title");
  GtkWidget *message_hint = gtk_label_new(
      "请写明发生了什么、如何复现，以及你期望的结果。最多 10,000 字。");
  gtk_label_set_xalign(GTK_LABEL(message_hint), 0.0F);
  gtk_label_set_line_wrap(GTK_LABEL(message_hint), TRUE);
  sui::add_class(message_hint, "row-subtitle");
  GtkWidget *feedback_scroll =
      sui::make_scrolled_text(&window.feedback_view, 200);
  gtk_box_pack_start(GTK_BOX(message_box), message_title, FALSE, FALSE, 0);
  gtk_box_pack_start(GTK_BOX(message_box), message_hint, FALSE, FALSE, 0);
  gtk_box_pack_start(GTK_BOX(message_box), feedback_scroll, FALSE, FALSE, 0);
  gtk_box_pack_start(GTK_BOX(form_card), message_box, FALSE, FALSE, 0);
  gtk_box_pack_start(GTK_BOX(page.content), form_card, FALSE, FALSE, 0);

  GtkWidget *privacy_card = sui::make_card();
  window.feedback_include_doctor =
      GTK_CHECK_BUTTON(gtk_check_button_new_with_label("附带 Doctor 结果"));
  window.feedback_include_bundle =
      GTK_CHECK_BUTTON(gtk_check_button_new_with_label(
          "附带脱敏支持包（最大 5 MiB，默认关闭）"));
  gtk_box_pack_start(
      GTK_BOX(privacy_card),
      sui::make_row("诊断信息",
                    "Doctor 和支持包都不会自动附带。支持包不含原始录音、API "
                    "Key 或词典正文，但仍应自行检查。",
                    GTK_WIDGET(window.feedback_include_doctor)),
      FALSE, FALSE, 0);
  gtk_box_pack_start(GTK_BOX(privacy_card),
                     sui::make_row("支持包",
                                   "包含脱敏配置、Doctor、服务日志与输入法诊断"
                                   "；服务器附件默认私有保存。",
                                   GTK_WIDGET(window.feedback_include_bundle)),
                     FALSE, FALSE, 0);
  gtk_box_pack_start(GTK_BOX(page.content), privacy_card, FALSE, FALSE, 0);

  GtkWidget *advanced = gtk_expander_new("高级：使用自托管反馈服务器");
  GtkWidget *advanced_box = gtk_box_new(GTK_ORIENTATION_VERTICAL, 8);
  gtk_container_set_border_width(GTK_CONTAINER(advanced_box), 12);
  window.feedback_custom_endpoint =
      GTK_CHECK_BUTTON(gtk_check_button_new_with_label("启用自定义端点"));
  window.feedback_endpoint = GTK_ENTRY(sui::make_entry());
  gtk_entry_set_placeholder_text(window.feedback_endpoint,
                                 "https://example.org/v1/feedback");
  gtk_entry_set_text(
      window.feedback_endpoint,
      "https://feedback.vocotype-linux.lsamc.website/v1/feedback");
  gtk_widget_set_sensitive(GTK_WIDGET(window.feedback_endpoint), FALSE);
  gtk_box_pack_start(GTK_BOX(advanced_box),
                     GTK_WIDGET(window.feedback_custom_endpoint), FALSE, FALSE,
                     0);
  gtk_box_pack_start(
      GTK_BOX(advanced_box),
      sui::make_row("自定义端点",
                    "仅供企业、发行版或 fork 使用；普通用户应使用官方端点。",
                    GTK_WIDGET(window.feedback_endpoint)),
      FALSE, FALSE, 0);
  gtk_container_add(GTK_CONTAINER(advanced), advanced_box);
  gtk_box_pack_start(GTK_BOX(page.content), advanced, FALSE, FALSE, 0);

  GtkWidget *actions = sui::make_button_row();
  GtkWidget *send = gtk_button_new_with_label("发送给 VoCoType 维护者");
  GtkWidget *github = gtk_button_new_with_label("在 GitHub 创建公开 Issue");
  sui::set_button_suggested(send);
  window.feedback_status = GTK_LABEL(sui::make_status_label(""));
  gtk_box_pack_start(GTK_BOX(actions), send, FALSE, FALSE, 0);
  gtk_box_pack_start(GTK_BOX(actions), github, FALSE, FALSE, 0);
  gtk_box_pack_start(GTK_BOX(actions), GTK_WIDGET(window.feedback_status), TRUE,
                     TRUE, 0);
  gtk_box_pack_start(GTK_BOX(page.content), actions, FALSE, FALSE, 0);

  g_signal_connect(window.feedback_custom_endpoint, "toggled",
                   G_CALLBACK(+[](GtkToggleButton *button, gpointer data) {
                     auto *self = static_cast<SettingsWindow *>(data);
                     gtk_widget_set_sensitive(
                         GTK_WIDGET(self->feedback_endpoint),
                         gtk_toggle_button_get_active(button));
                   }),
                   &window);
  g_signal_connect_swapped(
      send, "clicked", G_CALLBACK(+[](SettingsWindow *self) {
        const std::string message = text_view_text(self->feedback_view);
        const char *category_id =
            gtk_combo_box_get_active_id(GTK_COMBO_BOX(self->feedback_category));
        const std::string category = category_id ? category_id : "other";
        const std::string contact = gtk_entry_get_text(self->feedback_contact);
        const std::string endpoint =
            gtk_toggle_button_get_active(
                GTK_TOGGLE_BUTTON(self->feedback_custom_endpoint))
                ? gtk_entry_get_text(self->feedback_endpoint)
                : "https://feedback.vocotype-linux.lsamc.website/v1/feedback";
        const bool include_doctor = gtk_toggle_button_get_active(
            GTK_TOGGLE_BUTTON(self->feedback_include_doctor));
        const bool include_bundle = gtk_toggle_button_get_active(
            GTK_TOGGLE_BUTTON(self->feedback_include_bundle));
        if (message.empty()) {
          set_label(self->feedback_status, "请先填写反馈内容。");
          return;
        }
        set_label(self->feedback_status, "正在发送反馈…");
        run_async(
            [category, contact, message, endpoint, include_doctor,
             include_bundle] {
              const std::string report = include_doctor ? doctor_report() : "";
              std::filesystem::path bundle;
              if (include_bundle) {
                const Json generated = create_support_bundle(doctor_report());
                if (!generated.value("success", false))
                  return generated;
                bundle = generated.value("path", "");
              }
              Json result = submit_feedback(endpoint, category, contact,
                                            message, report, bundle);
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
          set_label(self->feedback_status, "请先填写反馈内容。");
          return;
        }
        const char *category_id =
            gtk_combo_box_get_active_id(GTK_COMBO_BOX(self->feedback_category));
        std::string body =
            "类别：" + std::string(category_id ? category_id : "other") +
            "\n联系方式：" + gtk_entry_get_text(self->feedback_contact) +
            "\n\n" + message;
        if (gtk_toggle_button_get_active(
                GTK_TOGGLE_BUTTON(self->feedback_include_doctor)))
          body +=
              "\n\n<details><summary>VoCoType Doctor</summary>\n\n```text\n" +
              doctor_report() + "\n```\n</details>";
        const std::string uri =
            "https://github.com/LeonardNJU/VocoType-linux/issues/new?labels="
            "feedback&title=" +
            uri_escape("[Feedback] ") + "&body=" + uri_escape(body);
        std::string error;
        if (!open_uri(GTK_WINDOW(self->window), uri, error))
          set_label(self->feedback_status, "打开 GitHub 失败：" + error);
      }),
      &window);
  return page.scroller;
}

void clear_container(GtkContainer *container) {
  GList *children = gtk_container_get_children(container);
  for (GList *node = children; node != nullptr; node = node->next)
    gtk_widget_destroy(GTK_WIDGET(node->data));
  g_list_free(children);
}

void render_doctor(SettingsWindow &window, const std::string &report) {
  if (window.doctor_output)
    set_text(window.doctor_output, report);
  if (!window.doctor_list)
    return;
  clear_container(GTK_CONTAINER(window.doctor_list));
  std::istringstream input(report);
  std::string line;
  int passed = 0;
  int failed = 0;
  while (std::getline(input, line)) {
    if (line.rfind("[PASS] ", 0) != 0 && line.rfind("[FAIL] ", 0) != 0)
      continue;
    const bool success = line.rfind("[PASS] ", 0) == 0;
    std::string content = line.substr(7);
    const std::size_t separator = content.find(" — ");
    const std::string title =
        separator == std::string::npos ? content : content.substr(0, separator);
    const std::string details =
        separator == std::string::npos ? "" : content.substr(separator + 5);
    GtkWidget *card = sui::make_card();
    GtkWidget *status = gtk_label_new(success ? "通过" : "需要处理");
    sui::add_class(status, success ? "status-pass" : "status-fail");
    gtk_box_pack_start(GTK_BOX(card),
                       sui::make_row(title.c_str(), details.c_str(), status),
                       FALSE, FALSE, 0);
    gtk_box_pack_start(window.doctor_list, card, FALSE, FALSE, 0);
    success ? ++passed : ++failed;
  }
  const std::string summary =
      failed == 0 ? "✅ " + std::to_string(passed) + " 项检查全部通过"
                  : "⚠️ " + std::to_string(passed) + " 项通过，" +
                        std::to_string(failed) + " 项需要处理";
  if (window.doctor_summary)
    set_label(window.doctor_summary, summary);
  if (window.overview_summary)
    set_label(window.overview_summary, summary);
  gtk_widget_show_all(GTK_WIDGET(window.doctor_list));
}

GtkWidget *build_doctor(SettingsWindow &window) {
  const auto page =
      sui::make_page("诊断", "自动检查常见安装与运行问题。仍无法解决时，可生成"
                             "不含录音和凭据的支持包。");

  GtkWidget *version_card = sui::make_card();
  window.version_status = GTK_LABEL(
      sui::make_status_label((std::string("当前版本：") + VOCOTYPE_VERSION +
                              "；尚未查询 GitHub 最新 release。")
                                 .c_str()));
  GtkWidget *latest = gtk_button_new_with_label("查询 GitHub 最新版本");
  gtk_box_pack_start(
      GTK_BOX(version_card),
      sui::make_row(
          "版本与远程完整性",
          "查询 release 版本；本地 native payload 仍由 SHA-256 清单独立校验。",
          latest),
      FALSE, FALSE, 0);
  gtk_box_pack_start(
      GTK_BOX(version_card),
      sui::make_row("查询结果", "", GTK_WIDGET(window.version_status)), FALSE,
      FALSE, 0);
  gtk_box_pack_start(GTK_BOX(page.content), version_card, FALSE, FALSE, 0);

  GtkWidget *actions = sui::make_button_row();
  GtkWidget *run = gtk_button_new_with_label("运行 Doctor");
  GtkWidget *bundle = gtk_button_new_with_label("导出支持包");
  GtkWidget *open_support = gtk_button_new_with_label("打开支持目录");
  GtkWidget *github = gtk_button_new_with_label("在 GitHub 创建 Issue");
  sui::set_button_suggested(run);
  window.doctor_summary = GTK_LABEL(sui::make_status_label("尚未运行检查"));
  gtk_box_pack_start(GTK_BOX(actions), run, FALSE, FALSE, 0);
  gtk_box_pack_start(GTK_BOX(actions), bundle, FALSE, FALSE, 0);
  gtk_box_pack_start(GTK_BOX(actions), open_support, FALSE, FALSE, 0);
  gtk_box_pack_start(GTK_BOX(actions), github, FALSE, FALSE, 0);
  gtk_box_pack_start(GTK_BOX(actions), GTK_WIDGET(window.doctor_summary), TRUE,
                     TRUE, 0);
  gtk_box_pack_start(GTK_BOX(page.content), actions, FALSE, FALSE, 0);

  window.doctor_list = GTK_BOX(gtk_box_new(GTK_ORIENTATION_VERTICAL, 8));
  gtk_box_pack_start(GTK_BOX(page.content), GTK_WIDGET(window.doctor_list),
                     FALSE, FALSE, 0);

  GtkWidget *raw_expander = gtk_expander_new("高级：查看原始 Doctor 输出");
  GtkWidget *raw_scroll =
      sui::make_scrolled_text(&window.doctor_output, 260, true, GTK_WRAP_NONE);
  gtk_text_view_set_editable(window.doctor_output, FALSE);
  gtk_container_add(GTK_CONTAINER(raw_expander), raw_scroll);
  gtk_box_pack_start(GTK_BOX(page.content), raw_expander, FALSE, FALSE, 0);
  window.support_status = GTK_LABEL(sui::make_status_label("尚未生成支持包"));
  gtk_box_pack_start(GTK_BOX(page.content), GTK_WIDGET(window.support_status),
                     FALSE, FALSE, 0);

  g_signal_connect_swapped(run, "clicked",
                           G_CALLBACK(+[](SettingsWindow *self) {
                             render_doctor(*self, doctor_report());
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
        render_doctor(*self, report);
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
        render_doctor(*self, report);
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
  return page.scroller;
}

GtkWidget *build_tutorial(SettingsWindow &window) {
  const auto page =
      sui::make_page("教程", "教程会根据“概览与安装”中的框架选择自动切换。");
  window.tutorial_title =
      GTK_LABEL(gtk_label_new("当前教程：Fcitx 5（全局 Module）"));
  gtk_label_set_xalign(window.tutorial_title, 0.0F);
  sui::add_class(GTK_WIDGET(window.tutorial_title), "section-title");
  gtk_box_pack_start(GTK_BOX(page.content), GTK_WIDGET(window.tutorial_title),
                     FALSE, FALSE, 0);
  window.tutorial_stack = GTK_STACK(gtk_stack_new());
  gtk_stack_set_transition_type(window.tutorial_stack,
                                GTK_STACK_TRANSITION_TYPE_CROSSFADE);
  gtk_stack_set_transition_duration(window.tutorial_stack, 120);

  auto tutorial_panel =
      [](const std::vector<std::pair<std::string, std::string>> &steps) {
        GtkWidget *card = sui::make_card();
        for (const auto &[title, description] : steps)
          gtk_box_pack_start(GTK_BOX(card),
                             sui::make_row(title.c_str(), description.c_str()),
                             FALSE, FALSE, 0);
        return card;
      };
  const std::vector<std::pair<std::string, std::string>> ibus_steps = {
      {"1. 安装 IBus 集成",
       "在“概览与安装”选择 IBus，执行安装 / 修复，并重启 IBus。"},
      {"2. 添加并切换输入法",
       "在系统“键盘 / 输入源”中添加 VoCoType；IBus 版本不是全局插件。"},
      {"3. 选择文本输入方案",
       "可以启用 VoCoType 内置 Rime，也可以只把它作为语音输入引擎使用。"},
      {"4. Playground 验证",
       "先测试麦克风、回放与真实 ASR；AI 功能需先完成端点 / 模型测活。"},
      {"5. 使用语音功能",
       "按住 F9 普通识别，Shift+F9 润色，Ctrl+F9 语音编辑。"},
      {"6. 排障",
       "F9 无响应时运行 Doctor，检查 IBus component、引擎进程与输入源。"},
  };
  const std::vector<std::pair<std::string, std::string>> fcitx_steps = {
      {"1. 安装 Fcitx 5 集成",
       "在“概览与安装”选择 Fcitx 5，执行安装 / 修复，并重启 Fcitx 5。"},
      {"2. 不要添加 VoCoType 输入法",
       "VoCoType 是全局 Module。继续使用现有输入法，直接按 F9 即可。"},
      {"3. Playground 验证",
       "先测试麦克风、回放与真实 ASR；AI 功能需先完成端点 / 模型测活。"},
      {"4. 使用语音功能",
       "按住 F9 普通识别，Shift+F9 润色，Ctrl+F9 语音编辑。"},
      {"5. 添加术语",
       "在用户词典加入项目名、人名和专业术语；hotword 提升识别概率。"},
      {"6. 排障",
       "F9 无响应时运行 Doctor，检查全局 Module、后台服务和桌面会话环境。"},
  };
  gtk_stack_add_titled(window.tutorial_stack, tutorial_panel(ibus_steps),
                       "ibus", "IBus");
  gtk_stack_add_titled(window.tutorial_stack, tutorial_panel(fcitx_steps),
                       "fcitx5", "Fcitx 5");
  gtk_box_pack_start(GTK_BOX(page.content), GTK_WIDGET(window.tutorial_stack),
                     FALSE, TRUE, 0);
  return page.scroller;
}

void show_settings_message(SettingsWindow &window, GtkMessageType type,
                           const std::string &primary,
                           const std::string &secondary = "") {
  GtkWidget *dialog =
      gtk_message_dialog_new(GTK_WINDOW(window.window), GTK_DIALOG_MODAL, type,
                             GTK_BUTTONS_OK, "%s", primary.c_str());
  if (!secondary.empty())
    gtk_message_dialog_format_secondary_text(GTK_MESSAGE_DIALOG(dialog), "%s",
                                             secondary.c_str());
  gtk_dialog_run(GTK_DIALOG(dialog));
  gtk_widget_destroy(dialog);
}

void activate(GtkApplication *application, gpointer user_data) {
  auto *window = static_cast<SettingsWindow *>(user_data);
  window->application = application;
  sui::apply_css();
  window->window = gtk_application_window_new(application);
  gtk_window_set_title(GTK_WINDOW(window->window), "VoCoType 设置");
  gtk_window_set_default_size(GTK_WINDOW(window->window), 1120, 760);
  gtk_widget_set_size_request(window->window, 900, 620);

  GtkWidget *header = gtk_header_bar_new();
  gtk_header_bar_set_show_close_button(GTK_HEADER_BAR(header), TRUE);
  gtk_header_bar_set_title(GTK_HEADER_BAR(header), "VoCoType");
  gtk_header_bar_set_subtitle(
      GTK_HEADER_BAR(header),
      (std::string("语音输入设置 · ") + VOCOTYPE_VERSION).c_str());
  GtkWidget *save = gtk_button_new_with_label("保存设置");
  sui::set_button_suggested(save);
  gtk_header_bar_pack_end(GTK_HEADER_BAR(header), save);
  gtk_window_set_titlebar(GTK_WINDOW(window->window), header);

  GtkWidget *root = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 0);
  gtk_container_add(GTK_CONTAINER(window->window), root);
  window->stack = GTK_STACK(gtk_stack_new());
  gtk_stack_set_transition_type(window->stack,
                                GTK_STACK_TRANSITION_TYPE_CROSSFADE);
  gtk_stack_set_transition_duration(window->stack, 160);
  gtk_widget_set_hexpand(GTK_WIDGET(window->stack), TRUE);
  gtk_widget_set_vexpand(GTK_WIDGET(window->stack), TRUE);
  GtkWidget *sidebar = gtk_stack_sidebar_new();
  gtk_stack_sidebar_set_stack(GTK_STACK_SIDEBAR(sidebar), window->stack);
  gtk_widget_set_size_request(sidebar, 220, -1);
  sui::add_class(sidebar, "sidebar");
  gtk_box_pack_start(GTK_BOX(root), sidebar, FALSE, FALSE, 0);
  gtk_box_pack_start(GTK_BOX(root), GTK_WIDGET(window->stack), TRUE, TRUE, 0);

  GtkWidget *overview = build_overview(*window);
  GtkWidget *general = build_recognition(*window);
  GtkWidget *terms = build_terms(*window);
  GtkWidget *slm = build_slm(*window);
  GtkWidget *playground = build_playground(*window);
  GtkWidget *doctor = build_doctor(*window);
  GtkWidget *tutorial = build_tutorial(*window);
  GtkWidget *feedback = build_feedback(*window);

  gtk_stack_add_titled(window->stack, overview, "overview", "概览与安装");
  gtk_stack_add_titled(window->stack, general, "general", "通用设置");
  gtk_stack_add_titled(window->stack, playground, "playground", "Playground");
  gtk_stack_add_titled(window->stack, terms, "terms", "用户词典");
  gtk_stack_add_titled(window->stack, slm, "slm", "AI 功能");
  gtk_stack_add_titled(window->stack, doctor, "doctor", "诊断");
  gtk_stack_add_titled(window->stack, tutorial, "tutorial", "教程");
  gtk_stack_add_titled(window->stack, feedback, "feedback", "反馈");

  populate_from_config(*window);
  const bool ai_enabled = gtk_switch_get_active(window->slm_enabled);
  gtk_widget_set_sensitive(window->playground_ai_controls, ai_enabled);
  set_label(window->playground_ai_gate_status,
            ai_enabled ? "AI 功能已启用；建议先在“AI 功能”页执行一次真实测活。"
                       : "AI 功能尚未启用。请先配置端点和模型并完成测活。");
  apply_framework_selection(*window, selected_framework(*window));
  gtk_stack_set_visible_child_name(window->stack, "overview");

  g_signal_connect_swapped(
      save, "clicked", G_CALLBACK(+[](SettingsWindow *self) {
        try {
          save_config(*self);
          (void)run_command({"fcitx5-remote", "-r"});
          const bool ready = restart_core_for_settings();
          show_settings_message(
              *self, GTK_MESSAGE_INFO, "设置已保存",
              ready ? "通用设置、AI 配置和当前输入法框架选项已应用。"
                    : "配置已写入；Core 将在下一次 F9 时按需启动。");
        } catch (const std::exception &error) {
          show_settings_message(*self, GTK_MESSAGE_ERROR, "保存设置失败",
                                error.what());
        }
      }),
      window);
  g_signal_connect(window->stack, "notify::visible-child-name",
                   G_CALLBACK(+[](GObject *, GParamSpec *, gpointer data) {
                     auto *self = static_cast<SettingsWindow *>(data);
                     const char *name =
                         gtk_stack_get_visible_child_name(self->stack);
                     if (name && std::string(name) == "overview")
                       refresh_overview(*self);
                   }),
                   window);

  gtk_widget_show_all(window->window);
  if (const char *probe = std::getenv("VOCOTYPE_SETTINGS_UI_PROBE");
      probe && *probe) {
    const std::string framework =
        std::string(probe) == "ibus" ? "ibus" : "fcitx5";
    apply_framework_selection(*window, framework);
    while (gtk_events_pending())
      gtk_main_iteration_do(FALSE);
    GtkTreeModel *schema_model =
        gtk_combo_box_get_model(GTK_COMBO_BOX(window->rime_schema));
    const int schema_count =
        schema_model ? gtk_tree_model_iter_n_children(schema_model, nullptr)
                     : 0;
    const auto visible = [](GtkWidget *widget) {
      return widget && gtk_widget_get_visible(widget);
    };
    Json result{
        {"framework", framework},
        {"rime_resource_visible", visible(window->rime_resource_row)},
        {"rime_schema_visible", visible(window->rime_schema_row)},
        {"fcitx_panel_visible", visible(window->fcitx_panel_card)},
        {"fcitx_output_visible", visible(window->fcitx_output_card)},
        {"fcitx_composing_visible", visible(window->fcitx_composing_row)},
        {"rime_schema_count", schema_count},
    };
    std::cout << result.dump() << std::endl;
    g_application_quit(G_APPLICATION(application));
  }
}

} // namespace

int main(int argc, char **argv) {
  const bool probing = std::getenv("VOCOTYPE_SETTINGS_UI_PROBE") != nullptr;
  GtkApplication *application = gtk_application_new(
      "io.github.LeonardNJU.VoCoType.Settings",
      probing ? G_APPLICATION_NON_UNIQUE : G_APPLICATION_DEFAULT_FLAGS);
  auto window = std::make_unique<SettingsWindow>();
  g_signal_connect(application, "activate", G_CALLBACK(activate), window.get());
  const int status = g_application_run(G_APPLICATION(application), argc, argv);
  if (!window->last_recording.empty())
    std::filesystem::remove(window->last_recording);
  g_object_unref(application);
  return status;
}
