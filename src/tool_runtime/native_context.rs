//! Optional zero-quota projection of native Codex context for coding startup.
//!
//! This module never starts a Codex model turn. It can only call a configured
//! Runner-local Context Bridge through the schema-bound MCP gateway after the
//! Project Runner and Workflow Session have already been resolved.

use crate::auth::AuthContext;
use crate::json_measurement::serialized_json_len;
use crate::mcp_gateway::{self, InternalMcpCallFailure, McpGatewayToolResult};
use serde_json::{json, Map, Value};
use sha2::{Digest, Sha256};
use std::collections::{HashMap, HashSet};
use unicase::UniCase;

use super::{ReadFilesItem, ResolvedProject, ToolResult, ToolRuntime};

const DEFAULT_NATIVE_CONTEXT_PROVIDER: &str = "codex_context";
const NATIVE_CONTEXT_PROVIDER_ENV: &str = "WEBCODEX_CODEX_CONTEXT_PROVIDER";
const NATIVE_CONTEXT_TOOL: &str = "bootstrap_context";
const NATIVE_SKILL_LIST_TOOL: &str = "list_native_skills";
const NATIVE_SKILL_READ_TOOL: &str = "read_native_skill";
const NATIVE_KNOWLEDGE_RESOLVE_TOOL: &str = "resolve_project_knowledge";
const MAX_NATIVE_SKILL_TEXT_BYTES: usize = 48 * 1024;
const MAX_NATIVE_SKILL_CANDIDATES: usize = 8;
const MAX_NATIVE_KNOWLEDGE_KEYS: usize = 32;
pub(crate) const STARTUP_NATIVE_CONTEXT_MAX_BYTES: usize = 4 * 1024;
const MAX_SKILL_ENTRIES: usize = 12;
const MAX_SKILL_DESCRIPTION_CHARS: usize = 180;
const MAX_HOOK_ENTRIES: usize = 8;
const MAX_KNOWLEDGE_ENTRIES: usize = 12;
const MAX_LIFECYCLE_RESULTS: usize = 4;
const MAX_HOOK_CONTEXT_CHARS: usize = 512;

impl ToolRuntime {
    pub(crate) async fn native_skill_load(
        &self,
        project: &ResolvedProject,
        name: String,
        native_skill_id: Option<String>,
        auth: Option<&AuthContext>,
    ) -> ToolResult {
        let name = match validate_native_skill_name(name) {
            Ok(name) => name,
            Err(reason) => return native_context_tool_error(&project.resolved_id, reason, None),
        };
        let requested_id = match native_skill_id {
            Some(value) => match validate_native_skill_id(value) {
                Ok(value) => Some(value),
                Err(reason) => {
                    return native_context_tool_error(&project.resolved_id, reason, None)
                }
            },
            None => None,
        };
        let catalog = match self
            .call_native_context_bridge(
                project,
                NATIVE_SKILL_LIST_TOOL,
                json!({"project_root": project.config.path, "force_reload": false}),
                auth,
            )
            .await
        {
            Ok(value) => value,
            Err(result) => return result,
        };
        let skills = catalog
            .get("skills")
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default();
        let mut matches = skills
            .iter()
            .filter_map(native_skill_candidate)
            .filter(|candidate| {
                UniCase::new(candidate.name.as_str()) == UniCase::new(name.as_str())
            })
            .collect::<Vec<_>>();
        matches.sort_by(|left, right| left.native_skill_id.cmp(&right.native_skill_id));
        if matches.is_empty() {
            return native_context_tool_error(&project.resolved_id, "native_skill_not_found", None);
        }
        let selected = if let Some(requested_id) = requested_id.as_deref() {
            let Some(candidate) = matches
                .iter()
                .find(|candidate| candidate.native_skill_id == requested_id)
                .cloned()
            else {
                return native_context_tool_error(
                    &project.resolved_id,
                    "native_skill_id_mismatch",
                    Some(json!({
                        "candidate_count": matches.len(),
                        "candidates": native_skill_candidate_projection(&matches),
                    })),
                );
            };
            candidate
        } else if matches.len() == 1 {
            matches.remove(0)
        } else {
            return native_context_tool_error(
                &project.resolved_id,
                "native_skill_name_ambiguous",
                Some(json!({
                    "candidate_count": matches.len(),
                    "candidates": native_skill_candidate_projection(&matches),
                    "candidates_truncated": matches.len() > MAX_NATIVE_SKILL_CANDIDATES,
                })),
            );
        };
        let raw = match self
            .call_native_context_bridge(
                project,
                NATIVE_SKILL_READ_TOOL,
                json!({
                    "project_root": project.config.path,
                    "skill_path": selected.path,
                }),
                auth,
            )
            .await
        {
            Ok(value) => value,
            Err(result) => return result,
        };
        let text = raw.get("text").and_then(Value::as_str).unwrap_or_default();
        let (text, truncated) = bounded_utf8_bytes(text, MAX_NATIVE_SKILL_TEXT_BYTES);
        let skill = raw.get("skill").unwrap_or(&Value::Null);
        let file = raw.get("file").unwrap_or(&Value::Null);
        ToolResult::ok(json!({
            "project": project.resolved_id,
            "native_skill_id": selected.native_skill_id,
            "name": skill.get("name").and_then(Value::as_str).unwrap_or(selected.name.as_str()),
            "description": skill.get("description").and_then(Value::as_str).map(|value| bounded_chars(value, 512)),
            "scope": skill.get("scope").and_then(Value::as_str).or(selected.scope.as_deref()),
            "enabled": skill.get("enabled").and_then(Value::as_bool),
            "plugin_id": skill.get("pluginId").and_then(Value::as_str).or(selected.plugin_id.as_deref()),
            "resource": raw.get("resource").and_then(Value::as_str).unwrap_or("SKILL.md"),
            "sha256": file.get("sha256").and_then(Value::as_str),
            "bytes": file.get("bytes").and_then(Value::as_u64),
            "text": text,
            "truncated": truncated,
            "state_changed": false,
        }))
    }

    pub(crate) async fn native_knowledge_load(
        &self,
        project: &ResolvedProject,
        key: String,
        start_line: Option<usize>,
        limit: Option<usize>,
        auth: Option<&AuthContext>,
    ) -> ToolResult {
        let key = match validate_native_knowledge_key(key) {
            Ok(key) => key,
            Err(reason) => return native_context_tool_error(&project.resolved_id, reason, None),
        };
        let resolved = match self
            .call_native_context_bridge(
                project,
                NATIVE_KNOWLEDGE_RESOLVE_TOOL,
                json!({"project_root": project.config.path}),
                auth,
            )
            .await
        {
            Ok(value) => value,
            Err(result) => return result,
        };
        let entries = resolved
            .get("entries")
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default();
        let Some(entry) = entries
            .iter()
            .find(|entry| entry.get("key").and_then(Value::as_str) == Some(key.as_str()))
        else {
            let available_keys = entries
                .iter()
                .filter_map(|entry| entry.get("key").and_then(Value::as_str))
                .take(MAX_NATIVE_KNOWLEDGE_KEYS)
                .collect::<Vec<_>>();
            return native_context_tool_error(
                &project.resolved_id,
                "native_knowledge_key_not_found",
                Some(json!({"available_keys": available_keys})),
            );
        };
        if entry.get("inside_project").and_then(Value::as_bool) != Some(true)
            || entry.get("exists").and_then(Value::as_bool) != Some(true)
        {
            return native_context_tool_error(
                &project.resolved_id,
                "native_knowledge_entry_unavailable",
                None,
            );
        }
        let Some(entry_file) = entry.get("entry_file").and_then(Value::as_str) else {
            return native_context_tool_error(
                &project.resolved_id,
                "native_knowledge_entry_file_unavailable",
                None,
            );
        };
        let Some(relative_file) = project_relative_bridge_path(&project.config.path, entry_file)
        else {
            return native_context_tool_error(
                &project.resolved_id,
                "native_knowledge_entry_outside_project",
                None,
            );
        };
        let read = self
            .read_files_resolved(
                project,
                vec![ReadFilesItem {
                    path: relative_file,
                    start_line,
                    limit,
                    expected_read_revision: None,
                }],
                Some(false),
            )
            .await;
        if !read.success {
            return native_context_tool_error(
                &project.resolved_id,
                "native_knowledge_read_failed",
                None,
            );
        }
        let Some(output) = read.output.pointer("/items/0/output") else {
            return native_context_tool_error(
                &project.resolved_id,
                "native_knowledge_read_invalid",
                None,
            );
        };
        let manifest_sha256 = resolved.pointer("/manifest/sha256").and_then(Value::as_str);
        let expected_sha256 = entry.get("entry_sha256").and_then(Value::as_str);
        let observed_sha256 = output.get("sha256").and_then(Value::as_str);
        if expected_sha256.is_some() && observed_sha256 != expected_sha256 {
            return native_context_tool_error(
                &project.resolved_id,
                "native_knowledge_changed",
                Some(json!({
                    "key": key,
                    "expected_sha256": expected_sha256,
                    "observed_sha256": observed_sha256,
                })),
            );
        }
        ToolResult::ok(json!({
            "project": project.resolved_id,
            "key": key,
            "manifest_sha256": manifest_sha256,
            "entry_sha256": observed_sha256.or(expected_sha256),
            "text": output.get("text").and_then(Value::as_str).unwrap_or_default(),
            "start_line": output.get("start_line"),
            "end_line": output.get("end_line"),
            "total_lines": output.get("total_lines"),
            "returned_lines": output.get("returned_lines"),
            "has_more": output.get("has_more"),
            "next_start_line": output.get("next_start_line"),
            "state_changed": false,
        }))
    }

    async fn call_native_context_bridge(
        &self,
        project: &ResolvedProject,
        tool_name: &str,
        arguments: Value,
        auth: Option<&AuthContext>,
    ) -> Result<Value, ToolResult> {
        let provider = native_context_provider_id()
            .map_err(|reason| native_context_tool_error(&project.resolved_id, reason, None))?;
        match mcp_gateway::call_tool_on_runner(
            self,
            &project.config.client_id,
            &provider,
            tool_name,
            arguments,
            auth,
        )
        .await
        {
            Ok(result) if !result.is_error => result.structured_content.ok_or_else(|| {
                native_context_tool_error(
                    &project.resolved_id,
                    "native_context_result_unstructured",
                    None,
                )
            }),
            Ok(result) => {
                let reason = result
                    .structured_content
                    .as_ref()
                    .and_then(|value| value.pointer("/error/code"))
                    .and_then(Value::as_str)
                    .filter(|value| valid_reason_code(value))
                    .unwrap_or("native_context_bridge_error");
                Err(native_context_tool_error(
                    &project.resolved_id,
                    reason,
                    None,
                ))
            }
            Err(failure) => Err(native_context_tool_error(
                &project.resolved_id,
                &failure.reason_code,
                failure
                    .dispatch_state
                    .as_deref()
                    .map(|dispatch_state| json!({"dispatch_state": dispatch_state})),
            )),
        }
    }

    pub(crate) async fn native_context_for_coding_startup(
        &self,
        project: &ResolvedProject,
        prompt: &str,
        resume_requested: bool,
        auth: Option<&AuthContext>,
    ) -> Value {
        let provider = match native_context_provider_id() {
            Ok(provider) => provider,
            Err(reason_code) => {
                return unavailable_native_context(
                    DEFAULT_NATIVE_CONTEXT_PROVIDER,
                    reason_code,
                    Some("not_started"),
                )
            }
        };
        let phase = if resume_requested {
            "resume"
        } else {
            "startup"
        };
        let arguments = json!({
            "project_root": project.config.path,
            "phase": phase,
            "prompt": prompt,
            "expected_fingerprint": Value::Null,
        });
        match mcp_gateway::call_tool_on_runner(
            self,
            &project.config.client_id,
            &provider,
            NATIVE_CONTEXT_TOOL,
            arguments,
            auth,
        )
        .await
        {
            Ok(result) => project_bridge_result(&provider, result),
            Err(failure) => project_bridge_failure(&provider, failure),
        }
    }
}

fn native_context_provider_id() -> Result<String, &'static str> {
    let provider = std::env::var(NATIVE_CONTEXT_PROVIDER_ENV)
        .unwrap_or_else(|_| DEFAULT_NATIVE_CONTEXT_PROVIDER.to_string());
    if mcp_gateway::validate_provider_id(&provider).is_err() {
        return Err("native_context_provider_invalid");
    }
    Ok(provider)
}

fn project_bridge_failure(provider: &str, failure: InternalMcpCallFailure) -> Value {
    unavailable_native_context(
        provider,
        &failure.reason_code,
        failure.dispatch_state.as_deref(),
    )
}

fn project_bridge_result(provider: &str, result: McpGatewayToolResult) -> Value {
    if result.is_error {
        let reason = result
            .structured_content
            .as_ref()
            .and_then(|value| value.pointer("/error/code"))
            .and_then(Value::as_str)
            .filter(|value| valid_reason_code(value))
            .unwrap_or("native_context_bridge_error");
        return unavailable_native_context(provider, reason, Some("completed"));
    }
    let Some(raw) = result.structured_content.as_ref() else {
        return unavailable_native_context(
            provider,
            "native_context_result_unstructured",
            Some("completed"),
        );
    };
    project_native_context(provider, raw)
}

fn unavailable_native_context(
    provider: &str,
    reason_code: &str,
    dispatch_state: Option<&str>,
) -> Value {
    let status = if dispatch_state == Some("outcome_unknown") {
        "uncertain"
    } else {
        "unavailable"
    };
    let mut value = json!({
        "status": status,
        "provider": provider,
        "reason_code": reason_code,
        "orchestration_scope": "work_on_project",
        "host_lifecycle_intercept": false,
    });
    if let Some(dispatch_state) = dispatch_state {
        value["dispatch_state"] = Value::String(dispatch_state.to_string());
    }
    value
}

fn project_native_context(provider: &str, raw: &Value) -> Value {
    let hooks = raw
        .pointer("/native_hooks/hooks")
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default();
    let hook_plugins = hooks
        .iter()
        .filter_map(|hook| {
            Some((
                hook.get("key")?.as_str()?.to_string(),
                hook.get("plugin_id")
                    .and_then(Value::as_str)
                    .map(str::to_string),
            ))
        })
        .collect::<HashMap<_, _>>();

    let mut projection = json!({
        "status": "available",
        "provider": provider,
        "orchestration_scope": "work_on_project",
        "host_lifecycle_intercept": false,
        "bridge_version": string_field(raw, "bridge_version"),
        "fingerprint": string_field(raw, "fingerprint"),
        "stale": raw.get("stale").and_then(Value::as_bool),
        "ponytail": {
            "mode": raw.pointer("/ponytail/mode").and_then(Value::as_str),
        },
        "skills": project_skills(raw),
        "hooks": project_hooks(raw),
        "knowledge": project_knowledge(raw),
        "lifecycle": {
            "session_start": project_hook_dispatch(raw.pointer("/lifecycle/session_start"), &hook_plugins),
            "user_prompt_submit": project_hook_dispatch(raw.pointer("/lifecycle/user_prompt_submit"), &hook_plugins),
        },
    });
    trim_native_context(&mut projection);
    projection
}

fn project_skills(raw: &Value) -> Value {
    let native = raw.get("native_skills").unwrap_or(&Value::Null);
    let total_count = native.get("count").and_then(Value::as_u64).unwrap_or(0);
    let direct = native
        .get("direct_user_skills")
        .and_then(Value::as_array)
        .into_iter()
        .flatten();
    let catalog = native
        .get("catalog")
        .and_then(Value::as_array)
        .into_iter()
        .flatten();
    let mut seen = HashSet::new();
    let mut entries = Vec::new();
    for skill in direct.chain(catalog) {
        let Some(name) = skill.get("name").and_then(Value::as_str) else {
            continue;
        };
        if !seen.insert(name.to_string()) {
            continue;
        }
        if entries.len() >= MAX_SKILL_ENTRIES {
            break;
        }
        let Some(path) = skill.get("path").and_then(Value::as_str) else {
            continue;
        };
        let mut entry = Map::new();
        entry.insert(
            "native_skill_id".to_string(),
            Value::String(native_skill_id(path)),
        );
        entry.insert("name".to_string(), Value::String(name.to_string()));
        if let Some(description) = skill.get("description").and_then(Value::as_str) {
            entry.insert(
                "description".to_string(),
                Value::String(bounded_chars(description, MAX_SKILL_DESCRIPTION_CHARS)),
            );
        }
        if let Some(enabled) = skill.get("enabled").and_then(Value::as_bool) {
            entry.insert("enabled".to_string(), Value::Bool(enabled));
        }
        if let Some(plugin_id) = skill.get("plugin_id").and_then(Value::as_str) {
            entry.insert(
                "plugin_id".to_string(),
                Value::String(plugin_id.to_string()),
            );
        }
        entries.push(Value::Object(entry));
    }
    let returned_count = entries.len();
    json!({
        "total_count": total_count,
        "returned_count": returned_count,
        "truncated": total_count as usize > returned_count,
        "entries": entries,
        "selection_hint": "Use names/descriptions as routing hints. When a native Skill matches, call native_skill_load with its name; on same-name ambiguity retry with the returned opaque native_skill_id. Never request or infer a native path.",
    })
}

fn project_hooks(raw: &Value) -> Value {
    let source = raw
        .pointer("/native_hooks/hooks")
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default();
    let total_count = raw
        .pointer("/native_hooks/count")
        .and_then(Value::as_u64)
        .unwrap_or(source.len() as u64);
    let entries = source
        .iter()
        .take(MAX_HOOK_ENTRIES)
        .filter_map(|hook| {
            let event_name = hook.get("event_name")?.as_str()?;
            let mut entry = Map::new();
            entry.insert(
                "event_name".to_string(),
                Value::String(event_name.to_string()),
            );
            for field in ["plugin_id", "trust_status"] {
                if let Some(value) = hook.get(field).and_then(Value::as_str) {
                    entry.insert(field.to_string(), Value::String(value.to_string()));
                }
            }
            if let Some(enabled) = hook.get("enabled").and_then(Value::as_bool) {
                entry.insert("enabled".to_string(), Value::Bool(enabled));
            }
            Some(Value::Object(entry))
        })
        .collect::<Vec<_>>();
    json!({
        "total_count": total_count,
        "returned_count": entries.len(),
        "truncated": total_count as usize > entries.len(),
        "entries": entries,
    })
}

fn project_knowledge(raw: &Value) -> Value {
    let knowledge = raw.get("knowledge").unwrap_or(&Value::Null);
    let manifest = knowledge.get("manifest");
    let manifest_present = manifest.is_some_and(|value| !value.is_null());
    let manifest_sha256 = manifest
        .and_then(|value| value.get("sha256"))
        .and_then(Value::as_str);
    let mut keys = knowledge
        .get("knowledge_paths")
        .and_then(Value::as_object)
        .map(|paths| paths.keys().cloned().collect::<Vec<_>>())
        .unwrap_or_default();
    keys.sort();
    keys.truncate(MAX_KNOWLEDGE_ENTRIES);

    let entries = knowledge
        .get("entries")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .take(MAX_KNOWLEDGE_ENTRIES)
        .filter_map(|entry| {
            let key = entry.get("key")?.as_str()?;
            let exists = entry
                .get("exists")
                .and_then(Value::as_bool)
                .or_else(|| entry.get("file").map(|file| !file.is_null()))
                .unwrap_or(false);
            let mut projected = Map::new();
            projected.insert("key".to_string(), Value::String(key.to_string()));
            projected.insert("exists".to_string(), Value::Bool(exists));
            if let Some(sha256) = entry
                .get("entry_sha256")
                .and_then(Value::as_str)
                .or_else(|| entry.pointer("/file/sha256").and_then(Value::as_str))
                .or_else(|| entry.get("sha256").and_then(Value::as_str))
            {
                projected.insert("sha256".to_string(), Value::String(sha256.to_string()));
            }
            Some(Value::Object(projected))
        })
        .collect::<Vec<_>>();

    let mut result = json!({
        "manifest_present": manifest_present,
        "keys": keys,
        "entries": entries,
        "selection_hint": "Use native_knowledge_load with one semantic key. Never request or infer the manifest path or entry path.",
    });
    if let Some(sha256) = manifest_sha256 {
        result["manifest_sha256"] = Value::String(sha256.to_string());
    }
    result
}

fn project_hook_dispatch(
    dispatch: Option<&Value>,
    hook_plugins: &HashMap<String, Option<String>>,
) -> Value {
    let Some(dispatch) = dispatch.filter(|dispatch| !dispatch.is_null()) else {
        return json!({"status": "not_run"});
    };
    let results_source = dispatch
        .get("results")
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default();
    let results = results_source
        .iter()
        .take(MAX_LIFECYCLE_RESULTS)
        .map(|result| {
            let mut projected = Map::new();
            if let Some(event_name) = result.get("event_name").and_then(Value::as_str) {
                projected.insert(
                    "event_name".to_string(),
                    Value::String(event_name.to_string()),
                );
            }
            if let Some(key) = result.get("key").and_then(Value::as_str) {
                if let Some(Some(plugin_id)) = hook_plugins.get(key) {
                    projected.insert("plugin_id".to_string(), Value::String(plugin_id.clone()));
                }
            }
            if let Some(code) = result.get("code").and_then(Value::as_i64) {
                projected.insert("code".to_string(), json!(code));
            }
            if let Some(timed_out) = result.get("timed_out").and_then(Value::as_bool) {
                projected.insert("timed_out".to_string(), Value::Bool(timed_out));
            }
            if let Some(context) = result
                .get("additional_context")
                .or_else(|| result.get("additionalContext"))
                .and_then(Value::as_str)
                .filter(|value| !value.is_empty())
            {
                projected.insert(
                    "additional_context".to_string(),
                    Value::String(bounded_chars(context, MAX_HOOK_CONTEXT_CHARS)),
                );
            }
            Value::Object(projected)
        })
        .collect::<Vec<_>>();
    json!({
        "status": "completed",
        "event_name": dispatch.get("event_name").and_then(Value::as_str),
        "result_count": results_source.len(),
        "returned_count": results.len(),
        "results_truncated": results_source.len() > results.len(),
        "results": results,
        "skipped_count": dispatch.get("skipped").and_then(Value::as_array).map_or(0, Vec::len),
        "warning_count": dispatch.get("warnings").and_then(Value::as_array).map_or(0, Vec::len),
        "error_count": dispatch.get("errors").and_then(Value::as_array).map_or(0, Vec::len),
    })
}

fn trim_native_context(value: &mut Value) {
    loop {
        if serialized_json_len(value)
            .map(|bytes| bytes <= STARTUP_NATIVE_CONTEXT_MAX_BYTES)
            .unwrap_or(false)
        {
            return;
        }
        if pop_array(value, "/skills/entries", 3) {
            value["skills"]["returned_count"] = json!(value
                .pointer("/skills/entries")
                .and_then(Value::as_array)
                .map_or(0, Vec::len));
            value["skills"]["truncated"] = Value::Bool(true);
            continue;
        }
        if pop_array(value, "/lifecycle/user_prompt_submit/results", 1) {
            value["lifecycle"]["user_prompt_submit"]["results_truncated"] = Value::Bool(true);
            continue;
        }
        if pop_array(value, "/lifecycle/session_start/results", 1) {
            value["lifecycle"]["session_start"]["results_truncated"] = Value::Bool(true);
            continue;
        }
        if pop_array(value, "/knowledge/entries", 0) || pop_array(value, "/knowledge/keys", 0) {
            continue;
        }
        if let Some(entries) = value
            .pointer_mut("/skills/entries")
            .and_then(Value::as_array_mut)
        {
            let mut removed = false;
            for entry in entries.iter_mut().rev() {
                if entry
                    .as_object_mut()
                    .is_some_and(|entry| entry.remove("description").is_some())
                {
                    removed = true;
                    break;
                }
            }
            if removed {
                continue;
            }
        }
        return;
    }
}

fn pop_array(value: &mut Value, pointer: &str, floor: usize) -> bool {
    let Some(items) = value.pointer_mut(pointer).and_then(Value::as_array_mut) else {
        return false;
    };
    if items.len() <= floor {
        return false;
    }
    items.pop();
    true
}

fn string_field<'a>(value: &'a Value, field: &str) -> Option<&'a str> {
    value.get(field).and_then(Value::as_str)
}

fn bounded_chars(value: &str, max_chars: usize) -> String {
    if value.chars().count() <= max_chars {
        return value.to_string();
    }
    value.chars().take(max_chars).collect()
}

fn valid_reason_code(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 80
        && value
            .bytes()
            .all(|byte| byte.is_ascii_lowercase() || byte.is_ascii_digit() || byte == b'_')
}

#[derive(Clone, Debug)]
struct NativeSkillCandidate {
    native_skill_id: String,
    name: String,
    path: String,
    scope: Option<String>,
    plugin_id: Option<String>,
    description: Option<String>,
}

fn native_skill_candidate(skill: &Value) -> Option<NativeSkillCandidate> {
    let name = skill.get("name")?.as_str()?.to_string();
    let path = skill.get("path")?.as_str()?.to_string();
    Some(NativeSkillCandidate {
        native_skill_id: native_skill_id(&path),
        name,
        path,
        scope: skill
            .get("scope")
            .and_then(Value::as_str)
            .map(str::to_string),
        plugin_id: skill
            .get("plugin_id")
            .or_else(|| skill.get("pluginId"))
            .and_then(Value::as_str)
            .map(str::to_string),
        description: skill
            .get("description")
            .and_then(Value::as_str)
            .map(|value| bounded_chars(value, 512)),
    })
}

fn native_skill_candidate_projection(candidates: &[NativeSkillCandidate]) -> Vec<Value> {
    candidates
        .iter()
        .take(MAX_NATIVE_SKILL_CANDIDATES)
        .map(|candidate| {
            json!({
                "native_skill_id": candidate.native_skill_id,
                "name": candidate.name,
                "scope": candidate.scope,
                "plugin_id": candidate.plugin_id,
                "description": candidate.description,
            })
        })
        .collect()
}

fn native_skill_id(path: &str) -> String {
    let mut hasher = Sha256::new();
    hasher.update(b"webcodex-native-skill-v1\0");
    hasher.update(path.as_bytes());
    format!(
        "wc_nskill_{}",
        webcodex_core::compact::encode(&hasher.finalize()[..16])
    )
}

fn validate_native_skill_name(name: String) -> Result<String, &'static str> {
    if name.is_empty()
        || name.trim() != name
        || name.chars().count() > 96
        || name.chars().any(char::is_control)
    {
        return Err("native_skill_name_invalid");
    }
    Ok(name)
}

fn validate_native_skill_id(value: String) -> Result<String, &'static str> {
    let suffix = value.strip_prefix("wc_nskill_");
    if suffix.is_none_or(|suffix| {
        suffix.len() != 22
            || !suffix
                .bytes()
                .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'_' | b'-'))
    }) {
        return Err("native_skill_id_invalid");
    }
    Ok(value)
}

fn validate_native_knowledge_key(key: String) -> Result<String, &'static str> {
    if key.is_empty()
        || key.len() > 96
        || key.trim() != key
        || !key
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'_' | b'-' | b'.' | b':'))
    {
        return Err("native_knowledge_key_invalid");
    }
    Ok(key)
}

fn project_relative_bridge_path(project_root: &str, absolute_entry: &str) -> Option<String> {
    let root = project_root.replace('\\', "/");
    let entry = absolute_entry.replace('\\', "/");
    let root = root.trim_end_matches('/');
    let relative = entry.strip_prefix(&format!("{root}/"))?;
    if relative.is_empty()
        || relative.starts_with('/')
        || relative.split('/').any(|segment| segment == "..")
    {
        return None;
    }
    Some(relative.to_string())
}

fn bounded_utf8_bytes(value: &str, max_bytes: usize) -> (String, bool) {
    if value.len() <= max_bytes {
        return (value.to_string(), false);
    }
    let mut end = max_bytes;
    while end > 0 && !value.is_char_boundary(end) {
        end -= 1;
    }
    (value[..end].to_string(), true)
}

fn native_context_tool_error(project: &str, error_kind: &str, extra: Option<Value>) -> ToolResult {
    let mut output = json!({
        "project": project,
        "error_kind": error_kind,
        "state_changed": false,
    });
    if let (Some(output), Some(extra)) = (
        output.as_object_mut(),
        extra.and_then(|v| v.as_object().cloned()),
    ) {
        output.extend(extra);
    }
    ToolResult::err_with_output("Native Codex context operation failed", output)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn native_context_projection_is_path_safe_bounded_and_keeps_lifecycle_context() {
        let raw = json!({
            "bridge_version": "0.4.0",
            "project_root": "/Users/private/work",
            "codex_home": "/Users/private/.codex",
            "fingerprint": "sha256:abc",
            "stale": false,
            "ponytail": {"mode": "full", "path": "/Users/private/.codex/plugins/data/.ponytail-active"},
            "native_skills": {
                "count": 20,
                "direct_user_skills": (0..12).map(|index| json!({
                    "name": format!("skill-{index}"),
                    "description": format!("description-{index}-{}", "x".repeat(300)),
                    "path": format!("/Users/private/.codex/skills/{index}/SKILL.md"),
                    "enabled": true
                })).collect::<Vec<_>>(),
                "catalog": []
            },
            "native_hooks": {
                "count": 2,
                "hooks": [
                    {
                        "key": "/Users/private/.codex/hooks.json:session_start:0",
                        "event_name": "sessionStart",
                        "plugin_id": null,
                        "enabled": true,
                        "trust_status": "trusted",
                        "command": "SECRET COMMAND"
                    },
                    {
                        "key": "ponytail@ponytail:hooks/private.json:user_prompt_submit:0",
                        "event_name": "userPromptSubmit",
                        "plugin_id": "ponytail@ponytail",
                        "enabled": true,
                        "trust_status": "trusted"
                    }
                ]
            },
            "knowledge": {
                "manifest": {"path": "/Users/private/work/reuse-manifest.json", "sha256": "manifest-hash"},
                "knowledge_paths": {"l3_machine_entry": "/Users/private/work/L3", "design": "docs/design"},
                "entries": [{"key": "l3_machine_entry", "exists": true, "entry_file": "/Users/private/work/L3/README.md", "entry_sha256": "entry-hash"}]
            },
            "lifecycle": {
                "session_start": {
                    "event_name": "sessionStart",
                    "results": [{"key": "/Users/private/.codex/hooks.json:session_start:0", "code": 0, "timed_out": false, "stdout": "PRIVATE", "additional_context": "startup context"}],
                    "skipped": [], "warnings": [], "errors": []
                },
                "user_prompt_submit": {
                    "event_name": "userPromptSubmit",
                    "results": [{"key": "ponytail@ponytail:hooks/private.json:user_prompt_submit:0", "code": 0, "timed_out": false, "stderr": "PRIVATE", "additional_context": "PONYTAIL:FULL"}],
                    "skipped": [], "warnings": [], "errors": []
                }
            }
        });

        let projected = project_native_context("codex_context", &raw);
        let encoded = serde_json::to_string(&projected).unwrap();
        assert_eq!(projected["status"], "available");
        assert_eq!(projected["ponytail"]["mode"], "full");
        assert_eq!(projected["knowledge"]["entries"][0]["sha256"], "entry-hash");
        assert_eq!(
            projected["lifecycle"]["user_prompt_submit"]["results"][0]["additional_context"],
            "PONYTAIL:FULL"
        );
        assert_eq!(
            projected["lifecycle"]["user_prompt_submit"]["results"][0]["plugin_id"],
            "ponytail@ponytail"
        );
        assert!(projected["skills"]["truncated"].as_bool().unwrap());
        assert!(serialized_json_len(&projected).unwrap() <= STARTUP_NATIVE_CONTEXT_MAX_BYTES);
        assert!(!encoded.contains("/Users/private"));
        assert!(!encoded.contains("SECRET COMMAND"));
        assert!(!encoded.contains("\"stdout\""));
        assert!(!encoded.contains("\"stderr\""));
    }

    #[test]
    fn native_skill_identity_and_candidates_never_expose_native_paths() {
        let private_path = "/Users/private/.codex/skills/demo/SKILL.md";
        let id = native_skill_id(private_path);
        assert!(id.starts_with("wc_nskill_"));
        assert_eq!(id.len(), "wc_nskill_".len() + 22);
        assert!(!id.contains("Users"));
        assert_eq!(id, native_skill_id(private_path));

        let candidate = native_skill_candidate(&json!({
            "name": "demo",
            "description": "private demo",
            "path": private_path,
            "scope": "user",
            "plugin_id": null
        }))
        .unwrap();
        let projected = native_skill_candidate_projection(&[candidate]);
        let encoded = serde_json::to_string(&projected).unwrap();
        assert!(encoded.contains("wc_nskill_"));
        assert!(!encoded.contains(private_path));
        assert!(!encoded.contains("/Users/private"));
    }

    #[test]
    fn project_relative_knowledge_path_is_strictly_inside_project() {
        assert_eq!(
            project_relative_bridge_path(
                "/Users/demo/project",
                "/Users/demo/project/L3/04-machine/llms.txt"
            ),
            Some("L3/04-machine/llms.txt".to_string())
        );
        assert_eq!(
            project_relative_bridge_path(
                r"C:\Users\demo\project",
                r"C:\Users\demo\project\L3\llms.txt"
            ),
            Some("L3/llms.txt".to_string())
        );
        assert_eq!(
            project_relative_bridge_path(
                "/Users/demo/project",
                "/Users/demo/project-other/private.txt"
            ),
            None
        );
        assert_eq!(
            project_relative_bridge_path(
                "/Users/demo/project",
                "/Users/demo/project/../private.txt"
            ),
            None
        );
    }

    #[test]
    fn native_skill_text_byte_bound_preserves_utf8() {
        let input = "你".repeat(32);
        let (bounded, truncated) = bounded_utf8_bytes(&input, 17);
        assert!(truncated);
        assert!(bounded.len() <= 17);
        assert!(std::str::from_utf8(bounded.as_bytes()).is_ok());
        assert_eq!(bounded, "你".repeat(5));
    }

    #[test]
    fn uncertain_gateway_failure_never_claims_hook_non_execution() {
        let projected = project_bridge_failure(
            "codex_context",
            InternalMcpCallFailure {
                reason_code: "gateway_timeout".to_string(),
                dispatch_state: Some("outcome_unknown".to_string()),
            },
        );
        assert_eq!(projected["status"], "uncertain");
        assert_eq!(projected["dispatch_state"], "outcome_unknown");
        assert_eq!(projected["host_lifecycle_intercept"], false);
    }
}
