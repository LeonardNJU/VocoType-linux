#pragma once

#include <gtk/gtk.h>

#include <string>

namespace vocotype::desktop::settings_ui {

struct Page {
  GtkWidget *scroller = nullptr;
  GtkWidget *content = nullptr;
};

void apply_css();
void add_class(GtkWidget *widget, const char *name);
Page make_page(const char *title, const char *subtitle);
GtkWidget *make_card();
GtkWidget *make_section_heading(const char *title, const char *subtitle = "");
GtkWidget *make_row(const char *title, const char *subtitle = "",
                    GtkWidget *control = nullptr);
GtkWidget *make_switch();
GtkWidget *make_entry(int width_chars = 30);
GtkWidget *make_scrolled_text(GtkTextView **out, int min_height = 150,
                              bool monospace = false,
                              GtkWrapMode wrap = GTK_WRAP_WORD_CHAR);
GtkWidget *make_preview_label(const char *text);
GtkWidget *make_button_row();
GtkWidget *make_status_label(const char *text = "");
void set_button_suggested(GtkWidget *button);

} // namespace vocotype::desktop::settings_ui
