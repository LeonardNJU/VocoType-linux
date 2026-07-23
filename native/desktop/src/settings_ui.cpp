#include "vocotype/desktop/settings_ui.hpp"

namespace vocotype::desktop::settings_ui {
namespace {

constexpr const char *kCss = R"CSS(
window {
  background-color: @theme_bg_color;
  color: @theme_fg_color;
}
headerbar {
  background-color: @theme_bg_color;
  color: @theme_fg_color;
  border-bottom: 1px solid alpha(@theme_fg_color, 0.16);
}
.sidebar {
  background-color: shade(@theme_bg_color, 0.96);
  color: @theme_fg_color;
  border-right: 1px solid alpha(@theme_fg_color, 0.16);
  padding: 12px;
}
.page { padding: 28px 34px; }
.page-title {
  font-size: 24px;
  font-weight: 700;
  color: @theme_fg_color;
}
.page-subtitle {
  font-size: 14px;
  color: alpha(@theme_fg_color, 0.68);
  margin-bottom: 14px;
}
.section-title {
  font-size: 17px;
  font-weight: 700;
  color: @theme_fg_color;
  margin-top: 8px;
}
.card {
  background-color: @theme_base_color;
  color: @theme_text_color;
  border: 1px solid alpha(@theme_fg_color, 0.16);
  border-radius: 12px;
  padding: 4px;
}
.card-row {
  padding: 12px 14px;
  border-bottom: 1px solid alpha(@theme_fg_color, 0.10);
}
.row-title {
  font-size: 15px;
  font-weight: 600;
  color: @theme_text_color;
}
.row-subtitle {
  font-size: 12px;
  color: alpha(@theme_text_color, 0.68);
}
.status-pass { color: #168b46; font-weight: 600; }
.status-warn { color: #a66a00; font-weight: 600; }
.status-fail { color: #bf2c2c; font-weight: 600; }
.monospace { font-family: monospace; }
.preview {
  background-color: shade(@theme_base_color, 0.96);
  color: @theme_text_color;
  border-radius: 8px;
  padding: 12px;
}
.waveform {
  background-color: shade(@theme_base_color, 0.96);
  border: 1px solid alpha(@theme_fg_color, 0.18);
  border-radius: 8px;
}
.accent {
  background-color: @theme_selected_bg_color;
  color: @theme_selected_fg_color;
  border-radius: 8px;
  padding: 8px 15px;
}
stacksidebar row { min-height: 36px; }
textview { padding: 8px; }
)CSS";

GtkWidget *make_label(const char *text, const char *style_class = nullptr) {
  GtkWidget *widget = gtk_label_new(text ? text : "");
  gtk_label_set_xalign(GTK_LABEL(widget), 0.0F);
  gtk_label_set_line_wrap(GTK_LABEL(widget), TRUE);
  gtk_label_set_line_wrap_mode(GTK_LABEL(widget), PANGO_WRAP_WORD_CHAR);
  if (style_class)
    add_class(widget, style_class);
  return widget;
}

} // namespace

void add_class(GtkWidget *widget, const char *name) {
  if (!widget || !name)
    return;
  gtk_style_context_add_class(gtk_widget_get_style_context(widget), name);
}

void apply_css() {
  GtkCssProvider *provider = gtk_css_provider_new();
  GError *error = nullptr;
  gtk_css_provider_load_from_data(provider, kCss, -1, &error);
  if (error) {
    g_warning("VoCoType settings CSS failed: %s", error->message);
    g_error_free(error);
  }
  GdkScreen *screen = gdk_screen_get_default();
  if (screen) {
    gtk_style_context_add_provider_for_screen(
        screen, GTK_STYLE_PROVIDER(provider),
        GTK_STYLE_PROVIDER_PRIORITY_APPLICATION);
  }
  g_object_unref(provider);
}

Page make_page(const char *title, const char *subtitle) {
  Page page;
  page.scroller = gtk_scrolled_window_new(nullptr, nullptr);
  gtk_scrolled_window_set_policy(GTK_SCROLLED_WINDOW(page.scroller),
                                 GTK_POLICY_NEVER, GTK_POLICY_AUTOMATIC);
  page.content = gtk_box_new(GTK_ORIENTATION_VERTICAL, 16);
  add_class(page.content, "page");
  gtk_box_pack_start(GTK_BOX(page.content), make_label(title, "page-title"),
                     FALSE, FALSE, 0);
  gtk_box_pack_start(GTK_BOX(page.content),
                     make_label(subtitle, "page-subtitle"), FALSE, FALSE, 0);
  gtk_container_add(GTK_CONTAINER(page.scroller), page.content);
  return page;
}

GtkWidget *make_card() {
  GtkWidget *box = gtk_box_new(GTK_ORIENTATION_VERTICAL, 0);
  add_class(box, "card");
  return box;
}

GtkWidget *make_section_heading(const char *title, const char *subtitle) {
  GtkWidget *box = gtk_box_new(GTK_ORIENTATION_VERTICAL, 3);
  gtk_box_pack_start(GTK_BOX(box), make_label(title, "section-title"), FALSE,
                     FALSE, 0);
  if (subtitle && *subtitle) {
    gtk_box_pack_start(GTK_BOX(box), make_label(subtitle, "row-subtitle"),
                       FALSE, FALSE, 0);
  }
  return box;
}

GtkWidget *make_row(const char *title, const char *subtitle,
                    GtkWidget *control) {
  GtkWidget *row = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 16);
  add_class(row, "card-row");
  GtkWidget *labels = gtk_box_new(GTK_ORIENTATION_VERTICAL, 3);
  gtk_box_pack_start(GTK_BOX(labels), make_label(title, "row-title"), FALSE,
                     FALSE, 0);
  if (subtitle && *subtitle) {
    gtk_box_pack_start(GTK_BOX(labels), make_label(subtitle, "row-subtitle"),
                       FALSE, FALSE, 0);
  }
  gtk_box_pack_start(GTK_BOX(row), labels, TRUE, TRUE, 0);
  if (control) {
    gtk_widget_set_valign(control, GTK_ALIGN_CENTER);
    gtk_box_pack_end(GTK_BOX(row), control, FALSE, FALSE, 0);
  }
  return row;
}

GtkWidget *make_switch() {
  GtkWidget *widget = gtk_switch_new();
  gtk_widget_set_halign(widget, GTK_ALIGN_END);
  return widget;
}

GtkWidget *make_entry(int width_chars) {
  GtkWidget *entry = gtk_entry_new();
  gtk_entry_set_width_chars(GTK_ENTRY(entry),
                            width_chars < 30 ? 30 : width_chars);
  gtk_entry_set_max_width_chars(GTK_ENTRY(entry),
                                width_chars < 60 ? 60 : width_chars);
  gtk_widget_set_hexpand(entry, FALSE);
  gtk_widget_set_halign(entry, GTK_ALIGN_END);
  return entry;
}

GtkWidget *make_scrolled_text(GtkTextView **out, int min_height, bool monospace,
                              GtkWrapMode wrap) {
  GtkWidget *scroll = gtk_scrolled_window_new(nullptr, nullptr);
  gtk_scrolled_window_set_policy(GTK_SCROLLED_WINDOW(scroll),
                                 GTK_POLICY_AUTOMATIC, GTK_POLICY_AUTOMATIC);
  gtk_scrolled_window_set_min_content_height(GTK_SCROLLED_WINDOW(scroll),
                                             min_height);
  GtkWidget *view = gtk_text_view_new();
  gtk_text_view_set_wrap_mode(GTK_TEXT_VIEW(view), wrap);
  gtk_text_view_set_monospace(GTK_TEXT_VIEW(view), monospace);
  gtk_container_add(GTK_CONTAINER(scroll), view);
  if (out)
    *out = GTK_TEXT_VIEW(view);
  return scroll;
}

GtkWidget *make_preview_label(const char *text) {
  GtkWidget *widget = make_label(text, "preview");
  gtk_label_set_selectable(GTK_LABEL(widget), TRUE);
  gtk_widget_set_hexpand(widget, TRUE);
  return widget;
}

GtkWidget *make_button_row() {
  return gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 8);
}

GtkWidget *make_status_label(const char *text) {
  GtkWidget *widget = make_label(text);
  gtk_label_set_selectable(GTK_LABEL(widget), TRUE);
  return widget;
}

void set_button_suggested(GtkWidget *button) {
  add_class(button, "suggested-action");
}

} // namespace vocotype::desktop::settings_ui
