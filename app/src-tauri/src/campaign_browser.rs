// In-app campaign browser: a separate window showing contentrewards.com, signed in by the user.
//
// Rules (Whop / Content Rewards terms):
// - ClipFarm never fetches these sites in the background. This window only shows what the user browses.
// - Nothing here clicks Join / Submit or fills anything in. The injected toolbar only reads the page
//   when the user presses "Import this page".
// - The site gets NO access to ClipFarm (no Tauri IPC in this window). The toolbar hands the page over
//   by navigating to https://clipfarm.import/chunk?..., which on_navigation intercepts and cancels here,
//   then forwards to the main window, which sends it to the engine.
// - The WebView2 profile is stored in the app's data folder, so the sign-in survives restarts.
use std::collections::HashMap;
use std::sync::Mutex;
use tauri::{AppHandle, Emitter, Manager, Url, WebviewUrl, WebviewWindowBuilder};

pub const LABEL: &str = "campaign-browser";
const HOME: &str = "https://contentrewards.com/c/discover";
const SCRIPT: &str = include_str!("campaign_browser.js");

#[derive(Default)]
pub struct Pending(Mutex<HashMap<String, Vec<Option<String>>>>);

fn allowed(url: &Url) -> bool {
    let host = url.host_str().unwrap_or("").trim_start_matches("www.");
    url.scheme() == "https"
        && (host == "contentrewards.com" || host.ends_with(".contentrewards.com")
            || host == "whop.com" || host.ends_with(".whop.com"))
}

/// Open (or bring to front) the campaign browser, optionally at a campaign page ("Open to join").
#[tauri::command]
pub fn open_campaign_browser(app: AppHandle, url: Option<String>) -> Result<(), String> {
    let target: Url = url
        .as_deref()
        .unwrap_or(HOME)
        .parse()
        .map_err(|_| "That isn't a web address.".to_string())?;
    if !allowed(&target) {
        return Err("The campaign browser only opens Content Rewards and Whop pages.".into());
    }
    if let Some(w) = app.get_webview_window(LABEL) {
        w.navigate(target).map_err(|e| e.to_string())?;
        let _ = w.unminimize();
        let _ = w.show();
        let _ = w.set_focus();
        return Ok(());
    }
    let handle = app.clone();
    WebviewWindowBuilder::new(&app, LABEL, WebviewUrl::External(target))
        .title("ClipFarm – Campaigns (Content Rewards)")
        .inner_size(1180.0, 820.0)
        .min_inner_size(820.0, 600.0)
        .initialization_script(SCRIPT)
        .on_navigation(move |url| {
            if url.host_str() == Some("clipfarm.import") {
                receive_chunk(&handle, url);
                return false; // cancel: the page on screen stays as it is
            }
            // Ordinary browsing (sign-in pages included) goes ahead.
            matches!(url.scheme(), "https" | "about" | "blob" | "data")
        })
        .build()
        .map_err(|e| e.to_string())?;
    Ok(())
}

/// Show a message (e.g. the import summary) in the browser window's toolbar.
#[tauri::command]
pub fn campaign_browser_status(app: AppHandle, message: String) -> Result<(), String> {
    if let Some(w) = app.get_webview_window(LABEL) {
        let js = format!(
            "window.__clipfarmStatus && window.__clipfarmStatus({});",
            serde_json::to_string(&message).map_err(|e| e.to_string())?
        );
        w.eval(&js).map_err(|e| e.to_string())?;
    }
    Ok(())
}

fn receive_chunk(app: &AppHandle, url: &Url) {
    let q: HashMap<String, String> = url.query_pairs().into_owned().collect();
    let (Some(id), Some(i), Some(n), Some(d)) = (q.get("id"), q.get("i"), q.get("n"), q.get("d")) else {
        return;
    };
    let (Ok(i), Ok(n)) = (i.parse::<usize>(), n.parse::<usize>()) else { return };
    if n == 0 || n > 64 || i >= n || id.len() > 16 {
        return;
    }
    let state = app.state::<Pending>();
    let mut map = state.0.lock().unwrap();
    let parts = map.entry(id.clone()).or_insert_with(|| vec![None; n]);
    if parts.len() != n {
        map.remove(id);
        return;
    }
    parts[i] = Some(d.clone());
    if parts.iter().all(|p| p.is_some()) {
        let data: String = parts.iter().map(|p| p.as_deref().unwrap_or("")).collect();
        map.remove(id);
        // base64url JSON {url, title, text, links}; the main window decodes it and calls the engine.
        let _ = app.emit_to("main", "campaign-page", data);
    }
}
