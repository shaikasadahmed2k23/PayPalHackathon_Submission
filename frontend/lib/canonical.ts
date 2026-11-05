// Byte-identical to backend app/crypto.py canonical_json:
//   sorted keys (recursive), separators "," and ":", ASCII only, whole-number floats as ints.
// JS already prints 5 for 5.0 and 4.5 for 4.5, so numbers need no special handling.
// Locked to the backend by shared/canonical_fixture.json (see canonical.test.ts).

type Json = null | boolean | number | string | Json[] | { [key: string]: Json | undefined };

function str(s: string): string {
  // JSON.stringify gives Python's short escapes (\n \" \\ ...); Python also \u-escapes everything
  // outside 0x20-0x7e (incl. DEL), as lowercase hex.
  return JSON.stringify(s).replace(
    /[\u007f-￿]/g,
    (c) => "\\u" + c.charCodeAt(0).toString(16).padStart(4, "0"),
  );
}

function write(v: Json | undefined): string {
  if (v === null) return "null";
  if (typeof v === "boolean") return v ? "true" : "false";
  if (typeof v === "number") {
    if (!Number.isFinite(v)) throw new Error("Cannot canonicalise a non-finite number");
    return String(v);
  }
  if (typeof v === "string") return str(v);
  if (Array.isArray(v)) return "[" + v.map(write).join(",") + "]";
  if (typeof v === "object") {
    const keys = Object.keys(v)
      .filter((k) => v[k] !== undefined)
      .sort(); // code-unit order; same as Python for BMP keys
    return "{" + keys.map((k) => str(k) + ":" + write(v[k])).join(",") + "}";
  }
  throw new Error(`Cannot canonicalise ${typeof v}`);
}

export function canonicalJson(obj: unknown): string {
  return write(obj as Json);
}

export function canonicalBytes(obj: unknown): Uint8Array {
  return new TextEncoder().encode(canonicalJson(obj)); // ASCII, so UTF-8 == what Python signs
}

/** "YYYY-MM-DDTHH:MM:SSZ": ISO without milliseconds, the only timestamp format the backend signs. */
export function utcStamp(d: Date = new Date()): string {
  return d.toISOString().replace(/\.\d{3}Z$/, "Z");
}
