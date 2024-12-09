"""Widget generators and their signal handlers"""

import os
from collections import defaultdict

# Standard Library
# pylint: disable=no-member,too-many-public-methods
from gettext import gettext as _
from typing import DefaultDict

# Third Party Libraries
from gi.repository import Gtk

# Lutris Modules
from lutris import settings, sysoptions
from lutris.config import LutrisConfig
from lutris.game import Game
from lutris.gui.config.widget_generator import ConfigErrorBox, ConfigWarningBox, WidgetGenerator
from lutris.gui.widgets.common import Label, VBox
from lutris.runners import InvalidRunnerError, import_runner
from lutris.util.log import logger
from lutris.util.wine.wine import clear_wine_version_cache


class ConfigBox(VBox):
    """Dynamically generate a vbox built upon on a python dict."""

    config_section = NotImplemented

    def __init__(self, config_level: str, lutris_config: LutrisConfig, game: Game = None) -> None:
        super().__init__()
        self.options = []
        self.config_level = config_level
        self.lutris_config = lutris_config
        self.game = game
        self.config = None
        self.raw_config = None
        self.files = []
        self.files_list_store = None
        self.reset_buttons = {}
        self.wrappers = {}
        self.warning_boxes: DefaultDict[str, list] = defaultdict(list)
        self.error_boxes: DefaultDict[str, list] = defaultdict(list)
        self._advanced_visibility = False
        self._filter = ""

    @property
    def advanced_visibility(self):
        return self._advanced_visibility

    @advanced_visibility.setter
    def advanced_visibility(self, value):
        """Sets the visibility of every 'advanced' option and every section that
        contains only 'advanced' options."""
        self._advanced_visibility = value
        self.update_option_visibility()

    @property
    def filter(self):
        return self._filter

    @filter.setter
    def filter(self, value):
        """Sets the visibility of the options that have some text in the label or
        help-text."""
        self._filter = value
        self.update_option_visibility()

    def update_option_visibility(self):
        """Recursively searches out all the options and shows or hides them according to
        the filter and advanced-visibility settings."""

        def update_widgets(widgets):
            filter_text = self.filter.lower()

            visible_count = 0
            for widget in widgets:
                if isinstance(widget, ConfigBox.SectionFrame):
                    frame_visible_count = update_widgets(widget.vbox.get_children())
                    visible_count += frame_visible_count
                    widget.set_visible(frame_visible_count > 0)
                else:
                    widget_visible = self.advanced_visibility or not widget.get_style_context().has_class("advanced")
                    if widget_visible and filter_text and hasattr(widget, "lutris_option_label"):
                        label = widget.lutris_option_label.lower()
                        helptext = widget.lutris_option_helptext.lower()
                        if filter_text not in label and filter_text not in helptext:
                            widget_visible = False
                    widget.set_visible(widget_visible)
                    widget.set_no_show_all(not widget_visible)
                    if widget_visible:
                        visible_count += 1
                        widget.show_all()

            return visible_count

        update_widgets(self.get_children())

    def generate_top_info_box(self, text):
        """Add a top section with general help text for the current tab"""
        help_box = Gtk.Box()
        help_box.set_margin_left(15)
        help_box.set_margin_right(15)
        help_box.set_margin_bottom(5)

        icon = Gtk.Image.new_from_icon_name("dialog-information", Gtk.IconSize.MENU)
        help_box.pack_start(icon, False, False, 5)

        title_label = Gtk.Label("<i>%s</i>" % text)
        title_label.set_line_wrap(True)
        title_label.set_alignment(0, 0.5)
        title_label.set_use_markup(True)
        help_box.pack_start(title_label, False, False, 5)

        self.pack_start(help_box, False, False, 0)
        self.pack_start(Gtk.HSeparator(), False, False, 12)

        help_box.show_all()

    def get_widget_generator(self):
        gen = WidgetGenerator()
        gen.changed.register(self.on_option_changed)

        if self.game and self.game.directory:
            gen.default_directory = self.game.directory
        elif self.game and self.game.has_runner:
            gen.default_directory = self.game.runner.working_dir
        elif self.lutris_config:
            gen.default_directory = self.lutris_config.system_config.get("game_path") or os.path.expanduser("~")
        else:
            gen.default_directory = os.path.expanduser("~")

        return gen

    def generate_widgets(self):  # noqa: C901 # pylint: disable=too-many-branches,too-many-statements
        """Parse the config dict and generates widget accordingly."""
        if not self.options:
            no_options_label = Label(_("No options available"), width_request=-1)
            no_options_label.set_halign(Gtk.Align.CENTER)
            no_options_label.set_valign(Gtk.Align.CENTER)
            self.pack_start(no_options_label, True, True, 0)
            self.show_all()
            return

        # Select config section.
        if self.config_section == "game":
            self.config = self.lutris_config.game_config
            self.raw_config = self.lutris_config.raw_game_config
        elif self.config_section == "runner":
            self.config = self.lutris_config.runner_config
            self.raw_config = self.lutris_config.raw_runner_config
        elif self.config_section == "system":
            self.config = self.lutris_config.system_config
            self.raw_config = self.lutris_config.raw_system_config

        current_section = None
        current_vbox = self
        gen = self.get_widget_generator()

        # Go thru all options.
        for option in self.options:
            option = option.copy()  # we will mutate this, so let's not alter the original

            try:
                if "scope" in option:
                    if self.config_level not in option["scope"]:
                        continue
                option_key = option["option"]
                value = self.config.get(option_key)

                if "visible" in option:
                    if callable(option["visible"]):
                        option["visible"] = option["visible"]()

                    if not option["visible"]:
                        continue

                if callable(option.get("choices")) and option["type"] != "choice_with_search":
                    option["choices"] = option["choices"]()
                if callable(option.get("condition")):
                    option["condition"] = option["condition"]()

                if option.get("section") != current_section:
                    current_section = option.get("section")
                    if current_section:
                        frame = ConfigBox.SectionFrame(current_section)
                        current_vbox = frame.vbox
                        self.pack_start(frame, False, False, 0)
                    else:
                        current_vbox = self

                # Generate option widget
                option_widget = gen.generate_widget(option, value)
                wrapper = gen.wrapper
                default = gen.default_value
                tooltip_default = gen.tooltip_default
                self.wrappers[option_key] = wrapper

                if option_key in self.raw_config:
                    self.set_style_property("font-weight", "bold", wrapper)
                elif value != default:
                    self.set_style_property("font-style", "italic", wrapper)

                # Reset button
                reset_btn = Gtk.Button.new_from_icon_name("edit-undo-symbolic", Gtk.IconSize.MENU)
                reset_btn.set_valign(Gtk.Align.CENTER)
                reset_btn.set_margin_bottom(6)
                reset_btn.set_relief(Gtk.ReliefStyle.NONE)
                reset_btn.set_tooltip_text(_("Reset option to global or default config"))
                reset_btn.connect(
                    "clicked",
                    self.on_reset_button_clicked,
                    option,
                    option_widget,
                    wrapper,
                )
                self.reset_buttons[option_key] = reset_btn

                placeholder = Gtk.Box()
                placeholder.set_size_request(32, 32)

                if option_key not in self.raw_config:
                    reset_btn.set_visible(False)
                    reset_btn.set_no_show_all(True)
                placeholder.pack_start(reset_btn, False, False, 0)

                # Tooltip
                helptext = option.get("help")
                if isinstance(tooltip_default, str):
                    helptext = helptext + "\n\n" if helptext else ""
                    helptext += _("<b>Default</b>: ") + _(tooltip_default)
                if value != default and option_key not in self.raw_config:
                    helptext = helptext + "\n\n" if helptext else ""
                    helptext += _(
                        "<i>(Italic indicates that this option is modified in a lower configuration level.)</i>"
                    )
                if helptext:
                    wrapper.props.has_tooltip = True
                    wrapper.connect("query-tooltip", self.on_query_tooltip, helptext)

                hbox = Gtk.Box(visible=True)
                option_container = hbox
                hbox.set_margin_left(18)
                hbox.pack_end(placeholder, False, False, 5)
                hbox.pack_start(wrapper, True, True, 0)

                # Grey out option if condition unmet
                if "condition" in option and not option["condition"]:
                    wrapper.set_sensitive(False)

                if "warning" in option:
                    option_body = option_container
                    option_container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, visible=True)
                    option_container.pack_start(option_body, False, False, 0)
                    warning = ConfigWarningBox(option["warning"], option_key)
                    warning.update_warning(self.lutris_config)
                    self.warning_boxes[option_key].append(warning)
                    option_container.pack_start(warning, False, False, 0)

                if "error" in option:
                    option_body = option_container
                    option_container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, visible=True)
                    option_container.pack_start(option_body, False, False, 0)
                    error = ConfigErrorBox(option["error"], option_key, wrapper)
                    error.update_warning(self.lutris_config)
                    gen.error_widgets.append(error)

                for error_widget in gen.error_widgets:
                    self.error_boxes[option_key].append(error_widget)
                    option_container.pack_start(error_widget, False, False, 0)

                # Hide if advanced
                if option.get("advanced"):
                    option_container.get_style_context().add_class("advanced")

                option_container.lutris_option_key = option_key
                option_container.lutris_option_label = option["label"]
                option_container.lutris_option_helptext = option.get("help") or ""
                current_vbox.pack_start(option_container, False, False, 0)
            except Exception as ex:
                logger.exception("Failed to generate option widget for '%s': %s", option.get("option"), ex)
        self.show_all()

        show_advanced = settings.read_setting("show_advanced_options") == "True"
        self.advanced_visibility = show_advanced

    def update_warnings(self) -> None:
        for box_list in self.warning_boxes.values():
            for box in box_list:
                box.update_warning(self.lutris_config)

        for box_list in self.error_boxes.values():
            for box in box_list:
                box.update_warning(self.lutris_config)

    def on_option_changed(self, option_name, value):
        """Common actions when value changed on a widget"""
        self.raw_config[option_name] = value
        self.config[option_name] = value
        reset_btn = self.reset_buttons.get(option_name)
        wrapper = self.wrappers.get(option_name)

        if reset_btn:
            reset_btn.set_visible(True)

        if wrapper:
            self.set_style_property("font-weight", "bold", wrapper)

        self.update_warnings()

    @staticmethod
    def on_query_tooltip(_widget, x, y, keybmode, tooltip, text):  # pylint: disable=unused-argument
        """Prepare a custom tooltip with a fixed width"""
        label = Label(text)
        label.set_use_markup(True)
        label.set_max_width_chars(60)
        event_box = Gtk.EventBox()
        event_box.add(label)
        event_box.show_all()
        tooltip.set_custom(event_box)
        return True

    def on_reset_button_clicked(self, btn, option, _widget, wrapper):
        """Clear option (remove from config, reset option widget)."""
        option_key = option["option"]
        current_value = self.config[option_key]

        btn.set_visible(False)
        self.set_style_property("font-weight", "normal", wrapper)
        self.raw_config.pop(option_key, None)
        self.lutris_config.update_cascaded_config()

        reset_value = self.config.get(option_key)
        if current_value == reset_value:
            return

        gen = self.get_widget_generator()
        gen.generate_widget(option, reset_value, wrapper=wrapper)
        self.update_warnings()

    @staticmethod
    def set_style_property(property_, value, wrapper):
        """Add custom style."""
        style_provider = Gtk.CssProvider()
        style_provider.load_from_data("GtkHBox {{{}: {};}}".format(property_, value).encode())
        style_context = wrapper.get_style_context()
        style_context.add_provider(style_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    class SectionFrame(Gtk.Frame):
        """A frame that is styled to have particular margins, and can have its frame hidden.
        This leaves the content but removes the margins and borders and all that, so it looks
        like the frame was never there."""

        def __init__(self, section):
            super().__init__(label=section)
            self.section = section
            self.vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            self.add(self.vbox)
            self.get_style_context().add_class("section-frame")


class GameBox(ConfigBox):
    config_section = "game"

    def __init__(self, config_level: str, lutris_config: LutrisConfig, game: Game):
        ConfigBox.__init__(self, config_level, lutris_config, game)
        self.runner = game.runner
        if self.runner:
            self.options = self.runner.game_options
        else:
            logger.warning("No runner in game supplied to GameBox")


class RunnerBox(ConfigBox):
    """Configuration box for runner specific options"""

    config_section = "runner"

    def __init__(self, config_level: str, lutris_config: LutrisConfig, game: Game = None):
        ConfigBox.__init__(self, config_level, lutris_config, game)

        try:
            self.runner = import_runner(self.lutris_config.runner_slug)()
        except InvalidRunnerError:
            self.runner = None
        if self.runner:
            self.options = self.runner.get_runner_options()

        if lutris_config.level == "game":
            self.generate_top_info_box(
                _("If modified, these options supersede the same options from " "the base runner configuration.")
            )

    def generate_widgets(self):
        # Better safe than sorry - we search of Wine versions in directories
        # we do not control, so let's keep up to date more aggresively.
        clear_wine_version_cache()
        return super().generate_widgets()


class SystemConfigBox(ConfigBox):
    config_section = "system"

    def __init__(self, config_level: str, lutris_config: LutrisConfig) -> None:
        ConfigBox.__init__(self, config_level, lutris_config)
        self.runner = None
        runner_slug = self.lutris_config.runner_slug

        if runner_slug:
            self.options = sysoptions.with_runner_overrides(runner_slug)
        else:
            self.options = sysoptions.system_options

        if lutris_config.game_config_id and runner_slug:
            self.generate_top_info_box(
                _(
                    "If modified, these options supersede the same options from "
                    "the base runner configuration, which themselves supersede "
                    "the global preferences."
                )
            )
        elif runner_slug:
            self.generate_top_info_box(
                _("If modified, these options supersede the same options from " "the global preferences.")
            )
