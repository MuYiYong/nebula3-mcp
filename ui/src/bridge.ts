export type ToolResultListener = (params: unknown) => void;

export interface McpBridge {
  connect(): Promise<unknown>;
  request(method: string, params: unknown): Promise<unknown>;
  subscribeToToolInput(listener: ToolResultListener): () => void;
  subscribeToToolResult(listener: ToolResultListener): () => void;
  dispose(): void;
}

export function createBridge(parentWindow: Window = window.parent): McpBridge {
  let nextRequestId = 1;
  const pending = new Map<
    number,
    { resolve: (result: unknown) => void; reject: (reason: unknown) => void }
  >();
  const toolInputListeners = new Set<ToolResultListener>();
  const toolResultListeners = new Set<ToolResultListener>();

  const handleMessage = (event: MessageEvent<unknown>): void => {
    if (
      event.source !== parentWindow ||
      !isRecord(event.data) ||
      event.data.jsonrpc !== "2.0"
    ) {
      return;
    }
    const message = event.data;
    if (typeof message.id === "number") {
      const request = pending.get(message.id);
      if (request === undefined) {
        return;
      }
      pending.delete(message.id);
      if ("error" in message) {
        request.reject(message.error);
      } else {
        request.resolve(message.result);
      }
      return;
    }
    if (message.method === "ui/notifications/tool-input") {
      for (const listener of toolInputListeners) {
        listener(message.params);
      }
    } else if (message.method === "ui/notifications/tool-result") {
      for (const listener of toolResultListeners) {
        listener(message.params);
      }
    }
  };

  window.addEventListener("message", handleMessage);

  const request = (method: string, params: unknown): Promise<unknown> => {
    const id = nextRequestId++;
    const response = new Promise<unknown>((resolve, reject) => {
      pending.set(id, { resolve, reject });
    });
    parentWindow.postMessage({ jsonrpc: "2.0", id, method, params }, "*");
    return response;
  };

  return {
    async connect(): Promise<unknown> {
      const host = await request("ui/initialize", {
        appInfo: { name: "nebula3-mcp-query-result", version: "0.6.0" },
        appCapabilities: { availableDisplayModes: ["inline", "fullscreen"] },
        protocolVersion: "2026-01-26",
      });
      parentWindow.postMessage(
        {
          jsonrpc: "2.0",
          method: "ui/notifications/initialized",
          params: {},
        },
        "*",
      );
      return host;
    },
    request,
    subscribeToToolInput(listener: ToolResultListener): () => void {
      toolInputListeners.add(listener);
      return () => toolInputListeners.delete(listener);
    },
    subscribeToToolResult(listener: ToolResultListener): () => void {
      toolResultListeners.add(listener);
      return () => toolResultListeners.delete(listener);
    },
    dispose(): void {
      window.removeEventListener("message", handleMessage);
      pending.clear();
      toolInputListeners.clear();
      toolResultListeners.clear();
    },
  };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}
