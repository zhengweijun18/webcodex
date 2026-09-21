use crate::models::{EnhancedRuntimeComponentSnapshot, EnhancedRuntimeSnapshot};
use serde::Deserialize;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::time::{SystemTime, UNIX_EPOCH};

const NATIVE_PROBE_TIMEOUT_MS: &str = "3000";
const VUE_VERSION: &str = "2.2.12";
const TYPESCRIPT_VERSION: &str = "5.9.3";

#[derive(Debug, Deserialize)]
struct NativeProbe {
    status: String,
    reason: String,
    owner: String,
    impact: String,
    next_action: String,
    observed_at_ms: u64,
    source_version: String,
    native_model_turns: u64,
}

fn observed_at_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis()
        .try_into()
        .unwrap_or(u64::MAX)
}

fn component(
    status: &str,
    reason: &str,
    owner: &str,
    impact: &str,
    next_action: &str,
    source_version: impl Into<String>,
) -> EnhancedRuntimeComponentSnapshot {
    EnhancedRuntimeComponentSnapshot {
        status: status.to_string(),
        reason: reason.to_string(),
        owner: owner.to_string(),
        impact: impact.to_string(),
        next_action: next_action.to_string(),
        observed_at_ms: observed_at_ms(),
        source_version: source_version.into(),
    }
}

fn bundled_regular_file(path: &Path) -> bool {
    std::fs::symlink_metadata(path)
        .is_ok_and(|metadata| metadata.is_file() && !metadata.file_type().is_symlink())
}

fn bundled_regular_dir(path: &Path) -> bool {
    std::fs::symlink_metadata(path)
        .is_ok_and(|metadata| metadata.is_dir() && !metadata.file_type().is_symlink())
}

fn package_version(path: &Path) -> Option<String> {
    let payload: serde_json::Value =
        serde_json::from_slice(&std::fs::read(path).ok()?).ok()?;
    payload.get("version")?.as_str().map(str::to_string)
}

fn native_probe(node: &Path, bridge: &Path, bridge_version: &str) -> EnhancedRuntimeComponentSnapshot {
    let readiness = bridge.join("readiness.mjs");
    if !bundled_regular_file(&readiness) {
        return component(
            "unavailable",
            "bundled_bridge_missing",
            "desktop",
            "native_context_only",
            "reinstall_webcodex",
            bridge_version,
        );
    }
    let mut command = Command::new(node);
    command
        .arg(&readiness)
        .arg("--json")
        .arg("--timeout-ms")
        .arg(NATIVE_PROBE_TIMEOUT_MS)
        .env_clear();
    for key in [
        "HOME",
        "PATH",
        "CODEX_BIN",
        "CODEX_HOME",
        "TMPDIR",
        "TMP",
        "TEMP",
    ] {
        if let Some(value) = std::env::var_os(key).filter(|value| !value.is_empty()) {
            command.env(key, value);
        }
    }
    #[cfg(windows)]
    if let Some(value) = std::env::var_os("SYSTEMROOT").filter(|value| !value.is_empty()) {
        command.env("SYSTEMROOT", value);
    }
    let Ok(output) = command.output() else {
        return component(
            "unavailable",
            "native_context_probe_failed",
            "desktop",
            "native_context_only",
            "reinstall_webcodex",
            bridge_version,
        );
    };
    if !output.status.success() || output.stdout.len() > 64 * 1024 {
        return component(
            "unavailable",
            "native_context_probe_failed",
            "desktop",
            "native_context_only",
            "reinstall_webcodex",
            bridge_version,
        );
    }
    let Ok(probe) = serde_json::from_slice::<NativeProbe>(&output.stdout) else {
        return component(
            "unavailable",
            "native_context_probe_invalid",
            "desktop",
            "native_context_only",
            "reinstall_webcodex",
            bridge_version,
        );
    };
    if probe.native_model_turns != 0 || probe.source_version != bridge_version {
        return component(
            "unavailable",
            "native_context_probe_version_mismatch",
            "desktop",
            "native_context_only",
            "reinstall_webcodex",
            bridge_version,
        );
    }
    if !matches!(probe.status.as_str(), "ready" | "degraded" | "unavailable") {
        return component(
            "unavailable",
            "native_context_probe_invalid",
            "desktop",
            "native_context_only",
            "reinstall_webcodex",
            bridge_version,
        );
    }
    EnhancedRuntimeComponentSnapshot {
        status: probe.status,
        reason: probe.reason,
        owner: probe.owner,
        impact: probe.impact,
        next_action: probe.next_action,
        observed_at_ms: probe.observed_at_ms,
        source_version: probe.source_version,
    }
}

fn vue_toolchain_root() -> Option<PathBuf> {
    let home = std::env::var_os("HOME").filter(|value| !value.is_empty())?;
    Some(
        PathBuf::from(home)
            .join("Library/Application Support/dev.webcodex.desktop/local-tools")
            .join(format!("vue-lsp-{VUE_VERSION}")),
    )
}

fn vue_lsp_snapshot() -> EnhancedRuntimeComponentSnapshot {
    let expected_version = format!("vue-{VUE_VERSION}/typescript-{TYPESCRIPT_VERSION}");
    let root = vue_toolchain_root();
    let server = std::env::var_os("WEBCODEX_VUE_LANGUAGE_SERVER")
        .filter(|value| !value.is_empty())
        .map(PathBuf::from)
        .or_else(|| root.as_ref().map(|root| root.join("bin/vue-language-server")));
    let tsdk = std::env::var_os("WEBCODEX_VUE_TSDK")
        .filter(|value| !value.is_empty())
        .map(PathBuf::from)
        .or_else(|| {
            root.as_ref()
                .map(|root| root.join("node_modules/typescript/lib"))
        });
    let (Some(server), Some(tsdk)) = (server, tsdk) else {
        return component(
            "unavailable",
            "vue_toolchain_missing",
            "vue_toolchain",
            "vue_lsp_only",
            "install_vue_lsp_toolchain",
            expected_version,
        );
    };
    let (Ok(server), Ok(tsdk)) = (server.canonicalize(), tsdk.canonicalize()) else {
        return component(
            "unavailable",
            "vue_toolchain_missing",
            "vue_toolchain",
            "vue_lsp_only",
            "install_vue_lsp_toolchain",
            expected_version,
        );
    };
    if !server.is_file() || !tsdk.is_dir() {
        return component(
            "unavailable",
            "vue_toolchain_missing",
            "vue_toolchain",
            "vue_lsp_only",
            "install_vue_lsp_toolchain",
            expected_version,
        );
    }
    let Some(tool_root) = server.parent().and_then(Path::parent) else {
        return component(
            "unavailable",
            "vue_toolchain_drift",
            "vue_toolchain",
            "vue_lsp_only",
            "repair_vue_lsp_toolchain",
            expected_version,
        );
    };
    let vue_package = tool_root.join("node_modules/@vue/language-server/package.json");
    let ts_package = tsdk
        .parent()
        .map(|root| root.join("package.json"))
        .unwrap_or_default();
    let vue_version = package_version(&vue_package);
    let ts_version = package_version(&ts_package);
    let wrapper_version = Command::new(&server)
        .arg("--version")
        .output()
        .ok()
        .filter(|output| output.status.success())
        .and_then(|output| String::from_utf8(output.stdout).ok())
        .map(|value| value.trim().to_string());
    if vue_version.as_deref() != Some(VUE_VERSION)
        || ts_version.as_deref() != Some(TYPESCRIPT_VERSION)
        || wrapper_version.as_deref() != Some(VUE_VERSION)
    {
        return component(
            "unavailable",
            "vue_toolchain_drift",
            "vue_toolchain",
            "vue_lsp_only",
            "repair_vue_lsp_toolchain",
            expected_version,
        );
    }
    component(
        "ready",
        "vue_toolchain_external_ready",
        "vue_toolchain",
        "vue_lsp_only",
        "none",
        expected_version,
    )
}

pub(crate) fn snapshot(resource_dir: &Path) -> EnhancedRuntimeSnapshot {
    let tools = resource_dir.join("webcodex-tools");
    let node = tools.join("node/node");
    let bridge = tools.join("codex-context-bridge");

    let node_version = if bundled_regular_file(&node) {
        Command::new(&node)
            .arg("--version")
            .output()
            .ok()
            .filter(|output| output.status.success())
            .and_then(|output| String::from_utf8(output.stdout).ok())
            .map(|value| value.trim().to_string())
    } else {
        None
    };
    let bundled_node = if let Some(version) = node_version {
        component(
            "ready",
            "bundled_node_ready",
            "desktop",
            "native_context_only",
            "none",
            version,
        )
    } else {
        component(
            "unavailable",
            "bundled_node_missing",
            "desktop",
            "native_context_only",
            "reinstall_webcodex",
            "unknown",
        )
    };

    let bridge_version = if bundled_regular_dir(&bridge)
        && [
            "bridge-lib.mjs",
            "readiness.mjs",
            "package.json",
            "server.mjs",
            "self-check.mjs",
        ]
        .iter()
        .all(|name| bundled_regular_file(&bridge.join(name)))
    {
        package_version(&bridge.join("package.json"))
    } else {
        None
    };
    let bundled_context_bridge = if let Some(version) = bridge_version.as_deref() {
        component(
            "ready",
            "bundled_bridge_ready",
            "desktop",
            "native_context_only",
            "none",
            version,
        )
    } else {
        component(
            "unavailable",
            "bundled_bridge_missing",
            "desktop",
            "native_context_only",
            "reinstall_webcodex",
            "unknown",
        )
    };

    let native_context =
        if bundled_node.status == "ready" && bundled_context_bridge.status == "ready" {
            native_probe(&node, &bridge, bridge_version.as_deref().unwrap_or("unknown"))
        } else if bundled_node.status != "ready" {
            component(
                "unavailable",
                "bundled_node_missing",
                "desktop",
                "native_context_only",
                "reinstall_webcodex",
                bridge_version.unwrap_or_else(|| "unknown".to_string()),
            )
        } else {
            component(
                "unavailable",
                "bundled_bridge_missing",
                "desktop",
                "native_context_only",
                "reinstall_webcodex",
                "unknown",
            )
        };

    EnhancedRuntimeSnapshot {
        bundled_node,
        bundled_context_bridge,
        native_context,
        vue_lsp: vue_lsp_snapshot(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn missing_bundle_is_truthfully_unavailable() {
        let temp = tempfile::tempdir().unwrap();
        let snapshot = snapshot(temp.path());
        assert_eq!(snapshot.bundled_node.status, "unavailable");
        assert_eq!(snapshot.bundled_context_bridge.status, "unavailable");
        assert_eq!(snapshot.native_context.status, "unavailable");
        assert_eq!(snapshot.native_context.reason, "bundled_node_missing");
    }
}
