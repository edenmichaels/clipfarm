// ClipFarm desktop shell (Tauri v2).
// - Picks a free local port and spawns the Python engine sidecar with a random per-launch token.
// - Closing the window hides it to the system tray so long-running projects keep going.
// - Kills the engine on real exit (tray > Quit); the engine also exits by itself if this process dies.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::net::TcpListener;
use std::sync::Mutex;
use tauri::{
    menu::{Menu, MenuItem},
    tray::{MouseButton, TrayIconBuilder, TrayIconEvent},
    AppHandle, Manager, RunEvent, WindowEvent,
};
use tauri_plugin_shell::{
    process::{CommandChild, CommandEvent},
    ShellExt,
};

struct Backend {
    port: u16,
    token: String,
    child: Mutex<Option<CommandChild>>,
}

#[derive(serde::Serialize)]
struct BackendInfo {
    port: u16,
    token: String,
}

#[tauri::command]
fn backend_info(state: tauri::State<Backend>) -> BackendInfo {
    BackendInfo { port: state.port, token: state.token.clone() }
}

/// Ask the OS for a free port; fall back to the default if that fails.
fn free_port() -> u16 {
    TcpListener::bind("127.0.0.1:0")
        .and_then(|l| l.local_addr())
        .map(|a| a.port())
        .unwrap_or(47821)
}

fn show_main(app: &AppHandle) {
    if let Some(w) = app.get_webview_window("main") {
        let _ = w.unminimize();
        let _ = w.show();
        let _ = w.set_focus();
    }
}

fn main() {
    let token = uuid::Uuid::new_v4().to_string();
    let port = free_port();

    tauri::Builder::default()
        // Must be first: a second launch just focuses the running window.
        .plugin(tauri_plugin_single_instance::init(|app, _, _| show_main(app)))
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_opener::init())
        .manage(Backend { port, token: token.clone(), child: Mutex::new(None) })
        .invoke_handler(tauri::generate_handler![backend_info])
        .setup(move |app| {
            // --- start the Python engine sidecar ---
            let (mut rx, child) = app
                .shell()
                .sidecar("clipfarm-engine")?
                .env("CLIPFARM_TOKEN", &token)
                .env("CLIPFARM_PORT", port.to_string())
                .env("CLIPFARM_PARENT_PID", std::process::id().to_string())
                .spawn()?;
            *app.state::<Backend>().child.lock().unwrap() = Some(child);
            tauri::async_runtime::spawn(async move {
                // Drain output so the pipe never fills up and blocks the engine.
                while let Some(ev) = rx.recv().await {
                    if let CommandEvent::Stderr(line) = ev {
                        #[cfg(debug_assertions)]
                        eprintln!("[engine] {}", String::from_utf8_lossy(&line));
                        #[cfg(not(debug_assertions))]
                        let _ = line;
                    }
                }
            });

            // --- tray icon: Open / Quit ---
            let open = MenuItem::with_id(app, "open", "Open ClipFarm", true, None::<&str>)?;
            let quit = MenuItem::with_id(app, "quit", "Quit (stops all projects)", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&open, &quit])?;
            let mut tray = TrayIconBuilder::new()
                .tooltip("ClipFarm is running")
                .menu(&menu)
                .on_menu_event(|app, e| match e.id.as_ref() {
                    "open" => show_main(app),
                    "quit" => app.exit(0),
                    _ => {}
                })
                .on_tray_icon_event(|tray, e| {
                    if let TrayIconEvent::Click { button: MouseButton::Left, .. } = e {
                        show_main(tray.app_handle());
                    }
                });
            if let Some(icon) = app.default_window_icon() {
                tray = tray.icon(icon.clone());
            }
            tray.build(app)?;
            Ok(())
        })
        .on_window_event(|window, event| {
            // Close button = hide to tray, so jobs keep running.
            if let WindowEvent::CloseRequested { api, .. } = event {
                let _ = window.hide();
                api.prevent_close();
            }
        })
        .build(tauri::generate_context!())
        .expect("error while building ClipFarm")
        .run(|app, event| {
            if let RunEvent::Exit = event {
                let child = app.state::<Backend>().child.lock().unwrap().take();
                if let Some(child) = child {
                    let _ = child.kill();
                }
            }
        });
}
