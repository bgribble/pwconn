"""
pw_info.py -- load information about the Pipewire graph using pw-cli
"""

import json
import subprocess
import logging

def pw_type(obj):
    if (
        ("media.type" in obj and obj["media.type"] == "Audio")
        or ("media.class" in obj and obj["media.class"] == "Audio/Device")
    ):
        return "device_audio"
    elif (
       "media.class" in obj and obj["media.class"].startswith("Midi")
    ):
        return "device_jack_midi"
    elif (
       "media.class" in obj and obj["media.class"] == "Video/Device"
    ):
        return "device_video"
    elif "port.id" in obj:
        return "port"
    elif "media.class" in obj and obj["media.class"] in (
        "Audio/Source", "Audio/Sink", "Video/Source", "Video/Sink"
    ):
        return "portgroup"
    elif "link.output.port" in obj:
        return "link"
    elif "Client" in obj.get("object.type", ""):
        return "client"
    else:
        return "unknown"

def annotate_pw_info(info):
    for obj_id, obj in info.items():
        obj["object.pwtype"] = pw_type(obj)

        if obj["object.pwtype"] == "device_video":
            obj["device.nick"] = obj["device.description"]
        elif obj["object.pwtype"] == "port":
            node = info.get(obj['node.id'])
            ports = node.setdefault('node.ports', [])
            ports.append(obj_id)

            node_type = pw_type(node)
            obj['port.type'] = "audio"
            if "midi" in node_type:
                obj['port.type'] = "midi"

            # JACK MIDI ports are sometimes connected to
            # nodes that are media.type Audio along with legit audio ports
            # (but not always)
            if obj.get("format.dsp") == "8 bit raw midi" and node.get("media.type") == "Audio":
                node["object.pwtype"] = "device_audio device_jack_midi"
                obj['port.type'] = "midi"
        elif obj["object.pwtype"] == "portgroup":
            device = info.get(obj['device.id'])
            groups = device.setdefault("node.portgroups", [])
            groups.append(obj_id)
        elif obj["object.pwtype"] == "link":
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



