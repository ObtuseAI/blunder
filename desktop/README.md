# Blunder vNext desktop

The desktop work surface is a constrained Tauri 2 host over the authenticated local Python v1 service. The renderer has no shell permission, filesystem permission, credential access, or direct service token. Only the Rust-owned `core_request` command crosses the renderer boundary, and it accepts bounded `GET` and `POST` requests under `/api/v1/`.

## Prerequisites

From the repository root, validate the exact local build prerequisites:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\obtuse\blunder-vnext-desktop-prerequisites.ps1 -Root .
```

The check fails explicitly when the pinned project-local Rust toolchain, Node lockfile, Visual Studio C++ workload, or WebView2 runtime is absent. Rust state belongs beneath the ignored `.tooling` directory; JavaScript dependencies belong beneath the ignored `desktop/node_modules` directory.

## Validation

```powershell
cd desktop
npm ci
npm run typecheck
npm run build
```

Build the packaged Python sidecar and launch Tauri:

```powershell
$python = (Resolve-Path ..\.venv\Scripts\python.exe).Path
powershell -NoProfile -ExecutionPolicy Bypass -File ..\scripts\obtuse\build-blunder-vnext-sidecar.ps1 -Root .. -Python $python
npm run tauri:dev
```

Debug builds bind to the repository that produced the binary. Release builds resolve the Tauri-bundled PyInstaller sidecar beside the desktop executable and require an operator-selected project root. Neither path nor the process-bound token is sent to the renderer. Until the release evidence gate is complete, the repository remains `CCOS_RESEARCH_PROTOTYPE` and no installer claim is made.
