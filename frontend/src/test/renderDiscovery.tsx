import { render } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import App from "../App";
import { AuthRoot } from "../auth/AuthProvider";
import { installAuthBackend } from "./authBackend";
import { installDiscoveryBackend, type DiscoveryState } from "./discoveryBackend";
import { installFakeBackend } from "./fakeBackend";

/** Render the app at `path` over the discovery backend. With a role, sign-in is required and that role is signed in. */
export function renderDiscovery(path: string, initial: Partial<DiscoveryState> = {}, role?: "viewer" | "analyst" | "admin") {
  const base = role ? installAuthBackend({ mode: "required", session: { username: `${role}-a`, role } }) : installFakeBackend();
  const backend = installDiscoveryBackend(initial, base);
  render(
    <MemoryRouter initialEntries={[path]}>
      <AuthRoot>
        <App />
      </AuthRoot>
    </MemoryRouter>,
  );
  return backend;
}
