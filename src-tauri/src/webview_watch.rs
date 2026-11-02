//! Windows only: the WebView2 events Tauri does not surface. Neither Tauri nor wry handles a
//! failed navigation or a WebView2 process crash, so without this a window just stays white and
//! nothing reaches muse.log.
use std::path::PathBuf;
use std::sync::atomic::{AtomicU32, Ordering};
use std::sync::Arc;

use tauri::{AppHandle, Manager, WebviewWindow};
use tauri_plugin_dialog::DialogExt;
use webview2_com::Microsoft::Web::WebView2::Win32::*;
use webview2_com::{take_pwstr, NavigationCompletedEventHandler, ProcessFailedEventHandler};
use windows_core::{Interface, BOOL, PWSTR};

use crate::{fail_main, shell_log};

/// Render-process reloads before the main window gives up and shows the failure page.
const MAX_RELOADS: u32 = 3;

pub fn watch(window: &WebviewWindow, app: AppHandle, log_path: PathBuf) {
    let label = window.label().to_string();
    let reloads = Arc::new(AtomicU32::new(0));
    let err_log = log_path.clone();
    let result = window.with_webview(move |wv| {
        let core = match unsafe { wv.controller().CoreWebView2() } {
            Ok(core) => core,
            Err(e) => {
                shell_log(&log_path, &format!("window {label}: no WebView2 core ({e})"));
                return;
            }
        };

        let (a, l, lp) = (app.clone(), label.clone(), log_path.clone());
        let nav = NavigationCompletedEventHandler::create(Box::new(move |webview, args| {
            let (Some(webview), Some(args)) = (webview, args) else { return Ok(()) };
            let mut ok = BOOL::default();
            unsafe { args.IsSuccess(&mut ok)? };
            if ok.as_bool() {
                return Ok(());
            }
            let mut status = COREWEBVIEW2_WEB_ERROR_STATUS::default();
            unsafe { args.WebErrorStatus(&mut status)? };
            // Superseded by a newer navigation (or refused by on_navigation): not a failure.
            if status == COREWEBVIEW2_WEB_ERROR_STATUS_OPERATION_CANCELED {
                return Ok(());
            }
            let mut uri = PWSTR::null();
            unsafe { webview.Source(&mut uri)? };
            let uri = take_pwstr(uri);
            let why = web_error_name(status.0);
            shell_log(&lp, &format!("window {l} could not load {uri}: {why} ({})", status.0));
            if uri.contains("tauri.localhost") {
                return Ok(()); // the boot page itself: nothing better to show
            }
            if l == "main" {
                fail_main(&a, vec![
                    format!("The window could not load Muse: {why}."),
                    "Muse itself is running. Security software, a VPN or a proxy on this PC may be blocking connections to 127.0.0.1.".to_string(),
                ]);
            } else if let Some(w) = a.get_webview_window(&l) {
                let _ = w.close();
            }
            Ok(())
        }));
        let mut token = 0i64;
        if let Err(e) = unsafe { core.add_NavigationCompleted(&nav, &mut token) } {
            shell_log(&log_path, &format!("window {label}: cannot watch navigation ({e})"));
        }

        let (a, l, lp, reloads) = (app.clone(), label.clone(), log_path.clone(), reloads.clone());
        let failed = ProcessFailedEventHandler::create(Box::new(move |webview, args| {
            let Some(args) = args else { return Ok(()) };
            let mut kind = COREWEBVIEW2_PROCESS_FAILED_KIND::default();
            unsafe { args.ProcessFailedKind(&mut kind)? };
            let detail = args
                .cast::<ICoreWebView2ProcessFailedEventArgs2>()
                .map(|a2| {
                    let mut reason = COREWEBVIEW2_PROCESS_FAILED_REASON::default();
                    let mut code = 0i32;
                    unsafe {
                        let _ = a2.Reason(&mut reason);
                        let _ = a2.ExitCode(&mut code);
                    }
                    format!(", reason {}, exit code {code:#x}", reason.0)
                })
                .unwrap_or_default();
            shell_log(&lp, &format!("window {l}: WebView2 process failed (kind {}{detail})", kind.0));

            if kind == COREWEBVIEW2_PROCESS_FAILED_KIND_BROWSER_PROCESS_EXITED {
                // The whole WebView2 instance is gone; no window of ours can draw again.
                let a2 = a.clone();
                a.dialog()
                    .message(format!(
                        "Muse's window stopped working (WebView2 closed{detail}). Open Muse again. If this keeps happening, send the log at {}.",
                        lp.display()
                    ))
                    .title("Muse")
                    .show(move |_| a2.exit(1));
            } else if kind == COREWEBVIEW2_PROCESS_FAILED_KIND_RENDER_PROCESS_EXITED
                || kind == COREWEBVIEW2_PROCESS_FAILED_KIND_RENDER_PROCESS_UNRESPONSIVE
                || kind == COREWEBVIEW2_PROCESS_FAILED_KIND_FRAME_RENDER_PROCESS_EXITED
            {
                if reloads.fetch_add(1, Ordering::SeqCst) < MAX_RELOADS {
                    if let Some(webview) = webview {
                        let _ = unsafe { webview.Reload() };
                    }
                } else if l == "main" {
                    fail_main(&a, vec![format!(
                        "The window's page crashed {} times (kind {}{detail}).",
                        MAX_RELOADS + 1,
                        kind.0
                    )]);
                }
            }
            Ok(())
        }));
        let mut token = 0i64;
        if let Err(e) = unsafe { core.add_ProcessFailed(&failed, &mut token) } {
            shell_log(&log_path, &format!("window {label}: cannot watch for crashes ({e})"));
        }
    });
    if let Err(e) = result {
        shell_log(&err_log, &format!("window: with_webview failed ({e})"));
    }
}

fn web_error_name(status: i32) -> &'static str {
    match status {
        6 => "the server is unreachable",
        7 => "the connection timed out",
        8 => "the server sent an invalid reply",
        9 => "the connection was aborted",
        10 => "the connection was reset",
        11 => "the network is disconnected",
        12 => "it cannot connect",
        13 => "the host name was not resolved",
        15 => "a redirect failed",
        17 | 18 => "a proxy or server asked for credentials",
        _ => "an unexpected error",
    }
}
