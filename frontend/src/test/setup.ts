import "@testing-library/jest-dom/vitest";
import { afterEach, vi } from "vitest";
import { cleanup } from "@testing-library/react";
import { resetSession } from "../auth/session";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  resetSession();
});
