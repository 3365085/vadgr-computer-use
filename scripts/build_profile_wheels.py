#!/usr/bin/env python3
"""Construct immutable profile wheels from source and validated native inputs."""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import io
import json
import re
import stat
import struct
import sys
import time
import zipfile
from pathlib import Path

import tomllib

ROOT = Path(__file__).resolve().parents[1]
PROFILES = tuple(
    f"{os}-{arch}" for os in ("windows", "macos", "linux", "wsl") for arch in ("x86_64", "aarch64")
)
MAX_BYTES = 512 * 1024 * 1024
MAX_FILES = 10000
MAX_DEPTH = 4
FIXED_TIME = (1980, 1, 1, 0, 0, 0)
PREFIX = "computer_use/browser/"


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def unique_object(pairs: list[tuple[str, object]]) -> dict:
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def read_json(data: bytes) -> dict:
    value = json.loads(data, object_pairs_hook=unique_object)
    if not isinstance(value, dict) or canonical(value) != data:
        raise ValueError("JSON must be a canonical sorted-key UTF-8 object with LF")
    return value


def safe_path(name: str) -> str:
    if not isinstance(name, str) or not name or "\\" in name or ":" in name:
        raise ValueError(f"unsafe member path: {name!r}")
    parts = name.split("/")
    reserved = {"con", "prn", "aux", "nul", "conin$", "conout$"}
    reserved.update(f"{prefix}{n}" for prefix in ("com", "lpt") for n in range(1, 10))
    for part in parts:
        if (
            part in {"", ".", ".."}
            or part.endswith((".", " "))
            or any(ord(c) < 32 or c in '<>"|?*' for c in part)
            or part.split(".")[0].casefold() in reserved
        ):
            raise ValueError(f"unsafe member path: {name!r}")
    return name


def zip_members(data: bytes, budget: list[int] | None = None) -> dict[str, bytes]:
    budget = budget if budget is not None else [0, 0]
    result = {}
    folded = set()
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        if len(archive.infolist()) > MAX_FILES:
            raise ValueError("archive member limit exceeded")
        for info in archive.infolist():
            name = safe_path(info.filename)
            kind = stat.S_IFMT(info.external_attr >> 16)
            if info.is_dir() or kind not in {0, stat.S_IFREG} or info.flag_bits & 1:
                raise ValueError(f"non-regular or encrypted archive member: {name}")
            if name.casefold() in folded:
                raise ValueError(f"duplicate or case-colliding archive member: {name}")
            folded.add(name.casefold())
            budget[0] += info.file_size
            budget[1] += 1
            if budget[0] > MAX_BYTES or budget[1] > MAX_FILES:
                raise ValueError("archive extraction budget exceeded")
            result[name] = archive.read(info)
    # A file cannot also be a parent directory, including case-only aliases.
    for name in folded:
        if any("/".join(name.split("/")[:i]) in folded for i in range(1, len(name.split("/")))):
            raise ValueError("archive file/directory collision")
    return result


def make_zip(files: dict[str, bytes]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, data in sorted(files.items()):
            safe_path(name)
            info = zipfile.ZipInfo(name, FIXED_TIME)
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, data)
    result = output.getvalue()
    zip_members(result)
    return result


def native_identity(data: bytes) -> tuple[str, str] | None:
    if data.startswith(b"MZ"):
        if len(data) < 64:
            raise ValueError("truncated PE header")
        offset = struct.unpack_from("<I", data, 60)[0]
        if offset + 24 > len(data) or data[offset : offset + 4] != b"PE\0\0":
            raise ValueError("invalid PE header")
        machine = struct.unpack_from("<H", data, offset + 4)[0]
        if machine not in {0x8664, 0xAA64}:
            raise ValueError("unsupported PE architecture")
        return "windows", {0x8664: "x86_64", 0xAA64: "aarch64"}[machine]
    if data.startswith(b"\x7fELF"):
        if len(data) < 20 or data[4] != 2 or data[5] not in {1, 2}:
            raise ValueError("invalid ELF header")
        machine = int.from_bytes(data[18:20], "little" if data[5] == 1 else "big")
        if machine not in {62, 183}:
            raise ValueError("unsupported ELF architecture")
        return "linux", {62: "x86_64", 183: "aarch64"}[machine]
    if data[:4] in {b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xcf"}:
        if len(data) < 8:
            raise ValueError("truncated Mach-O header")
        machine = int.from_bytes(data[4:8], "little" if data[0] == 0xCF else "big")
        if machine not in {0x1000007, 0x100000C}:
            raise ValueError("unsupported Mach-O architecture")
        return "macos", {0x1000007: "x86_64", 0x100000C: "aarch64"}[machine]
    if data[:4] in {b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca"}:
        raise ValueError("unreviewed universal executable")
    return None


def identity(name: str, data: bytes, key: str = "path") -> dict:
    return {key: name, "size": len(data), "sha256": digest(data)}


def interpreter(profile: str) -> dict:
    os_name, architecture = profile.split("-", 1)
    tag = {
        "windows": {"x86_64": "win_amd64", "aarch64": "win_arm64"},
        "macos": {"x86_64": "macosx_10_15_x86_64", "aarch64": "macosx_11_0_arm64"},
        "linux": {"x86_64": "manylinux_2_28_x86_64", "aarch64": "manylinux_2_28_aarch64"},
    }
    return {
        "python": "py3",
        "abi": "none",
        "platform": tag["linux" if os_name == "wsl" else os_name][architecture],
    }


def inventory(files: dict[str, bytes], profile: str) -> tuple[list, list]:
    if profile not in PROFILES:
        raise ValueError("unknown release profile")
    os_name, architecture = profile.split("-", 1)
    execution_os = "linux" if os_name == "wsl" else os_name
    executables, archives = [], []
    budget = [0, 0]

    def visit(wheel_path: str, members: list[str], data: bytes, depth: int) -> None:
        if depth > MAX_DEPTH:
            raise ValueError("nested archive depth exceeded")
        name = members[-1] if members else wheel_path
        native = native_identity(data)
        script = name.endswith((".py", ".sh", ".ps1"))
        if data.startswith(b"#!") and not script:
            raise ValueError(f"executable script disguised as data: {name}")
        if native or script:
            role, process_os = "runtime", execution_os
            if wheel_path.startswith(PREFIX + "winhost/") and native:
                role, process_os = "browser-relay", "windows"
            elif wheel_path.startswith(PREFIX + "winbroker/") and (native or name.endswith(".ps1")):
                role, process_os = "browser-broker", "windows"
            if role != "runtime" and os_name not in {"windows", "wsl"}:
                raise ValueError("native Linux/macOS profile contains Windows helpers")
            if native and native != (process_os, architecture):
                raise ValueError(f"incorrect native identity: {wheel_path}:{name}")
            executables.append(
                {
                    "release_profile": profile,
                    "role": role,
                    "execution_os": process_os,
                    "architecture": architecture,
                    "wheel_path": wheel_path,
                    "archive_members": members,
                    "size": len(data),
                    "sha256": digest(data),
                    "component": "vadgr-computer-use" if not members else "browser-broker-runtime",
                    "consumer": "computer_use.browser.windows_broker"
                    if role == "browser-broker"
                    else "computer_use.setup.extension_setup"
                    if role == "browser-relay"
                    else "computer_use.mcp_server",
                }
            )
        elif name.lower().endswith((".exe", ".dll", ".pyd", ".so", ".dylib")):
            raise ValueError(f"executable has no recognized native header: {name}")
        if data.startswith((b"PK\x03\x04", b"PK\x05\x06")):
            nested = zip_members(data, budget)
            archives.append(
                {
                    "wheel_path": wheel_path,
                    "archive_members": members,
                    "size": len(data),
                    "sha256": digest(data),
                    "members": [identity(p, b) for p, b in sorted(nested.items())],
                }
            )
            for path, payload in sorted(nested.items()):
                visit(wheel_path, members + [path], payload, depth + 1)
        elif (
            name.lower().endswith((".zip", ".7z", ".tar", ".gz", ".xz", ".bz2"))
            or data.startswith((b"7z\xbc\xaf\x27\x1c", b"\x1f\x8b", b"\xfd7zXZ", b"Rar!"))
            or data[257:262] == b"ustar"
        ):
            raise ValueError(f"unknown or malformed container: {name}")

    for path, data in sorted(files.items()):
        safe_path(path)
        visit(path, [], data, 0)
    return executables, archives


def source_files(repository: Path) -> dict[str, bytes]:
    files = {}
    for path in sorted((repository / "computer_use").rglob("*")):
        relative = path.relative_to(repository).as_posix()
        if path.is_symlink():
            raise ValueError(f"source symlink is forbidden: {relative}")
        if not path.is_file() or any(p in {"tests", "__pycache__", "profiles"} for p in path.parts):
            continue
        if path.name == "_profile_trust.py":
            continue
        if (
            path.suffix == ".py"
            or "typing_profiles/" in relative
            and path.suffix == ".json"
            or relative.endswith(".rules")
            or "/gnome_extension/" in relative
            and path.suffix in {".js", ".json", ".css"}
        ):
            files[relative] = path.read_bytes()
    return files


def helper_files(root: Path, architecture: str, version: str, commit: str) -> tuple[dict, dict]:
    relay = root / "vadgr-cua-host.exe"
    manifests = list(root.glob("*.manifest.json"))
    archives = list(root.glob("*.zip"))
    if len(manifests) != 1 or len(archives) != 1:
        raise ValueError("each native helper input must have one archive and one member manifest")
    manifest, archive = manifests[0], archives[0]
    value = read_json(manifest.read_bytes())
    data = archive.read_bytes()
    if (
        value.get("version") != version
        or value.get("source_commit") != commit
        or value.get("target") != architecture + "-pc-windows-msvc"
        or value.get("archive_sha256") != digest(data)
        or value.get("archive_size") != len(data)
    ):
        raise ValueError("native helper manifest identity differs from the selected source/profile")
    nested = zip_members(data)
    expected = [identity(p, b) for p, b in sorted(nested.items())]
    if value.get("files") != expected:
        raise ValueError("native helper member inventory differs")
    if native_identity(relay.read_bytes()) != ("windows", architecture):
        raise ValueError("relay native architecture differs")
    files = {
        PREFIX + f"winhost/{architecture}/" + relay.name: relay.read_bytes(),
        PREFIX + f"winbroker/{architecture}/" + archive.name: data,
        PREFIX + f"winbroker/{architecture}/" + manifest.name: manifest.read_bytes(),
    }
    paths = list(files)
    return files, {
        "architecture": architecture,
        "relay": identity(paths[0], files[paths[0]]),
        "archive": identity(paths[1], files[paths[1]]),
        "member_manifest": identity(paths[2], files[paths[2]]),
    }


def wheel_bytes(
    files: dict[str, bytes], project: dict, tag: str, *, build_tag: str | None = None
) -> bytes:
    files = dict(files)
    info = f"vadgr_computer_use-{project['version']}.dist-info/"
    metadata = [
        "Metadata-Version: 2.3",
        "Name: vadgr-computer-use",
        "Version: " + project["version"],
        "Requires-Python: " + project["requires-python"],
        "License: Apache-2.0",
        "Summary: " + project["description"],
    ]
    metadata += ["Requires-Dist: " + item for item in project["dependencies"]]
    for extra, dependencies in project.get("optional-dependencies", {}).items():
        metadata.append("Provides-Extra: " + extra)
        for dependency in dependencies:
            requirement, separator, marker = dependency.partition(";")
            metadata.append(
                "Requires-Dist: "
                + requirement.strip()
                + "; "
                + (f"({marker.strip()}) and " if separator else "")
                + f'extra == "{extra}"'
            )
    files[info + "METADATA"] = ("\n".join(metadata) + "\n\n").encode()
    wheel = (
        "Wheel-Version: 1.0\nGenerator: vadgr-profile-builder\n"
        "Root-Is-Purelib: true\nTag: " + tag + "\n"
    )
    if build_tag is not None:
        if not build_tag or not build_tag[0].isdigit() or "-" in build_tag:
            raise ValueError("invalid wheel build tag")
        wheel += "Build: " + build_tag + "\n"
    files[info + "WHEEL"] = wheel.encode()
    files[info + "entry_points.txt"] = (
        "[console_scripts]\n"
        + "\n".join(f"{key} = {value}" for key, value in sorted(project["scripts"].items()))
        + "\n"
    ).encode()
    record = io.StringIO(newline="")
    writer = csv.writer(record, lineterminator="\n")
    for name, data in sorted(files.items()):
        encoded = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
        writer.writerow((name, "sha256=" + encoded, len(data)))
    writer.writerow((info + "RECORD", "", ""))
    files[info + "RECORD"] = record.getvalue().encode()
    return make_zip(files)


def freeze(path: Path, data: bytes) -> dict:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as file:
        file.write(data)
    return identity(path.name, data, "filename")


def validate_producer(producer: dict) -> None:
    expected = {
        "repository",
        "repository_id",
        "owner_id",
        "source_commit",
        "tooling_commit",
        "workflow",
        "workflow_id",
        "ref",
        "event",
        "run_id",
        "attempt",
        "runner_environment",
    }
    if set(producer) != expected:
        raise ValueError("producer schema differs")
    if (
        producer["repository"] != "MONTBRAIN/vadgr-computer-use"
        or producer["repository_id"] != 1215350293
        or producer["owner_id"] != 165305289
        or producer["workflow"] != ".github/workflows/profile-wheels.yml"
        or producer["ref"] != "refs/heads/master"
        or producer["event"] != "workflow_dispatch"
        or producer["attempt"] != 1
        or producer["runner_environment"] != "github-hosted"
    ):
        raise ValueError("producer is not an authorized first-attempt master workflow")
    for key in ("repository_id", "owner_id", "workflow_id", "run_id"):
        if type(producer[key]) is not int or producer[key] <= 0:
            raise ValueError(f"invalid numeric producer identity: {key}")
    for key in ("source_commit", "tooling_commit"):
        if not re.fullmatch(r"[0-9a-f]{40}", producer[key]):
            raise ValueError(f"invalid producer commit: {key}")
    # The trusted master workflow binds an independently reviewed source commit.
    # Keeping both identities permits qualification before the product PR merges.


def check_adoption_policy(
    policy: dict,
    root: bytes,
    verifier: bytes,
    architecture: str,
    version: str,
    commit: str,
    helper: dict,
) -> None:
    fields = {
        "schema",
        "architecture",
        "cua_version",
        "source_commit",
        "input_closure",
        "root_sha256",
        "verifier_sha256",
        "repository",
        "repository_id",
        "owner_id",
        "certificate_identity",
        "issuer",
        "source_ref",
        "source_sha_allowlist",
        "signer_sha_allowlist",
        "expires_at",
        "relay_path",
        "files",
    }
    if set(policy) != fields:
        raise ValueError("standalone adoption policy schema differs")
    if (
        policy["schema"] != 1
        or policy["architecture"] != architecture
        or policy["cua_version"] != version
        or policy["source_commit"] != commit
        or policy["repository"] != "MONTBRAIN/vadgr"
        or policy["repository_id"] != 1158230114
        or policy["owner_id"] != 165305289
        or policy["certificate_identity"]
        != "https://github.com/MONTBRAIN/vadgr/.github/workflows/candidate.yml@refs/heads/master"
        or policy["issuer"] != "https://token.actions.githubusercontent.com"
        or policy["source_ref"] != "refs/heads/master"
        or type(policy["expires_at"]) is not int
        or policy["expires_at"] <= time.time()
    ):
        raise ValueError("standalone adoption authority or validity differs")
    for field in ("source_sha_allowlist", "signer_sha_allowlist"):
        pins = policy[field]
        if (
            not isinstance(pins, list)
            or not pins
            or len(pins) != len(set(pins))
            or any(
                not isinstance(p, str) or not re.fullmatch(r"[0-9a-f]{40}", p) or p == "0" * 40
                for p in pins
            )
        ):
            raise ValueError(
                "standalone adoption policy has no exact reviewed workflow/source pins"
            )
    closure = {
        "relay_sha256": helper["relay"]["sha256"],
        "archive_sha256": helper["archive"]["sha256"],
        "manifest_sha256": helper["member_manifest"]["sha256"],
    }
    if (
        policy["input_closure"] != closure
        or policy["root_sha256"] != digest(root)
        or policy["verifier_sha256"] != digest(verifier)
        or native_identity(verifier) != ("windows", architecture)
    ):
        raise ValueError("standalone adoption input, root or verifier differs")
    if not isinstance(policy["files"], dict) or not policy["files"]:
        raise ValueError("standalone adoption policy has no reviewed signing classes")
    if safe_path(policy["relay_path"]) not in policy["files"]:
        raise ValueError("standalone adoption policy omits its relay")


def build(
    repository: Path, helpers: dict[str, Path], output: Path, descriptor: dict, adoption: Path
) -> dict:
    producer = descriptor["producer"]
    validate_producer(producer)
    project = tomllib.loads((repository / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    version, commit = project["version"], producer["source_commit"]
    common = source_files(repository)
    common["vadgr_computer_use-" + version + ".dist-info/licenses/LICENSE"] = (
        repository / "LICENSE"
    ).read_bytes()
    if (repository / "NOTICE").is_file():
        common["vadgr_computer_use-" + version + ".dist-info/licenses/NOTICE"] = (
            repository / "NOTICE"
        ).read_bytes()
    closures = {arch: helper_files(root, arch, version, commit) for arch, root in helpers.items()}
    if set(closures) != {"x86_64", "aarch64"}:
        raise ValueError("both native Windows helper architectures are required")
    catalog = {
        "schema": 1,
        "cua_version": version,
        "source_commit": commit,
        "producer": producer,
        "profiles": {},
        "standalone": {},
    }
    manifest_bytes = {}
    for profile in PROFILES:
        os_name, architecture = profile.split("-", 1)
        files = dict(common)
        helper = None
        if os_name in {"windows", "wsl"}:
            payload, helper = closures[architecture]
            files.update(payload)
            for name in ("install-managed.ps1", "predecessor-catalog.json"):
                files[PREFIX + "winbroker/" + name] = (
                    repository / PREFIX / "winbroker" / name
                ).read_bytes()
        executables, archives = inventory(files, profile)
        manifest = {
            "schema": 1,
            "cua_version": version,
            "source_commit": commit,
            "release_profile": profile,
            "interpreter": interpreter(profile),
            "files": [identity(p, b, "wheel_path") for p, b in sorted(files.items())],
            "executables": executables,
            "archives": archives,
            "helpers": helper,
        }
        raw = canonical(manifest)
        manifest_bytes[profile] = raw
        files[PREFIX + f"profiles/{profile}/cua-profile-manifest.json"] = raw
        trust = {
            "schema": 1,
            "mode": "managed",
            "release_profile": profile,
            "manifest_sha256": {profile: digest(raw)},
        }
        files[PREFIX + "_profile_trust.py"] = ("TRUST = " + repr(trust) + "\n").encode()
        tags = interpreter(profile)
        tag = f"{tags['python']}-{tags['abi']}-{tags['platform']}"
        build_tag = "1" + profile.replace("-", "").replace("_", "")
        name = f"vadgr_computer_use-{version}-{build_tag}-{tag}.whl"
        wheel = wheel_bytes(files, project, tag, build_tag=build_tag)
        catalog["profiles"][profile] = {
            "interpreter": tags,
            "wheel": freeze(output / name, wheel),
            "input_role_manifest": freeze(output / f"{profile}-cua-profile-manifest.json", raw),
            "evidence": descriptor["profiles"][profile],
        }
    standalone = dict(common)
    for payload, _ in closures.values():
        standalone.update(payload)
    for name in ("install.ps1", "install-managed.ps1", "adopt-native.ps1", "predecessor-catalog.json"):
        standalone[PREFIX + "winbroker/" + name] = (
            repository / PREFIX / "winbroker" / name
        ).read_bytes()
    for profile, raw in manifest_bytes.items():
        standalone[PREFIX + f"profiles/{profile}/cua-profile-manifest.json"] = raw
    adoption_pins = {}
    for architecture in ("x86_64", "aarch64"):
        directory = adoption / architecture
        payload = {
            name: (directory / name).read_bytes()
            for name in ("adoption-policy.json", "trusted-root.json", "gh.exe", "LICENSE")
        }
        policy = read_json(payload["adoption-policy.json"])
        check_adoption_policy(
            policy,
            payload["trusted-root.json"],
            payload["gh.exe"],
            architecture,
            version,
            commit,
            closures[architecture][1],
        )
        for name, data in payload.items():
            standalone[PREFIX + f"adoption/{architecture}/{name}"] = data
        adoption_pins[architecture] = {
            "policy_sha256": digest(payload["adoption-policy.json"]),
            "root_sha256": digest(payload["trusted-root.json"]),
            "verifier_sha256": digest(payload["gh.exe"]),
        }
    trust = {
        "schema": 1,
        "mode": "standalone-input",
        "manifest_sha256": {p: digest(b) for p, b in manifest_bytes.items()},
        "adoption": adoption_pins,
    }
    standalone[PREFIX + "_profile_trust.py"] = ("TRUST = " + repr(trust) + "\n").encode()
    raw = wheel_bytes(standalone, project, "py3-none-any")
    catalog["standalone"] = {
        "wheel": freeze(output / f"vadgr_computer_use-{version}-py3-none-any.whl", raw),
        "manifest_sha256": trust["manifest_sha256"],
        "adoption": adoption_pins,
    }
    freeze(output / "cua-profile-catalog.json", canonical(catalog))
    return catalog


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, default=ROOT)
    parser.add_argument("--x86-64-helpers", type=Path, required=True)
    parser.add_argument("--aarch64-helpers", type=Path, required=True)
    parser.add_argument("--descriptor", type=Path, required=True)
    parser.add_argument("--adoption", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = build(
        args.repository,
        {"x86_64": args.x86_64_helpers, "aarch64": args.aarch64_helpers},
        args.output,
        read_json(args.descriptor.read_bytes()),
        args.adoption,
    )
    print(f"Constructed {len(result['profiles'])} managed profiles and one standalone wheel")
    return 0


if __name__ == "__main__":
    sys.exit(main())
