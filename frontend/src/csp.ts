/**
 * Content Security Policy for the BIP frontend.
 *
 * Production builds load CSS from files, so inline styles stay forbidden. The Vite dev server
 * injects imported CSS as inline <style> elements; with the strict policy the browser blocks them
 * and the app renders unstyled. Only development relaxes style-src; scripts stay same-origin.
 */
export function contentSecurityPolicy(command: "build" | "serve"): string {
  const style = command === "serve" ? "style-src 'self' 'unsafe-inline';" : "style-src 'self';";
  return (
    "default-src 'self'; script-src 'self'; " +
    style +
    " img-src 'self' data:; connect-src 'self'; base-uri 'none'; form-action 'none'"
  );
}
