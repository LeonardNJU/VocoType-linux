#include "vocotype/desktop/config.hpp"

#include <curl/curl.h>
#include <openssl/evp.h>

#include <array>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

using vocotype::desktop::Json;
namespace {

struct FileSpec {
  const char *name;
  const char *revision;
  const char *sha256;
};
struct ModelSpec {
  const char *id;
  std::vector<FileSpec> files;
  bool streaming;
};

const std::vector<ModelSpec> &manifest() {
  static const std::vector<ModelSpec> value = {
      {"iic/"
       "speech_paraformer-large-contextual_asr_nat-zh-cn-16k-common-vocab8404-"
       "onnx",
       {
           {"am.mvn", "1c4c4d2ee95dfc59aa7efa3bacce6b72af4622bc",
            "29b3c740a2c0cfc6b308126d31d7f265fa2be74f3bb095cd2f143ea970896ae5"},
           {"configuration.json", "1c4c4d2ee95dfc59aa7efa3bacce6b72af4622bc",
            "6e4e5234d4f657eb5530fbda198915b9f219aedbaa13a7b59b62819f43022af3"},
           {"config.yaml", "0e8b6a277734182ed17edb1302051dc40295622e",
            "1d9057edeaba9e131cb98f26011606497cf3af187d8943525ddb5ee36c836b1b"},
           {"model_eb.onnx", "8f0881c891ceba7360e215b04e54cad564a68c41",
            "d31446a5af664291a2922cca253a4200a523f347d6fc3cb1bff356bf60a116b6"},
           {"model_quant.onnx", "8f0881c891ceba7360e215b04e54cad564a68c41",
            "f404e6eb532b54fd95761e2b4be4ed1998e8cff3cb3b930a9bee1f2d556e5035"},
           {"seg_dict", "1c4c4d2ee95dfc59aa7efa3bacce6b72af4622bc",
            "59a2ef803a3f1648ad03a2e1480db1c1ee0c0d7dc4ef4dbd16cea33944329022"},
           {"tokens.json", "0e8b6a277734182ed17edb1302051dc40295622e",
            "2b20c2b12572d682afff84ce1c8d560f67b8b32a4c1f21567411d141ed352127"},
       },
       false},
      {"iic/"
       "speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-online-onnx",
       {
           {"am.mvn", "8379db60b12deddfd8ddc95350c2bbd8e058667c",
            "29b3c740a2c0cfc6b308126d31d7f265fa2be74f3bb095cd2f143ea970896ae5"},
           {"configuration.json", "8379db60b12deddfd8ddc95350c2bbd8e058667c",
            "cbf1c4e973cbb914d5c3aa24dcb30468bccc3abef60c1b9f9bb28b1f75c81255"},
           {"config.yaml", "3f52bb64b3b3a09c2c06ab6da22795d438e7c777",
            "35e6bf41f8c7eaf9a0f787af7fdc8fc5ed75fa8009ade7d3c2f3ef5bce20c648"},
           {"decoder_quant.onnx", "8379db60b12deddfd8ddc95350c2bbd8e058667c",
            "9855f60417e6eccddc4c7340d448fe99e95be689cba6e76c5330aa653d430aea"},
           {"model_quant.onnx", "8379db60b12deddfd8ddc95350c2bbd8e058667c",
            "c008250f40abf9bfe1957cf98d47cc734d6da6ce7301de40c534b57f9b366336"},
           {"tokens.json", "3f52bb64b3b3a09c2c06ab6da22795d438e7c777",
            "2b20c2b12572d682afff84ce1c8d560f67b8b32a4c1f21567411d141ed352127"},
       },
       true},
      {"iic/speech_fsmn_vad_zh-cn-16k-common-onnx",
       {
           {"am.mvn", "9e08ccbfe15c79d0f4b41247c5ed4266031ac362",
            "6820fef9687708c4fc3fab2530179c8fcea6262daa25514380056cd8f6eb1754"},
           {"configuration.json", "bdaa7c49489f31282c1c17a3b86e2c80e40f8d30",
            "f62fcbcebbaade798714b34c521cad87d869ee6a327ceed948e01ff2336bbfc0"},
           {"config.yaml", "60b33f725eb866acee2a4fce6f8d73ab4276ca6b",
            "96dd96779c8200123ad15f5230783c862e8c99d1805e89c9394635514d5a243d"},
           {"model_quant.onnx", "f436483dfd1bf3eb1b410133ff86ecc9e10abc4a",
            "5289eb2aa3c9af2d7a4284bcfa7c3ceb81d360814ed4203239b6c5d0569da8a1"},
       },
       false},
      {"iic/punc_ct-transformer_zh-cn-common-vocab272727-onnx",
       {
           {"configuration.json", "8297bd50cd5c29b53a51c661520e27d2faadc6f3",
            "16097d3034818080e39331fa08909dffe75189f5c986f74155afd90b5f531ee4"},
           {"config.yaml", "c9d8ba523014110650428e422a4975343193975c",
            "a56ec10925b06fa976ad51af373396be2b13e1eb8dc62a5426b5adebaba7071d"},
           {"model_quant.onnx", "8f239ff78c6267c4d859233e7eb3bbdb68c61824",
            "e6cd8399bf7d0e75f8d9af4a107310e1968ecab1d50135e765b8f0265b27a83d"},
           {"tokens.json", "c9d8ba523014110650428e422a4975343193975c",
            "c960ab87bccea4aa15cf49a59f71973c2c330b46668048cd8da253749ec71ee3"},
       },
       false},
  };
  return value;
}

std::string sha256_file(const std::filesystem::path &path) {
  std::ifstream input(path, std::ios::binary);
  if (!input)
    return {};
  EVP_MD_CTX *context = EVP_MD_CTX_new();
  if (!context)
    throw std::runtime_error("cannot allocate SHA-256 context");
  std::array<unsigned char, EVP_MAX_MD_SIZE> digest{};
  unsigned int digest_size = 0;
  if (EVP_DigestInit_ex(context, EVP_sha256(), nullptr) != 1) {
    EVP_MD_CTX_free(context);
    throw std::runtime_error("cannot initialize SHA-256");
  }
  std::array<char, 1024 * 1024> buffer{};
  while (input) {
    input.read(buffer.data(), static_cast<std::streamsize>(buffer.size()));
    const auto count = input.gcount();
    if (count > 0 && EVP_DigestUpdate(context, buffer.data(),
                                      static_cast<std::size_t>(count)) != 1) {
      EVP_MD_CTX_free(context);
      throw std::runtime_error("SHA-256 update failed");
    }
  }
  if (EVP_DigestFinal_ex(context, digest.data(), &digest_size) != 1) {
    EVP_MD_CTX_free(context);
    throw std::runtime_error("SHA-256 finalization failed");
  }
  EVP_MD_CTX_free(context);
  std::ostringstream output;
  for (unsigned int index = 0; index < digest_size; ++index)
    output << std::hex << std::setw(2) << std::setfill('0')
           << static_cast<int>(digest[index]);
  return output.str();
}

std::filesystem::path default_cache() {
  if (const char *configured = std::getenv("MODELSCOPE_CACHE");
      configured && *configured)
    return std::filesystem::path(configured) / "models";
  return vocotype::desktop::home_path() / ".cache/modelscope/hub/models";
}

struct Progress {
  std::string model;
  std::string file;
  std::chrono::steady_clock::time_point last = std::chrono::steady_clock::now();
};

int progress_callback(void *pointer, curl_off_t total, curl_off_t current,
                      curl_off_t, curl_off_t) {
  auto &progress = *static_cast<Progress *>(pointer);
  const auto now = std::chrono::steady_clock::now();
  if (now - progress.last < std::chrono::milliseconds(500) && current != total)
    return 0;
  progress.last = now;
  Json event{{"type", "progress"},
             {"model", progress.model},
             {"file", progress.file},
             {"downloaded", current},
             {"total", total}};
  std::cout << event.dump() << '\n' << std::flush;
  return 0;
}

void download(const std::string &model, const FileSpec &file,
              const std::filesystem::path &destination) {
  std::filesystem::create_directories(destination.parent_path());
  const auto partial = destination.string() + ".part";
  FILE *output = std::fopen(partial.c_str(), "wb");
  if (!output)
    throw std::runtime_error("cannot create " + partial);
  CURL *curl = curl_easy_init();
  if (!curl) {
    std::fclose(output);
    throw std::runtime_error("cannot initialize libcurl");
  }
  const std::string url = "https://modelscope.cn/models/" + model +
                          "/resolve/" + file.revision + "/" + file.name;
  Progress progress{model, file.name};
  curl_easy_setopt(curl, CURLOPT_URL, url.c_str());
  curl_easy_setopt(curl, CURLOPT_FOLLOWLOCATION, 1L);
  curl_easy_setopt(curl, CURLOPT_FAILONERROR, 1L);
  curl_easy_setopt(curl, CURLOPT_WRITEDATA, output);
  curl_easy_setopt(curl, CURLOPT_WRITEFUNCTION, nullptr);
  curl_easy_setopt(curl, CURLOPT_USERAGENT, "VoCoType-native/3");
  curl_easy_setopt(curl, CURLOPT_CONNECTTIMEOUT, 30L);
  curl_easy_setopt(curl, CURLOPT_LOW_SPEED_LIMIT, 1024L);
  curl_easy_setopt(curl, CURLOPT_LOW_SPEED_TIME, 60L);
  curl_easy_setopt(curl, CURLOPT_NOPROGRESS, 0L);
  curl_easy_setopt(curl, CURLOPT_XFERINFOFUNCTION, progress_callback);
  curl_easy_setopt(curl, CURLOPT_XFERINFODATA, &progress);
  const CURLcode result = curl_easy_perform(curl);
  long status = 0;
  curl_easy_getinfo(curl, CURLINFO_RESPONSE_CODE, &status);
  curl_easy_cleanup(curl);
  std::fclose(output);
  if (result != CURLE_OK)
    throw std::runtime_error("download failed for " + model + "/" + file.name +
                             ": " + curl_easy_strerror(result));
  if (status < 200 || status >= 300)
    throw std::runtime_error("HTTP " + std::to_string(status) + " for " + url);
  const std::string actual = sha256_file(partial);
  if (actual != file.sha256) {
    std::filesystem::remove(partial);
    throw std::runtime_error("checksum mismatch for " + model + "/" +
                             file.name);
  }
  std::filesystem::rename(partial, destination);
  std::filesystem::permissions(destination,
                               std::filesystem::perms::owner_read |
                                   std::filesystem::perms::owner_write |
                                   std::filesystem::perms::group_read |
                                   std::filesystem::perms::others_read,
                               std::filesystem::perm_options::replace);
}

Json check_models(const std::filesystem::path &cache, bool include_streaming,
                  bool repair) {
  int checked = 0;
  int missing = 0;
  Json files = Json::array();
  for (const auto &model : manifest()) {
    if (model.streaming && !include_streaming)
      continue;
    for (const auto &file : model.files) {
      ++checked;
      const auto destination = cache / model.id / file.name;
      bool valid = std::filesystem::is_regular_file(destination) &&
                   sha256_file(destination) == file.sha256;
      if (!valid && repair) {
        download(model.id, file, destination);
        valid = true;
      }
      if (!valid)
        ++missing;
      files.push_back({{"model", model.id},
                       {"file", file.name},
                       {"valid", valid},
                       {"path", destination.string()}});
    }
  }
  return {{"success", missing == 0},
          {"checked", checked},
          {"missing", missing},
          {"cache", cache.string()},
          {"files", files}};
}

} // namespace

int main(int argc, char **argv) {
  try {
    bool repair = false;
    bool streaming = true;
    std::filesystem::path cache = default_cache();
    for (int index = 1; index < argc; ++index) {
      const std::string argument = argv[index];
      if (argument == "--download" || argument == "--repair")
        repair = true;
      else if (argument == "--check")
        repair = false;
      else if (argument == "--minimal")
        streaming = false;
      else if (argument == "--all")
        streaming = true;
      else if (argument == "--cache" && index + 1 < argc)
        cache = argv[++index];
      else if (argument == "--help") {
        std::cout << "Usage: vocotype-model-manager [--check|--download] "
                     "[--minimal|--all] [--cache DIR]\n";
        return 0;
      } else
        throw std::runtime_error("unknown argument: " + argument);
    }
    curl_global_init(CURL_GLOBAL_DEFAULT);
    const Json result = check_models(cache, streaming, repair);
    curl_global_cleanup();
    std::cout << result.dump() << '\n';
    return result.value("success", false) ? 0 : 2;
  } catch (const std::exception &error) {
    std::cout << Json{{"success", false}, {"error", error.what()}}.dump()
              << '\n';
    return 1;
  }
}
