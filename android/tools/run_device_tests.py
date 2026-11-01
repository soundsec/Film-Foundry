"""Run isolated Android component/pipeline checks; collect JSON, never user media.

python android/tools/run_device_tests.py --serial DEVICE --build
Add --full for one 4096x2730 pass (quick default: 768x512).
Subsequent runs can omit --build to reuse the packaged test build.
"""
from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
import subprocess
import sys
import time
from datetime import datetime


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serial", required=True, help="ADB device serial; never selects another device")
    parser.add_argument("--build", action="store_true", help="Build debug app/test APKs using the declared application version")
    parser.add_argument("--full", action="store_true", help="4096x2730 instead of quick 768x512; may take several minutes")
    parser.add_argument("--focused", action="store_true", help="Only the usual two-worker/handoff path; omits cross-mode output comparison")
    parser.add_argument("--gpu", action="store_true", help="Measure the existing qualified GLES backend with CPU fallback (requires --focused)")
    parser.add_argument("--gpu-probe", action="store_true", help="Only record synthetic GPU/CPU numeric probes; no pipeline or UI")
    parser.add_argument("--hardware-self-test", action="store_true", help="Run the production synthetic hardware self-test; no media pipeline")
    parser.add_argument("--capture-ingest", action="store_true", help="Synthetic DNG/Ultra HDR file ingest and pipeline; never opens a camera")
    parser.add_argument("--viewer-ui", action="store_true", help="Only synthetic viewer transition/cache and safe-area checks")
    parser.add_argument("--spectral", action="store_true", help="Use matching 31-wavelength material/observer integration (requires --focused)")
    parser.add_argument("--capture-ui", action="store_true", help="Synthetic landscape imprint controls; never opens a camera")
    opts = parser.parse_args()
    if opts.capture_ui and any((opts.viewer_ui, opts.full, opts.focused, opts.gpu, opts.gpu_probe, opts.spectral, opts.hardware_self_test, opts.capture_ingest)):
        parser.error("--capture-ui is a separate diagnostic profile")
    if opts.viewer_ui and any((opts.full, opts.focused, opts.gpu, opts.gpu_probe, opts.spectral, opts.hardware_self_test, opts.capture_ingest)):
        parser.error("--viewer-ui is a separate diagnostic profile")
    if opts.capture_ingest and any((opts.full, opts.focused, opts.gpu, opts.gpu_probe, opts.spectral, opts.hardware_self_test)):
        parser.error("--capture-ingest is a separate synthetic diagnostic profile")
    if opts.hardware_self_test and any((opts.full, opts.focused, opts.gpu, opts.gpu_probe, opts.spectral)):
        parser.error("--hardware-self-test is a separate diagnostic profile")
    if opts.gpu and not opts.focused:
        parser.error("--gpu requires --focused; CPU/GPU do not promise byte-identical output")
    if opts.spectral and (not opts.focused or opts.gpu_probe):
        parser.error("--spectral requires --focused and cannot be combined with --gpu-probe")
    root = Path(__file__).resolve().parents[2]
    android = root / "android"
    sdk = Path(os.environ.get("ANDROID_HOME", Path.home()/"AppData/Local/Android/Sdk"))
    adb = sdk / "platform-tools/adb.exe"
    env = dict(os.environ)
    if not env.get("JAVA_HOME"):
        parser.error("Set JAVA_HOME to a JDK 17 installation before running device checks")
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S") + f"-{os.getpid()}"
    out = root / ".test_artifacts/device_automation" / run_id
    out.mkdir(parents=True, exist_ok=False)
    prefix = [str(adb), "-s", opts.serial]

    def run(argv: list[str], timeout: int = 60) -> str:
        result = subprocess.run(argv, cwd=android, env=env, capture_output=True,
                                text=True, encoding="utf-8", errors="replace", timeout=timeout)
        if result.returncode:
            raise RuntimeError(f"Command failed ({result.returncode}): {result.stdout}\n{result.stderr}")
        return result.stdout

    def require_idle() -> None:
        if run(prefix + ["get-state"]).strip() != "device":
            raise RuntimeError("Specified phone is not online")
        services = run(prefix + ["shell", "dumpsys", "activity", "services", "com.filmfoundry.mobile"])
        if "ServiceRecord{" in services:
            raise RuntimeError("Film Foundry has an active service. Finish/pause the current job before testing.")

    require_idle()
    if opts.build:
        print("Building app + isolated instrumentation APK (Temurin 17, offline)...", flush=True)
        # A fixed batch path/arguments: no user input enters a shell command.
        result = subprocess.run([str(android/"gradlew.bat"), "--offline", ":app:assembleDebug", ":app:assembleDebugAndroidTest"],
                                cwd=android, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, encoding="utf-8", errors="replace")
        (out/"build.log").write_text(result.stdout, encoding="utf-8")
        if result.returncode:
            print(result.stdout[-6000:]); return 1
    app = android / "app/build/outputs/apk/debug/app-debug.apk"
    test = android / "app/build/outputs/apk/androidTest/debug/app-debug-androidTest.apk"
    if not app.is_file() or not test.is_file():
        raise RuntimeError("Missing APKs. Run again with --build.")
    require_idle()
    print("Installing test build while preserving application data...", flush=True)
    for apk in (app, test):
        result = run(prefix + ["install", "-r", str(apk)], 120)
        if "Success" not in result: raise RuntimeError(result)
    metadata = json.loads((app.parent/"output-metadata.json").read_text(encoding="utf-8"))
    (out/"apk-metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    profile = "capture_ui" if opts.capture_ui else "viewer_ui" if opts.viewer_ui else "capture_ingest" if opts.capture_ingest else "hardware_self_test" if opts.hardware_self_test else "gpu_probe" if opts.gpu_probe else "full" if opts.full else "quick"
    require_idle()
    # Some vendors freeze an instrumented app on an already sleeping display despite a wake lock.
    # Wake only (never unlock or change timeout/settings); the fixture keeps the display on.
    run(prefix + ["shell", "input", "keyevent", "KEYCODE_WAKEUP"])
    print(f"Running {profile} synthetic checks. Results: {out}", flush=True)
    command = prefix + ["shell", "am", "instrument", "-w", "-r", "-e", "run_id", run_id,
                        "-e", "profile", profile, "-e", "focused", str(opts.focused).lower(), "-e", "gpu", str(opts.gpu).lower(),
                        "-e", "spectral", str(opts.spectral).lower(),
                        "com.filmfoundry.mobile.test/com.filmfoundry.automation.DeviceTestRunner"]
    with (out/"instrumentation.log").open("w", encoding="utf-8") as log:
        process = subprocess.Popen(command, cwd=android, stdout=log, stderr=subprocess.STDOUT)
        started = time.monotonic()
        next_update = started+15
        timeout = 32*60 if opts.full else 7*60
        timed_out = False
        while process.poll() is None:
            time.sleep(1)
            if time.monotonic() >= next_update:
                lines = (out/"instrumentation.log").read_text(encoding="utf-8", errors="replace").splitlines()
                phases = [line.strip() for line in lines if line.endswith("(running)")]
                print(f"{phases[-1] if phases else 'Starting'} ({int(time.monotonic()-started)} s)...", flush=True)
                next_update += 15
            if time.monotonic()-started > timeout:
                process.terminate(); process.wait(timeout=10)
                timed_out = True
                break
    payload = run(prefix + ["exec-out", "run-as", "com.filmfoundry.mobile", "cat", f"files/automation/results/{run_id}.json"])
    result = json.loads(payload)
    result["host_wall_ms"] = round((time.monotonic()-started)*1000)
    if result["status"] == "running":
        result["last_device_status"] = "running"
        result["status"] = "interrupted"
    if timed_out:
        result["status"] = "timed_out"
        result["host_detail"] = "ADB wait ended; no app task was forcibly stopped. Inspect this checkpoint before retrying."
    if result["status"] in ("interrupted", "timed_out"):
        # A killed process cannot merge its latest five-second sampler checkpoint.
        # Only this run's diagnostic JSON is read; never enumerate media or the real catalog.
        directory = f"cache/automation/{run_id}/files/performance_test"
        recovered = []
        try:
            names = run(prefix + ["shell", "run-as", "com.filmfoundry.mobile", "ls", directory], 10).splitlines()
            for name in [n.strip() for n in names if re.fullmatch(r"[a-f0-9-]{36}\.json", n.strip())][:4]:
                recovered.append(json.loads(run(prefix + ["exec-out", "run-as", "com.filmfoundry.mobile", "cat", f"{directory}/{name}"], 10)))
        except (RuntimeError, subprocess.SubprocessError, OSError, ValueError) as error:
            result["checkpoint_collection_error"] = type(error).__name__
        result["interrupted_performance"] = recovered
    (out/"result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Build {result['build']}: {result['status']}; {len(result['checks'])} checks; {result['wall_ms']/1000:.1f} s", flush=True)
    for check in result["checks"]:
        print(f"  {check['status']}: {check['name']} {check.get('detail','')}")
    for probe in result.get("gpu_probes", []):
        print(f"  {probe['operation']}: max error {probe['max_absolute_error']}; actual {probe['actual']}")
    for row in result.get("hardware_self_test", {}).get("measurements", []):
        print(f"  {row['operation']}: CPU {row['cpu_ms']:.3f} ms; GPU {row['gpu_ms']} ms; {row['detail']}")
    for measurement in result["performance"]:
        trace = measurement["trace"]
        print(f"  {measurement['case']}: {trace['wall_ms']/1000:.2f} s; main queue max {measurement['main_queue_max_ms']} ms")
        if measurement.get("compute_backend"):
            print("    " + "; ".join(measurement["compute_backend"]))
    if result["phase"]=="finished" and not timed_out:
        run(prefix + ["shell", "am", "start", "-n", "com.filmfoundry.mobile/com.filmfoundry.app.ProcessHostActivity"])
    return 0 if result["status"]=="passed" and process.returncode==0 else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, subprocess.SubprocessError, OSError, ValueError) as error:
        print(f"Automation stopped: {error}", file=sys.stderr)
        raise SystemExit(1)
