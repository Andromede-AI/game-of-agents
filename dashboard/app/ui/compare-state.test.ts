import test from "node:test";
import assert from "node:assert/strict";

import {
  beginCompareResourceFetch,
  failCompareResourceFetch,
  needsCompareResourceFetch,
  succeedCompareResourceFetch,
} from "./compare-state";

test("compare resource transitions preserve stale data on error", () => {
  const loading = beginCompareResourceFetch<number>(undefined, 100);
  assert.equal(loading.status, "loading");
  assert.equal(loading.data, undefined);

  const success = succeedCompareResourceFetch(42, 100);
  assert.equal(success.status, "success");
  assert.equal(success.data, 42);

  const failure = failCompareResourceFetch(success, "boom", 100);
  assert.equal(failure.status, "error");
  assert.equal(failure.data, 42);
  assert.equal(failure.error, "boom");
});

test("compare resource fetch needs refresh when the run is newer", () => {
  assert.equal(needsCompareResourceFetch(undefined, 50), true);
  assert.equal(
    needsCompareResourceFetch({ status: "loading", sourceUpdatedAt: 50 }, 60),
    false,
  );
  assert.equal(
    needsCompareResourceFetch({ status: "success", sourceUpdatedAt: 50 }, 50),
    false,
  );
  assert.equal(
    needsCompareResourceFetch({ status: "success", sourceUpdatedAt: 50 }, 51),
    true,
  );
});
