import { colorScheme, onThemeChange, paintTelegramChrome } from './telegram';

/** Follow Telegram's light/dark theme, and paint Telegram's header to match. */
export function applyTheme(): () => void {
  const apply = () => {
    const scheme = colorScheme();
    document.documentElement.dataset.theme = scheme;
    paintTelegramChrome(scheme === 'dark' ? '#0e1620' : '#efeff4');
  };
  apply();
  return onThemeChange(apply);
}
