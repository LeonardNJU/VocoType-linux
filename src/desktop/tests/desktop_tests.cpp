#include "vocotype/common/terms_yaml.hpp"
#include "vocotype/desktop/audio.hpp"
#include "vocotype/desktop/config.hpp"
#include "vocotype/desktop/fcitx_profile.hpp"
#include "vocotype/desktop/hotkey.hpp"
#include "vocotype/desktop/ipc.hpp"
#include "vocotype/desktop/streaming_preview.hpp"
#include "vocotype/desktop/wav.hpp"
#include <cassert>
#include <cstdio>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <string>
#include <sys/stat.h>
#include <unistd.h>
int main() {
  using namespace vocotype::desktop;
  const auto require = [](bool condition, const char *message) {
    if (!condition) {
      std::cerr << "FAIL: " << message << "\n";
      std::exit(1);
    }
  };
  int stderr_pipe[2]{};
  require(pipe(stderr_pipe) == 0, "create stderr capture pipe");
  const int saved_stderr = dup(STDERR_FILENO);
  require(saved_stderr >= 0, "duplicate stderr");
  std::fflush(stderr);
  require(dup2(stderr_pipe[1], STDERR_FILENO) >= 0, "capture stderr");
  close(stderr_pipe[1]);
  (void)list_audio_devices();
  std::fflush(stderr);
  require(dup2(saved_stderr, STDERR_FILENO) >= 0, "restore stderr");
  close(saved_stderr);
  std::string audio_probe_stderr;
  char audio_probe_buffer[4096];
  ssize_t audio_probe_bytes = 0;
  while ((audio_probe_bytes = read(stderr_pipe[0], audio_probe_buffer,
                                   sizeof(audio_probe_buffer))) > 0) {
    audio_probe_stderr.append(audio_probe_buffer,
                              static_cast<std::size_t>(audio_probe_bytes));
  }
  close(stderr_pipe[0]);
  require(audio_probe_stderr.empty(),
          "audio enumeration must not emit ALSA/JACK probe noise");

  const std::vector<std::int16_t> source{0, 1000, 2000, 3000};
  const auto doubled = resample_linear(source, 4, 8);
  assert(doubled.size() == 8);
  assert(doubled.front() == 0 && doubled.back() == 3000);
  const std::string encoded =
      base64_encode(reinterpret_cast<const unsigned char *>("abc"), 3);
  assert(encoded == "YWJj");

  StreamingPreviewTranscript preview;
  const auto first_preview = preview.update_session_text("你好世界");
  require(first_preview.has_value() && *first_preview == "你好世界",
          "first streaming preview was not accepted");
  require(!preview.update_session_text("   \n\t").has_value(),
          "whitespace-only streaming preview was not ignored");
  require(preview.display_text() == "你好世界",
          "whitespace-only preview erased valid live text");
  preview.begin_recovery();
  const auto replayed_preview = preview.update_session_text("世界继续说话");
  require(replayed_preview.has_value() &&
              *replayed_preview == "你好世界继续说话",
          "recovered streaming preview did not deduplicate overlap");
  preview.begin_recovery();
  const auto continued_preview = preview.update_session_text("说话然后补充第二句");
  require(continued_preview.has_value() &&
              *continued_preview == "你好世界继续说话然后补充第二句",
          "recovered streaming preview did not preserve committed prefix");
  require(!preview.update_session_text("\r\n  ").has_value() &&
              preview.display_text() == "你好世界继续说话然后补充第二句",
          "blank post-recovery preview replaced the accumulated transcript");
  preview.reset();
  require(!preview.has_text() && preview.display_text().empty(),
          "streaming preview reset retained stale text");

  const Hotkey normal = parse_hotkey("F9");
  const Hotkey polish = parse_hotkey("Shift+F9");
  const Hotkey edit = parse_hotkey("Ctrl+Alt_R");
  assert(normal.valid() && hotkey_to_string(normal) == "F9");
  assert(polish.valid() && hotkey_to_string(polish) == "Shift+F9");
  assert(edit.valid() && hotkey_to_string(edit) == "Ctrl+Alt_R");
  assert(hotkey_matches(polish, GDK_KEY_F9, GDK_SHIFT_MASK));
  assert(!hotkey_matches(polish, GDK_KEY_F9, 0));
  const Hotkey right_alt = parse_hotkey("Alt_R");
  assert(right_alt.valid());
  assert(hotkey_matches(right_alt, GDK_KEY_Alt_R, 0));
  assert(hotkey_matches(right_alt, GDK_KEY_Alt_R, GDK_MOD1_MASK));
  assert(hotkeys_equal(parse_hotkey("Control+F8"), parse_hotkey("Ctrl+F8")));
  assert(hotkey_to_string(parse_hotkey("not-a-real-key", normal)) == "F9");
  assert(hotkey_safety_error(parse_hotkey("a")).find("裸字母") !=
         std::string::npos);
  assert(hotkey_safety_error(parse_hotkey("Shift+7")).find("裸字母") !=
         std::string::npos);
  assert(hotkey_safety_error(parse_hotkey("Left")).find("基础输入") !=
         std::string::npos);
  assert(hotkey_safety_error(parse_hotkey("Ctrl+C")).find("常用系统") !=
         std::string::npos);
  assert(hotkey_safety_error(parse_hotkey("Alt_R")).empty());
  assert(hotkey_safety_error(parse_hotkey("F8")).empty());
  assert(hotkey_safety_error(parse_hotkey("Ctrl+Shift+F8")).empty());

  const auto terms_document =
      vocotype::common::parse_terms_yaml_content(R"(terms:
  - canonical: Ghostty
    aliases:
      - 鬼斯提
      - "ghost ty"
    hotwords:
      - Ghostty
    protect: true
replace:
  NodeJS:
    - node js
protect:
  - 一百米计划
)");
  assert(terms_document.terms.size() == 2);
  assert(terms_document.terms[0].canonical == "Ghostty");
  assert(terms_document.terms[0].aliases.size() == 2);
  assert(terms_document.terms[0].hotwords.size() == 1);
  assert(terms_document.terms[1].canonical == "NodeJS");
  assert(terms_document.terms[1].aliases.size() == 1);
  assert(terms_document.protected_phrases.size() == 1);
  bool invalid_terms_rejected = false;
  try {
    (void)vocotype::common::parse_terms_yaml_content("terms: [");
  } catch (const std::exception &) {
    invalid_terms_rejected = true;
  }
  assert(invalid_terms_rejected);

  const auto yaml_test_root =
      std::filesystem::temp_directory_path() /
      ("vocotype-yaml-test-" + std::to_string(getpid()));
  std::filesystem::remove_all(yaml_test_root);
  std::filesystem::create_directories(yaml_test_root);
  const auto schema_path = yaml_test_root / "rime_frost.schema.yaml";
  {
    std::ofstream output(schema_path);
    output << "schema:\n"
              "  schema_id: rime_frost_double_pinyin\n"
              "  name: '白霜拼音 · 双拼' # display label\n";
  }
  const auto schema =
      vocotype::common::parse_rime_schema_metadata(schema_path, "rime_frost");
  assert(schema.schema_id == "rime_frost_double_pinyin");
  assert(schema.name == "白霜拼音 · 双拼");
  std::filesystem::remove_all(yaml_test_root);

  const auto path = create_secure_wav_path();
  write_pcm16_wav(path, source, 16000);
  const auto decoded = read_pcm16_wav(path);
  assert(decoded.sample_rate == 16000);
  assert(decoded.channels == 1);
  assert(decoded.samples == source);
  std::ifstream input(path, std::ios::binary);
  char header[4]{};
  input.read(header, 4);
  assert(std::string(header, 4) == "RIFF");
  std::filesystem::remove(path);

  const Json addon_payload = {
      {"data", Json::array({Json::array({
                   Json::array({"keyboard", "Keyboard", "", "", "", true}),
                   Json::array({"vocotype", "VoCoType", "", "", "", false}),
               })})}};
  const auto addon_states = parse_fcitx_addon_states(addon_payload);
  assert(addon_states.has_value());
  assert(addon_states->at("keyboard"));
  assert(!addon_states->at("vocotype"));
  assert(!parse_fcitx_addon_states(Json::array()).has_value());

  const auto test_root =
      std::filesystem::temp_directory_path() /
      ("vocotype-fcitx-profile-test-" + std::to_string(getpid()));
  std::filesystem::remove_all(test_root);
  std::filesystem::create_directories(test_root);
  const auto profile = test_root / "profile";
  const std::string legacy_profile = "[Groups/0]\n"
                                     "Name=Default\n"
                                     "DefaultIM=vocotype\n"
                                     "\n"
                                     "[Groups/0/Items/0]\n"
                                     "Name=keyboard-us\n"
                                     "Layout=\n"
                                     "\n"
                                     "[Groups/0/Items/1]\n"
                                     "Name=vocotype\n"
                                     "Layout=\n"
                                     "\n"
                                     "[Groups/0/Items/2]\n"
                                     "Name=rime\n"
                                     "Layout=\n"
                                     "\n"
                                     "[Groups/1]\n"
                                     "Name=Empty\n"
                                     "DefaultIM=vocotype\n";
  {
    std::ofstream output(profile);
    output << legacy_profile;
  }
  chmod(profile.c_str(), 0600);
  const auto references = legacy_fcitx_profile_references(profile);
  assert(references.size() == 3);
  const auto migration = migrate_legacy_fcitx_profile(profile);
  assert(migration.changed);
  assert(migration.removed_entries == 1);
  assert(migration.restored_defaults.size() == 2);
  assert(std::filesystem::is_regular_file(migration.backup));
  std::ifstream backup_input(migration.backup);
  const std::string backup_text((std::istreambuf_iterator<char>(backup_input)),
                                std::istreambuf_iterator<char>());
  assert(backup_text == legacy_profile);
  std::ifstream migrated_input(profile);
  const std::string migrated_text(
      (std::istreambuf_iterator<char>(migrated_input)),
      std::istreambuf_iterator<char>());
  assert(migrated_text.find("DefaultIM=rime") != std::string::npos);
  assert(migrated_text.find("Name=vocotype") == std::string::npos);
  assert(migrated_text.find("[Groups/0/Items/2]") == std::string::npos);
  assert(migrated_text.find("[Groups/1/Items/0]") != std::string::npos);
  assert(migrated_text.find("Name=keyboard-us") != std::string::npos);
  assert(legacy_fcitx_profile_references(profile).empty());
  const auto second_migration = migrate_legacy_fcitx_profile(profile);
  assert(!second_migration.changed);
  struct stat migrated_stat{};
  assert(stat(profile.c_str(), &migrated_stat) == 0);
  assert((migrated_stat.st_mode & 0777) == 0600);
  std::filesystem::remove_all(test_root);

  const auto layout_root =
      std::filesystem::temp_directory_path() /
      ("vocotype-config-layout-test-" + std::to_string(getpid()));
  std::filesystem::remove_all(layout_root);
  std::filesystem::create_directories(layout_root / "vocotype");
  const char *old_xdg_raw = std::getenv("XDG_CONFIG_HOME");
  const char *old_custom_raw = std::getenv("VOCOTYPE_CONFIG");
  const std::optional<std::string> old_xdg =
      old_xdg_raw ? std::optional<std::string>(old_xdg_raw) : std::nullopt;
  const std::optional<std::string> old_custom =
      old_custom_raw ? std::optional<std::string>(old_custom_raw)
                     : std::nullopt;
  setenv("XDG_CONFIG_HOME", layout_root.c_str(), 1);
  unsetenv("VOCOTYPE_CONFIG");
  write_json_file_atomic(legacy_runtime_config_path(),
                         Json{{"audio", Json{{"sample_rate", 48000}}},
                              {"slm", Json{{"enabled", true}}},
                              {"hotkeys", Json{{"transcribe", "Alt_R"},
                                               {"polish", "Shift+F8"},
                                               {"edit", "Ctrl+F8"}}}});
  const auto layout_migration = migrate_config_layout();
  require(layout_migration.changed && layout_migration.shared_created &&
              layout_migration.ibus_normalized &&
              layout_migration.legacy_archived,
          "legacy runtime config migrates into role-specific files");
  const Json shared_config = read_json_file(shared_config_path(), false);
  require(!shared_config.contains("hotkeys"),
          "shared config must not contain runtime hotkeys");
  require(shared_config["audio"].value("sample_rate", 0) == 48000,
          "shared config preserves audio settings");
  const Json ibus_config = read_json_file(ibus_config_path(), false);
  require(ibus_config.size() == 1 && ibus_config.contains("hotkeys") &&
              ibus_config["hotkeys"].size() == 3 &&
              ibus_config["hotkeys"].value("transcribe", "") == "Alt_R" &&
              ibus_config["hotkeys"].value("polish", "") == "Shift+F8" &&
              ibus_config["hotkeys"].value("edit", "") == "Ctrl+F8",
          "IBus config contains exactly three migrated IBus hotkeys");
  require(!std::filesystem::exists(legacy_runtime_config_path()) &&
              std::filesystem::is_regular_file(
                  legacy_runtime_config_path().string() + ".migrated"),
          "legacy Fcitx backend JSON is archived after migration");
  require(runtime_config_path() == shared_config_path(),
          "runtime config resolves to the shared config");
  require(!migrate_config_layout().changed,
          "config layout migration is idempotent");

  {
    std::ofstream audio_override(audio_config_path());
    audio_override << "[audio]\n"
                      "device_id = 77\n"
                      "device_name = stale microphone\n"
                      "sample_rate = 44100\n";
  }
  const AudioConfig overridden_audio = load_audio_config();
  require(
      overridden_audio.device_id && *overridden_audio.device_id == 77 &&
          overridden_audio.device_name == "stale microphone" &&
          overridden_audio.sample_rate == 44100,
      "legacy audio.conf remains active before a modern device is selected");
  Json rewritten_shared = read_json_file(shared_config_path(), false);
  rewritten_shared["audio"] = Json{{"device", 8},
                                   {"device_name", "saved microphone"},
                                   {"sample_rate", 48000},
                                   {"block_ms", 20}};
  // Bypass write_shared_config once so the stale override is still present:
  // normal runtime loading must nevertheless prefer the modern selection.
  write_json_file_atomic(shared_config_path(), rewritten_shared);
  const AudioConfig modern_wins = load_audio_config();
  require(modern_wins.device_id && *modern_wins.device_id == 8 &&
              modern_wins.device_name == "saved microphone" &&
              modern_wins.sample_rate == 48000,
          "modern microphone selection ignores an implicit stale audio.conf");
  const AudioConfig explicit_override = load_audio_config(audio_config_path());
  require(explicit_override.device_id && *explicit_override.device_id == 77 &&
              explicit_override.device_name == "stale microphone" &&
              explicit_override.sample_rate == 44100,
          "explicitly requested legacy/headless audio override still works");
  write_shared_config(rewritten_shared);
  require(!std::filesystem::exists(audio_config_path()),
          "saving shared settings retires stale audio.conf override");
  const AudioConfig saved_audio = load_audio_config();
  require(
      saved_audio.device_id && *saved_audio.device_id == 8 &&
          saved_audio.device_name == "saved microphone" &&
          saved_audio.sample_rate == 48000,
      "saved shared microphone becomes effective after override retirement");
  if (old_xdg)
    setenv("XDG_CONFIG_HOME", old_xdg->c_str(), 1);
  else
    unsetenv("XDG_CONFIG_HOME");
  if (old_custom)
    setenv("VOCOTYPE_CONFIG", old_custom->c_str(), 1);
  else
    unsetenv("VOCOTYPE_CONFIG");
  std::filesystem::remove_all(layout_root);

  std::cout << "desktop tests passed\n";
}
