import json
import logging
import subprocess
import re

from textual.app import App
from textual.widgets import Header, Footer, Static, Label, ListView, ListItem
from textual.containers import Horizontal
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

        self.selected_devices = set()
        self.selected_ports = set()
        self.selected_conn = set()

        self.list_items = []
        self.list_selection = 0

    def compose(self):
        yield Header()

        if self.media_type == "audio" and self.pw_info:
            yield self.render_audio()

        elif self.media_type == "jack_midi" and self.pw_info:
            yield self.render_jack_midi()

        elif self.media_type == "alsa_midi" and self.alsa_info:
            yield self.render_alsa_midi()

        elif self.media_type == "video" and self.alsa_info:
            yield self.render_video()

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
                    key = f"{sel.get('object.id')}:{sel.get('port.direction')}:{sel.get('port.id')}"
                    if key in self.selected_ports:
                        self.selected_ports.remove(key)
                    else:
                        self.selected_ports.add(key)
                    need_refresh = True
            elif event.key == "left_square_bracket":
                if sel.get("object.pwtype").startswith("device"):
                    self.expanded_devices.add(sel.get("object.id"))
                    need_refresh = True
            elif event.key == "right_square_bracket":
                if sel.get("object.pwtype").startswith("device"):
                    self.expanded_devices.remove(sel.get("object.id"))
                    need_refresh = True

        if need_refresh:
            await self.recompose()
            self.query_one(ListView).focus()

    def on_list_view_highlighted(self, highlight):
        for i, item in enumerate(self.list_items):
            if item[1] == highlight.item:
                self.list_selection = i
                break

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

    def render_port(self, port):
        key = f"{port.get('object.id')}:{port.get('port.direction')}:{port.get('port.id')}"
        tag = ''
        if key in self.selected_ports:
            tag = ' (*)'
        items = [(
            port,
            ListItem(
                Horizontal(
                    Label(port.get("object.id", ""), classes="col_1"),
                    Label("", classes="col_1"),
                    Label(
                        f"{port.get('port.id', '')}: {port.get('port.name', '')}{tag}",
                        classes="col_5"
                    )
                )
            )
        )]
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
                    item,
                    ListItem(
                        Horizontal(
                            Label("", classes="col_1_5"),
                            Label("input", classes="col_5_5")
                        )
                    )
                ))
                for i in sorted(in_ports, key=lambda p: p.get("port.id")):
                    items.extend(self.render_port(i))

            if out_ports:
                items.append((
                    item,
                    ListItem(
                        Horizontal(
                            Label("", classes="col_1_5"),
                            Label("output", classes="col_5_5")
                        )
                    )
                ))
                for i in sorted(out_ports, key=lambda p: p.get("port.id")):
                    items.extend(self.render_port(i))

            if mon_ports:
                items.append((
                    item,
                    ListItem(
                        Horizontal(
                            Label("", classes="col_1_5"),
                            Label("monitor", classes="col_5_5")
                        )
                    )
                ))
                for i in sorted(mon_ports, key=lambda p: p.get("port.id")):
                    items.extend(self.render_port(i))

        return items

    async def action_filter_audio(self):
        self.media_type = "audio"
        await self.redraw()

    async def action_filter_jack_midi(self):
        self.media_type = "jack_midi"
        await self.redraw()

    async def action_filter_midi(self):
        self.media_type = "alsa_midi"
        await self.redraw()

    async def action_filter_video(self):
        self.media_type = "video"
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
