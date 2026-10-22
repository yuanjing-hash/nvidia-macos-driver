#!/usr/bin/env python3
"""Build a checksum-paired offline 1401 from the matching official app source."""
import hashlib
import json
from pathlib import Path
import subprocess

root = Path(__file__).resolve().parents[1]
app = root / ".deps/1401"
out = root / "build-out"
expected = "fc09d19530b4e6aca0b2b41416cbb16440531911"
actual = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=app, text=True).strip()
if actual != expected:
    raise SystemExit("Installer source commit mismatch")
package = out / "nullmoth-2.5.2-rtx6000-bar-preflight-v1.tar.gz"
sha = hashlib.sha256(package.read_bytes()).hexdigest()
main = app / "mac/Sources/main.swift"
source = main.read_text()
assert source.count('static let name = "nullmoth-nvidia-2.5.2.tar.gz"') == 1
source = source.replace('static let name = "nullmoth-nvidia-2.5.2.tar.gz"', f'static let name = "{package.name}"')
source = source.replace('static let sha256 = "b3c01bbdce788e6181636836c73653be19052e217442c87763923ed37296007d"', f'static let sha256 = "{sha}"', 1)
source = source.replace('window.title = "1401";', 'window.title = "1401 - RTX6000 BAR Preflight TEST 2.5.2";', 1)
needle = '        c.append(fm.urls(for: .downloadsDirectory, in: .userDomainMask)[0].appendingPathComponent(Package.name))'
assert source.count(needle) == 1
source = source.replace(needle, needle + '\n        c.append(Bundle.main.bundleURL.deletingLastPathComponent().appendingPathComponent(Package.name))')
# The test app installs only its checksum-paired local file. An upstream update
# through this app would silently replace the experiment with a different build.
for old, new in [
    ('case "checkUpdate": checkUpdate()', 'case "checkUpdate": send("upd", ["state": "error", "why": "Offline BAR preflight test: use this paired package; official updates are disabled in the test app."])'),
    ('case "updateDriver": updateDriver(efi: b["efi"] as? String ?? "auto")', 'case "updateDriver": send("upd", ["state": "error", "why": "Use Install the driver with the paired local test archive."])'),
    ('case "updateApp": updateApp()', 'case "updateApp": send("upd", ["state": "error", "why": "This test app does not update itself."])'),
    ('case "download": download()', 'case "download": send("dl", ["state": "error", "why": "Put the paired test tar.gz beside this app or in Downloads, then reopen the app."])'),
]:
    assert source.count(old) == 1, old
    source = source.replace(old, new)
main.write_text(source)
subprocess.run(["bash", "mac/build.sh", str(root / "app-build")], cwd=app, check=True)
old_bundle = root / "app-build/1401.app"
bundle = old_bundle.with_name("1401-RTX6000-BAR-Preflight-Test.app")
old_bundle.rename(bundle)
subprocess.run(["codesign", "--verify", "--deep", "--strict", str(bundle)], check=True)
subprocess.run(["xcrun", "lipo", str(bundle / "Contents/MacOS/1401"), "-verify_arch", "x86_64"], check=True)
# Do not ship a different uninstall/setup helper under the official app's UI.
for name in ("nullmoth-setup.sh", "nullmoth-uninstall.sh", "NullMothSafe.efi"):
    upstream = subprocess.check_output(["git", "show", f"{expected}:mac/Resources/{name}"], cwd=app)
    assert upstream == (bundle / "Contents/Resources" / name).read_bytes(), name
zipfile = out / "1401-Mac-2.5.2-RTX6000-BAR-Preflight-Test.zip"
subprocess.run(["ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", str(bundle), str(zipfile)], check=True)
with (out / "SHA256SUMS.txt").open("a") as sums:
    sums.write(f"{hashlib.sha256(zipfile.read_bytes()).hexdigest()}  {zipfile.name}\n")
(out / "installer-proof.json").write_text(json.dumps({
    "source_commit": actual, "package_sha256": sha,
    "package_name": package.name, "offline_test_app": zipfile.name,
    "official_setup_uninstall_and_efi_helper_preserved": True,
    "hardware_tested": False,
}, indent=2) + "\n")
print("Paired offline App built; official setup/uninstall helpers preserved.")
