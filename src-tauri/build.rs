fn main() {
    tauri_build::try_build(
        tauri_build::Attributes::new().app_manifest(
            tauri_build::AppManifest::new().commands(&["boot_status", "smoke_report", "open_mini", "show_main"]),
        ),
    )
    .expect("failed to run tauri-build")
}
