//! Claude Code Hooks Observatory - Rust Implementation
//!
//! A transparent server for observing Claude Code hook events in real-time.
//! Supports both TCP and Unix domain socket transports in a single binary.
//!
//! Why Rust?
//!     This implementation teaches Rust-specific concepts: enums for transport
//!     abstraction, raw libc FFI for SO_PEERCRED, terminal-native YAML formatting,
//!     and ownership patterns for socket lifecycle management.
//!
//! Usage:
//!     rust-observatory tcp                              # TCP on 127.0.0.1:23518
//!     rust-observatory tcp --port 9999                  # Custom port
//!     rust-observatory tcp --pretty-yaml                # Colored YAML output
//!     rust-observatory unix                             # Unix socket (default path)
//!     rust-observatory unix --socket /tmp/my.sock       # Custom socket path
//!     rust-observatory unix --output-socket /tmp/o.sock # Multi-reader output

use std::collections::HashMap;
use std::io::{IsTerminal, Read, Write};
use std::net::TcpListener;
use std::os::unix::net::{UnixListener, UnixStream};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;

use chrono::Utc;
use clap::{Parser, Subcommand};
use serde_json::Value;

// === CLI DEFINITIONS ===

/// Claude Code Hooks Observatory - Rust implementation
///
/// A transparent server for observing Claude Code hook events.
/// Supports both TCP and Unix socket transports in a single binary.
#[derive(Parser)]
#[command(name = "rust-observatory", version, about)]
struct Cli {
    #[command(subcommand)]
    mode: TransportMode,
}

#[derive(Subcommand)]
enum TransportMode {
    /// Listen on a TCP socket (like tcp-observatory/server.py)
    Tcp {
        /// Port to listen on
        #[arg(long, default_value_t = DEFAULT_TCP_PORT)]
        port: u16,

        /// Address to bind to (default: 127.0.0.1 for security)
        #[arg(long, default_value = DEFAULT_BIND)]
        bind: String,

        /// Output indented multiline JSON
        #[arg(long, group = "format")]
        pretty_json: bool,

        /// Output YAML with terminal syntax highlighting
        #[arg(long, group = "format")]
        pretty_yaml: bool,
    },

    /// Listen on a Unix domain socket (like unix-socket-observatory/server.py)
    Unix {
        /// Socket file path
        #[arg(long, default_value = DEFAULT_SOCKET)]
        socket: String,

        /// Socket file permissions in octal (e.g., 0660)
        #[arg(long, default_value = "0660")]
        mode: String,

        /// Output socket path for multi-reader output
        #[arg(long)]
        output_socket: Option<String>,

        /// Output to both stdout and output socket
        #[arg(long, requires = "output_socket")]
        tee: bool,

        /// Output indented multiline JSON
        #[arg(long, group = "format")]
        pretty_json: bool,

        /// Output YAML with terminal syntax highlighting
        #[arg(long, group = "format")]
        pretty_yaml: bool,
    },
}

// === CONSTANTS ===

const DEFAULT_TCP_PORT: u16 = 23518; // Same as Python tcp-observatory
const DEFAULT_BIND: &str = "127.0.0.1";
const DEFAULT_SOCKET: &str = "/tmp/claude-observatory-rust.sock";
const ENV_TCP_PORT: &str = "CLAUDE_REST_HOOK_WATCHER";
const ENV_UNIX_SOCKET: &str = "CLAUDE_RUST_UNIX_HOOK_WATCHER";

// === OUTPUT FORMATTING ===

/// Output format, set once at startup from CLI flags.
#[derive(Clone, Copy)]
enum OutputMode {
    Jsonl,      // Compact single-line JSON (default, pipeable)
    PrettyJson, // Indented JSON (human-readable)
    PrettyYaml, // YAML with syntax highlighting (if TTY)
}

/// Minimal YAML formatter that uses terminal-native attributes (bold/normal)
/// instead of forcing a color theme.
///
/// Unlike syntect/pygments which impose specific colors (even with 256-color
/// approximation), this uses only bold (\x1b[1m) for keys and the terminal's
/// default foreground for values. The output adapts perfectly to any terminal
/// color scheme - dark, light, solarized, etc.
struct YamlHighlighter;

impl YamlHighlighter {
    fn new() -> Self {
        Self
    }

    /// Highlight YAML by bolding keys, leaving values as default foreground.
    ///
    /// Detects YAML mapping keys (lines matching `indent + key + ":"`) and
    /// applies bold. Everything else uses the terminal's normal foreground color.
    fn highlight(&self, yaml_text: &str) -> String {
        let mut output = String::new();
        for line in yaml_text.lines() {
            let trimmed = line.trim_start();
            // YAML mapping key: starts with word chars (or quoted), followed by ":"
            // Skip list items (- ...) and comments (# ...)
            if !trimmed.is_empty()
                && !trimmed.starts_with('-')
                && !trimmed.starts_with('#')
            {
                // Find the key-value separator ": " or trailing ":"
                if let Some(colon_pos) = trimmed.find(": ").map(|p| {
                    // Offset back to full line position
                    p + (line.len() - trimmed.len())
                }).or_else(|| {
                    if line.ends_with(':') { Some(line.len() - 1) } else { None }
                }) {
                    // Bold the key + colon, normal for the value
                    output.push_str(&format!(
                        "\x1b[1m{}\x1b[22m{}\n",
                        &line[..=colon_pos],
                        &line[colon_pos + 1..]
                    ));
                    continue;
                }
            }
            output.push_str(line);
            output.push('\n');
        }
        output
    }
}

/// Format a single event in the configured output format.
fn format_event(data: &Value, mode: OutputMode, highlighter: &YamlHighlighter) -> String {
    match mode {
        OutputMode::Jsonl => {
            // Compact JSON, no whitespace - ideal for piping to jq
            serde_json::to_string(data).unwrap() + "\n"
        }
        OutputMode::PrettyJson => {
            serde_json::to_string_pretty(data).unwrap() + "\n"
        }
        OutputMode::PrettyYaml => {
            let yaml_text = serde_yaml::to_string(data).unwrap();
            let is_tty = std::io::stdout().is_terminal();
            if is_tty {
                // Gray "---" separator + syntax-highlighted YAML (matching Python's pattern)
                format!("\x1b[90m---\x1b[0m\n{}", highlighter.highlight(&yaml_text))
            } else {
                // Plain YAML when piped (no ANSI escape codes)
                format!("---\n{}", yaml_text)
            }
        }
    }
}

// === HTTP PARSING ===
// Manual HTTP parsing - same approach as Python's server_selectors.py.
// This shows what HTTP frameworks (hyper, actix, etc.) do behind the scenes.

/// Parse a raw HTTP request into (method, path, body, headers).
///
/// HTTP/1.1 requests look like:
///     POST /hook?event=PreToolUse HTTP/1.1\r\n
///     Content-Type: application/json\r\n
///     Content-Length: 42\r\n
///     \r\n
///     {"tool_name": "Bash"}
///
/// The \r\n\r\n separates headers from body.
fn parse_http_request(data: &[u8]) -> (String, String, String, HashMap<String, String>) {
    let text = String::from_utf8_lossy(data);

    // Split headers from body at the blank line
    let (header_section, body) = match text.find("\r\n\r\n") {
        Some(pos) => (&text[..pos], text[pos + 4..].to_string()),
        None => (text.as_ref(), String::new()),
    };

    let mut lines = header_section.split("\r\n");

    // First line: "POST /hook?event=PreToolUse HTTP/1.1"
    let request_line = lines.next().unwrap_or("");
    let parts: Vec<&str> = request_line.splitn(3, ' ').collect();
    let method = parts.first().unwrap_or(&"").to_string();
    let path = parts.get(1).unwrap_or(&"/").to_string();

    // Remaining lines are headers: "Key: Value"
    let mut headers = HashMap::new();
    for line in lines {
        if let Some((key, value)) = line.split_once(": ") {
            headers.insert(key.to_lowercase(), value.to_string());
        }
    }

    (method, path, body, headers)
}

/// Build a raw HTTP/1.1 response.
fn build_http_response(status: u16, body: &str) -> Vec<u8> {
    let reason = match status {
        200 => "OK",
        404 => "Not Found",
        _ => "Unknown",
    };
    format!(
        "HTTP/1.1 {} {}\r\nContent-Type: application/json\r\nContent-Length: {}\r\n\r\n{}",
        status,
        reason,
        body.len(),
        body
    )
    .into_bytes()
}

/// Parse URL query string into key-value pairs.
/// "event=PreToolUse&foo=bar" → {"event": "PreToolUse", "foo": "bar"}
fn parse_query_string(query: &str) -> HashMap<String, String> {
    query
        .split('&')
        .filter(|s| !s.is_empty())
        .filter_map(|pair| {
            let (k, v) = pair.split_once('=')?;
            Some((k.to_string(), v.to_string()))
        })
        .collect()
}

// === TIMESTAMPS & ENRICHMENT ===

/// Peer information varies by transport type.
/// TCP: we only know the client IP address.
/// Unix: the kernel tells us PID, UID, GID (unforgeable via SO_PEERCRED).
enum PeerInfo {
    Tcp { client_addr: String },
    Unix { pid: i32, uid: u32, gid: u32 },
    Unknown,
}

/// Return current UTC timestamp in ISO 8601 format.
/// Matches Python's: datetime.now(timezone.utc).isoformat(timespec="seconds")
fn get_timestamp() -> String {
    Utc::now().format("%Y-%m-%dT%H:%M:%S+00:00").to_string()
}

/// Add metadata fields (prefixed with _) to the payload.
/// The underscore prefix distinguishes our fields from Claude Code's payload fields.
fn enrich_payload(payload: Value, event: &str, peer: &PeerInfo) -> Value {
    let mut result = serde_json::Map::new();
    result.insert("_ts".into(), Value::String(get_timestamp()));
    result.insert("_event".into(), Value::String(event.to_string()));

    match peer {
        PeerInfo::Tcp { client_addr } => {
            result.insert("_client".into(), Value::String(client_addr.clone()));
        }
        PeerInfo::Unix { pid, uid, gid } => {
            result.insert("_peer_pid".into(), serde_json::json!(*pid));
            result.insert("_peer_uid".into(), serde_json::json!(*uid));
            result.insert("_peer_gid".into(), serde_json::json!(*gid));
        }
        PeerInfo::Unknown => {}
    }

    // Merge original payload fields after our metadata
    if let Value::Object(map) = payload {
        for (k, v) in map {
            result.insert(k, v);
        }
    }

    Value::Object(result)
}

// === SO_PEERCRED (libc FFI) ===
// Raw libc calls to get peer credentials from Unix domain sockets.
// This is intentionally low-level (not using rustix/nix) to show the FFI boundary.

/// Get peer credentials (pid, uid, gid) from a Unix domain socket.
///
/// The kernel records which process connected to our socket. We retrieve
/// this with getsockopt(SO_PEERCRED) on Linux. These credentials are
/// unforgeable - they come from the kernel, not from the connecting process.
fn get_peer_creds(stream: &UnixStream) -> PeerInfo {
    #[cfg(target_os = "linux")]
    {
        use std::mem;
        use std::os::unix::io::AsRawFd;

        let fd = stream.as_raw_fd();
        let mut ucred: libc::ucred = unsafe { mem::zeroed() };
        let mut len = mem::size_of::<libc::ucred>() as libc::socklen_t;

        let ret = unsafe {
            libc::getsockopt(
                fd,
                libc::SOL_SOCKET,
                libc::SO_PEERCRED,
                &mut ucred as *mut _ as *mut libc::c_void,
                &mut len,
            )
        };

        // pid=0 means the socket isn't AF_UNIX or isn't connected
        // (learned from Python implementation: Linux returns (0,-1,-1) for non-Unix sockets)
        if ret == 0 && ucred.pid > 0 {
            return PeerInfo::Unix {
                pid: ucred.pid,
                uid: ucred.uid,
                gid: ucred.gid,
            };
        }
    }

    #[cfg(target_os = "macos")]
    {
        use std::os::unix::io::AsRawFd;

        let fd = stream.as_raw_fd();
        let mut uid: libc::uid_t = 0;
        let mut gid: libc::gid_t = 0;

        let ret = unsafe { libc::getpeereid(fd, &mut uid, &mut gid) };

        if ret == 0 {
            return PeerInfo::Unix {
                pid: -1, // macOS getpeereid doesn't provide PID
                uid,
                gid,
            };
        }
    }

    PeerInfo::Unknown
}

// === OUTPUT MANAGER ===
// Manages where output goes: stdout, output socket, or both (tee).
// Mirrors Python's OutputManager class from unix-socket-observatory/server.py.

struct OutputManager {
    tee: bool,
    has_output_socket: bool,
    listener: Option<UnixListener>,
    clients: Vec<UnixStream>,
    output_socket_path: Option<String>,
}

impl OutputManager {
    fn new(output_socket_path: Option<String>, tee: bool) -> std::io::Result<Self> {
        let listener = if let Some(ref path) = output_socket_path {
            // Clean up stale socket file from a previous crash
            let _ = std::fs::remove_file(path);
            let listener = UnixListener::bind(path)?;
            listener.set_nonblocking(true)?;
            eprintln!("Output socket: {}", path);
            Some(listener)
        } else {
            None
        };

        Ok(Self {
            tee,
            has_output_socket: output_socket_path.is_some(),
            listener,
            clients: Vec::new(),
            output_socket_path,
        })
    }

    /// Accept any pending output socket connections (non-blocking).
    fn accept_pending(&mut self) {
        if let Some(ref listener) = self.listener {
            loop {
                match listener.accept() {
                    Ok((client, _)) => {
                        let _ = client.set_nonblocking(true);
                        self.clients.push(client);
                        eprintln!(
                            "Output reader connected ({} total)",
                            self.clients.len()
                        );
                    }
                    Err(ref e) if e.kind() == std::io::ErrorKind::WouldBlock => break,
                    Err(_) => break,
                }
            }
        }
    }

    /// Write formatted output to the configured destinations.
    fn write(&mut self, line: &str) {
        if self.has_output_socket && !self.tee {
            // Output socket only - don't write to stdout
            self.write_to_clients(line);
        } else if self.has_output_socket && self.tee {
            // Both stdout and output socket
            print!("{}", line);
            let _ = std::io::stdout().flush();
            self.write_to_clients(line);
        } else {
            // Default: stdout only
            print!("{}", line);
            let _ = std::io::stdout().flush();
        }
    }

    fn write_to_clients(&mut self, line: &str) {
        let data = line.as_bytes();
        let mut dead_indices = Vec::new();
        for (i, client) in self.clients.iter_mut().enumerate() {
            if client.write_all(data).is_err() {
                dead_indices.push(i);
            }
        }
        // Remove dead clients in reverse order to preserve indices
        for i in dead_indices.into_iter().rev() {
            self.clients.remove(i);
        }
    }

    fn cleanup(&mut self) {
        self.clients.clear();
        self.listener = None;
        if let Some(ref path) = self.output_socket_path {
            let _ = std::fs::remove_file(path);
        }
    }
}

// === SOCKET CLEANUP GUARD ===
// Uses Rust's Drop trait to ensure socket files are cleaned up on exit.
// This is more reliable than Python's try/finally - Drop runs even on panic.

struct SocketCleanup {
    path: String,
}

impl Drop for SocketCleanup {
    fn drop(&mut self) {
        let _ = std::fs::remove_file(&self.path);
    }
}

// === CONNECTION HANDLING ===

/// Handle a single HTTP connection. Generic over stream type so it works
/// for both TcpStream and UnixStream - both implement Read + Write.
fn handle_connection(
    stream: &mut (impl Read + Write),
    peer: PeerInfo,
    output_mode: OutputMode,
    highlighter: &YamlHighlighter,
    output_manager: &mut OutputManager,
) {
    // Read the request (hook payloads are small, one read suffices)
    let mut buf = [0u8; 65536];
    let n = match stream.read(&mut buf) {
        Ok(0) | Err(_) => return,
        Ok(n) => n,
    };

    let (method, path, mut body, headers) = parse_http_request(&buf[..n]);

    // GET /health - health check endpoint
    if method == "GET" && path == "/health" {
        let resp = build_http_response(200, r#"{"status":"ok"}"#);
        let _ = stream.write_all(&resp);
        return;
    }

    // Only accept POST requests
    if method != "POST" {
        let resp = build_http_response(404, "");
        let _ = stream.write_all(&resp);
        return;
    }

    // Extract event type from query string: /hook?event=PreToolUse
    let event = if let Some(query_start) = path.find('?') {
        let query = &path[query_start + 1..];
        let params = parse_query_string(query);
        params.get("event").cloned().unwrap_or_else(|| "Unknown".into())
    } else {
        "Unknown".into()
    };

    // If body is shorter than Content-Length, read more
    if let Some(expected_str) = headers.get("content-length") {
        if let Ok(expected) = expected_str.parse::<usize>() {
            while body.len() < expected {
                let mut more = [0u8; 65536];
                match stream.read(&mut more) {
                    Ok(0) | Err(_) => break,
                    Ok(n) => body.push_str(&String::from_utf8_lossy(&more[..n])),
                }
            }
        }
    }

    // Parse JSON payload
    let payload: Value = if body.is_empty() {
        Value::Object(serde_json::Map::new())
    } else {
        serde_json::from_str(&body).unwrap_or_else(|_| {
            serde_json::json!({"_raw": body})
        })
    };

    // Enrich and format
    let enriched = enrich_payload(payload, &event, &peer);
    let formatted = format_event(&enriched, output_mode, highlighter);
    output_manager.write(&formatted);

    // Return empty 200 (no-op response - action proceeds)
    let resp = build_http_response(200, "");
    let _ = stream.write_all(&resp);
}

// === MAIN ===

fn main() {
    let cli = Cli::parse();
    let highlighter = YamlHighlighter::new();

    // Shared shutdown flag for Ctrl+C
    let running = Arc::new(AtomicBool::new(true));
    let r = running.clone();
    let _ = ctrlc_handler(r);

    match cli.mode {
        TransportMode::Tcp {
            port,
            bind,
            pretty_json,
            pretty_yaml,
        } => {
            let output_mode = if pretty_yaml {
                OutputMode::PrettyYaml
            } else if pretty_json {
                OutputMode::PrettyJson
            } else {
                OutputMode::Jsonl
            };

            // Check env var for port override
            let port = match std::env::var(ENV_TCP_PORT) {
                Ok(val) if val.parse::<u16>().is_ok() => {
                    // CLI default means env var wins, explicit CLI wins
                    if port == DEFAULT_TCP_PORT {
                        val.parse().unwrap()
                    } else {
                        port
                    }
                }
                _ => port,
            };

            let addr = format!("{}:{}", bind, port);
            let listener = match TcpListener::bind(&addr) {
                Ok(l) => {
                    // Non-blocking so we can check the shutdown flag between accepts
                    l.set_nonblocking(true).expect("set_nonblocking");
                    l
                }
                Err(e) => {
                    eprintln!("Error: Cannot bind to {}: {}", addr, e);
                    std::process::exit(1);
                }
            };

            eprintln!("Claude Code Hooks Observatory (Rust/TCP) listening on {}", addr);
            eprintln!("Press Ctrl+C to stop\n");

            let mut output_manager = OutputManager::new(None, false).unwrap();

            while running.load(Ordering::SeqCst) {
                match listener.accept() {
                    Ok((mut stream, addr)) => {
                        // Set accepted connection to blocking for reads
                        let _ = stream.set_nonblocking(false);
                        let peer = PeerInfo::Tcp {
                            client_addr: addr.ip().to_string(),
                        };
                        handle_connection(
                            &mut stream,
                            peer,
                            output_mode,
                            &highlighter,
                            &mut output_manager,
                        );
                    }
                    Err(ref e) if e.kind() == std::io::ErrorKind::WouldBlock => {
                        std::thread::sleep(std::time::Duration::from_millis(50));
                    }
                    Err(_) => continue,
                }
            }

            eprintln!("\nShutting down...");
        }

        TransportMode::Unix {
            socket,
            mode,
            output_socket,
            tee,
            pretty_json,
            pretty_yaml,
        } => {
            let output_mode = if pretty_yaml {
                OutputMode::PrettyYaml
            } else if pretty_json {
                OutputMode::PrettyJson
            } else {
                OutputMode::Jsonl
            };

            // Check env var for socket path override
            let socket = match std::env::var(ENV_UNIX_SOCKET) {
                Ok(val) if !val.is_empty() && socket == DEFAULT_SOCKET => val,
                _ => socket,
            };

            // Parse octal permissions
            let perms = u32::from_str_radix(mode.trim_start_matches('0'), 8).unwrap_or(0o660);

            // Clean up stale socket from previous crash
            let _ = std::fs::remove_file(&socket);

            let listener = match UnixListener::bind(&socket) {
                Ok(l) => {
                    l.set_nonblocking(true).expect("set_nonblocking");
                    l
                }
                Err(e) => {
                    eprintln!("Error: Cannot bind to {}: {}", socket, e);
                    std::process::exit(1);
                }
            };

            // Set socket file permissions
            set_socket_permissions(&socket, perms);

            // Socket cleanup on exit (Drop guard)
            let _cleanup = SocketCleanup {
                path: socket.clone(),
            };

            let mut output_manager = match OutputManager::new(output_socket, tee) {
                Ok(m) => m,
                Err(e) => {
                    eprintln!("Error creating output manager: {}", e);
                    std::process::exit(1);
                }
            };

            eprintln!(
                "Claude Code Hooks Observatory (Rust/Unix) listening on {}",
                socket
            );
            eprintln!("Socket permissions: 0{:o}", perms);
            eprintln!("Press Ctrl+C to stop\n");

            while running.load(Ordering::SeqCst) {
                // Poll for output socket connections between requests
                output_manager.accept_pending();

                match listener.accept() {
                    Ok((mut stream, _)) => {
                        let _ = stream.set_nonblocking(false);
                        let peer = get_peer_creds(&stream);
                        handle_connection(
                            &mut stream,
                            peer,
                            output_mode,
                            &highlighter,
                            &mut output_manager,
                        );
                    }
                    Err(ref e) if e.kind() == std::io::ErrorKind::WouldBlock => {
                        std::thread::sleep(std::time::Duration::from_millis(50));
                    }
                    Err(_) => continue,
                }
            }

            eprintln!("\nShutting down...");
            output_manager.cleanup();
        }
    }
}

/// Set up Ctrl+C handler using libc signal handling.
/// Sets the running flag to false so the main loop exits gracefully.
fn ctrlc_handler(running: Arc<AtomicBool>) -> Result<(), std::io::Error> {
    // Use a simple approach: set the listener to non-blocking so we can
    // check the running flag periodically. The actual signal will cause
    // accept() to return an error which we handle in the loop.
    //
    // For proper signal handling, we use libc directly:
    unsafe {
        // Store the Arc's raw pointer in a static so the signal handler can access it
        static mut RUNNING_PTR: *const AtomicBool = std::ptr::null();
        RUNNING_PTR = Arc::into_raw(running.clone());

        extern "C" fn handler(_: libc::c_int) {
            unsafe {
                if !RUNNING_PTR.is_null() {
                    (*RUNNING_PTR).store(false, Ordering::SeqCst);
                }
            }
        }

        libc::signal(libc::SIGINT, handler as libc::sighandler_t);
        libc::signal(libc::SIGTERM, handler as libc::sighandler_t);
    }
    Ok(())
}

/// Set Unix file permissions on a socket path using libc::chmod.
fn set_socket_permissions(path: &str, mode: u32) {
    use std::ffi::CString;
    if let Ok(c_path) = CString::new(path) {
        unsafe {
            libc::chmod(c_path.as_ptr(), mode as libc::mode_t);
        }
    }
}

// === TESTS ===

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_parse_http_request_post() {
        let raw = b"POST /hook?event=PreToolUse HTTP/1.1\r\nContent-Type: application/json\r\nContent-Length: 21\r\n\r\n{\"tool_name\": \"Bash\"}";
        let (method, path, body, headers) = parse_http_request(raw);
        assert_eq!(method, "POST");
        assert_eq!(path, "/hook?event=PreToolUse");
        assert_eq!(body, "{\"tool_name\": \"Bash\"}");
        assert_eq!(headers.get("content-type").unwrap(), "application/json");
        assert_eq!(headers.get("content-length").unwrap(), "21");
    }

    #[test]
    fn test_parse_http_request_get() {
        let raw = b"GET /health HTTP/1.1\r\nHost: localhost\r\n\r\n";
        let (method, path, body, _headers) = parse_http_request(raw);
        assert_eq!(method, "GET");
        assert_eq!(path, "/health");
        assert_eq!(body, "");
    }

    #[test]
    fn test_parse_http_request_empty_body() {
        let raw = b"POST /hook?event=Stop HTTP/1.1\r\nContent-Length: 0\r\n\r\n";
        let (method, path, body, _) = parse_http_request(raw);
        assert_eq!(method, "POST");
        assert_eq!(path, "/hook?event=Stop");
        assert_eq!(body, "");
    }

    #[test]
    fn test_build_http_response_200() {
        let resp = build_http_response(200, "");
        let text = String::from_utf8(resp).unwrap();
        assert!(text.starts_with("HTTP/1.1 200 OK\r\n"));
        assert!(text.contains("Content-Length: 0"));
    }

    #[test]
    fn test_build_http_response_200_with_body() {
        let resp = build_http_response(200, r#"{"status":"ok"}"#);
        let text = String::from_utf8(resp).unwrap();
        assert!(text.contains("Content-Length: 15"));
        assert!(text.ends_with(r#"{"status":"ok"}"#));
    }

    #[test]
    fn test_build_http_response_404() {
        let resp = build_http_response(404, "");
        let text = String::from_utf8(resp).unwrap();
        assert!(text.starts_with("HTTP/1.1 404 Not Found"));
    }

    #[test]
    fn test_parse_query_string() {
        let params = parse_query_string("event=PreToolUse&foo=bar");
        assert_eq!(params.get("event").unwrap(), "PreToolUse");
        assert_eq!(params.get("foo").unwrap(), "bar");
    }

    #[test]
    fn test_parse_query_string_single() {
        let params = parse_query_string("event=SessionStart");
        assert_eq!(params.get("event").unwrap(), "SessionStart");
        assert_eq!(params.len(), 1);
    }

    #[test]
    fn test_parse_query_string_empty() {
        let params = parse_query_string("");
        assert!(params.is_empty());
    }

    #[test]
    fn test_enrich_payload_tcp() {
        let payload = serde_json::json!({"tool_name": "Bash"});
        let peer = PeerInfo::Tcp {
            client_addr: "127.0.0.1".into(),
        };
        let result = enrich_payload(payload, "PreToolUse", &peer);
        let obj = result.as_object().unwrap();
        assert!(obj.contains_key("_ts"));
        assert_eq!(obj["_event"], "PreToolUse");
        assert_eq!(obj["_client"], "127.0.0.1");
        assert_eq!(obj["tool_name"], "Bash");
        assert!(!obj.contains_key("_peer_pid"));
    }

    #[test]
    fn test_enrich_payload_unix() {
        let payload = serde_json::json!({"tool_name": "Read"});
        let peer = PeerInfo::Unix {
            pid: 1234,
            uid: 1000,
            gid: 1000,
        };
        let result = enrich_payload(payload, "PostToolUse", &peer);
        let obj = result.as_object().unwrap();
        assert_eq!(obj["_event"], "PostToolUse");
        assert_eq!(obj["_peer_pid"], 1234);
        assert_eq!(obj["_peer_uid"], 1000);
        assert_eq!(obj["_peer_gid"], 1000);
        assert_eq!(obj["tool_name"], "Read");
        assert!(!obj.contains_key("_client"));
    }

    #[test]
    fn test_get_timestamp_format() {
        let ts = get_timestamp();
        // Should be ISO 8601 format with timezone
        assert!(ts.contains("+00:00") || ts.ends_with("Z"));
        assert!(ts.contains("T"));
        // Should be parseable
        assert!(ts.len() >= 20);
    }

    #[test]
    fn test_format_event_jsonl() {
        let data = serde_json::json!({"a": 1, "b": 2});
        let highlighter = YamlHighlighter::new();
        let output = format_event(&data, OutputMode::Jsonl, &highlighter);
        // Should be single line
        assert_eq!(output.matches('\n').count(), 1);
        // Should be valid JSON
        let parsed: Value = serde_json::from_str(output.trim()).unwrap();
        assert_eq!(parsed["a"], 1);
    }

    #[test]
    fn test_format_event_pretty_json() {
        let data = serde_json::json!({"key": "value"});
        let highlighter = YamlHighlighter::new();
        let output = format_event(&data, OutputMode::PrettyJson, &highlighter);
        // Should be multi-line (indented)
        assert!(output.matches('\n').count() > 1);
        // Should be valid JSON
        let parsed: Value = serde_json::from_str(output.trim()).unwrap();
        assert_eq!(parsed["key"], "value");
    }
}
