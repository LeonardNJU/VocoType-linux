#include "../vocotype_module.h"

#include <cstdlib>
#include <iostream>
#include <string>

int main() {
  const auto require = [](bool condition, const char *message) {
    if (!condition) {
      std::cerr << "FAIL: " << message << '\n';
      std::exit(1);
    }
  };

  vocotype::VoCoTypeModuleConfig config;
  fcitx::RawConfig description;
  config.dumpDescription(description);

  const auto type = description.get("VoCoTypeModuleConfig");
  require(type != nullptr, "module configuration description exists");
  require(type->subItemsSize() == 1,
          "Fcitx configuration exposes exactly one entry");

  const auto entry = description.get("VoCoTypeModuleConfig/SettingsCenter");
  require(entry != nullptr, "settings-center entry exists");
  const std::string *kind = entry->valueByPath("Type");
  const std::string *command = entry->valueByPath("External");
  require(kind != nullptr && *kind == "External",
          "settings-center entry is an external option");
  require(command != nullptr && *command == "vocotype-settings",
          "settings-center entry launches the packaged settings command");

  fcitx::RawConfig saved;
  config.save(saved);
  require(saved.get("PTTKey") != nullptr,
          "hidden runtime shortcuts remain serializable");
  require(saved.get("SettingsCenter") == nullptr,
          "external settings entry is not persisted as runtime state");

  std::cout << "config entry tests passed\n";
  return 0;
}
