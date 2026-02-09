//! Integration tests for the Rust Observatory binary.
//!
//! These tests spawn the server as a subprocess (like Python's test_server_selectors.py)
//! and communicate with it via TCP or Unix sockets. This verifies the full request/response
//! cycle including HTTP parsing, enrichment, and output formatting.
//!
//! Each test gets a unique port/socket path via an atomic counter to avoid
//! collisions when tests run in parallel.

use std::io::{Read, Write};
use std::net::TcpStream;
use std::os::unix::net::UnixStream;
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicU16, Ordering};
use std::time::Duration;

/// Atomic counter for unique port allocation across parallel tests.
static PORT_COUNTER: AtomicU16 = AtomicU16::new(0);

/// Get a unique port for this test (base 23600 + counter).
fn unique_port() -> u16 {
    23600 + PORT_COUNTER.fetch_add(1, Ordering::SeqCst)
}

/// Get a unique socket path for this test.
fn unique_socket_path() -> String {
    let id = PORT_COUNTER.fetch_add(1, Ordering::SeqCst);
    format!("/tmp/rust-obs-test-{}-{}.sock", std::process::id(), id)
}

/// Path to the built binary (cargo puts it here during `cargo test`).
fn binary_path() -> String {
    let mut path = std::env::current_exe()
        .unwrap()
        .parent() // deps/
        .unwrap()
        .parent() // debug/
        .unwrap()
        .to_path_buf();
    path.push("rust-observatory");
    path.to_string_lossy().to_string()
}

/// Send an HTTP request over a stream and return (status_code, body).
///
/// Uses a raw HTTP/1.1 request with Connection: close to ensure the server
/// closes the connection after responding, allowing read_to_string to return.
fn send_request(
    stream: &mut impl Read,
    writer: &mut impl Write,
    method: &str,
    path: &str,
    body: Option<&str>,
) -> (u16, String) {
    let body_str = body.unwrap_or("");
    let request = if body_str.is_empty() {
        format!(
            "{} {} HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n",
            method, path
        )
    } else {
        format!(
            "{} {} HTTP/1.1\r\nHost: localhost\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
            method, path, body_str.len(), body_str
        )
    };
    writer.write_all(request.as_bytes()).unwrap();
    // Shutdown the write side so server sees EOF if it tries to read more
    let _ = writer.flush();

    let mut response = String::new();
    let _ = stream.read_to_string(&mut response);

    // Parse status code from first line: "HTTP/1.1 200 OK\r\n..."
    let status = response
        .lines()
        .next()
        .and_then(|line| line.split_whitespace().nth(1))
        .and_then(|code| code.parse::<u16>().ok())
        .unwrap_or(0);

    // Extract body (after \r\n\r\n)
    let body_out = response
        .split_once("\r\n\r\n")
        .map(|(_, b)| b.to_string())
        .unwrap_or_default();

    (status, body_out)
}

// === TCP INTEGRATION TESTS ===

/// Start a TCP server on a unique port and return (child, port).
fn start_tcp_server(port: u16) -> Child {
    let child = Command::new(binary_path())
        .arg("tcp")
        .arg("--port")
        .arg(port.to_string())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .expect("Failed to start TCP server");

    // Wait for server to start listening
    for _ in 0..50 {
        if TcpStream::connect(format!("127.0.0.1:{}", port)).is_ok() {
            return child;
        }
        std::thread::sleep(Duration::from_millis(100));
    }
    panic!("TCP server did not start within 5 seconds on port {}", port);
}

#[test]
fn test_tcp_health_returns_ok() {
    let port = unique_port();
    let mut child = start_tcp_server(port);

    let mut stream = TcpStream::connect(format!("127.0.0.1:{}", port)).unwrap();
    stream
        .set_read_timeout(Some(Duration::from_secs(5)))
        .unwrap();
    let mut writer = stream.try_clone().unwrap();

    let (status, body) = send_request(&mut stream, &mut writer, "GET", "/health", None);
    assert_eq!(status, 200);
    let parsed: serde_json::Value = serde_json::from_str(&body).unwrap();
    assert_eq!(parsed["status"], "ok");

    child.kill().unwrap();
    let _ = child.wait();
}

#[test]
fn test_tcp_hook_returns_200() {
    let port = unique_port();
    let mut child = start_tcp_server(port);

    let mut stream = TcpStream::connect(format!("127.0.0.1:{}", port)).unwrap();
    stream
        .set_read_timeout(Some(Duration::from_secs(5)))
        .unwrap();
    let mut writer = stream.try_clone().unwrap();

    let payload = r#"{"tool_name":"Bash","tool_input":{"command":"ls"}}"#;
    let (status, body) = send_request(
        &mut stream,
        &mut writer,
        "POST",
        "/hook?event=PreToolUse",
        Some(payload),
    );
    assert_eq!(status, 200);
    assert_eq!(body, "");

    child.kill().unwrap();
    let _ = child.wait();
}

#[test]
fn test_tcp_outputs_enriched_jsonl() {
    let port = unique_port();
    let mut child = start_tcp_server(port);

    // Send a hook event
    {
        let mut stream = TcpStream::connect(format!("127.0.0.1:{}", port)).unwrap();
        stream
            .set_read_timeout(Some(Duration::from_secs(5)))
            .unwrap();
        let mut writer = stream.try_clone().unwrap();
        let payload = r#"{"tool_name":"Edit"}"#;
        let (status, _) = send_request(
            &mut stream,
            &mut writer,
            "POST",
            "/hook?event=PostToolUse",
            Some(payload),
        );
        assert_eq!(status, 200);
    }

    // Give server time to process and flush stdout
    std::thread::sleep(Duration::from_millis(500));

    // Kill and read stdout
    child.kill().unwrap();
    let output = child.wait_with_output().unwrap();
    let stdout = String::from_utf8_lossy(&output.stdout);

    // Should contain enriched JSONL
    let lines: Vec<&str> = stdout.trim().split('\n').filter(|l| !l.is_empty()).collect();
    assert!(
        !lines.is_empty(),
        "Expected JSONL output on stdout, got nothing"
    );

    let event: serde_json::Value = serde_json::from_str(lines[0]).unwrap();
    assert_eq!(event["_event"], "PostToolUse");
    assert_eq!(event["tool_name"], "Edit");
    assert!(event["_ts"].is_string());
    assert!(event["_client"].is_string()); // TCP includes _client
}

#[test]
fn test_tcp_404_for_get_hook() {
    let port = unique_port();
    let mut child = start_tcp_server(port);

    let mut stream = TcpStream::connect(format!("127.0.0.1:{}", port)).unwrap();
    stream
        .set_read_timeout(Some(Duration::from_secs(5)))
        .unwrap();
    let mut writer = stream.try_clone().unwrap();

    let (status, _) = send_request(
        &mut stream,
        &mut writer,
        "GET",
        "/hook?event=PreToolUse",
        None,
    );
    assert_eq!(status, 404);

    child.kill().unwrap();
    let _ = child.wait();
}

// === UNIX SOCKET INTEGRATION TESTS ===

/// Start a Unix socket server and return (child, socket_path).
fn start_unix_server(socket_path: &str) -> Child {
    // Clean up any stale socket
    let _ = std::fs::remove_file(socket_path);

    let child = Command::new(binary_path())
        .arg("unix")
        .arg("--socket")
        .arg(socket_path)
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .expect("Failed to start Unix server");

    // Wait for socket file to appear and be connectable
    for _ in 0..50 {
        if std::path::Path::new(socket_path).exists() {
            if UnixStream::connect(socket_path).is_ok() {
                return child;
            }
        }
        std::thread::sleep(Duration::from_millis(100));
    }
    panic!(
        "Unix socket server did not start within 5 seconds at {}",
        socket_path
    );
}

#[test]
fn test_unix_health_returns_ok() {
    let path = unique_socket_path();
    let mut child = start_unix_server(&path);

    let mut stream = UnixStream::connect(&path).unwrap();
    stream
        .set_read_timeout(Some(Duration::from_secs(5)))
        .unwrap();
    let mut writer = stream.try_clone().unwrap();

    let (status, body) = send_request(&mut stream, &mut writer, "GET", "/health", None);
    assert_eq!(status, 200);
    let parsed: serde_json::Value = serde_json::from_str(&body).unwrap();
    assert_eq!(parsed["status"], "ok");

    child.kill().unwrap();
    let _ = child.wait();
    let _ = std::fs::remove_file(&path);
}

#[test]
fn test_unix_hook_returns_200() {
    let path = unique_socket_path();
    let mut child = start_unix_server(&path);

    let mut stream = UnixStream::connect(&path).unwrap();
    stream
        .set_read_timeout(Some(Duration::from_secs(5)))
        .unwrap();
    let mut writer = stream.try_clone().unwrap();

    let payload = r#"{"tool_name":"Bash","tool_input":{"command":"ls"}}"#;
    let (status, body) = send_request(
        &mut stream,
        &mut writer,
        "POST",
        "/hook?event=PreToolUse",
        Some(payload),
    );
    assert_eq!(status, 200);
    assert_eq!(body, "");

    child.kill().unwrap();
    let _ = child.wait();
    let _ = std::fs::remove_file(&path);
}

#[test]
fn test_unix_peer_credentials() {
    let path = unique_socket_path();
    let mut child = start_unix_server(&path);

    // Send a hook event
    {
        let mut stream = UnixStream::connect(&path).unwrap();
        stream
            .set_read_timeout(Some(Duration::from_secs(5)))
            .unwrap();
        let mut writer = stream.try_clone().unwrap();
        let payload = r#"{"tool_name":"Read"}"#;
        let (status, _) = send_request(
            &mut stream,
            &mut writer,
            "POST",
            "/hook?event=PreToolUse",
            Some(payload),
        );
        assert_eq!(status, 200);
    }

    // Give server time to flush stdout
    std::thread::sleep(Duration::from_millis(500));

    // Kill and read stdout
    child.kill().unwrap();
    let output = child.wait_with_output().unwrap();
    let stdout = String::from_utf8_lossy(&output.stdout);
    let _ = std::fs::remove_file(&path);

    // Parse JSONL output
    let lines: Vec<&str> = stdout.trim().split('\n').filter(|l| !l.is_empty()).collect();
    assert!(!lines.is_empty(), "Expected JSONL output on stdout");

    let event: serde_json::Value = serde_json::from_str(lines[0]).unwrap();
    assert_eq!(event["_event"], "PreToolUse");
    assert_eq!(event["tool_name"], "Read");

    // On Linux, SO_PEERCRED should provide peer credentials
    if cfg!(target_os = "linux") {
        assert!(
            event["_peer_pid"].is_number(),
            "Expected _peer_pid in output, got: {}",
            event
        );
        assert!(event["_peer_uid"].is_number());
        assert!(event["_peer_gid"].is_number());
        // PID should be positive (valid process)
        assert!(event["_peer_pid"].as_i64().unwrap() > 0);
    }
}

#[test]
fn test_unix_multiple_events() {
    let path = unique_socket_path();
    let mut child = start_unix_server(&path);

    let events = ["SessionStart", "PreToolUse", "PostToolUse", "SessionEnd"];
    for event_name in &events {
        let mut stream = UnixStream::connect(&path).unwrap();
        stream
            .set_read_timeout(Some(Duration::from_secs(5)))
            .unwrap();
        let mut writer = stream.try_clone().unwrap();
        let payload = format!(r#"{{"event_data":"{}"}}"#, event_name);
        let (status, _) = send_request(
            &mut stream,
            &mut writer,
            "POST",
            &format!("/hook?event={}", event_name),
            Some(&payload),
        );
        assert_eq!(status, 200);
    }

    // Give server time to flush
    std::thread::sleep(Duration::from_millis(500));

    child.kill().unwrap();
    let output = child.wait_with_output().unwrap();
    let stdout = String::from_utf8_lossy(&output.stdout);
    let _ = std::fs::remove_file(&path);

    let lines: Vec<&str> = stdout.trim().split('\n').filter(|l| !l.is_empty()).collect();
    assert_eq!(lines.len(), 4, "Expected 4 events, got {}", lines.len());

    for (i, event_name) in events.iter().enumerate() {
        let event: serde_json::Value = serde_json::from_str(lines[i]).unwrap();
        assert_eq!(event["_event"], *event_name);
    }
}
