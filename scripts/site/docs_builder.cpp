#include <algorithm>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <regex>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

namespace fs = std::filesystem;

namespace {

std::string read_text(const fs::path &path) {
  std::ifstream input(path, std::ios::binary);
  if (!input)
    throw std::runtime_error("cannot read " + path.string());
  return std::string(std::istreambuf_iterator<char>(input),
                     std::istreambuf_iterator<char>());
}

void write_text(const fs::path &path, const std::string &text) {
  fs::create_directories(path.parent_path());
  std::ofstream output(path, std::ios::binary | std::ios::trunc);
  if (!output)
    throw std::runtime_error("cannot write " + path.string());
  output << text;
}

std::string trim(std::string value) {
  const auto space = [](unsigned char character) {
    return std::isspace(character) != 0;
  };
  value.erase(value.begin(),
              std::find_if_not(value.begin(), value.end(), space));
  value.erase(std::find_if_not(value.rbegin(), value.rend(), space).base(),
              value.end());
  return value;
}

std::string escape_html(std::string_view value) {
  std::string output;
  output.reserve(value.size());
  for (char character : value) {
    switch (character) {
    case '&':
      output += "&amp;";
      break;
    case '<':
      output += "&lt;";
      break;
    case '>':
      output += "&gt;";
      break;
    case '"':
      output += "&quot;";
      break;
    case '\'':
      output += "&#39;";
      break;
    default:
      output.push_back(character);
      break;
    }
  }
  return output;
}

std::string rewrite_link(std::string target) {
  const auto hash = target.find('#');
  const std::string suffix =
      hash == std::string::npos ? "" : target.substr(hash);
  std::string path =
      hash == std::string::npos ? target : target.substr(0, hash);
  if (path.ends_with("README.md"))
    path.replace(path.size() - std::string("README.md").size(),
                 std::string("README.md").size(), "index.html");
  else if (path.ends_with(".md"))
    path.replace(path.size() - 3U, 3U, ".html");
  return path + suffix;
}

std::string inline_markup(std::string value) {
  value = escape_html(value);
  value = std::regex_replace(value, std::regex(R"(\[([^\]]+)\]\(([^\)]+)\))"),
                             R"(<a href="$2">$1</a>)");
  std::smatch match;
  std::string rebuilt;
  const std::regex link_pattern("<a href=\\\"([^\\\"]+)\\\">");
  while (std::regex_search(value, match, link_pattern)) {
    rebuilt += match.prefix().str();
    rebuilt += "<a href=\"" + rewrite_link(match[1].str()) + "\">";
    value = match.suffix().str();
  }
  rebuilt += value;
  value = std::move(rebuilt);
  value = std::regex_replace(value, std::regex(R"(`([^`]+)`)"),
                             R"(<code>$1</code>)");
  value = std::regex_replace(value, std::regex(R"(\*\*([^*]+)\*\*)"),
                             R"(<strong>$1</strong>)");
  value =
      std::regex_replace(value, std::regex(R"(\*([^*]+)\*)"), R"(<em>$1</em>)");
  return value;
}

std::string slug(std::string value) {
  std::string result;
  bool dash = false;
  for (const unsigned char character : value) {
    if (std::isalnum(character) || character >= 0x80U) {
      if (dash && !result.empty())
        result.push_back('-');
      result.push_back(static_cast<char>(std::tolower(character)));
      dash = false;
    } else {
      dash = true;
    }
  }
  return result.empty() ? "section" : result;
}

std::string page_title(const std::string &markdown, const fs::path &path) {
  std::istringstream input(markdown);
  std::string line;
  while (std::getline(input, line)) {
    if (line.rfind("# ", 0) == 0)
      return trim(line.substr(2));
  }
  return path.stem() == "README" ? "VoCoType Linux 文档" : path.stem().string();
}

bool table_separator(const std::string &line) {
  if (line.find('|') == std::string::npos)
    return false;
  for (char character : line) {
    if (character != '|' && character != '-' && character != ':' &&
        !std::isspace(static_cast<unsigned char>(character)))
      return false;
  }
  return true;
}

std::vector<std::string> table_cells(std::string line) {
  line = trim(std::move(line));
  if (!line.empty() && line.front() == '|')
    line.erase(line.begin());
  if (!line.empty() && line.back() == '|')
    line.pop_back();
  std::vector<std::string> result;
  std::istringstream input(line);
  std::string cell;
  while (std::getline(input, cell, '|'))
    result.push_back(trim(std::move(cell)));
  return result;
}

bool safe_code_language(std::string_view language) {
  return std::all_of(language.begin(), language.end(), [](unsigned char ch) {
    return std::isalnum(ch) != 0 || ch == '-' || ch == '_' || ch == '+';
  });
}

std::string markdown_to_html(const std::string &markdown,
                             const fs::path &source) {
  std::vector<std::string> lines;
  std::istringstream input(markdown);
  std::string line;
  while (std::getline(input, line))
    lines.push_back(line);

  std::ostringstream output;
  bool code = false;
  std::size_t code_indent = 0;
  bool unordered = false;
  bool ordered = false;
  bool paragraph = false;
  auto close_lists = [&] {
    if (unordered) {
      output << "</ul>\n";
      unordered = false;
    }
    if (ordered) {
      output << "</ol>\n";
      ordered = false;
    }
  };
  auto close_paragraph = [&] {
    if (paragraph) {
      output << "</p>\n";
      paragraph = false;
    }
  };

  for (std::size_t index = 0; index < lines.size(); ++index) {
    line = lines[index];
    const std::string stripped = trim(line);
    if (stripped.rfind("```", 0) == 0) {
      close_paragraph();
      close_lists();
      if (!code) {
        code_indent = line.find_first_not_of(' ');
        if (code_indent == std::string::npos)
          code_indent = 0;
        const std::string language = trim(stripped.substr(3));
        if (!language.empty() && !safe_code_language(language)) {
          throw std::runtime_error(source.string() + ":" +
                                   std::to_string(index + 1U) +
                                   ": invalid fenced-code language");
        }
        output << "<pre><code";
        if (!language.empty())
          output << " class=\"language-" << language << "\"";
        output << ">";
        code = true;
      } else {
        output << "</code></pre>\n";
        code = false;
        code_indent = 0;
      }
      continue;
    }
    if (code) {
      std::size_t remove = 0;
      while (remove < code_indent && remove < line.size() &&
             line[remove] == ' ')
        ++remove;
      output << escape_html(std::string_view(line).substr(remove)) << '\n';
      continue;
    }
    if (stripped.rfind("=== ", 0) == 0 || stripped.rfind("!!! ", 0) == 0 ||
        stripped.rfind("??? ", 0) == 0 || stripped.rfind("::: ", 0) == 0) {
      throw std::runtime_error(source.string() + ":" +
                               std::to_string(index + 1U) +
                               ": unsupported Markdown extension");
    }
    if (index + 1U < lines.size() && line.find('|') != std::string::npos &&
        table_separator(lines[index + 1U])) {
      close_paragraph();
      close_lists();
      output << "<table><thead><tr>";
      for (const auto &cell : table_cells(line))
        output << "<th>" << inline_markup(cell) << "</th>";
      output << "</tr></thead><tbody>\n";
      index += 2U;
      while (index < lines.size() &&
             lines[index].find('|') != std::string::npos &&
             !trim(lines[index]).empty()) {
        output << "<tr>";
        for (const auto &cell : table_cells(lines[index]))
          output << "<td>" << inline_markup(cell) << "</td>";
        output << "</tr>\n";
        ++index;
      }
      output << "</tbody></table>\n";
      if (index < lines.size())
        --index;
      continue;
    }
    if (stripped.empty()) {
      close_paragraph();
      close_lists();
      continue;
    }
    if (stripped == "---") {
      close_paragraph();
      close_lists();
      output << "<hr>\n";
      continue;
    }
    if (stripped.starts_with("<div") || stripped == "</div>" ||
        stripped.starts_with("<!--"))
      continue;
    std::size_t heading = 0;
    while (heading < stripped.size() && stripped[heading] == '#')
      ++heading;
    if (heading > 0 && heading <= 6 && heading < stripped.size() &&
        stripped[heading] == ' ') {
      close_paragraph();
      close_lists();
      const std::string text = trim(stripped.substr(heading + 1U));
      output << "<h" << heading << " id=\"" << slug(text) << "\">"
             << inline_markup(text) << "</h" << heading << ">\n";
      continue;
    }
    if (stripped.rfind("- ", 0) == 0 || stripped.rfind("* ", 0) == 0) {
      close_paragraph();
      if (ordered) {
        output << "</ol>\n";
        ordered = false;
      }
      if (!unordered) {
        output << "<ul>\n";
        unordered = true;
      }
      output << "<li>" << inline_markup(trim(stripped.substr(2))) << "</li>\n";
      continue;
    }
    if (std::regex_match(stripped, std::regex(R"([0-9]+\. .*)"))) {
      close_paragraph();
      if (unordered) {
        output << "</ul>\n";
        unordered = false;
      }
      if (!ordered) {
        output << "<ol>\n";
        ordered = true;
      }
      const auto dot = stripped.find('.');
      output << "<li>" << inline_markup(trim(stripped.substr(dot + 1U)))
             << "</li>\n";
      continue;
    }
    if (stripped.rfind("> ", 0) == 0) {
      close_paragraph();
      close_lists();
      output << "<blockquote>" << inline_markup(stripped.substr(2))
             << "</blockquote>\n";
      continue;
    }
    close_lists();
    if (!paragraph) {
      output << "<p>";
      paragraph = true;
    } else {
      output << ' ';
    }
    output << inline_markup(stripped);
  }
  close_paragraph();
  close_lists();
  if (code) {
    throw std::runtime_error(source.string() + ": unclosed fenced code block");
  }
  return output.str();
}

struct Document {
  fs::path source;
  fs::path relative;
  fs::path output;
  std::string title;
};

void validate_markdown_links(const Document &document,
                             const std::string &markdown) {
  const std::regex pattern(R"(\[[^\]]+\]\(([^)]+)\))");
  for (std::sregex_iterator iterator(markdown.begin(), markdown.end(), pattern),
       end;
       iterator != end; ++iterator) {
    std::string target = (*iterator)[1].str();
    if (target.empty() || target.front() == '#' || target.front() == '/' ||
        std::regex_search(target, std::regex(R"(^[A-Za-z][A-Za-z0-9+.-]*:)")))
      continue;
    const std::size_t hash = target.find('#');
    if (hash != std::string::npos)
      target.erase(hash);
    const std::size_t query = target.find('?');
    if (query != std::string::npos)
      target.erase(query);
    if (target.empty())
      continue;
    const fs::path resolved =
        (document.source.parent_path() / target).lexically_normal();
    if (!fs::exists(resolved)) {
      throw std::runtime_error(document.source.string() +
                               ": broken relative link: " + target);
    }
  }
}

std::string navigation(const std::vector<Document> &documents,
                       const fs::path &current_output) {
  const fs::path current_directory = current_output.parent_path();
  std::ostringstream output;
  output
      << "<nav><a class=\"brand\" href=\""
      << fs::relative("index.html", current_directory).generic_string()
      << "\">VoCoType Docs</a><a href=\"/zh.html\">项目主页</a>"
      << "<a href=\"https://github.com/LeonardNJU/VocoType-linux\">GitHub</a>"
      << "<details><summary>文档目录</summary><ul>";
  for (const auto &document : documents) {
    output << "<li><a href=\""
           << fs::relative(document.output, current_directory).generic_string()
           << "\">" << escape_html(document.title) << "</a></li>";
  }
  output << "</ul></details></nav>";
  return output.str();
}

std::string page_html(const Document &document, const std::string &markdown,
                      const std::vector<Document> &documents) {
  const fs::path depth = document.output.parent_path();
  const std::string root = fs::relative(".", depth).generic_string();
  std::ostringstream output;
  output
      << "<!doctype html><html lang=\"zh-CN\"><head>"
      << "<meta charset=\"utf-8\"><meta name=\"viewport\" "
         "content=\"width=device-width,initial-scale=1\">"
      << "<title>" << escape_html(document.title) << " · VoCoType Linux</title>"
      << "<link rel=\"icon\" href=\"" << root << "/favicon.svg\">"
      << "<style>"
         ":root{color-scheme:light dark;--bg:#fff;--fg:#172033;--muted:#667085;"
         "--card:#f7f8fb;--link:#3267d6;--border:#d8deea}"
         "@media(prefers-color-scheme:dark){:root{--bg:#10131a;--fg:#e7eaf0;"
         "--muted:#a5adbd;--card:#191e29;--link:#82aaff;--border:#303849}}"
         "*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var("
         "--fg);"
         "font:16px/1.7 system-ui,-apple-system,BlinkMacSystemFont,'Segoe "
         "UI',sans-serif}"
         "nav{position:sticky;top:0;display:flex;gap:1.1rem;align-items:center;"
         "padding:.8rem 1.2rem;"
         "background:color-mix(in srgb,var(--bg) "
         "92%,transparent);border-bottom:1px solid var(--border);"
         "backdrop-filter:blur(12px);z-index:2}nav .brand{font-weight:750}nav "
         "details{margin-left:auto}"
         "nav "
         "ul{position:absolute;right:1rem;max-height:70vh;overflow:auto;"
         "background:var(--card);"
         "border:1px solid var(--border);padding:1rem "
         "1.5rem;border-radius:.7rem;min-width:20rem}"
         "main{max-width:960px;margin:0 auto;padding:2.5rem 1.4rem 5rem}"
         "h1,h2,h3{line-height:1.25;margin-top:1.8em}a{color:var(--link);text-"
         "decoration:none}"
         "a:hover{text-decoration:underline}pre{overflow:auto;padding:1rem;"
         "border-radius:.7rem;"
         "background:var(--card);border:1px solid "
         "var(--border)}code{font-family:ui-monospace,monospace}"
         "pre code{display:block;white-space:pre;min-width:max-content;"
         "font-size:.92rem;line-height:1.55;tab-size:2}"
         "p code,li code{padding:.12rem "
         ".3rem;background:var(--card);border-radius:.3rem}"
         "table{border-collapse:collapse;width:100%;overflow:auto;display:"
         "block}th,td{border:1px solid var(--border);"
         "padding:.45rem "
         ".65rem;text-align:left}blockquote,aside{border-left:4px solid "
         "var(--link);"
         "margin:1.2rem 0;padding:.6rem "
         "1rem;background:var(--card)}footer{color:var(--muted);"
         "border-top:1px solid var(--border);padding-top:1rem;margin-top:3rem}"
      << "</style></head><body>" << navigation(documents, document.output)
      << "<main>" << markdown_to_html(markdown, document.source)
      << "<footer>Generated by the compiled VoCoType documentation builder. "
         "Source: <a "
         "href=\"https://github.com/LeonardNJU/VocoType-linux/tree/master/"
         "docs\">docs/</a>"
         "</footer></main></body></html>";
  return output.str();
}

} // namespace

int main(int argc, char **argv) {
  try {
    if (argc != 3) {
      std::cerr << "Usage: vocotype-docs-builder DOCS_DIR OUTPUT_DIR\n";
      return 2;
    }
    const fs::path docs = fs::absolute(argv[1]);
    const fs::path output = fs::absolute(argv[2]);
    fs::remove_all(output);
    fs::create_directories(output);

    std::vector<Document> documents;
    for (const auto &entry : fs::recursive_directory_iterator(docs)) {
      if (!entry.is_regular_file() || entry.path().extension() != ".md")
        continue;
      const fs::path relative = fs::relative(entry.path(), docs);
      fs::path target = relative;
      if (target.filename() == "README.md")
        target.replace_filename("index.html");
      else
        target.replace_extension(".html");
      const std::string markdown = read_text(entry.path());
      documents.push_back(
          {entry.path(), relative, target, page_title(markdown, relative)});
    }
    std::sort(documents.begin(), documents.end(),
              [](const Document &left, const Document &right) {
                return left.relative < right.relative;
              });
    for (const auto &document : documents) {
      const std::string markdown = read_text(document.source);
      validate_markdown_links(document, markdown);
      write_text(output / document.output,
                 page_html(document, markdown, documents));
    }
    std::cout << "Generated " << documents.size() << " documentation pages in "
              << output << '\n';
    return documents.empty() ? 1 : 0;
  } catch (const std::exception &error) {
    std::cerr << "vocotype-docs-builder: " << error.what() << '\n';
    return 1;
  }
}
