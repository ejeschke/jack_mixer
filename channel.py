# This file is part of jack_mixer
#
# Copyright (C) 2006 Nedko Arnaudov <nedko@arnaudov.name>
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; version 2 of the License
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin St, Fifth Floor, Boston, MA 02110-1301 USA.

import logging

import gi
from gi.repository import Gtk
from gi.repository import Gdk
from gi.repository import GObject
from gi.repository import Pango

import abspeak
import meter
import slider
from serialization import SerializedObject


log = logging.getLogger(__name__)
button_padding = 1
css = b"""
.top_label {
    padding: 0px .1em;
    min-height: 1.5rem;
}

.wide {
    font-size: medium
}

.narrow {
    font-size: smaller
}

button {
    padding: 0px
}
"""
css_provider = Gtk.CssProvider()
css_provider.load_from_data(css)
context = Gtk.StyleContext()
screen = Gdk.Screen.get_default()
context.add_provider_for_screen(screen, css_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)


def get_text_color(background_color):
    """Calculates the luminance of the given color (GdkRGBA)
       and returns an appropriate text color."""
    # luminance coefficients taken from section C-9 from
    # http://www.faqs.org/faqs/graphics/colorspace-faq/
    brightess = background_color.red * 0.212671 + \
            background_color.green * 0.715160 + \
            background_color.blue * 0.072169

    if brightess > 0.5:
        return 'black'
    else:
        return 'white'


def set_background_color(widget, name, color):
    color_string = color.to_string()
    css = """
    .%s {
        background-color: %s;
        color: %s;
    }
""" % (name, color_string, get_text_color(color))

    css_provider = Gtk.CssProvider()
    css_provider.load_from_data(css.encode('utf-8'))
    context = Gtk.StyleContext()
    screen = Gdk.Screen.get_default()
    context.add_provider_for_screen(screen, css_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    widget_context = widget.get_style_context()
    widget_context.add_class(name)


def random_color():
    from random import uniform, seed
    seed()
    return Gdk.RGBA(uniform(0, 1), uniform(0, 1), uniform(0, 1), 1)


class Channel(Gtk.VBox, SerializedObject):
    '''Widget with slider and meter used as base class for more specific
       channel widgets'''
    monitor_button = None
    num_instances = 0
    def __init__(self, app, name, stereo, value = None):
        Gtk.VBox.__init__(self)
        self.app = app
        self.mixer = app.mixer
        self.channel = None
        self.gui_factory = app.gui_factory
        self._channel_name = name
        self.stereo = stereo
        self.initial_value = value
        self.meter_scale = self.gui_factory.get_default_meter_scale()
        self.slider_scale = self.gui_factory.get_default_slider_scale()
        self.slider_adjustment = slider.AdjustmentdBFS(self.slider_scale, 0.0, 0.02)
        self.balance_adjustment = slider.BalanceAdjustment()
        self.post_fader_output_channel = None
        self.future_out_mute = None
        self.future_volume_midi_cc = None
        self.future_balance_midi_cc = None
        self.future_mute_midi_cc = None
        self.future_solo_midi_cc = None
        self.css_name = "css_name_%d" % Channel.num_instances
        self.label_name = None
        self.wide = True
        self.label_chars_wide = 12
        self.label_chars_narrow = 8
        Channel.num_instances += 1

    def get_channel_name(self):
        return self._channel_name

    def set_channel_name(self, name):
        self.app.on_channel_rename(self._channel_name, name);
        self._channel_name = name
        if self.label_name:
            self.label_name.set_text(name)
            if len(name) > (self.label_chars_wide if self.wide else self.label_chars_narrow):
                self.label_name.set_tooltip_text(name)
        if self.channel:
            self.channel.name = name
        if self.post_fader_output_channel:
            self.post_fader_output_channel.name = "%s Out" % name;
    channel_name = property(get_channel_name, set_channel_name)

    def realize(self):
        log.debug('Realizing channel "%s".', self.channel_name)
        if self.future_out_mute != None:
            self.channel.out_mute = self.future_out_mute

        # Widgets
        # Channel strip label
        self.vbox = Gtk.VBox()
        self.pack_start(self.vbox, False, True, 0)
        self.label_name = Gtk.Label()
        self.label_name.get_style_context().add_class('top_label')
        self.label_name.set_text(self.channel_name)
        self.label_name.set_max_width_chars(self.label_chars_wide if self.wide else
                                            self.label_chars_narrow)
        self.label_name.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        self.label_name_event_box = Gtk.EventBox()
        self.label_name_event_box.connect('button-press-event', self.on_label_mouse)
        self.label_name_event_box.add(self.label_name)

        # Volume fader
        self.slider = None
        self.create_slider_widget()

        # Volume entry
        self.volume_digits = Gtk.Entry()
        self.volume_digits.set_property('xalign', 0.5)
        self.volume_digits.connect("key-press-event", self.on_volume_digits_key_pressed)
        self.volume_digits.connect("focus-out-event", self.on_volume_digits_focus_out)

        # Peak level label
        self.abspeak = abspeak.AbspeakWidget()
        self.abspeak.connect("reset", self.on_abspeak_reset)
        self.abspeak.connect("volume-adjust", self.on_abspeak_adjust)

        # Level meter
        if self.stereo:
            self.meter = meter.StereoMeterWidget(self.meter_scale)
        else:
            self.meter = meter.MonoMeterWidget(self.meter_scale)

        self.meter.set_events(Gdk.EventMask.SCROLL_MASK)
        self.on_vumeter_color_changed(self.gui_factory)

        if self.initial_value != None:
            if self.initial_value == True:
                self.slider_adjustment.set_value(0)
            else:
                self.slider_adjustment.set_value_db(0)

        self.slider_adjustment.connect("volume-changed", self.on_volume_changed)
        self.slider_adjustment.connect("volume-changed-from-midi", self.on_volume_changed_from_midi)
        self.balance_adjustment.connect("balance-changed", self.on_balance_changed)

        self.gui_factory.connect("default-meter-scale-changed", self.on_default_meter_scale_changed)
        self.gui_factory.connect("default-slider-scale-changed", self.on_default_slider_scale_changed)
        self.gui_factory.connect('vumeter-color-changed', self.on_vumeter_color_changed)
        self.gui_factory.connect('vumeter-color-scheme-changed', self.on_vumeter_color_changed)
        self.gui_factory.connect('use-custom-widgets-changed', self.on_custom_widgets_changed)

        self.connect("key-press-event", self.on_key_pressed)
        self.connect("scroll-event", self.on_scroll)

    def unrealize(self):
        log.debug('Unrealizing channel "%s".', self.channel_name)
        pass

    def narrow(self):
        self.wide = False
        ctx = self.label_name.get_style_context()
        ctx.remove_class('wide')
        ctx.add_class('narrow')
        label = self.label_name.get_label()
        self.label_name.set_max_width_chars(self.label_chars_narrow)

        if len(label) > self.label_chars_narrow:
            self.label_name.set_tooltip_text(label)

    def widen(self):
        self.wide = True
        self.label_name.set_tooltip_text(None)
        ctx = self.label_name.get_style_context()
        ctx.remove_class('narrow')
        ctx.add_class('wide')
        label = self.label_name.get_label()
        self.label_name.set_max_width_chars(self.label_chars_wide)

        if len(label) > self.label_chars_wide:
            self.label_name.set_tooltip_text(label)

    def create_balance_widget(self):
        self.balance = slider.BalanceSlider(self.balance_adjustment, (20, 20), (0, 100))
        self.pack_start(self.balance, False, True, 0)

        if self.monitor_button:
            self.reorder_child(self.monitor_button, -1)

        self.balance.show()

    def on_label_mouse(self, widget, event):
        if event.type == Gdk.EventType._2BUTTON_PRESS:
            if event.button == 1:
                self.on_channel_properties()
            return True
        elif (event.state & Gdk.ModifierType.CONTROL_MASK and
              event.type == Gdk.EventType.BUTTON_PRESS and
              event.button == 1):
            if self.wide:
                self.narrow()
            else:
                self.widen()
            return True

    def create_slider_widget(self):
        parent = None
        if self.slider:
            parent = self.slider.get_parent()
            self.slider.destroy()

        if self.gui_factory.use_custom_widgets:
            self.slider = slider.CustomSliderWidget(self.slider_adjustment)
        else:
            self.slider = slider.VolumeSlider(self.slider_adjustment)

        if parent:
            parent.pack_start(self.slider, True, True, 0)
            parent.reorder_child(self.slider, 0)

        self.slider.show()

    def on_default_meter_scale_changed(self, gui_factory, scale):
        log.debug("Default meter scale change detected.")
        self.meter.set_scale(scale)

    def on_default_slider_scale_changed(self, gui_factory, scale):
        log.debug("Default slider scale change detected.")
        self.slider_scale = scale
        self.slider_adjustment.set_scale(scale)
        if self.channel:
            self.channel.midi_scale = self.slider_scale.scale

    def on_vumeter_color_changed(self, gui_factory, *args):
        color = gui_factory.get_vumeter_color()
        color_scheme = gui_factory.get_vumeter_color_scheme()
        if color_scheme != 'solid':
            self.meter.set_color(None)
        else:
            self.meter.set_color(Gdk.color_parse(color))

    def on_custom_widgets_changed(self, gui_factory, value):
        self.balance.destroy()
        self.create_balance_widget()
        self.create_slider_widget()

    def on_abspeak_adjust(self, abspeak, adjust):
        log.debug("abspeak adjust %f", adjust)
        self.slider_adjustment.set_value_db(self.slider_adjustment.get_value_db() + adjust)
        self.channel.abspeak = None
        #self.update_volume(False)   # We want to update gui even if actual decibels have not changed (scale wrap for example)

    def on_abspeak_reset(self, abspeak):
        log.debug("abspeak reset")
        self.channel.abspeak = None

    def on_volume_digits_key_pressed(self, widget, event):
        if (event.keyval == Gdk.KEY_Return or event.keyval == Gdk.KEY_KP_Enter):
            db_text = self.volume_digits.get_text()
            try:
                db = float(db_text)
                log.debug('Volume digits confirmation "%f dBFS".', db)
            except (ValueError) as e:
                log.debug("Volume digits confirmation ignore, reset to current.")
                self.update_volume(False)
                return
            self.slider_adjustment.set_value_db(db)
            #self.grab_focus()
            #self.update_volume(False)   # We want to update gui even if actual decibels have not changed (scale wrap for example)

    def on_volume_digits_focus_out(self, widget, event):
        log.debug("Volume digits focus out detected.")
        self.update_volume(False)

    def read_meter(self):
        if not self.channel:
            return
        if self.stereo:
            peak_left, peak_right, rms_left, rms_right = self.channel.kmeter
            self.meter.set_values(peak_left, peak_right, rms_left, rms_right)
        else:
            peak, rms = self.channel.kmeter
            self.meter.set_values(peak, rms)

        self.abspeak.set_peak(self.channel.abspeak)

    def on_scroll(self, widget, event):
        if event.direction == Gdk.ScrollDirection.DOWN:
            self.slider_adjustment.step_down()
        elif event.direction == Gdk.ScrollDirection.UP:
            self.slider_adjustment.step_up()
        return True

    def update_volume(self, update_engine, from_midi = False):
        db = self.slider_adjustment.get_value_db()

        db_text = "%.2f" % db
        self.volume_digits.set_text(db_text)

        if update_engine:
            if not from_midi:
                self.channel.volume = db
            self.app.update_monitor(self)

    def on_volume_changed(self, adjustment):
        self.update_volume(True)

    def on_volume_changed_from_midi(self, adjustment):
        self.update_volume(True, from_midi = True)

    def on_balance_changed(self, adjustment):
        balance = self.balance_adjustment.get_value()
        log.debug("%s balance: %f", self.channel_name, balance)
        self.channel.balance = balance
        self.app.update_monitor(self)

    def on_key_pressed(self, widget, event):
        if (event.keyval == Gdk.KEY_Up):
            log.debug(self.channel_name + " Up")
            self.slider_adjustment.step_up()
            return True
        elif (event.keyval == Gdk.KEY_Down):
            log.debug(self.channel_name + " Down")
            self.slider_adjustment.step_down()
            return True

        return False

    def serialize(self, object_backend):
        object_backend.add_property("volume", "%f" % self.slider_adjustment.get_value_db())
        object_backend.add_property("balance", "%f" % self.balance_adjustment.get_value())
        object_backend.add_property("wide", "%s" % str(self.wide))

        if hasattr(self.channel, 'out_mute'):
            object_backend.add_property('out_mute', str(self.channel.out_mute))
        if self.channel.volume_midi_cc != -1:
            object_backend.add_property('volume_midi_cc', str(self.channel.volume_midi_cc))
        if self.channel.balance_midi_cc != -1:
            object_backend.add_property('balance_midi_cc', str(self.channel.balance_midi_cc))
        if self.channel.mute_midi_cc != -1:
            object_backend.add_property('mute_midi_cc', str(self.channel.mute_midi_cc))
        if self.channel.solo_midi_cc != -1:
            object_backend.add_property('solo_midi_cc', str(self.channel.solo_midi_cc))


    def unserialize_property(self, name, value):
        if name == "volume":
            self.slider_adjustment.set_value_db(float(value))
            return True
        if name == "balance":
            self.balance_adjustment.set_value(float(value))
            return True
        if name == 'out_mute':
            self.future_out_mute = (value == 'True')
            return True
        if name == 'volume_midi_cc':
            self.future_volume_midi_cc = int(value)
            return True
        if name == 'balance_midi_cc':
            self.future_balance_midi_cc = int(value)
            return True
        if name == 'mute_midi_cc':
            self.future_mute_midi_cc = int(value)
            return True
        if name == 'solo_midi_cc':
            self.future_solo_midi_cc = int(value)
            return True
        if name == "wide":
            self.wide = value == "True"
            return True
        return False

    def on_midi_event_received(self, *args):
        self.slider_adjustment.set_value_db(self.channel.volume, from_midi = True)
        self.balance_adjustment.set_balance(self.channel.balance, from_midi = True)

    def on_monitor_button_toggled(self, button):
        if button.get_active():
            for channel in self.app.channels + self.app.output_channels:
                if channel.monitor_button.get_active() and channel.monitor_button is not button:
                    channel.monitor_button.handler_block_by_func(
                                channel.on_monitor_button_toggled)
                    channel.monitor_button.set_active(False)
                    channel.monitor_button.handler_unblock_by_func(
                                channel.on_monitor_button_toggled)
            self.app.set_monitored_channel(self)
        else:
            if self.app._monitored_channel.channel.name == self.channel.name:
                self.monitor_button.handler_block_by_func(self.on_monitor_button_toggled)
                self.monitor_button.set_active(True)
                self.monitor_button.handler_unblock_by_func(self.on_monitor_button_toggled)

    def set_monitored(self):
        if self.channel:
            self.app.set_monitored_channel(self)
        self.monitor_button.set_active(True)

    def set_color(self, color):
        self.color = color
        set_background_color(self.label_name_event_box, self.css_name, self.color)

class InputChannel(Channel):
    post_fader_output_channel = None

    def realize(self):
        self.channel = self.mixer.add_channel(self.channel_name, self.stereo)

        if self.channel == None:
            raise Exception("Cannot create a channel")

        super().realize()

        if self.future_volume_midi_cc != None:
            self.channel.volume_midi_cc = self.future_volume_midi_cc
        if self.future_balance_midi_cc != None:
            self.channel.balance_midi_cc = self.future_balance_midi_cc
        if self.future_mute_midi_cc != None:
            self.channel.mute_midi_cc = self.future_mute_midi_cc
        if self.future_solo_midi_cc != None:
            self.channel.solo_midi_cc = self.future_solo_midi_cc
        if self.app._init_solo_channels and self.channel_name in self.app._init_solo_channels:
            self.channel.solo = True

        self.channel.midi_scale = self.slider_scale.scale

        self.on_volume_changed(self.slider_adjustment)
        self.on_balance_changed(self.balance_adjustment)

        entries = [Gtk.TargetEntry.new("INPUT_CHANNEL", Gtk.TargetFlags.SAME_APP, 0)]
        self.label_name_event_box.drag_source_set(Gdk.ModifierType.BUTTON1_MASK, entries,
                Gdk.DragAction.MOVE)
        self.label_name_event_box.connect("drag-data-get", self.on_drag_data_get)
        self.drag_dest_set(Gtk.DestDefaults.ALL, entries, Gdk.DragAction.MOVE)
        self.connect_after("drag-data-received", self.on_drag_data_received)

        self.vbox.pack_start(self.label_name_event_box, True, True, 0)
#         self.label_stereo = Gtk.Label()
#         if self.stereo:
#             self.label_stereo.set_text("stereo")
#         else:
#             self.label_stereo.set_text("mono")
#         self.label_stereo.set_size_request(0, -1)
#         self.vbox.pack_start(self.label_stereo, True)

        frame = Gtk.Frame()
        frame.set_shadow_type(Gtk.ShadowType.IN)
        frame.add(self.abspeak);
        self.pack_start(frame, False, True, 0)

        # hbox child at lower part
        self.hbox = Gtk.HBox()
        self.hbox.pack_start(self.slider, True, True, 0)
        frame = Gtk.Frame()
        frame.set_shadow_type(Gtk.ShadowType.IN)
        frame.add(self.meter);
        self.hbox.pack_start(frame, True, True, 0)
        frame = Gtk.Frame()
        frame.set_shadow_type(Gtk.ShadowType.IN)
        frame.add(self.hbox);
        self.pack_start(frame, True, True, 0)

        self.volume_digits.set_width_chars(6)
        self.pack_start(self.volume_digits, False, False, 0)

        self.create_balance_widget()

        self.hbox_mutesolo = Gtk.Box(False, 0)

        self.mute = Gtk.ToggleButton()
        self.mute.set_label("M")
        self.mute.set_name("mute")
        self.mute.set_active(self.channel.out_mute)
        self.mute.connect("toggled", self.on_mute_toggled)
        self.hbox_mutesolo.pack_start(self.mute, True, True, 0)

        self.solo = Gtk.ToggleButton()
        self.solo.set_label("S")
        self.solo.set_name("solo")
        self.solo.set_active(self.channel.solo)
        self.solo.connect("toggled", self.on_solo_toggled)
        self.hbox_mutesolo.pack_start(self.solo, True, True, 0)

        self.pack_start(self.hbox_mutesolo, False, False, 0)

        self.monitor_button = Gtk.ToggleButton('MON')
        self.monitor_button.connect('toggled', self.on_monitor_button_toggled)
        self.pack_start(self.monitor_button, False, False, 0)

        if not self.wide:
            self.narrow()

    def narrow(self):
        super().narrow()
        for cg in self.get_control_groups():
            cg.narrow()

    def widen(self):
        super().widen()
        for cg in self.get_control_groups():
            cg.widen()

    def on_drag_data_get(self, widget, drag_context, data, info, time):
        channel = widget.get_parent().get_parent()
        data.set(data.get_target(), 8, channel._channel_name.encode('utf-8'))

    def on_drag_data_received(self, widget, drag_context, x, y, data, info, time):
        source_name = data.get_data().decode('utf-8')
        if source_name == self._channel_name:
            return
        self.emit("input-channel-order-changed", source_name, self._channel_name)

    def add_control_group(self, channel):
        control_group = ControlGroup(channel, self)
        control_group.show_all()
        self.vbox.pack_start(control_group, True, True, 0)
        return control_group

    def remove_control_group(self, channel):
        ctlgroup = self.get_control_group(channel)
        self.vbox.remove(ctlgroup)

    def update_control_group(self, channel):
        for control_group in self.vbox.get_children():
            if isinstance(control_group, ControlGroup):
                if control_group.output_channel is channel:
                    control_group.update()

    def get_control_group(self, channel):
        for control_group in self.get_control_groups():
            if control_group.output_channel is channel:
                return control_group
        return None

    def get_control_groups(self):
        ctlgroups = []
        for c in self.vbox.get_children():
            if isinstance(c, ControlGroup):
                ctlgroups.append(c)
        return ctlgroups

    def unrealize(self):
        super().unrealize()
        if self.post_fader_output_channel:
            self.post_fader_output_channel.remove()
            self.post_fader_output_channel = None
        self.channel.remove()
        self.channel = None

    channel_properties_dialog = None

    def on_channel_properties(self):
        if not self.channel_properties_dialog:
            self.channel_properties_dialog = ChannelPropertiesDialog(self, self.app)
        self.channel_properties_dialog.show()
        self.channel_properties_dialog.present()

    def on_mute_toggled(self, button):
        self.channel.out_mute = self.mute.get_active()

    def on_solo_toggled(self, button):
        self.channel.solo = self.solo.get_active()

    def midi_events_check(self):
        if hasattr(self, 'channel') and self.channel.midi_in_got_events:
            self.mute.set_active(self.channel.out_mute)
            self.solo.set_active(self.channel.solo)
            super().on_midi_event_received()

    def on_solo_button_pressed(self, button, event, *args):
        if event.button == 3:
            # right click on the solo button, act on all output channels
            if button.get_active(): # was soloed
                button.set_active(False)
                if hasattr(button, 'touched_channels'):
                    touched_channels = button.touched_channels
                    for chan in touched_channels:
                        ctlgroup = self.get_control_group(chan)
                        ctlgroup.solo.set_active(False)
                    del button.touched_channels
            else: # was not soloed
                button.set_active(True)
                touched_channels = []
                for chan in self.app.output_channels:
                    ctlgroup = self.get_control_group(chan)
                    if not ctlgroup.solo.get_active():
                        ctlgroup.solo.set_active(True)
                        touched_channels.append(chan)
                button.touched_channels = touched_channels
            return True
        return False

    @classmethod
    def serialization_name(cls):
        return 'input_channel'

    def serialize(self, object_backend):
        object_backend.add_property("name", self.channel_name)
        if self.stereo:
            object_backend.add_property("type", "stereo")
        else:
            object_backend.add_property("type", "mono")
        super().serialize(object_backend)

    def unserialize_property(self, name, value):
        if name == "name":
            self.channel_name = str(value)
            return True
        if name == "type":
            if value == "stereo":
                self.stereo = True
                return True
            if value == "mono":
                self.stereo = False
                return True
        return super().unserialize_property(name, value)

GObject.signal_new("input-channel-order-changed", InputChannel,
                GObject.SignalFlags.RUN_FIRST | GObject.SignalFlags.ACTION,
                None, [GObject.TYPE_STRING, GObject.TYPE_STRING])

class OutputChannel(Channel):
    _display_solo_buttons = False

    _init_muted_channels = None
    _init_solo_channels = None
    _init_prefader_channels = None

    channel_properties_dialog = None

    def get_display_solo_buttons(self):
        return self._display_solo_buttons

    def set_display_solo_buttons(self, value):
        self._display_solo_buttons = value
        # notifying control groups
        for inputchannel in self.app.channels:
            inputchannel.update_control_group(self)

    display_solo_buttons = property(get_display_solo_buttons, set_display_solo_buttons)

    def realize(self):
        self.channel = self.mixer.add_output_channel(self.channel_name, self.stereo)

        if self.channel == None:
            raise Exception("Cannot create a channel")

        super().realize()

        if self.future_volume_midi_cc != None:
            self.channel.volume_midi_cc = self.future_volume_midi_cc
        if self.future_balance_midi_cc != None:
            self.channel.balance_midi_cc = self.future_balance_midi_cc
        if self.future_mute_midi_cc != None:
            self.channel.mute_midi_cc = self.future_mute_midi_cc
        self.channel.midi_scale = self.slider_scale.scale

        self.on_volume_changed(self.slider_adjustment)
        self.on_balance_changed(self.balance_adjustment)

        entries = [Gtk.TargetEntry.new("OUTPUT_CHANNEL", Gtk.TargetFlags.SAME_APP, 0)]
        self.label_name_event_box.drag_source_set(Gdk.ModifierType.BUTTON1_MASK, entries,
                Gdk.DragAction.MOVE)
        self.label_name_event_box.connect("drag-data-get", self.on_drag_data_get)
        self.drag_dest_set(Gtk.DestDefaults.ALL, entries, Gdk.DragAction.MOVE)
        self.connect_after("drag-data-received", self.on_drag_data_received)

        if not hasattr(self, 'color'):
            self.color = random_color()
        set_background_color(self.label_name_event_box, self.css_name, self.color)
        self.vbox.pack_start(self.label_name_event_box, True, True, 0)
        frame = Gtk.Frame()
        frame.set_shadow_type(Gtk.ShadowType.IN)
        frame.add(self.abspeak);
        self.vbox.pack_start(frame, False, True, 0)

        # hbox child at lower part
        self.hbox = Gtk.HBox()
        self.hbox.pack_start(self.slider, True, True, 0)
        frame = Gtk.Frame()
        frame.set_shadow_type(Gtk.ShadowType.IN)
        frame.add(self.meter);
        self.hbox.pack_start(frame, True, True, 0)
        frame = Gtk.Frame()
        frame.set_shadow_type(Gtk.ShadowType.IN)
        frame.add(self.hbox);
        self.pack_start(frame, True, True, 0)

        self.volume_digits.set_width_chars(6)
        self.pack_start(self.volume_digits, False, True, 0)

        self.create_balance_widget()

        self.mute = Gtk.ToggleButton()
        self.mute.set_label("M")
        self.mute.set_name("mute")
        self.mute.set_active(self.channel.out_mute)
        self.mute.connect("toggled", self.on_mute_toggled)

        hbox = Gtk.HBox()
        hbox.pack_start(self.mute, True, True, 0)
        self.pack_start(hbox, False, False, 0)

        self.monitor_button = Gtk.ToggleButton('MON')
        self.monitor_button.connect('toggled', self.on_monitor_button_toggled)
        self.pack_start(self.monitor_button, False, False, 0)

        # add control groups to the input channels, and initialize them
        # appropriately
        for input_channel in self.app.channels:
            ctlgroup = input_channel.add_control_group(self)
            if self._init_muted_channels and input_channel.channel.name in self._init_muted_channels:
                ctlgroup.mute.set_active(True)
            if self._init_solo_channels and input_channel.channel.name in self._init_solo_channels:
                ctlgroup.solo.set_active(True)
            if self._init_prefader_channels and input_channel.channel.name in self._init_prefader_channels:
                ctlgroup.prefader.set_active(True)
            if not input_channel.wide:
                ctlgroup.narrow()

        self._init_muted_channels = None
        self._init_solo_channels = None
        self._init_prefader_channels = None

        if not self.wide:
            self.narrow()

    def on_drag_data_get(self, widget, drag_context, data, info, time):
        channel = widget.get_parent().get_parent()
        data.set(data.get_target(), 8, channel._channel_name.encode('utf-8'))

    def on_drag_data_received(self, widget, drag_context, x, y, data, info, time):
        source_name = data.get_data().decode('utf-8')
        if source_name == self._channel_name:
            return
        self.emit("output-channel-order-changed", source_name, self._channel_name)

    def on_channel_properties(self):
        if not self.channel_properties_dialog:
            self.channel_properties_dialog = OutputChannelPropertiesDialog(self, self.app)
        self.channel_properties_dialog.show()
        self.channel_properties_dialog.present()

    def on_mute_toggled(self, button):
        self.channel.out_mute = self.mute.get_active()

    def midi_events_check(self):
        if self.channel != None and self.channel.midi_in_got_events:
            self.mute.set_active(self.channel.out_mute)
            super().on_midi_event_received()

    def unrealize(self):
        # remove control groups from input channels
        for input_channel in self.app.channels:
            input_channel.remove_control_group(self)
        # then remove itself
        super().unrealize()
        self.channel.remove()
        self.channel = None

    @classmethod
    def serialization_name(cls):
        return 'output_channel'

    def serialize(self, object_backend):
        object_backend.add_property("name", self.channel_name)
        if self.stereo:
            object_backend.add_property("type", "stereo")
        else:
            object_backend.add_property("type", "mono")
        if self.display_solo_buttons:
            object_backend.add_property("solo_buttons", "true")
        muted_channels = []
        solo_channels = []
        prefader_in_channels = []
        for input_channel in self.app.channels:
            if self.channel.is_muted(input_channel.channel):
                muted_channels.append(input_channel)
            if self.channel.is_solo(input_channel.channel):
                solo_channels.append(input_channel)
            if self.channel.is_in_prefader(input_channel.channel):
                prefader_in_channels.append(input_channel)
        if muted_channels:
            object_backend.add_property('muted_channels', '|'.join([x.channel.name for x in muted_channels]))
        if solo_channels:
            object_backend.add_property('solo_channels', '|'.join([x.channel.name for x in solo_channels]))
        if prefader_in_channels:
            object_backend.add_property('prefader_channels', '|'.join([x.channel.name for x in prefader_in_channels]))
        object_backend.add_property("color", self.color.to_string())
        super().serialize(object_backend)

    def unserialize_property(self, name, value):
        if name == "name":
            self.channel_name = str(value)
            return True
        if name == "type":
            if value == "stereo":
                self.stereo = True
                return True
            if value == "mono":
                self.stereo = False
                return True
        if name == "solo_buttons":
            if value == "true":
                self.display_solo_buttons = True
                return True
        if name == 'muted_channels':
            self._init_muted_channels = value.split('|')
            return True
        if name == 'solo_channels':
            self._init_solo_channels = value.split('|')
            return True
        if name == 'prefader_channels':
            self._init_prefader_channels = value.split('|')
            return True
        if name == 'color':
            c = Gdk.RGBA()
            c.parse(value)
            self.color = c
            return True
        return super().unserialize_property(name, value)

class ChannelPropertiesDialog(Gtk.Dialog):
    channel = None

    def __init__(self, parent, app):
        self.channel = parent
        self.app = app
        self.mixer = self.channel.mixer
        Gtk.Dialog.__init__(self, 'Channel "%s" Properties' % self.channel.channel_name, app.window)
        self.set_default_size(365, -1)

        self.add_button(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL)
        self.ok_button = self.add_button(Gtk.STOCK_APPLY, Gtk.ResponseType.APPLY)
        self.set_default_response(Gtk.ResponseType.APPLY);

        self.create_ui()
        self.fill_ui()

        self.connect('response', self.on_response_cb)
        self.connect('delete-event', self.on_response_cb)

    def create_frame(self, label, child, padding=8):
        # need to pass an empty label, otherwise no label widget is created
        frame = Gtk.Frame(label='')
        frame.get_label_widget().set_markup('<b>%s</b>' % label)
        frame.set_border_width(3)
        frame.set_shadow_type(Gtk.ShadowType.NONE)

        alignment = Gtk.Alignment.new(0.5, 0, 1, 1)
        alignment.set_padding(padding, padding, padding, padding)
        frame.add(alignment)
        alignment.add(child)

        return frame

    def create_ui(self):
        vbox = self.get_content_area()

        self.properties_grid = grid = Gtk.Grid()
        vbox.pack_start(self.create_frame('Properties', grid), True, True, 0)
        grid.set_row_spacing(8)
        grid.set_column_spacing(8)
        grid.set_column_homogeneous(True)

        name_label = Gtk.Label.new_with_mnemonic('_Name')
        name_label.set_halign(Gtk.Align.START)
        grid.attach(name_label, 0, 0, 1, 1)
        self.entry_name = Gtk.Entry()
        self.entry_name.set_activates_default(True)
        self.entry_name.connect('changed', self.on_entry_name_changed)
        name_label.set_mnemonic_widget(self.entry_name)
        grid.attach(self.entry_name, 1, 0, 2, 1)

        grid.attach(Gtk.Label(label='Mode', halign=Gtk.Align.START), 0, 1, 1, 1)
        self.mono = Gtk.RadioButton.new_with_mnemonic(None, '_Mono')
        self.stereo = Gtk.RadioButton.new_with_mnemonic_from_widget(self.mono, '_Stereo')
        grid.attach(self.mono, 1, 1, 1, 1)
        grid.attach(self.stereo, 2, 1, 1, 1)

        grid = Gtk.Grid()
        vbox.pack_start(self.create_frame('MIDI Control Changes', grid), True, True, 0)
        grid.set_row_spacing(8)
        grid.set_column_spacing(8)
        grid.set_column_homogeneous(True)

        cc_tooltip = "{} MIDI Control Change number (0-127, set to -1 to assign next free CC #)"
        volume_label = Gtk.Label.new_with_mnemonic('_Volume')
        volume_label.set_halign(Gtk.Align.START)
        grid.attach(volume_label, 0, 0, 1, 1)
        self.entry_volume_cc = Gtk.SpinButton.new_with_range(-1, 127, 1)
        self.entry_volume_cc.set_tooltip_text(cc_tooltip.format("Volume"))
        volume_label.set_mnemonic_widget(self.entry_volume_cc)
        grid.attach(self.entry_volume_cc, 1, 0, 1, 1)
        self.button_sense_midi_volume = Gtk.Button('Learn')
        self.button_sense_midi_volume.connect('clicked',
                        self.on_sense_midi_volume_clicked)
        grid.attach(self.button_sense_midi_volume, 2, 0, 1, 1)

        balance_label = Gtk.Label.new_with_mnemonic('_Balance')
        balance_label.set_halign(Gtk.Align.START)
        grid.attach(balance_label, 0, 1, 1, 1)
        self.entry_balance_cc = Gtk.SpinButton.new_with_range(-1, 127, 1)
        self.entry_balance_cc.set_tooltip_text(cc_tooltip.format("Balance"))
        balance_label.set_mnemonic_widget(self.entry_balance_cc)
        grid.attach(self.entry_balance_cc, 1, 1, 1, 1)
        self.button_sense_midi_balance = Gtk.Button('Learn')
        self.button_sense_midi_balance.connect('clicked',
                        self.on_sense_midi_balance_clicked)
        grid.attach(self.button_sense_midi_balance, 2, 1, 1, 1)

        mute_label = Gtk.Label.new_with_mnemonic('M_ute')
        mute_label.set_halign(Gtk.Align.START)
        grid.attach(mute_label, 0, 2, 1, 1)
        self.entry_mute_cc = Gtk.SpinButton.new_with_range(-1, 127, 1)
        self.entry_mute_cc.set_tooltip_text(cc_tooltip.format("Mute"))
        mute_label.set_mnemonic_widget(self.entry_mute_cc)
        grid.attach(self.entry_mute_cc, 1, 2, 1, 1)
        self.button_sense_midi_mute = Gtk.Button('Learn')
        self.button_sense_midi_mute.connect('clicked',
                        self.on_sense_midi_mute_clicked)
        grid.attach(self.button_sense_midi_mute, 2, 2, 1, 1)

        if (isinstance(self, NewChannelDialog) or (self.channel and
            isinstance(self.channel, InputChannel))):
            solo_label = Gtk.Label.new_with_mnemonic('S_olo')
            solo_label.set_halign(Gtk.Align.START)
            grid.attach(solo_label, 0, 3, 1, 1)
            self.entry_solo_cc = Gtk.SpinButton.new_with_range(-1, 127, 1)
            self.entry_solo_cc.set_tooltip_text(cc_tooltip.format("Solo"))
            solo_label.set_mnemonic_widget(self.entry_solo_cc)
            grid.attach(self.entry_solo_cc, 1, 3, 1, 1)
            self.button_sense_midi_solo = Gtk.Button('Learn')
            self.button_sense_midi_solo.connect('clicked',
                            self.on_sense_midi_solo_clicked)
            grid.attach(self.button_sense_midi_solo, 2, 3, 1, 1)

        self.vbox.show_all()

    def fill_ui(self):
        self.entry_name.set_text(self.channel.channel_name)
        if self.channel.channel.is_stereo:
            self.stereo.set_active(True)
        else:
            self.mono.set_active(True)
        self.mono.set_sensitive(False)
        self.stereo.set_sensitive(False)
        self.entry_volume_cc.set_value(self.channel.channel.volume_midi_cc)
        self.entry_balance_cc.set_value(self.channel.channel.balance_midi_cc)
        self.entry_mute_cc.set_value(self.channel.channel.mute_midi_cc)
        if (self.channel and isinstance(self.channel, InputChannel)):
            self.entry_solo_cc.set_value(self.channel.channel.solo_midi_cc)

    def sense_popup_dialog(self, entry):
        window = Gtk.Window.new(Gtk.WindowType.TOPLEVEL)
        window.set_destroy_with_parent(True)
        window.set_transient_for(self)
        window.set_decorated(False)
        window.set_modal(True)
        window.set_position(Gtk.WindowPosition.CENTER_ON_PARENT)
        window.set_border_width(10)

        vbox = Gtk.VBox(10)
        window.add(vbox)
        window.timeout = 5
        vbox.pack_start(Gtk.Label(label='Please move the MIDI control you want to use for this function.'), True, True, 0)
        timeout_label = Gtk.Label(label='This window will close in 5 seconds')
        vbox.pack_start(timeout_label, True, True, 0)
        def close_sense_timeout(window, entry):
            window.timeout -= 1
            timeout_label.set_text('This window will close in %d seconds.' % window.timeout)
            if window.timeout == 0:
                window.destroy()
                entry.set_value(self.mixer.last_midi_channel)
                return False
            return True
        window.show_all()
        GObject.timeout_add_seconds(1, close_sense_timeout, window, entry)

    def on_sense_midi_volume_clicked(self, *args):
        self.mixer.last_midi_channel = int(self.entry_volume_cc.get_value())
        self.sense_popup_dialog(self.entry_volume_cc)

    def on_sense_midi_balance_clicked(self, *args):
        self.mixer.last_midi_channel = int(self.entry_balance_cc.get_value())
        self.sense_popup_dialog(self.entry_balance_cc)

    def on_sense_midi_mute_clicked(self, *args):
        self.mixer.last_midi_channel = int(self.entry_mute_cc.get_value())
        self.sense_popup_dialog(self.entry_mute_cc)

    def on_sense_midi_solo_clicked(self, *args):
        self.mixer.last_midi_channel = int(self.entry_solo_cc.get_value())
        self.sense_popup_dialog(self.entry_solo_cc)

    def on_response_cb(self, dlg, response_id, *args):
        self.channel.channel_properties_dialog = None
        name = self.entry_name.get_text()
        if response_id == Gtk.ResponseType.APPLY:
            if name != self.channel.channel_name:
                self.channel.channel_name = name
            for control in ('volume', 'balance', 'mute', 'solo'):
                widget = getattr(self, 'entry_{}_cc'.format(control), None)
                if widget is not None:
                    value = int(widget.get_value())
                    if value != -1:
                        setattr(self.channel.channel, '{}_midi_cc'.format(control), value)
        self.destroy()

    def on_entry_name_changed(self, entry):
        sensitive = False
        if len(entry.get_text()):
            if self.channel and self.channel.channel.name == entry.get_text():
                sensitive = True
            elif entry.get_text() not in [x.channel.name for x in self.app.channels] + \
                        [x.channel.name for x in self.app.output_channels] + ['MAIN']:
                sensitive = True
        self.ok_button.set_sensitive(sensitive)

GObject.signal_new("output-channel-order-changed", OutputChannel,
                GObject.SignalFlags.RUN_FIRST | GObject.SignalFlags.ACTION,
                None, [GObject.TYPE_STRING, GObject.TYPE_STRING])


class NewChannelDialog(ChannelPropertiesDialog):
    def create_ui(self):
        ChannelPropertiesDialog.create_ui(self)
        self.add_initial_value_radio()
        self.vbox.show_all()

    def add_initial_value_radio(self):
        grid = self.properties_grid
        grid.attach(Gtk.Label(label='Value', halign=Gtk.Align.START), 0, 2, 1, 1)
        self.minus_inf = Gtk.RadioButton.new_with_mnemonic(None, '-_Inf')
        self.zero_dB = Gtk.RadioButton.new_with_mnemonic_from_widget(self.minus_inf, '_0dB')
        grid.attach(self.minus_inf, 1, 2, 1, 1)
        grid.attach(self.zero_dB, 2, 2, 1, 1)


class NewInputChannelDialog(NewChannelDialog):
    def __init__(self, app):
        Gtk.Dialog.__init__(self, 'New Input Channel', app.window)
        self.set_default_size(365, -1)
        self.mixer = app.mixer
        self.app = app
        self.create_ui()

        self.add_button(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL)
        self.ok_button = self.add_button(Gtk.STOCK_ADD, Gtk.ResponseType.OK)
        self.ok_button.set_sensitive(False)
        self.set_default_response(Gtk.ResponseType.OK);

    def fill_ui(self, **values):
        self.entry_name.set_text(values.get('name', ''))
        # don't set MIDI CCs to previously used values, because they
        # would overwrite existing mappings, if accepted.
        self.entry_volume_cc.set_value(-1)
        self.entry_balance_cc.set_value(-1)
        self.entry_mute_cc.set_value(-1)
        self.entry_solo_cc.set_value(-1)
        self.stereo.set_active(values.get('stereo', True))
        self.minus_inf.set_active(values.get('value', False))
        self.entry_name.grab_focus()

    def get_result(self):
        return {
            'name': self.entry_name.get_text(),
            'stereo': self.stereo.get_active(),
            'volume_cc': int(self.entry_volume_cc.get_value()),
            'balance_cc': int(self.entry_balance_cc.get_value()),
            'mute_cc': int(self.entry_mute_cc.get_value()),
            'solo_cc': int(self.entry_solo_cc.get_value()),
            'value': self.minus_inf.get_active()
        }


class OutputChannelPropertiesDialog(ChannelPropertiesDialog):
    def create_ui(self):
        ChannelPropertiesDialog.create_ui(self)

        grid = self.properties_grid
        color_label = Gtk.Label.new_with_mnemonic('_Color')
        color_label.set_halign(Gtk.Align.START)
        grid.attach(color_label, 0, 3, 1, 1)
        self.color_chooser_button = Gtk.ColorButton()
        self.color_chooser_button.set_use_alpha(True)
        self.color_chooser_button.set_rgba(Gdk.RGBA(0, 0, 0, 0))
        color_label.set_mnemonic_widget(self.color_chooser_button)
        grid.attach(self.color_chooser_button, 1, 3, 2, 1)

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.vbox.pack_start(self.create_frame('Input Channels', vbox), True, True, 0)

        self.display_solo_buttons = Gtk.CheckButton.new_with_mnemonic('_Display solo buttons')
        vbox.pack_start(self.display_solo_buttons, True, True, 0)

        self.vbox.show_all()

    def fill_ui(self):
        ChannelPropertiesDialog.fill_ui(self)
        self.display_solo_buttons.set_active(self.channel.display_solo_buttons)
        self.color_chooser_button.set_rgba(self.channel.color)

    def on_response_cb(self, dlg, response_id, *args):
        ChannelPropertiesDialog.on_response_cb(self, dlg, response_id, *args)
        if response_id == Gtk.ResponseType.APPLY:
            self.channel.display_solo_buttons = self.display_solo_buttons.get_active()
            self.channel.set_color(self.color_chooser_button.get_rgba())
            for inputchannel in self.app.channels:
                inputchannel.update_control_group(self.channel)


class NewOutputChannelDialog(NewChannelDialog, OutputChannelPropertiesDialog):
    def __init__(self, app):
        Gtk.Dialog.__init__(self, 'New Output Channel', app.window)
        self.mixer = app.mixer
        self.app = app
        OutputChannelPropertiesDialog.create_ui(self)
        self.add_initial_value_radio()
        self.vbox.show_all()
        self.set_default_size(365, -1)

        # TODO: disable mode for output channels as mono output channels may
        # not be correctly handled yet.
        self.mono.set_sensitive(False)
        self.stereo.set_sensitive(False)

        self.add_button(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL)
        self.ok_button = self.add_button(Gtk.STOCK_ADD, Gtk.ResponseType.OK)
        self.ok_button.set_sensitive(False)
        self.set_default_response(Gtk.ResponseType.OK);

    def fill_ui(self, **values):
        self.entry_name.set_text(values.get('name', ''))
        # don't set MIDI CCs to previously used values, because they
        # would overwrite existing mappings, if accepted.
        self.entry_volume_cc.set_value(-1)
        self.entry_balance_cc.set_value(-1)
        self.entry_mute_cc.set_value(-1)
        self.stereo.set_active(values.get('stereo', True))
        self.minus_inf.set_active(values.get('value', False))
        self.color_chooser_button.set_rgba(values.get('color', Gdk.RGBA(0, 0, 0, 0)))
        self.display_solo_buttons.set_active(values.get('display_solo_buttons', False))
        self.entry_name.grab_focus()

    def get_result(self):
        return {
            'name': self.entry_name.get_text(),
            'stereo': self.stereo.get_active(),
            'volume_cc': int(self.entry_volume_cc.get_value()),
            'balance_cc': int(self.entry_balance_cc.get_value()),
            'mute_cc': int(self.entry_mute_cc.get_value()),
            'display_solo_buttons': self.display_solo_buttons.get_active(),
            'color': self.color_chooser_button.get_rgba(),
            'value': self.minus_inf.get_active()
        }


class ControlGroup(Gtk.Alignment):
    def __init__(self, output_channel, input_channel):
        GObject.GObject.__init__(self)
        self.set(0.5, 0.5, 1, 1)
        self.output_channel = output_channel
        self.input_channel = input_channel
        self.app = input_channel.app

        self.hbox = Gtk.HBox()
        self.vbox = Gtk.VBox()
        self.add(self.vbox)
        self.buttons_box = Gtk.Box(False, button_padding)

        set_background_color(self.vbox, output_channel.css_name, output_channel.color)

        self.vbox.pack_start(self.hbox, True, True, button_padding)
        css = b"""
.control_group {
    min-width: 0px;
    padding: 0px;
}

.control_group #label,
.control_group #mute,
.control_group #pre_fader,
.control_group #solo {
    font-size: smaller;
    padding: 0px .1em;
}
"""

        css_provider = Gtk.CssProvider()
        css_provider.load_from_data(css)
        context = Gtk.StyleContext()
        screen = Gdk.Screen.get_default()
        context.add_provider_for_screen(screen, css_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        hbox_context = self.hbox.get_style_context()
        hbox_context.add_class('control_group')

        name = output_channel.channel.name
        self.label = Gtk.Label(name)
        self.label.set_name("label")
        self.label.set_max_width_chars(self.input_channel.label_chars_narrow)
        self.label.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        if len(name) > self.input_channel.label_chars_narrow:
            self.label.set_tooltip_text(name)
        self.hbox.pack_start(self.label, False, False, button_padding)
        self.hbox.pack_end(self.buttons_box, False, False, button_padding)
        mute = Gtk.ToggleButton()
        mute.set_label("M")
        mute.set_name("mute")
        mute.set_tooltip_text("Mute output channel send")
        mute.connect("toggled", self.on_mute_toggled)
        self.mute = mute
        solo = Gtk.ToggleButton()
        solo.set_name("solo")
        solo.set_label("S")
        solo.set_tooltip_text("Solo output send")
        solo.connect("toggled", self.on_solo_toggled)
        self.solo = solo
        pre = Gtk.ToggleButton("P")
        pre.set_name("pre_fader")
        pre.set_tooltip_text("Pre (on) / Post (off) fader send")
        pre.connect("toggled", self.on_prefader_toggled)
        self.prefader = pre
        self.buttons_box.pack_start(pre, True, True, button_padding)
        self.buttons_box.pack_start(mute, True, True, button_padding)
        if self.output_channel.display_solo_buttons:
            self.buttons_box.pack_start(solo, True, True, button_padding)

    def update(self):
        if self.output_channel.display_solo_buttons:
            if not self.solo in self.buttons_box.get_children():
                self.buttons_box.pack_start(self.solo, True, True, button_padding)
                self.solo.show()
        else:
            if self.solo in self.buttons_box.get_children():
                self.buttons_box.remove(self.solo)

        name = self.output_channel.channel.name
        self.label.set_text(name)
        if len(name) > self.input_channel.label_chars_narrow:
            self.label.set_tooltip_text(name)

        set_background_color(self.vbox, self.output_channel.css_name, self.output_channel.color)

    def on_mute_toggled(self, button):
        self.output_channel.channel.set_muted(self.input_channel.channel, button.get_active())
        self.app.update_monitor(self)

    def on_solo_toggled(self, button):
        self.output_channel.channel.set_solo(self.input_channel.channel, button.get_active())
        self.app.update_monitor(self)

    def on_prefader_toggled(self, button):
        self.output_channel.channel.set_in_prefader(self.input_channel.channel, button.get_active())

    def narrow(self):
        self.hbox.remove(self.label)
        self.hbox.set_child_packing(self.buttons_box, True, True, button_padding, Gtk.PackType.END)

    def widen(self):
        self.hbox.pack_start(self.label, False, False, button_padding)
        self.hbox.set_child_packing(self.buttons_box, False, False, button_padding, Gtk.PackType.END)
