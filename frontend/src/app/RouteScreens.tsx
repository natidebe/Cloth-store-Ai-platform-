import { useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { Navigate, useLocation, useRouteError } from 'react-router';

import { ErrorScreen, OpenFromBot } from './AccessScreens';

/**
 * The store's bot opens /app/?store=<id>; the platform bot opens
 * /app/platform. Inside a store everything lives under /app/s/<id>/…
 */
export function Entry() {
  const { t } = useTranslation();
  const store = new URLSearchParams(useLocation().search).get('store');
  if (store && /^[0-9a-f-]{36}$/i.test(store)) {
    return <Navigate to={`/s/${store}/products`} replace />;
  }
  return <OpenFromBot body={t('access.missingStore')} />;
}

const RELOADED = 'store-dashboard-reloaded';

/** A screen's file is gone: a new version was deployed while the app was open. */
function isStaleBuild(error: unknown): boolean {
  return (
    error instanceof Error &&
    /dynamically imported module|Importing a module script failed/i.test(error.message)
  );
}

export function RouteError() {
  const error = useRouteError();
  const stale = isStaleBuild(error);

  useEffect(() => {
    if (!stale) return;
    try {
      if (sessionStorage.getItem(RELOADED)) return; // reload once, not in a loop
      sessionStorage.setItem(RELOADED, '1');
    } catch {
      return;
    }
    window.location.reload();
  }, [stale]);

  return <ErrorScreen error={error} onRetry={() => window.location.reload()} />;
}
