// release builds on Windows get no console window
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]
use std::collections::{HashMap, VecDeque};
use std::fs::OpenOptions;
use std::io::{Read, Write};
use std::net::{SocketAddr, TcpStream};
use std::path::{Path, PathBuf};
use std::sync::{Mutex, OnceLock};
use std::time::{Duration, Instant};

mod tray;
#[cfg(windows)]
mod webview_watch;

use tauri::{Manager, RunEvent, State, WebviewUrl, WebviewWindowBuilder, WindowEvent};
use tauri_plugin_dialog::DialogExt;
use tauri_plugin_shell::process::{CommandChild, CommandEvent};
use tauri_plugin_shell::ShellExt;

pub struct Shell {
    pub port: Option<u16>,
    pub failed: bool,
    pub lines: VecDeque<String>,
    pub log_path: PathBuf,
    pub data_dir: PathBuf,
    pub token: String,
    pub child: Option<CommandChild>,
}

/// The boot page; Windows serves the app's own pages from http://tauri.localhost.
pub(crate) const BOOT_URL: &str =
    if cfg!(windows) { "http://tauri.localhost/index.html" } else { "tauri://localhost/index.html" };

/// WebView2 arguments for every window. Setting any replaces wry's defaults, so they are repeated
/// here (the first two). --no-proxy-server: a system or PAC proxy must never sit between the window
/// and the loopback server; the UI loads nothing from anywhere else. All windows share one WebView2
/// data folder, so they must all get exactly these.
#[cfg_attr(not(windows), allow(dead_code))]
pub(crate) const WEBVIEW_ARGS: &str =
    "--disable-features=msWebOOUI,msPdfOOUI,msSmartScreenProtection --autoplay-policy=no-user-gesture-required --no-proxy-server";

static STARTED: OnceLock<Instant> = OnceLock::new();

/// One line from the shell itself into muse.log, next to the sidecar's output.
pub(crate) fn shell_log(path: &Path, msg: &str) {
    let secs = STARTED.get_or_init(Instant::now).elapsed().as_secs_f32();
    if let Ok(mut f) = OpenOptions::new().create(true).append(true).open(path) {
        let _ = writeln!(f, "shell +{secs:.1}s  {msg}");
    }
}

/// Show the boot page's failure state with these lines, whatever the main window is showing.
pub(crate) fn fail_main(app: &tauri::AppHandle, lines: Vec<String>) {
    {
        let s = app.state::<Mutex<Shell>>();
        let mut s = s.lock().unwrap();
        s.failed = true;
        s.port = None;
        for line in lines {
            shell_log(&s.log_path, &line);
            push_line(&mut s, line);
        }
    }
    if let Some(m) = app.get_webview_window("mini") {
        let _ = m.close();
    }
    if let (Some(w), Ok(url)) = (app.get_webview_window("main"), BOOT_URL.parse()) {
        let _ = w.navigate(url);
    }
}

/// Wait until the server answers an HTTP request on the loopback port. Any status counts: this only
/// asks whether this PC lets a local program connect to it at all. MUSE_LISTENING comes before the
/// app's startup (migrations included), so a slow first answer is normal; hence the long deadline.
fn probe(port: u16) -> Result<(), String> {
    let addr = SocketAddr::from(([127, 0, 0, 1], port));
    let deadline = Instant::now() + Duration::from_secs(120);
    loop {
        let err = match probe_once(addr) {
            Ok(()) => return Ok(()),
            Err(e) => e,
        };
        if Instant::now() >= deadline {
            return Err(err);
        }
        std::thread::sleep(Duration::from_millis(500));
    }
}

fn probe_once(addr: SocketAddr) -> Result<(), String> {
    let mut stream = TcpStream::connect_timeout(&addr, Duration::from_secs(3)).map_err(|e| format!("connect: {e}"))?;
    let _ = stream.set_read_timeout(Some(Duration::from_secs(10)));
    let _ = stream.set_write_timeout(Some(Duration::from_secs(3)));
    stream
        .write_all(format!("GET / HTTP/1.1\r\nHost: {addr}\r\nConnection: close\r\n\r\n").as_bytes())
        .map_err(|e| format!("send: {e}"))?;
    let mut head = [0u8; 8];
    let mut n = 0;
    while n < head.len() {
        match stream.read(&mut head[n..]) {
            Ok(0) => break,
            Ok(k) => n += k,
            Err(e) => return Err(format!("read: {e}")),
        }
    }
    if head[..n].starts_with(b"HTTP/1.") {
        Ok(())
    } else if n == 0 {
        Err("the connection closed with no reply".to_string())
    } else {
        Err(format!("unexpected reply {:?}", String::from_utf8_lossy(&head[..n])))
    }
}

fn push_line(shell: &mut Shell, line: String) {
    if shell.lines.len() >= 20 {
        shell.lines.pop_front();
    }
    shell.lines.push_back(line);
}

#[tauri::command]
fn boot_status(state: State<'_, Mutex<Shell>>) -> serde_json::Value {
    let s = state.lock().unwrap();
    serde_json::json!({
        "state": if s.failed { "failed" } else { "starting" },
        "lines": s.lines,
        "log_path": s.log_path.to_string_lossy(),
    })
}

/// Downloads and installs the newest release, then restarts into it. Ok(false) when the update feed
/// has nothing newer to offer. The restart goes through RunEvent::Exit, so the sidecar is dropped as
/// on any quit; on Windows the installer's preinstall hook stops it before files are copied.
#[tauri::command]
async fn update_install(app: tauri::AppHandle) -> Result<bool, String> {
    use tauri_plugin_updater::UpdaterExt;
    let update = app.updater().map_err(|e| e.to_string())?.check().await.map_err(|e| e.to_string())?;
    let Some(update) = update else { return Ok(false) };
    update.download_and_install(|_, _| {}, || {}).await.map_err(|e| e.to_string())?;
    app.request_restart();
    Ok(true)
}

#[tauri::command]
fn smoke_report(app: tauri::AppHandle, report: serde_json::Value) -> Result<(), String> {
    let path = std::env::var("MUSE_SMOKE_REPORT").map_err(|_| "smoke mode is off".to_string())?;
    std::fs::write(path, report.to_string()).map_err(|e| e.to_string())?;
    app.exit(0);
    Ok(())
}

pub(crate) fn allowed_origin(url: &tauri::Url, port: Option<u16>) -> bool {
    url.scheme() == "tauri"
        || url.host_str() == Some("tauri.localhost")
        || (url.scheme() == "http" && url.host_str() == Some("127.0.0.1") && port.is_some() && url.port() == port)
}

pub(crate) fn current_port(app: &tauri::AppHandle) -> Option<u16> {
    let s = app.try_state::<Mutex<Shell>>()?;
    let s = s.lock().unwrap();
    if s.failed { None } else { s.port }
}

fn main() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            tray::show_main(app.clone());
        }))
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .invoke_handler(tauri::generate_handler![
            boot_status,
            smoke_report,
            update_install,
            tray::open_mini,
            tray::show_main
        ])
        .setup(|app| {
            let data_dir = match std::env::var_os("MUSE_DATA_DIR") {
                Some(d) => PathBuf::from(d),
                None => app.path().app_local_data_dir()?,
            };
            std::fs::create_dir_all(&data_dir)?;
            let log_path = data_dir.join("muse.log");
            if std::fs::metadata(&log_path).map(|m| m.len() > 5 * 1024 * 1024).unwrap_or(false) {
                let _ = std::fs::rename(&log_path, data_dir.join("muse.log.1"));
            }
            shell_log(&log_path, &format!("Muse {} starting", app.package_info().version));
            // A release build on Windows has no console, so a panic would vanish without this.
            let hook_log = log_path.clone();
            let default_hook = std::panic::take_hook();
            std::panic::set_hook(Box::new(move |info| {
                shell_log(&hook_log, &format!("panic: {info}"));
                default_hook(info);
            }));

            let mut bytes = [0u8; 32];
            getrandom::fill(&mut bytes).map_err(|e| format!("getrandom: {e}"))?;
            let token: String = bytes.iter().map(|b| format!("{b:02x}")).collect();

            let mut script = format!("window.__MUSE_TOKEN__ = \"{token}\";");
            if std::env::var_os("MUSE_SMOKE_REPORT").is_some() {
                script.push_str(include_str!("smoke.js"));
            }

            let nav_handle = app.handle().clone();
            let builder = WebviewWindowBuilder::new(app, "main", WebviewUrl::App("index.html".into()))
                .title("Muse")
                .inner_size(1200.0, 800.0)
                .min_inner_size(360.0, 560.0)
                .initialization_script(script)
                .on_navigation(move |url| allowed_origin(url, current_port(&nav_handle)));
            #[cfg(windows)]
            let builder = builder.additional_browser_args(WEBVIEW_ARGS);
            let window = builder.build()?;
            #[cfg(windows)]
            webview_watch::watch(&window, app.handle().clone(), log_path.clone());

            let mut env = HashMap::new();
            env.insert("MUSE_DATA_DIR".to_string(), data_dir.to_string_lossy().into_owned());
            env.insert("MUSE_TOKEN".to_string(), token.clone());
            env.insert("MUSE_APP_VERSION".to_string(), app.package_info().version.to_string());
            env.insert("MUSE_PARENT_WATCH".to_string(), "1".to_string());
            env.insert("OPENBLAS_NUM_THREADS".to_string(), "1".to_string());
            #[cfg(debug_assertions)]
            env.insert(
                "MUSE_WEB_DIST".to_string(),
                concat!(env!("CARGO_MANIFEST_DIR"), "/../web/dist").to_string(),
            );

            let (mut rx, child) = app.shell().sidecar("muse-core")?.envs(env).spawn()?;
            app.manage(Mutex::new(Shell {
                port: None,
                failed: false,
                lines: VecDeque::new(),
                log_path: log_path.clone(),
                data_dir,
                token,
                child: Some(child),
            }));

            tray::build(app.handle())?;

            let handle = app.handle().clone();
            let hider = window.clone();
            window.on_window_event(move |event| {
                let WindowEvent::CloseRequested { api, .. } = event else { return };
                let (ready, data_dir) = {
                    let s = handle.state::<Mutex<Shell>>();
                    let s = s.lock().unwrap();
                    (s.port.is_some(), s.data_dir.clone())
                };
                if !ready {
                    return;
                }
                api.prevent_close();
                let _ = hider.hide();
                let marker = data_dir.join(".tray-notice-shown");
                if !marker.exists() && std::fs::write(&marker, "").is_ok() {
                    let place = if cfg!(target_os = "macos") { "menu bar" } else { "tray" };
                    handle
                        .dialog()
                        .message(format!(
                            "Muse is still playing in the {place}. To quit, choose Quit Muse from its menu."
                        ))
                        .title("Muse")
                        .show(|_| {});
                }
            });

            let handle = app.handle().clone();
            tauri::async_runtime::spawn(async move {
                let mut log = OpenOptions::new().create(true).append(true).open(&log_path).ok();
                while let Some(event) = rx.recv().await {
                    let text = match &event {
                        CommandEvent::Stdout(b) | CommandEvent::Stderr(b) => {
                            Some(String::from_utf8_lossy(b).trim_end().to_string())
                        }
                        CommandEvent::Terminated(p) => {
                            let died_after_start = {
                                let s = handle.state::<Mutex<Shell>>();
                                let mut s = s.lock().unwrap();
                                let after_start = s.port.take().is_some();
                                s.failed = true;
                                let code = p.code.map_or("none".to_string(), |c| c.to_string());
                                push_line(&mut s, format!("Muse stopped (exit code {code})"));
                                after_start
                            };
                            if died_after_start {
                                if let Some(m) = handle.get_webview_window("mini") {
                                    let _ = m.close();
                                }
                                if let Ok(url) = BOOT_URL.parse() {
                                    let _ = window.navigate(url);
                                }
                            }
                            None
                        }
                        _ => None,
                    };
                    let Some(text) = text else { continue };
                    if let Some(f) = log.as_mut() {
                        let _ = writeln!(f, "{text}");
                    }
                    let mut port = text.strip_prefix("MUSE_LISTENING ").and_then(|p| p.trim().parse::<u16>().ok());
                    {
                        let s = handle.state::<Mutex<Shell>>();
                        let mut s = s.lock().unwrap();
                        if s.failed {
                            port = None; // late port after failure: logged, never adopted
                        }
                        push_line(&mut s, text);
                        if let Some(p) = port {
                            s.port = Some(p);
                        }
                    }
                    if let Some(p) = port {
                        // Check the window will be able to reach the server before sending it there:
                        // if this PC blocks loopback connections, say so instead of showing a blank page.
                        let (h, w, lp) = (handle.clone(), window.clone(), log_path.clone());
                        tauri::async_runtime::spawn(async move {
                            let started = Instant::now();
                            let result = tauri::async_runtime::spawn_blocking(move || probe(p))
                                .await
                                .unwrap_or_else(|e| Err(format!("the check did not finish ({e})")));
                            if current_port(&h) != Some(p) {
                                return; // the sidecar stopped meanwhile; its own failure is showing
                            }
                            match result {
                                Ok(()) => {
                                    let ms = started.elapsed().as_millis();
                                    shell_log(&lp, &format!("server answered on 127.0.0.1:{p} after {ms} ms; opening it"));
                                    if let Ok(url) = format!("http://127.0.0.1:{p}/").parse() {
                                        let _ = w.navigate(url);
                                    }
                                }
                                Err(e) => fail_main(&h, vec![
                                    format!("Muse started, but the window could not connect to it on 127.0.0.1:{p} ({e})."),
                                    "Security software, a VPN or a proxy on this PC may be blocking connections to 127.0.0.1.".to_string(),
                                ]),
                            }
                        });
                    }
                }
            });

            let handle = app.handle().clone();
            std::thread::spawn(move || {
                std::thread::sleep(Duration::from_secs(90));
                let s = handle.state::<Mutex<Shell>>();
                let mut s = s.lock().unwrap();
                if s.port.is_none() && !s.failed {
                    s.failed = true;
                    push_line(&mut s, "Muse did not start within 90 seconds".to_string());
                }
            });
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building Muse");

    app.run(|handle, event| {
        let log_path = || handle.state::<Mutex<Shell>>().lock().unwrap().log_path.clone();
        match event {
            RunEvent::ExitRequested { code, .. } => {
                shell_log(&log_path(), &format!("exit requested (code {code:?})"));
            }
            RunEvent::Exit => {
                shell_log(&log_path(), "exiting");
                // drop closes the sidecar's stdin; never kill (onefile bootloader would orphan its child)
                let child = handle.state::<Mutex<Shell>>().lock().unwrap().child.take();
                drop(child);
            }
            #[cfg(target_os = "macos")]
            RunEvent::Reopen { .. } => tray::show_main(handle.clone()),
            _ => {}
        }
    });
}
