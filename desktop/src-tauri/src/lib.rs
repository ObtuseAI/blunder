use rand::RngExt;
use reqwest::blocking::Client;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::env;
use std::io::{BufRead, BufReader, Read, Write};
use std::mem::size_of;
use std::os::windows::io::AsRawHandle;
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::Duration;
use tauri::{AppHandle, Emitter, Manager, State};
use windows_sys::Win32::Foundation::{CloseHandle, HANDLE};
use windows_sys::Win32::System::JobObjects::{
    AssignProcessToJobObject, CreateJobObjectW, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
    JOBOBJECT_EXTENDED_LIMIT_INFORMATION, JobObjectExtendedLimitInformation,
    SetInformationJobObject,
};

const MAX_REQUEST_PATH: usize = 2048;
const MAX_REQUEST_BODY: usize = 16_384;

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct CoreReady {
    schema: String,
    status: String,
    host: String,
    port: u16,
    pid: u32,
    launcher_pid: u32,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct CoreRequest {
    method: String,
    path: String,
    body: Option<Value>,
}

#[derive(Debug, Serialize)]
struct CoreResponse {
    status: u16,
    body: Value,
}

struct CoreProcess {
    child: Mutex<Child>,
    _job: OwnedJob,
    endpoint: String,
    token: String,
    client: Client,
}

struct OwnedChildGuard {
    child: Option<Child>,
    job: Option<OwnedJob>,
}

impl OwnedChildGuard {
    fn new(mut child: Child) -> Result<Self, String> {
        match OwnedJob::attach(&child) {
            Ok(job) => Ok(Self {
                child: Some(child),
                job: Some(job),
            }),
            Err(error) => {
                let _ = child.kill();
                let _ = child.wait();
                Err(error)
            }
        }
    }

    fn child_mut(&mut self) -> Result<&mut Child, String> {
        self.child
            .as_mut()
            .ok_or("Owned sidecar process is no longer available.".to_string())
    }

    fn take(&mut self) -> Result<(Child, OwnedJob), String> {
        let child = self
            .child
            .take()
            .ok_or("Owned sidecar process is no longer available.".to_string())?;
        let job = self
            .job
            .take()
            .ok_or("Owned sidecar job is no longer available.".to_string())?;
        Ok((child, job))
    }
}

struct OwnedJob {
    handle: isize,
}

impl OwnedJob {
    fn attach(child: &Child) -> Result<Self, String> {
        unsafe {
            let handle = CreateJobObjectW(std::ptr::null(), std::ptr::null());
            if handle.is_null() {
                return Err(format!(
                    "Failed to create the Blunder sidecar job: {}",
                    std::io::Error::last_os_error()
                ));
            }
            let mut limits = JOBOBJECT_EXTENDED_LIMIT_INFORMATION::default();
            limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
            let configured = SetInformationJobObject(
                handle,
                JobObjectExtendedLimitInformation,
                &limits as *const JOBOBJECT_EXTENDED_LIMIT_INFORMATION as *const _,
                size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
            );
            let assigned = configured != 0
                && AssignProcessToJobObject(handle, child.as_raw_handle() as HANDLE) != 0;
            if !assigned {
                let error = std::io::Error::last_os_error();
                CloseHandle(handle);
                return Err(format!(
                    "Failed to bind the Blunder sidecar to its owned Windows job: {error}"
                ));
            }
            Ok(Self {
                handle: handle as isize,
            })
        }
    }
}

impl Drop for OwnedJob {
    fn drop(&mut self) {
        unsafe {
            let _ = CloseHandle(self.handle as HANDLE);
        }
    }
}

impl Drop for OwnedChildGuard {
    fn drop(&mut self) {
        if let Some(child) = self.child.as_mut() {
            let _ = child.kill();
            let _ = child.wait();
        }
    }
}

impl Drop for CoreProcess {
    fn drop(&mut self) {
        if let Ok(child) = self.child.get_mut() {
            let _ = child.kill();
            let _ = child.wait();
        }
    }
}

fn required_environment(name: &str) -> Result<String, String> {
    env::var(name)
        .map_err(|_| format!("Required Blunder desktop environment variable is missing: {name}"))
}

fn session_token() -> String {
    let mut bytes = [0_u8; 48];
    rand::rng().fill(&mut bytes);
    bytes.iter().map(|byte| format!("{byte:02x}")).collect()
}

fn core_executable() -> Result<PathBuf, String> {
    if cfg!(debug_assertions) {
        return Ok(PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("binaries")
            .join("blunder-core-x86_64-pc-windows-msvc.exe"));
    }
    let executable = env::current_exe()
        .map_err(|error| format!("Failed to resolve the Blunder desktop executable: {error}"))?;
    let parent = executable
        .parent()
        .ok_or("Blunder desktop executable has no parent directory.".to_string())?;
    Ok(parent.join("blunder-core.exe"))
}

fn repository_root() -> Result<PathBuf, String> {
    if cfg!(debug_assertions) {
        let root = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("..")
            .join("..");
        if !root.is_dir() {
            return Err(format!(
                "The debug Blunder repository root does not exist: {}",
                root.display()
            ));
        }
        return Ok(root);
    }
    required_environment("BLUNDER_REPOSITORY_ROOT").map(PathBuf::from)
}

fn start_core() -> Result<CoreProcess, String> {
    let repository_root = repository_root()?;
    let executable = core_executable()?;
    if !executable.is_file() {
        return Err(format!(
            "The packaged Blunder core sidecar is missing: {}",
            executable.display()
        ));
    }
    let token = session_token();
    let root_argument = repository_root
        .to_str()
        .ok_or("Blunder repository path is not valid UTF-8.".to_string())?;
    let child = Command::new(executable)
        .args(["--root", root_argument, "--token-stdin", "--port", "0"])
        .current_dir(&repository_root)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|error| format!("Failed to start the Blunder Python core: {error}"))?;
    let mut owned_child = OwnedChildGuard::new(child)?;
    let child_id = owned_child.child_mut()?.id();
    let stdin = owned_child
        .child_mut()?
        .stdin
        .as_mut()
        .ok_or("Blunder core stdin was not captured.".to_string())?;
    stdin
        .write_all(format!("{token}\n").as_bytes())
        .and_then(|_| stdin.flush())
        .map_err(|error| format!("Failed to send the process-bound core token: {error}"))?;
    let stdout = owned_child
        .child_mut()?
        .stdout
        .take()
        .ok_or("Blunder core stdout was not captured.".to_string())?;
    let stderr = owned_child
        .child_mut()?
        .stderr
        .take()
        .ok_or("Blunder core stderr was not captured.".to_string())?;
    let mut reader = BufReader::new(stdout);
    let mut error_reader = BufReader::new(stderr);
    let mut ready_line = String::new();
    reader
        .read_line(&mut ready_line)
        .map_err(|error| format!("Failed to read Blunder core readiness: {error}"))?;
    if ready_line.trim().is_empty() {
        let mut error_text = String::new();
        error_reader
            .read_to_string(&mut error_text)
            .map_err(|error| format!("Failed to read Blunder core startup error: {error}"))?;
        return Err(format!(
            "Blunder core returned no readiness record. stderr={}",
            error_text.trim()
        ));
    }
    let ready: CoreReady = serde_json::from_str(ready_line.trim())
        .map_err(|error| format!("Blunder core returned invalid readiness JSON: {error}"))?;
    if ready.schema != "obtuse.blunder.core-ready.v1"
        || ready.status != "READY"
        || ready.host != "127.0.0.1"
    {
        return Err("Blunder core readiness identity or loopback boundary is invalid.".to_string());
    }
    if ready.launcher_pid != child_id || ready.pid == 0 {
        return Err(
            "Blunder core readiness launcher identity does not match the owned sidecar process."
                .to_string(),
        );
    }
    let (child, job) = owned_child.take()?;
    Ok(CoreProcess {
        child: Mutex::new(child),
        _job: job,
        endpoint: format!("http://{}:{}", ready.host, ready.port),
        token,
        client: Client::builder()
            .timeout(Duration::from_secs(30))
            .build()
            .map_err(|error| format!("Failed to construct the local core client: {error}"))?,
    })
}

fn validate_request(request: &CoreRequest) -> Result<(), String> {
    if request.method != "GET" && request.method != "POST" {
        return Err("Desktop core requests support only GET and POST.".to_string());
    }
    if !request.path.starts_with("/api/v1/")
        || request.path.len() > MAX_REQUEST_PATH
        || request.path.contains("..")
    {
        return Err("Desktop core request path is outside the versioned local API.".to_string());
    }
    if let Some(body) = &request.body {
        let size = serde_json::to_vec(body)
            .map_err(|error| format!("Desktop core request body cannot be encoded: {error}"))?
            .len();
        if size > MAX_REQUEST_BODY {
            return Err("Desktop core request body exceeds the bounded service limit.".to_string());
        }
    }
    Ok(())
}

#[tauri::command]
fn core_request(
    request: CoreRequest,
    state: State<'_, Arc<CoreProcess>>,
) -> Result<CoreResponse, String> {
    validate_request(&request)?;
    let url = format!("{}{}", state.endpoint, request.path);
    let builder = match request.method.as_str() {
        "GET" => state.client.get(url),
        "POST" => state
            .client
            .post(url)
            .json(&request.body.unwrap_or(Value::Object(Default::default()))),
        _ => return Err("Unsupported core request method.".to_string()),
    }
    .header("X-Blunder-Token", &state.token);
    let response = builder
        .send()
        .map_err(|error| format!("Blunder local core request failed: {error}"))?;
    let status = response.status().as_u16();
    let body = response
        .json::<Value>()
        .map_err(|error| format!("Blunder local core response is not valid JSON: {error}"))?;
    Ok(CoreResponse { status, body })
}

fn bridge_events(app: AppHandle, core: Arc<CoreProcess>) {
    thread::spawn(move || {
        let mut cursor = 0_u64;
        loop {
            let url = format!(
                "{}/api/v1/events/stream?after={cursor}&limit=200",
                core.endpoint
            );
            let response = core
                .client
                .get(url)
                .header("X-Blunder-Token", &core.token)
                .send();
            match response {
                Ok(value) => match value.text() {
                    Ok(text) => {
                        for line in text.lines().filter(|line| line.starts_with("data: ")) {
                            let payload = &line[6..];
                            if let Ok(event) = serde_json::from_str::<Value>(payload) {
                                if let Some(sequence) =
                                    event.get("sequence").and_then(Value::as_u64)
                                {
                                    cursor = cursor.max(sequence);
                                    let _ = app.emit("task-event", event);
                                }
                            }
                        }
                    }
                    Err(error) => {
                        let _ = app.emit(
                            "core-error",
                            format!("Task event stream read failed: {error}"),
                        );
                    }
                },
                Err(error) => {
                    let _ = app.emit(
                        "core-error",
                        format!("Task event stream request failed: {error}"),
                    );
                }
            }
            thread::sleep(Duration::from_secs(1));
        }
    });
}

pub fn run() -> Result<(), String> {
    let core = Arc::new(start_core()?);
    let managed_core = Arc::clone(&core);
    tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(
            |app, _arguments, _directory| {
                if let Some(window) = app.get_webview_window("main") {
                    let _ = window.set_focus();
                }
            },
        ))
        .manage(managed_core)
        .invoke_handler(tauri::generate_handler![core_request])
        .setup(move |app| {
            bridge_events(app.handle().clone(), Arc::clone(&core));
            Ok(())
        })
        .run(tauri::generate_context!())
        .map_err(|error| format!("Blunder desktop runtime failed: {error}"))?;
    Ok(())
}
