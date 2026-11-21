import type { ReactNode } from "react";
import { NARROW, useMediaQuery } from "../lib/useMediaQuery";

export type Column<T> = {
  key: string;
  header: string;
  render: (row: T) => ReactNode;
  /** 1 = card title on narrow screens; 2 = shown in the card; 3 = shown under "More" */
  priority: 1 | 2 | 3;
  numeric?: boolean;
};

/** A table on wide screens, a list of cards below 600 px. Never an empty table. */
export function DataTable<T>({ caption, rows, columns, rowKey, empty = "Nothing to show." }: {
  caption: string;
  rows: T[];
  columns: Column<T>[];
  rowKey: (row: T) => string;
  empty?: ReactNode;
}) {
  const narrow = useMediaQuery(NARROW);
  if (rows.length === 0) return <p className="muted">{empty}</p>;
  if (narrow) {
    const title = columns.find((c) => c.priority === 1) ?? columns[0];
    const body = columns.filter((c) => c !== title && c.priority === 2);
    const more = columns.filter((c) => c !== title && c.priority === 3);
    return (
      <ul className="data-cards" aria-label={caption}>
        {rows.map((row) => (
          <li key={rowKey(row)} className="data-card">
            <h3 className="data-card-title">{title ? title.render(row) : null}</h3>
            <dl className="kv">
              {body.map((c) => (
                <Pair key={c.key} label={c.header} value={c.render(row)} />
              ))}
            </dl>
            {more.length ? (
              <details>
                <summary>More</summary>
                <dl className="kv">
                  {more.map((c) => (
                    <Pair key={c.key} label={c.header} value={c.render(row)} />
                  ))}
                </dl>
              </details>
            ) : null}
          </li>
        ))}
      </ul>
    );
  }
  return (
    <div className="table-wrap">
      <table>
        <caption className="visually-hidden">{caption}</caption>
        <thead>
          <tr>
            {columns.map((c) => (
              <th key={c.key} scope="col" className={c.numeric ? "num" : undefined}>
                {c.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={rowKey(row)}>
              {columns.map((c) => (
                <td key={c.key} className={c.numeric ? "num" : undefined}>
                  {c.render(row)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Pair({ label, value }: { label: string; value: ReactNode }) {
  return (
    <>
      <dt>{label}</dt>
      <dd>{value}</dd>
    </>
  );
}
