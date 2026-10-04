import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Navigate, useNavigate } from 'react-router';

import { useSaveSettings, useSettings } from '@/api/queries';
import type { DailySummary } from '@/api/types';
import { ErrorScreen } from '@/app/AccessScreens';
import { useBackButton, useErrorText, useStore } from '@/app/hooks';
import { BottomBar, Button, Card, Notice, Page, PageHeader, SkeletonList } from '@/components/ui';
import { haptic } from '@/lib/telegram';
import { toast } from '@/state/toasts';

import { Choice } from './StaffDiscountScreen';
import s from './settings.module.css';

/** Phase 15 (D70): yesterday's numbers every morning, in the style of "Staff discount limit". Owner only. */
export function DailySummaryScreen() {
  const { storeId, isOwner } = useStore();
  const settings = useSettings(storeId, isOwner);
  useBackButton(`/s/${storeId}/settings`);

  if (!isOwner) return <Navigate to={`/s/${storeId}/settings`} replace />;
  if (settings.isError) {
    return <ErrorScreen error={settings.error} onRetry={() => void settings.refetch()} />;
  }
  if (!settings.data) {
    return (
      <Page>
        <SkeletonList rows={3} />
      </Page>
    );
  }
  return <SummaryForm saved={settings.data.daily_summary ?? 'am'} />;
}

function SummaryForm({ saved }: { saved: DailySummary }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const errorText = useErrorText();
  const { storeId, role, store } = useStore();
  const save = useSaveSettings(storeId);
  const [choice, setChoice] = useState<DailySummary>(saved);

  const choose = (value: DailySummary) => {
    haptic.select();
    setChoice(value);
  };
  const submit = () =>
    save.mutate(
      { daily_summary: choice },
      {
        onSuccess: () => {
          toast.info(t('common.saved'));
          void navigate(`/s/${storeId}/settings`);
        },
        onError: (error) => toast.error(errorText(error)),
      },
    );

  return (
    <Page>
      <PageHeader title={t('dailySummary.title')} role={role} />
      <p className={s.lead}>{t('dailySummary.subtitle')}</p>

      <Card flush>
        <div role="radiogroup" aria-label={t('dailySummary.title')}>
          <Choice
            on={choice === 'am'}
            title={t('dailySummary.am')}
            hint={t('dailySummary.sendHint')}
            onPick={() => choose('am')}
          />
          <Choice
            on={choice === 'en'}
            title={t('dailySummary.en')}
            hint={t('dailySummary.sendHint')}
            onPick={() => choose('en')}
          />
          <Choice
            on={choice === 'off'}
            title={t('dailySummary.off')}
            hint={t('dailySummary.offHint')}
            onPick={() => choose('off')}
          />
        </div>
      </Card>

      {choice !== 'off' && store.bot_username && (
        <Notice tone="info" icon="send">
          {t('dailySummary.startFirst', { bot: store.bot_username })}
        </Notice>
      )}

      <BottomBar>
        <Button
          variant="primary"
          block
          busy={save.isPending}
          disabled={choice === saved}
          onClick={submit}
        >
          {t('common.save')}
        </Button>
      </BottomBar>
    </Page>
  );
}
