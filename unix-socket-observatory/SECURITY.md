# Security: Unix Socket vs TCP

How Unix domain sockets provide stronger security than TCP for local IPC.

## SO_PEERCRED: Kernel-Verified Identity

When a process connects over a Unix socket, the kernel records its credentials. The server retrieves them with `getsockopt`:

```python
# Linux: SO_PEERCRED returns struct ucred {pid_t pid; uid_t uid; gid_t gid;}
SO_PEERCRED = 17
cred = sock.getsockopt(socket.SOL_SOCKET, SO_PEERCRED, struct.calcsize("3i"))
pid, uid, gid = struct.unpack("3i", cred)
```

These credentials are **unforgeable** - they come from the kernel, not from the connecting process. A process cannot claim to be a different PID or UID.

### Platform Support

| Platform | Socket Option | What You Get |
|----------|-------------|-------------|
| Linux | `SO_PEERCRED` | PID, UID, GID in one call |
| macOS | `LOCAL_PEERCRED` + `LOCAL_PEERPID` | UID/GID in one call, PID in a separate call |
| FreeBSD | `LOCAL_PEERCRED` | UID, GID (no PID) |
| Windows | N/A | Unix sockets not available |

Our implementation tries Linux first, falls back to macOS, returns `None` on unsupported platforms.

### TCP Comparison

With TCP on `127.0.0.1`, the server sees the client IP (`127.0.0.1`) but nothing else. Any process on the machine can connect, and they all look identical. You cannot distinguish your Claude Code process from any other local process.

## Filesystem Permissions

Unix socket files support standard filesystem permissions:

```bash
# Default: owner + group can connect
./server.py --mode 0660    # -rw-rw----

# Restrictive: owner only
./server.py --mode 0600    # -rw-------

# Open: anyone on the system
./server.py --mode 0666    # -rw-rw-rw-
```

The server sets permissions on the socket file at creation time. Only processes with the appropriate file permissions can connect.

### Directory Traversal

The connecting process also needs execute permission on every directory in the path to the socket file. For `/tmp/claude-observatory.sock`:

* Need `+x` on `/tmp` (world-executable by default)
* Socket file itself needs read+write for the connecting user

For more restrictive setups:

```bash
mkdir -p /run/user/$(id -u)/claude
./server.py --socket /run/user/$(id -u)/claude/observatory.sock --mode 0600
```

This uses the user's private runtime directory (common on systemd-based Linux).

## TCP vs Unix Socket Security Comparison

| Aspect | TCP (localhost) | Unix Socket |
|--------|----------------|-------------|
| **Who can connect** | Any local process | Controlled by file permissions |
| **Client identity** | IP address (always 127.0.0.1) | PID, UID, GID (kernel-verified) |
| **Identity forgeable?** | N/A (no meaningful identity) | No (kernel-enforced) |
| **Network exposure risk** | Misconfigured `--bind 0.0.0.0` exposes to network | No network exposure possible |
| **Port/path conflicts** | Port collisions possible | Path collisions possible (but easier to avoid) |
| **Firewall needed?** | Yes (if exposed to network) | No (never on network) |
| **Access control** | Bind address only | File permissions + directory traversal |

## Real-World Examples

### PostgreSQL

PostgreSQL supports both TCP and Unix sockets:

```
# TCP (default port 5432)
psql -h localhost -p 5432 mydb

# Unix socket (default /var/run/postgresql/.s.PGSQL.5432)
psql mydb
```

PostgreSQL uses `SO_PEERCRED` to implement `peer` authentication - no password needed because the kernel verifies who you are.

### Docker

Docker's daemon listens on `/var/run/docker.sock`. Access to this socket is equivalent to root access (you can mount the host filesystem in a container). Docker uses the socket's group ownership (`docker` group) for access control.

### SSH Agent

`ssh-agent` uses a Unix socket (`$SSH_AUTH_SOCK`) to communicate with SSH clients. The socket is created in a directory owned by the user with `0700` permissions.

## The `--mode` Flag

```bash
# Owner read+write, group read+write (default)
./server.py --mode 0660

# Owner only
./server.py --mode 0600

# Numeric permissions are octal, same as chmod
```

The first digit is always 0 (no setuid/setgid/sticky). The remaining three digits are owner/group/other permissions, each a sum of: read (4) + write (2) + execute (1).

For Unix sockets, "read" and "write" permissions control who can connect. "Execute" is not used.
