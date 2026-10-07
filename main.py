"""MUSE WebSocket example.

Hosts a small web page on the controller and keeps it updated over a WebSocket.
To try it, upload this program, then browse to http://<controller-ip>:8080/.
Open the page in two browser tabs to see state stay in sync across clients.

The page demonstrates three message flows:
  - Server push: a MUSE timeline sends a tick to every browser once per second.
  - Shared state: any browser can toggle a switch, and every browser sees the change.
  - Broadcast: text that one browser sends appears in every browser's message log.
"""

import json
import os
from datetime import datetime

from mojo import context

from websocket_server import WebSocketServer

HTTP_PORT = 8080  # Port 80 and 443 are used by the MUSE web interface.
WEB_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")

state = {"switch": False, "ticks": 0}

server = WebSocketServer(port=HTTP_PORT, web_root=WEB_ROOT)


def send_json(client, message):
    client.send(json.dumps(message))


def broadcast_json(message):
    server.broadcast(json.dumps(message))


def now():
    return datetime.now().strftime("%H:%M:%S")


# ---- WebSocket callbacks ----------------------------------------------------

def on_connect(client):
    context.log.info("WebSocket client connected: %s (%d total)" % (client.address, len(server.clients)))
    # Brings the new page up to date with the current shared state.
    send_json(client, {"type": "welcome", "you": client.address, "switch": state["switch"], "ticks": state["ticks"]})
    broadcast_json({"type": "clients", "count": len(server.clients)})


def on_disconnect(client):
    context.log.info("WebSocket client disconnected: %s (%d total)" % (client.address, len(server.clients)))
    broadcast_json({"type": "clients", "count": len(server.clients)})


def on_message(client, text):
    try:
        message = json.loads(text)
    except ValueError:
        context.log.warn("Ignored non-JSON message from %s: %s" % (client.address, text))
        return

    kind = message.get("type")
    if kind == "toggle":
        state["switch"] = not state["switch"]
        context.log.info("Switch set to %s by %s" % ("ON" if state["switch"] else "OFF", client.address))
        broadcast_json({"type": "switch", "value": state["switch"], "by": client.address})
    elif kind == "chat":
        body = str(message.get("text", ""))[:500]
        context.log.info("Message from %s: %s" % (client.address, body))
        broadcast_json({"type": "chat", "from": client.address, "text": body, "time": now()})
    elif kind == "ping":
        # Echoes the browser's timestamp back so the page can show round-trip time.
        send_json(client, {"type": "pong", "sent": message.get("sent")})
    else:
        context.log.warn("Unknown message type from %s: %s" % (client.address, kind))


server.on_connect = on_connect
server.on_disconnect = on_disconnect
server.on_message = on_message


# ---- Timeline: push a tick to every browser once per second --------------------

def tick_listener(_event):
    state["ticks"] += 1
    broadcast_json({"type": "tick", "count": state["ticks"], "time": now()})


tick = context.services.get("timeline")
tick.expired.listen(tick_listener)
tick.start([1000], False, -1)


# ---- Start ------------------------------------------------------------------------

server.start()
context.log.info("WebSocket example running: browse to http://<controller-ip>:%d/" % HTTP_PORT)

# leave this as the last line in the Python script
context.run(globals())
