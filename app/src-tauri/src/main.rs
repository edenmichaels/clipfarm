// ClipFarm desktop shell (Tauri v2).
// - Picks a free local port and spawns the Python engine sidecar with a random per-launch token.
// - Watchdog: if the engine crashes, it is restarted on the same port/token (the UI shows
//   "Reconnecting" meanwhile). More than 5 crashes in 2 minutes = give up and tell the UI.
// - Passes the install (resource) folder so the engine finds bundled FFmpeg and fonts.
// - Closing the window hides it to the system tray so long-running projects keep going.
// - Kills the engine on real exit (tray > Quit); the engine also exits by itself if this process dies.
// - Auto-updater: new versions install over the top, so uninstalling is never needed to update.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::net::TcpListener;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Mutex;
use std::time::{Duration, Instant};
use tauri::{
    menu::{Menu, MenuItem},
    tray::{MouseButton, TrayIconBuilder, TrayIconEvent},
    AppHandle, Emitter, Manager, RunEvent, WindowEvent,
};
use tauri_plugin_shell::{
    process::{CommandChild, CommandEvent},
    ShellExt,
};

struct Backend {
    port: u16,
    token: String,
    child: Mutex<Option<CommandChild>>,
    exiting: AtomicBool,
    crashes: Mutex<Vec<Instant>>,
}

#[derive(serde::Serialize)]
struct BackendInfo {
    port: u16,
    token: String,
}

#[derive(Clone, serde::Serialize)]
struct EngineStatus {
    state: String,
    message: String,
}

#[tauri::command]
fn backend_info(state: tauri::State<Backend>) -> BackendInfo {
    BackendInfo { port: state.port, token: state.token.clone() }
}

/// True when this build was signed for auto-updates (the updater public key is set).
#[tauri::command]
fn updates_enabled(app: AppHandle) -> bool {
    app.config()
        .plugins
        .0
        .get("updater")
        .and_then(|v| v.get("pubkey"))
        .and_then(|v| v.as_str())
        .map(|s| !s.trim().is_empty())
        .unwrap_or(false)
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

fn status(app: &AppHandle, state: &str, message: String) {
    let _ = app.emit("engine-status", EngineStatus { state: state.to_string(), message });
}

/// Start the engine and watch it. On an unexpected exit, restart it with back-off.
fn spawn_engine(app: &AppHandle) -> Result<(), Box<dyn std::error::Error>> {
    let backend = app.state::<Backend>();
    let resource_dir = app
        .path()
        .resource_dir()
        .map(|p| p.to_string_lossy().to_string())
        .unwrap_or_default();
    let (mut rx, child) = app
        .shell()
        .sidecar("clipfarm-engine")?
        .env("CLIPFARM_TOKEN", &backend.token)
        .env("CLIPFARM_PORT", backend.port.to_string())
        .env("CLIPFARM_PARENT_PID", std::process::id().to_string())
        .env("CLIPFARM_RESOURCE_DIR", resource_dir)
        .spawn()?;
    *backend.child.lock().unwrap() = Some(child);

    let app2 = app.clone();
    tauri::async_runtime::spawn(async move {
        // Drain output so the pipe never fills up and blocks the engine.
        while let Some(ev) = rx.recv().await {
            match ev {
                CommandEvent::Stderr(line) => {
                    #[cfg(debug_assertions)]
                    eprintln!("[engine] {}", String::from_utf8_lossy(&line));
                    #[cfg(not(debug_assertions))]
                    let _ = line;
                }
                CommandEvent::Terminated(payload) => {
                    let st = app2.state::<Backend>();
                    if st.exiting.load(Ordering::SeqCst) {
                        break;
                    }
                    let n = {
                        let mut v = st.crashes.lock().unwrap();
                        let now = Instant::now();
                        v.retain(|t| now.duration_since(*t) < Duration::from_secs(120));
                        v.push(now);
                        v.len()
                    };
                    if n > 5 {
                        status(
                            &app2,
                            "failed",
                            format!("It stopped {} times in 2 minutes. Open Setup Health or reinstall.", n),
                        );
                        break;
                    }
                    status(&app2, "restarting", format!("(engine exited with code {:?})", payload.code));
                    let wait = Duration::from_secs([1u64, 2, 5, 10, 15][(n - 1).min(4)]);
                    let _ = tauri::async_runtime::spawn_blocking(move || std::thread::sleep(wait)).await;
                    if st.exiting.load(Ordering::SeqCst) {
                        break;
                    }
                    match spawn_engine(&app2) {
                        Ok(()) => status(&app2, "running", String::new()),
                        Err(e) => status(&app2, "failed", format!("Couldn't restart the engine: {}", e)),
                    }
                    break;
                }
                _ => {}
            }
        }
    });
    Ok(())
}

fn stop_engine(app: &AppHandle) {
    let st = app.state::<Backend>();
    st.exiting.store(true, Ordering::SeqCst);
    let child = st.child.lock().unwrap().take();
    if let Some(child) = child {
        let _ = child.kill();
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
        .plugin(tauri_plugin_process::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .manage(Backend {
            port,
            token: token.clone(),
            child: Mutex::new(None),
            exiting: AtomicBool::new(false),
            crashes: Mutex::new(Vec::new()),
        })
        .invoke_handler(tauri::generate_handler![backend_info, updates_enabled])
        .setup(move |app| {
            // --- start the Python engine sidecar (with watchdog) ---
            spawn_engine(app.handle())?;

            // --- tray icon: Open / Quit ---
            let open = MenuItem::with_id(app, "open", "Open ClipFarm", true, None::<&str>)?;
            let quit = MenuItem::with_id(app, "quit", "Quit (stops all projects)", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&open, &quit])?;
            let mut tray = TrayIconBuilder::new()
                .tooltip("ClipFarm is running")
                .menu(&menu)
                .on_menu_event(|app, e| match e.id.as_ref() {
                    "open" => show_main(app),
                    "quit" => {
                        stop_engine(app);
                        app.exit(0)
                    }
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
                stop_engine(app);
            }
        });
}
