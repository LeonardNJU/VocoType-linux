#include "vocotype/common/terms_yaml.hpp"
#include "vocotype/desktop/config.hpp"
#include "vocotype/desktop/settings_backend.hpp"

#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <string>
#include <vector>
#include <sys/stat.h>
#include <unistd.h>

int main() {
  namespace settings = vocotype::desktop::settings;
  const auto require = [](bool condition, const char *message) {
    if (!condition) {
      std::cerr << "FAIL: " << message << '\n';
      std::exit(1);
    }
  };

  const auto root = std::filesystem::temp_directory_path() /
                    ("vocotype-settings-backend-test-" +
                     std::to_string(::getpid()));
  std::filesystem::remove_all(root);
  std::filesystem::create_directories(root);

  const char *old_xdg_raw = std::getenv("XDG_CONFIG_HOME");
  const char *old_config_raw = std::getenv("VOCOTYPE_CONFIG");
  const std::string old_xdg = old_xdg_raw ? old_xdg_raw : "";
  const std::string old_config = old_config_raw ? old_config_raw : "";
  setenv("XDG_CONFIG_HOME", root.c_str(), 1);
  unsetenv("VOCOTYPE_CONFIG");

  const auto process =
      settings::run_process({"/bin/sh", "-c", "printf settings-backend-ok"});
  require(process.value("success", false), "process helper succeeds");
  require(process.value("output", "") == "settings-backend-ok",
          "process helper captures output exactly");

  const std::string terms = R"(# 保留这条用户注释
terms:
  - canonical: VoCoType-linux
    aliases:
      - vocotype linux
    hotwords:
      - VoCoType-linux
    protect: true
protect:
  - 一百米计划
)";
  const auto saved = settings::validate_and_save_terms(terms);
  require(saved.value("success", false), "valid terms save");
  require(saved.value("terms", 0U) == 1U, "term count reported");
  require(saved.value("protected_phrases", 0U) == 1U,
          "protected phrase count reported");
  require(settings::load_terms() == terms, "saved terms round-trip");
  struct stat terms_stat {};
  require(::stat(vocotype::desktop::terms_path().c_str(), &terms_stat) == 0,
          "terms file exists");
  require((terms_stat.st_mode & 0777) == 0600,
          "terms file permissions are private");

  const auto invalid = settings::validate_and_save_terms("terms: [");
  require(!invalid.value("success", true), "invalid terms rejected");
  require(settings::load_terms() == terms,
          "invalid terms do not overwrite last valid file");

  const auto appended = settings::append_term(
      "ChatGPT", {"chat gbt", "chat gbt", "ChatGPT"}, true, false);
  require(appended.value("success", false), "structured term append succeeds");
  require(appended.value("terms", 0U) == 2U,
          "structured term append reports updated count");
  require(appended.value("aliases", 0U) == 1U,
          "structured term append deduplicates aliases");
  const std::string after_append = settings::load_terms();
  require(after_append.find("# 保留这条用户注释") != std::string::npos,
          "structured append preserves existing comments");
  const auto appended_document =
      vocotype::common::parse_terms_yaml_content(after_append);
  require(appended_document.terms.size() == 2U,
          "structured append produces valid YAML");
  require(appended_document.terms.back().canonical == "ChatGPT",
          "structured append stores canonical spelling");
  require(appended_document.terms.back().aliases ==
              std::vector<std::string>{"chat gbt"},
          "structured append stores aliases");
  require(appended_document.terms.back().hotwords ==
              std::vector<std::string>{"ChatGPT"},
          "structured append enables canonical hotword");
  require(!appended_document.terms.back().protect,
          "structured append stores explicit protect=false");

  const auto duplicate =
      settings::append_term("ChatGPT", {}, true, true);
  require(!duplicate.value("success", true), "duplicate canonical rejected");
  require(settings::load_terms() == after_append,
          "duplicate canonical does not modify dictionary");

  const auto protected_added = settings::append_protected_phrase("三体问题");
  require(protected_added.value("success", false),
          "structured protected phrase append succeeds");
  const std::string after_protect = settings::load_terms();
  const auto protected_document =
      vocotype::common::parse_terms_yaml_content(after_protect);
  require(protected_document.protected_phrases.size() == 2U,
          "protected phrase count updated");
  require(protected_document.protected_phrases.back() == "三体问题",
          "protected phrase stored exactly");
  const auto protected_duplicate =
      settings::append_protected_phrase("三体问题");
  require(!protected_duplicate.value("success", true),
          "duplicate protected phrase rejected");
  require(settings::load_terms() == after_protect,
          "duplicate protected phrase does not modify dictionary");

  const auto imported_path = root / "imported-terms.yaml";
  const std::string imported_terms =
      "terms:\n  - canonical: Claude\n    aliases: []\n    hotword: true\n"
      "    protect: true\nprotect: []\n";
  {
    std::ofstream output(imported_path);
    output << imported_terms;
  }
  const auto imported = settings::import_terms(imported_path);
  require(imported.value("success", false), "valid dictionary import succeeds");
  require(settings::load_terms() == imported_terms,
          "dictionary import replaces content exactly");
  const auto reloaded = settings::reload_terms();
  require(reloaded.value("success", false), "dictionary hot reload validates file");
  require(settings::load_terms() == imported_terms,
          "dictionary hot reload preserves content");

  const auto invalid_import_path = root / "invalid-terms.yaml";
  {
    std::ofstream output(invalid_import_path);
    output << "terms: [";
  }
  const auto invalid_import = settings::import_terms(invalid_import_path);
  require(!invalid_import.value("success", true),
          "invalid dictionary import rejected");
  require(settings::load_terms() == imported_terms,
          "invalid import does not overwrite dictionary");

  const auto missing_recording =
      settings::play_recording(root / "missing.wav");
  require(!missing_recording.value("success", true),
          "missing recording produces structured failure");

  if (old_xdg_raw)
    setenv("XDG_CONFIG_HOME", old_xdg.c_str(), 1);
  else
    unsetenv("XDG_CONFIG_HOME");
  if (old_config_raw)
    setenv("VOCOTYPE_CONFIG", old_config.c_str(), 1);
  else
    unsetenv("VOCOTYPE_CONFIG");
  std::filesystem::remove_all(root);
  std::cout << "settings backend tests passed\n";
}
