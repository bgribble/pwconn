"""
pwconn -- text UI for manipulating Pipewire connections

Copyright (c) Bill Gribble <grib@billgribble.com>
"""

import argparse
import logging
import subprocess
import shutil
import sys

from textual.app import App
from textual.widgets import Header, Footer, Static, Label, ListView, ListItem
from textual.containers import Horizontal, Container
from textual.logging import TextualHandler

from .alsa_info import get_alsa_info
from .pw_info import get_pw_info, conn_pairs

logging.basicConfig(
    level="NOTSET",
    handlers=[TextualHandler()],
)

def import_to_tree(tree, device_info):
    # find devices first
    devices = {
        id_key: obj for id_key, obj in device_info.items()
        if (
            "device" in obj.get("object.pwtype", "")
            and (
                len(obj.get("node.portgroups", []))
                or len(obj.get("node.ports", []))
            )
        )
    }
    for device_id, device in devices.items():
        port_ids = device.get("node.ports", [])

        group_ids = device.get("node.portgroups", [])
        for group_id in group_ids:
            if group_id in device_info:
                group = device_info.get(group_id)
                port_ids.extend(group.get("node.ports", []))

        device["node.ports"] = [
            device_info.get(port_id)
            for port_id in port_ids if port_id in device_info
        ]
        tree[device_id] = device


class KeysFooter(Static):
    """
    Helper class to allow async updating of the hotkey list
    """
    def __init__(self, init_string):
        super().__init__(
            init_string,
            classes="keys_footer"
        )


class PWConnApp(App):
    CSS_PATH = "pwconn.tcss"
    AUTO_FOCUS = ".main_list"

    BINDINGS = [
        ("a", "filter_audio", "Audio"),
        ("m", "filter_midi", "ALSA MIDI"),
        ("j", "filter_jack_midi", "JACK MIDI"),
        ("v", "filter_video", "Video"),
        ("r", "refresh", "Refresh"),
        ("q", "quit", "Quit")
    ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.media_type = "audio"

        self.expanded_devices = set()
        self.expanded_ports = set()

        self.selected_ports = set()

        self.list_items = []
        self.list_selection = 0

        self.pw_info = None
        self.alsa_info = None
        self.device_info = {}

        self.update_info()

    def update_info(self):
        import json
        self.pw_info = get_pw_info()
        self.alsa_info = get_alsa_info()

        tree = {}
        import_to_tree(tree, self.pw_info)
        import_to_tree(tree, self.alsa_info)
        self.device_info = tree

    def compose(self):
        yield Header()
        yield self.render_media_header()

        content = []
        if self.media_type == "audio" and self.pw_info:
            content.append(self.render_audio())

        elif self.media_type == "jack_midi" and self.pw_info:
            content.append(self.render_jack_midi())

        elif self.media_type == "alsa_midi" and self.alsa_info:
            content.append(self.render_alsa_midi())

        elif self.media_type == "video" and self.pw_info:
            content.append(self.render_video())

        content.append(self.render_keys_footer())
        yield Container(
            *content,
            classes="content_container"
        )
        yield Footer()

    def on_mount(self):
        self.title = "pwconn"
        self.sub_title = "Manage Pipewire connections"

    async def on_key(self, event):
        need_refresh = False
        if hasattr(event, 'key'):
            sel = self.list_items[self.list_selection][0]

            if "object.pwtype" in sel:
                if event.key == "space":
                    if sel.get("object.pwtype") == "port":
                        key = sel.get('object.id')
                        if key in self.selected_ports:
                            self.selected_ports.remove(key)
                        else:
                            self.selected_ports.add(key)
                        self.update_port_label(sel)
                elif event.key == "left_square_bracket":
                    if sel.get("object.pwtype").startswith("device"):
                        self.expanded_devices.add(sel.get("object.id"))
                        need_refresh = True
                    elif sel.get("object.pwtype").startswith("port"):
                        self.expanded_ports.add(sel.get("object.id"))
                        need_refresh = True
                elif event.key == "right_square_bracket":
                    if sel.get("object.pwtype").startswith("device"):
                        if sel.get("object.id") in self.expanded_devices:
                            self.expanded_devices.remove(sel.get("object.id"))
                        need_refresh = True
                    elif sel.get("object.pwtype").startswith("port"):
                        if sel.get("object.id") in self.expanded_ports:
                            self.expanded_ports.remove(sel.get("object.id"))
                        need_refresh = True
            if event.key == "left_curly_bracket":
                for obj_id, obj in self.pw_info.items():
                    if obj.get("object.pwtype", "").startswith("device"):
                        self.expanded_devices.add(obj_id)
                    elif obj.get("object.pwtype", "") == "port":
                        self.expanded_ports.add(obj_id)
                for obj_id, obj in self.alsa_info.items():
                    if obj.get("object.pwtype", "").startswith("device"):
                        self.expanded_devices.add(obj_id)
                    elif obj.get("object.pwtype", "") == "port":
                        self.expanded_ports.add(obj_id)
                need_refresh = True
            elif event.key == "right_curly_bracket":
                self.expanded_devices = set()
                self.expanded_ports = set()
                self.list_selection = 0
                need_refresh = True
            elif event.key == "up":
                self.list_selection = max(0, self.list_selection - 1)
                self.update_keys_footer()
            elif event.key == "down":
                self.list_selection = min(len(self.list_items) - 1, self.list_selection + 1)
                self.update_keys_footer()
            elif event.key == "c":
                self.connect_marked()
                need_refresh = True
            elif event.key == "d":
                self.disconnect_selected()
                need_refresh = True

        if need_refresh:
            await self.recompose()
            self.query_one(ListView).focus()

    async def on_list_view_highlighted(self, highlight):
        for i, item in enumerate(self.list_items):
            if item[1] == highlight.item:
                self.list_selection = i
                self.update_keys_footer()
                item[1].add_class("highlighted_item")
            else:
                item[1].remove_class("highlighted_item")

    def disconnect_selected(self):
        link = self.list_items[self.list_selection][0]
        if not link.get("object.pwtype") == "link":
            return
        if self.media_type == "alsa_midi":
            outport = link.get("link.output.port")
            inport = link.get("link.input.port")

            subprocess.run(
                [
                    "aconnect",  "-d",
                    outport.replace("out:", ""),
                    inport.replace("in:", ""),
                ],
                capture_output=True, check=True
            )
        else:
            subprocess.run(
                ["pw-link", "-d", link.get("object.id")],
                capture_output=True, check=True
            )

        self.update_info()

    def connect_marked(self):
        in_ports = []
        out_ports = []

        if self.media_type == "alsa_midi":
            all_info = self.alsa_info
        else:
            all_info = self.pw_info

        for port_id in self.selected_ports:
            port = all_info.get(port_id)
            if port.get("port.direction") == "in":
                in_ports.append(port)
            elif port.get("port.direction") == "out":
                out_ports.append(port)

        if not len(in_ports) or not len(out_ports):
            return

        in_ports.sort(key=lambda p: p.get("port.alias") or p.get("port.name"))
        out_ports.sort(key=lambda p: p.get("port.alias") or p.get("port.name"))

        self.expanded_ports |= set([p.get('object.id') for p in (in_ports + out_ports)])

        pairs = conn_pairs(len(out_ports), len(in_ports))

        for outport_ind, inport_ind in pairs:
            outport = out_ports[outport_ind]
            inport = in_ports[inport_ind]
            if self.media_type == "alsa_midi":
                subprocess.run(
                    [
                        "aconnect",
                        outport.get("object.id").replace("out:", ""),
                        inport.get("object.id").replace("in:", ""),
                    ],
                    capture_output=True, check=True
                )

            else:
                subprocess.run(
                    ["pw-link", outport.get("object.id"), inport.get("object.id")],
                    capture_output=True, check=True
                )

        self.update_info()

        self.selected_ports = set()

    def render_media_header(self):
        labels = dict(
            audio="Audio",
            jack_midi="JACK MIDI",
            alsa_midi="ALSA MIDI",
            video="Video"
        )
        return Static(f"{labels.get(self.media_type)} devices", classes="title")

    def keys_footer_content(self):
        keys = [
            ("open", r"\[", "Open"),
            ("close", "]", "Close"),
            ("openall", r"{", "Open all"),
            ("closeall", r"}", "Close all"),
            ("mark", "SPC", "Toggle mark"),
            ("connect", "c", "Connect marked"),
            ("disconnect", "d", "Disconnect"),
        ]

        active_keys = []
        actions = []

        if self.list_selection is not None:
            current_item = self.list_items[self.list_selection]

            highlight_type = current_item[0].get("object.pwtype", "").split(' ')[0]
            if highlight_type in (
                "device_audio", "device_alsa_midi", "device_jack_midi", "device_video"
            ):
                actions = ["open", "close", "openall", "closeall"]
            elif highlight_type == "port":
                actions = ["open", "close", "openall", "closeall", "mark"]
            elif highlight_type == "link":
                actions = ["disconnect"]

        if len(self.selected_ports) > 1:
            actions.append("connect")

        active_keys = [
            k for k in keys
            if k[0] in actions
        ]

        return '  '.join(
            f"[bold][$accent]{k}[/][/] {cmd}"
            for tag, k, cmd in active_keys
        )


    def update_keys_footer(self):
        self.query_one(KeysFooter).update(self.keys_footer_content())

    def update_port_label(self, port):
        self.query_one(
            f"#port_label_{port.get('object.id')}".replace(':', "_")
        ).update(self.port_label_content(port))

    def render_keys_footer(self):
        return KeysFooter(self.keys_footer_content())

    def render_alsa_midi(self):
        devices = [
            obj for id, obj in self.device_info.items()
            if "device_alsa_midi" in obj.get("object.pwtype")
        ]
        return self.render_device_list(devices, "", self.alsa_info)

    def render_jack_midi(self):
        devices = [
            obj for id, obj in self.device_info.items()
            if "device_jack_midi" in obj.get("object.pwtype", "")
        ]
        return self.render_device_list(devices, "midi", self.pw_info)

    def render_video(self):
        devices = [
            obj for id, obj in self.device_info.items()
            if "device_video" in obj.get("object.pwtype", "")
        ]
        return self.render_device_list(devices, "video", self.pw_info)


    def render_audio(self):
        devices = [
            obj for id, obj in self.device_info.items()
            if "device_audio" in obj.get("object.pwtype", "")
        ]
        return self.render_device_list(devices, "audio", self.pw_info)


    def render_device_list(self, devices, device_type, all_items):
        device_items = [(
            {},
            ListItem(
                Horizontal(
                    Label(
                        "[bold]Name[/]",
                        classes="col_2"
                    ),
                    Label(
                        "[bold]Ports[/]",
                        classes="col_2"
                    ),
                    Label(
                        "[bold]Connections[/]",
                        classes="col_2"
                    )
                ),
                classes="device_line"
            )
        )]
        for i in sorted(devices, key=lambda i: int(i.get("object.id"), 0)):
            device_items.extend(
                self.render_device_item(i, device_type, all_items)
            )
        self.list_items = device_items
        self.list_selection = max(0, min(self.list_selection, len(self.list_items)-1))

        return ListView(
            *[d[1] for d in device_items],
            initial_index=self.list_selection,
            classes="main_list"
        )

    def port_label_content(self, port):
        tag = ''
        obj_id = port.get('object.id')
        if obj_id in self.selected_ports:
            tag = '[$warning]*[/] '
        return f"{port.get('port.id', '')}: {tag}{port.get('port.name', '')}"

    def render_port(self, port, all_items):
        obj_id = port.get("object.id", "")
        links_in = port.get("port.links_in", [])
        links_out = port.get("port.links_out", [])
        link_count = len(links_in) + len(links_out)

        items = [(
            port,
            ListItem(
                Horizontal(
                    Label("", classes="col_1"),
                    Label(
                        self.port_label_content(port),
                        classes="col_3",
                        id=f"port_label_{obj_id}".replace(':', "_")
                    ),
                    Label(
                        f"[{link_count}]",
                        classes="col_2"
                    )
                )
            )
        )]

        if obj_id in self.expanded_ports:
            for link_id in sorted(port.get("port.links_in", [])):
                link = all_items.get(link_id)

                other_node = all_items.get(link.get("link.input.node"))
                other_port = all_items.get(link.get("link.input.port"))

                if not other_port:
                    continue

                if "device.id" in other_node:
                    device_node = all_items.get(other_node.get("device.id"))
                else:
                    device_node = other_node

                other_node_name = (
                    device_node.get("device.nick")
                    or device_node.get("device.name")
                    or device_node.get("node.name")
                )

                arrow = "-->"
                items.append((
                    link,
                    ListItem(
                        Horizontal(
                            Label("", classes="col_1"),
                            Label(
                                f" {arrow} {other_node_name}:{other_port.get('port.name')}",
                                classes="col_5"
                            )
                        )
                    )
                ))

            for link_id in sorted(port.get("port.links_out", [])):
                link = all_items.get(link_id)
                other_node = all_items.get(link.get("link.output.node"))
                other_port = all_items.get(link.get("link.output.port"))
                if not other_port:
                    continue

                if "device.id" in other_node:
                    device_node = all_items.get(other_node.get("device.id"))
                else:
                    device_node = other_node

                other_node_name = (
                    device_node.get("device.nick")
                    or device_node.get("device.name")
                    or device_node.get("node.name")
                )

                arrow = "<--"
                items.append((
                    link,
                    ListItem(
                        Horizontal(
                            Label("", classes="col_1"),
                            Label(
                                f" {arrow} {other_node_name}:{other_port.get('port.name')}",
                                classes="col_5"
                            )
                        )
                    )
                ))

        return items

    def render_device_item(self, item, device_type, all_items):
        obj_id = item.get("object.id")
        ports = item.get("node.ports")

        conn_in = sum(
            len(p.get("port.links_out", []))
            for p in ports
        )
        conn_out = sum(
            len(p.get("port.links_in", []))
            for p in ports
        )

        in_ports = [
            p for p in ports if "in" in p.get("port.direction")
        ]
        in_desc = f"{len(in_ports)} in" if len(in_ports) else ""
        out_ports = [
            p for p in ports
            if "out" in p.get("port.direction") and not p.get("port.monitor") == "true"
        ]
        out_desc = f"{len(out_ports)} out" if len(out_ports) else ""
        mon_ports = [
            p for p in ports
            if "out" in p.get("port.direction") and p.get("port.monitor") == "true"
        ]
        mon_desc = f"{len(mon_ports)} mon" if len(mon_ports) else ""
        port_desc = f"({'/'.join([f for f in (in_desc, out_desc, mon_desc) if f])})"
        conn_desc = f'[{conn_in} in/{conn_out} out]'

        items = [(
            item,
            ListItem(
                Horizontal(
                    Label(
                        item.get("device.nick") or item.get("device.name") or item.get("node.name"),
                        classes="col_2"
                    ),
                    Label(
                        port_desc,
                        classes="col_2"
                    ),
                    Label(
                        conn_desc,
                        classes="col_2"
                    )
                ),
                classes="device_line"
            )
        )]

        if obj_id in self.expanded_devices:
            if in_ports:
                items.append((
                    {},
                    ListItem(
                        Horizontal(
                            Label("", classes="col_0_5"),
                            Label("input", classes="col_5_5")
                        )
                    )
                ))
                for i in sorted(in_ports, key=lambda p: int(p.get("port.id"))):
                    items.extend(self.render_port(i, all_items))

            if out_ports:
                items.append((
                    {},
                    ListItem(
                        Horizontal(
                            Label("", classes="col_0_5"),
                            Label("output", classes="col_5_5")
                        )
                    )
                ))
                for i in sorted(out_ports, key=lambda p: int(p.get("port.id"))):
                    items.extend(self.render_port(i, all_items))

            if mon_ports:
                items.append((
                    {},
                    ListItem(
                        Horizontal(
                            Label("", classes="col_0_5"),
                            Label("monitor", classes="col_5_5")
                        )
                    )
                ))
                for i in sorted(mon_ports, key=lambda p: int(p.get("port.id"))):
                    items.extend(self.render_port(i, all_items))

        return items

    async def action_filter_audio(self):
        self.media_type = "audio"
        self.list_selection = 0
        self.selected_ports = set()
        await self.redraw()

    async def action_filter_jack_midi(self):
        self.media_type = "jack_midi"
        self.list_selection = 0
        self.selected_ports = set()
        await self.redraw()

    async def action_filter_midi(self):
        self.media_type = "alsa_midi"
        self.list_selection = 0
        self.selected_ports = set()
        await self.redraw()

    async def action_filter_video(self):
        self.media_type = "video"
        self.list_selection = 0
        self.selected_ports = set()
        await self.redraw()

    async def action_refresh(self):
        self.update_info()
        await self.redraw()

    async def redraw(self):
        await self.recompose()
        self.query_one(ListView).focus()

    def action_quit(self):
        self.exit()

description = "pwconn - manage Pipewire connections via text UI"
footer = """
To report bugs or download source:

    http://github.com/bgribble/pwconn

Copyright (c) Bill Gribble <grib@billgribble.com>

pwconn is free software, and you are welcome to redistribute it
under certain conditions.  See the file COPYING for details.
"""

def main():
    parser = argparse.ArgumentParser(
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=description, epilog=footer
    )

    parser.add_argument(
        "-a", "--audio", action="store_true",
        help="Initially view audio devices"
    )
    parser.add_argument(
        "-j", "--jack-midi", action="store_true",
        help="Initially view JACK MIDI devices"
    )
    parser.add_argument(
        "-m", "--alsa-midi", action="store_true",
        help="Initially view ALSA MIDI devices"
    )
    parser.add_argument(
        "-v", "--video", action="store_true",
        help="Initially view video devices"
    )

    # check for pw-link, pw-cli, and aconnect
    for helper in ("pw-link", "pw-cli", "aconnect"):
        path = shutil.which(helper)
        if not path:
            print(f"pwconn: unable to find helper '{helper}' in $PATH, is it installed?")
            sys.exit(-1)

    args = vars(parser.parse_args())
    app = PWConnApp()

    if args.get("alsa_midi"):
        app.media_type = "alsa_midi"
    if args.get("jack_midi"):
        app.media_type = "jack_midi"
    if args.get("video"):
        app.media_type = "video"
    if args.get("audio"):
        app.media_type = "audio"

    app.run()

if __name__ == "__main__":
    main()
