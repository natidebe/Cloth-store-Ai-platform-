import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';

import { ApiError } from '@/api/client';
import { BottomBar, Button, Centered, Page } from '@/components/ui';
import { closeApp } from '@/lib/telegram';

/** Design: "Not in the staff group". */
export function NotInGroup() {
  const { t } = useTranslation();
  return (
    <Page>
      <Centered icon="lock" title={t('access.notInGroupTitle')} body={t('access.notInGroupBody')} />
      <BottomBar>
        <Button variant="primary" block onClick={closeApp}>
          {t('common.close')}
        </Button>
      </BottomBar>
    </Page>
  );
}

/** Opened outside Telegram, or the login expired. */
export function OpenFromBot({ body }: { body?: string }) {
  const { t } = useTranslation();
  return (
    <Page bar={false}>
      <Centered
        icon="bot"
        title={t('access.openFromBotTitle')}
        body={body ?? t('access.openFromBotBody')}
      />
    </Page>
  );
}

/** Design: "Error". */
export function ErrorScreen({ error, onRetry }: { error: unknown; onRetry: () => void }) {
  const { t } = useTranslation();
  const code = error instanceof ApiError && error.status ? `HTTP ${error.status}` : undefined;
  return (
    <Page>
      <Centered
        icon="warning"
        title={t('access.errorTitle')}
        body={t('access.errorBody')}
        code={code}
      />
      <BottomBar>
        <Button variant="primary" block onClick={onRetry}>
          {t('common.tryAgain')}
        </Button>
      </BottomBar>
    </Page>
  );
}

/** The right screen for a failed first load. */
export function LoadFailed({
  error,
  onRetry,
  forbidden,
}: {
  error: unknown;
  onRetry: () => void;
  forbidden?: ReactNode;
}) {
  if (error instanceof ApiError) {
    if (error.isUnauthorized) return <OpenFromBot />;
    if (error.isForbidden) return <>{forbidden ?? <NotInGroup />}</>;
  }
  return <ErrorScreen error={error} onRetry={onRetry} />;
}
