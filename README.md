# MUSE WebSocket example

This MUSE Python program hosts a small web page on the controller and keeps it
updated over a WebSocket. It uses only the Python standard library, so you don't
need a `requirements.txt` file or internet access on the controller.

## Files

| File | Purpose |
| --- | --- |
| `main.py` | Entry script. Starts the server, handles messages, and runs a MUSE timeline. |
| `websocket_server.py` | Minimal HTTP and WebSocket (RFC 6455) server on one port. |
| `web/index.html` | The demo page that the controller serves. |
| `program.json` | MUSE program metadata. |

## Run the example

1. In VS Code, right-click the `websocket_example` folder and select
   **Upload to Mojo controller**.
1. In a browser, go to `http://<controller-ip>:8080/`.
1. To see shared state, open the same address in a second tab or on another
   device.

The status indicator turns green when the page connects. If the program
restarts, the page reconnects automatically.

## What the page shows

- **Server push:** A MUSE timeline fires every second, and the program sends the
  tick count and controller time to every connected browser.
- **Shared state:** When you select **Toggle switch**, the controller flips a
  value and sends it to every browser.
- **Broadcast messages:** Text you send from one browser appears in every
  browser's message log.
- **Round trip:** The page sends a ping every two seconds and shows the time it
  takes the controller to answer.

## Message format

The browser and controller exchange JSON text frames. Each message has a `type`
field.

| Direction | `type` | Other fields |
| --- | --- | --- |
| Browser to controller | `toggle` | None |
| Browser to controller | `chat` | `text` |
| Browser to controller | `ping` | `sent` |
| Controller to browser | `welcome` | `you`, `switch`, `ticks` |
| Controller to browser | `tick` | `count`, `time` |
| Controller to browser | `clients` | `count` |
| Controller to browser | `switch` | `value`, `by` |
| Controller to browser | `chat` | `from`, `text`, `time` |
| Controller to browser | `pong` | `sent` |

## Adapt the example

- To change the port, edit `HTTP_PORT` in `main.py`. Avoid ports 80 and 443,
  because the MUSE web interface uses them.
- To add a command, add a branch for a new `type` in `on_message()` in
  `main.py`. For example, to drive a relay from the page, get the device with
  `context.devices.get("idevice")` and set the relay state in that branch.
- To push device feedback to the page, call `broadcast_json()` from a device
  `watch` or `listen` callback.

## Limitations

- The server uses plain HTTP and `ws://`, with no authentication. Use it only on
  a trusted control network.
- The server accepts text frames only and closes connections that send a message
  larger than 1 MB.
