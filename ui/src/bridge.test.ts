// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from "vitest";

import { createBridge, type McpBridge } from "./bridge";

const activeBridges: McpBridge[] = [];

afterEach(() => {
  for (const bridge of activeBridges.splice(0)) {
    bridge.dispose();
  }
});

function fakeParent(): { parent: Window; postMessage: ReturnType<typeof vi.fn> } {
  const postMessage = vi.fn();
  return {
    parent: { postMessage } as unknown as Window,
    postMessage,
  };
}

describe("MCP Apps bridge", () => {
  it("completes the MCP Apps initialization handshake", async () => {
    const fake = fakeParent();
    const bridge = createBridge(fake.parent);
    activeBridges.push(bridge);

    const connected = bridge.connect();
    const initialize = fake.postMessage.mock.calls[0]?.[0];
    expect(initialize).toEqual({
      jsonrpc: "2.0",
      id: 1,
      method: "ui/initialize",
      params: {
        appInfo: { name: "nebula3-mcp-query-result", version: "0.6.0" },
        appCapabilities: { availableDisplayModes: ["inline", "fullscreen"] },
        protocolVersion: "2026-01-26",
      },
    });

    const host = {
      protocolVersion: "2026-01-26",
      hostInfo: { name: "test-host", version: "1.0.0" },
      hostCapabilities: {},
      hostContext: { theme: "dark" },
    };
    window.dispatchEvent(
      new MessageEvent("message", {
        source: fake.parent,
        data: { jsonrpc: "2.0", id: initialize.id, result: host },
      }),
    );

    await expect(connected).resolves.toEqual(host);
    expect(fake.postMessage.mock.calls[1]?.[0]).toEqual({
      jsonrpc: "2.0",
      method: "ui/notifications/initialized",
      params: {},
    });
  });

  it("correlates a tools/call response by JSON-RPC id", async () => {
    const fake = fakeParent();
    const bridge = createBridge(fake.parent);
    activeBridges.push(bridge);

    const pending = bridge.request("tools/call", {
      name: "nebula_select_space",
      arguments: { space: "demo" },
    });
    const sent = fake.postMessage.mock.calls[0]?.[0];
    let settled = false;
    void pending.then(() => {
      settled = true;
    });

    expect(sent).toMatchObject({
      jsonrpc: "2.0",
      method: "tools/call",
      params: {
        name: "nebula_select_space",
        arguments: { space: "demo" },
      },
    });
    expect(sent.id).toBeTypeOf("number");

    window.dispatchEvent(
      new MessageEvent("message", {
        source: fake.parent,
        data: { jsonrpc: "1.0", id: sent.id, result: { ignored: true } },
      }),
    );
    await Promise.resolve();
    expect(settled).toBe(false);

    window.dispatchEvent(
      new MessageEvent("message", {
        source: fake.parent,
        data: {
          jsonrpc: "2.0",
          id: sent.id,
          result: { structuredContent: { ok: true } },
        },
      }),
    );

    await expect(pending).resolves.toEqual({ structuredContent: { ok: true } });
  });

  it("delivers tool-result notifications only from the configured parent", () => {
    const fake = fakeParent();
    const bridge = createBridge(fake.parent);
    activeBridges.push(bridge);
    const listener = vi.fn();
    bridge.subscribeToToolResult(listener);
    const params = { structuredContent: { result: { status: { ok: true } } } };

    window.dispatchEvent(
      new MessageEvent("message", {
        source: window,
        data: { jsonrpc: "2.0", method: "ui/notifications/tool-result", params },
      }),
    );
    expect(listener).not.toHaveBeenCalled();

    window.dispatchEvent(
      new MessageEvent("message", {
        source: fake.parent,
        data: { jsonrpc: "2.0", method: "ui/notifications/tool-result", params },
      }),
    );
    expect(listener).toHaveBeenCalledOnce();
    expect(listener).toHaveBeenCalledWith(params);
  });

  it("delivers complete tool input notifications", () => {
    const fake = fakeParent();
    const bridge = createBridge(fake.parent);
    activeBridges.push(bridge);
    const listener = vi.fn();
    bridge.subscribeToToolInput(listener);
    const params = { arguments: { result: { query: { statement: "RETURN 1" } } } };

    window.dispatchEvent(
      new MessageEvent("message", {
        source: fake.parent,
        data: { jsonrpc: "2.0", method: "ui/notifications/tool-input", params },
      }),
    );

    expect(listener).toHaveBeenCalledOnce();
    expect(listener).toHaveBeenCalledWith(params);
  });
});
