import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import App from "../App";
import { SYNTHETIC_CSRF, SYNTHETIC_PASSWORD, installAuthBackend, type AuthBackendOptions, type Call } from "../test/authBackend";
import { AuthGate, AuthRoot } from "./AuthProvider";
import { getSession } from "./session";

const renderApp = (path = "/", options: AuthBackendOptions = {}) => {
  const backend = installAuthBackend(options);
  const view = render(
    <MemoryRouter initialEntries={[path]}>
      <AuthRoot>
        <App />
      </AuthRoot>
    </MemoryRouter>,
  );
  return { backend, ...view };
};
const callsTo = (calls: Call[], path: string) => calls.filter((c) => c.path === path);

async function signIn(user: ReturnType<typeof userEvent.setup>, name = "analyst-a", password = SYNTHETIC_PASSWORD) {
  await user.type(await screen.findByLabelText("Username"), name);
  await user.type(screen.getByLabelText("Password"), password);
  await user.click(screen.getByRole("button", { name: "Sign in" }));
}

describe("sign-in disabled (the default)", () => {
  it("shows the application as before: no sign-in screen, no user menu, no restrictions", async () => {
    const { backend } = renderApp("/watchlists", { mode: "disabled" });
    expect(await screen.findByRole("heading", { level: 1, name: "Watchlists" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Sign in" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Account menu/ })).not.toBeInTheDocument();
    expect(screen.getByText("Local prototype · no authentication")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Create watchlist" })).toBeEnabled();
    expect(screen.queryByRole("note")).not.toBeInTheDocument();
    expect(callsTo(backend.calls, "/auth/me")).toHaveLength(0);
    expect(getSession()).toMatchObject({ mode: "disabled", csrf: null, user: null });
  });
});

describe("protected route", () => {
  it("renders nothing but the sign-in form while anonymous, and requests no data", async () => {
    const { backend } = renderApp("/", { mode: "required" });
    expect(await screen.findByRole("heading", { level: 1, name: "Sign in" })).toBeInTheDocument();
    expect(screen.queryByRole("navigation", { name: "Main" })).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Overview" })).not.toBeInTheDocument();
    const paths = backend.calls.map((c) => c.path);
    expect(paths).toEqual(["/auth/status"]);
  });

  it("shows a loading state first and never flashes the application", async () => {
    installAuthBackend({ mode: "required" });
    vi.mocked(fetch).mockImplementation(() => new Promise(() => undefined)); // status never answers
    render(
      <MemoryRouter>
        <AuthRoot>
          <p>secret application</p>
        </AuthRoot>
      </MemoryRouter>,
    );
    expect(screen.getByText("Checking sign-in…")).toBeInTheDocument();
    expect(screen.queryByText("secret application")).not.toBeInTheDocument();
  });

  it("gates by session state, not by URL", async () => {
    installAuthBackend({ mode: "required" });
    render(
      <AuthGate>
        <p>secret application</p>
      </AuthGate>,
    );
    // no provider ran: status is still "loading", so the child must not render
    expect(screen.queryByText("secret application")).not.toBeInTheDocument();
  });

  it("renders the shell, not a sign-in form, when the backend is unreachable", async () => {
    renderApp("/", { mode: "required", statusNetworkError: true });
    expect(await screen.findByRole("heading", { level: 1, name: "Overview" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Sign in" })).not.toBeInTheDocument();
    expect(getSession().status).toBe("unavailable");
  });

  it("tells the operator how to create the first user when none exist", async () => {
    renderApp("/", { mode: "required", setupRequired: true });
    expect(await screen.findByText(/No users exist yet/)).toBeInTheDocument();
    expect(screen.getByText(/bip\.tools\.users create/)).toBeInTheDocument();
  });
});

describe("sign-in form", () => {
  it("is labelled, with the right autocomplete hints", async () => {
    renderApp("/");
    const username = await screen.findByLabelText("Username");
    const password = screen.getByLabelText("Password");
    expect(username).toHaveAttribute("autocomplete", "username");
    expect(password).toHaveAttribute("autocomplete", "current-password");
    expect(password).toHaveAttribute("type", "password");
    expect(screen.getByRole("form", { name: "Sign in" })).toBeInTheDocument();
    expect(document.title).toBe("Sign in · BIP");
  });

  it("asks for both fields before calling the server", async () => {
    const user = userEvent.setup();
    const { backend } = renderApp("/");
    await user.click(await screen.findByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Enter your username and password.");
    expect(callsTo(backend.calls, "/auth/login")).toHaveLength(0);
  });

  it("reports a wrong password in an alert, clears the field and keeps the user on the form", async () => {
    const user = userEvent.setup();
    renderApp("/");
    await signIn(user, "analyst-a", "synthetic-wrong-password");
    const alert = await screen.findByText(/Invalid username or password/);
    expect(alert.closest("[role=alert]")).not.toBeNull();
    expect(screen.getByLabelText("Password")).toHaveValue("");
    expect(screen.getByLabelText("Username")).toHaveValue("analyst-a");
    expect(screen.getByRole("button", { name: "Sign in" })).toBeEnabled();
    await waitFor(() => expect(screen.getByLabelText("Password")).toHaveFocus());
    expect(screen.queryByRole("navigation", { name: "Main" })).not.toBeInTheDocument();
  });

  it("is disabled while submitting", async () => {
    const user = userEvent.setup();
    renderApp("/");
    const real = vi.mocked(fetch).getMockImplementation();
    if (!real) throw new Error("no mock");
    let release: () => void = () => undefined;
    let loginCalls = 0;
    vi.mocked(fetch).mockImplementation((input, init) => {
      const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
      if (url.endsWith("/auth/login")) {
        loginCalls++;
        return new Promise<Response>((resolve) => {
          release = () => resolve(real(input, init) as Promise<Response>);
        });
      }
      return real(input, init);
    });
    await signIn(user);
    const busy = await screen.findByRole("button", { name: "Signing in…" });
    expect(busy).toBeDisabled();
    expect(screen.getByLabelText("Username")).toBeDisabled();
    await user.click(busy); // a second click does nothing
    expect(loginCalls).toBe(1);
    await act(async () => release());
    expect(await screen.findByRole("heading", { level: 1, name: "Overview" })).toBeInTheDocument();
  });

  it("shows the throttle message with the wait time and blocks another attempt until it passes", async () => {
    const user = userEvent.setup();
    renderApp("/", { throttleLogin: 7 });
    await signIn(user);
    expect(await screen.findByText("Too many requests. Try again in 7 seconds.")).toBeInTheDocument();
    const button = screen.getByRole("button", { name: /Try again in \d+ s/ });
    expect(button).toBeDisabled();
  });

  it("signs in, shows the dashboard and the account menu, and uses the CSRF token afterwards", async () => {
    const user = userEvent.setup();
    const { backend } = renderApp("/watchlists");
    await signIn(user, "analyst-a");
    expect(await screen.findByRole("heading", { level: 1, name: "Watchlists" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Sign in" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Account menu, analyst-a, analyst" })).toBeInTheDocument();
    expect(screen.getByText("Sign-in required · protected session")).toBeInTheDocument();
    expect(callsTo(backend.calls, "/auth/login")[0]?.body).toEqual({ username: "analyst-a", password: SYNTHETIC_PASSWORD });
    // a protected write needs the token
    await user.type(screen.getByLabelText("Name", { exact: true }), "Whales");
    await user.click(screen.getByRole("button", { name: "Create watchlist" }));
    await screen.findByRole("heading", { level: 2, name: "Whales" });
    const post = backend.calls.find((c) => c.method === "POST" && c.path === "/watchlists");
    expect(post?.csrf).toBe(SYNTHETIC_CSRF);
    expect(post?.credentials).toBe("same-origin");
    // never persisted in the browser
    expect(JSON.stringify({ ...localStorage, ...sessionStorage })).not.toContain(SYNTHETIC_CSRF);
  });
});

describe("session restore on reload", () => {
  it("keeps the user signed in and recovers the CSRF token from /auth/me", async () => {
    const user = userEvent.setup();
    const { backend } = renderApp("/watchlists", { mode: "required", session: { username: "analyst-a", role: "analyst" } });
    expect(await screen.findByRole("heading", { level: 1, name: "Watchlists" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Sign in" })).not.toBeInTheDocument();
    expect(callsTo(backend.calls, "/auth/me")).toHaveLength(1);
    await user.type(screen.getByLabelText("Name", { exact: true }), "After reload");
    await user.click(screen.getByRole("button", { name: "Create watchlist" }));
    await screen.findByRole("heading", { level: 2, name: "After reload" });
    expect(backend.calls.find((c) => c.method === "POST" && c.path === "/watchlists")?.csrf).toBe(SYNTHETIC_CSRF);
  });

  it("uses a CSRF token from /auth/status when the server sends one (no /auth/me call)", async () => {
    const { backend } = renderApp("/", { mode: "required", session: { username: "viewer-a", role: "viewer" }, csrfInStatus: true });
    await screen.findByRole("heading", { level: 1, name: "Overview" });
    expect(callsTo(backend.calls, "/auth/me")).toHaveLength(0);
    expect(getSession().csrf).toBe(SYNTHETIC_CSRF);
  });

  it("falls back to the sign-in form when the session cookie is stale", async () => {
    renderApp("/", { mode: "required", session: null });
    expect(await screen.findByRole("heading", { level: 1, name: "Sign in" })).toBeInTheDocument();
  });
});

describe("sign out and session end", () => {
  it("signs out with the CSRF header and returns to the sign-in form", async () => {
    const user = userEvent.setup();
    const { backend } = renderApp("/", { mode: "required", session: { username: "analyst-a", role: "analyst" } });
    await user.click(await screen.findByRole("button", { name: /Account menu/ }));
    const menu = screen.getByRole("group", { name: "Account" });
    expect(within(menu).getByText("analyst-a")).toBeInTheDocument();
    await user.click(within(menu).getByRole("button", { name: "Sign out" }));
    expect(await screen.findByRole("heading", { level: 1, name: "Sign in" })).toBeInTheDocument();
    expect(screen.getByText("You have signed out.")).toBeInTheDocument();
    const out = backend.calls.find((c) => c.path === "/auth/logout");
    expect(out?.method).toBe("POST");
    expect(out?.csrf).toBe(SYNTHETIC_CSRF);
    expect(getSession().csrf).toBeNull();
  });

  it("signs out locally even when the server call fails", async () => {
    const user = userEvent.setup();
    renderApp("/", { mode: "required", session: { username: "analyst-a", role: "analyst" } });
    await user.click(await screen.findByRole("button", { name: /Account menu/ }));
    const real = vi.mocked(fetch).getMockImplementation();
    vi.mocked(fetch).mockImplementation((input, init) =>
      String(input).endsWith("/auth/logout") ? Promise.reject(new TypeError("Failed to fetch")) : (real?.(input, init) as Promise<Response>),
    );
    await user.click(screen.getByRole("button", { name: "Sign out" }));
    expect(await screen.findByRole("heading", { level: 1, name: "Sign in" })).toBeInTheDocument();
  });

  it("returns to the sign-in form when the server ends the session mid-use", async () => {
    renderApp("/watchlists", { mode: "required", session: { username: "analyst-a", role: "analyst" }, protectedFailure: { status: 401, code: "unauthenticated", message: "Sign in to use this endpoint." } });
    expect(await screen.findByRole("heading", { level: 1, name: "Sign in" })).toBeInTheDocument();
    expect(screen.getByText(/Your session has ended/)).toBeInTheDocument();
    expect(screen.queryByRole("navigation", { name: "Main" })).not.toBeInTheDocument();
  });

  it("keeps the page URL so sign-in continues where the user was", async () => {
    const user = userEvent.setup();
    renderApp("/watchlists", { mode: "required" });
    await signIn(user);
    expect(await screen.findByRole("heading", { level: 1, name: "Watchlists" })).toBeInTheDocument();
  });
});

describe("role-aware UI", () => {
  it("disables analyst actions for a viewer and says why", async () => {
    renderApp("/watchlists", { mode: "required", session: { username: "viewer-a", role: "viewer" } });
    const create = await screen.findByRole("button", { name: "Create watchlist" });
    expect(create).toBeDisabled();
    expect(create).toHaveAttribute("title", "Needs the analyst role. You are signed in as viewer.");
    expect(screen.getByText("Needs the analyst role. You are signed in as viewer.")).toBeVisible();
    expect(screen.getByRole("button", { name: "Account menu, viewer-a, viewer" })).toBeInTheDocument();
  });

  it("leaves analyst actions enabled for an analyst, but disables admin-only ones", async () => {
    renderApp("/chains", { mode: "required", session: { username: "analyst-a", role: "analyst" } });
    const fetchHead = (await screen.findAllByRole("button", { name: /Fetch latest/ }))[0];
    expect(fetchHead).toBeDisabled(); // provider-contacting: admin
    expect(screen.getAllByText("Needs the admin role. You are signed in as analyst.").length).toBeGreaterThan(0);
  });

  it("enables everything for an admin", async () => {
    renderApp("/watchlists", { mode: "required", session: { username: "admin-a", role: "admin" } });
    expect(await screen.findByRole("button", { name: "Create watchlist" })).toBeEnabled();
    expect(screen.queryByText(/^Needs the/)).not.toBeInTheDocument();
  });

  it("still shows a clear message when the server refuses an action the UI allowed", async () => {
    const user = userEvent.setup();
    renderApp("/watchlists", {
      mode: "required",
      session: { username: "analyst-a", role: "analyst" },
      protectedFailure: { status: 403, code: "forbidden", message: "This action needs the analyst role." },
    });
    // GET /watchlists is also refused by the scripted failure; the page must survive it
    await screen.findByRole("heading", { level: 1, name: "Watchlists" });
    await user.type(screen.getByLabelText("Name", { exact: true }), "Nope");
    await user.click(screen.getByRole("button", { name: "Create watchlist" }));
    const refused = await screen.findAllByText("Your role does not allow this. This action needs the analyst role.");
    expect(refused.length).toBeGreaterThan(0);
    expect(screen.queryByRole("heading", { name: "Sign in" })).not.toBeInTheDocument();
  });

  it("explains a CSRF refusal without signing the user out", async () => {
    const user = userEvent.setup();
    renderApp("/watchlists", {
      mode: "required",
      session: { username: "analyst-a", role: "analyst" },
      protectedFailure: { status: 403, code: "csrf_failed", message: "The CSRF token is missing or wrong." },
    });
    await screen.findByRole("heading", { level: 1, name: "Watchlists" });
    await user.type(screen.getByLabelText("Name", { exact: true }), "Nope");
    await user.click(screen.getByRole("button", { name: "Create watchlist" }));
    expect((await screen.findAllByText(/security token is missing or out of date/)).length).toBeGreaterThan(0);
  });
});

describe("change password", () => {
  const open = async (user: ReturnType<typeof userEvent.setup>) => {
    await user.click(await screen.findByRole("button", { name: /Account menu/ }));
    await user.click(screen.getByRole("button", { name: "Change password" }));
    return screen.getByRole("dialog", { name: "Change password" });
  };

  it("validates locally before calling the server", async () => {
    const user = userEvent.setup();
    const { backend } = renderApp("/", { mode: "required", session: { username: "analyst-a", role: "analyst" } });
    const dialog = await open(user);
    await user.type(within(dialog).getByLabelText("Current password"), SYNTHETIC_PASSWORD);
    await user.type(within(dialog).getByLabelText(/^New password/), "short");
    await user.type(within(dialog).getByLabelText("Repeat new password"), "short");
    await user.click(within(dialog).getByRole("button", { name: "Change password" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent(/at least 12 characters/);
    expect(callsTo(backend.calls, "/auth/password")).toHaveLength(0);
  });

  it("shows the server's reason when the current password is wrong", async () => {
    const user = userEvent.setup();
    renderApp("/", { mode: "required", session: { username: "analyst-a", role: "analyst" } });
    const dialog = await open(user);
    await user.type(within(dialog).getByLabelText("Current password"), "synthetic-wrong-current");
    await user.type(within(dialog).getByLabelText(/^New password/), "synthetic-new-password-1");
    await user.type(within(dialog).getByLabelText("Repeat new password"), "synthetic-new-password-1");
    await user.click(within(dialog).getByRole("button", { name: "Change password" }));
    expect(await within(dialog).findByText("the current password is not correct")).toBeInTheDocument();
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("sends the schema fields with the CSRF header, then asks to sign in again", async () => {
    const user = userEvent.setup();
    const { backend } = renderApp("/", { mode: "required", session: { username: "analyst-a", role: "analyst" } });
    const dialog = await open(user);
    await user.type(within(dialog).getByLabelText("Current password"), SYNTHETIC_PASSWORD);
    await user.type(within(dialog).getByLabelText(/^New password/), "synthetic-new-password-1");
    await user.type(within(dialog).getByLabelText("Repeat new password"), "synthetic-new-password-1");
    await user.click(within(dialog).getByRole("button", { name: "Change password" }));
    expect(await screen.findByRole("heading", { level: 1, name: "Sign in" })).toBeInTheDocument();
    expect(screen.getByText(/Password changed/)).toBeInTheDocument();
    const call = callsTo(backend.calls, "/auth/password")[0];
    expect(call?.body).toEqual({ current_password: SYNTHETIC_PASSWORD, new_password: "synthetic-new-password-1" });
    expect(call?.csrf).toBe(SYNTHETIC_CSRF);
  });

  it("closes on Escape and returns focus to the menu button", async () => {
    const user = userEvent.setup();
    renderApp("/", { mode: "required", session: { username: "analyst-a", role: "analyst" } });
    await open(user);
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});
