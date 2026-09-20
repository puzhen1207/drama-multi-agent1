import type { GenerationResult, MemoryItem, ReviewScores } from "./types";

export interface AuthSession {
  status: string;
  token: string;
  user_id: string;
  expires_at: number;
}

export class ApiClient {
  constructor(private getToken: () => string) {}

  private async request<T>(path: string, init: RequestInit = {}): Promise<T> {
    const headers = new Headers(init.headers || {});
    const token = this.getToken();
    if (token) headers.set("Authorization", `Bearer ${token}`);
    const response = await fetch(path, { ...init, headers });
    const contentType = response.headers.get("content-type") || "";
    const body = contentType.includes("application/json")
      ? await response.json()
      : await response.text();
    if (!response.ok) {
      const message = typeof body === "object" && body?.detail
        ? String(body.detail)
        : typeof body === "string" && body
          ? body
          : `请求失败（HTTP ${response.status}）`;
      throw new Error(message);
    }
    return body as T;
  }

  health() {
    return this.request<any>("/health");
  }

  register(username: string, password: string) {
    return this.request<AuthSession>("/v1/auth/register", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
    });
  }

  login(username: string, password: string) {
    return this.request<AuthSession>("/v1/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
    });
  }

  me() {
    return this.request<{ authenticated: boolean; user_id: string }>("/v1/auth/me");
  }

  logout() {
    return this.request<{ status: string }>("/v1/auth/logout", { method: "POST" });
  }

  generate(payload: Record<string, unknown>, signal?: AbortSignal) {
    return this.request<{ status: string; data: GenerationResult; error?: string }>("/v1/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      signal,
    });
  }

  async stream(
    payload: Record<string, unknown>,
    onEvent: (event: Record<string, any>) => void,
    signal?: AbortSignal,
  ) {
    const headers = new Headers({ "Content-Type": "application/json" });
    const token = this.getToken();
    if (token) headers.set("Authorization", `Bearer ${token}`);
    const response = await fetch("/v1/stream", {
      method: "POST",
      headers,
      body: JSON.stringify(payload),
      signal,
    });
    if (!response.ok) {
      let message = `生成请求失败（状态码 ${response.status}）`;
      try {
        const body = await response.json();
        if (body?.detail) message = String(body.detail);
      } catch {
        // Keep the status-based message when the server returned no JSON body.
      }
      throw new Error(message);
    }
    if (!response.body) throw new Error("浏览器未提供可读取的流式响应");

    const reader = response.body.getReader();
    const decoder = new TextDecoder("utf-8");
    let buffer = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const chunks = buffer.split(/\r?\n\r?\n/);
      buffer = chunks.pop() || "";
      for (const chunk of chunks) {
        if (!chunk.trim()) continue;
        let type = "message";
        let data = "";
        for (const line of chunk.split(/\r?\n/)) {
          if (line.startsWith("event:")) type = line.slice(6).trim();
          if (line.startsWith("data:")) data += line.slice(5).trimStart();
        }
        if (!data) continue;
        try {
          onEvent({ ...JSON.parse(data), type });
        } catch {
          // Ignore malformed partial events; the next SSE frame remains readable.
        }
      }
    }
  }

  listMemories(userId: string, full = true) {
    return this.request<{ total: number; memories: MemoryItem[] }>(
      `/v1/memory?user_id=${encodeURIComponent(userId)}&full=${full}`,
    );
  }

  saveMemory(payload: Record<string, unknown>) {
    return this.request<{ status: string; memory_id: string; total: number }>("/v1/memory/save", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  }

  updateMemory(memoryId: string, payload: Record<string, unknown>) {
    return this.request<{ status: string; memory: MemoryItem }>(
      `/v1/memory/${encodeURIComponent(memoryId)}`,
      {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      },
    );
  }

  deleteMemory(memoryId: string, userId: string) {
    return this.request<{ status: string; deleted: string; total: number }>(
      `/v1/memory/${encodeURIComponent(memoryId)}?user_id=${encodeURIComponent(userId)}`,
      { method: "DELETE" },
    );
  }

  exportMemories(userId: string) {
    return this.request<any>(`/v1/memory/export?user_id=${encodeURIComponent(userId)}`);
  }

  importMemories(userId: string, memories: unknown[]) {
    return this.request<{ imported: number; skipped: number; total: number }>("/v1/memory/import", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ user_id: userId, memories, skip_duplicates: true }),
    });
  }

  submitReview(userId: string, sessionId: string, scores: ReviewScores) {
    return this.request<any>("/v1/evaluations/human", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        user_id: userId,
        session_id: sessionId,
        case_id: "interactive",
        mode: "full_workflow",
        ...scores,
      }),
    });
  }
}
