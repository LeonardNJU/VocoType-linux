#import <Carbon/Carbon.h>
#import <CoreFoundation/CoreFoundation.h>

#include <chrono>
#include <filesystem>
#include <iostream>
#include <stdexcept>
#include <string>
#include <thread>

#include <nlohmann/json.hpp>

namespace {
using Json = nlohmann::json;

std::string cf_string(CFTypeRef value) {
  if (!value || CFGetTypeID(value) != CFStringGetTypeID())
    return {};
  const auto text = static_cast<CFStringRef>(value);
  const CFIndex length = CFStringGetLength(text);
  const CFIndex bytes = CFStringGetMaximumSizeForEncoding(
      length, kCFStringEncodingUTF8) + 1;
  std::string result(static_cast<std::size_t>(bytes), '\0');
  if (!CFStringGetCString(text, result.data(), bytes, kCFStringEncodingUTF8))
    return {};
  result.resize(std::char_traits<char>::length(result.c_str()));
  return result;
}

bool cf_bool(CFTypeRef value) {
  return value && CFGetTypeID(value) == CFBooleanGetTypeID() &&
         CFBooleanGetValue(static_cast<CFBooleanRef>(value));
}

Json describe(TISInputSourceRef source) {
  return {
      {"id", cf_string(TISGetInputSourceProperty(
                 source, kTISPropertyInputSourceID))},
      {"bundle_id", cf_string(TISGetInputSourceProperty(
                        source, kTISPropertyBundleID))},
      {"name", cf_string(TISGetInputSourceProperty(
                   source, kTISPropertyLocalizedName))},
      {"category", cf_string(TISGetInputSourceProperty(
                       source, kTISPropertyInputSourceCategory))},
      {"type", cf_string(TISGetInputSourceProperty(
                   source, kTISPropertyInputSourceType))},
      {"enable_capable", cf_bool(TISGetInputSourceProperty(
                              source, kTISPropertyInputSourceIsEnableCapable))},
      {"enabled", cf_bool(TISGetInputSourceProperty(
                      source, kTISPropertyInputSourceIsEnabled))},
      {"selectable", cf_bool(TISGetInputSourceProperty(
                         source, kTISPropertyInputSourceIsSelectCapable))},
      {"selected", cf_bool(TISGetInputSourceProperty(
                       source, kTISPropertyInputSourceIsSelected))},
  };
}

TISInputSourceRef find_source(const std::string &identifier) {
  CFStringRef value = CFStringCreateWithCString(
      kCFAllocatorDefault, identifier.c_str(), kCFStringEncodingUTF8);
  if (!value)
    return nullptr;
  const void *key = kTISPropertyInputSourceID;
  const void *filter_value = value;
  CFDictionaryRef filter = CFDictionaryCreate(
      kCFAllocatorDefault, &key, &filter_value, 1,
      &kCFTypeDictionaryKeyCallBacks, &kCFTypeDictionaryValueCallBacks);
  CFRelease(value);
  if (!filter)
    return nullptr;
  CFArrayRef sources = TISCreateInputSourceList(filter, true);
  CFRelease(filter);
  if (!sources)
    return nullptr;
  TISInputSourceRef result = nullptr;
  if (CFArrayGetCount(sources) > 0) {
    result = static_cast<TISInputSourceRef>(
        const_cast<void *>(CFArrayGetValueAtIndex(sources, 0)));
    CFRetain(result);
  }
  CFRelease(sources);
  return result;
}

Json list_sources(const std::string &needle) {
  Json items = Json::array();
  CFArrayRef sources = TISCreateInputSourceList(nullptr, true);
  if (!sources)
    return items;
  const CFIndex count = CFArrayGetCount(sources);
  for (CFIndex index = 0; index < count; ++index) {
    const auto source = static_cast<TISInputSourceRef>(
        const_cast<void *>(CFArrayGetValueAtIndex(sources, index)));
    Json item = describe(source);
    const std::string id = item.value("id", "");
    const std::string bundle = item.value("bundle_id", "");
    if (needle.empty() || id.find(needle) != std::string::npos ||
        bundle.find(needle) != std::string::npos)
      items.push_back(std::move(item));
  }
  CFRelease(sources);
  return items;
}

int register_bundle(const std::filesystem::path &path) {
  const std::string absolute = std::filesystem::absolute(path).string();
  CFURLRef url = CFURLCreateFromFileSystemRepresentation(
      kCFAllocatorDefault,
      reinterpret_cast<const UInt8 *>(absolute.data()),
      static_cast<CFIndex>(absolute.size()), true);
  if (!url)
    throw std::runtime_error("cannot create input-source URL");
  const OSStatus status = TISRegisterInputSource(url);
  CFRelease(url);
  std::cout << Json{{"success", status == noErr},
                    {"status", status},
                    {"path", absolute}}
                   .dump()
            << '\n';
  return status == noErr ? 0 : 2;
}

int enable_source(const std::string &identifier) {
  TISInputSourceRef source = find_source(identifier);
  if (!source) {
    std::cout << Json{{"success", false},
                      {"error", "input source not found"},
                      {"identifier", identifier}}
                     .dump()
              << '\n';
    return 3;
  }
  const OSStatus status = TISEnableInputSource(source);
  CFRelease(source);

  Json item = Json::object();
  bool persisted = false;
  for (int attempt = 0; attempt < 20; ++attempt) {
    source = find_source(identifier);
    if (source) {
      item = describe(source);
      persisted = item.value("enabled", false);
      CFRelease(source);
      if (persisted)
        break;
    }
    std::this_thread::sleep_for(std::chrono::milliseconds(100));
  }
  item["identifier"] = identifier;
  item["enable_status"] = status;
  item["success"] = status == noErr;
  item["pending_session_reload"] = status == noErr && !persisted;
  if (status != noErr)
    item["error"] = "TISEnableInputSource failed";
  std::cout << item.dump() << '\n';
  return item.value("success", false) ? 0 : 4;
}

int activate_source(const std::string &identifier) {
  TISInputSourceRef source = find_source(identifier);
  if (!source) {
    std::cout << Json{{"success", false},
                      {"error", "input source not found"},
                      {"identifier", identifier}}
                     .dump()
              << '\n';
    return 3;
  }
  Json item = describe(source);
  if (item.value("category", "") != "TISCategoryPaletteInputSource") {
    CFRelease(source);
    item["success"] = false;
    item["error"] = "refusing to select a non-palette input source";
    std::cout << item.dump() << '\n';
    return 7;
  }
  const OSStatus enabled = TISEnableInputSource(source);
  const OSStatus selected =
      enabled == noErr ? TISSelectInputSource(source) : paramErr;
  CFRelease(source);

  bool active = false;
  for (int attempt = 0; attempt < 50; ++attempt) {
    source = find_source(identifier);
    if (source) {
      item = describe(source);
      active = item.value("enabled", false) &&
               item.value("selected", false);
      CFRelease(source);
      if (active)
        break;
    }
    std::this_thread::sleep_for(std::chrono::milliseconds(100));
  }
  item["identifier"] = identifier;
  item["enable_status"] = enabled;
  item["select_status"] = selected;
  item["success"] = enabled == noErr && selected == noErr && active;
  if (!item.value("success", false))
    item["error"] = "palette activation did not become active";
  std::cout << item.dump() << '\n';
  return item.value("success", false) ? 0 : 8;
}

int install_source(const std::filesystem::path &path,
                   const std::string &identifier) {
  const std::string absolute = std::filesystem::absolute(path).string();
  CFURLRef url = CFURLCreateFromFileSystemRepresentation(
      kCFAllocatorDefault,
      reinterpret_cast<const UInt8 *>(absolute.data()),
      static_cast<CFIndex>(absolute.size()), true);
  if (!url)
    throw std::runtime_error("cannot create input-source URL");
  const OSStatus registered = TISRegisterInputSource(url);
  CFRelease(url);
  if (registered != noErr) {
    std::cout << Json{{"success", false},
                      {"register_status", registered},
                      {"path", absolute}}
                     .dump()
              << '\n';
    return 2;
  }
  for (int attempt = 0; attempt < 20; ++attempt) {
    TISInputSourceRef source = find_source(identifier);
    if (source) {
      CFRelease(source);
      return activate_source(identifier);
    }
    std::this_thread::sleep_for(std::chrono::milliseconds(100));
  }
  std::cout << Json{{"success", false},
                    {"error", "registered palette did not become available"},
                    {"identifier", identifier},
                    {"path", absolute}}
                   .dump()
            << '\n';
  return 6;
}

int disable_source(const std::string &identifier) {
  TISInputSourceRef source = find_source(identifier);
  if (!source) {
    std::cout << Json{{"success", true},
                      {"already_absent", true},
                      {"identifier", identifier}}
                     .dump()
              << '\n';
    return 0;
  }
  const OSStatus deselected = TISDeselectInputSource(source);
  const OSStatus disabled = TISDisableInputSource(source);
  Json item = describe(source);
  CFRelease(source);
  item["success"] =
      (deselected == noErr || deselected == paramErr) && disabled == noErr;
  item["deselect_status"] = deselected;
  item["disable_status"] = disabled;
  std::cout << item.dump() << '\n';
  return item.value("success", false) ? 0 : 5;
}

void usage() {
  std::cerr << "Usage: vocotype-input-source-tool "
               "--install APP ID | --register APP | --list [FILTER] | "
               "--current | --enable ID | --activate ID | --disable ID\n";
}

} // namespace

int main(int argc, char **argv) {
  try {
    if (argc < 2) {
      usage();
      return 1;
    }
    const std::string action = argv[1];
    if (action == "--install" && argc == 4)
      return install_source(argv[2], argv[3]);
    if (action == "--register" && argc == 3)
      return register_bundle(argv[2]);
    if (action == "--list") {
      const std::string filter = argc >= 3 ? argv[2] : "";
      std::cout << Json{{"success", true},
                        {"sources", list_sources(filter)}}
                       .dump()
                << '\n';
      return 0;
    }
    if (action == "--current") {
      TISInputSourceRef source = TISCopyCurrentKeyboardInputSource();
      if (!source)
        throw std::runtime_error("cannot read current input source");
      Json item = describe(source);
      CFRelease(source);
      item["success"] = true;
      std::cout << item.dump() << '\n';
      return 0;
    }
    if (action == "--enable" && argc == 3)
      return enable_source(argv[2]);
    if (action == "--activate" && argc == 3)
      return activate_source(argv[2]);
    if (action == "--disable" && argc == 3)
      return disable_source(argv[2]);
    usage();
    return 1;
  } catch (const std::exception &error) {
    std::cout << Json{{"success", false}, {"error", error.what()}}.dump()
              << '\n';
    return 1;
  }
}
