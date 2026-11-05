"use client";

import { AllCommunityModule, ModuleRegistry, colorSchemeLight, themeQuartz, type ColDef, type ICellRendererParams } from "ag-grid-community";
import { AgGridReact } from "ag-grid-react";
import { useMemo, useState } from "react";
import { pillClass } from "@/lib/status";

ModuleRegistry.registerModules([AllCommunityModule]);

const theme = themeQuartz.withPart(colorSchemeLight).withParams({
  backgroundColor: "#ffffff",
  foregroundColor: "#0b2a5b",
  headerBackgroundColor: "#eaf0f7",
  headerTextColor: "#0b2a5b",
  borderColor: "#cfdae8",
  accentColor: "#0070e0",
  oddRowBackgroundColor: "#f5f8fc",
  rowHoverColor: "#e6effa",
  browserColorScheme: "light",
  fontFamily: "inherit",
  fontSize: 13,
  wrapperBorderRadius: 16,
});

// ---- cell renderers --------------------------------------------------------------------

export function VerdictPill({ value }: ICellRendererParams) {
  if (!value) return null;
  const v = String(value);
  return <span className={pillClass(v)}>{v.replaceAll("_", " ")}</span>;
}

export function Mono({ value }: ICellRendererParams) {
  const [copied, setCopied] = useState(false);
  if (!value) return null;
  return (
    <button
      title="Copy"
      className="max-w-full truncate font-mono text-xs text-muted hover:text-accent"
      onClick={() => {
        navigator.clipboard.writeText(String(value));
        setCopied(true);
        setTimeout(() => setCopied(false), 1200);
      }}
    >
      {copied ? "copied ✓" : String(value)}
    </button>
  );
}

export function Reasons({ value }: ICellRendererParams) {
  const list = (value as string[] | undefined) ?? [];
  if (!list.length) return null;
  return <span>{list.join("; ")}</span>;
}

export const moneyFmt = (v: number | null | undefined) => (v === null || v === undefined ? "" : `$${Number(v).toFixed(2)}`);

/** DB timestamps are UTC; the API sends them without a zone suffix. */
export const when = (v: string | null | undefined) => {
  if (!v) return "";
  const d = new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(v) ? v : v + "Z");
  return Number.isNaN(d.getTime()) ? v : d.toLocaleString();
};

export function DataGrid<T>({ rows, columns, quickFilter }: { rows: T[]; columns: ColDef<T>[]; quickFilter: string }) {
  const defaultColDef = useMemo<ColDef<T>>(() => ({ sortable: true, filter: true, resizable: true, minWidth: 90 }), []);
  return (
    <div style={{ height: 540 }}>
      <AgGridReact<T>
        theme={theme}
        rowData={rows}
        columnDefs={columns}
        defaultColDef={defaultColDef}
        quickFilterText={quickFilter}
        animateRows
        pagination
        paginationPageSize={25}
        paginationPageSizeSelector={[25, 50, 100]}
        enableCellTextSelection
      />
    </div>
  );
}
