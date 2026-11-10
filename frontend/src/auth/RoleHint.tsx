import type { Access } from "./useAuth";

/**
 * Visible reason an action is unavailable for the signed-in role. Renders nothing when the action
 * is allowed (so nothing changes with sign-in disabled). Pair it with `disabled={!can.allowed}`.
 */
export function RoleHint({ can }: { can: Access }) {
  if (can.allowed) return null;
  return (
    <span className="role-hint" role="note">
      {can.reason}
    </span>
  );
}
