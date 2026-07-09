import test from "node:test";
import assert from "node:assert/strict";

import { serializeCsv } from "./csv";

test("serializeCsv quotes commas and newlines", () => {
  const csv = serializeCsv(
    [
      {
        agent: "alpha",
        note: "line 1\nline 2",
        summary: 'hello, "world"',
      },
    ],
    ["agent", "note", "summary"],
  );

  assert.equal(
    csv,
    'agent,note,summary\nalpha,"line 1\nline 2","hello, ""world"""\n',
  );
});
