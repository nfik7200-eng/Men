import { useEffect } from "react";

/** Sets document.title to "<title> · BIP". */
export function usePageTitle(title: string): void {
  useEffect(() => {
    document.title = `${title} · BIP`;
  }, [title]);
}
