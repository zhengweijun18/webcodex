use super::RunnerCapabilityRequirement::OwnerOnly;
use super::ToolVisibility::ModelVisible;
use super::{
    adaptive_runtime_direct, def, model_spec, require_any_scopes, ToolDefinition,
    TOOL_CATEGORY_RUNTIME,
};
use crate::metadata::{
    ToolPathHint::None as NoPath,
    ToolRisk::{JobRun, Read, RunControl},
    JOB_RUN, PLUGIN_INSPECT, PLUGIN_INVOKE, PLUGIN_MANAGE, PROJECT_READ, TOOL_PROVIDER_CONTROL,
    TOOL_PROVIDER_RUNNER,
};

const PLUGIN_GATEWAY_SCOPES: &[&str] = &[PLUGIN_INSPECT, PLUGIN_INVOKE, PLUGIN_MANAGE];

pub(super) const DEFINITIONS: &[ToolDefinition] = &[
    adaptive_runtime_direct(
        model_spec(
            def(
                "native_mcp_search",
                super::ToolAuditPolicy::typed_fields(&[
                    super::ToolAuditResultField::value("project"),
                    super::ToolAuditResultField::value("total_matches"),
                    super::ToolAuditResultField::value("returned_count"),
                    super::ToolAuditResultField::value("next_offset"),
                    super::ToolAuditResultField::value("quota_mode"),
                    super::ToolAuditResultField::value("model_turn_started"),
                    super::ToolAuditResultField::value("error_kind"),
                ])
                .session_input(super::ToolAuditSessionInputPolicy::OmitTopLevel(&["query"])),
                ModelVisible,
                TOOL_CATEGORY_RUNTIME,
                Some(OwnerOnly),
                TOOL_PROVIDER_RUNNER,
                super::ToolSemanticContract {
                    effect: super::ToolEffect::Observe,
                    risk: Read,
                    approval: super::ToolApprovalPolicy::None,
                    idempotency: super::ToolIdempotency::PureRead,
                },
                Some(PROJECT_READ),
                true,
                NoPath,
                false,
                false,
                super::ToolSessionEvidencePolicy::NONE,
            ),
            "Search the live native Codex MCP/Plugin tool catalog on the same Runner with bounded pagination. Defaults to tools explicitly marked read-only and non-destructive; starts no Codex model turn.",
        ),
        31,
    ),
    adaptive_runtime_direct(
        model_spec(
            def(
                "native_mcp_describe",
                super::ToolAuditPolicy::typed_fields(&[
                    super::ToolAuditResultField::value("project"),
                    super::ToolAuditResultField::value("server"),
                    super::ToolAuditResultField::value("name"),
                    super::ToolAuditResultField::value("policy_class"),
                    super::ToolAuditResultField::value("quota_mode"),
                    super::ToolAuditResultField::value("model_turn_started"),
                    super::ToolAuditResultField::value("error_kind"),
                ]),
                ModelVisible,
                TOOL_CATEGORY_RUNTIME,
                Some(OwnerOnly),
                TOOL_PROVIDER_RUNNER,
                super::ToolSemanticContract {
                    effect: super::ToolEffect::Observe,
                    risk: Read,
                    approval: super::ToolApprovalPolicy::None,
                    idempotency: super::ToolIdempotency::PureRead,
                },
                Some(PROJECT_READ),
                true,
                NoPath,
                false,
                false,
                super::ToolSessionEvidencePolicy::NONE,
            ),
            "Describe one exact live native Codex MCP/Plugin tool, including annotations, target-mode policy class, and bounded input schema. Starts no Codex model turn.",
        ),
        32,
    ),
    adaptive_runtime_direct(
        model_spec(
            def(
                "native_mcp_call_readonly",
                super::ToolAuditPolicy::typed_fields(&[
                    super::ToolAuditResultField::value("project"),
                    super::ToolAuditResultField::value("server"),
                    super::ToolAuditResultField::value("tool"),
                    super::ToolAuditResultField::value("quota_mode"),
                    super::ToolAuditResultField::value("model_turn_started"),
                    super::ToolAuditResultField::value("error_kind"),
                ])
                .session_input(super::ToolAuditSessionInputPolicy::OmitTopLevel(&["arguments"])),
                ModelVisible,
                TOOL_CATEGORY_RUNTIME,
                Some(OwnerOnly),
                TOOL_PROVIDER_RUNNER,
                super::ToolSemanticContract {
                    effect: super::ToolEffect::Observe,
                    risk: Read,
                    approval: super::ToolApprovalPolicy::None,
                    idempotency: super::ToolIdempotency::PureRead,
                },
                Some(PROJECT_READ),
                true,
                NoPath,
                false,
                false,
                super::ToolSessionEvidencePolicy::NONE,
            ),
            "Call one exact native Codex-managed MCP/Plugin tool only when its live annotations explicitly mark it read-only and non-destructive. The Bridge rechecks the live tool before dispatch and starts no Codex model turn.",
        ),
        33,
    ),
    adaptive_runtime_direct(
        model_spec(
            def(
                "native_mcp_call_effectful",
                super::ToolAuditPolicy::typed_fields(&[
                    super::ToolAuditResultField::value("project"),
                    super::ToolAuditResultField::value("server"),
                    super::ToolAuditResultField::value("tool"),
                    super::ToolAuditResultField::value("policy_class"),
                    super::ToolAuditResultField::value("quota_mode"),
                    super::ToolAuditResultField::value("model_turn_started"),
                    super::ToolAuditResultField::value("error_kind"),
                ])
                .session_input(super::ToolAuditSessionInputPolicy::OmitTopLevel(&["arguments"])),
                ModelVisible,
                TOOL_CATEGORY_RUNTIME,
                Some(OwnerOnly),
                TOOL_PROVIDER_RUNNER,
                super::ToolSemanticContract {
                    effect: super::ToolEffect::Execute,
                    risk: JobRun,
                    approval: super::ToolApprovalPolicy::Standard,
                    idempotency: super::ToolIdempotency::NonIdempotent,
                },
                Some(JOB_RUN),
                true,
                NoPath,
                true,
                true,
                super::ToolSessionEvidencePolicy::NONE,
            ),
            "Call one exact native Codex-managed MCP/Plugin tool only when its live annotations explicitly mark it effectful and non-destructive. Destructive or unclassified tools fail closed; starts no Codex model turn.",
        ),
        68,
    ),
    adaptive_runtime_direct(
        require_any_scopes(
            model_spec(
                def(
                    "plugin_tool",
                    super::ToolAuditPolicy::TYPED_CANONICAL,
                    ModelVisible,
                    TOOL_CATEGORY_RUNTIME,
                    None,
                    TOOL_PROVIDER_CONTROL,
                    super::ToolSemanticContract {
                        effect: super::ToolEffect::Execute,
                        risk: RunControl,
                        approval: super::ToolApprovalPolicy::Standard,
                        idempotency: super::ToolIdempotency::NonIdempotent,
                    },
                    None,
                    false,
                    NoPath,
                    false,
                    false,
                    super::ToolSessionEvidencePolicy::NONE,
                ),
                "Stable gateway for Runner-owned native Tool Plugins. Provider tools are never outer WebCodex MCP tools. Discovery begins at an exact caller-visible Runner; describe observes one exact Runner/provider/tool schema and returns an opaque binding; call accepts only binding + arguments, never retargets, relists, reloads, or blindly retries. Gateway visibility requires any Plugin scope, while each action separately enforces plugin:inspect, plugin:invoke, or plugin:manage before provider dispatch.",
            ).with_gpt_action_description("Access Runner-owned Tool Plugins. List/describe before call; calls use an exact opaque binding plus provider arguments and never retarget or blindly retry. Action-specific Plugin scopes remain enforced by the runtime."),
            PLUGIN_GATEWAY_SCOPES,
        ),
        26,
    ),
];
