/**
 * A small typed wrapper around Telegram's Mini App script (telegram-web-app.js,
 * loaded in index.html). Everything else in the app talks to Telegram only
 * through this file, so it also works in a normal browser (development, tests).
 * https://core.telegram.org/bots/webapps
 */

type ColorScheme = 'light' | 'dark';
type HapticImpact = 'light' | 'medium' | 'heavy';
type HapticNotice = 'error' | 'success' | 'warning';

interface TelegramBackButton {
  show(): void;
  hide(): void;
  onClick(cb: () => void): void;
  offClick(cb: () => void): void;
}

interface TelegramWebApp {
  initData: string;
  initDataUnsafe: { user?: { id: number; first_name: string; language_code?: string } };
  colorScheme: ColorScheme;
  ready(): void;
  expand(): void;
  close(): void;
  setHeaderColor?(color: string): void;
  setBackgroundColor?(color: string): void;
  disableVerticalSwipes?(): void;
  openTelegramLink(url: string): void;
  openLink(url: string): void;
  showConfirm?(message: string, cb: (ok: boolean) => void): void;
  onEvent(event: 'themeChanged', cb: () => void): void;
  offEvent(event: 'themeChanged', cb: () => void): void;
  BackButton: TelegramBackButton;
  HapticFeedback?: {
    impactOccurred(style: HapticImpact): void;
    notificationOccurred(type: HapticNotice): void;
    selectionChanged(): void;
  };
  isVersionAtLeast?(version: string): boolean;
}

declare global {
  interface Window {
    Telegram?: { WebApp?: TelegramWebApp };
  }
}

export function webApp(): TelegramWebApp | undefined {
  return window.Telegram?.WebApp;
}

/** The signed login data the backend checks (empty outside Telegram). */
export function initData(): string {
  return webApp()?.initData ?? '';
}

export function telegramLanguage(): string | undefined {
  return webApp()?.initDataUnsafe.user?.language_code;
}

export function colorScheme(): ColorScheme {
  const app = webApp();
  if (app?.initData) return app.colorScheme;
  return window.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}

export function onThemeChange(cb: () => void): () => void {
  const app = webApp();
  app?.onEvent('themeChanged', cb);
  return () => app?.offEvent('themeChanged', cb);
}

/** Call once at start: tell Telegram we're ready, use the full height. */
export function startTelegram(): void {
  const app = webApp();
  if (!app) return;
  app.ready();
  app.expand();
  if (app.isVersionAtLeast?.('7.7')) app.disableVerticalSwipes?.();
}

export function paintTelegramChrome(background: string): void {
  const app = webApp();
  if (!app?.initData) return;
  app.setHeaderColor?.(background);
  app.setBackgroundColor?.(background);
}

/** Telegram's own Back button (top left). Returns a cleanup function. */
export function showBackButton(onBack: () => void): () => void {
  const app = webApp();
  if (!app?.initData) return () => undefined;
  app.BackButton.onClick(onBack);
  app.BackButton.show();
  return () => {
    app.BackButton.offClick(onBack);
    app.BackButton.hide();
  };
}

export const haptic = {
  tap: () => webApp()?.HapticFeedback?.impactOccurred('light'),
  select: () => webApp()?.HapticFeedback?.selectionChanged(),
  success: () => webApp()?.HapticFeedback?.notificationOccurred('success'),
  error: () => webApp()?.HapticFeedback?.notificationOccurred('error'),
};

/** Ask before something destructive; falls back to the browser's confirm. */
export function confirmAction(message: string): Promise<boolean> {
  const app = webApp();
  if (app?.initData && app.showConfirm) {
    return new Promise((resolve) => app.showConfirm?.(message, resolve));
  }
  return Promise.resolve(window.confirm(message));
}

export function openTelegramLink(url: string): void {
  const app = webApp();
  if (app?.initData) app.openTelegramLink(url);
  else window.open(url, '_blank', 'noopener');
}

export function closeApp(): void {
  const app = webApp();
  if (app?.initData) app.close();
  else window.history.back();
}
