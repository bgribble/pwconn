"""
alsa_info.py -- load info about the alsa sequencer graph using aconnect
"""
import json
import subprocess
import re
import logging

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
    """
    Get info about ALSA sequencer clients and format
    """
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
    ports = {}

    # link lines can reference to clients we haven't seen yet
    pending_in = []

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

        # port line
        elif line.startswith("    "):
            key, val = stripline.split(" ", maxsplit=1)
            val = re.sub("'", '"', val)
            obj_key = f"{current_id}:{key}"

            if obj_key in in_ports:
                obj_id = f"{current_id}:in:{key}"
                port_obj = {
                    "object.pwtype": "port",
                    "object.id": obj_id,
                    "port.id": f"in:{key}",
                    "node.id": current_id,
                    "port.direction": "in",
                    "port.name": json.loads(val).strip(),
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
                connlist = re.sub(r"\[[^]]+\]", "", match.group(3))
                conninfo = connlist.split(", ")

                for link in conninfo:
                    link_obj = {
                        'object.pwtype': "link",
                    }
                    other_port = None
                    if direction == "To:":
                        node_id, port_num = link.split(":")
                        port_id = f"{node_id}:in:{port_num}"
                        link_id = f"link:{current_port['object.id']}:{port_id}"
                        link_obj["object.id"] = link_id
                        pending_in.append((port_id, link_id))
                        link_obj['link.input.node'] = node_id
                        link_obj['link.input.port'] = port_id
                        link_obj['link.output.node'] = current_port['node.id']
                        link_obj['link.output.port'] = current_port['object.id']
                        links = current_port.setdefault("port.links_in", [])
                        if link_id not in links:
                            links.append(link_id)

                        info[link_id] = link_obj
    
    for obj_id, link_id in pending_in:
        port = info.get(obj_id)
        if not port:
            continue
        links = port.setdefault("port.links_out", [])
        if link_id not in links:
            links.append(link_id)

    return info
