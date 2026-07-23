#include "vocotype/feedback/service.hpp"

#include <boost/asio.hpp>
#include <boost/beast/core.hpp>
#include <boost/beast/http.hpp>

#include <algorithm>
#include <charconv>
#include <cstdlib>
#include <iostream>
#include <optional>
#include <set>
#include <sstream>
#include <string>
#include <thread>
#include <vector>

namespace asio = boost::asio;
namespace beast = boost::beast;
namespace http = beast::http;
using tcp = asio::ip::tcp;
using vocotype::feedback::AcceptedFeedback;
using vocotype::feedback::Attachment;
using vocotype::feedback::Config;
using vocotype::feedback::FeedbackError;
using vocotype::feedback::Json;
using vocotype::feedback::Store;

namespace {

struct Options {
  std::string host = "127.0.0.1";
  int port = 18088;
  std::string status;
  int limit = 50;
  std::string note;
  int attachment_days = 30;
  std::string backup_dir = "/var/backups/vocotype-feedback";
  int backup_days = 14;
};

std::string lowercase(std::string value) {
  std::transform(value.begin(), value.end(), value.begin(),
                 [](unsigned char character) {
                   return static_cast<char>(std::tolower(character));
                 });
  return value;
}

std::string trim(std::string value) {
  const auto is_space = [](unsigned char character) {
    return std::isspace(character) != 0;
  };
  value.erase(value.begin(),
              std::find_if_not(value.begin(), value.end(), is_space));
  value.erase(std::find_if_not(value.rbegin(), value.rend(), is_space).base(),
              value.end());
  return value;
}

std::vector<std::string> split_csv(const std::string &value) {
  std::vector<std::string> result;
  std::istringstream input(value);
  std::string item;
  while (std::getline(input, item, ',')) {
    item = trim(std::move(item));
    if (!item.empty())
      result.push_back(std::move(item));
  }
  return result;
}

std::string environment_value(const char *name,
                              const std::string &fallback = {}) {
  const char *value = std::getenv(name);
  return value && *value ? value : fallback;
}

std::optional<std::string> header_address(const std::string &value) {
  const std::string candidate = trim(value.substr(0, value.find(',')));
  if (candidate.empty())
    return std::nullopt;
  beast::error_code error;
  (void)asio::ip::make_address(candidate, error);
  if (error)
    return std::nullopt;
  return candidate;
}

std::string client_address(const http::request<http::string_body> &request,
                           const tcp::socket &socket) {
  beast::error_code error;
  const std::string direct =
      socket.remote_endpoint(error).address().to_string();
  if (error)
    return "unknown";
  const auto trusted = split_csv(
      environment_value("VOCOTYPE_FEEDBACK_TRUSTED_PROXIES", "127.0.0.1,::1"));
  if (std::find(trusted.begin(), trusted.end(), direct) == trusted.end())
    return direct;
  if (const auto forwarded =
          header_address(std::string(request["X-Forwarded-For"])))
    return *forwarded;
  if (const auto real = header_address(std::string(request["X-Real-IP"])))
    return *real;
  return direct;
}

Json parse_feedback_request(const http::request<http::string_body> &request,
                            std::optional<Attachment> &attachment) {
  const std::string content_type =
      std::string(request[http::field::content_type]);
  const std::string lowered = lowercase(content_type);
  if (lowered.find("multipart/form-data") != std::string::npos) {
    std::optional<Json> payload;
    std::string bundle_name;
    std::string bundle_data;
    for (const auto &part :
         vocotype::feedback::parse_multipart(content_type, request.body())) {
      if (part.name == "payload") {
        try {
          payload = Json::parse(part.data);
        } catch (const Json::exception &) {
          throw FeedbackError("payload 不是有效的 UTF-8 JSON");
        }
      } else if (part.name == "bundle") {
        bundle_name = part.filename.empty() ? "support.tar.gz" : part.filename;
        bundle_data = part.data;
      }
    }
    if (!payload)
      throw FeedbackError("缺少 payload 字段");
    if (!bundle_data.empty())
      attachment = vocotype::feedback::validate_attachment(
          std::move(bundle_name), std::move(bundle_data));
    return *payload;
  }
  if (lowered.find("application/json") != std::string::npos) {
    Json payload;
    try {
      payload = Json::parse(request.body());
    } catch (const Json::exception &) {
      throw FeedbackError("请求体不是有效的 UTF-8 JSON");
    }
    if (!payload.is_object())
      throw FeedbackError("请求体必须是 JSON 对象");
    if (payload.contains("bundle_base64")) {
      if (!payload.at("bundle_base64").is_string())
        throw FeedbackError("支持包 base64 无效", 400, "invalid_bundle");
      const std::string filename =
          payload.value("bundle_name", "support.tar.gz");
      attachment = vocotype::feedback::validate_attachment(
          filename, vocotype::feedback::base64_decode(
                        payload.at("bundle_base64").get<std::string>()));
      payload.erase("bundle_base64");
      payload.erase("bundle_name");
    }
    return payload;
  }
  throw FeedbackError("只接受 multipart/form-data 或 application/json", 415,
                      "unsupported_media_type");
}

http::response<http::string_body> json_response(http::status status,
                                                const Json &body,
                                                unsigned version,
                                                bool keep_alive = false) {
  http::response<http::string_body> response{status, version};
  response.set(http::field::content_type, "application/json; charset=utf-8");
  response.set(http::field::cache_control, "no-store");
  response.set("X-Content-Type-Options", "nosniff");
  response.keep_alive(keep_alive);
  response.body() = body.dump();
  response.prepare_payload();
  return response;
}

void handle_connection(tcp::socket socket, Store &store) {
  beast::flat_buffer buffer;
  beast::error_code error;
  http::request_parser<http::string_body> parser;
  parser.body_limit(vocotype::feedback::kMaxRequestBytes);
  http::read(socket, buffer, parser, error);
  if (error)
    return;
  const auto request = parser.release();

  try {
    if (request.method() == http::verb::get &&
        (request.target() == "/" || request.target() == "/healthz")) {
      http::write(
          socket,
          json_response(
              http::status::ok,
              {{"service", "VoCoType Feedback"}, {"version", 1}, {"ok", true}},
              request.version()),
          error);
      return;
    }
    if (request.target() != "/v1/feedback") {
      http::write(socket,
                  json_response(http::status::not_found,
                                {{"ok", false}, {"error", "not_found"}},
                                request.version()),
                  error);
      return;
    }
    if (request.method() != http::verb::post) {
      http::write(
          socket,
          json_response(http::status::method_not_allowed,
                        {{"ok", false}, {"error", "method_not_allowed"}},
                        request.version()),
          error);
      return;
    }

    std::optional<Attachment> attachment;
    const Json payload = parse_feedback_request(request, attachment);
    const AcceptedFeedback accepted =
        store.accept(payload, client_address(request, socket),
                     attachment ? &*attachment : nullptr);
    http::write(socket,
                json_response(http::status::accepted, accepted.to_json(),
                              request.version()),
                error);
  } catch (const FeedbackError &exception) {
    auto response =
        json_response(static_cast<http::status>(exception.status_code()),
                      {{"ok", false},
                       {"error", exception.error_code()},
                       {"message", exception.what()}},
                      request.version());
    if (exception.status_code() == 429)
      response.set(http::field::retry_after, "3600");
    http::write(socket, response, error);
  } catch (const std::exception &exception) {
    http::write(socket,
                json_response(http::status::bad_request,
                              {{"ok", false},
                               {"error", "invalid_request"},
                               {"message", exception.what()}},
                              request.version()),
                error);
  }
}

int parse_integer(const std::string &value, int fallback) {
  int parsed = fallback;
  const auto result =
      std::from_chars(value.data(), value.data() + value.size(), parsed);
  return result.ec == std::errc{} && result.ptr == value.data() + value.size()
             ? parsed
             : fallback;
}

Options parse_options(int argc, char **argv, int start) {
  Options options;
  for (int index = start; index < argc; ++index) {
    const std::string argument = argv[index];
    auto next = [&]() -> std::string {
      if (index + 1 >= argc)
        throw std::runtime_error("missing value for " + argument);
      return argv[++index];
    };
    if (argument == "--host")
      options.host = next();
    else if (argument == "--port")
      options.port = parse_integer(next(), options.port);
    else if (argument == "--status")
      options.status = next();
    else if (argument == "--limit")
      options.limit = parse_integer(next(), options.limit);
    else if (argument == "--note")
      options.note = next();
    else if (argument == "--attachment-days")
      options.attachment_days = parse_integer(next(), options.attachment_days);
    else if (argument == "--backup-dir")
      options.backup_dir = next();
    else if (argument == "--backup-days")
      options.backup_days = parse_integer(next(), options.backup_days);
    else
      throw std::runtime_error("unknown option: " + argument);
  }
  return options;
}

void print_usage() {
  std::cout << "Usage:\n"
            << "  vocotype-feedback serve [--host 127.0.0.1] [--port 18088]\n"
            << "  vocotype-feedback list [--status new] [--limit 50]\n"
            << "  vocotype-feedback show FEEDBACK_ID\n"
            << "  vocotype-feedback status FEEDBACK_ID "
               "new|triaged|resolved|spam [--note TEXT]\n"
            << "  vocotype-feedback maintenance [--attachment-days 30] "
               "[--backup-dir DIR] [--backup-days 14]\n";
}

int serve(Store &store, const Options &options) {
  asio::io_context context;
  tcp::acceptor acceptor(context, {asio::ip::make_address(options.host),
                                   static_cast<unsigned short>(options.port)});
  std::cout << "VoCoType Feedback listening on " << options.host << ':'
            << options.port << '\n';
  for (;;) {
    tcp::socket socket(context);
    acceptor.accept(socket);
    std::thread(handle_connection, std::move(socket), std::ref(store)).detach();
  }
}

} // namespace

int main(int argc, char **argv) {
  try {
    if (argc < 2) {
      print_usage();
      return 2;
    }
    const std::string command = argv[1];
    if (command == "--help" || command == "help") {
      print_usage();
      return 0;
    }

    Store store(Config::from_environment());
    if (command == "serve")
      return serve(store, parse_options(argc, argv, 2));
    if (command == "list") {
      const Options options = parse_options(argc, argv, 2);
      std::cout << store.list_feedback(options.status, options.limit).dump(2)
                << '\n';
      return 0;
    }
    if (command == "show") {
      if (argc < 3)
        throw std::runtime_error("feedback ID is required");
      const Json result = store.get_feedback(argv[2]);
      if (result.is_null())
        return 3;
      std::cout << result.dump(2) << '\n';
      return 0;
    }
    if (command == "status") {
      if (argc < 4)
        throw std::runtime_error("feedback ID and status are required");
      const Options options = parse_options(argc, argv, 4);
      return store.update_status(argv[2], argv[3], options.note) ? 0 : 3;
    }
    if (command == "maintenance") {
      const Options options = parse_options(argc, argv, 2);
      std::cout << store
                       .maintenance(options.attachment_days, options.backup_dir,
                                    options.backup_days)
                       .dump(2)
                << '\n';
      return 0;
    }
    throw std::runtime_error("unknown command: " + command);
  } catch (const std::exception &exception) {
    std::cerr << "vocotype-feedback: " << exception.what() << '\n';
    return 1;
  }
}
