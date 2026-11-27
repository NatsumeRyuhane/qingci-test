import type { CaseStatus, Route } from "../api/client";
import { label, ROUTE_LABELS, STATUS_LABELS } from "../lib/labels";

export function RouteBadge({ route }: { route: Route | null | undefined }) {
  if (!route) return <span className="badge badge-muted">未起草</span>;
  return <span className={`badge badge-route-${route}`}>{ROUTE_LABELS[route]}</span>;
}

export function StatusBadge({ status }: { status: CaseStatus }) {
  return <span className={`badge badge-status-${status}`}>{label(STATUS_LABELS, status)}</span>;
}

export function ErrorNotice({ message }: { message: string | null }) {
  if (!message) return null;
  return (
    <p role="alert" className="notice notice-error">
      {message}
    </p>
  );
}
