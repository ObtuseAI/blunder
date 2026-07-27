use base64::{Engine as _, engine::general_purpose::URL_SAFE_NO_PAD};
use ed25519_dalek::{Signature, Verifier, VerifyingKey};
use reqwest::blocking::Client;
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet};
use std::env;
use std::ffi::{OsString, c_void};
use std::fs;
use std::io::{BufRead, BufReader, Read};
use std::mem::size_of;
use std::os::windows::io::AsRawHandle;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::ptr::null_mut;
use std::slice;
use std::sync::{Arc, Mutex};
use std::time::Duration;
use tauri::{Manager, State};
use time::{OffsetDateTime, format_description::well_known::Rfc3339};
use windows_sys::Win32::Foundation::{CloseHandle, HANDLE};
use windows_sys::Win32::Security::Credentials::{
    CRED_TYPE_GENERIC, CREDENTIALW, CredFree, CredReadW,
};
use windows_sys::Win32::System::JobObjects::{
    AssignProcessToJobObject, CreateJobObjectW, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
    JOBOBJECT_EXTENDED_LIMIT_INFORMATION, JobObjectExtendedLimitInformation,
    SetInformationJobObject,
};

const MAX_REQUEST_PATH: usize = 2048;
const FUND_CONTROL_SNAPSHOT_PATH: &str = "/api/v1/fund/control-snapshot";
const CORE_RUNNER_CONFIG_SCHEMA: &str = "dumbmoney.core-runner-config.v1";
const CORE_READINESS_SCHEMA: &str = "dumbmoney.readiness-descriptor.v1";
const SIGNED_ENVELOPE_SCHEMA: &str = "dumbmoney.signed-envelope.v1";
const DEFAULT_CORE_CONFIG_PATH: &str = r"C:\ProgramData\DumbMoney\config\core-runner.v1.json";
const DEFAULT_PRODUCTION_DATA_ROOT: &str = r"C:\ProgramData\DumbMoney";
const DEFAULT_PRODUCTION_RELEASE_ROOT: &str = r"C:\Program Files\DumbMoney";
const MAX_LOCAL_CONFIG_BYTES: u64 = 1_048_576;
const MAX_READINESS_BYTES: u64 = 262_144;

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct FundCoreReady {
    schema: String,
    service_name: String,
    host: String,
    port: u16,
    process_id: u32,
    instance_id: String,
    readiness_path: String,
    signer_key_id: String,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct CoreCredentialTargets {
    core_signing_seed: String,
    desktop_read_token: String,
    operator_bearer_token: String,
    allocator_bearer_token: String,
    dummy_cell_bearer_token: String,
    dopey_cell_bearer_token: String,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct CoreAttachConfig {
    schema: String,
    data_root: PathBuf,
    policy_path: PathBuf,
    readiness_path: PathBuf,
    core_public_key_path: PathBuf,
    fund_lock_path: PathBuf,
    service_manifest_path: PathBuf,
    bind_port: u16,
    release_id: String,
    risk_policy_sha256: String,
    core_public_key_sha256: String,
    fund_lock_sha256: String,
    service_manifest_sha256: String,
    readiness_ttl_seconds: u16,
    processing_interval_milliseconds: u32,
    credential_targets: CoreCredentialTargets,
    role_public_keys_base64url: BTreeMap<String, Vec<String>>,
}

struct LoadedAttachConfig {
    config: CoreAttachConfig,
    file_sha256: String,
    role_public_keys_sha256: String,
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

struct CoreConnection {
    child: Option<Mutex<Child>>,
    _job: Option<OwnedJob>,
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
                    "Failed to create the DumbMoney sidecar job: {}",
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
                    "Failed to bind the DumbMoney sidecar to its owned Windows job: {error}"
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

impl Drop for CoreConnection {
    fn drop(&mut self) {
        if let Some(child) = self.child.as_mut() {
            if let Ok(child) = child.get_mut() {
                let _ = child.kill();
                let _ = child.wait();
            }
        }
    }
}

fn required_digest_environment(name: &str) -> Result<String, String> {
    let value = env::var(name)
        .map_err(|_| format!("{name} must provide the release-pinned config digest."))?;
    if !is_digest(&value) {
        return Err(format!("{name} must be a canonical SHA-256 digest."));
    }
    Ok(value)
}

fn read_bounded(path: &PathBuf, maximum: u64, context: &str) -> Result<Vec<u8>, String> {
    let metadata =
        fs::metadata(path).map_err(|_| format!("{context} is unavailable or unreadable."))?;
    if !metadata.is_file() || metadata.len() == 0 || metadata.len() > maximum {
        return Err(format!("{context} has an invalid byte length."));
    }
    fs::read(path).map_err(|_| format!("{context} is unavailable or unreadable."))
}

fn is_digest(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}

fn sha256_hex(value: &[u8]) -> String {
    format!("{:x}", Sha256::digest(value))
}

fn verify_file_digest(path: &PathBuf, expected: &str, context: &str) -> Result<(), String> {
    if !is_digest(expected) {
        return Err(format!("{context} digest is not canonical SHA-256."));
    }
    let bytes = read_bounded(path, 64 * 1024 * 1024, context)?;
    if sha256_hex(&bytes) != expected {
        return Err(format!("{context} does not match its pinned digest."));
    }
    Ok(())
}

fn valid_identifier(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 128
        && value.bytes().enumerate().all(|(index, byte)| {
            byte.is_ascii_alphanumeric() || (index > 0 && b"._:/-".contains(&byte))
        })
}

fn load_attach_config(
    path: &PathBuf,
    expected_file_sha256: &str,
) -> Result<LoadedAttachConfig, String> {
    if !path.is_absolute() {
        return Err("DumbMoney Core config path must be absolute.".to_string());
    }
    if !is_digest(expected_file_sha256) {
        return Err("DumbMoney Core config pin is not canonical SHA-256.".to_string());
    }
    let raw = read_bounded(path, MAX_LOCAL_CONFIG_BYTES, "DumbMoney Core config")?;
    let file_sha256 = sha256_hex(&raw);
    if file_sha256 != expected_file_sha256 {
        return Err("DumbMoney Core config does not match its release pin.".to_string());
    }
    let config: CoreAttachConfig = serde_json::from_slice(&raw)
        .map_err(|_| "DumbMoney Core config is not valid JSON.".to_string())?;
    if config.schema != CORE_RUNNER_CONFIG_SCHEMA
        || config.release_id.trim().is_empty()
        || !is_digest(&config.risk_policy_sha256)
        || !is_digest(&config.core_public_key_sha256)
        || !is_digest(&config.fund_lock_sha256)
        || !is_digest(&config.service_manifest_sha256)
        || !(10..=120).contains(&config.readiness_ttl_seconds)
        || !(100..=60_000).contains(&config.processing_interval_milliseconds)
    {
        return Err("DumbMoney Core config identity is invalid.".to_string());
    }
    for (path, context) in [
        (&config.data_root, "data_root"),
        (&config.policy_path, "policy_path"),
        (&config.readiness_path, "readiness_path"),
        (&config.core_public_key_path, "core_public_key_path"),
        (&config.fund_lock_path, "fund_lock_path"),
        (&config.service_manifest_path, "service_manifest_path"),
    ] {
        if !path.is_absolute() {
            return Err(format!("DumbMoney Core {context} must be absolute."));
        }
    }
    let targets = [
        &config.credential_targets.core_signing_seed,
        &config.credential_targets.desktop_read_token,
        &config.credential_targets.operator_bearer_token,
        &config.credential_targets.allocator_bearer_token,
        &config.credential_targets.dummy_cell_bearer_token,
        &config.credential_targets.dopey_cell_bearer_token,
    ];
    if targets.iter().any(|target| !valid_identifier(target))
        || targets.iter().collect::<BTreeSet<_>>().len() != targets.len()
    {
        return Err("Core credential target identifiers are invalid.".to_string());
    }
    let expected_roles: BTreeSet<&str> = [
        "operator",
        "evaluator",
        "promoter",
        "allocator",
        "research",
        "dummy_venue",
        "dopey_venue",
        "migration",
    ]
    .into_iter()
    .collect();
    if config
        .role_public_keys_base64url
        .keys()
        .map(String::as_str)
        .collect::<BTreeSet<_>>()
        != expected_roles
    {
        return Err("Core role public-key bundle is incomplete.".to_string());
    }
    for (role, keys) in &config.role_public_keys_base64url {
        let allow_empty = role == "migration";
        if (!allow_empty && keys.is_empty())
            || keys.iter().collect::<BTreeSet<_>>().len() != keys.len()
            || keys
                .iter()
                .any(|key| decode_base64url(key, "role public key").map_or(true, |v| v.len() != 32))
        {
            return Err(format!(
                "Core role public-key bundle is invalid for {role}."
            ));
        }
    }
    verify_file_digest(
        &config.policy_path,
        &config.risk_policy_sha256,
        "risk policy",
    )?;
    verify_file_digest(
        &config.core_public_key_path,
        &config.core_public_key_sha256,
        "Core public key",
    )?;
    verify_file_digest(
        &config.fund_lock_path,
        &config.fund_lock_sha256,
        "fund lock",
    )?;
    verify_file_digest(
        &config.service_manifest_path,
        &config.service_manifest_sha256,
        "service manifest",
    )?;
    let role_public_keys_sha256 = sha256_hex(
        &serde_json::to_vec(&config.role_public_keys_base64url)
            .map_err(|_| "Core role public-key bundle cannot be canonicalized.".to_string())?,
    );
    Ok(LoadedAttachConfig {
        config,
        file_sha256,
        role_public_keys_sha256,
    })
}

fn decode_base64url(value: &str, context: &str) -> Result<Vec<u8>, String> {
    if value.is_empty() || value.contains('=') {
        return Err(format!("{context} is not canonical base64url."));
    }
    let decoded = URL_SAFE_NO_PAD
        .decode(value)
        .map_err(|_| format!("{context} is not valid base64url."))?;
    if URL_SAFE_NO_PAD.encode(&decoded) != value {
        return Err(format!("{context} is not canonical base64url."));
    }
    Ok(decoded)
}

fn windows_credential(target: &str) -> Result<String, String> {
    let wide_target: Vec<u16> = target.encode_utf16().chain(std::iter::once(0)).collect();
    let mut credential: *mut CREDENTIALW = null_mut();
    let loaded = unsafe { CredReadW(wide_target.as_ptr(), CRED_TYPE_GENERIC, 0, &mut credential) };
    if loaded == 0 || credential.is_null() {
        return Err("Desktop read credential is unavailable to this Windows identity.".to_string());
    }
    let result = unsafe {
        let size = (*credential).CredentialBlobSize as usize;
        let pointer = (*credential).CredentialBlob;
        if size == 0 || size > 16_384 || pointer.is_null() {
            Err("Desktop read credential has an invalid byte length.".to_string())
        } else {
            let bytes = slice::from_raw_parts(pointer, size).to_vec();
            String::from_utf8(bytes)
                .map_err(|_| "Desktop read credential must contain UTF-8 bytes.".to_string())
        }
    };
    unsafe {
        CredFree(credential.cast::<c_void>());
    }
    let token = result?;
    if token.len() < 32 || token.len() > 512 || token.chars().any(char::is_whitespace) {
        return Err("Desktop read credential has an invalid value.".to_string());
    }
    Ok(token)
}

fn string_field<'a>(
    value: &'a Map<String, Value>,
    name: &str,
    context: &str,
) -> Result<&'a str, String> {
    value
        .get(name)
        .and_then(Value::as_str)
        .ok_or_else(|| format!("{context}.{name} must be a string."))
}

fn parse_time(value: &str, context: &str) -> Result<OffsetDateTime, String> {
    let bytes = value.as_bytes();
    let canonical_shape = (bytes.len() == 20 || bytes.len() == 27)
        && bytes.get(4) == Some(&b'-')
        && bytes.get(7) == Some(&b'-')
        && bytes.get(10) == Some(&b'T')
        && bytes.get(13) == Some(&b':')
        && bytes.get(16) == Some(&b':')
        && bytes.last() == Some(&b'Z')
        && (bytes.len() == 20
            || (bytes.get(19) == Some(&b'.') && bytes[20..26].iter().all(u8::is_ascii_digit)))
        && bytes.iter().enumerate().all(|(index, byte)| {
            matches!(index, 4 | 7 | 10 | 13 | 16)
                || index == bytes.len() - 1
                || (bytes.len() == 27 && matches!(index, 19 | 26))
                || byte.is_ascii_digit()
        });
    if !canonical_shape {
        return Err(format!(
            "{context} must use canonical UTC RFC3339 formatting with Z."
        ));
    }
    OffsetDateTime::parse(value, &Rfc3339)
        .map_err(|_| format!("{context} must be canonical RFC3339 time."))
}

#[derive(Debug)]
struct ReadinessIdentity {
    endpoint: String,
    instance_id: String,
    signer_key_id: String,
}

fn verify_readiness(loaded: &LoadedAttachConfig) -> Result<ReadinessIdentity, String> {
    let config = &loaded.config;
    let public_text = String::from_utf8(read_bounded(
        &config.core_public_key_path,
        1024,
        "Core public key",
    )?)
    .map_err(|_| "Core public key must be ASCII base64url.".to_string())?;
    let public_bytes = decode_base64url(public_text.trim(), "Core public key")?;
    let public_array: [u8; 32] = public_bytes
        .try_into()
        .map_err(|_| "Core public key must be exactly 32 bytes.".to_string())?;
    let signer_key_id = sha256_hex(&public_array);

    let raw = read_bounded(
        &config.readiness_path,
        MAX_READINESS_BYTES,
        "signed Core readiness",
    )?;
    let value: Value = serde_json::from_slice(&raw)
        .map_err(|_| "signed Core readiness is not valid JSON.".to_string())?;
    let object = value
        .as_object()
        .ok_or("signed Core readiness must be an object.".to_string())?;
    let expected_fields: BTreeSet<&str> = [
        "schema",
        "source_id",
        "source_sequence",
        "event_id",
        "correlation_id",
        "causation_id",
        "nonce",
        "not_before",
        "expires_at",
        "body_schema",
        "body_digest",
        "body",
        "signature_algorithm",
        "signer_key_id",
        "signature",
    ]
    .into_iter()
    .collect();
    let observed_fields: BTreeSet<&str> = object.keys().map(String::as_str).collect();
    if observed_fields != expected_fields
        || string_field(object, "schema", "readiness")? != SIGNED_ENVELOPE_SCHEMA
        || string_field(object, "source_id", "readiness")? != "DumbMoneyCore"
        || string_field(object, "body_schema", "readiness")? != CORE_READINESS_SCHEMA
        || string_field(object, "signature_algorithm", "readiness")? != "Ed25519"
        || string_field(object, "signer_key_id", "readiness")? != signer_key_id
    {
        return Err("signed Core readiness envelope identity is invalid.".to_string());
    }

    let body = object
        .get("body")
        .and_then(Value::as_object)
        .ok_or("signed Core readiness body must be an object.".to_string())?;
    let expected_body_fields: BTreeSet<&str> = [
        "schema",
        "service_name",
        "release_id",
        "instance_id",
        "process_id",
        "generation",
        "observed_at",
        "valid_until",
        "endpoint",
        "fund_lock_sha256",
        "service_manifest_sha256",
        "authority",
        "health",
        "capabilities",
    ]
    .into_iter()
    .collect();
    if body.keys().map(String::as_str).collect::<BTreeSet<_>>() != expected_body_fields {
        return Err("signed Core readiness body fields are invalid.".to_string());
    }
    let body_bytes = serde_json::to_vec(&Value::Object(body.clone()))
        .map_err(|_| "Core readiness body cannot be canonicalized.".to_string())?;
    if string_field(object, "body_digest", "readiness")? != sha256_hex(&body_bytes) {
        return Err("Core readiness body digest mismatch.".to_string());
    }
    let mut identity_object = object.clone();
    identity_object.remove("event_id");
    identity_object.remove("signature");
    let identity_bytes = serde_json::to_vec(&Value::Object(identity_object))
        .map_err(|_| "Core readiness identity cannot be canonicalized.".to_string())?;
    if string_field(object, "event_id", "readiness")? != sha256_hex(&identity_bytes) {
        return Err("Core readiness event identity mismatch.".to_string());
    }
    let mut signing_object = object.clone();
    signing_object.remove("signature");
    let signing_bytes = serde_json::to_vec(&Value::Object(signing_object))
        .map_err(|_| "Core readiness signature input cannot be canonicalized.".to_string())?;
    let signature_bytes = decode_base64url(
        string_field(object, "signature", "readiness")?,
        "Core readiness signature",
    )?;
    let signature = Signature::from_slice(&signature_bytes)
        .map_err(|_| "Core readiness signature must be 64 bytes.".to_string())?;
    let verifying_key = VerifyingKey::from_bytes(&public_array)
        .map_err(|_| "Core readiness public key is invalid.".to_string())?;
    verifying_key
        .verify(&signing_bytes, &signature)
        .map_err(|_| "Core readiness signature verification failed.".to_string())?;

    let now = OffsetDateTime::now_utc();
    let envelope_not_before = parse_time(
        string_field(object, "not_before", "readiness")?,
        "readiness.not_before",
    )?;
    let envelope_expires = parse_time(
        string_field(object, "expires_at", "readiness")?,
        "readiness.expires_at",
    )?;
    if now < envelope_not_before || now >= envelope_expires {
        return Err("signed Core readiness envelope is not current.".to_string());
    }
    if string_field(body, "schema", "readiness.body")? != CORE_READINESS_SCHEMA
        || string_field(body, "service_name", "readiness.body")? != "DumbMoneyCore"
        || string_field(body, "release_id", "readiness.body")? != config.release_id
        || string_field(body, "fund_lock_sha256", "readiness.body")? != config.fund_lock_sha256
        || string_field(body, "service_manifest_sha256", "readiness.body")?
            != config.service_manifest_sha256
    {
        return Err("signed Core readiness body identity is invalid.".to_string());
    }
    let observed_at = parse_time(
        string_field(body, "observed_at", "readiness.body")?,
        "readiness.body.observed_at",
    )?;
    let valid_until = parse_time(
        string_field(body, "valid_until", "readiness.body")?,
        "readiness.body.valid_until",
    )?;
    if now < observed_at
        || now >= valid_until
        || valid_until - observed_at > time::Duration::seconds(120)
        || envelope_not_before != observed_at
        || envelope_expires != valid_until
    {
        return Err("signed Core readiness body is stale or future-dated.".to_string());
    }
    let endpoint = body
        .get("endpoint")
        .and_then(Value::as_object)
        .ok_or("readiness endpoint is missing.".to_string())?;
    if string_field(endpoint, "transport", "readiness.endpoint")? != "http"
        || string_field(endpoint, "host", "readiness.endpoint")? != "127.0.0.1"
        || string_field(endpoint, "base_path", "readiness.endpoint")? != "/"
    {
        return Err("readiness endpoint is not literal IPv4 loopback HTTP.".to_string());
    }
    let port = endpoint
        .get("port")
        .and_then(Value::as_u64)
        .filter(|port| (1024..=65_535).contains(port))
        .ok_or("readiness endpoint port is invalid.".to_string())?;
    if config.bind_port != 0 && port != u64::from(config.bind_port) {
        return Err("readiness endpoint does not match the configured Core port.".to_string());
    }
    let authority = body
        .get("authority")
        .and_then(Value::as_object)
        .ok_or("readiness authority is missing.".to_string())?;
    let expected_authority_fields: BTreeSet<&str> = ["broker", "mode", "execution_enabled"]
        .into_iter()
        .collect();
    if authority
        .keys()
        .map(String::as_str)
        .collect::<BTreeSet<_>>()
        != expected_authority_fields
        || string_field(authority, "broker", "readiness.authority")? != "NONE"
        || string_field(authority, "mode", "readiness.authority")? != "OFFLINE"
        || authority.get("execution_enabled").and_then(Value::as_bool) != Some(false)
    {
        return Err("Core readiness claims forbidden broker authority.".to_string());
    }
    let health = body
        .get("health")
        .and_then(Value::as_object)
        .ok_or("readiness health is missing.".to_string())?;
    let expected_health_fields: BTreeSet<&str> = [
        "status",
        "control_status",
        "reason_codes",
        "ledger_head_sequence",
        "ledger_chain_head",
        "runner_config_sha256",
        "risk_policy_sha256",
        "core_public_key_sha256",
        "role_public_keys_sha256",
    ]
    .into_iter()
    .collect();
    if health.keys().map(String::as_str).collect::<BTreeSet<_>>() != expected_health_fields
        || string_field(health, "runner_config_sha256", "readiness.health")? != loaded.file_sha256
        || string_field(health, "risk_policy_sha256", "readiness.health")?
            != config.risk_policy_sha256
        || string_field(health, "core_public_key_sha256", "readiness.health")?
            != config.core_public_key_sha256
        || string_field(health, "role_public_keys_sha256", "readiness.health")?
            != loaded.role_public_keys_sha256
    {
        return Err("Core readiness health does not match the sealed attach inputs.".to_string());
    }
    let capabilities = body
        .get("capabilities")
        .and_then(Value::as_array)
        .ok_or("readiness capabilities are missing.".to_string())?;
    if !capabilities
        .iter()
        .any(|capability| capability.as_str() == Some("control-snapshot"))
    {
        return Err("Core readiness lacks the control-snapshot capability.".to_string());
    }
    let instance_id = string_field(body, "instance_id", "readiness.body")?.to_string();
    Ok(ReadinessIdentity {
        endpoint: format!("http://127.0.0.1:{port}"),
        instance_id,
        signer_key_id,
    })
}

fn attach_core(
    loaded: &LoadedAttachConfig,
    owned: Option<(Child, OwnedJob)>,
) -> Result<CoreConnection, String> {
    let readiness = verify_readiness(loaded)?;
    let token = windows_credential(&loaded.config.credential_targets.desktop_read_token)?;
    let client = Client::builder()
        .timeout(Duration::from_secs(10))
        .build()
        .map_err(|_| "Failed to construct the local Core client.".to_string())?;
    let response = client
        .get(format!(
            "{}{}",
            readiness.endpoint, FUND_CONTROL_SNAPSHOT_PATH
        ))
        .header("X-Blunder-Token", &token)
        .send()
        .map_err(|_| "DumbMoney Core snapshot probe failed.".to_string())?;
    if !response.status().is_success() {
        return Err("DumbMoney Core rejected the read-only desktop probe.".to_string());
    }
    let snapshot: Value = response
        .json()
        .map_err(|_| "DumbMoney Core snapshot probe returned invalid JSON.".to_string())?;
    if snapshot.get("schema").and_then(Value::as_str) != Some("dumbmoney.control-snapshot.v1") {
        return Err("DumbMoney Core snapshot probe returned the wrong schema.".to_string());
    }
    let (child, job) = match owned {
        Some((child, job)) => (Some(Mutex::new(child)), Some(job)),
        None => (None, None),
    };
    Ok(CoreConnection {
        child,
        _job: job,
        endpoint: readiness.endpoint,
        token,
        client,
    })
}

fn start_development_core(
    config_path: &PathBuf,
    config_sha256: &str,
    loaded: &LoadedAttachConfig,
) -> Result<CoreConnection, String> {
    let executable = env::var_os("DUMBMONEY_FUND_CORE_DEV_EXE")
        .map(PathBuf::from)
        .ok_or("DUMBMONEY_FUND_CORE_DEV_EXE is required for explicit dev spawn.".to_string())?;
    if !executable.is_absolute()
        || executable.file_name().and_then(|value| value.to_str()) != Some("blunder-fund-core.exe")
        || !executable.is_file()
    {
        return Err(
            "DUMBMONEY_FUND_CORE_DEV_EXE must name an absolute blunder-fund-core.exe.".to_string(),
        );
    }
    let child = Command::new(executable)
        .arg("--config")
        .arg(config_path)
        .arg("--config-sha256")
        .arg(config_sha256)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|error| format!("Failed to start isolated dev Core: {error}"))?;
    let mut owned_child = OwnedChildGuard::new(child)?;
    let child_id = owned_child.child_mut()?.id();
    let stdout = owned_child
        .child_mut()?
        .stdout
        .take()
        .ok_or("DumbMoney dev Core stdout was not captured.".to_string())?;
    let stderr = owned_child
        .child_mut()?
        .stderr
        .take()
        .ok_or("DumbMoney dev Core stderr was not captured.".to_string())?;
    let mut reader = BufReader::new(stdout);
    let error_reader = BufReader::new(stderr);
    let mut ready_line = String::new();
    reader
        .read_line(&mut ready_line)
        .map_err(|error| format!("Failed to read dev Core readiness: {error}"))?;
    if ready_line.trim().is_empty() {
        let mut error_text = String::new();
        error_reader
            .take(16_384)
            .read_to_string(&mut error_text)
            .map_err(|error| format!("Failed to read dev Core startup error: {error}"))?;
        return Err(format!(
            "Dev Core returned no readiness record. stderr={}",
            error_text.trim()
        ));
    }
    let ready: FundCoreReady = serde_json::from_str(ready_line.trim())
        .map_err(|_| "Dev Core returned invalid readiness JSON.".to_string())?;
    if ready.schema != "dumbmoney.core-service-ready.v1"
        || ready.service_name != "DumbMoneyCore"
        || ready.host != "127.0.0.1"
        || ready.process_id != child_id
        || ready.port < 1024
        || ready.readiness_path != loaded.config.readiness_path.to_string_lossy().as_ref()
    {
        return Err("Dev Core readiness identity is invalid.".to_string());
    }
    let (child, job) = owned_child.take()?;
    let connection = attach_core(loaded, Some((child, job)))?;
    let verified = verify_readiness(loaded)?;
    if verified.instance_id != ready.instance_id || verified.signer_key_id != ready.signer_key_id {
        return Err("Dev Core stdout and signed readiness identities differ.".to_string());
    }
    if verified.endpoint != format!("http://127.0.0.1:{}", ready.port) {
        return Err("Dev Core stdout and signed readiness endpoints differ.".to_string());
    }
    Ok(connection)
}

fn canonicalize_with_missing_tail(path: &Path) -> Result<PathBuf, String> {
    if !path.is_absolute() {
        return Err("Dev Core isolation path must be absolute.".to_string());
    }
    let mut existing = path.to_path_buf();
    let mut missing = Vec::<OsString>::new();
    while !existing.exists() {
        let name = existing
            .file_name()
            .ok_or("Dev Core isolation path cannot be resolved safely.".to_string())?;
        missing.push(name.to_os_string());
        existing = existing
            .parent()
            .ok_or("Dev Core isolation path cannot be resolved safely.".to_string())?
            .to_path_buf();
    }
    let mut canonical = fs::canonicalize(existing)
        .map_err(|_| "Dev Core isolation path cannot be resolved safely.".to_string())?;
    for component in missing.into_iter().rev() {
        canonical.push(component);
    }
    Ok(canonical)
}

fn normalized_windows_path(path: &Path) -> Result<String, String> {
    let canonical = canonicalize_with_missing_tail(path)?;
    let mut value = canonical.to_string_lossy().replace('/', "\\");
    if let Some(rest) = value.strip_prefix(r"\\?\UNC\") {
        value = format!(r"\\{rest}");
    } else if let Some(rest) = value.strip_prefix(r"\\?\") {
        value = rest.to_string();
    }
    while value.len() > 3 && value.ends_with('\\') {
        value.pop();
    }
    Ok(value.to_lowercase())
}

fn path_is_within(path: &PathBuf, root: &str) -> Result<bool, String> {
    let candidate = normalized_windows_path(path)?;
    let normalized_root = normalized_windows_path(Path::new(root))?;
    Ok(candidate == normalized_root
        || candidate
            .strip_prefix(&normalized_root)
            .is_some_and(|tail| tail.starts_with('\\')))
}

fn validate_development_isolation(
    config_path: &PathBuf,
    loaded: &LoadedAttachConfig,
) -> Result<(), String> {
    let config = &loaded.config;
    if config_path == &PathBuf::from(DEFAULT_CORE_CONFIG_PATH)
        || path_is_within(config_path, DEFAULT_PRODUCTION_DATA_ROOT)?
        || path_is_within(config_path, DEFAULT_PRODUCTION_RELEASE_ROOT)?
        || path_is_within(&config.data_root, DEFAULT_PRODUCTION_DATA_ROOT)?
        || path_is_within(&config.readiness_path, DEFAULT_PRODUCTION_DATA_ROOT)?
        || path_is_within(&config.data_root, DEFAULT_PRODUCTION_RELEASE_ROOT)?
        || path_is_within(&config.readiness_path, DEFAULT_PRODUCTION_RELEASE_ROOT)?
    {
        return Err("Dev spawn may not reuse DumbMoney production paths.".to_string());
    }
    let targets = [
        &config.credential_targets.core_signing_seed,
        &config.credential_targets.desktop_read_token,
        &config.credential_targets.operator_bearer_token,
        &config.credential_targets.allocator_bearer_token,
        &config.credential_targets.dummy_cell_bearer_token,
        &config.credential_targets.dopey_cell_bearer_token,
    ];
    if targets
        .iter()
        .any(|target| !target.starts_with("credential-target:DumbMoneyDev/"))
    {
        return Err(
            "Dev spawn requires distinct credential-target:DumbMoneyDev/* identities.".to_string(),
        );
    }
    Ok(())
}

fn connect_core() -> Result<CoreConnection, String> {
    match env::var("DUMBMONEY_DESKTOP_DEV_SPAWN") {
        Err(env::VarError::NotPresent) => {
            let config_path = PathBuf::from(DEFAULT_CORE_CONFIG_PATH);
            let config_sha256 = required_digest_environment("DUMBMONEY_CORE_CONFIG_SHA256")?;
            let loaded = load_attach_config(&config_path, &config_sha256)?;
            attach_core(&loaded, None)
        }
        Ok(value) if value == "1" => {
            let config_path = env::var_os("DUMBMONEY_DESKTOP_DEV_CONFIG_PATH")
                .map(PathBuf::from)
                .ok_or(
                    "DUMBMONEY_DESKTOP_DEV_CONFIG_PATH is required for dev spawn.".to_string(),
                )?;
            let config_sha256 = required_digest_environment("DUMBMONEY_DESKTOP_DEV_CONFIG_SHA256")?;
            let loaded = load_attach_config(&config_path, &config_sha256)?;
            validate_development_isolation(&config_path, &loaded)?;
            start_development_core(&config_path, &config_sha256, &loaded)
        }
        Ok(_) => Err("DUMBMONEY_DESKTOP_DEV_SPAWN must be exactly 1 or absent.".to_string()),
        Err(env::VarError::NotUnicode(_)) => {
            Err("DUMBMONEY_DESKTOP_DEV_SPAWN is not valid Unicode.".to_string())
        }
    }
}

fn validate_request(request: &CoreRequest) -> Result<(), String> {
    if request.method != "GET" {
        return Err("DumbMoney desktop core requests are read-only.".to_string());
    }
    if request.path != FUND_CONTROL_SNAPSHOT_PATH || request.path.len() > MAX_REQUEST_PATH {
        return Err("DumbMoney desktop request path is not allowlisted.".to_string());
    }
    if request.body.is_some() {
        return Err("DumbMoney desktop GET requests cannot carry a body.".to_string());
    }
    Ok(())
}

#[tauri::command]
fn core_request(
    request: CoreRequest,
    state: State<'_, Arc<CoreConnection>>,
) -> Result<CoreResponse, String> {
    validate_request(&request)?;
    let url = format!("{}{}", state.endpoint, request.path);
    let builder = state
        .client
        .get(url)
        .header("X-Blunder-Token", &state.token);
    let response = builder
        .send()
        .map_err(|error| format!("DumbMoney Core request failed: {error}"))?;
    let status = response.status().as_u16();
    let body = response
        .json::<Value>()
        .map_err(|error| format!("DumbMoney Core response is not valid JSON: {error}"))?;
    Ok(CoreResponse { status, body })
}

pub fn run() -> Result<(), String> {
    let core = Arc::new(connect_core()?);
    tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(
            |app, _arguments, _directory| {
                if let Some(window) = app.get_webview_window("main") {
                    let _ = window.set_focus();
                }
            },
        ))
        .manage(core)
        .invoke_handler(tauri::generate_handler![core_request])
        .run(tauri::generate_context!())
        .map_err(|error| format!("DumbMoney desktop runtime failed: {error}"))?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn desktop_bridge_accepts_only_the_exact_read_only_snapshot_request() {
        validate_request(&CoreRequest {
            method: "GET".to_string(),
            path: FUND_CONTROL_SNAPSHOT_PATH.to_string(),
            body: None,
        })
        .expect("exact read-only request should pass");

        for request in [
            CoreRequest {
                method: "POST".to_string(),
                path: FUND_CONTROL_SNAPSHOT_PATH.to_string(),
                body: None,
            },
            CoreRequest {
                method: "GET".to_string(),
                path: "/api/v1/fund/control-snapshot?expanded=true".to_string(),
                body: None,
            },
            CoreRequest {
                method: "GET".to_string(),
                path: FUND_CONTROL_SNAPSHOT_PATH.to_string(),
                body: Some(Value::Null),
            },
        ] {
            assert!(validate_request(&request).is_err());
        }
    }

    #[test]
    fn readiness_time_requires_the_core_canonical_utc_wire_shape() {
        assert!(parse_time("2026-07-26T12:34:56Z", "test").is_ok());
        assert!(parse_time("2026-07-26T12:34:56.123456Z", "test").is_ok());
        assert!(parse_time("2026-07-26T12:34:56+00:00", "test").is_err());
        assert!(parse_time("2026-07-26T12:34:56.123Z", "test").is_err());
    }

    #[test]
    fn release_pins_and_target_identifiers_are_strict() {
        assert!(is_digest(&"a".repeat(64)));
        assert!(!is_digest(&"A".repeat(64)));
        assert!(valid_identifier(
            "credential-target:DumbMoney/DesktopReadToken"
        ));
        assert!(!valid_identifier("/credential-target"));
        assert!(!valid_identifier("credential target"));
    }

    #[test]
    fn dev_isolation_normalizes_windows_case_and_verbatim_prefixes() {
        assert!(
            path_is_within(
                &PathBuf::from(r"c:\PROGRAMDATA\DumbMoney\dev\readiness.json"),
                DEFAULT_PRODUCTION_DATA_ROOT,
            )
            .expect("case-normalized production path should resolve")
        );
        assert!(
            path_is_within(
                &PathBuf::from(r"\\?\C:\ProgramData\DumbMoney\dev\readiness.json"),
                DEFAULT_PRODUCTION_DATA_ROOT,
            )
            .expect("verbatim production path should resolve")
        );
        assert!(
            !path_is_within(
                &PathBuf::from(r"C:\DumbMoneyDev\readiness.json"),
                DEFAULT_PRODUCTION_DATA_ROOT,
            )
            .expect("isolated dev path should resolve")
        );
    }
}
