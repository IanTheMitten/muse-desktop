use std::sync::Mutex;

use tauri::menu::{Menu, MenuItem, PredefinedMenuItem};
use tauri::tray::TrayIconBuilder;
use tauri::{AppHandle, Emitter, Manager, WebviewUrl, WebviewWindowBuilder};

use crate::Shell;

pub fn build(app: &AppHandle) -> tauri::Result<()> {
    let toggle = MenuItem::with_id(app, "toggle", "Play/Pause", true, None::<&str>)?;
    let next = MenuItem::with_id(app, "next", "Next", true, None::<&str>)?;
    let mini = MenuItem::with_id(app, "mini", "Mini player", true, None::<&str>)?;
    let show = MenuItem::with_id(app, "show", "Show Muse", true, None::<&str>)?;
    let quit = MenuItem::with_id(app, "quit", "Quit Muse", true, None::<&str>)?;
    let sep = PredefinedMenuItem::separator(app)?;
    let menu = Menu::with_items(app, &[&toggle, &next, &mini, &show, &sep, &quit])?;

    let builder = TrayIconBuilder::with_id("muse")
        .tooltip("Muse")
        .menu(&menu)
        .show_menu_on_left_click(true)
        .on_menu_event(|app, event| match event.id().as_ref() {
            id @ ("toggle" | "next") => {
                let _ = app.emit_to("main", "muse:command", id);
            }
            "mini" => {
                let _ = open_mini(app.clone());
            }
            "show" => show_main(app.clone()),
            "quit" => app.exit(0),
            _ => {}
        });
    #[cfg(target_os = "macos")]
    let builder = builder
        .icon(tauri::image::Image::from_bytes(include_bytes!("../icons/tray/44x44.png"))?)
        .icon_as_template(true);
    #[cfg(not(target_os = "macos"))]
    let builder = match app.default_window_icon() {
        Some(icon) => builder.icon(icon.clone()),
        None => builder,
    };
    builder.build(app)?;
    Ok(())
}

#[tauri::command]
pub fn show_main(app: AppHandle) {
    if let Some(w) = app.get_webview_window("main") {
        let _ = w.show();
        let _ = w.unminimize();
        let _ = w.set_focus();
    }
}

#[tauri::command]
pub fn open_mini(app: AppHandle) -> Result<(), String> {
    if let Some(w) = app.get_webview_window("mini") {
        let _ = w.show();
        let _ = w.set_focus();
        return Ok(());
    }
    let (port, token) = {
        let s = app.state::<Mutex<Shell>>();
        let s = s.lock().unwrap();
        (s.port, s.token.clone())
    };
    let port = port.ok_or("Muse is still starting")?;
    let url = format!("http://127.0.0.1:{port}/mini").parse().map_err(|e| format!("{e}"))?;
    WebviewWindowBuilder::new(&app, "mini", WebviewUrl::External(url))
        .title("Muse")
        .inner_size(400.0, 96.0)
        .resizable(false)
        .decorations(false)
        .always_on_top(true)
        .skip_taskbar(true)
        .initialization_script(format!("window.__MUSE_TOKEN__ = \"{token}\";"))
        .on_navigation(|url| crate::allowed_origin(url))
        .build()
        .map_err(|e| e.to_string())?;
    Ok(())
}
