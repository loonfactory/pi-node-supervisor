import assert from "node:assert/strict";
import test from "node:test";
import React from "react";
import { act, create } from "react-test-renderer";
import { useCameraStream } from "../src/hooks/useCameraStream.js";

test("a new authentication version recovers a preview that received 401", async () => {
  const previous = {
    fetch: global.fetch,
    window: global.window,
    sessionStorage: global.sessionStorage,
  };
  let result;
  let requests = 0;
  let renderer;
  global.window = new EventTarget();
  global.sessionStorage = { getItem: () => "secret" };
  global.fetch = async () => {
    requests += 1;
    return requests === 1
      ? {
          ok: false,
          status: 401,
          json: async () => ({ detail: "authentication required" }),
        }
      : { ok: true, status: 204, json: async () => ({}) };
  };
  function Probe({ version, enabled = true }) {
    result = useCameraStream("rgb", enabled, version);
    return null;
  }
  try {
    await act(async () => {
      renderer = create(React.createElement(Probe, { version: 0 }));
    });
    assert.equal(result.phase, "unauthorized");
    assert.equal(result.src, null);
    await act(async () => {
      renderer.update(React.createElement(Probe, { version: 1 }));
    });
    assert.equal(requests, 2);
    assert.equal(result.src, "/api/camera/rgb");
    await act(async () => {
      result.onLoad();
    });
    assert.equal(result.phase, "streaming");
    await act(async () => {
      renderer.update(
        React.createElement(Probe, { version: 1, enabled: false }),
      );
    });
    assert.equal(result.src, null);
    assert.equal(result.phase, "idle");
  } finally {
    if (renderer) await act(async () => renderer.unmount());
    Object.assign(global, previous);
  }
});
