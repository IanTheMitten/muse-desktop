import type { ReactNode } from "react";
import { Link } from "react-router-dom";

/** The frame every view sits in: a back affordance, a title, and the body.
 *
 * Loading is deliberately quiet: a spinner that flashes for 40 ms over the
 * tailnet is worse than nothing. Only the error state is loud. */
export function Screen({
  title,
  subtitle,
  back,
  hero,
  error,
  loading,
  children,
}: {
  title: string;
  subtitle?: string;
  back?: { to: string; label: string };
  /** Optional art above the title, such as an album's cover. */
  hero?: ReactNode;
  error?: string | null;
  loading?: boolean;
  children: ReactNode;
}) {
  return (
    <div className="view">
      <header>
        {back && (
          <Link className="link-btn" to={back.to}>
            <span aria-hidden="true">‹</span> {back.label}
          </Link>
        )}
        {hero}
        <h1 className="page-title">{title}</h1>
        {subtitle && <p className="page-sub">{subtitle}</p>}
      </header>

      {error && <p className="error">{error}</p>}
      {!error && loading && !children && <p className="muted">Loading…</p>}
      {!error && children}
    </div>
  );
}
