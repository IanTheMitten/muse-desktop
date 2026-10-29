// release builds on Windows get no console window
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]
use std::collections::{HashMap, VecDeque};
use std::fs::OpenOptions;
use std::io::Write;
use std::path::PathBuf;
use std::sync::Mutex;
use std::time::Duration;

mod tray;

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
        .invoke_handler(tauri::generate_handler![boot_status, smoke_report, tray::open_mini, tray::show_main])
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

            let mut bytes = [0u8; 32];
            getrandom::fill(&mut bytes).map_err(|e| format!("getrandom: {e}"))?;
            let token: String = bytes.iter().map(|b| format!("{b:02x}")).collect();

            let mut script = format!("window.__MUSE_TOKEN__ = \"{token}\";");
            if std::env::var_os("MUSE_SMOKE_REPORT").is_some() {
                script.push_str(include_str!("smoke.js"));
            }

            let nav_handle = app.handle().clone();
            let window = WebviewWindowBuilder::new(app, "main", WebviewUrl::App("index.html".into()))
                .title("Muse")
                .inner_size(1200.0, 800.0)
                .min_inner_size(360.0, 560.0)
                .initialization_script(script)
                .on_navigation(move |url| allowed_origin(url, current_port(&nav_handle)))
                .build()?;

            let mut env = HashMap::new();
            env.insert("MUSE_DATA_DIR".to_string(), data_dir.to_string_lossy().into_owned());
            env.insert("MUSE_TOKEN".to_string(), token.clone());
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
                                let boot = if cfg!(windows) { "http://tauri.localhost/index.html" } else { "tauri://localhost/index.html" };
                                if let Ok(url) = boot.parse() {
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
                        if let Ok(url) = format!("http://127.0.0.1:{p}/").parse() {
                            let _ = window.navigate(url);
                        }
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
        match event {
            RunEvent::Exit => {
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
