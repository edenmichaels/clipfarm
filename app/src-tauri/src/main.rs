// ClipFarm desktop shell (Tauri v2).
// - Picks a free local port and spawns the Python engine sidecar with a random per-launch token.
// - Watchdog: if the engine crashes, it is restarted on the same port/token (the UI shows
//   "Reconnecting" meanwhile). More than 5 crashes in 2 minutes = give up and tell the UI.
// - Passes the install (resource) folder so the engine finds bundled FFmpeg and fonts.
// - Closing the window does what you chose in Settings › General: keep running in the tray (default; the tray
//   menu has Open, Pause/Resume engine and Quit) or quit. You're told once where it went.
// - Quit stops the engine properly: it asks the engine to stop its child processes (ffmpeg), then kills the
//   engine's whole process tree. The engine also exits by itself if this process dies, and on Windows its
//   children are in a Job Object that dies with it.
// - Only one copy runs (single-instance plugin); the engine also holds a lock so only one job loop runs.
// - Secondary windows (the Campaigns browser) only open when you click something in the visible main window,
//   and close when the main window goes to the tray.
// - Auto-updater: new versions install over the top, so uninstalling is never needed to update.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod campaign_browser;

use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream};
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Mutex;
use std::time::{Duration, Instant};
use tauri::{
    menu::{Menu, MenuItem},
    tray::{MouseButton, TrayIconBuilder, TrayIconEvent},
    AppHandle, Emitter, Manager, RunEvent, WindowEvent, Wry,
};
use tauri_plugin_notification::NotificationExt;
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
    paused: AtomicBool,
    pause_item: Mutex<Option<MenuItem<Wry>>>,
}

/// What the window's close button does. Saved in the app's config folder (close.json).
#[derive(Clone, serde::Serialize, serde::Deserialize)]
struct CloseSettings {
    action: String, // "tray" | "quit"
    told: bool,     // we've shown "ClipFarm is still running in the tray" once
}

impl Default for CloseSettings {
    fn default() -> Self {
        CloseSettings { action: "tray".into(), told: false }
    }
}

fn close_file(app: &AppHandle) -> Option<PathBuf> {
    app.path().app_config_dir().ok().map(|d| d.join("close.json"))
}

fn load_close(app: &AppHandle) -> CloseSettings {
    close_file(app)
        .and_then(|p| std::fs::read_to_string(p).ok())
        .and_then(|s| serde_json::from_str(&s).ok())
        .unwrap_or_default()
}

fn save_close(app: &AppHandle, c: &CloseSettings) {
    if let Some(p) = close_file(app) {
        if let Some(d) = p.parent() {
            let _ = std::fs::create_dir_all(d);
        }
        let _ = std::fs::write(p, serde_json::to_string(c).unwrap_or_default());
    }
}

#[tauri::command]
fn get_close_action(app: AppHandle) -> String {
    load_close(&app).action
}

#[tauri::command]
fn set_close_action(app: AppHandle, action: String) -> Result<(), String> {
    if action != "tray" && action != "quit" {
        return Err("action is tray or quit".into());
    }
    let mut c = load_close(&app);
    c.action = action;
    c.told = true;
    save_close(&app, &c);
    Ok(())
}

/// POST to the engine's local API (pause, resume, shutdown). Plain HTTP on 127.0.0.1 with the per-launch token.
fn engine_post(port: u16, token: &str, path: &str) -> Option<String> {
    let addr = std::net::SocketAddr::from(([127, 0, 0, 1], port));
    let mut s = TcpStream::connect_timeout(&addr, Duration::from_secs(2)).ok()?;
    let _ = s.set_read_timeout(Some(Duration::from_secs(5)));
    let req = format!(
        "POST {} HTTP/1.1\r\nHost: 127.0.0.1:{}\r\nX-ClipFarm-Token: {}\r\nContent-Type: application/json\r\nContent-Length: 2\r\nConnection: close\r\n\r\n{{}}",
        path, port, token
    );
    s.write_all(req.as_bytes()).ok()?;
    let mut out = String::new();
    let _ = s.read_to_string(&mut out);
    Some(out)
}

fn update_pause_item(app: &AppHandle) {
    let st = app.state::<Backend>();
    let paused = st.paused.load(Ordering::SeqCst);
    if let Some(item) = st.pause_item.lock().unwrap().as_ref() {
        let _ = item.set_text(if paused { "Resume engine" } else { "Pause engine" });
    }
}

/// The UI tells the tray when you pause/resume from the sidebar, so both always agree.
#[tauri::command]
fn set_engine_paused(app: AppHandle, paused: bool) {
    app.state::<Backend>().paused.store(paused, Ordering::SeqCst);
    update_pause_item(&app);
}

fn toggle_pause_from_tray(app: &AppHandle) {
    let st = app.state::<Backend>();
    let paused = !st.paused.load(Ordering::SeqCst);
    let path = if paused { "/engine/pause" } else { "/engine/resume" };
    if engine_post(st.port, &st.token, path).is_some() {
        st.paused.store(paused, Ordering::SeqCst);
        update_pause_item(app);
        let _ = app.emit("engine-paused", paused);
    }
}

/// Quit for real: stop the engine's child processes, stop the engine (whole process tree), exit.
#[tauri::command]
fn quit_app(app: AppHandle) {
    stop_engine(&app);
    app.exit(0);
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
    if st.exiting.swap(true, Ordering::SeqCst) {
        return; // already stopping
    }
    // Ask the engine to stop its ffmpeg/other children and exit by itself...
    let _ = engine_post(st.port, &st.token, "/engine/shutdown");
    std::thread::sleep(Duration::from_millis(600));
    let child = st.child.lock().unwrap().take();
    if let Some(child) = child {
        // ...then make sure: kill the engine and everything it started.
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            const CREATE_NO_WINDOW: u32 = 0x0800_0000;
            let _ = std::process::Command::new("taskkill")
                .args(["/PID", &child.pid().to_string(), "/T", "/F"])
                .creation_flags(CREATE_NO_WINDOW)
                .stdin(std::process::Stdio::null())
                .stdout(std::process::Stdio::null())
                .stderr(std::process::Stdio::null())
                .status();
        }
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
            paused: AtomicBool::new(false),
            pause_item: Mutex::new(None),
        })
        .manage(campaign_browser::Pending::default())
        .invoke_handler(tauri::generate_handler![
            backend_info,
            updates_enabled,
            get_close_action,
            set_close_action,
            set_engine_paused,
            quit_app,
            campaign_browser::open_campaign_browser,
            campaign_browser::campaign_browser_status
        ])
        .setup(move |app| {
            // --- start the Python engine sidecar (with watchdog) ---
            spawn_engine(app.handle())?;

            // --- tray icon: Open / Pause engine / Quit ---
            let open = MenuItem::with_id(app, "open", "Open ClipFarm", true, None::<&str>)?;
            let pause = MenuItem::with_id(app, "pause", "Pause engine", true, None::<&str>)?;
            let quit = MenuItem::with_id(app, "quit", "Quit ClipFarm (stops all processing)", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&open, &pause, &quit])?;
            *app.state::<Backend>().pause_item.lock().unwrap() = Some(pause.clone());
            let mut tray = TrayIconBuilder::new()
                .tooltip("ClipFarm is running")
                .menu(&menu)
                .on_menu_event(|app, e| match e.id.as_ref() {
                    "open" => show_main(app),
                    "pause" => toggle_pause_from_tray(app),
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
            // Main window's close button: what you chose in Settings › General (tray by default, or quit).
            // The campaign browser closes normally (its sign-in is kept in the WebView2 profile).
            if let WindowEvent::CloseRequested { api, .. } = event {
                if window.label() != "main" {
                    return;
                }
                api.prevent_close();
                let app = window.app_handle().clone();
                let mut c = load_close(&app);
                if c.action == "quit" {
                    stop_engine(&app);
                    app.exit(0);
                    return;
                }
                let _ = window.hide();
                // Don't leave secondary windows floating around while ClipFarm is in the tray.
                if let Some(w) = app.get_webview_window(campaign_browser::LABEL) {
                    let _ = w.close();
                }
                if !c.told {
                    c.told = true;
                    save_close(&app, &c);
                    let _ = app
                        .notification()
                        .builder()
                        .title("ClipFarm is still running")
                        .body("It's in the system tray. Right-click the icon to pause the engine or quit. You can make the close button quit instead in Settings › General.")
                        .show();
                }
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
