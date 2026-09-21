import { useLocale } from "../../i18n/locale";
import { productText, useProduct, type ProductKey } from "../../i18n/product";
import type { DesktopState } from "../../models/topology";

export function observationTime(timestamp: number | null | undefined, locale: string, now = Date.now()): string {
  if (!timestamp) return productText(locale, "noActivity");
  const seconds = Math.min(0, Math.round((timestamp - now) / 1000));
  const format = new Intl.RelativeTimeFormat(locale, { numeric: "auto" });
  if (seconds > -60) return format.format(seconds, "second");
  if (seconds > -3600) return format.format(Math.round(seconds / 60), "minute");
  if (seconds > -86_400) return format.format(Math.round(seconds / 3600), "hour");
  return format.format(Math.round(seconds / 86_400), "day");
}
export function statusKey(status: string | undefined): ProductKey {
  if (status === "ready" || status === "running") return "running";
  if (status === "starting" || status === "connecting") return "starting";
  if (status === "error" || status === "unavailable") return "unavailable";
  if (!status || status === "stopped" || status === "disabled") return "stopped";
  return "unknown";
}
export function WorkspaceStatus({ state }: { state: DesktopState }) {
  const p = useProduct();
  const tunnel = state.regular_tunnel?.status || (state.topology?.experience === "quick_share" && state.readiness.exposure === "remote_ready" ? "ready" : state.readiness.exposure === "starting" ? "starting" : state.readiness.exposure === "error" || state.readiness.exposure === "degraded" ? "unavailable" : state.readiness.exposure === "unknown" || state.topology?.server.kind === "remote" ? "unknown" : "stopped");
  const nativeContext = !state.enhanced_runtime
    ? "unknown"
    : state.enhanced_runtime.native_context_ready
      ? "ready"
      : "unavailable";
  const values = [["Server", state.readiness.server], ["Runner", state.readiness.runner], ["Secure Tunnel", tunnel], ["Native Context", nativeContext]];
  return <dl className="workspace-status-strip" aria-label={p("workspace")} role="status">
    {values.map(([name, status]) => <div key={name}><dt>{name}</dt><dd><i className={`status-dot ${status === "ready" ? "ready" : status === "error" ? "error" : "unknown"}`} aria-hidden="true" />{p(statusKey(status))}</dd></div>)}
  </dl>;
}
export function ChatgptObservation({ state }: { state: DesktopState }) {
  const p = useProduct(); const { locale } = useLocale();
  const at = state.chatgpt_activity?.last_meaningful_activity_at_ms;
  return <p className="workspace-observation">{at ? <>{p("lastChatgpt")} · <time dateTime={new Date(at).toISOString()}>{observationTime(at, locale)}</time></> : p("noChatgpt")}</p>;
}
