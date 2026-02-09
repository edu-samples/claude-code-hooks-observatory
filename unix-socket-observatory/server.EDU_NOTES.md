# server.py (HTTPServer) - Educational Notes

How the Unix socket HTTPServer variant works.

## Architecture

```
curl --unix-socket  →  UnixHTTPServer (AF_UNIX)  →  HookHandler.do_POST()  →  OutputManager  →  stdout / output socket
```

## Key Override: UnixHTTPServer

Python's `HTTPServer` normally creates a TCP socket. We override three things to make it use Unix domain sockets:

### 1. address_family

```python
class UnixHTTPServer(HTTPServer):
    address_family = socket.AF_UNIX
```

This single line changes the socket type from `AF_INET` (TCP) to `AF_UNIX` (filesystem). The `HTTPServer.__init__` reads `self.address_family` when creating the socket.

### 2. server_bind()

```python
def server_bind(self):
    self.socket.bind(self.server_address)  # Bind to path, not (host, port)
    os.chmod(self.socket_path, self._socket_mode)  # Set permissions
```

TCP sockets bind to `(host, port)`. Unix sockets bind to a filesystem path. After binding, we set the file permissions to control who can connect.

### 3. service_actions()

```python
def service_actions(self):
    self.output_manager.accept_pending()
```

`serve_forever()` calls `service_actions()` on every iteration of its main loop. We use this to check for new output socket connections without needing a separate thread.

## SO_PEERCRED: Kernel-Verified Identity

The most important difference from TCP: we know exactly who connected.

```python
cred = sock.getsockopt(socket.SOL_SOCKET, SO_PEERCRED, struct.calcsize("3i"))
pid, uid, gid = struct.unpack("3i", cred)
```

The kernel fills a `struct ucred` with the connecting process's PID, UID, and GID. This is unforgeable - unlike TCP where any process on localhost looks the same.

## OutputManager: Multi-Reader Pattern

Instead of shell `tee` + FIFOs, the server has a built-in output socket:

```
./server.py --output-socket /tmp/obs-out.sock
```

The `OutputManager` creates a second Unix socket for readers:

1. Readers connect to the output socket
2. `service_actions()` accepts pending connections each loop iteration
3. `write()` sends data to all connected readers
4. Dead connections are cleaned up automatically

`--tee` sends to both stdout and the output socket. Without it, output goes only to the output socket.

## Differences from TCP server.py

| Aspect | TCP server.py | This file |
|--------|--------------|-----------|
| Server class | `HTTPServer` | `UnixHTTPServer(HTTPServer)` |
| Address | `(host, port)` | Socket file path |
| Client identity | `self.client_address[0]` (IP) | `get_peer_creds(self.request)` |
| Enrichment | `_client: "127.0.0.1"` | `_peer_pid`, `_peer_uid`, `_peer_gid` |
| Output | stdout only | stdout / output socket / tee |
| Security | Bind address | File permissions (`--mode`) |

## What This Hides

This file uses HTTPServer which handles socket creation, accept loops, and HTTP parsing behind clean abstractions. To see every step done manually, read [server_selectors.EDU_NOTES.md](server_selectors.EDU_NOTES.md).
