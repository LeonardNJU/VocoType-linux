#include "vocotype/desktop/audio.hpp"
#include "vocotype/desktop/fcitx_profile.hpp"
#include "vocotype/desktop/hotkey.hpp"
#include "vocotype/desktop/ipc.hpp"
#include "vocotype/desktop/wav.hpp"
#include <cassert>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <sys/stat.h>
#include <unistd.h>
int main() {
  using namespace vocotype::desktop;
  const std::vector<std::int16_t> source{0, 1000, 2000, 3000};
  const auto doubled = resample_linear(source, 4, 8);
  assert(doubled.size() == 8);
  assert(doubled.front() == 0 && doubled.back() == 3000);
  const std::string encoded =
      base64_encode(reinterpret_cast<const unsigned char *>("abc"), 3);
  assert(encoded == "YWJj");

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
  std::cout << "desktop tests passed\n";
}
