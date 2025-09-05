import json
import logging
import subprocess
import re

from textual.app import App
from textual.widgets import Header, Footer, Static, Label, ListView, ListItem
from textual.containers import Horizontal, Vertical, Container
from textual.logging import TextualHandler

logging.basicConfig(
    level="NOTSET",
    handlers=[TextualHandler()],
)

ALSA_CLIENT = r"^client ([0-9]+): '([^']+)'"
ALSA_PORT = r"^([0-9]+) '([^']+)'"
ALSA_CONNECTION = r"(Connected|Connecting) (To:|From:) (.*)$"

INPUT = "-o"
OUTPUT = "-i"


class KeysFooter(Static):
    def __init__(self, init_string):
        super().__init__(
            init_string,
            classes="keys_footer"
        )



def get_alsa_portdir(direction):
    """
    aconnect -l has most of the info we want, but it doesn't
    distinguish input from output ports. this finds the jeys
    of the input ports
    """
    objlist = subprocess.run(
        ["aconnect", direction],
        capture_output=True
    )
    ports = []

    current_id = None

    stdout_str = objlist.stdout.decode("UTF-8")
    for line in stdout_str.split("\n"):
        stripline = line.strip()

        # end of input.
        if not stripline:
            break

        # client line
        if not line.startswith('\t') and not line.startswith('    '):
            match = re.search(ALSA_CLIENT, stripline)
            if match:
                current_id = match.group(1)

        # port line
        elif line.startswith("    "):
            key, _ = stripline.split(" ", maxsplit=1)
            ports.append(f"{current_id}:{key}")
    return ports


def get_alsa_info():
    in_ports = get_alsa_portdir(INPUT)
    out_ports = get_alsa_portdir(OUTPUT)

    objlist = subprocess.run(
        ["aconnect", "-l"],
        capture_output=True
    )

    info = {}
    current_obj = None
    current_id = None
    current_port = {}
    connections = None
    ports = {}

    stdout_str = objlist.stdout.decode("UTF-8")

    for line in stdout_str.split("\n"):
        stripline = line.strip()

        # end of input. save final client block
        if not stripline:
            if current_id:
                info[current_id] = current_obj
            break

        # client line
        if not line.startswith('\t') and not line.startswith('    '):
            match = re.search(ALSA_CLIENT, stripline)
            if match:
                # save previous client block
                if current_id and current_obj:
                    info[current_id] = current_obj

                # start a new one
                ports = {}
                current_obj = {
                    "object.id": match.group(1),
                    "object.client_id": match.group(1),
                    "object.pwtype": "device_alsa_midi",
                    "device.name": match.group(2),
                    "ports": ports
                }
                current_id = match.group(1)
                connections = []

        # port line
        elif line.startswith("    "):
            key, val = stripline.split(" ", maxsplit=1)
            val = re.sub("'", '"', val)
            obj_key = f"{current_id}:{key}"

            connections = {"to": [], "from": []}

            if obj_key in in_ports:
                obj_id = f"{current_id}:in:{key}"
                port_obj = {
                    "object.pwtype": "port",
                    "object.id": obj_id,
                    "port.id": f"in:{key}",
                    "node.id": current_id,
                    "port.direction": "in",
                    "port.name": json.loads(val).strip(),
                    "connections": connections
                }
                node_ports = current_obj.setdefault("node.ports", [])
                node_ports.append(obj_id)
                ports[key] = port_obj

                info[obj_id] = port_obj
                current_port = port_obj

            if obj_key in out_ports:
                obj_id = f"{current_id}:out:{key}"
                port_obj = {
                    "object.pwtype": "port",
                    "object.id": obj_id,
                    "port.id": f"out:{key}",
                    "node.id": current_id,
                    "port.direction": "out",
                    "port.name": json.loads(val).strip(),
                    "connections": connections
                }
                node_ports = current_obj.setdefault("node.ports", [])
                node_ports.append(obj_id)
                ports[key] = port_obj

                info[obj_id] = port_obj
                current_port = port_obj

        # connection line
        else:
            match = re.search(ALSA_CONNECTION, stripline)
            if match:
                direction = match.group(2)
                conninfo = match.group(3).split(", ")
                if direction == "From:":
                    current_port['connections']['from'].extend(conninfo)
                else:
                    current_port['connections']['to'].extend(conninfo)

                for link in conninfo:
                    link_obj = {
                        'object.id': obj_id,
                        'object.pwtype': "link",
                    }
                    if direction == "From:":
                        node_id, port_id = link.split(":")
                        link_obj['link.output.node'] = node_id
                        link_obj['link.output.port'] = port_id
                        link_obj['link.input.node'] = current_port['node.id']
                        link_obj['link.input.port'] = current_port['port.id']
                    else:
                        node_id, port_id = link.split(":")
                        link_obj['link.input.node'] = node_id
                        link_obj['link.input.port'] = port_id
                        link_obj['link.output.node'] = current_port['node.id']
                        link_obj['link.output.port'] = current_port['port.id']

                    links = current_obj.setdefault("port.links", [])
                    links.append(obj_id)
    return info


def annotate_pw_info(info):
    for obj_id, obj in info.items():
        if (
            ("media.type" in obj and obj["media.type"] == "Audio")
            or ("media.class" in obj and obj["media.class"] == "Audio/Device")
        ):
            obj["object.pwtype"] = "device_audio"
        elif (
           "media.class" in obj and obj["media.class"].startswith("Midi")
        ):
            obj["object.pwtype"] = "device_jack_midi"
        elif (
           "media.class" in obj and obj["media.class"] == "Video/Device"
        ):
            obj["object.pwtype"] = "device_video"
            obj["device.nick"] = obj["device.description"]
        elif "port.id" in obj:
            obj["object.pwtype"] = "port"
            node = info.get(obj['node.id'])
            ports = node.setdefault('node.ports', [])
            ports.append(obj_id)
        elif "media.class" in obj and obj["media.class"] in (
            "Audio/Source", "Audio/Sink", "Video/Source", "Video/Sink"
        ):
            obj["object.pwtype"] = "portgroup"
            device = info.get(obj['device.id'])
            groups = device.setdefault("node.portgroups", [])
            groups.append(obj_id)
        elif "link.output.port" in obj:
            obj["object.pwtype"] = "link"
            outport = info.get(obj["link.output.port"])
            links = outport.setdefault("port.links_in", [])
            links.append(obj_id)
            inport = info.get(obj["link.input.port"])
            links = inport.setdefault("port.links_out", [])
            links.append(obj_id)


def get_pw_info():
    objlist = subprocess.run(
        ["pw-cli", "ls"],
        capture_output=True
    )

    info = {}
    current_obj = {}
    current_id = None

    stdout_str = objlist.stdout.decode("UTF-8")

    for line in stdout_str.split("\n"):
        stripline = line.strip()
        if not stripline:
            if current_id:
                info[current_id] = current_obj
            break
        if line.startswith('\t') and not line.startswith('\t\t'):
            if current_id:
                info[current_id] = current_obj
            current_id = stripline.split(", ")[0].split(" ")[1]
            current_type = stripline.split(", ", maxsplit=1)[1].split(" ", maxsplit=1)[1]
            current_obj = {
                "object.id": current_id,
                "object.type": current_type
            }
        else:
            key, val = stripline.split(" = ")
            current_obj[key] = json.loads(val)

    annotate_pw_info(info)
    return info


def conn_pairs(num_out, num_in):
    count = max(num_out, num_in)
    div_out = num_out / count
    div_in = num_in / count

    connections = []
    for conn in range(count):
        connections.append((int(div_out * conn), int(div_in * conn)))
    return connections


class PWConnApp(App):
    CSS_PATH = "main.tcss"
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
        self.pw_info = get_pw_info()
        self.alsa_info = get_alsa_info()

        self.expanded_devices = set()
        self.expanded_ports = set()

        self.selected_ports = set()

        self.list_items = []
        self.list_selection = 0

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

        elif self.media_type == "video" and self.alsa_info:
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
            if event.key == "space":
                if sel.get("object.pwtype") == "port":
                    key = sel.get('object.id')
                    if key in self.selected_ports:
                        self.selected_ports.remove(key)
                    else:
                        self.selected_ports.add(key)
                    need_refresh = True
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
            elif event.key == "left_curly_bracket":
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

    def on_list_view_highlighted(self, highlight):
        for i, item in enumerate(self.list_items):
            if item[1] == highlight.item:
                self.list_selection = i
                self.update_keys_footer()
                break

    def disconnect_selected(self):
        link = self.list_items[self.list_selection][0]
        if not link.get("object.pwtype") == "link":
            return

        output = subprocess.run(
            ["pw-link", "-d", link.get("object.id")],
            capture_output=True
        )

        self.pw_info = get_pw_info()
        self.alsa_info = get_alsa_info()

    def connect_marked(self):
        in_ports = []
        out_ports = []

        logging.debug(f"[connect] selected ports = {self.selected_ports}")
        for port_id in self.selected_ports:
            port = self.pw_info.get(port_id)
            if port.get("port.direction") == "in":
                in_ports.append(port)
            elif port.get("port.direction") == "out":
                out_ports.append(port)

        in_ports.sort(key=lambda p: p.get("port.alias") or p.get("port.name"))
        out_ports.sort(key=lambda p: p.get("port.alias") or p.get("port.name"))

        pairs = conn_pairs(len(out_ports), len(in_ports))

        for outport_ind, inport_ind in pairs:
            outport = out_ports[outport_ind]
            inport = in_ports[inport_ind]
            output = subprocess.run(
                ["pw-link", outport.get("object.id"), inport.get("object.id")],
                capture_output=True
            )

        self.pw_info = get_pw_info()
        self.alsa_info = get_alsa_info()

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

            highlight_type = current_item[0].get("object.pwtype")
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
            f"[bold][#ffa500]{k}[/][/] {cmd}"
            for tag, k, cmd in active_keys
        )

    def update_keys_footer(self):
        self.query_one(KeysFooter).update(self.keys_footer_content())

    def render_keys_footer(self):
        return KeysFooter(self.keys_footer_content())

    def render_alsa_midi(self):
        devices = [
            obj for id, obj in self.alsa_info.items()
            if (
                obj.get("object.pwtype") == "device_alsa_midi"
                and (len(obj.get("node.portgroups", []))
                     or len(obj.get("node.ports", [])))
            )
        ]
        return self.render_device_list(devices, self.alsa_info)

    def render_jack_midi(self):
        devices = [
            obj for id, obj in self.pw_info.items()
            if (
                obj.get("object.pwtype") == "device_jack_midi"
                and (len(obj.get("node.portgroups", []))
                     or len(obj.get("node.ports", [])))
            )
        ]
        return self.render_device_list(devices, self.pw_info)

    def render_video(self):
        devices = [
            obj for id, obj in self.pw_info.items()
                if (
                obj.get("object.pwtype") == "device_video"
                and (len(obj.get("node.portgroups", []))
                     or len(obj.get("node.ports", [])))
            )
        ]
        return self.render_device_list(devices, self.pw_info)

    def render_audio(self):
        devices = [
            obj for id, obj in self.pw_info.items()
            if (
                obj.get("object.pwtype") == "device_audio"
                and (len(obj.get("node.portgroups", []))
                     or len(obj.get("node.ports", [])))
            )
        ]
        return self.render_device_list(devices, self.pw_info)

    def render_device_list(self, devices, all_items):
        device_items = []
        for i in sorted(devices, key=lambda i: int(i.get("object.id"), 0)):
            device_items.extend(
                self.render_device_item(i, all_items)
            )
        self.list_items = device_items
        return ListView(
            *[d[1] for d in device_items],
            initial_index=self.list_selection,
            classes="main_list"
        )

    def render_port(self, port, all_items):
        obj_id = port.get("object.id", "")
        tag = ''
        if obj_id in self.selected_ports:
            tag = '[#00ff00]*[/] '
        items = [(
            port,
            ListItem(
                Horizontal(
                    Label(obj_id, classes="col_1"),
                    Label("", classes="col_1"),
                    Label(
                        f"{port.get('port.id', '')}: {tag}{port.get('port.name', '')}",
                        classes="col_5"
                    )
                )
            )
        )]

        if obj_id in self.expanded_ports:
            for link_id in sorted(port.get("port.links_in", [])):
                link = all_items.get(link_id)
                other_node = all_items.get(link.get("link.input.node"))
                other_port = all_items.get(link.get("link.input.port"))

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
                            Label(port.get("object.id", ""), classes="col_1"),
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
                            Label(port.get("object.id", ""), classes="col_1"),
                            Label("", classes="col_1"),
                            Label(
                                f" {arrow} {other_node_name}:{other_port.get('port.name')}",
                                classes="col_5"
                            )
                        )
                    )
                ))

        return items

    def render_device_item(self, item, all_items):
        obj_id = item.get("object.id")
        items = [(
            item,
            ListItem(
                Horizontal(
                    Label(item.get("object.id", ""), classes="col_1"),
                    Label(
                        item.get("device.nick") or item.get("device.name") or item.get("node.name"),
                        classes="col_6"
                    )
                )
            )
        )]

        node_ids = [obj_id] + [
            oid
            for oid, obj in all_items.items()
            if obj.get("device.id") == obj_id and obj.get("media.class")
        ]

        if obj_id in self.expanded_devices:
            ports = [
                obj for oid, obj in all_items.items()
                if obj.get("node.id") in node_ids
            ]

            in_ports = [p for p in ports if "in" in p.get("port.direction")]
            out_ports = [
                p for p in ports
                if "out" in p.get("port.direction") and not p.get("port.monitor") == "true"
            ]
            mon_ports = [
                p for p in ports
                if "out" in p.get("port.direction") and p.get("port.monitor") == "true"
            ]

            if in_ports:
                items.append((
                    {},
                    ListItem(
                        Horizontal(
                            Label("", classes="col_1_5"),
                            Label("input", classes="col_5_5")
                        )
                    )
                ))
                for i in sorted(in_ports, key=lambda p: p.get("port.id")):
                    items.extend(self.render_port(i, all_items))

            if out_ports:
                items.append((
                    {},
                    ListItem(
                        Horizontal(
                            Label("", classes="col_1_5"),
                            Label("output", classes="col_5_5")
                        )
                    )
                ))
                for i in sorted(out_ports, key=lambda p: p.get("port.id")):
                    items.extend(self.render_port(i, all_items))

            if mon_ports:
                items.append((
                    {},
                    ListItem(
                        Horizontal(
                            Label("", classes="col_1_5"),
                            Label("monitor", classes="col_5_5")
                        )
                    )
                ))
                for i in sorted(mon_ports, key=lambda p: p.get("port.id")):
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
        self.pw_info = get_pw_info()
        self.alsa_info = get_alsa_info()
        await self.redraw()

    async def redraw(self):
        await self.recompose()
        self.query_one(ListView).focus()

    def action_quit(self):
        self.exit()


if __name__ == "__main__":
    app = PWConnApp()
    app.run()
