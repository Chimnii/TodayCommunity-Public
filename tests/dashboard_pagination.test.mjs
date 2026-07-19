import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

const appUrl = new URL("../dashboard/app.js", import.meta.url);
const appSource = await readFile(appUrl, "utf8");
const appWithoutInitialization = appSource.replace(/\binitialize\(\);\s*$/, "");

assert.notEqual(
  appWithoutInitialization,
  appSource,
  "The dashboard test harness must remove the final initialize() call"
);

const context = {
  document: {
    querySelector() {
      return {};
    },
  },
};

vm.runInNewContext(
  `${appWithoutInitialization}\nglobalThis.__dashboardPaginationFunctions = {\n  getPageSequence: typeof getPageSequence === "function" ? getPageSequence : undefined,\n  parsePageJump: typeof parsePageJump === "function" ? parsePageJump : undefined,\n};`,
  context,
  { filename: appUrl.pathname }
);

const { getPageSequence, parsePageJump } = context.__dashboardPaginationFunctions;

function pageSequence(currentPage, totalPages) {
  return Array.from(getPageSequence(currentPage, totalPages));
}

test("loads the dashboard's pagination helpers without running initialize", () => {
  assert.equal(typeof getPageSequence, "function");
  assert.equal(typeof parsePageJump, "function");
});

test("getPageSequence exposes a nine-page window around middle pages", () => {
  const cases = [
    [1, 20, [1, 2, 3, 4, 5, "ellipsis", 20]],
    [5, 20, [1, 2, 3, 4, 5, 6, 7, 8, 9, "ellipsis", 20]],
    [6, 20, [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, "ellipsis", 20]],
    [10, 20, [1, "ellipsis", 6, 7, 8, 9, 10, 11, 12, 13, 14, "ellipsis", 20]],
    [16, 20, [1, "ellipsis", 12, 13, 14, 15, 16, 17, 18, 19, 20]],
    [20, 20, [1, "ellipsis", 16, 17, 18, 19, 20]],
  ];

  for (const [currentPage, totalPages, expected] of cases) {
    assert.deepEqual(
      pageSequence(currentPage, totalPages),
      expected,
      `Unexpected page sequence for page ${currentPage} of ${totalPages}`
    );
  }
});

test("getPageSequence includes every page when the total is small", () => {
  for (let totalPages = 1; totalPages <= 7; totalPages += 1) {
    const expected = Array.from({ length: totalPages }, (_, index) => index + 1);

    for (let currentPage = 1; currentPage <= totalPages; currentPage += 1) {
      assert.deepEqual(pageSequence(currentPage, totalPages), expected);
    }
  }
});

test("getPageSequence always returns ordered, unique, in-range page numbers", () => {
  for (let totalPages = 1; totalPages <= 50; totalPages += 1) {
    for (let currentPage = 1; currentPage <= totalPages; currentPage += 1) {
      const numbers = pageSequence(currentPage, totalPages).filter(
        (entry) => typeof entry === "number"
      );

      assert.deepEqual(numbers, [...numbers].sort((left, right) => left - right));
      assert.equal(numbers.length, new Set(numbers).size);
      assert.ok(numbers.every((page) => Number.isInteger(page) && page >= 1 && page <= totalPages));
    }
  }
});

test("parsePageJump accepts integer strings and numbers within the available pages", () => {
  for (const [value, expected] of [
    ["1", 1],
    ["7", 7],
    ["20", 20],
    [1, 1],
    [7, 7],
    [20, 20],
  ]) {
    assert.equal(parsePageJump(value, 20), expected);
  }
});

test("parsePageJump rejects empty, non-integer, non-numeric, and out-of-range values", () => {
  for (const value of [
    "",
    "   ",
    0,
    "0",
    -1,
    "-1",
    1.5,
    "1.5",
    "page 5",
    Number.NaN,
    Number.POSITIVE_INFINITY,
    21,
    "21",
  ]) {
    assert.equal(parsePageJump(value, 20), null, `Expected ${String(value)} to be rejected`);
  }
});
