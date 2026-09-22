import { getIdToken } from "../firebase";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "http://localhost:8000";

export class ApiError extends Error {
  constructor(
    message: string,
    public statusCode?: number,
    public data?: unknown,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

/**
 * Every request attaches the current Firebase ID token — replaces the
 * Supabase-session-in-localStorage flow from neurix-ui's HttpClient
 * (proactive refresh threshold, 401-retry, manual clear-and-redirect).
 * Token lifecycle is owned entirely by the Firebase SDK via
 * onIdTokenChanged, so there is no equivalent refresh logic to duplicate
 * here.
 */
class HttpClient {
  constructor(private baseUrl: string) {}

  private async getAuthHeaders(additionalHeaders?: HeadersInit): Promise<Record<string, string>> {
    const token = await getIdToken();
    const headers: Record<string, string> = {
      "Content-Type": "application/json",
      ...(additionalHeaders as Record<string, string> | undefined),
    };
    if (token) {
      headers["Authorization"] = `Bearer ${token}`;
    }
    return headers;
  }

  private async request<T>(endpoint: string, options: RequestInit = {}): Promise<T> {
    const url = `${this.baseUrl}${endpoint}`;
    const headers = await this.getAuthHeaders(options.headers);
    const response = await fetch(url, { ...options, headers });

    if (!response.ok) {
      const raw = await response.text();
      let data: unknown = raw;
      try {
        data = raw ? JSON.parse(raw) : null;
      } catch {
        // keep raw text
      }
      throw new ApiError(`Request failed with status ${response.status}`, response.status, data);
    }

    if (response.status === 204) {
      return {} as T;
    }
    return response.json();
  }

  get<T>(endpoint: string, options?: RequestInit): Promise<T> {
    return this.request<T>(endpoint, { ...options, method: "GET" });
  }

  post<T>(endpoint: string, data?: unknown, options?: RequestInit): Promise<T> {
    return this.request<T>(endpoint, {
      ...options,
      method: "POST",
      body: data ? JSON.stringify(data) : undefined,
    });
  }
}

export const httpClient = new HttpClient(API_BASE_URL);
