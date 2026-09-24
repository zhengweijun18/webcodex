use serde_json::{json, Value};

use super::common::{
    array_schema, nullable_schema, schema_type, suggested_tool_call_schema, wrapped_output_schema,
};
use webcodex_core::skill_metadata::{MAX_SKILL_DESCRIPTION_CHARS, MAX_SKILL_NAME_CHARS};
use webcodex_core::skill_store::MAX_OPERATOR_SKILL_KEY_CHARS;

fn descriptor_schema() -> Value {
    json!({
        "type": "object",
        "properties": {
            "skill_id": {"type": "string", "pattern": "^wc_skill_[A-Za-z0-9_-]{21}[AQgw]$"},
            "name": {"type": "string", "maxLength": MAX_SKILL_NAME_CHARS},
            "description": {"type": "string", "maxLength": MAX_SKILL_DESCRIPTION_CHARS},
            "definition_revision": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
            "source_scope": {"type": "string", "enum": ["project", "runner"]},
            "trust": {"type": "string", "enum": ["project_content", "operator_configured_guidance", "operator_installed_guidance"]},
            "package_revision": {"anyOf": [{"type":"string","pattern":"^wc_skillpkg_[A-Za-z0-9_-]{43}$"},{"type":"null"}]},
            "name_conflict": {"type": "boolean"}
        },
        "required": ["skill_id", "name", "description", "definition_revision", "source_scope", "trust", "package_revision", "name_conflict"],
        "additionalProperties": false
    })
}

fn skill_load_candidate_schema() -> Value {
    json!({
        "type": "object",
        "properties": {
            "skill_id": {"type": "string", "pattern": "^wc_skill_[A-Za-z0-9_-]{21}[AQgw]$"},
            "name": {"type": "string", "maxLength": MAX_SKILL_NAME_CHARS},
            "source_scope": {"type": "string", "enum": ["project", "runner"]},
            "trust": {"type": "string", "enum": ["project_content", "operator_configured_guidance", "operator_installed_guidance"]},
            "package_revision": {"anyOf": [{"type":"string","pattern":"^wc_skillpkg_[A-Za-z0-9_-]{43}$"},{"type":"null"}]},
            "definition_revision": {"type": "string", "pattern": "^[0-9a-f]{64}$"}
        },
        "required": ["skill_id", "name", "source_scope", "trust", "package_revision", "definition_revision"],
        "additionalProperties": false
    })
}

fn skill_versions_recovery_call_schema() -> Value {
    suggested_tool_call_schema(
        "skill_versions",
        json!({
            "type": "object",
            "additionalProperties": false,
            "properties": {
                "project": {"type": "string", "minLength": 1},
                "skill_key": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": MAX_OPERATOR_SKILL_KEY_CHARS,
                    "pattern": "^[A-Za-z0-9._-]+$"
                }
            },
            "required": ["project", "skill_key"]
        }),
        "Parser-ready advisory skill_versions reconciliation using only the exact Project and logical Skill key owned by the failed mutation. On Adaptive Runtime this target remains a discovered call_runtime_tool gateway action; the call grants no authority and is not mutation retry permission.",
    )
}

fn apply_skill_recovery_contract(name: &str, schema: &mut Value) {
    let mutation = matches!(
        name,
        "skill_install" | "skill_activate" | "skill_remove_revision"
    );
    {
        let properties = schema["properties"]["output"]["properties"]
            .as_object_mut()
            .expect("wrapped Skill output properties");
        if mutation {
            properties.insert(
                "suggested_call".to_string(),
                skill_versions_recovery_call_schema(),
            );
            properties.insert(
                "reconcile_with".to_string(),
                json!({
                    "type": "string",
                    "const": "skill_versions",
                    "description": "Non-actionable reconciliation-family hint used only when a complete safe skill_versions invocation cannot be proven. It grants no authority."
                }),
            );
        }
    }
    let output_all_of = schema["properties"]["output"]
        .as_object_mut()
        .expect("wrapped Skill output schema")
        .entry("allOf".to_string())
        .or_insert_with(|| json!([]))
        .as_array_mut()
        .expect("Skill output allOf");
    output_all_of.push(json!({"not": {"required": ["recovery_tool"]}}));
    if mutation {
        output_all_of.extend([
            json!({
                "if": {"required": ["suggested_call"]},
                "then": {
                    "not": {"anyOf": [
                        {"required": ["reconcile_with"]},
                        {"required": ["recovery_kind"]}
                    ]}
                }
            }),
            json!({
                "if": {"required": ["reconcile_with"]},
                "then": {
                    "required": ["recovery_kind"],
                    "not": {"required": ["suggested_call"]},
                    "properties": {"recovery_kind": {"const": "reconcile"}}
                }
            }),
        ]);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn descriptor_trust_distinguishes_configured_and_managed_runner_guidance() {
        let schema = descriptor_schema();
        let values = schema["properties"]["trust"]["enum"]
            .as_array()
            .expect("trust enum");
        assert!(values.iter().any(|value| value == "project_content"));
        assert!(values
            .iter()
            .any(|value| value == "operator_configured_guidance"));
        assert!(values
            .iter()
            .any(|value| value == "operator_installed_guidance"));
    }

    #[test]
    fn skill_recovery_schemas_use_exact_call_or_family_only_without_legacy_alias() {
        for tool in [
            "skill_list",
            "skill_read_file",
            "skill_versions",
            "skill_install",
            "skill_activate",
            "skill_remove_revision",
        ] {
            let schema = output_schema_for_tool(tool).expect("Skill output schema");
            let properties = schema["properties"]["output"]["properties"]
                .as_object()
                .expect("Skill output properties");
            assert!(
                !properties.contains_key("recovery_tool"),
                "{tool} still publishes recovery_tool"
            );
            let all_of = schema["properties"]["output"]["allOf"]
                .as_array()
                .expect("Skill recovery constraints");
            assert!(all_of
                .iter()
                .any(|constraint| { constraint["not"]["required"] == json!(["recovery_tool"]) }));
        }

        for tool in ["skill_install", "skill_activate", "skill_remove_revision"] {
            let schema = output_schema_for_tool(tool).expect("Skill mutation output schema");
            let properties = schema["properties"]["output"]["properties"]
                .as_object()
                .expect("Skill mutation output properties");
            let suggested = &properties["suggested_call"];
            assert_eq!(suggested["properties"]["tool"]["const"], "skill_versions");
            assert_eq!(
                suggested["properties"]["arguments"]["required"],
                json!(["project", "skill_key"])
            );
            assert_eq!(
                suggested["properties"]["arguments"]["additionalProperties"],
                false
            );
            assert_eq!(properties["reconcile_with"]["const"], "skill_versions");
        }
    }
}

pub(super) fn output_schema_for_tool(name: &str) -> Option<Value> {
    let mut schema = match name {
        "skill_load" => Some(wrapped_output_schema(vec![
            ("project", schema_type("string", "Resolved Project id.")),
            (
                "catalog_revision",
                schema_type("string", "Digest of the freshly observed bounded Skill catalog used for exact-name selection."),
            ),
            ("skill_id", schema_type("string", "Opaque project-scoped Skill identity.")),
            ("name", schema_type("string", "Selected Skill metadata name.")),
            ("source_scope", schema_type("string", "Selected Skill source: project or runner.")),
            ("trust", schema_type("string", "Guidance trust label for the selected source; never execution authority.")),
            (
                "package_revision",
                nullable_schema("string", "Active immutable whole-package revision for runner-installed Skills; null for project/configured Skills."),
            ),
            ("definition_revision", schema_type("string", "Current SKILL.md content digest.")),
            ("path", schema_type("string", "Package-relative SKILL.md path.")),
            ("sha256", schema_type("string", "Full current SKILL.md SHA-256.")),
            ("text", schema_type("string", "Bounded UTF-8 SKILL.md text.")),
            ("start_line", schema_type("integer", "Effective 1-based selected start line.")),
            ("end_line", nullable_schema("integer", "Last returned line, or null when none.")),
            ("returned_lines", schema_type("integer", "Returned SKILL.md source lines.")),
            ("has_more", schema_type("boolean", "Whether SKILL.md lines remain.")),
            ("next_start_line", nullable_schema("integer", "Continuation line when has_more.")),
            ("descriptor", descriptor_schema()),
            ("candidate_count", json!({"type":"integer","minimum":2,"description":"Total case-fold-equivalent exact-name candidates when selection is ambiguous."})),
            (
                "candidates",
                {
                    let mut schema = array_schema(
                        skill_load_candidate_schema(),
                        "At most eight bounded exact-name ambiguity candidates.",
                    );
                    schema["maxItems"] = json!(8);
                    schema
                },
            ),
            ("candidates_truncated", schema_type("boolean", "Whether more than eight ambiguity candidates exist.")),
            ("discovery_truncated", schema_type("boolean", "True when bounded catalog discovery was incomplete and exact-name uniqueness could not be proven.")),
            ("error_kind", schema_type("string", "Stable guard/error code on failure.")),
            ("state_changed", schema_type("boolean", "Always false for Skill loading failures.")),
        ])),
        "native_skill_list" => Some(wrapped_output_schema(vec![
            ("project", schema_type("string", "Resolved Project id.")),
            ("query", nullable_schema("string", "Effective optional catalog query.")),
            ("total_count", schema_type("integer", "Native Skills matching the optional query.")),
            ("offset", schema_type("integer", "Zero-based filtered catalog offset.")),
            ("returned_count", schema_type("integer", "Pathless Skill descriptors returned in this page.")),
            ("entries", array_schema(
                json!({
                    "type":"object",
                    "properties":{
                        "native_skill_id":{"type":"string","pattern":"^wc_nskill_[A-Za-z0-9_-]{22}$"},
                        "name":{"type":"string","maxLength":96},
                        "scope":{"anyOf":[{"type":"string"},{"type":"null"}]},
                        "plugin_id":{"anyOf":[{"type":"string"},{"type":"null"}]},
                        "description":{"anyOf":[{"type":"string"},{"type":"null"}]}
                    },
                    "required":["native_skill_id","name","scope","plugin_id","description"],
                    "additionalProperties":false
                }),
                "Deterministically sorted pathless native Skill descriptors."
            )),
            ("next_offset", nullable_schema("integer", "Next zero-based offset, or null when complete.")),
            ("error_kind", schema_type("string", "Stable native Skill catalog guard/error code on failure.")),
            ("dispatch_state", schema_type("string", "Provider dispatch certainty when a gateway failure exposes it.")),
            ("state_changed", schema_type("boolean", "Always false for native Skill discovery.")),
        ])),
        "native_skill_load" => Some(wrapped_output_schema(vec![
            ("project", schema_type("string", "Resolved Project id.")),
            ("native_skill_id", schema_type("string", "Opaque pathless native Codex Skill identity.")),
            ("name", schema_type("string", "Selected native Skill name.")),
            ("description", nullable_schema("string", "Bounded native Skill description.")),
            ("scope", nullable_schema("string", "Native Codex Skill scope.")),
            ("enabled", nullable_schema("boolean", "Whether native Codex currently enables the Skill.")),
            ("plugin_id", nullable_schema("string", "Owning native Plugin id when present.")),
            ("resource", schema_type("string", "Loaded Skill resource name, normally SKILL.md.")),
            ("sha256", nullable_schema("string", "Loaded resource SHA-256 when reported by the bridge.")),
            ("bytes", nullable_schema("integer", "Loaded resource byte size when reported by the bridge.")),
            ("text", schema_type("string", "Bounded UTF-8 native Skill text page.")),
            ("start_line", schema_type("integer", "Effective 1-based start line.")),
            ("end_line", nullable_schema("integer", "Last returned line, or null for an empty source.")),
            ("total_lines", schema_type("integer", "Total SKILL.md source lines.")),
            ("returned_lines", schema_type("integer", "Returned complete source lines.")),
            ("has_more", schema_type("boolean", "Whether more SKILL.md lines remain.")),
            ("next_start_line", nullable_schema("integer", "Next 1-based line for pathless continuation.")),
            ("truncated", schema_type("boolean", "Compatibility alias for has_more.")),
            ("candidate_count", json!({"type":"integer","minimum":2,"description":"Exact-name candidate count when selection is ambiguous."})),
            ("candidates", {
                let mut schema = array_schema(
                    json!({
                        "type":"object",
                        "properties":{
                            "native_skill_id":{"type":"string","pattern":"^wc_nskill_[A-Za-z0-9_-]{22}$"},
                            "name":{"type":"string","maxLength":96},
                            "scope":{"anyOf":[{"type":"string"},{"type":"null"}]},
                            "plugin_id":{"anyOf":[{"type":"string"},{"type":"null"}]},
                            "description":{"anyOf":[{"type":"string"},{"type":"null"}]}
                        },
                        "required":["native_skill_id","name","scope","plugin_id","description"],
                        "additionalProperties":false
                    }),
                    "At most eight pathless exact-name native Skill ambiguity candidates.",
                );
                schema["maxItems"] = json!(8);
                schema
            }),
            ("candidates_truncated", schema_type("boolean", "Whether more than eight ambiguity candidates exist.")),
            ("error_kind", schema_type("string", "Stable native Skill guard/error code on failure.")),
            ("dispatch_state", schema_type("string", "Provider dispatch certainty when a gateway failure exposes it.")),
            ("state_changed", schema_type("boolean", "Always false for native Skill loading.")),
        ])),
        "native_mcp_search" => Some(wrapped_output_schema(vec![
            ("project", schema_type("string", "Resolved Project id.")),
            ("query", nullable_schema("string", "Effective optional tool query.")),
            ("server", nullable_schema("string", "Optional exact native MCP server filter.")),
            ("read_only_only", schema_type("boolean", "Whether discovery was restricted to explicitly read-only tools.")),
            ("total_matches", schema_type("integer", "Matching live native MCP/Plugin tools.")),
            ("offset", schema_type("integer", "Zero-based filtered result offset.")),
            ("returned_count", schema_type("integer", "Tool descriptors returned in this page.")),
            ("tools", array_schema(
                json!({
                    "type":"object",
                    "properties":{
                        "server":{"type":"string"},
                        "name":{"type":"string"},
                        "description":{"anyOf":[{"type":"string"},{"type":"null"}]},
                        "annotations":{"type":["object","null"],"additionalProperties":true},
                        "read_only":{"type":"boolean"},
                        "policy_class":{"type":"string","enum":["read_only","effectful_non_destructive","destructive","unclassified"]}
                    },
                    "required":["server","name","description","annotations","read_only","policy_class"],
                    "additionalProperties":false
                }),
                "Live native MCP/Plugin tool descriptors."
            )),
            ("next_offset", nullable_schema("integer", "Next zero-based result offset, or null when complete.")),
            ("truncated", schema_type("boolean", "Whether more filtered tools remain.")),
            ("quota_mode", schema_type("string", "Always zero_codex_model_turn.")),
            ("model_turn_started", schema_type("boolean", "Always false.")),
            ("state_changed", schema_type("boolean", "Always false for discovery.")),
            ("error_kind", schema_type("string", "Stable native MCP guard/error code on failure.")),
            ("dispatch_state", schema_type("string", "Provider dispatch certainty when a gateway failure exposes it.")),
        ])),
        "native_mcp_describe" => Some(wrapped_output_schema(vec![
            ("project", schema_type("string", "Resolved Project id.")),
            ("server", schema_type("string", "Exact native MCP server.")),
            ("name", schema_type("string", "Exact native MCP/Plugin tool name.")),
            ("description", nullable_schema("string", "Live tool description.")),
            ("annotations", json!({"type":["object","null"],"additionalProperties":true,"description":"Live native tool annotations."})),
            ("policy_class", schema_type("string", "Target-mode policy class derived from live annotations.")),
            ("input_schema", json!({"type":"object","additionalProperties":true,"description":"Bounded envelope containing the live input schema or a bounded preview when oversized."})),
            ("quota_mode", schema_type("string", "Always zero_codex_model_turn.")),
            ("model_turn_started", schema_type("boolean", "Always false.")),
            ("state_changed", schema_type("boolean", "Always false for describe.")),
            ("error_kind", schema_type("string", "Stable native MCP guard/error code on failure.")),
            ("dispatch_state", schema_type("string", "Provider dispatch certainty when a gateway failure exposes it.")),
        ])),
        "native_mcp_call_readonly" | "native_mcp_call_effectful" => Some(wrapped_output_schema(vec![
            ("project", schema_type("string", "Resolved Project id.")),
            ("server", schema_type("string", "Exact native MCP server.")),
            ("tool", schema_type("string", "Exact native MCP/Plugin tool name.")),
            ("annotations", json!({"type":["object","null"],"additionalProperties":true,"description":"Annotations rechecked immediately before dispatch."})),
            ("policy_class", schema_type("string", "Effectful calls report effectful_non_destructive; read-only calls may omit this field.")),
            ("result", json!({"type":"object","additionalProperties":true,"description":"Bounded native MCP result envelope."})),
            ("quota_mode", schema_type("string", "Always zero_codex_model_turn.")),
            ("model_turn_started", schema_type("boolean", "Always false.")),
            ("state_changed", schema_type("boolean", "Present and false only for the explicitly read-only lane.")),
            ("error_kind", schema_type("string", "Stable native MCP guard/error code on failure.")),
            ("dispatch_state", schema_type("string", "Provider dispatch certainty when a gateway failure exposes it.")),
        ])),
        "native_knowledge_load" => Some(wrapped_output_schema(vec![
            ("project", schema_type("string", "Resolved Project id.")),
            ("key", schema_type("string", "Exact semantic reuse-manifest knowledge key.")),
            ("manifest_sha256", nullable_schema("string", "Current reuse-manifest SHA-256 when present.")),
            ("entry_sha256", nullable_schema("string", "Observed entry-file SHA-256.")),
            ("text", schema_type("string", "Bounded UTF-8 knowledge entry text.")),
            ("start_line", nullable_schema("integer", "Effective 1-based start line.")),
            ("end_line", nullable_schema("integer", "Last returned line, or null.")),
            ("total_lines", nullable_schema("integer", "Total source lines when available.")),
            ("returned_lines", nullable_schema("integer", "Returned source lines.")),
            ("has_more", nullable_schema("boolean", "Whether more lines remain for this semantic key.")),
            ("next_start_line", nullable_schema("integer", "Next 1-based line for pathless continuation.")),
            ("available_keys", array_schema(schema_type("string", "Available semantic key."), "Bounded semantic keys returned only when the requested key is absent.")),
            ("expected_sha256", nullable_schema("string", "Manifest-fenced entry hash on a changed-entry failure.")),
            ("observed_sha256", nullable_schema("string", "Observed entry hash on a changed-entry failure.")),
            ("error_kind", schema_type("string", "Stable native knowledge guard/error code on failure.")),
            ("dispatch_state", schema_type("string", "Provider dispatch certainty when a gateway failure exposes it.")),
            ("state_changed", schema_type("boolean", "Always false for native knowledge loading.")),
        ])),
        "skill_list" => Some(wrapped_output_schema(vec![
            ("project", schema_type("string", "Resolved Project id.")),
            (
                "catalog_revision",
                schema_type(
                    "string",
                    "Digest of the freshly observed deterministic Skill catalog state.",
                ),
            ),
            (
                "total_count",
                schema_type("integer", "Valid Skills matching the optional query."),
            ),
            (
                "returned_count",
                schema_type("integer", "Descriptors returned in this page."),
            ),
            (
                "offset",
                schema_type(
                    "integer",
                    "Page offset within the filtered deterministic catalog.",
                ),
            ),
            (
                "next_offset",
                nullable_schema(
                    "integer",
                    "Next offset, or null when this page is complete.",
                ),
            ),
            (
                "truncated",
                schema_type("boolean", "Whether more filtered descriptors remain."),
            ),
            (
                "skills",
                array_schema(
                    descriptor_schema(),
                    "Lightweight Skill descriptors; never SKILL.md bodies.",
                ),
            ),
            (
                "invalid_count",
                schema_type(
                    "integer",
                    "Malformed/invalid packages isolated from the valid catalog.",
                ),
            ),
            (
                "diagnostics",
                array_schema(
                    json!({"type":"object","additionalProperties":true}),
                    "Bounded reason-code-only invalid package diagnostics.",
                ),
            ),
            (
                "discovery_truncated",
                schema_type(
                    "boolean",
                    "Whether the hard discovery package ceiling was reached.",
                ),
            ),
            (
                "error_kind",
                schema_type("string", "Stable guard/error code on failure."),
            ),
            (
                "state_changed",
                schema_type("boolean", "Always false for Skill runtime failures."),
            ),
        ])),
        "skill_read_file" => Some(wrapped_output_schema(vec![
            ("project", schema_type("string", "Resolved Project id.")),
            (
                "skill_id",
                schema_type("string", "Opaque project-scoped Skill identity."),
            ),
            ("name", schema_type("string", "Skill metadata name.")),
            ("source_scope", schema_type("string", "Selected Skill source: project or runner.")),
            ("trust", schema_type("string", "Guidance trust label for the selected source; never execution authority.")),
            (
                "package_revision",
                nullable_schema(
                    "string",
                    "Active immutable whole-package revision for runner-installed Skills; null for project Skills.",
                ),
            ),
            (
                "definition_revision",
                schema_type(
                    "string",
                    "Current SKILL.md content digest only; not a package-tree revision.",
                ),
            ),
            (
                "path",
                schema_type("string", "Package-relative resource path."),
            ),
            (
                "sha256",
                schema_type("string", "Full current resource-file SHA-256."),
            ),
            (
                "text",
                schema_type("string", "Bounded UTF-8 selected text range."),
            ),
            (
                "start_line",
                schema_type("integer", "Effective 1-based selected start line."),
            ),
            (
                "end_line",
                nullable_schema("integer", "Last returned line, or null when none."),
            ),
            (
                "returned_lines",
                schema_type("integer", "Returned source lines."),
            ),
            (
                "has_more",
                schema_type("boolean", "Whether resource lines remain."),
            ),
            (
                "next_start_line",
                nullable_schema("integer", "Continuation line when has_more."),
            ),
            (
                "error_kind",
                schema_type("string", "Stable guard/error code on failure."),
            ),
            (
                "state_changed",
                schema_type("boolean", "Always false for Skill runtime failures."),
            ),
        ])),
        "skill_versions" => Some(wrapped_output_schema(vec![
            ("project", schema_type("string", "Resolved Project id.")),
            ("skill_id", schema_type("string", "Runner-scoped opaque Skill identity.")),
            ("skill_key", schema_type("string", "Stable logical operator Skill key.")),
            ("state_revision", schema_type("string", "CAS revision for installed/active state.")),
            ("active_package_revision", nullable_schema("string", "Currently active immutable package revision.")),
            ("total_count", schema_type("integer", "Installed immutable revision count.")),
            ("offset", schema_type("integer", "Returned page offset.")),
            ("next_offset", nullable_schema("integer", "Continuation offset.")),
            ("versions", array_schema(json!({
                "type":"object",
                "properties": {
                    "package_revision":{"type":"string"},
                    "definition_revision":{"type":"string"},
                    "name":{"type":"string"},
                    "description":{"type":"string"},
                    "file_count":{"type":"integer"},
                    "total_bytes":{"type":"integer"},
                    "installed_at_unix_ms":{"type":"integer"}
                },
                "additionalProperties": false
            }), "Bounded immutable revision metadata; never package bodies or native paths.")),
            ("error_kind", schema_type("string", "Stable error code on failure.")),
            ("state_changed", schema_type("boolean", "Always false for version observation.")),
        ])),

        "skill_install" => Some(wrapped_output_schema(vec![
            ("project", schema_type("string", "Authorized source Project id.")),
            ("skill_id", schema_type("string", "Runner-scoped opaque Skill identity.")),
            ("skill_key", schema_type("string", "Logical operator Skill key.")),
            ("package_revision", schema_type("string", "Immutable whole-package content revision.")),
            ("definition_revision", schema_type("string", "SKILL.md SHA-256 only.")),
            ("artifact_sha256", schema_type("string", "Verified source ZIP SHA-256.")),
            ("file_count", schema_type("integer", "Validated package file count.")),
            ("total_bytes", schema_type("integer", "Validated decompressed package bytes.")),
            ("installed", schema_type("boolean", "Whether this call committed a new immutable revision.")),
            ("activated", schema_type("boolean", "Whether this call changed active state.")),
            ("replayed", schema_type("boolean", "Whether the result reconciled the same idempotent intent.")),
            ("state_revision", schema_type("string", "Current CAS state revision.")),
            ("active_package_revision", nullable_schema("string", "Current active package revision.")),
            ("outcome_unknown", schema_type("boolean", "True only when dispatch may have executed but result cannot be reconciled yet.")),
            ("recovery_kind", schema_type("string", "reconcile when uncertain state requires observation.")),
            ("retry_same_idempotency_key", schema_type("boolean", "When true, any retry must reuse the same logical idempotency key; never invent a new key.")),
            ("error_kind", schema_type("string", "Stable error code on failure.")),
            ("state_changed", nullable_schema("boolean", "Observed mutation flag when outcome is known; null when the mutation outcome is unknown.")),
        ])),
        "skill_activate" => Some(wrapped_output_schema(vec![
            ("project", schema_type("string", "Resolved Project id.")),
            ("skill_id", schema_type("string", "Runner-scoped opaque Skill identity.")),
            ("skill_key", schema_type("string", "Logical operator Skill key.")),
            ("previous_active_package_revision", nullable_schema("string", "Previous active package revision.")),
            ("active_package_revision", schema_type("string", "Current active package revision.")),
            ("state_revision", schema_type("string", "Current CAS state revision.")),
            ("changed", schema_type("boolean", "Whether active state changed.")),
            ("replayed", schema_type("boolean", "Whether same-key reconciliation supplied this result.")),
            ("outcome_unknown", schema_type("boolean", "Whether dispatch outcome must be reconciled with the same key.")),
            ("recovery_kind", schema_type("string", "reconcile when uncertain state requires observation.")),
            ("retry_same_idempotency_key", schema_type("boolean", "When true, any retry must reuse the same logical idempotency key.")),
            ("error_kind", schema_type("string", "Stable error code on failure.")),
            ("state_changed", nullable_schema("boolean", "Observed mutation flag when outcome is known; null when the mutation outcome is unknown.")),
        ])),
        "skill_remove_revision" => Some(wrapped_output_schema(vec![
            ("project", schema_type("string", "Resolved Project id.")),
            ("skill_id", schema_type("string", "Runner-scoped opaque Skill identity.")),
            ("skill_key", schema_type("string", "Logical operator Skill key.")),
            ("package_revision", schema_type("string", "Target immutable package revision.")),
            ("state_revision", schema_type("string", "Current CAS state revision.")),
            ("removed", schema_type("boolean", "Whether the inactive revision was removed.")),
            ("replayed", schema_type("boolean", "Whether same-key reconciliation supplied this result.")),
            ("outcome_unknown", schema_type("boolean", "Whether dispatch outcome must be reconciled with the same key.")),
            ("recovery_kind", schema_type("string", "reconcile when uncertain state requires observation.")),
            ("retry_same_idempotency_key", schema_type("boolean", "When true, any retry must reuse the same logical idempotency key.")),
            ("error_kind", schema_type("string", "Stable error code on failure.")),
            ("state_changed", nullable_schema("boolean", "Observed mutation flag when outcome is known; null when the mutation outcome is unknown.")),
        ])),
        _ => None,
    }?;
    apply_skill_recovery_contract(name, &mut schema);
    Some(schema)
}
