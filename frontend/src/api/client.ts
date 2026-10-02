import { initData } from '@/lib/telegram';

/**
 * Every request carries Telegram's signed login (X-Telegram-Init-Data); the
 * backend checks it (backend/app/core/telegram_auth.py). Errors come back as
 * ApiError with the HTTP status and the backend's `detail` message.
 */
export class ApiError extends Error {
  readonly status: number;
  /** Why it was refused, when the server says (X-Error-Code), e.g. "held_by_online_order". */
  readonly code: string | null;

  constructor(status: number, message: string, code: string | null = null) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
  }

  /** Not opened from Telegram, or the login is too old. */
  get isUnauthorized(): boolean {
    return this.status === 401;
  }

  /** Signed in, but not allowed (e.g. not in the staff group, staff not owner). */
  get isForbidden(): boolean {
    return this.status === 403;
  }

  /** A rule refused it (e.g. stock below 0): show `message` to the user. */
  get isRefused(): boolean {
    return this.status === 409 || this.status === 400 || this.status === 413 || this.status === 415;
  }
}

/** The network itself failed (offline, server down). */
export const NETWORK_ERROR = 0;

type Method = 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';

interface RequestOptions {
  method?: Method;
  body?: unknown;
  form?: FormData;
  signal?: AbortSignal;
}

function detailOf(body: unknown): string | undefined {
  if (body && typeof body === 'object' && 'detail' in body) {
    const detail = (body as { detail: unknown }).detail;
    if (typeof detail === 'string') return detail;
    if (Array.isArray(detail)) {
      // FastAPI validation errors: [{loc: [...], msg: "..."}]
      return detail
        .map((d) => (d && typeof d === 'object' && 'msg' in d ? String(d.msg) : ''))
        .filter(Boolean)
        .join('; ');
    }
  }
  return undefined;
}

export async function api<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const headers: Record<string, string> = { 'X-Telegram-Init-Data': initData() };
  let body: BodyInit | undefined;
  if (options.form) {
    body = options.form; // the browser sets the multipart boundary
  } else if (options.body !== undefined) {
    headers['Content-Type'] = 'application/json';
    body = JSON.stringify(options.body);
  }

  let response: Response;
  try {
    response = await fetch(`/api/v1${path}`, {
      method: options.method ?? 'GET',
      headers,
      body,
      signal: options.signal,
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error;
    throw new ApiError(NETWORK_ERROR, 'network');
  }

  const text = await response.text();
  const parsed: unknown = text ? safeJson(text) : undefined;
  if (!response.ok) {
    throw new ApiError(
      response.status,
      detailOf(parsed) ?? response.statusText,
      response.headers.get('X-Error-Code'),
    );
  }
  return parsed as T;
}

function safeJson(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    return undefined;
  }
}
